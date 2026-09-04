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
from ..errors import RemoteCommandError, SlurmPolicyError
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

    # -- submit ----------------------------------------------------------------

    async def submit(
        self,
        *,
        job_name: str,
        working_directory: str,
        command: list[str] | str,
        partition: str | None = None,
        nodes: int = 1,
        ntasks: int = 1,
        cpus_per_task: int = 1,
        memory: str | int | None = None,
        time_limit: str | None = None,
        gpus: int = 0,
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
        nodes: int = 1,
        ntasks: int = 1,
        cpus_per_task: int = 1,
        memory: str | int | None = None,
        time_limit: str | None = None,
        gpus: int = 0,
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

        # concurrency check against currently active tracked jobs
        states = await self._queue_states()
        active = await self._tracker.count_active(states)
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
        except Exception:
            # A submitted job without an ownership record would either leak
            # resources or become unmanageable under a shared account.
            await self._ssh.run(["scancel", "--", job_id], check=False)
            raise
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

    async def _queue_states(self) -> dict[str, str]:
        res = await self._ssh.run(
            ["squeue", "-h", "-o", "%i %T"], check=False
        )
        states: dict[str, str] = {}
        if res.exit_code == 0:
            for line in res.stdout_text.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0].isdigit():
                    states[parts[0]] = parts[1]
        return states

    async def status(self, job_id: str) -> dict:
        entry = await self._tracker.require_owned(job_id)
        states = await self._queue_states()
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
        res = await self._ssh.run(
            ["squeue", "-h", "-o", "%i|%j|%T|%M|%l|%D|%R"], check=False
        )
        jobs: list[dict] = []
        if res.exit_code == 0:
            for line in res.stdout_text.splitlines():
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
