from __future__ import annotations

import time

import pytest

from app.core.rate_limiter import RateLimiter


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_within_limit(self):
        limiter = RateLimiter(max_per_second=10)
        start = time.time()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.time() - start
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_acquire_exceeds_limit(self):
        limiter = RateLimiter(max_per_second=2)
        start = time.time()
        for _ in range(3):
            await limiter.acquire()
        elapsed = time.time() - start
        assert elapsed >= 0.5
