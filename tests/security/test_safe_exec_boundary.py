import pytest

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.errors import CommandPolicyError
from hpc_mcp.shell.safe_exec import SafeExec
from hpc_mcp.ssh.manager import RemoteResult

ROOT = "/home/shared_account/fangxiaozhong"


class FakeSSH:
    def __init__(self):
        self.commands = []
        self.realpaths = {}

    async def realpath(self, path):
        return self.realpaths.get(path, path)

    async def run_raw(self, command, **kwargs):
        self.commands.append(command)
        return RemoteResult(b"PATH=/usr/bin:/bin\n", b"", 0)


def make_exec():
    cfg = Config(root=ROOT, ssh=SshConfig(host="h", user="u"))
    ssh = FakeSSH()
    return SafeExec(cfg, ssh), ssh


@pytest.mark.asyncio
async def test_in_root_symlink_operand_denied():
    safe, ssh = make_exec()
    ssh.realpaths[ROOT + "/link"] = "/etc/passwd"
    with pytest.raises(CommandPolicyError, match="escapes"):
        await safe.run("cat " + ROOT + "/link", cwd=ROOT)


@pytest.mark.asyncio
async def test_slurm_query_cannot_bypass_ownership():
    safe, _ = make_exec()
    with pytest.raises(CommandPolicyError, match="job isolation"):
        await safe.run("squeue", cwd=ROOT)


@pytest.mark.asyncio
async def test_environment_query_is_minimal():
    safe, ssh = make_exec()
    await safe.run("env", cwd=ROOT)
    assert "env -i PATH=/usr/bin:/bin HOME=/nonexistent" in ssh.commands[0]
    assert "GIT_CONFIG_GLOBAL=/dev/null" in ssh.commands[0]


@pytest.mark.asyncio
async def test_non_positive_timeout_denied():
    safe, _ = make_exec()
    with pytest.raises(CommandPolicyError):
        await safe.run("pwd", cwd=ROOT, timeout=0)
