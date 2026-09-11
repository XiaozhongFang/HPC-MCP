"""Logging and audit trail for hpc-mcp.

Rules:

* All human-readable logs go to **stderr** (or an optional log file).
  stdout is reserved exclusively for the stdio MCP protocol.
* An audit record is emitted for every tool invocation with the decision
  (ALLOW/DENY), reason, duration, and sanitized arguments.
* Secrets (private keys, passwords, tokens) are never logged.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

_LOGGER_NAME = "hpc_mcp"
_SENSITIVE_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+"),
]
_SENSITIVE_KEYS = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|private[_-]?key|credential)")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_configured = False


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure the package logger.

    Safe to call more than once: the stderr handler is installed on the first
    call only, while ``log_file`` attaches an additional file handler the
    first time that path is requested.  A later call that supplies the path
    configured by the user therefore still takes effect instead of being
    silently dropped.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not _configured:
        _configured = True
        stream = logging.StreamHandler(stream=sys.stderr)
        stream.setFormatter(fmt)
        logger.addHandler(stream)

    if log_file and not _has_file_handler(logger, log_file):
        try:
            path = Path(log_file).expanduser()
            # The default path lives under ~/.local/share; create it so the
            # very first run does not lose its audit trail to a missing dir.
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(path, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError as exc:  # fail soft: log file is optional
            logger.warning("Could not open log file %s: %s", log_file, exc)


def _has_file_handler(logger: logging.Logger, log_file: str) -> bool:
    """Whether a FileHandler for exactly this path is already attached."""
    target = os.path.abspath(os.path.expanduser(log_file))
    return any(
        isinstance(handler, logging.FileHandler) and handler.baseFilename == target
        for handler in logger.handlers
    )


def get_logger() -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(_LOGGER_NAME)


def sanitize(value: Any, *, max_len: int = 300) -> str:
    """Render a value for logging, redacting secrets and truncating."""
    def redact(item: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "[REDACTED:depth]"
        if isinstance(item, dict):
            return {
                str(key): "[REDACTED]" if _SENSITIVE_KEYS.search(str(key)) else redact(val, depth + 1)
                for key, val in item.items()
            }
        if isinstance(item, (list, tuple, set)):
            return [redact(val, depth + 1) for val in item]
        if isinstance(item, str):
            text = _CONTROL_CHARS.sub(lambda m: f"\\x{ord(m.group(0)):02x}", item)
            for pat in _SENSITIVE_PATTERNS:
                text = pat.sub("[REDACTED]", text)
            return text
        return item

    text = repr(redact(value))
    for pat in _SENSITIVE_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    if len(text) > max_len:
        text = text[:max_len] + "...[truncated]"
    return text


class AuditLogger:
    """Structured audit logging for tool invocations."""

    def __init__(self) -> None:
        self._log = get_logger()

    def record(
        self,
        *,
        tool: str,
        decision: str,
        reason: str | None = None,
        args: dict[str, Any] | None = None,
        job_id: str | None = None,
        duration: float | None = None,
        exit_code: int | None = None,
        note: str | None = None,
    ) -> None:
        fields = [f"tool={tool}", f"decision={decision}"]
        if args:
            fields.append("args=" + sanitize(args))
        if reason:
            fields.append("reason=" + sanitize(reason))
        if job_id:
            fields.append(f"job_id={job_id}")
        if duration is not None:
            fields.append(f"duration={duration:.2f}s")
        if exit_code is not None:
            fields.append(f"exit_code={exit_code}")
        if note:
            fields.append("note=" + sanitize(note))
        self._log.info("AUDIT " + " ".join(fields))


class ToolTimer:
    """Context manager measuring tool-call duration."""

    def __init__(self) -> None:
        self.duration = 0.0
        self._start = 0.0

    def __enter__(self) -> "ToolTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        self.duration = time.monotonic() - self._start
