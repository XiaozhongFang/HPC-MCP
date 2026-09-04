"""Path sandbox policy.

Every remote path the agent touches must resolve, after full
canonicalization, to a location inside the configured ``user_root``.

Two layers are provided:

1. :func:`validate_path` -- **pure lexical validation**.  It normalizes the
   path, resolves ``.``/``..`` segments (with :func:`posixpath.normpath`,
   which is precise for POSIX paths, unlike ``str.startswith``), rejects
   tilde expansion, NUL bytes and relative paths, and verifies the result
   stays inside ``user_root``.  This catches every traversal that does not
   involve symlinks, without needing network access.

2. :func:`verify_remote_realpath` -- **canonical verification on the
   server**.  The SSH layer resolves the *parent* directory of the target
   with ``realpath`` on the remote host, then re-checks containment.  This
   defeats symlink escapes (e.g. ``allowed/link -> /etc``), including for
   paths that do not exist yet (write/create targets), where the nearest
   existing ancestor is resolved instead.

If either check cannot produce a definitive answer, the verdict is DENY
(fail-closed).
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from ..errors import PathSandboxError

# Characters that must never appear in a path we place inside a remote
# shell command line (defense in depth -- paths are always shlex-quoted
# in addition, but rejecting outright removes whole classes of mistakes).
_FORBIDDEN_CHARS = re.compile(r"[\x00\r\n]")

_LEXICAL_SCOPE = "paths inside the configured user root"
_SENSITIVE_LOCAL_NAMES = {
    ".ssh", ".env", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
}


def normalize_lexical(path: str, user_root: str) -> str:
    """Lexically normalize ``path`` and require it to stay in ``user_root``.

    Returns the normalized absolute path.  Raises :class:`PathSandboxError`
    on any violation.  This function performs no I/O.
    """
    if not isinstance(user_root, str) or not user_root.startswith("/") or posixpath.normpath(user_root) == "/":
        raise PathSandboxError("Configured user root must be a dedicated absolute directory", scope=user_root)
    if not isinstance(path, str) or not path:
        raise PathSandboxError("Path must be a non-empty string")
    if _FORBIDDEN_CHARS.search(path):
        raise PathSandboxError(
            "Path contains forbidden characters (NUL/CR/LF)",
            requested=path,
            scope=_LEXICAL_SCOPE,
        )
    # Reject tilde expansion: we never allow the shell to expand '~' to the
    # shared account home (which is outside the per-user root).
    if path.startswith("~"):
        raise PathSandboxError(
            "Tilde (~) paths are not allowed; use absolute paths inside your configured root",
            requested=path,
            scope=user_root,
        )
    if not path.startswith("/"):
        raise PathSandboxError(
            "Path must be absolute and located inside the configured user root",
            requested=path,
            scope=user_root,
        )

    # normpath collapses '.', '..' and duplicate separators lexically.
    normalized = posixpath.normpath(path)

    if not is_within(normalized, user_root):
        raise PathSandboxError(
            "Path escapes the configured user root",
            requested=path,
            scope=user_root,
        )
    return normalized


def is_within(path: str, user_root: str) -> bool:
    """True iff normalized absolute ``path`` equals or sits under ``user_root``."""
    if not isinstance(path, str) or not isinstance(user_root, str):
        return False
    if not path.startswith("/") or not user_root.startswith("/"):
        return False
    root = posixpath.normpath(user_root)
    normalized = posixpath.normpath(path)
    if root == "/":
        return False
    if normalized == root:
        return True
    prefix = root if root.endswith("/") else root + "/"
    return normalized.startswith(prefix)


def validate_path(path: str, user_root: str) -> str:
    """Full lexical validation entry point.  Returns the normalized path."""
    return normalize_lexical(path, user_root)


def validate_local_path(path: str, local_root: str, *, for_write: bool = False) -> str:
    """Resolve a local transfer path under ``local_root`` without symlinks.

    Uploads must name a regular file and downloads must target an existing
    directory tree.  Refusing symlink components prevents an agent from using
    a transfer as a local secret read or arbitrary overwrite primitive.
    """
    if not isinstance(path, str) or not path or _FORBIDDEN_CHARS.search(path):
        raise PathSandboxError("Local path must be a non-empty path without control characters", requested=str(path), scope=local_root)
    root = Path(local_root).expanduser().resolve(strict=True)
    candidate = Path(path).expanduser()
    candidate_abs = candidate if candidate.is_absolute() else root / candidate
    # Inspect the user-supplied path before resolving it; otherwise a symlink
    # to another in-root file would disappear from the check.
    current = root
    try:
        relative_candidate = candidate_abs.relative_to(root)
    except ValueError as exc:
        raise PathSandboxError("Local path escapes the configured local root", requested=path, scope=str(root)) from exc
    for part in relative_candidate.parts:
        current = current / part
        if current.is_symlink():
            raise PathSandboxError("Symlink components are not allowed for local transfers", requested=path, scope=str(root))
    resolved = candidate_abs.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathSandboxError("Local path escapes the configured local root", requested=path, scope=str(root)) from exc

    parts_to_check = list(resolved.relative_to(root).parts)
    parent_parts = parts_to_check if for_write else parts_to_check[:-1]
    current = root
    for part in parent_parts:
        current = current / part
        if current.is_symlink():
            raise PathSandboxError("Symlink components are not allowed for local transfers", requested=path, scope=str(root))
    name = resolved.name.lower()
    if any(part.lower() in _SENSITIVE_LOCAL_NAMES for part in parts_to_check) or name.endswith((".pem", ".key", ".p12")):
        raise PathSandboxError("Local credential files are not allowed in transfers", requested=path, scope=str(root))
    if for_write and resolved.exists() and resolved.is_symlink():
        raise PathSandboxError("Download destination may not be a symlink", requested=path, scope=str(root))
    return str(resolved)


def check_canonical_parent(parent_real: str, basename: str, user_root: str) -> str:
    """Verify a canonical parent directory and child name stay in the sandbox.

    ``parent_real`` is the output of remote ``realpath`` on the parent
    directory; ``basename`` is the final path component.  The combined path
    must sit inside ``user_root``.  Raises on violation.
    """
    parent_real = posixpath.normpath(parent_real)
    if not is_within(parent_real, user_root):
        raise PathSandboxError(
            "Resolved (symlink-canonical) parent directory escapes the configured user root",
            requested=posixpath.join(parent_real, basename),
            scope=user_root,
        )
    if not basename or basename in (".", "..") or "/" in basename or _FORBIDDEN_CHARS.search(basename):
        raise PathSandboxError(
            "Invalid final path component",
            requested=basename,
            scope=_LEXICAL_SCOPE,
        )
    candidate = posixpath.join(parent_real, basename)
    if not is_within(candidate, user_root):
        raise PathSandboxError(
            "Resolved path escapes the configured user root",
            requested=candidate,
            scope=user_root,
        )
    return candidate
