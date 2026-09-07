"""Command cost policy tests: legal commands still pay a time/output budget."""

import pytest

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.security.command_cost import (
    HIGH,
    LOW,
    MEDIUM,
    clamp_output,
    clamp_timeout,
    tier_for,
)
from hpc_mcp.shell.safe_exec import SafeExec
from hpc_mcp.ssh.manager import RemoteResult

ROOT = "/home/shared_account/alice"


def make_cfg() -> Config:
    return Config(root=ROOT, ssh=SshConfig(host="h", user="u"))


class TestTierFor:
    def test_cheap_commands_low(self):
        assert tier_for(["ls"]) is LOW
        assert tier_for(["pwd"]) is LOW
        assert tier_for(["head"]) is LOW

    def test_medium_scans(self):
        assert tier_for(["grep"]) is MEDIUM
        assert tier_for(["cat"]) is MEDIUM

    def test_expensive_commands_high(self):
        assert tier_for(["find"]) is HIGH
        assert tier_for(["du"]) is HIGH
        assert tier_for(["sort"]) is HIGH

    def test_git_subcommand_refines(self):
        assert tier_for(["git", "status"]) is LOW
        assert tier_for(["git", "diff"]) is MEDIUM
        assert tier_for(["git", "grep"]) is HIGH
        assert tier_for(["git", "log"]) is HIGH

    def test_unknown_falls_back_to_medium(self):
        assert tier_for(["somecmd"]) is MEDIUM

    def test_clamps(self):
        # timeout never exceeds tier cap or server cap
        assert clamp_timeout(LOW, None, 30) == 15
        assert clamp_timeout(HIGH, 60, 30) == 10
        assert clamp_timeout(LOW, 5, 30) == 5
        # output never exceeds tier cap or server cap
        assert clamp_output(LOW, 1_000_000) == 256 * 1024
        assert clamp_output(HIGH, 128 * 1024) == 128 * 1024


class RecordingSsh:
    def __init__(self):
        self.remote_cmds: list[str] = []

    async def run(self, argv, **kw):
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def run_raw(self, cmd, *, timeout=None, max_output=None, **kw):
        self.remote_cmds.append(cmd)
        self.last_timeout = timeout
        self.last_max_output = max_output
        return RemoteResult(stdout=b"ok\n", stderr=b"", exit_code=0)

    async def realpath(self, path):
        return path


@pytest.mark.asyncio
class TestSafeExecCostEnforcement:
    async def test_find_gets_high_tier_timeout(self):
        ssh = RecordingSsh()
        se = SafeExec(make_cfg(), ssh)
        out = await se.run("find . -maxdepth 2", cwd=ROOT)
        assert out["cost_tier"] == "high"
        assert out["timeout_seconds"] == 10  # HIGH tier cap
        assert "timeout 10" in ssh.remote_cmds[0]

    async def test_ls_gets_low_tier_timeout(self):
        ssh = RecordingSsh()
        se = SafeExec(make_cfg(), ssh)
        out = await se.run("ls -la", cwd=ROOT)
        assert out["cost_tier"] == "low"
        assert out["timeout_seconds"] == 15

    async def test_user_timeout_clamped_by_tier(self):
        ssh = RecordingSsh()
        se = SafeExec(make_cfg(), ssh)
        # find is HIGH tier (10s cap); requesting 120s must be clamped to 10
        out = await se.run("find . -name '*.jl'", cwd=ROOT, timeout=120)
        assert out["timeout_seconds"] == 10

    async def test_git_grep_high_tier(self):
        ssh = RecordingSsh()
        se = SafeExec(make_cfg(), ssh)
        out = await se.run("git grep TODO", cwd=ROOT)
        assert out["cost_tier"] == "high"
