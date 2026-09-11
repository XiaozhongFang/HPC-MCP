"""Scratch-directory conventions handed to the agent.

Temporary agent files (test scripts, test logs, intermediate artifacts) must
have exactly one home so a finished task can be cleaned up with a single
recursive delete instead of hunting stray files through the project tree.
"""

import pytest

from hpc_mcp.config import Config
from hpc_mcp.filesystem.service import FileService
from hpc_mcp.shell.safe_exec import SafeExec
from hpc_mcp.slurm.jobs import JobTracker
from hpc_mcp.slurm.manager import SlurmManager
from hpc_mcp.ssh.manager import RemoteResult
from hpc_mcp.tools.registry import build_tools

ROOT = "/home/u/me"


class FakeSsh:
    async def probe(self):
        return {"slurm_available": "True", "cluster": "testcluster"}

    async def run(self, argv, **kw):
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def run_raw(self, cmd, **kw):
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def realpath(self, path):
        return path

    async def close(self):
        return None


class StubTransfer:
    async def upload(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("transfer must not be used by hpc.info")

    async def download(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("transfer must not be used by hpc.info")


def _info_tool(cfg):
    ssh = FakeSsh()
    files = FileService(cfg, ssh)
    slurm = SlurmManager(cfg, ssh, JobTracker(cfg, ssh))
    tools = build_tools(cfg, ssh, files, StubTransfer(), SafeExec(cfg, ssh), slurm)
    return next(t for t in tools if t.name == "hpc.info")


class TestScratchPaths:
    def test_tmp_dir_lives_in_the_managed_metadata_directory(self):
        assert Config(root=ROOT).tmp_dir == f"{ROOT}/.hpc-mcp/tmp"

    def test_tmp_dir_ignores_a_trailing_slash_on_root(self):
        assert Config(root=f"{ROOT}/").tmp_dir == f"{ROOT}/.hpc-mcp/tmp"

    def test_session_tmp_dir_is_one_level_below_tmp_dir(self):
        cfg = Config(root=ROOT)
        session_dir = cfg.session_tmp_dir("abc123def456")
        assert session_dir == f"{cfg.tmp_dir}/abc123def456"

    def test_session_id_cannot_escape_the_scratch_directory(self):
        """Separators and traversal characters are stripped, never escaped."""
        cfg = Config(root=ROOT)
        session_dir = cfg.session_tmp_dir("../../etc")
        assert session_dir.startswith(cfg.tmp_dir + "/")
        assert session_dir.count("/") == cfg.tmp_dir.count("/") + 1

    def test_empty_session_id_still_yields_a_named_directory(self):
        assert Config(root=ROOT).session_tmp_dir("/") == f"{ROOT}/.hpc-mcp/tmp/session"


class TestInfoAdvertisesScratchDirs:
    @pytest.mark.asyncio
    async def test_hpc_info_returns_scratch_directories(self):
        cfg = Config(root=ROOT)
        result = await _info_tool(cfg).handler({})
        assert result["tmp_dir"] == f"{ROOT}/.hpc-mcp/tmp"
        assert result["session_tmp_dir"].startswith(result["tmp_dir"] + "/")

    def test_hpc_info_description_tells_the_agent_to_clean_up(self):
        description = _info_tool(Config(root=ROOT)).description
        assert "session_tmp_dir" in description
        assert "hpc.files.delete(recursive=true)" in description
