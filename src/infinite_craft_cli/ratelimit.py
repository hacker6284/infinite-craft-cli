"""Sliding window rate limiter for API requests."""

import asyncio
import time
from collections import deque
from typing import Callable

# Returned by acquire(); pass to release() to drop a specific slot.
RateLimitToken = float | None


class RateLimitCancelled(Exception):
    """acquire() was interrupted before a request slot became available."""


class RateLimiter:
    """Limits requests to max_requests per window_seconds using a sliding window."""

    def __init__(self, max_requests: int, window_seconds: float = 60.0):
        self._max = max_requests
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        cancel_check: Callable[[], bool] | None = None,
        *,
        sleep_step: float = 0.1,
        _wait_callback: Callable[[bool], None] | None = None,
    ) -> RateLimitToken:
        """Wait until a request slot is available, then record the request.

        Returns a token for this slot; pass it to ``release()`` if the request
        is cancelled before the HTTP call. Raises RateLimitCancelled if
        ``cancel_check`` returns True during a wait (no slot recorded).

        If ``_wait_callback`` provided (internal, for TUI indicator only), called
        with True on entering a backoff wait and False on exit. Calls are best-effort
        (exceptions swallowed) so UI errors never strand the flag or skip waits.
        """
        if self._max <= 0:
            return None

        while True:
            async with self._lock:
                now = time.monotonic()
                # Evict expired timestamps
                while self._timestamps and self._timestamps[0] + self._window <= now:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max:
                    token = time.monotonic()
                    self._timestamps.append(token)
                    return token

                # Wait until the oldest request expires
                wait_time = (self._timestamps[0] + self._window) - now

            if wait_time <= 0:
                if cancel_check and cancel_check():
                    raise RateLimitCancelled()
                continue

            remaining = wait_time
            if _wait_callback:
                try:
                    _wait_callback(True)
                except Exception:
                    pass  # best-effort UI only; never break wait or leave flag
            try:
                while remaining > 0:
                    if cancel_check and cancel_check():
                        raise RateLimitCancelled()
                    chunk = min(sleep_step, remaining)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
            finally:
                if _wait_callback:
                    try:
                        _wait_callback(False)
                    except Exception:
                        pass

    async def release(self, token: RateLimitToken):
        """Release a specific acquire token (e.g. after cancel before HTTP)."""
        if token is None:
            return
        async with self._lock:
            try:
                self._timestamps.remove(token)
            except ValueError:
                pass
