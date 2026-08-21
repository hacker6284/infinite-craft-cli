"""Tests for the RateLimiter."""

import asyncio
import sys
import time
import pytest

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.ratelimit import RateLimiter, RateLimitCancelled


def run_async(coro):
    return asyncio.run(coro)


class TestAcquire:
    def test_immediate_when_under_limit(self):
        limiter = RateLimiter(max_requests=10)
        start = time.monotonic()
        run_async(limiter.acquire())
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_zero_limit_never_blocks(self):
        limiter = RateLimiter(max_requests=0)
        start = time.monotonic()
        for _ in range(10):
            run_async(limiter.acquire())
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_blocks_when_at_limit(self):
        # Use a tiny window so the test completes quickly
        limiter = RateLimiter(max_requests=2, window_seconds=0.2)
        run_async(limiter.acquire())
        run_async(limiter.acquire())
        # Third acquire should block ~0.2s
        start = time.monotonic()
        run_async(limiter.acquire())
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15  # some tolerance

    def test_unblocks_after_window(self):
        limiter = RateLimiter(max_requests=1, window_seconds=0.1)
        run_async(limiter.acquire())
        # Wait for the window to pass
        time.sleep(0.15)
        start = time.monotonic()
        run_async(limiter.acquire())
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # should be immediate

    def test_cancel_check_interrupts_wait(self):
        limiter = RateLimiter(max_requests=1, window_seconds=2.0)
        run_async(limiter.acquire())
        cancelled = False

        def cancel_check():
            nonlocal cancelled
            return cancelled

        async def wait_then_cancel():
            nonlocal cancelled
            await asyncio.sleep(0.05)
            cancelled = True

        async def run():
            waiter = asyncio.create_task(
                limiter.acquire(cancel_check=cancel_check, sleep_step=0.02)
            )
            await wait_then_cancel()
            with pytest.raises(RateLimitCancelled):
                await waiter

        start = time.monotonic()
        run_async(run())
        elapsed = time.monotonic() - start
        assert elapsed < 0.5
        # First acquire still holds the only slot; cancel did not free it.
        left, maximum, _frac = limiter.chrome_snapshot()
        assert left == 0
        assert maximum == 1
        blocked = asyncio.Event()

        async def second_acquire():
            blocked.set()
            await limiter.acquire(cancel_check=lambda: False, sleep_step=0.02)

        async def run_blocked():
            task = asyncio.create_task(second_acquire())
            await blocked.wait()
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        run_async(run_blocked())
        # Slot still held after cancelled waiter; left remains 0.
        left2, _, _ = limiter.chrome_snapshot()
        assert left2 == 0

    def test_release_restores_slot(self):
        limiter = RateLimiter(max_requests=1, window_seconds=2.0)
        token = run_async(limiter.acquire())
        left, _, _ = limiter.chrome_snapshot()
        assert left == 0
        run_async(limiter.release(token))
        left_after, _, _ = limiter.chrome_snapshot()
        assert left_after == 1
        # Re-acquire succeeds immediately after release.
        start = time.monotonic()
        run_async(limiter.acquire())
        assert time.monotonic() - start < 0.1
        left_final, _, _ = limiter.chrome_snapshot()
        assert left_final == 0

    def test_cancel_on_zero_wait_branch(self):
        """Cancel during re-acquire spin must not consume a slot."""
        limiter = RateLimiter(max_requests=1, window_seconds=5.0)
        token = run_async(limiter.acquire())
        with pytest.raises(RateLimitCancelled):
            run_async(limiter.acquire(cancel_check=lambda: True, sleep_step=0.01))
        # First slot still held; cancel did not consume another.
        left, _, _ = limiter.chrome_snapshot()
        assert left == 0
        # Second acquire succeeds only after release of the first token.
        run_async(limiter.release(token))
        start = time.monotonic()
        run_async(limiter.acquire(cancel_check=lambda: False, sleep_step=0.01))
        assert time.monotonic() - start < 0.1


class TestChromeSnapshot:
    def test_idle_oldest_frac_is_full(self):
        limiter = RateLimiter(max_requests=60, window_seconds=60.0)
        left, maximum, frac_milli = limiter.chrome_snapshot()
        assert left == 60
        assert maximum == 60
        assert frac_milli == 1000

    def test_fresh_acquire_oldest_frac_near_zero(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60.0)
        run_async(limiter.acquire())
        left, maximum, frac_milli = limiter.chrome_snapshot()
        assert left == 4
        assert maximum == 5
        # First segment: start = birth of oldest → frac near 0.
        assert 0 <= frac_milli < 50

    def test_oldest_frac_grows_with_age(self):
        limiter = RateLimiter(max_requests=1, window_seconds=1.0)
        run_async(limiter.acquire())
        time.sleep(0.4)
        _left, _maximum, frac_milli = limiter.chrome_snapshot()
        assert 250 <= frac_milli <= 700

    def test_frac_resets_on_slot_free_and_scales_to_next_drop(self):
        """After a free, bar resets; next fill uses inter-drop interval, not full window."""
        limiter = RateLimiter(max_requests=2, window_seconds=0.4)
        run_async(limiter.acquire())
        time.sleep(0.15)
        run_async(limiter.acquire())  # full; oldest ages ~0.15s of 0.4s
        # Wait until first slot frees (oldest expires).
        time.sleep(0.30)
        left, _maximum, frac_just_after = limiter.chrome_snapshot()
        assert left == 1  # one free
        # Just after free: segment start = free time → frac near 0 (not ~1000 from full window).
        assert frac_just_after < 350
        time.sleep(0.12)
        _left2, _m2, frac_mid = limiter.chrome_snapshot()
        # Midway through remaining life of second request (~0.25s left after free).
        assert frac_mid > frac_just_after

    def test_release_resets_last_freed(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60.0)
        t1 = run_async(limiter.acquire())
        run_async(limiter.acquire())
        run_async(limiter.release(t1))
        left, _maximum, frac_milli = limiter.chrome_snapshot()
        assert left == 1
        # Release counts as a free → segment restarts near 0.
        assert frac_milli < 50

    def test_chrome_snapshot_zero_limit(self):
        limiter = RateLimiter(max_requests=0)
        assert limiter.chrome_snapshot() == (0, 0, 1000)


class TestHiveExtensions:
    def test_effective_max_splits_and_restores(self):
        limiter = RateLimiter(max_requests=60)
        assert limiter.base_max == 60
        limiter.set_effective_max(30)
        left, maximum, _f = limiter.chrome_snapshot()
        assert maximum == 30
        # Restore never exceeds base.
        limiter.set_effective_max(999)
        assert limiter.chrome_snapshot()[1] == 60
        # Never below 1.
        limiter.set_effective_max(0)
        assert limiter.chrome_snapshot()[1] == 1

    def test_effective_max_actually_gates_acquire(self):
        async def run():
            limiter = RateLimiter(max_requests=60)
            limiter.set_effective_max(2)
            await limiter.acquire()
            await limiter.acquire()
            # Third acquire must now block (window full at the split budget).
            with pytest.raises(RateLimitCancelled):
                await limiter.acquire(cancel_check=lambda: True)

        asyncio.run(asyncio.wait_for(run(), timeout=5))

    def test_fleet_slots_tracked_in_split_snapshot(self):
        async def run():
            limiter = RateLimiter(max_requests=60)
            t1 = await limiter.acquire()
            limiter.mark_fleet(t1)
            await limiter.acquire()  # own spend, untagged
            left, maximum, frac, fleet = limiter.chrome_snapshot_split()
            assert maximum == 60
            assert left == 58
            assert fleet == 1

        asyncio.run(asyncio.wait_for(run(), timeout=5))

    def test_release_drops_fleet_tag(self):
        async def run():
            limiter = RateLimiter(max_requests=60)
            t1 = await limiter.acquire()
            limiter.mark_fleet(t1)
            assert limiter.fleet_used() == 1
            await limiter.release(t1)
            assert limiter.fleet_used() == 0

        asyncio.run(asyncio.wait_for(run(), timeout=5))
