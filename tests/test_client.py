"""Tests for InfiniteCraftClient (mocked curl_cffi)."""

import asyncio
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.client import (
    InfiniteCraftClient,
    _BASE_URL,
    _PAIR_ENDPOINT,
    _IMPERSONATE,
)


def run_async(coro):
    # modernized (was deprecated get_event_loop)
    return asyncio.run(asyncio.wait_for(coro, timeout=30.0))


def _make_mock_session(response_data=None):
    """Create a mock AsyncSession that returns the given response data."""
    mock_session_cls = MagicMock()
    mock_session = AsyncMock()

    mock_resp = MagicMock()
    mock_resp.json.return_value = response_data or {
        "result": "Steam",
        "emoji": "💨",
        "isNew": False,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_session.get = AsyncMock(return_value=mock_resp)
    mock_session.close = AsyncMock()
    mock_session_cls.return_value = mock_session

    return mock_session_cls, mock_session, mock_resp


class TestSessionLifecycle:
    def test_visits_base_url_on_enter(self):
        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter"):
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
        # First call should be to base URL for cookies
        first_call = mock_session.get.call_args_list[0]
        assert (
            first_call.kwargs.get("url") == _BASE_URL or first_call.args[0] == _BASE_URL
        )

    def test_closes_session_on_exit(self):
        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter"):
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                run_async(client.__aexit__(None, None, None))
        mock_session.close.assert_called_once()

    def test_session_none_after_exit(self):
        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter"):
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                run_async(client.__aexit__(None, None, None))
        assert client._session is None


class TestPair:
    def test_sends_correct_params(self):
        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                run_async(client.pair("Water", "Fire"))
        # Second get call (first is cookie visit)
        pair_call = mock_session.get.call_args_list[1]
        assert pair_call.args[0] == _PAIR_ENDPOINT
        assert pair_call.kwargs["params"] == {"first": "Water", "second": "Fire"}

    def test_returns_element_on_success(self):
        mock_cls, _, _ = _make_mock_session(
            {"result": "Steam", "emoji": "💨", "isNew": False}
        )
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                result = run_async(client.pair("Water", "Fire"))
        assert result.name == "Steam"
        assert result.emoji == "💨"
        assert result.is_first_discovery is False

    def test_returns_none_element_for_nothing(self):
        mock_cls, _, _ = _make_mock_session(
            {"result": "Nothing", "emoji": "", "isNew": False}
        )
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                result = run_async(client.pair("Water", "Water"))
        assert result.name is None

    def test_first_discovery(self):
        mock_cls, _, _ = _make_mock_session(
            {"result": "Unicorn", "emoji": "🦄", "isNew": True}
        )
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                result = run_async(client.pair("Horse", "Rainbow"))
        assert result.is_first_discovery is True

    def test_calls_rate_limiter(self):
        mock_cls, _, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            mock_acquire = AsyncMock()
            MockRL.return_value.acquire = mock_acquire
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                run_async(client.pair("Water", "Fire"))
        mock_acquire.assert_called_once_with(
            cancel_check=None,
            sleep_step=0.1,
            _wait_callback=None,
        )

    def test_pair_aborts_after_cancelled_acquire(self):
        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock(return_value=123.0)
            MockRL.return_value.release = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient(cancel_check=lambda: True)
                run_async(client.__aenter__())
                from infinite_craft_cli.ratelimit import RateLimitCancelled

                with pytest.raises(RateLimitCancelled):
                    run_async(client.pair("Water", "Fire"))
        mock_session.get.assert_called_once()
        MockRL.return_value.release.assert_awaited_once_with(123.0)

    def test_forwards_cancel_check_to_acquire(self):
        mock_cls, _, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            mock_acquire = AsyncMock(return_value=1.0)
            MockRL.return_value.acquire = mock_acquire
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                check = lambda: False
                client = InfiniteCraftClient(cancel_check=check)
                run_async(client.__aenter__())
                run_async(client.pair("Water", "Fire"))
        assert mock_acquire.await_args.kwargs["cancel_check"] is check

    def test_forwards_rate_limit_sleep_step(self):
        mock_cls, _, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            mock_acquire = AsyncMock()
            MockRL.return_value.acquire = mock_acquire
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient(rate_limit_sleep_step=0.05)
                run_async(client.__aenter__())
                run_async(client.pair("Water", "Fire"))
        mock_acquire.assert_called_once_with(
            cancel_check=None, sleep_step=0.05, _wait_callback=None
        )

    def test_raises_on_http_error(self):
        mock_cls, mock_session, mock_resp = _make_mock_session()
        mock_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                with pytest.raises(Exception, match="403"):
                    run_async(client.pair("Water", "Fire"))

    def test_uses_chrome_impersonation(self):
        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                run_async(client.pair("Water", "Fire"))
        pair_call = mock_session.get.call_args_list[1]
        assert pair_call.kwargs["impersonate"] == _IMPERSONATE
