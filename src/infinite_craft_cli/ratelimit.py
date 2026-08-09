"""Sliding window rate limiter for API requests.

Pure chrome math (slots left, next-slot fraction, bar fills) lives in the
shared kernel; this module owns the clock, deque, async acquire, and HTTP
integration seams.
"""

import asyncio
import time
from collections import deque
from typing import Callable

from infinite_craft_cli._sudo import craft

# Returned by acquire(); pass to release() to drop a specific slot.
RateLimitToken = float | None


class RateLimitCancelled(Exception):
    """acquire() was interrupted before a request slot became available."""


def _ms(t: float) -> int:
    """Monotonic/wall seconds → integer milliseconds for kernel math."""
    return int(t * 1000)


class RateLimiter:
    """Limits requests to max_requests per window_seconds using a sliding window."""

    def __init__(self, max_requests: int, window_seconds: float = 60.0):
        self._max = max_requests
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        # Monotonic time of the last slot free (window expiry or release).
        # Left half of the rate bar resets here and fills until the next free.
        self._last_freed_at: float | None = None

    def _evict_expired(self, now: float) -> None:
        """Drop timestamps outside the window; record free time for the rate bar."""
        freed = False
        while self._timestamps and self._timestamps[0] + self._window <= now:
            self._timestamps.popleft()
            freed = True
        if freed:
            self._last_freed_at = now

    def _next_slot_frac(self, now: float) -> float:
        """Progress [0,1] toward the next slot free (kernel pure math)."""
        if not self._timestamps:
            return 1.0
        oldest = self._timestamps[0]
        last_freed = self._last_freed_at if self._last_freed_at is not None else 0.0
        milli = craft.rate_next_slot_frac_milli(
            _ms(now),
            _ms(oldest),
            _ms(last_freed),
            _ms(self._window),
            True,
            self._last_freed_at is not None,
        )
        return milli / 1000.0

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
                self._evict_expired(now)

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
                self._last_freed_at = time.monotonic()
            except ValueError:
                pass

    def remaining(self) -> tuple[int, int]:
        """Return ``(slots_left, max_requests)`` in the current sliding window.

        Pure snapshot for chrome/UI; does not acquire a slot. Evicts expired
        timestamps on the calling thread without the async lock (best-effort
        display only — concurrent acquire may race slightly).
        """
        left, maximum, _frac = self.chrome_snapshot()
        return (left, maximum)

    def chrome_snapshot(self) -> tuple[int, int, float]:
        """Return ``(slots_left, max_requests, next_slot_frac)`` for the rate bar.

        ``next_slot_frac`` is in ``[0, 1]``: progress from the last slot free
        (or the current oldest's birth) until the next free. Resets to 0 when
        something drops off the window log, then fills over that inter-drop
        interval — so a 2s gap to the next free fills the left half in 2s,
        not 60s. ``1.0`` when idle (no in-window requests). Best-effort display
        only (no async lock).
        """
        if self._max <= 0:
            return (0, 0, 1.0)
        now = time.monotonic()
        self._evict_expired(now)
        left = craft.rate_slots_left(len(self._timestamps), self._max)
        frac = self._next_slot_frac(now)
        return (left, self._max, frac)
