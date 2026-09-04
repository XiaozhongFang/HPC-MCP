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

from ..config import Config
from ..errors import PathSandboxError
from ..security import limits
from ..security.path_policy import check_canonical_parent, validate_path
from ..ssh.manager import SshManager

_CHUNK = 512 * 1024  # base64 chunk size for read/write round trips


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

    async def list_dir(self, path: str, *, recursive: bool = False, max_entries: int | None = None) -> list[dict]:
        real = await self.resolve_existing(path)
        cap = self._bounded_cap(max_entries, self._cfg.files.max_list_entries, "max_entries")
        if recursive:
            argv = ["find", real, "-mindepth", "1", "-maxdepth", "8", "-printf", "%y %s %p\n"]
        else:
            argv = ["find", real, "-mindepth", "1", "-maxdepth", "1", "-printf", "%y %s %p\n"]
        res = await self._ssh.run(argv, check=True)
        entries: list[dict] = []
        for line in res.stdout_text.splitlines():
            if len(entries) >= cap:
                break
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
        entries.sort(key=lambda e: (e["type"] != "directory", e["name"]))
        return entries

    async def read_file(self, path: str, *, max_bytes: int | None = None, offset: int = 0) -> dict:
        real = await self.resolve_existing(path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PathSandboxError("offset must be a non-negative integer", requested=str(offset))
        cap = self._bounded_cap(max_bytes, self._cfg.files.max_read_bytes, "max_bytes")

        # size check first
        st = await self._ssh.run(["stat", "-c", "%s", "--", real], check=True)
        size = int(st.stdout_text.strip() or "0")
        if offset == 0:
            limits.check_read_size(size, cap)

        fetch = cap + 1
        # dd with base64 to transfer raw bytes safely over the exec channel;
        # refuse symlinks in the same invocation to close the stat-then-read
        # TOCTOU window on shared accounts.
        argv = [
            "sh", "-c",
            f"if test -L {_q(real)}; then exit 1; fi; "
            f"dd if={_q(real)} bs=1 skip={offset} count={fetch} status=none | base64 -w0",
        ]
        res = await self._ssh.run(argv, check=True, max_output=int(cap * 1.4) + 4096)
        data = base64.b64decode(res.stdout.strip() or b"")
        truncated = len(data) > cap
        if truncated:
            data = data[:cap]
        return {
            "path": real,
            "size": size,
            "offset": offset,
            "bytes": len(data),
            "truncated": truncated or (offset + len(data)) < size,
            "content": data.decode("utf-8", errors="replace"),
        }

    async def write_file(self, path: str, content: str | bytes, *, append: bool = False) -> dict:
        real = await self.resolve_for_create(path)
        data = content.encode() if isinstance(content, str) else content
        limits.check_write_size(len(data), self._cfg.files.max_write_bytes)

        # ensure parent exists? Parent must already exist (fail-closed).
        op = ">>" if append else ">"
        # write in base64 chunks through stdin-free exec: embed in argv is
        # unsafe for large payloads, so decode remotely from base64 heredoc.
        # We send base64 on the command line in chunks (bounded sizes).
        total = 0
        first = True
        for i in range(0, len(data), _CHUNK):
            chunk = data[i : i + _CHUNK]
            b64 = base64.b64encode(chunk).decode()
            redirect = op if first else ">>"
            argv = [
                "sh", "-c",
                f"printf %s {_q(b64)} | base64 -d {redirect} {_q(real)}",
            ]
            await self._ssh.run(argv, check=True, max_output=1024)
            total += len(chunk)
            first = False
        if not data:  # empty file
            await self._ssh.run(["sh", "-c", f": {op} {_q(real)}"], check=True)
        return {"path": real, "bytes_written": total}

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
