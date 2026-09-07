"""Bounded TTL cache for idempotent read-only tool calls.

Repeated identical queries in one agent turn (status/status/status, list,
stat, accounting) are the easiest way to burn login-node I/O.  This module
deduplicates them transparently: same tool + same normalized arguments within
the TTL window returns the previous result without a new SSH round trip.

* Only *idempotent*, read-only tools are cached.
* Every mutating tool (write/delete/upload/mkdir/submit/cancel) invalidates
  the whole cache, so a cached listing can never go stale after a change.
* The cache is bounded (max entries) and never grows without limit.
"""

from __future__ import annotations

import json
import time
from typing import Any


class QueryCache:
    def __init__(self, *, ttl_seconds: float = 2.0, max_entries: int = 512) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or ttl_seconds < 0:
            raise ValueError("ttl_seconds must be a non-negative number")
        self._ttl = float(ttl_seconds)
        self._max_entries = int(max_entries)
        self._data: dict[str, tuple[float, Any]] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def key(self, tool: str, args: dict[str, Any] | None) -> str:
        """Normalized cache key for a tool + arguments."""
        payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
        return f"{tool}\x00{payload}"

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if time.monotonic() > expires:
            self._data.pop(key, None)
            return None
        return value

    def put(self, key: str, value: Any) -> None:
        if self._ttl <= 0:
            return
        if len(self._data) >= self._max_entries:
            # drop the oldest entry to stay bounded
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (time.monotonic() + self._ttl, value)

    def invalidate(self) -> None:
        """Drop everything (called after any mutating tool)."""
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
