"""QueryCache tests: dedup, TTL expiry, bounded size, invalidation."""

import time

import pytest

from hpc_mcp.cache import QueryCache


class TestQueryCache:
    def test_same_args_same_key(self):
        c = QueryCache()
        assert c.key("hpc.slurm.status", {"job_id": "1"}) == c.key("hpc.slurm.status", {"job_id": "1"})

    def test_different_args_different_key(self):
        c = QueryCache()
        assert c.key("hpc.slurm.status", {"job_id": "1"}) != c.key("hpc.slurm.status", {"job_id": "2"})

    def test_key_is_order_independent(self):
        c = QueryCache()
        assert c.key("t", {"a": 1, "b": 2}) == c.key("t", {"b": 2, "a": 1})

    def test_get_put_roundtrip(self):
        c = QueryCache(ttl_seconds=5)
        k = c.key("t", {"x": 1})
        assert c.get(k) is None
        c.put(k, {"state": "PENDING"})
        assert c.get(k) == {"state": "PENDING"}

    def test_ttl_expiry(self):
        c = QueryCache(ttl_seconds=0.05)
        k = c.key("t", {})
        c.put(k, "v")
        time.sleep(0.1)
        assert c.get(k) is None

    def test_invalidate_clears_all(self):
        c = QueryCache(ttl_seconds=5)
        c.put(c.key("a", {}), 1)
        c.put(c.key("b", {}), 2)
        c.invalidate()
        assert len(c) == 0

    def test_bounded_entries(self):
        c = QueryCache(ttl_seconds=5, max_entries=3)
        for i in range(10):
            c.put(c.key("t", {"i": i}), i)
        assert len(c) <= 3

    def test_ttl_zero_disables(self):
        c = QueryCache(ttl_seconds=0)
        k = c.key("t", {})
        c.put(k, "v")
        assert c.get(k) is None

    def test_invalid_ttl_rejected(self):
        with pytest.raises(ValueError):
            QueryCache(ttl_seconds=-1)
