import asyncio

import pytest

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.ssh.manager import SshManager


class _FakeProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.stdin = None
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


@pytest.mark.asyncio
async def test_run_does_not_require_a_local_control_socket(monkeypatch):
    calls: list[list[str]] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        calls.append(list(argv))
        return _FakeProcess(stdout=b"OK\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    cfg = Config(root="/home/u/me", ssh=SshConfig(host="h", user="u"))
    manager = SshManager(cfg)

    try:
        result = await manager.run(["echo", "OK"])
    finally:
        await manager.close()

    assert result.stdout_text == "OK\n"
    assert len(calls) == 1
    assert not any(arg.startswith("ControlPath=") for arg in calls[0])
    assert "ControlMaster=auto" not in calls[0]
    assert "ControlMaster=no" in calls[0]
