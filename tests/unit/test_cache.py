from __future__ import annotations

import time

from app.core.cache import TTLCache


class TestTTLCache:
    def test_set_and_get(self):
        cache = TTLCache()
        cache.set("key", "value", 60)
        assert cache.get("key") == "value"

    def test_get_expired(self):
        cache = TTLCache()
        cache.set("key", "value", 0.01)
        time.sleep(0.02)
        assert cache.get("key") is None

    def test_invalidate(self):
        cache = TTLCache()
        cache.set("key", "value", 60)
        cache.invalidate("key")
        assert cache.get("key") is None

    def test_clear(self):
        cache = TTLCache()
        cache.set("a", 1, 60)
        cache.set("b", 2, 60)
        cache.set("c", 3, 60)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None

    def test_get_nonexistent(self):
        cache = TTLCache()
        assert cache.get("unknown") is None
