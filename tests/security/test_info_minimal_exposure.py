"""hpc.info minimal-exposure regression: no local path roots, no SSH details."""

import pytest

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.tools.registry import build_tools
from hpc_mcp.ssh.manager import RemoteResult


class FakeInfoSsh:
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


@pytest.mark.asyncio
async def test_hpc_info_hides_local_roots_and_ssh_details():
    cfg = Config(
        root="/home/shared_account/alice",
        ssh=SshConfig(host="secret-host", user="shared_account"),
        local_roots=["/home/secret_user/my_project", "/tmp"],
    )
    # build tools with stub dependencies
    from hpc_mcp.filesystem.service import FileService
    from hpc_mcp.filesystem.transfer import TransferService
    from hpc_mcp.shell.safe_exec import SafeExec
    from hpc_mcp.slurm.jobs import JobTracker
    from hpc_mcp.slurm.manager import SlurmManager
    from hpc_mcp.ssh.sftp import SftpClient

    ssh = FakeInfoSsh()
    sftp = SftpClient(cfg)
    files = FileService(cfg, ssh)
    transfer = TransferService(cfg, sftp, files)
    safe = SafeExec(cfg, ssh)
    tracker = JobTracker(cfg, ssh)
    slurm = SlurmManager(cfg, ssh, tracker)
    tools = build_tools(cfg, ssh, files, transfer, safe, slurm)
    info_tool = next(t for t in tools if t.name == "hpc.info")

    result = await info_tool.handler({})
    text = repr(result)
    # the sensitive LOCAL path (with username) must not appear, nor the SSH host
    assert "secret_user" not in text
    assert "secret-host" not in text
    assert "/home/secret_user/my_project" not in text
    # capability booleans replace the raw roots
    assert result.get("workspace_available") is True
    assert result.get("transfer_enabled") is True
    assert "local_roots" not in result
