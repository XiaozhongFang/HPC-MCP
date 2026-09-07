"""FileService tests with a fake SSH layer (no real network)."""

import base64

import pytest

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.errors import PathSandboxError, PolicyDenied
from hpc_mcp.filesystem.service import FileService
from hpc_mcp.ssh.manager import RemoteResult

ROOT = "/home/shared_account/alice"


def make_cfg() -> Config:
    return Config(root=ROOT, ssh=SshConfig(host="h", user="u"))


class FakeSsh:
    """Scriptable SSH fake. ``realpath_map`` maps path -> resolved path."""

    def __init__(self):
        self.realpath_map: dict[str, str] = {}
        self.file_sizes: dict[str, int] = {}
        self.file_mtimes: dict[str, int] = {}
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
                if path not in self.file_sizes:
                    return RemoteResult(stdout=b"", stderr=b"stat: no such file", exit_code=1)
                size = self.file_sizes.get(path, 0)
                return RemoteResult(stdout=str(size).encode(), stderr=b"", exit_code=0)
            if argv[2] == "%s|%Y":
                if path not in self.file_sizes:
                    return RemoteResult(stdout=b"", stderr=b"stat: no such file", exit_code=1)
                size = self.file_sizes.get(path, 0)
                mtime = self.file_mtimes.get(path, 1700000000)
                return RemoteResult(stdout=f"{size}|{mtime}".encode(), stderr=b"", exit_code=0)
        if argv[0] == "sha256sum":
            import hashlib as _hashlib

            data = self.file_data.get(argv[-1], b"")
            digest = _hashlib.sha256(data).hexdigest()
            return RemoteResult(stdout=f"{digest}  {argv[-1]}\n".encode(), stderr=b"", exit_code=0)
        if argv[0] == "stat" and "%F" in argv[2]:
            return RemoteResult(stdout=b"regular file|10|644|u|g|1700000000", stderr=b"", exit_code=0)
        if argv[0] == "sh" and "base64" in argv[-1]:
            # parse the dd command: extract the input path, skip= and count=
            import re as _re

            cmd = argv[-1]
            m_path = _re.search(r"dd if=['\"]?([^ '\"]+)", cmd)
            path = m_path.group(1) if m_path else ROOT + "/project/f.txt"
            data = self.file_data.get(path, b"")
            m_skip = _re.search(r"skip=(\d+)", cmd)
            m_count = _re.search(r"count=(\d+)", cmd)
            skip = int(m_skip.group(1)) if m_skip else 0
            count = int(m_count.group(1)) if m_count else len(data)
            sliced = data[skip : skip + count]
            return RemoteResult(stdout=base64.b64encode(sliced), stderr=b"", exit_code=0)
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
        assert out["offset"] == 0
        assert out["end_of_file"] is True

    async def test_read_offset_applies(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        data = b"0123456789"
        ssh.file_sizes[ROOT + "/project/f.txt"] = len(data)
        ssh.file_data[ROOT + "/project/f.txt"] = data
        out = await fs.read_file(ROOT + "/project/f.txt", offset=3, max_bytes=4)
        assert out["content"] == "3456"
        assert out["offset"] == 3
        assert out["bytes"] == 4
        assert out["end_of_file"] is False
        assert out["next_offset"] == 7

    async def test_read_offset_to_end(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        data = b"0123456789"
        ssh.file_sizes[ROOT + "/project/f.txt"] = len(data)
        ssh.file_data[ROOT + "/project/f.txt"] = data
        out = await fs.read_file(ROOT + "/project/f.txt", offset=8)
        assert out["content"] == "89"
        assert out["end_of_file"] is True
        assert out["next_offset"] is None

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

    async def test_read_large_file_first_chunk_ok(self):
        """A file larger than the cap is not refused: the first chunk is
        returned and the agent is told how to continue (end_of_file=false)."""
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        big = ROOT + "/big.log"
        data = b"x" * 100 * 1024
        ssh.file_sizes[big] = len(data)
        ssh.file_data[big] = data
        out = await fs.read_file(big, max_bytes=1000)
        assert out["bytes"] == 1000
        assert out["end_of_file"] is False
        assert out["next_offset"] == 1000
        assert out["size"] == len(data)

    async def test_read_large_file_pages_to_end(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        big = ROOT + "/big.log"
        data = b"abcdefghij" * 300  # 3000 bytes
        ssh.file_sizes[big] = len(data)
        ssh.file_data[big] = data
        # 分块读完全部（底层能力仍在，只是不再被工具描述引导）
        offset = 0
        chunks = []
        while True:
            out = await fs.read_file(big, offset=offset, max_bytes=1000)
            chunks.append(out["content"])
            if out["end_of_file"]:
                break
            offset = out["next_offset"]
        assert "".join(chunks) == data.decode()
        assert len(chunks) == 3

    async def test_read_respects_slice_budget(self):
        """hpc.files.read is a bounded slice: a huge max_bytes is clamped to
        the configured slice cap so a large file is not read in one call."""
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        big = ROOT + "/big.log"
        data = b"x" * (1024 * 1024)
        ssh.file_sizes[big] = len(data)
        ssh.file_data[big] = data
        # max_bytes far above the 256 KiB slice budget
        out = await fs.read_file(big, max_bytes=1024 * 1024)
        assert out["bytes"] <= 256 * 1024
        assert out["end_of_file"] is False
        assert out["next_offset"] is not None

    async def test_read_slice_budget_configurable(self):
        cfg = make_cfg()
        cfg.files.max_read_slice_bytes = 128
        ssh = FakeSsh()
        fs = FileService(cfg, ssh)
        big = ROOT + "/big.log"
        data = b"y" * 1000
        ssh.file_sizes[big] = len(data)
        ssh.file_data[big] = data
        out = await fs.read_file(big, max_bytes=100000)
        assert out["bytes"] == 128


@pytest.mark.asyncio
class TestListPaging:
    """hpc.files.list must bound output on the remote side and support
    cursor-based continuation for recursive listings."""

    ROOT_DIR = ROOT + "/project"

    class FakeListSsh:
        def __init__(self, layers: dict[int, list[str]]):
            """layers: depth -> list of 'type size path' lines."""
            self.layers = layers
            self.find_calls: list[list[str]] = []

        async def realpath(self, path):
            return path

        async def run(self, argv, *, timeout=None, max_output=4 * 1024 * 1024, check=True):
            self.find_calls.append(list(argv))
            if argv[0] == "bash" and "find" in argv[-1]:
                import re as _re

                m = _re.search(r"-mindepth (\d+) -maxdepth (\d+)", argv[-1])
                depth = int(m.group(1))
                head_m = _re.search(r"head -n (\d+)", argv[-1])
                limit = int(head_m.group(1)) if head_m else 10**9
                lines = self.layers.get(depth, [])
                cut = len(lines) > limit
                out_lines = lines[:limit]
                code = 141 if cut else 0  # pipefail: find killed by SIGPIPE
                return RemoteResult(
                    stdout=("\n".join(out_lines) + "\n").encode(),
                    stderr=b"",
                    exit_code=code,
                )
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

        async def run_raw(self, cmd, **kw):
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    def _lines(self, depth: int, base: str) -> list[str]:
        prefix = "/".join([self.ROOT_DIR] + ["d" + str(depth)] * (depth - 1))
        return [f"f {100 + i} {prefix}/file{i}.jl" for i in range(3)]

    async def test_non_recursive_returns_dict(self):
        ssh = self.FakeListSsh({1: self._lines(1, self.ROOT_DIR)})
        fs = FileService(make_cfg(), ssh)
        out = await fs.list_dir(self.ROOT_DIR)
        assert isinstance(out, dict)
        assert out["next_cursor"] is None
        assert len(out["entries"]) == 3

    async def test_non_recursive_page_truncated_remotely(self):
        """A huge single layer must not stream back more than page_size."""
        big_layer = [f"f {100 + i} {self.ROOT_DIR}/file{i}.jl" for i in range(5000)]
        ssh = self.FakeListSsh({1: big_layer})
        fs = FileService(make_cfg(), ssh)
        out = await fs.list_dir(self.ROOT_DIR, page_size=50)
        assert len(out["entries"]) == 50
        assert out["truncated"] is True
        # the remote command cut output with head -n 50
        assert any("head -n 50" in c[-1] for c in ssh.find_calls)

    async def test_recursive_pages_by_depth_with_cursor(self):
        ssh = self.FakeListSsh(
            {
                1: self._lines(1, self.ROOT_DIR),
                2: self._lines(2, self.ROOT_DIR),
                3: self._lines(3, self.ROOT_DIR),
            }
        )
        fs = FileService(make_cfg(), ssh)
        page1 = await fs.list_dir(self.ROOT_DIR, recursive=True, page_size=3)
        assert len(page1["entries"]) == 3
        assert page1["truncated"] is True
        assert page1["next_cursor"] == "depth:2"

        page2 = await fs.list_dir(self.ROOT_DIR, recursive=True, page_size=3, cursor=page1["next_cursor"])
        assert page2["next_cursor"] == "depth:3"
        # depth 1 was NOT re-scanned
        assert all("mindepth 1" not in c[-1] for c in ssh.find_calls[1:])

        page3 = await fs.list_dir(self.ROOT_DIR, recursive=True, page_size=3, cursor=page2["next_cursor"])
        assert page3["next_cursor"] is None
        assert page3["truncated"] is False
        assert len(page3["entries"]) == 3

    async def test_recursive_respects_max_depth(self):
        ssh = self.FakeListSsh({1: self._lines(1, self.ROOT_DIR), 2: self._lines(2, self.ROOT_DIR)})
        fs = FileService(make_cfg(), ssh)
        out = await fs.list_dir(self.ROOT_DIR, recursive=True, page_size=100, max_depth=1)
        assert out["depth_reached"] == 1
        assert all("maxdepth 1" in c[-1] for c in ssh.find_calls)
        assert len(out["entries"]) == 3

    async def test_invalid_cursor_denied(self):
        fs = FileService(make_cfg(), self.FakeListSsh({1: []}))
        with pytest.raises(PathSandboxError):
            await fs.list_dir(self.ROOT_DIR, recursive=True, cursor="../../etc")
        with pytest.raises(PathSandboxError):
            await fs.list_dir(self.ROOT_DIR, recursive=True, cursor="depth:999")


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

    async def test_write_reports_created(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        out = await fs.write_file(ROOT + "/project/new.txt", "hello")
        assert out["change"] == "created"
        assert out["existed_before"] is False
        assert out["bytes_written"] == 5
        assert out["new_size"] == 5

    async def test_write_reports_overwrite(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        path = ROOT + "/project/existing.txt"
        ssh.file_sizes[path] = 10
        out = await fs.write_file(path, "ab")
        assert out["change"] == "overwritten"
        assert out["existed_before"] is True
        assert out["previous_size"] == 10
        assert out["new_size"] == 2


@pytest.mark.asyncio
class TestWriteConcurrency:
    """Optimistic concurrency: refuse overwriting a file that changed since
    the agent last read it (expected_size/expected_mtime/expected_sha256)."""

    PATH = ROOT + "/project/data.txt"

    async def test_expected_size_mismatch_denied(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        ssh.file_sizes[self.PATH] = 100
        with pytest.raises(PathSandboxError, match="changed since"):
            await fs.write_file(self.PATH, "new", expected_size=50)

    async def test_expected_size_match_allowed(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        ssh.file_sizes[self.PATH] = 100
        out = await fs.write_file(self.PATH, "new", expected_size=100)
        assert out["change"] == "overwritten"

    async def test_expected_mtime_mismatch_denied(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        ssh.file_sizes[self.PATH] = 100
        ssh.file_mtimes[self.PATH] = 1700000000
        with pytest.raises(PathSandboxError, match="changed since"):
            await fs.write_file(self.PATH, "new", expected_mtime=1111111111)

    async def test_expected_sha256_mismatch_denied(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        ssh.file_sizes[self.PATH] = 100
        ssh.file_data[self.PATH] = b"original content"
        with pytest.raises(PathSandboxError, match="hash does not match"):
            await fs.write_file(self.PATH, "new", expected_sha256="0" * 64)

    async def test_expected_sha256_match_allowed(self):
        import hashlib as _hashlib

        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        data = b"original content"
        ssh.file_sizes[self.PATH] = len(data)
        ssh.file_data[self.PATH] = data
        digest = _hashlib.sha256(data).hexdigest()
        out = await fs.write_file(self.PATH, "new", expected_sha256=digest)
        assert out["change"] == "overwritten"

    async def test_expectation_on_missing_file_denied(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        with pytest.raises(PathSandboxError, match="no longer exists"):
            await fs.write_file(self.PATH, "new", expected_size=10)

    async def test_bad_sha256_format_denied(self):
        ssh = FakeSsh()
        fs = FileService(make_cfg(), ssh)
        ssh.file_sizes[self.PATH] = 10
        with pytest.raises(PathSandboxError):
            await fs.write_file(self.PATH, "new", expected_sha256="not-a-hash")


@pytest.mark.asyncio
class TestDelete:
    async def test_delete_root_denied(self):
        fs = FileService(make_cfg(), FakeSsh())
        with pytest.raises(PathSandboxError, match="user root"):
            await fs.delete(ROOT)


@pytest.mark.asyncio
class TestShellOperandSandbox:
    """shell.run_safe must sandbox absolute path operands, not only cwd."""

    async def _run(self, command: str):
        from hpc_mcp.shell.safe_exec import SafeExec

        class FakeSsh2(FakeSsh):
            async def run_raw(self, cmd, **kw):
                return RemoteResult(stdout=b"ok\n", stderr=b"", exit_code=0)

        se = SafeExec(make_cfg(), FakeSsh2())
        return await se.run(command, cwd=ROOT)

    async def test_cat_etc_denied(self):
        from hpc_mcp.errors import CommandPolicyError

        with pytest.raises(CommandPolicyError, match="escapes"):
            await self._run("cat /etc/passwd")

    async def test_find_root_denied(self):
        from hpc_mcp.errors import CommandPolicyError

        with pytest.raises(CommandPolicyError):
            await self._run("find / -maxdepth 1")

    async def test_grep_inside_ok(self):
        out = await self._run(f"grep -rn TODO {ROOT}/project")
        assert out["exit_code"] == 0
