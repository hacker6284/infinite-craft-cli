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

    def test_multiple_under_limit(self):
        limiter = RateLimiter(max_requests=5)
        for _ in range(5):
            run_async(limiter.acquire())
        # All 5 should succeed without blocking

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
        assert len(limiter._timestamps) == 1
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
        assert len(limiter._timestamps) == 1

    def test_release_restores_slot(self):
        limiter = RateLimiter(max_requests=1, window_seconds=2.0)
        token = run_async(limiter.acquire())
        assert len(limiter._timestamps) == 1
        run_async(limiter.release(token))
        assert len(limiter._timestamps) == 0
        run_async(limiter.acquire())
        assert len(limiter._timestamps) == 1

    def test_concurrent_release_only_drops_own_token(self):
        limiter = RateLimiter(max_requests=2, window_seconds=2.0)
        token_a = run_async(limiter.acquire())
        token_b = run_async(limiter.acquire())
        assert len(limiter._timestamps) == 2
        run_async(limiter.release(token_a))
        assert len(limiter._timestamps) == 1
        assert token_b in limiter._timestamps

    def test_cancel_on_zero_wait_branch(self):
        """Cancel during re-acquire spin must not consume a slot."""
        limiter = RateLimiter(max_requests=1, window_seconds=5.0)
        run_async(limiter.acquire())
        with pytest.raises(RateLimitCancelled):
            run_async(limiter.acquire(cancel_check=lambda: True, sleep_step=0.01))
        assert len(limiter._timestamps) == 1

    def test_wait_callback_invoked_true_then_false(self):
        """Direct unit test (no UI) for the callback contract on acquire backoff path."""
        limiter = RateLimiter(max_requests=1, window_seconds=0.1)
        run_async(limiter.acquire())
        calls: list[bool] = []

        def cb(w: bool) -> None:
            calls.append(w)

        start = time.monotonic()
        run_async(limiter.acquire(_wait_callback=cb, sleep_step=0.01))
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05
        assert calls and calls[0] is True
        assert calls[-1] is False
