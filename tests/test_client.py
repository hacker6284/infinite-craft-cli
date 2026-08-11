"""Tests for InfiniteCraftClient (mocked curl_cffi)."""

import asyncio
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.client import InfiniteCraftClient


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


class TestPair:
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
