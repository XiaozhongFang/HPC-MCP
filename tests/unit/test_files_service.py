"""FileService tests with a fake SSH layer (no real network)."""

import base64

import pytest

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.errors import PathSandboxError, PolicyDenied
from hpc_mcp.filesystem.service import FileService
from hpc_mcp.ssh.manager import RemoteResult

ROOT = "/home/shared_account/fangxiaozhong"


def make_cfg() -> Config:
    return Config(root=ROOT, ssh=SshConfig(host="h", user="u"))


class FakeSsh:
    """Scriptable SSH fake. ``realpath_map`` maps path -> resolved path."""

    def __init__(self):
        self.realpath_map: dict[str, str] = {}
        self.file_sizes: dict[str, int] = {}
        self.file_data: dict[str, bytes] = {}
        self.commands: list[list[str]] = []

    async def realpath(self, path: str):
        return self.realpath_map.get(path, path)

    async def run(self, argv, *, timeout=None, max_output=4 * 1024 * 1024, check=True):
        self.commands.append(argv)
        cmd = argv[:2]
        if cmd == ["stat", "-c"]:
            path = argv[-1]
            if argv[2] == "%s":
                size = self.file_sizes.get(path, 0)
                return RemoteResult(stdout=str(size).encode(), stderr=b"", exit_code=0)
        if argv[0] == "stat" and "%F" in argv[2]:
            return RemoteResult(stdout=b"regular file|10|644|u|g|1700000000", stderr=b"", exit_code=0)
        if argv[0] == "sh" and "base64" in argv[-1]:
            # extract dd target
            data = self.file_data.get(ROOT + "/project/f.txt", b"")
            return RemoteResult(stdout=base64.b64encode(data), stderr=b"", exit_code=0)
        if argv[0] == "find":
            return RemoteResult(stdout=b"f 10 " + ROOT.encode() + b"/project/a.jl\n", stderr=b"", exit_code=0)
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def run_raw(self, cmd, **kw):
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)


@pytest.mark.asyncio
class TestReadSandbox:
    async def test_read_inside_ok(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        ssh.file_sizes[ROOT + "/project/f.txt"] = 5
        ssh.file_data[ROOT + "/project/f.txt"] = b"hello"
        out = await fs.read_file(ROOT + "/project/f.txt")
        assert out["content"] == "hello"

    async def test_read_escape_denied(self):
        fs = FileService(make_cfg(), FakeSsh())
        with pytest.raises(PathSandboxError):
            await fs.read_file("/etc/passwd")

    async def test_read_symlink_escape_denied(self):
        ssh = FakeSsh()
        ssh.realpath_map[ROOT + "/link/passwd"] = "/etc/passwd"
        fs = FileService(make_cfg(), ssh)
        with pytest.raises(PathSandboxError):
            await fs.read_file(ROOT + "/link/passwd")

    async def test_read_too_large_denied(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        big = ROOT + "/big.log"
        ssh.file_sizes[big] = 100 * 1024 * 1024
        with pytest.raises(PolicyDenied, match="read limit"):
            await fs.read_file(big)


@pytest.mark.asyncio
class TestWriteSandbox:
    async def test_write_outside_denied(self):
        fs = FileService(make_cfg(), FakeSsh())
        with pytest.raises(PathSandboxError):
            await fs.write_file("/tmp/evil.txt", "x")

    async def test_write_symlink_parent_denied(self):
        ssh = FakeSsh()
        ssh.realpath_map[ROOT + "/link"] = "/etc"
        fs = FileService(make_cfg(), ssh)
        with pytest.raises(PathSandboxError):
            await fs.write_file(ROOT + "/link/pwned", "x")

    async def test_write_too_large_denied(self):
        fs = FileService(make_cfg(), FakeSsh())
        with pytest.raises(PolicyDenied, match="write limit"):
            await fs.write_file(ROOT + "/f.txt", "x" * (11 * 1024 * 1024))


@pytest.mark.asyncio
class TestDelete:
    async def test_delete_root_denied(self):
        fs = FileService(make_cfg(), FakeSsh())
        with pytest.raises(PathSandboxError, match="user root"):
            await fs.delete(ROOT)
