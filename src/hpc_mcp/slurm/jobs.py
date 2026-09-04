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

    async def _ensure_dirs(self) -> None:
        if self._initialized:
            return
        await self._ssh.run(["mkdir", "-p", "--", self._jobs_dir], check=True)
        self._initialized = True

    async def _read_register(self) -> dict:
        res = await self._ssh.run(
            ["cat", self._register_file], check=False, max_output=4 * 1024 * 1024
        )
        if res.exit_code != 0 or not res.stdout_text.strip():
            return {}
        try:
            data = json.loads(res.stdout_text)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    async def _write_register(self, data: dict) -> None:
        payload = json.dumps(data, indent=2)
        import shlex

        await self._ssh.run(
            ["sh", "-c", f"cat > {shlex.quote(self._register_file)} <<'HPCMCP_EOF'\n{payload}\nHPCMCP_EOF"],
            check=True,
        )

    async def register(self, job_id: str, *, job_name: str, project_root: str, job_dir: str) -> None:
        await self._ensure_dirs()
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

    async def require_owned(self, job_id: str) -> dict:
        """Return the tracking entry, or deny if this instance doesn't own it."""
        if not job_id or not job_id.isdigit():
            raise SlurmPolicyError(
                "job_id must be a numeric Slurm job ID registered by this server",
                requested=str(job_id),
            )
        data = await self._read_register()
        entry = data.get(job_id)
        if entry is None:
            raise SlurmPolicyError(
                f"Job {job_id} was not submitted by this MCP server instance. "
                "For shared-account safety, only jobs tracked by this instance can be managed. "
                "Submit your job via hpc.slurm.submit first.",
                requested=job_id,
            )
        return entry

    async def list_mine(self) -> list[dict]:
        data = await self._read_register()
        return list(data.values())

    async def count_active(self, states_by_id: dict[str, str]) -> int:
        """Count tracked jobs still in a non-terminal state."""
        terminal = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL"}
        data = await self._read_register()
        active = 0
        for jid in data:
            st = states_by_id.get(jid, "UNKNOWN")
            if st not in terminal:
                active += 1
        return active
