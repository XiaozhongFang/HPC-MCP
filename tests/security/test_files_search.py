"""FileSearchService tests: budgets, sandboxing and parsing."""

import pytest

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.errors import PathSandboxError
from hpc_mcp.filesystem.search import FileSearchService
from hpc_mcp.ssh.manager import RemoteResult

ROOT = "/home/shared_account/alice"


def make_cfg() -> Config:
    return Config(root=ROOT, ssh=SshConfig(host="h", user="u"))


class FakeSearchSsh:
    """Scriptable SSH for search: stat + grep responses keyed by path."""

    def __init__(self) -> None:
        self.file_sizes: dict[str, int] = {}
        self.grep_out: dict[str, str] = {}
        self.grep_code: dict[str, int] = {}
        self.grep_calls: list[str] = []

    async def realpath(self, path):
        return path

    async def run(self, argv, *, timeout=None, max_output=4 * 1024 * 1024, check=True):
        if argv[0] == "stat":
            size = self.file_sizes.get(argv[-1], 10)
            return RemoteResult(
                stdout=f"regular file|{size}|644|u|g|1700000000".encode(),
                stderr=b"", exit_code=0,
            )
        if argv[0] == "sh" and "grep" in argv[-1]:
            cmd = argv[-1]
            self.grep_calls.append(cmd)
            # find the target path in the command
            import re as _re

            for path in list(self.grep_out) + list(self.file_sizes):
                if path in cmd:
                    target = path
                    break
            else:
                target = None
            if target is None:
                return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
            data = self.grep_out.get(target, "")
            head_m = _re.search(r"head -n (\d+)", cmd)
            if head_m:
                limit = int(head_m.group(1))
                data = "\n".join(data.splitlines()[:limit]) + "\n"
            return RemoteResult(
                stdout=data.encode(),
                stderr=b"",
                exit_code=self.grep_code.get(target, 0),
            )
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def run_raw(self, cmd, **kw):
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)


@pytest.mark.asyncio
class TestSearch:
    async def test_search_single_file_ok(self):
        ssh = FakeSearchSsh()
        path = ROOT + "/project/job.out"
        ssh.file_sizes[path] = 1000
        ssh.grep_out[path] = "18291:ERROR: OOM killed\n18292:Traceback\n"
        svc = FileSearchService(make_cfg(), ssh)
        out = await svc.search(path, "ERROR|Traceback")
        assert out["match_count"] == 2
        assert out["matches"][0]["line"] == 18291
        assert out["matches"][0]["text"] == "ERROR: OOM killed"
        assert out["truncated"] is False
        assert out["scanned_files"] == 1

    async def test_search_no_match_is_not_an_error(self):
        ssh = FakeSearchSsh()
        path = ROOT + "/project/job.out"
        ssh.file_sizes[path] = 1000
        ssh.grep_out[path] = ""
        ssh.grep_code[path] = 1  # grep: no match
        svc = FileSearchService(make_cfg(), ssh)
        out = await svc.search(path, "ZZZ_NOT_THERE")
        assert out["match_count"] == 0
        assert out["matches"] == []

    async def test_search_oversized_file_denied(self):
        ssh = FakeSearchSsh()
        path = ROOT + "/project/big.out"
        ssh.file_sizes[path] = 1024 * 1024 * 1024  # 1 GiB > 64 MiB budget
        svc = FileSearchService(make_cfg(), ssh)
        with pytest.raises(PathSandboxError, match="scan budget"):
            await svc.search(path, "ERROR")

    async def test_search_escape_denied(self):
        svc = FileSearchService(make_cfg(), FakeSearchSsh())
        with pytest.raises(PathSandboxError):
            await svc.search("/etc/passwd", "root")

    async def test_search_control_chars_denied(self):
        ssh = FakeSearchSsh()
        path = ROOT + "/project/job.out"
        ssh.file_sizes[path] = 10
        svc = FileSearchService(make_cfg(), ssh)
        with pytest.raises(PathSandboxError):
            await svc.search(path, "a\nb")

    async def test_search_matches_capped(self):
        ssh = FakeSearchSsh()
        path = ROOT + "/project/job.out"
        ssh.file_sizes[path] = 100_000
        lines = "\n".join(f"{i}:line {i}" for i in range(1, 5001))
        ssh.grep_out[path] = lines + "\n"
        svc = FileSearchService(make_cfg(), ssh)
        out = await svc.search(path, "line", max_matches=50)
        assert out["match_count"] <= 50
        # remote command contains the -m cap; output is cut with head
        assert any("grep -n -C 10 -E -m 50" in c and "head -n" in c for c in ssh.grep_calls)

    async def test_search_pattern_quoted_no_injection(self):
        ssh = FakeSearchSsh()
        path = ROOT + "/project/job.out"
        ssh.file_sizes[path] = 1000
        ssh.grep_out[path] = "1:ok\n"
        svc = FileSearchService(make_cfg(), ssh)
        out = await svc.search(path, "foo; rm -rf /")
        assert out["match_count"] == 1
        # the injected fragment must appear only as a shell-quoted grep
        # argument, never as a raw operator in the remote command line
        assert any("'foo; rm -rf /'" in c for c in ssh.grep_calls)

    async def test_search_context_lines_parsed(self):
        ssh = FakeSearchSsh()
        path = ROOT + "/project/job.out"
        ssh.file_sizes[path] = 1000
        ssh.grep_out[path] = "3:ERROR boom\n4-after\n"
        svc = FileSearchService(make_cfg(), ssh)
        out = await svc.search(path, "ERROR", context_lines=1)
        kinds = [m["kind"] for m in out["matches"]]
        assert "match" in kinds and "context" in kinds
        assert out["matches"][0]["text"] == "ERROR boom"
