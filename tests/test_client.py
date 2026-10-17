"""Tests for InfiniteCraftClient (mocked curl_cffi)."""

import asyncio
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.client import InfiniteCraftClient, NealBadPairResponse
from infinite_craft_cli.neal_session import NealSessionRequired


async def _test_creds():
    return ("tok", "sess")


def run_async(coro):
    # modernized (was deprecated get_event_loop)
    return asyncio.run(asyncio.wait_for(coro, timeout=30.0))


def _make_mock_session(response_data=None):
    """Create a mock AsyncSession that returns the given response data."""
    mock_session_cls = MagicMock()
    mock_session = AsyncMock()

    mock_resp = MagicMock()
    mock_resp.json.return_value = (
        {
            "result": "Steam",
            "emoji": "💨",
            "isNew": False,
        }
        if response_data is None
        else response_data
    )
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
                client = InfiniteCraftClient(credentials=_test_creds)
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
                client = InfiniteCraftClient(credentials=_test_creds)
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
                client = InfiniteCraftClient(credentials=_test_creds)
                run_async(client.__aenter__())
                result = run_async(client.pair("Horse", "Rainbow"))
        assert result.is_first_discovery is True

    def _pair_body(self, body):
        mock_cls, _, _ = _make_mock_session(body)
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient(credentials=_test_creds)
                run_async(client.__aenter__())
                return run_async(client.pair("Water", "Fire"))

    def test_missing_result_is_not_nothing(self):
        with pytest.raises(NealBadPairResponse):
            self._pair_body({})

    def test_empty_result_is_not_nothing(self):
        with pytest.raises(NealBadPairResponse):
            self._pair_body({"result": "", "emoji": ""})

    def test_null_result_is_not_nothing(self):
        with pytest.raises(NealBadPairResponse):
            self._pair_body({"result": None})

    def test_raises_on_http_error(self):
        mock_cls, mock_session, mock_resp = _make_mock_session()
        mock_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient(credentials=_test_creds)
                run_async(client.__aenter__())
                with pytest.raises(Exception, match="403"):
                    run_async(client.pair("Water", "Fire"))

    def test_refuses_tokenless_pair(self):
        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient()
                run_async(client.__aenter__())
                with pytest.raises(NealSessionRequired):
                    run_async(client.pair("Water", "Fire"))
        assert mock_session.get.await_count == 1

    def test_empty_credentials_are_tokenless(self):
        async def empty():
            return ("", "")

        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient(credentials=empty)
                run_async(client.__aenter__())
                with pytest.raises(NealSessionRequired):
                    run_async(client.pair("Water", "Fire"))
        assert mock_session.get.await_count == 1

    def test_sends_token_session_and_sorted_names(self):
        mock_cls, mock_session, _ = _make_mock_session()
        with patch("infinite_craft_cli.client.RateLimiter") as MockRL:
            MockRL.return_value.acquire = AsyncMock()
            with patch("curl_cffi.requests.AsyncSession", mock_cls):
                client = InfiniteCraftClient(credentials=_test_creds)
                run_async(client.__aenter__())
                run_async(client.pair("Water", "Fire"))
        kwargs = mock_session.get.await_args_list[-1].kwargs
        assert kwargs["params"]["first"] == "Fire"
        assert kwargs["params"]["second"] == "Water"
        assert kwargs["params"]["token"] == "tok"
        assert kwargs["headers"]["x-craft-session"] == "sess"
