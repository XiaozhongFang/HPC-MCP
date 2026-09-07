"""Sandboxed remote filesystem operations.

Every public method:

1. lexically validates the path against ``user_root``;
2. resolves the *canonical* remote location (``realpath`` of the existing
   target, or of the nearest existing parent for new files) and re-checks
   containment -- defeating symlink escapes;
3. only then touches the remote file.

If any step is inconclusive the operation is denied (fail-closed).
"""

from __future__ import annotations

import base64
import posixpath
import re

from ..config import Config
from ..errors import PathSandboxError
from ..security import limits
from ..security.path_policy import check_canonical_parent, validate_path
from ..ssh.manager import SshManager

_CHUNK = 16 * 1024  # base64 chunk size for write round trips.  Kept small so
# the remote command line stays far below OS argv limits (Windows OpenSSH
# chokes around 32 KiB; even Linux has ARG_MAX constraints).


class FileService:
    def __init__(self, cfg: Config, ssh: SshManager) -> None:
        self._cfg = cfg
        self._ssh = ssh
        self._root = cfg.root

    # -- canonicalization ----------------------------------------------------

    async def resolve_existing(self, path: str) -> str:
        """Validate + canonicalize an existing remote path."""
        lexical = validate_path(path, self._root)
        real = await self._ssh.realpath(lexical)
        if real is None:
            raise PathSandboxError(
                "Could not resolve the remote path (fail-closed)",
                requested=path,
                scope=self._root,
            )
        return validate_path(real, self._root)

    async def resolve_for_create(self, path: str) -> str:
        """Validate + canonicalize the parent of a to-be-created path."""
        lexical = validate_path(path, self._root)
        parent, _, basename = lexical.rpartition("/")
        parent = parent or "/"
        real_parent = await self._ssh.realpath(parent)
        if real_parent is None:
            raise PathSandboxError(
                "Could not resolve the remote parent directory (fail-closed)",
                requested=path,
                scope=self._root,
            )
        candidate = check_canonical_parent(real_parent, basename, self._root)
        # realpath -m returns the lexical path for a new target.  A different
        # result therefore indicates that the final component already exists
        # as a symlink; refusing it prevents redirection/SFTP from following a
        # target outside the sandbox.
        target_real = await self._ssh.realpath(lexical)
        if target_real is not None and target_real != lexical:
            try:
                validate_path(target_real, self._root)
            except PathSandboxError:
                raise
            raise PathSandboxError(
                "The destination is a symlink; writes and uploads require a regular path",
                requested=path,
                scope=self._root,
            )
        return candidate

    # Compatibility aliases for internal callers from older integrations.
    _resolve_existing = resolve_existing
    _resolve_for_create = resolve_for_create

    # -- operations ------------------------------------------------------------

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_entries: int | None = None,
        page_size: int | None = None,
        cursor: str | None = None,
        max_depth: int | None = None,
    ) -> dict:
        """List directory contents with a hard, server-side budget.

        Returned dict: ``{path, entries, next_cursor, truncated, returned,
        depth_reached}``.

        * Non-recursive (default): a single-layer ``find -maxdepth 1`` whose
          output is cut at ``page_size`` entries *on the remote side*
          (``| head``), so a huge directory never streams a full listing back
          to the MCP.  Because ``find`` must still opendir the whole layer to
          know its members, per-layer truncation cannot be resumed -- use
          :meth:`search` for large trees.
        * Recursive: enumerates one depth level per remote call (each layer
          truncated remotely), accumulating up to ``page_size`` entries.
          ``next_cursor`` is ``"depth:N"`` and resumes from that depth, so a
          huge tree is never fully scanned in one call and earlier layers are
          not re-scanned.
        """
        real = await self.resolve_existing(path)
        cap = self._bounded_cap(max_entries, self._cfg.files.max_list_entries, "max_entries")
        page = self._bounded_cap(page_size, cap, "page_size") if page_size is not None else cap
        if page <= 0:
            raise PathSandboxError("page_size must be a positive integer", requested=str(page))

        start_depth = 1
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor.startswith("depth:"):
                raise PathSandboxError("Invalid cursor (expected 'depth:N')", requested=str(cursor))
            try:
                start_depth = int(cursor[len("depth:"):])
            except ValueError:
                raise PathSandboxError("Invalid cursor depth", requested=str(cursor))
            if start_depth < 1 or start_depth > 64:
                raise PathSandboxError("Cursor depth out of range", requested=str(cursor))

        depth_limit = min(
            max_depth if isinstance(max_depth, int) and max_depth >= 1 else self._cfg.files.max_recursive_depth,
            self._cfg.files.max_recursive_depth,
        )

        entries: list[dict] = []
        cur = start_depth
        truncated = False
        last_processed = 0
        if recursive:
            while cur <= depth_limit:
                remaining = page - len(entries)
                if remaining <= 0:
                    truncated = True
                    break
                layer, layer_cut = await self._enumerate_layer(real, cur, remaining)
                entries.extend(layer)
                last_processed = cur
                if layer_cut:
                    # the layer exceeds the remaining budget and head cut its
                    # output; the rest of this layer is not resumable, so the
                    # listing stops here (see docstring).
                    truncated = True
                    break
                cur += 1
            if len(entries) >= page and last_processed < depth_limit:
                next_cursor = f"depth:{last_processed + 1}"
            else:
                next_cursor = None
        else:
            entries, truncated = await self._enumerate_layer(real, 1, page)
            next_cursor = None

        entries.sort(key=lambda e: (e["type"] != "directory", e["name"]))
        return {
            "path": real,
            "entries": entries,
            "next_cursor": next_cursor,
            "truncated": truncated,
            "returned": len(entries),
            "depth_reached": last_processed,
        }

    async def _enumerate_layer(self, real: str, depth: int, limit: int) -> tuple[list[dict], bool]:
        """Enumerate one depth layer, cutting the remote output at ``limit``.

        ``| head -n limit`` terminates ``find`` early (SIGPIPE) once enough
        entries are printed, so a layer with a million members transfers only
        ``limit`` lines instead of the whole tree.  ``bash -o pipefail`` makes
        the pipe's exit code 141 when ``find`` was killed mid-output, which
        tells us the layer has more members than ``limit`` (a layer that
        happens to have exactly ``limit`` members exits 0).  Returns
        ``(entries, layer_cut)``.
        """
        fmt = r"%y %s %p\n"
        argv = [
            "bash", "-o", "pipefail", "-c",
            f"find {_q(real)} -mindepth {depth} -maxdepth {depth} -printf {_q(fmt)} | head -n {int(limit)}",
        ]
        res = await self._ssh.run(
            argv, check=False, max_output=int(limit) * 300 + 4096
        )
        entries: list[dict] = []
        lines = res.stdout_text.splitlines()
        for line in lines:
            parts = line.split(" ", 2)
            if len(parts) < 3:
                continue
            typech, size, p = parts
            entries.append(
                {
                    "name": posixpath.basename(p),
                    "path": p,
                    "type": {"d": "directory", "f": "file", "l": "symlink"}.get(typech, "other"),
                    "size": int(size) if size.isdigit() else None,
                }
            )
        layer_cut = res.exit_code != 0 and bool(lines)
        return entries, layer_cut

    async def read_file(self, path: str, *, max_bytes: int | None = None, offset: int = 0) -> dict:
        real = await self.resolve_existing(path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PathSandboxError("offset must be a non-negative integer", requested=str(offset))
        cap = self._bounded_cap(max_bytes, self._cfg.files.max_read_bytes, "max_bytes")

        # A bounded slice read: fetch at most cap bytes in ONE round trip and
        # return a per-call byte budget.  The caller is *not* guided toward
        # paging a whole file to EOF -- for diagnosis, use hpc.files.search to
        # locate a region first, then read a small slice around it.  Repeated
        # no-destination full-file reads are the single largest avoidable
        # login-node I/O cost, so the effective cap is clamped to the slice
        # budget (configurable via files.max_read_slice_bytes).
        slice_cap = self._cfg.files.max_read_slice_bytes
        effective = min(cap, slice_cap) if slice_cap else cap
        if effective <= 0:
            raise PathSandboxError("Read slice budget must be positive", requested=str(effective))

        # Report the full size so the agent can decide whether to page, but do
        # NOT refuse to read just because the file is larger than the cap.
        st = await self._ssh.run(["stat", "-c", "%s", "--", real], check=True)
        size = int(st.stdout_text.strip() or "0")

        fetch = effective + 1
        # dd with base64 to transfer raw bytes safely over the exec channel;
        # refuse symlinks in the same invocation to close the stat-then-read
        # TOCTOU window on shared accounts.
        argv = [
            "sh", "-c",
            f"if test -L {_q(real)}; then exit 1; fi; "
            f"dd if={_q(real)} bs=1 skip={offset} count={fetch} status=none | base64 -w0",
        ]
        res = await self._ssh.run(argv, check=True, max_output=int(effective * 1.4) + 4096)
        data = base64.b64decode(res.stdout.strip() or b"")
        truncated = len(data) > effective
        if truncated:
            data = data[:effective]
        end = offset + len(data)
        return {
            "path": real,
            "size": size,
            "offset": offset,
            "bytes": len(data),
            "truncated": truncated,
            "end_of_file": end >= size,
            "next_offset": end if end < size else None,
            "content": data.decode("utf-8", errors="replace"),
        }

    async def write_file(
        self,
        path: str,
        content: str | bytes,
        *,
        append: bool = False,
        expected_size: int | None = None,
        expected_mtime: int | None = None,
        expected_sha256: str | None = None,
    ) -> dict:
        real = await self.resolve_for_create(path)
        data = content.encode() if isinstance(content, str) else content
        limits.check_write_size(len(data), self._cfg.files.max_write_bytes)

        # Probe the target before writing so we can report exactly what the
        # write changed (new file vs. overwrite vs. append).
        st = await self._ssh.run(
            ["stat", "-c", "%s|%Y", "--", real], check=False
        )
        existed = st.exit_code == 0
        if existed:
            st_parts = st.stdout_text.strip().split("|")
            previous_size = int(st_parts[0]) if len(st_parts) > 0 and st_parts[0].isdigit() else 0
            previous_mtime = int(st_parts[1]) if len(st_parts) > 1 and st_parts[1].isdigit() else None
        else:
            previous_size = 0
            previous_mtime = None

        # Optimistic concurrency: refuse to overwrite if the file changed
        # since the agent last read it (shared-account safety).  Any supplied
        # expectation that does not match the current state denies the write;
        # fail-closed on unverifiable expectations.
        if expected_size is not None or expected_mtime is not None or expected_sha256 is not None:
            if not existed:
                raise PathSandboxError(
                    "File changed since it was last read: it no longer exists "
                    "(expected_size/expected_mtime/expected_sha256 provided).",
                    requested=path,
                )
            if expected_size is not None:
                if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
                    raise PathSandboxError("expected_size must be a non-negative integer", requested=str(expected_size))
                if previous_size != expected_size:
                    raise PathSandboxError(
                        f"File changed since it was last read: size is now {previous_size} "
                        f"bytes, expected {expected_size} bytes. Refusing to overwrite.",
                        requested=path,
                    )
            if expected_mtime is not None:
                if isinstance(expected_mtime, bool) or not isinstance(expected_mtime, int) or expected_mtime < 0:
                    raise PathSandboxError("expected_mtime must be a non-negative integer (epoch)", requested=str(expected_mtime))
                if previous_mtime != expected_mtime:
                    raise PathSandboxError(
                        f"File changed since it was last read: mtime is now {previous_mtime}, "
                        f"expected {expected_mtime}. Refusing to overwrite.",
                        requested=path,
                    )
            if expected_sha256 is not None:
                if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
                    raise PathSandboxError("expected_sha256 must be a 64-char hex digest", requested="(redacted)")
                digest = await self._remote_sha256(real)
                if digest != expected_sha256.lower():
                    raise PathSandboxError(
                        f"File changed since it was last read: content hash does not match "
                        f"(expected {expected_sha256[:12]}…). Refusing to overwrite.",
                        requested=path,
                    )

        op = ">>" if append else ">"
        # Write the whole payload in ONE round trip via stdin: the base64
        # text is piped to the remote `base64 -d` through the ssh channel
        # (not argv), so it is not limited by OS command-line length and
        # works on Windows OpenSSH too.  The remote command line itself
        # carries only the (quoted) path.
        b64 = base64.b64encode(data).decode()
        if data:
            remote = f"base64 -d {op} {_q(real)}"
        else:  # empty file
            remote = f": {op} {_q(real)}"
        res = await self._ssh.run_raw(
            remote, stdin_text=b64, check=True, max_output=4096
        )
        if res.exit_code != 0:
            raise RemoteCommandError(f"Remote write failed: {res.stderr_text.strip()[:400]}", exit_code=res.exit_code)
        total = len(data)

        if append:
            change = "appended"
            new_size = previous_size + total
        elif existed:
            change = "overwritten"
            new_size = total
        else:
            change = "created"
            new_size = total
        return {
            "path": real,
            "bytes_written": total,
            "change": change,
            "existed_before": existed,
            "previous_size": previous_size,
            "new_size": new_size,
        }

    async def _remote_sha256(self, real: str) -> str:
        """Compute the remote sha256 of a canonicalized file."""
        import hashlib as _hashlib

        res = await self._ssh.run(["sha256sum", "--", real], check=False, max_output=4096)
        if res.exit_code == 0:
            digest = res.stdout_text.split()[0].strip().lower()
            if _hashlib.sha256(b"").hexdigest() and len(digest) == 64:
                return digest
        raise PathSandboxError(
            "Could not verify the remote file hash (fail-closed)",
            requested=real,
        )

    async def mkdir(self, path: str, *, parents: bool = False) -> dict:
        if parents:
            lexical = validate_path(path, self._root)
            # verify nearest existing ancestor is inside root
            real = await self.resolve_for_create(lexical + "/.mkdir-marker")
            target = posixpath.dirname(real)
            await self._ssh.run(["mkdir", "-p", "--", target], check=True)
            return {"path": target, "created": True}
        real = await self.resolve_for_create(path)
        await self._ssh.run(["mkdir", "--", real], check=True)
        return {"path": real, "created": True}

    async def delete(self, path: str, *, recursive: bool = False) -> dict:
        real = await self.resolve_existing(path)
        if real == self._root:
            raise PathSandboxError(
                "Deleting the configured user root itself is not allowed",
                requested=path,
                scope=self._root,
            )
        if recursive:
            await self._ssh.run(["rm", "-rf", "--", real], check=True)
        else:
            await self._ssh.run(["rm", "--", real], check=True)
        return {"path": real, "deleted": True}

    async def rename(self, src: str, dst: str) -> dict:
        real_src = await self.resolve_existing(src)
        real_dst = await self.resolve_for_create(dst)
        await self._ssh.run(["mv", "-n", "--", real_src, real_dst], check=True)
        return {"src": real_src, "dst": real_dst}

    async def stat(self, path: str) -> dict:
        real = await self.resolve_existing(path)
        res = await self._ssh.run(
            ["stat", "-c", "%F|%s|%a|%U|%G|%Y", "--", real], check=True
        )
        ftype, size, mode, user, group, mtime = res.stdout_text.strip().split("|")
        return {
            "path": real,
            "type": ftype,
            "size": int(size),
            "mode": mode,
            "user": user,
            "group": group,
            "mtime_epoch": int(mtime),
        }

    @staticmethod
    def _bounded_cap(value: int | None, maximum: int, name: str) -> int:
        if value is None:
            return maximum
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PathSandboxError(f"{name} must be a non-negative integer", requested=str(value))
        return min(value, maximum)


def _q(s: str) -> str:
    import shlex

    return shlex.quote(s)
