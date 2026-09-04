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

from ..errors import PathSandboxError

# Characters that must never appear in a path we place inside a remote
# shell command line (defense in depth -- paths are always shlex-quoted
# in addition, but rejecting outright removes whole classes of mistakes).
_FORBIDDEN_CHARS = re.compile(r"[\x00\r\n]")

_LEXICAL_SCOPE = "paths inside the configured user root"


def normalize_lexical(path: str, user_root: str) -> str:
    """Lexically normalize ``path`` and require it to stay in ``user_root``.

    Returns the normalized absolute path.  Raises :class:`PathSandboxError`
    on any violation.  This function performs no I/O.
    """
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
    root = posixpath.normpath(user_root)
    if path == root:
        return True
    prefix = root if root.endswith("/") else root + "/"
    return path.startswith(prefix)


def validate_path(path: str, user_root: str) -> str:
    """Full lexical validation entry point.  Returns the normalized path."""
    return normalize_lexical(path, user_root)


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
