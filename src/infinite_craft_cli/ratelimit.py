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
        self._base_max = max_requests
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        # Subset of slots spent serving hive bounties (gold in the rate bar).
        self._fleet_timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        # Monotonic time of the last slot free (window expiry or release).
        # Left half of the rate bar resets here and fills until the next free.
        self._last_freed_at: float | None = None

    def set_effective_max(self, effective: int) -> None:
        """Shrink/restore the window for the same-IP budget split.

        Clamped to [1, base]; acquire() reads ``_max`` each loop so the new
        budget applies to the very next slot decision."""
        if self._base_max <= 0:
            return
        self._max = max(1, min(int(effective), self._base_max))

    @property
    def base_max(self) -> int:
        return self._base_max

    def _evict_expired(self, now: float) -> None:
        """Drop timestamps outside the window; record free time for the rate bar."""
        freed = False
        while self._timestamps and self._timestamps[0] + self._window <= now:
            self._timestamps.popleft()
            freed = True
        while self._fleet_timestamps and self._fleet_timestamps[0] + self._window <= now:
            self._fleet_timestamps.popleft()
        if freed:
            self._last_freed_at = now

    def mark_fleet(self, token: RateLimitToken) -> None:
        """Tag an acquired slot as fleet spend (bounty work) for the gold bar."""
        if token is not None:
            self._fleet_timestamps.append(token)

    def fleet_used(self) -> int:
        return len(self._fleet_timestamps)

    def _next_slot_frac_milli(self, now: float) -> int:
        """Progress toward next slot free in thousandths [0, 1000] (kernel)."""
        if not self._timestamps:
            return 1000
        oldest = self._timestamps[0]
        last_freed = self._last_freed_at if self._last_freed_at is not None else 0.0
        return craft.rate_next_slot_frac_milli(
            _ms(now),
            _ms(oldest),
            _ms(last_freed),
            _ms(self._window),
            True,
            self._last_freed_at is not None,
        )

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
            try:
                self._fleet_timestamps.remove(token)
            except ValueError:
                pass

    def chrome_snapshot(self) -> tuple[int, int, int]:
        """Return ``(slots_left, max_requests, frac_milli)`` for the rate bar.

        ``frac_milli`` is in ``[0, 1000]``: progress from the last slot free
        (or the current oldest's birth) until the next free. Resets to 0 when
        something drops off the window log, then fills over that inter-drop
        interval — so a 2s gap to the next free fills the left half in 2s,
        not 60s. ``1000`` when idle (no in-window requests). Best-effort display
        only (no async lock).
        """
        if self._max <= 0:
            return (0, 0, 1000)
        now = time.monotonic()
        self._evict_expired(now)
        left = craft.rate_slots_left(len(self._timestamps), self._max)
        return (left, self._max, self._next_slot_frac_milli(now))

    def chrome_snapshot_split(self) -> tuple[int, int, int, int]:
        """``(slots_left, max, frac_milli, fleet_used)`` for the hive bar."""
        left, maximum, frac = self.chrome_snapshot()
        return (left, maximum, frac, len(self._fleet_timestamps))
