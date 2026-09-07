"""ProjectService snapshot tests: bounded, sandboxed, no whole-tree scan."""

import json

import pytest

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.errors import PathSandboxError
from hpc_mcp.project.snapshot import ProjectService
from hpc_mcp.shell.safe_exec import SafeExec
from hpc_mcp.slurm.jobs import JobTracker
from hpc_mcp.ssh.manager import RemoteResult

ROOT = "/home/shared_account/alice"
PROJ = ROOT + "/proj"


def make_cfg() -> Config:
    return Config(root=ROOT, ssh=SshConfig(host="h", user="u"))


class FakeSnapSsh:
    """Combined fake for list_dir + git + tracked jobs."""

    def __init__(self) -> None:
        self.layers: dict[int, list[str]] = {}
        self.register: dict = {}
        self.git_out: dict[str, str] = {}
        self.git_code: dict[str, int] = {}
        self.commands: list[list[str]] = []

    async def realpath(self, path):
        return path

    async def run(self, argv, *, timeout=None, max_output=4 * 1024 * 1024, check=True):
        import re as _re

        self.commands.append(list(argv))
        if argv[0] == "bash" and "find" in argv[-1]:
            m = _re.search(r"-mindepth (\d+) -maxdepth (\d+)", argv[-1])
            depth = int(m.group(1))
            head_m = _re.search(r"head -n (\d+)", argv[-1])
            limit = int(head_m.group(1)) if head_m else 10**9
            lines = self.layers.get(depth, [])
            cut = len(lines) > limit
            code = 141 if cut else 0
            return RemoteResult(
                stdout=("\n".join(lines[:limit]) + "\n").encode(),
                stderr=b"", exit_code=code,
            )
        if argv[:2] == ["cat", f"{ROOT}/.hpc-mcp/tracked_jobs.json"]:
            return RemoteResult(stdout=json.dumps(self.register).encode(), stderr=b"", exit_code=0)
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def run_raw(self, cmd, *, stdin_text=None, **kw):
        import re as _re

        if "tracked_jobs.json" in cmd and "HPCMCP_EOF" in cmd:
            m = _re.search(r"HPCMCP_EOF'?\n(.*?)\nHPCMCP_EOF", cmd, _re.S)
            if m:
                self.register = json.loads(m.group(1))
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        # safe_exec runs `env -i ... git ...` through run_raw
        if "git rev-parse" in cmd:
            return RemoteResult(
                stdout=self.git_out.get("rev-parse", "main\n").encode(),
                stderr=b"", exit_code=self.git_code.get("rev-parse", 0),
            )
        if "git status" in cmd:
            return RemoteResult(
                stdout=self.git_out.get("status", " M file1.jl\n?? new.py\n").encode(),
                stderr=b"", exit_code=0,
            )
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)


@pytest.mark.asyncio
class TestSnapshot:
    def _svc(self, ssh: FakeSnapSsh) -> ProjectService:
        cfg = make_cfg()
        from hpc_mcp.filesystem.service import FileService

        files = FileService(cfg, ssh)
        safe = SafeExec(cfg, ssh)
        tracker = JobTracker(cfg, ssh)
        return ProjectService(cfg, ssh, files, safe, tracker)

    async def test_snapshot_bounded_tree_and_git(self):
        ssh = FakeSnapSsh()
        ssh.layers[1] = [f"d 0 {PROJ}/src", f"f 1200 {PROJ}/main.jl", f"f 50 {PROJ}/notes.txt"]
        ssh.layers[2] = [f"f 900 {PROJ}/src/core.jl"]
        ssh.git_out = {"rev-parse": "feature/x\n", "status": " M main.jl\n?? new.py\n"}
        out = await self._svc(ssh).snapshot(PROJ, depth=2)
        assert out["path"] == PROJ
        assert any(e["name"] == "main.jl" for e in out["tree"]["entries"])
        # interesting files include source, not plain notes
        names = [f["name"] for f in out["interesting_files"]]
        assert "main.jl" in names and "core.jl" in names
        assert out["git"]["branch"] == "feature/x"
        assert out["git"]["modified"] == 1
        assert out["git"]["untracked"] == 1

    async def test_snapshot_escape_denied(self):
        svc = self._svc(FakeSnapSsh())
        with pytest.raises(PathSandboxError):
            await svc.snapshot("/etc")

    async def test_snapshot_depth_capped(self):
        ssh = FakeSnapSsh()
        ssh.layers[1] = [f"f 10 {PROJ}/a.txt"]
        out = await self._svc(ssh).snapshot(PROJ, depth=999)
        assert out["depth"] == 3  # server cap: max_recursive_depth default

    async def test_snapshot_no_git_repo(self):
        ssh = FakeSnapSsh()
        ssh.git_code = {"rev-parse": 128}
        out = await self._svc(ssh).snapshot(PROJ)
        assert out["git"] is None

    async def test_snapshot_lists_tracked_jobs(self):
        ssh = FakeSnapSsh()
        ssh.layers[1] = [f"f 10 {PROJ}/a.txt"]
        svc = self._svc(ssh)
        # register via the tracker so tool_session matches this instance
        tracker = svc._tracker
        await tracker.ensure_ready()
        await tracker.register("1001", job_name="run1", project_root=PROJ, job_dir=f"{ROOT}/.hpc-mcp/jobs/1001")
        await tracker.register("1002", job_name="run2", project_root=ROOT + "/other", job_dir=f"{ROOT}/.hpc-mcp/jobs/1002")
        out = await svc.snapshot(PROJ, include_jobs=True)
        ids = [j["job_id"] for j in out["jobs"]]
        assert ids == ["1001"]  # only jobs of THIS project root
