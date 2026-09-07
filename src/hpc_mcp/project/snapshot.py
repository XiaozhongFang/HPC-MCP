"""Project snapshot (``hpc.project.snapshot``).

One bounded call that gives the agent enough context to start working on a
project: a shallow directory overview, selected file metadata, a read-only
git status summary and the tracked jobs associated with the project.  Every
internal query is individually budgeted -- the snapshot never recursively
scans the whole project.
"""

from __future__ import annotations

import posixpath

from ..config import Config
from ..errors import PathSandboxError
from ..filesystem.service import FileService
from ..security.path_policy import is_within
from ..shell.safe_exec import SafeExec
from ..slurm.jobs import JobTracker
from ..ssh.manager import SshManager

#: File name suffix patterns worth reporting sizes for in the snapshot.
_INTERESTING_SUFFIXES = (".jl", ".py", ".sh", ".c", ".cpp", ".h", ".toml", ".yaml", ".yml", ".json", ".md", ".out", ".log")


class ProjectService:
    def __init__(self, cfg: Config, ssh: SshManager, files: FileService, safe_exec: SafeExec, tracker: JobTracker) -> None:
        self._cfg = cfg
        self._ssh = ssh
        self._files = files
        self._safe = safe_exec
        self._tracker = tracker

    async def snapshot(
        self,
        path: str,
        *,
        depth: int | None = None,
        include_git: bool = True,
        include_jobs: bool = True,
    ) -> dict:
        real = await self._files.resolve_existing(path)
        if not is_within(real, self._cfg.root):
            raise PathSandboxError("Snapshot path escapes the configured user root", requested=path)

        depth_cap = min(depth if isinstance(depth, int) and depth >= 1 else 2, self._cfg.files.max_recursive_depth)
        page = min(self._cfg.files.max_list_entries, 500)

        listing = await self._files.list_dir(real, recursive=True, page_size=page, max_depth=depth_cap)

        interesting: list[dict] = []
        for entry in listing["entries"]:
            if entry["type"] == "file" and entry["name"].endswith(_INTERESTING_SUFFIXES):
                interesting.append(entry)
        interesting = interesting[:50]

        git: dict | None = None
        if include_git:
            git = await self._git_status(real)

        jobs: list[dict] = []
        if include_jobs:
            jobs = await self._tracked_jobs_for(real)

        return {
            "path": real,
            "depth": depth_cap,
            "tree": {
                "entries": listing["entries"][:page],
                "truncated": listing["truncated"],
                "next_cursor": listing["next_cursor"],
            },
            "interesting_files": interesting,
            "git": git,
            "jobs": jobs,
        }

    async def _git_status(self, real: str) -> dict | None:
        """Read-only git status/branch summary; None when not a git repo."""
        try:
            branch = await self._safe.run("git rev-parse --abbrev-ref HEAD", cwd=real)
        except Exception:
            return None
        if branch.get("exit_code") != 0 or not branch.get("stdout", "").strip():
            return None
        try:
            status = await self._safe.run("git status --porcelain", cwd=real)
        except Exception:
            status = {"stdout": "", "exit_code": 0}
        lines = [ln for ln in status.get("stdout", "").splitlines() if ln.strip()]
        return {
            "branch": branch.get("stdout", "").strip() or "detached",
            "modified": sum(1 for ln in lines if ln.startswith(" M") or ln.startswith("M")),
            "untracked": sum(1 for ln in lines if ln.startswith("??")),
            "staged": sum(1 for ln in lines if ln.startswith(("A", "M", "D")) and not ln.startswith("M ")),
            "porcelain_count": len(lines),
        }

    async def _tracked_jobs_for(self, real: str) -> list[dict]:
        """Tracked jobs whose project_root equals the snapshot path."""
        mine = await self._tracker.list_mine()
        out = []
        for entry in mine:
            if entry.get("project_root") == real:
                out.append(
                    {
                        "job_id": entry["job_id"],
                        "job_name": entry.get("job_name"),
                        "created_at": entry.get("created_at"),
                    }
                )
        out.sort(key=lambda j: j["job_id"], reverse=True)
        return out[:20]
