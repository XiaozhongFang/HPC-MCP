"""MCP-instance job tracking (ownership for shared accounts).

Because several users may share one SSH account, Unix ownership cannot
separate "my jobs" from "their jobs".  Each MCP server instance therefore
registers every job it submits in a JSON tracking file inside
``$ROOT/.hpc-mcp/jobs/`` and is only allowed to operate (status/output/
cancel/accounting) on job IDs it registered itself.
"""

from __future__ import annotations

import json
import time
import uuid

from ..config import Config
from ..errors import SlurmPolicyError
from ..security.path_policy import is_within
from ..ssh.manager import SshManager


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
        res = await self._ssh.run(
            ["cat", self._register_file], check=False, max_output=4 * 1024 * 1024
        )
        if res.exit_code != 0 or not res.stdout_text.strip():
            return {}
        try:
            data = json.loads(res.stdout_text)
            if not isinstance(data, dict) or len(data) > 10000:
                raise ValueError("tracking file must be a bounded JSON object")
            return data
        except (json.JSONDecodeError, ValueError) as exc:
            raise SlurmPolicyError("Tracked job metadata is invalid; refusing to overwrite it") from exc

    async def _write_register(self, data: dict) -> None:
        if len(data) > 10000:
            raise SlurmPolicyError("Tracked job metadata limit exceeded")
        payload = json.dumps(data, indent=2, sort_keys=True)
        import shlex

        q = shlex.quote(self._register_file)
        # noclobber + refuse symlinks: a planted symlink at the register path
        # must never turn register() into an arbitrary file overwrite.
        lock_path = self._register_file + ".lock"
        tmp_path = self._register_file + f".tmp.{self._session_id}"
        q_lock = shlex.quote(lock_path)
        q_tmp = shlex.quote(tmp_path)
        command = (
            f"set -eu; test ! -L {q}; mkdir {q_lock}; trap 'rmdir {q_lock}' EXIT; "
            f"test ! -e {q_tmp}; cat > {q_tmp} <<'HPCMCP_EOF'\n{payload}\nHPCMCP_EOF\n"
            f"test ! -L {q_tmp}; mv -f -- {q_tmp} {q}"
        )
        await self._ssh.run(["sh", "-c", command], check=True)

    async def register(self, job_id: str, *, job_name: str, project_root: str, job_dir: str) -> None:
        if not job_id.isdigit() or len(job_id) > 20:
            raise SlurmPolicyError("job_id must be a bounded numeric Slurm ID", requested=job_id)
        expected_dir = f"{self._jobs_dir}/{job_id}"
        if job_dir != expected_dir or not is_within(project_root, self._cfg.root):
            raise SlurmPolicyError("Invalid job metadata path")
        await self.ensure_ready()
        data = await self._read_register()
        data[job_id] = {
            "job_id": job_id,
            "job_name": job_name,
            "project_root": project_root,
            "job_dir": job_dir,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool_session": self._session_id,
        }
        await self._write_register(data)

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

    async def count_active(self, states_by_id: dict[str, str]) -> int:
        """Count tracked jobs still in a non-terminal state."""
        terminal = {
            "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY",
            "NODE_FAIL", "PREEMPTED", "DEADLINE", "BOOT_FAIL", "REVOKED", "SPECIAL_EXIT",
        }
        data = {entry["job_id"]: entry for entry in await self.list_mine()}
        active = 0
        for jid in data:
            st = states_by_id.get(jid, "UNKNOWN").split("+", 1)[0].strip()
            if st not in terminal:
                active += 1
        return active
