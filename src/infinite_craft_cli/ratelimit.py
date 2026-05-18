"""Sliding window rate limiter for API requests."""

import asyncio
import time
from collections import deque


class RateLimiter:
    """Limits requests to max_requests per window_seconds using a sliding window."""

    def __init__(self, max_requests: int, window_seconds: float = 60.0):
        self._max = max_requests
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until a request slot is available, then record the request."""
        if self._max <= 0:
            return

        while True:
            async with self._lock:
                now = time.monotonic()
                # Evict expired timestamps
                while self._timestamps and self._timestamps[0] + self._window <= now:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max:
                    self._timestamps.append(now)
                    return

                # Wait until the oldest request expires
                wait_time = (self._timestamps[0] + self._window) - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
