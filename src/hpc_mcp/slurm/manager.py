"""Slurm operations: submit/status/queue/output/cancel/accounting/wait.

All compute goes through ``sbatch`` with a generated batch script.  Every
resource parameter passes :mod:`hpc_mcp.security.slurm_policy` first, the
working directory passes the path sandbox, and job stdout/stderr are
captured inside ``$ROOT/.hpc-mcp/jobs/<job-id>/``.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex

from ..config import Config
from ..errors import HpcMcpError, RemoteCommandError, SlurmPolicyError
from ..security import slurm_policy
from ..security.path_policy import is_within, validate_path
from ..ssh.manager import SshManager
from .jobs import JobTracker

_JOB_ID_RE = re.compile(r"^\d+$")


class SlurmManager:
    def __init__(self, cfg: Config, ssh: SshManager, tracker: JobTracker) -> None:
        self._cfg = cfg
        self._ssh = ssh
        self._tracker = tracker
        self._submit_lock = asyncio.Lock()

    # -- high-level run -------------------------------------------------------

    #: Trusted runtime profiles for hpc.job.run.  Each profile maps a
    #: friendly runtime name to an executable basename and, when the script
    #: ends in a known extension, how to pass it to that runtime.
    _RUNTIME_PROFILES: dict[str, dict] = {
        "julia": {"exe": "julia", "defaults": ["--project=."]},
        "python": {"exe": "python", "defaults": []},
        "moose": {"exe": "mpirun", "moose": True},
        "bash": {"exe": "bash", "defaults": []},
        "shell": {"exe": "bash", "defaults": []},
    }

    async def run(
        self,
        *,
        runtime: str,
        script: str,
        args: list[str] | None = None,
        working_directory: str | None = None,
        job_name: str | None = None,
        partition: str | None = None,
        nodes: int | None = None,
        ntasks: int | None = None,
        cpus_per_task: int | None = None,
        memory: str | int | None = None,
        time_limit: str | None = None,
        gpus: int | None = None,
        environment: dict[str, str] | None = None,
    ) -> dict:
        """Submit a job from a trusted runtime profile.

        This is a convenience layer on top of :meth:`submit`: it only builds
        the argv from a fixed profile table -- every resource parameter still
        passes the Slurm policy, the working directory still passes the path
        sandbox, and the job is still tracked under this instance's
        ownership.  It never bypasses any policy layer.
        """
        profile = self._RUNTIME_PROFILES.get(runtime)
        if profile is None:
            raise SlurmPolicyError(
                f"Unknown runtime profile {runtime!r}. Allowed: {', '.join(sorted(self._RUNTIME_PROFILES))}",
                requested=runtime,
            )
        if not isinstance(script, str) or not script or len(script) > 4096:
            raise SlurmPolicyError("script must be a non-empty path string", requested=str(script))
        if any(ch in script for ch in "\x00\r\n"):
            raise SlurmPolicyError("script path may not contain control characters", requested="(redacted)")
        extra = args or []
        if not isinstance(extra, list) or len(extra) > 64 or any(
            not isinstance(a, str) or not a or len(a) > 4096 or any(ch in a for ch in "\x00\r\n") for a in extra
        ):
            raise SlurmPolicyError("args must be a bounded list of non-empty strings without control characters")

        if profile.get("moose"):
            # moose profile: mpirun -np <ntasks> -- <binary> <input...>
            cmd = ["mpirun", "-np", str(ntasks or 1), "--", script, *extra]
        elif runtime in ("bash", "shell"):
            cmd = ["bash", script, *extra]
        else:
            cmd = [profile["exe"], *profile.get("defaults", []), script, *extra]

        return await self.submit(
            job_name=job_name or runtime,
            working_directory=working_directory or self._cfg.root,
            command=cmd,
            partition=partition,
            nodes=nodes,
            ntasks=ntasks,
            cpus_per_task=cpus_per_task,
            memory=memory,
            time_limit=time_limit,
            gpus=gpus,
            environment=environment,
        )

    # -- submit ----------------------------------------------------------------

    async def submit(
        self,
        *,
        job_name: str,
        working_directory: str,
        command: list[str] | str,
        partition: str | None = None,
        nodes: int | None = None,
        ntasks: int | None = None,
        cpus_per_task: int | None = None,
        memory: str | int | None = None,
        time_limit: str | None = None,
        gpus: int | None = None,
        environment: dict[str, str] | None = None,
    ) -> dict:
        async with self._submit_lock:
            return await self._submit_impl(
                job_name=job_name,
                working_directory=working_directory,
                command=command,
                partition=partition,
                nodes=nodes,
                ntasks=ntasks,
                cpus_per_task=cpus_per_task,
                memory=memory,
                time_limit=time_limit,
                gpus=gpus,
                environment=environment,
            )

    async def _submit_impl(
        self,
        *,
        job_name: str,
        working_directory: str,
        command: list[str] | str,
        partition: str | None = None,
        nodes: int | None = None,
        ntasks: int | None = None,
        cpus_per_task: int | None = None,
        memory: str | int | None = None,
        time_limit: str | None = None,
        gpus: int | None = None,
        environment: dict[str, str] | None = None,
    ) -> dict:
        cmd_argv = self._validate_command(command)
        env_values = self._validate_environment(environment)
        if not isinstance(job_name, str) or not job_name or len(job_name) > 256 or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in job_name):
            raise SlurmPolicyError("job_name must be 1-256 characters without control characters")
        # working_directory must be inside the sandbox (realpath-verified)
        from ..filesystem.service import FileService

        fs = FileService(self._cfg, self._ssh)
        real_cwd = await fs._resolve_existing(working_directory)
        if not is_within(real_cwd, self._cfg.root):
            raise SlurmPolicyError(
                "Working directory escapes the configured user root",
                requested=working_directory,
                scope=self._cfg.root,
            )

        # Defaults come from the job script's #SBATCH directives when the
        # command is a .sh script; explicit agent args win over them.
        script_defaults = await self._read_sbatch_defaults(real_cwd, cmd_argv)
        partition = partition if partition is not None else script_defaults.get("partition")
        nodes = nodes if nodes is not None else int(script_defaults.get("nodes") or 1)
        ntasks = ntasks if ntasks is not None else int(script_defaults.get("ntasks") or 1)
        cpus_per_task = cpus_per_task if cpus_per_task is not None else int(script_defaults.get("cpus_per_task") or 1)
        memory = memory if memory is not None else script_defaults.get("memory")
        time_limit = time_limit if time_limit is not None else script_defaults.get("time_limit")
        gpus = gpus if gpus is not None else int(script_defaults.get("gpus") or 0)

        # concurrency check against currently active tracked jobs
        states, queue_ok = await self._queue_states()
        active = await self._tracker.count_active(states, queue_ok=queue_ok)
        eff = slurm_policy.validate_job_request(
            self._cfg.slurm,
            partition=partition,
            nodes=nodes,
            ntasks=ntasks,
            cpus_per_task=cpus_per_task,
            memory=memory,
            time_limit=time_limit,
            gpus=gpus,
            active_jobs=active,
        )

        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", job_name or "job")[:64]

        script = self._render_script(
            job_name=safe_name,
            cwd=real_cwd,
            cmd_argv=cmd_argv,
            eff=eff,
            environment=env_values,
        )

        res = await self._submit_via_stdin(script)

        job_id = res.stdout_text.strip().split(";")[0].strip()
        if not _JOB_ID_RE.match(job_id):
            raise RemoteCommandError(f"Could not parse sbatch job id from: {res.stdout_text!r}")

        # Cross-check with Slurm itself: never register a job ID we cannot
        # confirm actually exists and belongs to this submission (defends
        # against a tampered remote sbatch wrapper minting fake ownership).
        verify = await self._ssh.run(
            ["squeue", "-h", "-j", job_id, "-o", "%i"], check=False
        )
        seen = {ln.strip() for ln in verify.stdout_text.splitlines() if ln.strip()}
        if verify.exit_code != 0 or job_id not in seen:
            acct = await self._ssh.run(
                ["sacct", "-j", job_id, "-n", "-X", "-o", "JobID"], check=False
            )
            seen_acct = {ln.strip().split(".")[0] for ln in acct.stdout_text.splitlines() if ln.strip()}
            if job_id not in seen_acct:
                raise RemoteCommandError(
                    f"sbatch returned job id {job_id} but Slurm does not know it; refusing to track it"
                )

        try:
            job_dir = await self._tracker.ensure_job_dir(job_id)
            await self._tracker.prepare_output_links(job_id)
            await self._tracker.register(
                job_id, job_name=safe_name, project_root=real_cwd, job_dir=job_dir
            )
        except Exception as exc:
            # A submitted job without an ownership record would either leak
            # resources or become unmanageable under a shared account, so the
            # job is rolled back.  The rollback is stated in the error itself:
            # a bare "submit failed" would leave the agent looking for a job
            # that is already cancelled.
            rolled_back = False
            try:
                cancel = await self._ssh.run(["scancel", "--", job_id], check=False)
                rolled_back = cancel.exit_code == 0
            except Exception:  # noqa: BLE001 - never mask the original failure
                pass
            if rolled_back:
                note = (
                    f"\n\nJob {job_id} reached Slurm but could not be registered as "
                    "owned by this MCP session, so it was cancelled automatically."
                )
            else:
                note = (
                    f"\n\nJob {job_id} reached Slurm but could not be registered as "
                    "owned by this MCP session, and the automatic rollback (scancel) "
                    "did not succeed either: the job may still be queued and is not "
                    "manageable through this MCP instance."
                )
            message = getattr(exc, "user_message", None) or str(exc)
            if isinstance(exc, HpcMcpError):
                # Same error type and exit code, plus what happened to the job.
                exc.user_message = message + note
                raise
            raise RemoteCommandError(message + note) from exc
        return {
            "job_id": job_id,
            "job_name": safe_name,
            "partition": eff["partition"],
            "working_directory": real_cwd,
            "job_dir": job_dir,
            "stdout_path": f"{job_dir}/stdout.log",
            "stderr_path": f"{job_dir}/stderr.log",
            "time_limit": eff["time_limit"],
        }

    async def _submit_via_stdin(self, script: str):
        await self._tracker.ensure_ready()
        res = await self._ssh.run_raw(
            "sbatch --parsable",
            stdin_text=script,
            check=True,
        )
        return res

    @staticmethod
    def _validate_command(command: list[str] | str) -> list[str]:
        if isinstance(command, str):
            values = [command]
        elif isinstance(command, list):
            values = command
        else:
            raise SlurmPolicyError("command must be a string or argv list")
        if not values or len(values) > 256:
            raise SlurmPolicyError("command must contain between 1 and 256 argv items")
        result: list[str] = []
        total = 0
        for value in values:
            if not isinstance(value, str) or not value or len(value) > 4096:
                raise SlurmPolicyError("each command argv item must be a non-empty string of at most 4096 bytes")
            if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
                raise SlurmPolicyError("command argv may not contain control characters")
            result.append(value)
            total += len(value)
        if total > 128 * 1024:
            raise SlurmPolicyError("command argv exceeds the script size limit")
        return result

    #: #SBATCH directives that map to submit parameters
    _SBATCH_PARAMS = {
        "--partition": "partition",
        "--nodes": "nodes",
        "--ntasks": "ntasks",
        "--cpus-per-task": "cpus_per_task",
        "--mem": "memory",
        "--time": "time_limit",
        "--gres=gpu": "gpus",
    }

    async def _read_sbatch_defaults(self, working_directory: str, cmd_argv: list[str]) -> dict:
        """Parse #SBATCH directives from a user .sh script to serve as defaults.

        Only used when ``command`` is a single path to a ``.sh`` script inside
        the user root.  Values from the agent's explicit arguments win over
        these; the partition hint still has to pass the allow-list policy.
        """
        if len(cmd_argv) != 1 or not cmd_argv[0].endswith(".sh"):
            return {}
        from ..filesystem.service import FileService

        fs = FileService(self._cfg, self._ssh)
        script_path = await fs.resolve_existing(cmd_argv[0])
        if not script_path.startswith(self._cfg.root + "/") and script_path != self._cfg.root:
            raise SlurmPolicyError("Job script must be inside the configured user root")
        res = await self._ssh.run(["cat", "--", script_path], check=False, max_output=256 * 1024)
        if res.exit_code != 0:
            raise SlurmPolicyError(f"Could not read job script {cmd_argv[0]!r}")
        defaults: dict = {}
        for line in res.stdout_text.splitlines()[:200]:
            line = line.strip()
            if not line.startswith("#SBATCH"):
                continue
            directive = line[len("#SBATCH"):].strip()
            # support "--flag=value", "--flag value" and "--gres=gpu:N" forms
            if directive.startswith("--gres="):
                # --gres=gpu:N (or --gres=gpu:N:...) -> gpus=N
                gres_spec = directive[len("--gres="):]
                defaults["gpus"] = gres_spec.split(":")[1] if ":" in gres_spec else gres_spec
                continue
            flag, value = directive.split("=", 1) if "=" in directive else directive.split(" ", 1)
            flag = flag.strip()
            value = value.strip()
            if not flag or not value:
                continue
            if flag in ("--partition", "--nodes", "--ntasks", "--cpus-per-task", "--mem", "--time"):
                defaults[self._SBATCH_PARAMS[flag]] = value
        return defaults

    @staticmethod
    def _validate_environment(environment: dict[str, str] | None) -> dict[str, str]:
        if environment is None:
            return {}
        if not isinstance(environment, dict) or len(environment) > 128:
            raise SlurmPolicyError("environment must contain at most 128 variables")
        result: dict[str, str] = {}
        total = 0
        for key, value in environment.items():
            if not isinstance(key, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                raise SlurmPolicyError(f"Invalid environment variable name: {key!r}")
            if not isinstance(value, str) or len(value) > 8192 or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
                raise SlurmPolicyError("Environment values must be bounded strings without control characters")
            result[key] = value
            total += len(key) + len(value)
        if total > 256 * 1024:
            raise SlurmPolicyError("environment exceeds the script size limit")
        return result

    def _render_script(self, *, job_name, cwd, cmd_argv, eff, environment) -> str:
        lines = [
            "#!/bin/bash",
            f"#SBATCH --job-name={job_name}",
            f"#SBATCH --partition={eff['partition']}",
            f"#SBATCH --nodes={eff['nodes']}",
            f"#SBATCH --ntasks={eff['ntasks']}",
            f"#SBATCH --cpus-per-task={eff['cpus_per_task']}",
            f"#SBATCH --time={eff['time_limit']}",
            f"#SBATCH --chdir={cwd}",
        ]
        if eff.get("memory_mb"):
            lines.append(f"#SBATCH --mem={eff['memory_mb']}M")
        if eff.get("gpus"):
            lines.append(f"#SBATCH --gres=gpu:{eff['gpus']}")
        # stdout/stderr captured into the managed jobs dir (created post-submit)
        # We know the id only after sbatch; use %j so Slurm fills it in.
        lines.append(f"#SBATCH --output={self._cfg.jobs_dir}/%j.stdout.log")
        lines.append(f"#SBATCH --error={self._cfg.jobs_dir}/%j.stderr.log")
        lines.append("")
        for k, v in environment.items():
            lines.append(f"export {k}={shlex.quote(str(v))}")
        lines.append("")
        lines.append(" ".join(shlex.quote(c) for c in cmd_argv))
        lines.append("")
        return "\n".join(lines)

    # -- queries --------------------------------------------------------------

    #: Upper bound of job IDs sent to a single ``squeue -j`` invocation.  Kept
    #: well below argv limits so a long-lived tracking file cannot produce an
    #: un-runnable command line; larger sets are queried in batches.
    _SQUEUE_BATCH = 500

    async def _tracked_ids(self) -> list[str]:
        return await self._tracker.list_owned_job_ids()

    async def _query_squeue(self, job_ids: list[str], fmt: str) -> tuple[list[str], bool]:
        """Run ``squeue`` for exactly the given tracked job IDs.

        Never queries the whole shared-account queue: other users' jobs must
        not even reach this process.  Returns the raw output lines plus
        whether every invocation succeeded: a failed query also produces no
        lines, and callers that treat "not listed" as "no longer queued"
        must be able to tell the two apart.
        """
        if not job_ids:
            return [], True
        lines: list[str] = []
        ok = True
        for start in range(0, len(job_ids), self._SQUEUE_BATCH):
            chunk = job_ids[start : start + self._SQUEUE_BATCH]
            res = await self._ssh.run(
                ["squeue", "-h", "-j", ",".join(chunk), "-o", fmt], check=False
            )
            if res.exit_code == 0:
                lines.extend(res.stdout_text.splitlines())
            else:
                ok = False
        return lines, ok

    async def _queue_states(self) -> tuple[dict[str, str], bool]:
        """States of *tracked* active jobs only (never the whole account).

        Returns ``(states, query_ok)``; ``query_ok`` is False when squeue
        could not be asked at all, so an empty ``states`` map means either
        "nothing is queued" or "the query failed".
        """
        states: dict[str, str] = {}
        lines, ok = await self._query_squeue(await self._tracked_ids(), "%i %T")
        for line in lines:
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit():
                states[parts[0]] = parts[1]
        return states, ok

    async def status(self, job_id: str) -> dict:
        entry = await self._tracker.require_owned(job_id)
        states, _queue_ok = await self._queue_states()
        if job_id in states:
            return {"job_id": job_id, "state": states[job_id], "source": "squeue"}
        # fall back to accounting for finished jobs
        res = await self._ssh.run(
            ["sacct", "-j", job_id, "--format=JobID,State,ExitCode", "-n", "-P", "-X"],
            check=False,
        )
        if res.exit_code == 0 and res.stdout_text.strip():
            parts = res.stdout_text.strip().split("|")
            state = parts[1] if len(parts) > 1 else "UNKNOWN"
            exit_code = parts[2] if len(parts) > 2 else None
            return {
                "job_id": job_id,
                "state": state.strip(),
                "exit_code": exit_code,
                "source": "sacct",
                "job_dir": entry.get("job_dir"),
            }
        return {"job_id": job_id, "state": "UNKNOWN", "source": "none", "job_dir": entry.get("job_dir")}

    async def queue(self) -> list[dict]:
        mine = {e["job_id"]: e for e in await self._tracker.list_mine()}
        jobs: list[dict] = []
        lines, _queue_ok = await self._query_squeue(list(mine), "%i|%j|%T|%M|%l|%D|%R")
        for line in lines:
            parts = line.split("|", 6)
            if len(parts) == 7 and parts[0] in mine:
                jobs.append(
                    {
                        "job_id": parts[0],
                        "name": parts[1],
                        "state": parts[2],
                        "elapsed": parts[3],
                        "time_limit": parts[4],
                        "nodes": parts[5],
                        "reason": parts[6],
                    }
                )
        return jobs

    async def accounting(self, job_id: str) -> dict:
        await self._tracker.require_owned(job_id)
        fmt = "JobID,JobName,Elapsed,CPUTimeRAW,MaxRSS,State,ExitCode,NodeList,AllocCPUS"
        res = await self._ssh.run(["sacct", "-j", job_id, f"--format={fmt}", "-n", "-P", "-X"], check=True)
        line = res.stdout_text.strip().splitlines()
        if not line:
            raise RemoteCommandError(f"No accounting data for job {job_id} yet")
        parts = line[0].split("|")
        keys = ["job_id", "job_name", "elapsed", "cpu_time_raw", "max_rss", "state", "exit_code", "node_list", "alloc_cpus"]
        data = dict(zip(keys, parts))
        return data

    async def cancel(self, job_id: str) -> dict:
        await self._tracker.require_owned(job_id)
        await self._ssh.run(["scancel", "--", job_id], check=True)
        return {"job_id": job_id, "cancelled": True}

    async def output(self, job_id: str, *, stream: str = "stdout", tail_bytes: int | None = None) -> dict:
        entry = await self._tracker.require_owned(job_id)
        if stream not in ("stdout", "stderr"):
            raise SlurmPolicyError("stream must be 'stdout' or 'stderr'", requested=stream)
        job_dir = await self._tracker.ensure_job_dir(job_id)
        path = f"{job_dir}/{stream}.log"
        if tail_bytes is None:
            cap = self._cfg.shell.max_output_bytes
        elif isinstance(tail_bytes, bool) or not isinstance(tail_bytes, int) or tail_bytes < 0:
            raise SlurmPolicyError("tail_bytes must be a non-negative integer", requested=str(tail_bytes))
        else:
            cap = min(tail_bytes, self._cfg.shell.max_output_bytes)
        res = await self._ssh.run(
            ["tail", "-c", str(cap), "--", path], check=False, max_output=cap + 4096
        )
        return {
            "job_id": job_id,
            "stream": stream,
            "path": path,
            "content": res.stdout_text if res.exit_code == 0 else "",
            "available": res.exit_code == 0,
        }

    async def wait(self, job_id: str, *, timeout_seconds: int | None = None, poll_interval: int = 10) -> dict:
        entry = await self._tracker.require_owned(job_id)
        if timeout_seconds is None:
            deadline = self._cfg.wait_max_seconds
        elif isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 0:
            raise SlurmPolicyError("timeout_seconds must be a non-negative integer", requested=str(timeout_seconds))
        else:
            deadline = min(timeout_seconds, self._cfg.wait_max_seconds)
        terminal = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED"}
        waited = 0.0
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, int) or poll_interval <= 0:
            raise SlurmPolicyError("poll_interval must be a positive integer", requested=str(poll_interval))
        poll = max(2, min(poll_interval, 60))
        while True:
            st = await self.status(job_id)
            state = str(st.get("state", "UNKNOWN")).split("+")[0].strip()
            if state in terminal:
                st["waited_seconds"] = round(waited, 1)
                st["job_dir"] = entry.get("job_dir")
                return st
            if waited >= deadline:
                raise RemoteCommandError(
                    f"Job {job_id} did not reach a terminal state within {deadline}s "
                    f"(last state: {state}). Poll again or raise the wait timeout.",
                    exit_code=None,
                )
            await asyncio.sleep(poll)
            waited += poll

    async def wait_and_diagnose(
        self,
        job_id: str,
        *,
        timeout_seconds: int | None = None,
        poll_interval: int = 10,
        stdout_lines: int | None = None,
        stderr_lines: int | None = None,
    ) -> dict:
        """Wait for a tracked job to finish, then diagnose it in one call.

        Replaces the agent's ``wait -> status -> output -> accounting -> read``
        sequence: the wait loop uses only owned-job status queries (never a
        whole-account scan) and, once the job is terminal, delegates to
        :meth:`diagnose` with the same bounded tails and error scan.  If the
        job is already terminal the wait returns immediately.
        """
        await self._tracker.require_owned(job_id)
        terminal = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED"}
        waited = 0.0
        if timeout_seconds is None:
            deadline = self._cfg.wait_max_seconds
        elif isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 0:
            raise SlurmPolicyError("timeout_seconds must be a non-negative integer", requested=str(timeout_seconds))
        else:
            deadline = min(timeout_seconds, self._cfg.wait_max_seconds)
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, int) or poll_interval <= 0:
            raise SlurmPolicyError("poll_interval must be a positive integer", requested=str(poll_interval))
        poll = max(2, min(poll_interval, 60))

        while True:
            st = await self.status(job_id)
            state = str(st.get("state", "UNKNOWN")).split("+")[0].strip()
            if state in terminal:
                result = await self.diagnose(
                    job_id,
                    stdout_lines=stdout_lines,
                    stderr_lines=stderr_lines,
                    include_accounting=True,
                    include_error_scan=True,
                )
                result["waited_seconds"] = round(waited, 1)
                return result
            if waited >= deadline:
                raise RemoteCommandError(
                    f"Job {job_id} did not reach a terminal state within {deadline}s "
                    f"(last state: {state}). Poll again or raise the wait timeout.",
                    exit_code=None,
                )
            await asyncio.sleep(poll)
            waited += poll

    # -- diagnose -------------------------------------------------------------

    #: Error signatures looked up in job stderr/stdout when diagnosing.
    _DIAG_SIGNATURES = {
        "oom": ("out of memory", "oom", "killed process", "cannot allocate memory"),
        "segfault": ("segmentation fault", "segfault", "signal 11"),
        "timeout": ("time limit", "job killed after", "srun: job timed out", "cancelled at"),
        "gpu_error": ("cuda error", "out of memory (gpu)", "no gpu", "illegal memory access", "nvidia"),
        "mpi_error": ("mpi", "rank", "aborting", "srun: error"),
        "missing_file": ("no such file", "not found", "cannot open", "unable to open"),
    }

    async def diagnose(
        self,
        job_id: str,
        *,
        stdout_lines: int | None = None,
        stderr_lines: int | None = None,
        include_accounting: bool = True,
        include_error_scan: bool = True,
    ) -> dict:
        """One-call job diagnosis for the agent.

        Internally performs an ownership check, then a bounded status query,
        optional accounting, bounded stdout/stderr tails and a bounded error
        signature scan -- all restricted to this one tracked job.  This is the
        high-level replacement for the agent's
        ``status -> output -> accounting -> read`` sequence.
        """
        await self._tracker.require_owned(job_id)
        cap = self._cfg.files.search_max_matches
        out_lines = self._bounded_int_arg(stdout_lines, 80, "stdout_lines", cap=200)
        err_lines = self._bounded_int_arg(stderr_lines, 80, "stderr_lines", cap=200)

        job = await self.status(job_id)
        result: dict = {"job": job}

        if include_accounting:
            try:
                result["accounting"] = await self.accounting(job_id)
            except RemoteCommandError:
                # accounting is empty for pending/just-started jobs; not fatal
                result["accounting"] = None

        stdout_tail = await self.output(job_id, stream="stdout", tail_bytes=out_lines * 4096)
        stderr_tail = await self.output(job_id, stream="stderr", tail_bytes=err_lines * 4096)
        result["stdout_tail"] = {
            "lines": out_lines,
            "content": stdout_tail["content"],
            "available": stdout_tail["available"],
            "bytes": len(stdout_tail["content"].encode("utf-8", errors="replace")),
        }
        result["stderr_tail"] = {
            "lines": err_lines,
            "content": stderr_tail["content"],
            "available": stderr_tail["available"],
            "bytes": len(stderr_tail["content"].encode("utf-8", errors="replace")),
        }

        if include_error_scan:
            text = (result["stderr_tail"]["content"] + "\n" + result["stdout_tail"]["content"])
            result["diagnostics"] = self._scan_signatures(text, cap)

        return result

    @staticmethod
    def _scan_signatures(text: str, cap: int) -> dict:
        """Scan bounded log text for common failure signatures.

        Returns e.g. ``{"oom": true, "timeout": false, ...}`` plus the first
        few matching lines per signature.  The scan is cheap and only as
        accurate as the tail it receives -- it is a hint for the agent, not a
        substitute for reading the log.
        """
        low = text.lower()
        found: dict = {}
        for name, needles in SlurmManager._DIAG_SIGNATURES.items():
            hits: list[str] = []
            for line in text.splitlines():
                l = line.lower()
                if any(n in l for n in needles):
                    hits.append(line.strip()[:300])
                    if len(hits) >= cap:
                        break
            found[name] = bool(hits)
            if hits:
                found[f"{name}_lines"] = hits[:5]
        return found

    @staticmethod
    def _bounded_int_arg(value: int | None, default: int, name: str, *, cap: int) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SlurmPolicyError(f"{name} must be a non-negative integer", requested=str(value))
        return min(value, cap)
