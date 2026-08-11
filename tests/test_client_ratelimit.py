"""Integration tests for InfiniteCraftClient rate-limit cancellation."""

import asyncio
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.client import InfiniteCraftClient
from infinite_craft_cli.ratelimit import RateLimitCancelled, RateLimiter


def run_async(coro):
    return asyncio.run(coro)


def _make_mock_session():
    mock_session_cls = MagicMock()
    mock_session = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"result": "Steam", "emoji": "💨", "isNew": False}
    mock_resp.raise_for_status = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_resp)
    mock_session.close = AsyncMock()
    mock_session_cls.return_value = mock_session
    return mock_session_cls, mock_session


class TestPairRateLimitIntegration:
    def test_cancel_during_rate_limit_wait_aborts_without_http(self):
        limiter = RateLimiter(max_requests=1, window_seconds=5.0)
        run_async(limiter.acquire())
        cancelled = False

        def cancel_check():
            return cancelled

        mock_cls, mock_session = _make_mock_session()

        async def cancel_soon():
            nonlocal cancelled
            await asyncio.sleep(0.05)
            cancelled = True

        async def run():
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient(
                    rate_limit=60,
                    cancel_check=cancel_check,
                    rate_limit_sleep_step=0.02,
                )
                client._rate_limiter = limiter
                await client.__aenter__()
                with pytest.raises(RateLimitCancelled):
                    await asyncio.gather(
                        client.pair("Water", "Fire"),
                        cancel_soon(),
                    )

        run_async(run())
        assert mock_session.get.await_count == 1
        # First acquire still holds the only slot; cancelled pair did not HTTP.
        left, _, _ = limiter.chrome_snapshot()
        assert left == 0

    def test_post_acquire_cancel_releases_token_and_allows_retry(self):
        limiter = RateLimiter(max_requests=1, window_seconds=5.0)
        cancelled = True

        def cancel_check():
            return cancelled

        mock_cls, mock_session = _make_mock_session()

        async def run():
            nonlocal cancelled
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient(
                    rate_limit=60,
                    cancel_check=cancel_check,
                    rate_limit_sleep_step=0.02,
                )
                client._rate_limiter = limiter
                await client.__aenter__()
                with pytest.raises(RateLimitCancelled):
                    await client.pair("Water", "Fire")
                # Post-acquire cancel released the slot; retry can re-acquire.
                left, _, _ = limiter.chrome_snapshot()
                assert left == 1
                cancelled = False
                result = await client.pair("Water", "Fire")
                assert result.name == "Steam"

        run_async(run())
        # __aenter__ cloudflare visit + one successful pair HTTP call
        assert mock_session.get.await_count == 2

    def test_concurrent_pair_post_acquire_cancel(self):
        """Two concurrent pair() calls; one cancels after acquire, other completes."""
        limiter = RateLimiter(max_requests=2, window_seconds=5.0)
        mock_cls, mock_session = _make_mock_session()
        state = {"acquired": 0, "cancel": False}
        first_acquired = asyncio.Event()
        original_acquire = RateLimiter.acquire

        async def wrapped_acquire(self, *args, **kwargs):
            token = await original_acquire(self, *args, **kwargs)
            state["acquired"] += 1
            if state["acquired"] == 1:
                first_acquired.set()
            elif state["acquired"] == 2:
                state["cancel"] = True
            return token

        async def pair_with_retry(client, first, second):
            for _ in range(2):
                try:
                    return await client.pair(first, second)
                except RateLimitCancelled:
                    state["cancel"] = False
            raise AssertionError("retry failed")

        async def run():
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                with patch.object(RateLimiter, "acquire", wrapped_acquire):
                    client = InfiniteCraftClient(
                        rate_limit=60,
                        cancel_check=lambda: state["cancel"],
                        rate_limit_sleep_step=0.02,
                    )
                    client._rate_limiter = limiter
                    await client.__aenter__()
                    first_task = asyncio.create_task(client.pair("Water", "Fire"))
                    await first_acquired.wait()
                    second_task = asyncio.create_task(
                        pair_with_retry(client, "Wind", "Earth")
                    )
                    return await asyncio.gather(first_task, second_task)

        results = run_async(run())
        assert len(results) == 2
        assert all(r.name == "Steam" for r in results)
        # __aenter__ cloudflare visit + two successful pair HTTP calls
        assert mock_session.get.await_count == 3
