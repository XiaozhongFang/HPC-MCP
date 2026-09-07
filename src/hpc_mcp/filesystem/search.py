"""Bounded, sandboxed search over remote files (``hpc.files.search``).

The search is a first-class policy-controlled tool -- the agent must never
need to improvise ``grep``/``find`` pipelines through ``hpc.shell.run_safe``
to diagnose a job.  Every budget (max_matches, max_context_lines,
max_scan_bytes, timeout) is clamped server-side; the remote command is built
entirely from quoted, fixed parameters; output is cut on the remote side
with ``head``; and a non-zero grep exit code (no match) is not an error.

For single files the byte budget is exact (stat + refuse larger than
max_scan_bytes).  For recursive directory searches the byte budget cannot be
enforced precisely by ``grep -r`` -- there the timeout, the per-file ``-m``
cap and the global ``head`` cut provide the hard bounds, and the response
reports ``scanned_files: null`` so callers are not misled.
"""

from __future__ import annotations

import posixpath
import re

from ..config import Config
from ..errors import PathSandboxError
from ..security.path_policy import validate_path
from ..ssh.manager import SshManager
from .service import FileService, _q

#: Directories skipped when searching a directory tree (shared-account
#: metadata and version-control internals are not diagnostic targets).
_DEFAULT_EXCLUDE_DIRS = (".git", ".hpc-mcp", ".svn", ".hg", "node_modules", "__pycache__")

_LINE_RE = re.compile(r"^(\d+):(.*)$", re.DOTALL)


class FileSearchService:
    def __init__(self, cfg: Config, ssh: SshManager) -> None:
        self._cfg = cfg
        self._ssh = ssh
        self._fs = FileService(cfg, ssh)

    @staticmethod
    def _cap(value: int | None, maximum: int, name: str, *, minimum: int = 1) -> int:
        if value is None:
            return maximum
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise PathSandboxError(
                f"{name} must be an integer >= {minimum}", requested=str(value)
            )
        return min(value, maximum)

    async def search(
        self,
        path: str,
        pattern: str,
        *,
        max_matches: int | None = None,
        context_lines: int | None = None,
        max_scan_bytes: int | None = None,
        max_files: int | None = None,
        max_depth: int | None = None,
        timeout: int | None = None,
    ) -> dict:
        fcfg = self._cfg.files
        matches_cap = self._cap(max_matches, fcfg.search_max_matches, "max_matches", minimum=1)
        ctx_cap = self._cap(context_lines, fcfg.search_max_context_lines, "context_lines", minimum=0)
        scan_cap = self._cap(max_scan_bytes, fcfg.search_max_scan_bytes, "max_scan_bytes", minimum=1)
        files_cap = self._cap(max_files, fcfg.search_max_files, "max_files", minimum=1)
        depth_cap = min(
            max_depth if isinstance(max_depth, int) and max_depth >= 1 else fcfg.search_max_depth,
            fcfg.search_max_depth,
        )
        tmo = self._cap(timeout, fcfg.search_timeout, "timeout", minimum=1)

        if not isinstance(pattern, str) or not pattern or len(pattern) > 4096:
            raise PathSandboxError("pattern must be a non-empty string of at most 4096 chars", requested=str(pattern))
        if any(ch in pattern for ch in "\x00\r\n"):
            raise PathSandboxError("pattern may not contain control characters", requested="(redacted)")

        # Canonicalize + contain the search target first.
        real = await self._fs.resolve_existing(path)
        st = await self._fs.stat(real)
        is_dir = st["type"] == "directory"

        if is_dir:
            return await self._search_tree(
                real, pattern, matches_cap=matches_cap, ctx_cap=ctx_cap,
                files_cap=files_cap, depth_cap=depth_cap, tmo=tmo,
            )
        if st["type"] != "regular file":
            raise PathSandboxError("Search target must be a regular file or directory", requested=path)
        if st["size"] > scan_cap:
            raise PathSandboxError(
                f"File is {st['size']} bytes, exceeding the search scan budget of {scan_cap} bytes. "
                "Narrow the search target.",
                requested=path,
            )
        return await self._search_file(real, pattern, matches_cap=matches_cap, ctx_cap=ctx_cap, tmo=tmo)

    async def _search_file(
        self, real: str, pattern: str, *, matches_cap: int, ctx_cap: int, tmo: int
    ) -> dict:
        max_lines = matches_cap * (1 + 2 * ctx_cap)
        flags = "-n"
        if ctx_cap:
            flags += f" -C {ctx_cap}"
        argv = [
            "sh", "-c",
            f"timeout {int(tmo)} grep {flags} -E -m {int(matches_cap)} -- {_q(pattern)} {_q(real)} "
            f"| head -n {int(max_lines)}",
        ]
        res = await self._ssh.run(
            argv, check=False, timeout=tmo + 15, max_output=max_lines * 4096 + 8192
        )
        matches, truncated = self._parse_lines(res.stdout_text, matches_cap)
        return {
            "path": real,
            "pattern": pattern,
            "matches": matches,
            "match_count": len([m for m in matches if m["kind"] == "match"]),
            "truncated": truncated,
            "scanned_files": 1,
            "scanned_bytes": None,
        }

    async def _search_tree(
        self, real: str, pattern: str, *, matches_cap: int, ctx_cap: int,
        files_cap: int, depth_cap: int, tmo: int,
    ) -> dict:
        excludes = " ".join(f"! -path {_q('*/' + d + '/*')} ! -name {_q(d)}" for d in _DEFAULT_EXCLUDE_DIRS)
        max_lines = matches_cap * (1 + 2 * ctx_cap)
        flags = "-rn"
        if ctx_cap:
            flags += f" -C {ctx_cap}"
        argv = [
            "sh", "-c",
            f"timeout {int(tmo)} grep {flags} -m {int(matches_cap)} -- {_q(pattern)} {_q(real)} "
            f"{excludes} | head -n {int(max_lines)}",
        ]
        res = await self._ssh.run(
            argv, check=False, timeout=tmo + 15, max_output=max_lines * 4096 + 8192
        )
        # grep -rn prints 'path:LINE:text'; convert to the same line format.
        text = self._strip_path_prefix(res.stdout_text, real)
        matches, truncated = self._parse_lines(text, matches_cap)
        return {
            "path": real,
            "pattern": pattern,
            "matches": matches,
            "match_count": len([m for m in matches if m["kind"] == "match"]),
            "truncated": truncated,
            "scanned_files": None,
            "scanned_bytes": None,
        }

    @staticmethod
    def _strip_path_prefix(text: str, real: str) -> str:
        """Turn ``grep -rn`` output ('dir/...:LINE:text') back into
        'LINE:text' so both paths share one parser."""
        prefix = real.rstrip("/") + "/"
        out: list[str] = []
        for line in text.splitlines():
            if line.startswith(prefix):
                line = line[len(prefix):]
            out.append(line)
        return "\n".join(out)

    @staticmethod
    def _parse_lines(text: str, matches_cap: int) -> tuple[list[dict], bool]:
        """Parse ``grep -n [-C]`` output into match/context records.

        Match lines are ``LINE:text`` (line number); context lines printed by
        ``-C`` have no line number (GNU grep prints them bare or with ``-``).
        At most ``matches_cap`` *match* records are kept; anything beyond is
        truncated.
        """
        matches: list[dict] = []
        truncated = False
        for raw in text.splitlines():
            m = _LINE_RE.match(raw)
            if m:
                if sum(1 for x in matches if x["kind"] == "match") >= matches_cap:
                    truncated = True
                    break
                matches.append(
                    {"line": int(m.group(1)), "text": m.group(2), "kind": "match"}
                )
            else:
                body = raw[1:] if raw.startswith(("-", ":")) else raw
                if body.strip() == "" and raw == "":
                    continue
                matches.append({"line": None, "text": body, "kind": "context"})
        return matches, truncated
