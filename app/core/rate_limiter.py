from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_per_second: int = 10) -> None:
        self._max = max_per_second
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.time()
                while self._timestamps and now - self._timestamps[0] >= 1.0:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._max:
                    self._timestamps.append(time.time())
                    return
                wait = 1.0 - (now - self._timestamps[0])
            if wait > 0:
                await asyncio.sleep(wait)
