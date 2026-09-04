"""Byte/count limits for file operations and shell output (fail-closed)."""

from __future__ import annotations

from ..errors import PolicyDenied


def check_read_size(size: int | None, max_bytes: int) -> None:
    """Deny reading a file whose size exceeds the configured cap."""
    if size is not None and size > max_bytes:
        raise PolicyDenied(
            f"File is {size} bytes, exceeding the read limit of {max_bytes} bytes. "
            "Read it in smaller ranges or copy a subset.",
            scope=f"max read: {max_bytes} bytes",
        )


def check_write_size(nbytes: int, max_bytes: int) -> None:
    if nbytes > max_bytes:
        raise PolicyDenied(
            f"Write of {nbytes} bytes exceeds the write limit of {max_bytes} bytes",
            scope=f"max write: {max_bytes} bytes",
        )


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
