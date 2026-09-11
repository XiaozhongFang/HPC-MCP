"""Uploaded job scripts must be pointed at hpc.slurm.submit, not at chmod.

A script uploaded through SFTP has no executable bit, and the login node
denies both ``chmod`` and direct execution -- so the only workable path is
``hpc.slurm.submit``, which runs the script with bash and reads its #SBATCH
directives.  The tools say so explicitly instead of letting the agent burn
turns on permission errors.
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


class FakeTransfer:
    """Upload stub recording the call and returning the remote path."""

    def __init__(self):
        self.calls = []

    async def upload(self, local_path, remote_path):
        self.calls.append((local_path, remote_path))
        return {"local_path": local_path, "remote_path": remote_path, "bytes": 42}

    async def download(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("download must not be used here")


def _tools(cfg, transfer):
    ssh = FakeSsh()
    files = FileService(cfg, ssh)
    slurm = SlurmManager(cfg, ssh, JobTracker(cfg, ssh))
    return build_tools(cfg, ssh, files, transfer, SafeExec(cfg, ssh), slurm)


def _tool(cfg, name, transfer):
    return next(t for t in _tools(cfg, transfer) if t.name == name)


class TestUploadScriptHint:
    @pytest.mark.asyncio
    async def test_shell_script_upload_points_at_slurm_submit(self):
        transfer = FakeTransfer()
        tool = _tool(Config(root=ROOT), "hpc.files.upload", transfer)
        result = await tool.handler(
            {"local_path": "/tmp/run.sh", "remote_path": f"{ROOT}/.hpc-mcp/tmp/run.sh"}
        )
        hint = result["submit_hint"]
        assert "hpc.slurm.submit" in hint
        assert "executable bit" in hint
        assert "chmod" in hint

    @pytest.mark.asyncio
    async def test_non_script_upload_gets_no_hint(self):
        transfer = FakeTransfer()
        tool = _tool(Config(root=ROOT), "hpc.files.upload", transfer)
        result = await tool.handler(
            {"local_path": "/tmp/input.i", "remote_path": f"{ROOT}/input.i"}
        )
        assert "submit_hint" not in result
        assert transfer.calls == [("/tmp/input.i", f"{ROOT}/input.i")]


class TestSubmitDescription:
    def test_submit_description_states_the_script_contract(self):
        description = _tool(Config(root=ROOT), "hpc.slurm.submit", FakeTransfer()).description
        assert "bash" in description
        assert "executable bit" in description
        assert "session_tmp_dir" in description

    def test_upload_description_states_the_script_contract(self):
        tool = _tool(Config(root=ROOT), "hpc.files.upload", FakeTransfer())
        assert "hpc.slurm.submit" in tool.description
        assert "executable bit" in tool.description
