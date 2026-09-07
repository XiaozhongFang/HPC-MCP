"""Agent-response redaction (minimal, diagnostic-safe).

Audit-log redaction (:mod:`hpc_mcp.logging`) protects the *log*; this module
protects the *response returned to the agent*.  A shared-account job's
stdout/stderr can legitimately contain credential material (``API_KEY=...``,
``Bearer ...``, ``password=...``) that the agent should not be handed in
plaintext.

Design principles:

* **Minimal regexes only.**  We redact obvious secret *assignments*, bearer
  tokens and private-key blocks.  We never run aggressive generic patterns
  over scientific log content -- that would destroy the diagnostics the
  tools exist to provide.
* **Recursive over dict/list payloads**, mirroring how tool results are
  shaped, with a bounded depth and total size so pathological payloads cannot
  stall the server.
* **Cheap.**  Payloads are already size-capped upstream; this pass is O(n).
"""

from __future__ import annotations

import re
from typing import Any

#: `key=value` / `key: value` assignments for well-known secret names.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|auth[_-]?token|session[_-]?token|client[_-]?secret|"
    r"aws[_-]?secret[_-]?access[_-]?key|secret[_-]?access[_-]?key)\b"
    r"\s*[:=]\s*)([^\s,;]+)",
)
#: `Authorization: Bearer <token>` / `Bearer <token>` headers.
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
#: AWS access key ids (AKIA...) -- redact the id itself plus any nearby secret.
_AWS_ACCESS_KEY_ID = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")
#: PEM / OpenSSH private key blocks (multiline).
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
#: `AKIA...` secrets in the form `aws_secret_access_key = <value>` already
#: covered by _SECRET_ASSIGNMENT; generic AWS secret value pattern kept tight.
_AWS_SECRET = re.compile(r"(?i)(aws[_-]?(?:secret[_-]?access[_-]?)?key\s*=\s*)\S+")

_REDACTED = "[REDACTED]"

_PATTERNS = (
    _PRIVATE_KEY_BLOCK,
    _BEARER,
    _AWS_ACCESS_KEY_ID,
    _AWS_SECRET,
    _SECRET_ASSIGNMENT,
)


def _redact_text(text: str) -> str:
    """Redact obvious secrets from a single text string."""
    for pat in _PATTERNS:
        if pat is _SECRET_ASSIGNMENT or pat is _AWS_SECRET:
            text = pat.sub(lambda m: m.group(1) + _REDACTED, text)
        elif pat is _BEARER:
            text = pat.sub(lambda m: m.group(1) + _REDACTED, text)
        else:
            text = pat.sub(_REDACTED, text)
    return text


def redact_response(payload: Any, *, max_depth: int = 8, max_total: int = 1 << 20) -> Any:
    """Return a redacted copy of a tool-result payload.

    Recurses into dicts/lists; string leaves are scanned for obvious secret
    patterns.  Non-strings (numbers, booleans, None) pass through.  A depth or
    total-size ceiling stops pathological payloads without raising.
    """
    budget = {"remaining": max_total}

    def walk(item: Any, depth: int) -> Any:
        if depth > max_depth or budget["remaining"] <= 0:
            return _REDACTED
        if isinstance(item, dict):
            out: dict[str, Any] = {}
            for key, value in item.items():
                budget["remaining"] -= len(str(key)) + 4
                out[str(key)] = walk(value, depth + 1)
            return out
        if isinstance(item, (list, tuple)):
            return [walk(v, depth + 1) for v in item]
        if isinstance(item, str):
            budget["remaining"] -= len(item)
            return _redact_text(item)
        return item

    return walk(payload, 0)
