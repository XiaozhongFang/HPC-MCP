"""MCP-instance job tracking (ownership for shared accounts).

Because several users may share one SSH account, Unix ownership cannot
separate "my jobs" from "their jobs".  Each MCP server instance therefore
registers every job it submits in a JSON tracking file inside
``$ROOT/.hpc-mcp/jobs/`` and is only allowed to operate (status/output/
cancel/accounting) on job IDs it registered itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid

from ..config import Config
from ..errors import RemoteCommandError, SlurmPolicyError
from ..security.path_policy import is_within
from ..ssh.manager import SshManager

#: Version token reported for a registry file that does not exist yet.
_REGISTRY_ABSENT = "absent"
#: Remote exit code asking the caller to re-read the registry and merge again.
_REGISTRY_CONFLICT_EXIT = 3


class _RegistryConflict(Exception):
    """Another writer replaced the registry between our read and our write."""


class JobTracker:
    def __init__(self, cfg: Config, ssh: SshManager) -> None:
        self._cfg = cfg
        self._ssh = ssh
        self._session_id = uuid.uuid4().hex[:12]
        self._jobs_dir = cfg.jobs_dir
        self._register_file = f"{cfg.root.rstrip('/')}/.hpc-mcp/tracked_jobs.json"
        self._initialized = False

    @property
    def session_id(self) -> str:
        return self._session_id

    async def ensure_ready(self) -> None:
        if self._initialized:
            return
        metadata_root = f"{self._cfg.root.rstrip('/')}/.hpc-mcp"
        for path in (metadata_root, self._jobs_dir, self._register_file):
            real = await self._ssh.realpath(path)
            # A missing file resolves to itself with realpath -m.  Existing
            # symlinks (even to another in-root location) are rejected.
            if real is None or real != path or not is_within(real, self._cfg.root):
                raise SlurmPolicyError("Job metadata directory is outside the configured root")
        await self._ssh.run(["mkdir", "-p", "--", self._jobs_dir], check=True)
        for path in (metadata_root, self._jobs_dir, self._register_file):
            real = await self._ssh.realpath(path)
            if real is None or real != path or not is_within(real, self._cfg.root):
                raise SlurmPolicyError("Job metadata directory changed outside the configured root")
        self._initialized = True

    _ensure_dirs = ensure_ready

    async def _read_register(self) -> dict:
        data, _token = await self._read_register_snapshot()
        return data

    async def _read_register_snapshot(self) -> tuple[dict, str]:
        """Read the registry together with a token naming exactly that version.

        The token is the checksum of the bytes we actually received, so a
        write guarded by it can only commit while the file still holds those
        very bytes.  That is what makes concurrent registrations safe: a
        payload merged from a stale snapshot is refused instead of silently
        dropping the entries another MCP instance added in the meantime.
        """
        res = await self._ssh.run(
            ["cat", self._register_file], check=False, max_output=4 * 1024 * 1024
        )
        # A missing file is a version of its own ("absent"), not an error: the
        # guarded write must then commit only if it is still missing.
        if res.exit_code != 0:
            return {}, _REGISTRY_ABSENT
        token = hashlib.md5(res.stdout).hexdigest()
        if not res.stdout.strip():
            return {}, token
        try:
            data = json.loads(res.stdout_text)
            if not isinstance(data, dict) or len(data) > 10000:
                raise ValueError("tracking file must be a bounded JSON object")
            return data, token
        except (json.JSONDecodeError, ValueError) as exc:
            raise SlurmPolicyError("Tracked job metadata is invalid; refusing to overwrite it") from exc

    #: A lock directory older than this many minutes cannot belong to a live
    #: writer -- the guarded write takes well under a second -- so it is a
    #: leftover from an interrupted session and may be reclaimed.  Without
    #: this, one killed or hung submission would block every later submission
    #: of the account forever.
    _LOCK_STALE_MINUTES = 5
    #: Bounded wait for a lock held by a *live* writer.  A competing submit
    #: holds the lock for a fraction of a second, so waiting is far cheaper
    #: than failing the submission (which also cancels the job just sent to
    #: Slurm).  One attempt per second keeps the added latency bounded.
    _LOCK_ATTEMPTS = 6
    _LOCK_RETRY_SLEEP_SECONDS = 1
    #: Read-merge-write attempts for one registration.  Each attempt re-reads
    #: the registry and commits only while it is unchanged, so simultaneous
    #: submissions from several MCP instances all survive instead of the last
    #: writer winning.
    _REGISTER_ATTEMPTS = 6
    #: Small, growing back-off between those attempts: two instances retrying
    #: in lockstep would otherwise keep invalidating each other.
    _REGISTER_RETRY_SLEEP_SECONDS = 0.05

    async def _write_register(self, data: dict, *, expected: str) -> None:
        """Write the registry, committing only while it still holds ``expected``.

        ``expected`` is the version token from
        :meth:`_read_register_snapshot`: if another instance replaced the file
        in the meantime the write aborts with :class:`_RegistryConflict`
        instead of overwriting entries this payload knows nothing about.
        """
        if len(data) > 10000:
            raise SlurmPolicyError("Tracked job metadata limit exceeded")
        payload = json.dumps(data, indent=2, sort_keys=True)
        import shlex

        q = shlex.quote(self._register_file)
        q_expected = shlex.quote(expected)
        lock_path = self._register_file + ".lock"
        # A unique temp name per write: a temp file left behind by an
        # interrupted write must never block later writes.  The previous
        # per-session name made one interrupted write fatal for the whole
        # life of the MCP process.
        tmp_path = f"{self._register_file}.tmp.{self._session_id}.{uuid.uuid4().hex[:8]}"
        q_lock = shlex.quote(lock_path)
        q_tmp = shlex.quote(tmp_path)
        command = (
            "set -eu\n"
            # Registry entries name paths inside the user root; keep the file
            # private to the account instead of trusting the remote umask.
            "umask 077\n"
            f"reg={q}\nlock={q_lock}\ntmp={q_tmp}\n"
            "lock_held=0\n"
            "cleanup() {\n"
            '  if [ "$lock_held" = 1 ]; then rmdir -- "$lock" 2>/dev/null || true; fi\n'
            '  rm -f -- "$tmp" 2>/dev/null || true\n'
            "}\n"
            # The trap is installed *before* the lock is taken, so anything
            # that ends the script after a successful mkdir releases the lock
            # again.  lock_held guarantees the cleanup never removes a lock
            # owned by another writer.
            "trap cleanup EXIT\n"
            # A planted symlink at the register path must never turn this
            # atomic replace into an arbitrary file overwrite.
            'test ! -L "$reg"\n'
            "n=0\n"
            f'while [ "$n" -lt {self._LOCK_ATTEMPTS} ]; do\n'
            "  n=$((n + 1))\n"
            '  if mkdir -- "$lock" 2>/dev/null; then lock_held=1; break; fi\n'
            # A symlinked lock path is never ours; never unlink through it.
            '  if [ -L "$lock" ]; then\n'
            '    printf "%s\\n" "tracked-jobs lock path is a symlink; refusing" >&2\n'
            "    exit 1\n"
            "  fi\n"
            # Reclaim only an old *and empty* lock directory: rmdir refuses a
            # non-empty one, so a planted directory is never "reclaimed" and
            # never reported as a successful takeover.
            f'  if [ -d "$lock" ] && find "$lock" -maxdepth 0 -mmin +{self._LOCK_STALE_MINUTES} 2>/dev/null | grep -q .; then\n'
            '    if rmdir -- "$lock" 2>/dev/null; then continue; fi\n'
            "  fi\n"
            f"  sleep {self._LOCK_RETRY_SLEEP_SECONDS}\n"
            "done\n"
            'if [ "$lock_held" -ne 1 ]; then\n'
            '  printf "%s\\n" "tracked-jobs lock is busy: $lock" >&2\n'
            "  exit 1\n"
            "fi\n"
            # Compare-and-swap inside the lock: commit only while the file
            # still holds the exact bytes this payload was merged from.  Two
            # MCP instances submitting at the same time would otherwise each
            # write their own snapshot and one of the two jobs would vanish.
            "cur=absent\n"
            'if [ -e "$reg" ]; then\n'
            '  cur=$(md5sum < "$reg" 2>/dev/null | cut -d" " -f1)\n'
            '  [ -n "$cur" ] || cur=unknown\n'
            "fi\n"
            # An unreadable registry is never a version to overwrite.
            'if [ "$cur" = unknown ]; then\n'
            '  printf "%s\\n" "the registry exists but could not be read" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'if [ "$cur" = {q_expected} ]; then conflict=0; else conflict=1; fi\n'
            'if [ "$conflict" = 1 ]; then\n'
            '  printf "%s\\n" "registry changed while this write was prepared" >&2\n'
            f"  exit {_REGISTRY_CONFLICT_EXIT}\n"
            "fi\n"
            # noclobber: even the unique temp name must be created fresh, so
            # a pre-planted file or symlink at that path is refused rather
            # than truncated.
            "set -C\n"
            # Stream the payload over SSH stdin instead of embedding it in
            # argv.  Windows OpenSSH cannot launch command lines above about
            # 32 KiB, while the bounded registry may legitimately be larger.
            'cat > "$tmp"\n'
            "set +C\n"
            'test ! -L "$tmp"\n'
            'mv -f -- "$tmp" "$reg"\n'
        )
        # The command contains only trusted, quoted paths and fixed shell;
        # registry bytes travel separately on stdin and are never parsed as
        # shell syntax or exposed in the command debug log.
        res = await self._ssh.run_raw(command, check=False, stdin_text=payload)
        if res.exit_code == _REGISTRY_CONFLICT_EXIT:
            raise _RegistryConflict(
                res.stderr_text.strip() or "the registry changed before this write"
            )
        if res.exit_code != 0:
            # Report the remote diagnostic instead of echoing the command
            # template: the failing statement is not identifiable from the
            # template, and the template is what made this failure look like a
            # mysterious "lock file" problem.
            detail = res.stderr_text.strip()[:400]
            raise RemoteCommandError(
                "Could not record the job ownership metadata.\n\n"
                f"Registry file: {self._register_file}\n"
                f"Exit code: {res.exit_code}\n"
                f"Diagnostic: {detail or '(the remote command produced no output)'}\n\n"
                "Most likely another MCP instance is submitting at this moment, "
                "or an earlier submission was interrupted and left the lock "
                f"directory {lock_path} behind.\n"
                f"The lock is always an empty directory and is reclaimed "
                f"automatically once it is older than "
                f"{self._LOCK_STALE_MINUTES} minutes, so retrying is usually "
                f"enough; a user with login-node access can clear it "
                f"immediately with: rmdir {lock_path}",
                exit_code=res.exit_code,
            )

    async def register(self, job_id: str, *, job_name: str, project_root: str, job_dir: str) -> None:
        if not job_id.isdigit() or len(job_id) > 20:
            raise SlurmPolicyError("job_id must be a bounded numeric Slurm ID", requested=job_id)
        expected_dir = f"{self._jobs_dir}/{job_id}"
        if job_dir != expected_dir or not is_within(project_root, self._cfg.root):
            raise SlurmPolicyError("Invalid job metadata path")
        await self.ensure_ready()
        for attempt in range(self._REGISTER_ATTEMPTS):
            # Read-merge-write, retried on conflict: registering a job must not
            # drop the entries another MCP instance registered in the window
            # between our read and our write.
            data, token = await self._read_register_snapshot()
            data[job_id] = {
                "job_id": job_id,
                "job_name": job_name,
                "project_root": project_root,
                "job_dir": job_dir,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tool_session": self._session_id,
            }
            try:
                await self._write_register(data, expected=token)
                return
            except _RegistryConflict:
                if attempt + 1 < self._REGISTER_ATTEMPTS:
                    # Back off a little so two instances retrying in lockstep
                    # do not keep invalidating each other.
                    await asyncio.sleep(self._REGISTER_RETRY_SLEEP_SECONDS * (attempt + 1))
        raise RemoteCommandError(
            "Could not register the job: the tracked-job registry could not be "
            f"updated consistently in {self._REGISTER_ATTEMPTS} attempts. "
            "Either other MCP instances are submitting at the same time "
            "(retry the submission), or the registry file could not be "
            "rewritten."
        )

    async def ensure_job_dir(self, job_id: str) -> str:
        if not job_id.isdigit() or len(job_id) > 20:
            raise SlurmPolicyError("Invalid job ID for metadata directory")
        await self.ensure_ready()
        path = f"{self._jobs_dir}/{job_id}"
        existing = await self._ssh.realpath(path)
        if existing is not None and (existing != path or not is_within(existing, self._cfg.root)):
            raise SlurmPolicyError("Job output directory is a symlink or outside the root")
        await self._ssh.run(["mkdir", "-p", "--", path], check=True)
        real = await self._ssh.realpath(path)
        if real is None or real != path or not is_within(real, self._cfg.root):
            raise SlurmPolicyError("Job output directory is not a canonical path inside the root")
        return path

    async def prepare_output_links(self, job_id: str) -> None:
        """Expose Slurm's pre-created flat output files below the job directory.

        Slurm opens output files before the batch script starts and does not
        create intermediate directories.  The submit path therefore uses a
        flat ``%j`` filename and links it into the canonical per-job directory
        immediately after sbatch returns.
        """
        job_dir = await self.ensure_job_dir(job_id)
        for stream in ("stdout", "stderr"):
            source = f"{self._jobs_dir}/{job_id}.{stream}.log"
            destination = f"{job_dir}/{stream}.log"
            await self._ssh.run(["ln", "-s", "--", source, destination], check=True)

    async def require_owned(self, job_id: str) -> dict:
        """Return the tracking entry, or deny if this instance doesn't own it."""
        if not job_id or not job_id.isdigit() or len(job_id) > 20:
            raise SlurmPolicyError(
                "job_id must be a numeric Slurm job ID registered by this server",
                requested=str(job_id),
            )
        data = await self._read_register()
        entry = data.get(job_id)
        if not self._entry_is_owned(job_id, entry):
            raise SlurmPolicyError(
                f"Job {job_id} was not submitted by this MCP server instance. "
                "For shared-account safety, only jobs tracked by this instance can be managed. "
                "Submit your job via hpc.slurm.submit first.",
                requested=job_id,
            )
        return entry

    async def list_mine(self) -> list[dict]:
        data = await self._read_register()
        result: list[dict] = []
        for job_id, entry in data.items():
            if not isinstance(job_id, str) or not job_id.isdigit():
                continue
            if not self._entry_is_owned(job_id, entry):
                continue
            assert isinstance(entry, dict)
            result.append(entry)
        return result

    async def list_owned_job_ids(self) -> list[str]:
        """Job IDs tracked by *this* instance.

        This is the only source of job IDs that may ever be passed to
        ``squeue``/``sacct``.  Querying the full shared-account queue would
        leak other users' job metadata to the MCP process; ownership checks
        must never trigger a whole-account scan.
        """
        return [entry["job_id"] for entry in await self.list_mine()]

    def _entry_is_owned(self, job_id: str, entry: object) -> bool:
        expected_dir = f"{self._jobs_dir}/{job_id}"
        return (
            isinstance(entry, dict)
            and entry.get("tool_session") == self._session_id
            and entry.get("job_id") == job_id
            and entry.get("job_dir") == expected_dir
            and isinstance(entry.get("project_root"), str)
            and is_within(entry["project_root"], self._cfg.root)
        )

    async def count_active(self, states_by_id: dict[str, str], *, queue_ok: bool = False) -> int:
        """Count tracked jobs that still occupy a concurrency slot.

        squeue drops jobs as soon as they finish, so an entry missing from
        the squeue snapshot cannot be assumed active.  Such entries are
        re-checked against ``sacct`` (tracked ids only, never a whole-account
        scan); a terminal sacct state frees the quota even though the job is
        still registered.

        A job that neither source accounts for is released only when both
        probes are known to have run successfully: ``queue_ok`` reports that
        the squeue query itself succeeded (an empty result is then a fact,
        not a failure), and a successful ``sacct`` run that returns no row
        for an id proves the accounting record is gone.  If either probe
        failed the job stays counted, which is fail-closed for a
        just-submitted job that has not appeared anywhere yet.
        """
        terminal = {
            "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY",
            "NODE_FAIL", "PREEMPTED", "DEADLINE", "BOOT_FAIL", "REVOKED", "SPECIAL_EXIT",
        }
        data = {entry["job_id"]: entry for entry in await self.list_mine()}
        unresolved = [
            jid
            for jid in data
            if states_by_id.get(jid, "UNKNOWN").split("+", 1)[0].strip() not in terminal
        ]
        acct_states: dict[str, str] = {}
        acct_ok = True
        if unresolved:
            res = await self._ssh.run(
                ["sacct", "-j", ",".join(unresolved), "--format=JobID,State", "-n", "-P", "-X"],
                check=False,
            )
            acct_ok = res.exit_code == 0
            for line in res.stdout_text.splitlines():
                parts = line.split("|")
                if len(parts) >= 2 and parts[0].isdigit():
                    acct_states[parts[0]] = parts[1].split("+", 1)[0].strip()
        active = 0
        for jid in data:
            state = states_by_id.get(jid, "UNKNOWN").split("+", 1)[0].strip()
            if state in terminal:
                continue
            if jid in states_by_id and state != "UNKNOWN":
                # answered by squeue, still not terminal: it holds a slot
                active += 1
                continue
            acct_state = acct_states.get(jid)
            if acct_state is not None:
                if acct_state in terminal:
                    states_by_id[jid] = acct_state
                    continue
                active += 1
                continue
            if queue_ok and acct_ok:
                # Both probes ran successfully and neither knows this job any
                # more: it left the queue and its accounting record is gone,
                # so it cannot be holding a slot.  Releasing the quota here
                # prevents a permanent 20/20 deadlock once accounting records
                # expire, while a failed probe above keeps the fail-closed
                # behaviour.
                continue
            active += 1
        return active
