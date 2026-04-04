"""Tests for the RateLimiter."""

import asyncio
import sys
import time
import pytest
from unittest.mock import patch

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.ratelimit import RateLimiter


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


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
