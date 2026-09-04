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
import re
import sys
import time
from typing import Any

_LOGGER_NAME = "hpc_mcp"
_SENSITIVE_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+"),
]

_configured = False


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure the package logger exactly once."""
    global _configured
    if _configured:
        return
    _configured = True

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream = logging.StreamHandler(stream=sys.stderr)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if log_file:
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError as exc:  # fail soft: log file is optional
            logger.warning("Could not open log file %s: %s", log_file, exc)


def get_logger() -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(_LOGGER_NAME)


def sanitize(value: Any, *, max_len: int = 300) -> str:
    """Render a value for logging, redacting secrets and truncating."""
    text = repr(value)
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
