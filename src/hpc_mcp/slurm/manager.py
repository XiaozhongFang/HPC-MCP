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

        if isinstance(command, str):
            cmd_argv = [command]
        else:
            cmd_argv = [str(c) for c in command]
        if not cmd_argv or any("\n" in c or "\x00" in c for c in cmd_argv):
            raise SlurmPolicyError("command must be a non-empty argv without control characters")

        if environment:
            for k, v in environment.items():
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(k)):
                    raise SlurmPolicyError(f"Invalid environment variable name: {k!r}")
                if any(ch in str(v) for ch in ("\n", "\x00")):
                    raise SlurmPolicyError("Environment values may not contain control characters")

        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", job_name or "job")[:64]

        script = self._render_script(
            job_name=safe_name,
            cwd=real_cwd,
            cmd_argv=cmd_argv,
            eff=eff,
            environment=environment or {},
        )

        res = await self._submit_via_stdin(script)

        job_id = res.stdout_text.strip().split(";")[0].strip()
        if not _JOB_ID_RE.match(job_id):
            raise RemoteCommandError(f"Could not parse sbatch job id from: {res.stdout_text!r}")

        job_dir = f"{self._cfg.jobs_dir}/{job_id}"
        await self._ssh.run(["mkdir", "-p", "--", job_dir], check=True)
        await self._tracker.register(
            job_id, job_name=safe_name, project_root=real_cwd, job_dir=job_dir
        )
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
        quoted_dir = shlex.quote(self._cfg.jobs_dir)
        res = await self._ssh.run_raw(
            f"mkdir -p {quoted_dir} && sbatch --parsable",
            stdin_text=script,
            check=True,
        )
        return res

    def _render_script(self, *, job_name, cwd, cmd_argv, eff, environment) -> str:
        job_dir_placeholder = "$(scontrol show job $SLURM_JOB_ID | true)"  # unused; keep script simple
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
        lines.append(f"#SBATCH --output={self._cfg.jobs_dir}/%j/stdout.log")
        lines.append(f"#SBATCH --error={self._cfg.jobs_dir}/%j/stderr.log")
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
        path = f"{entry['job_dir']}/{stream}.log"
        cap = min(tail_bytes or self._cfg.shell.max_output_bytes, self._cfg.shell.max_output_bytes)
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
        deadline = min(timeout_seconds or self._cfg.wait_max_seconds, self._cfg.wait_max_seconds)
        terminal = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED"}
        waited = 0.0
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
