"""Tests for Infinibrowser integration: _ib_request, _ib_fetch, _ib_fetch_quiet."""

import json
import sys
import pytest
from unittest.mock import patch, MagicMock

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


@pytest.fixture(autouse=True)
def clear_ib_cache():
    import infinite_craft_cli.cli as cli
    cli._ib_cache.clear()
    yield
    cli._ib_cache.clear()


class TestIbRequest:
    def test_successful_request(self):
        from infinite_craft_cli.cli import _ib_request
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"text": "Water"}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response):
            result = _ib_request("item", {"id": "Water"})
        assert result == {"text": "Water"}

    def test_caches_result(self):
        from infinite_craft_cli.cli import _ib_request
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"text": "Water"}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            _ib_request("item", {"id": "Water"})
            _ib_request("item", {"id": "Water"})
        mock_urlopen.assert_called_once()

    def test_error_returns_none(self):
        from infinite_craft_cli.cli import _ib_request
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = _ib_request("item", {"id": "Water"})
        assert result is None

    def test_error_not_cached(self):
        from infinite_craft_cli.cli import _ib_request
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            _ib_request("item", {"id": "Fail"})
        # Second call should try again
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"text": "Fail"}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = _ib_request("item", {"id": "Fail"})
        assert result is not None
        mock_urlopen.assert_called_once()

    def test_url_construction(self):
        from infinite_craft_cli.cli import _ib_request, _IB_BASE
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            _ib_request("recipe", {"id": "Steam Engine"})
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert "recipe" in req.full_url
        assert "Steam" in req.full_url

    def test_user_agent_header(self):
        from infinite_craft_cli.cli import _ib_request, _IB_UA
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            _ib_request("item", {"id": "Test"})
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("User-agent") == _IB_UA


class TestIbFetch:
    def test_success(self, capsys):
        from infinite_craft_cli.cli import _ib_fetch
        with patch("infinite_craft_cli.cli._ib_request", return_value={"text": "Water"}):
            result = _ib_fetch("item", {"id": "Water"})
        assert result == {"text": "Water"}

    def test_failure_prints_error(self, capsys):
        from infinite_craft_cli.cli import _ib_fetch
        with patch("infinite_craft_cli.cli._ib_request", return_value=None):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = _ib_fetch("item", {"id": "Water"})
        assert result is None


class TestIbFetchQuiet:
    def test_success(self):
        from infinite_craft_cli.cli import _ib_fetch_quiet
        with patch("infinite_craft_cli.cli._ib_request", return_value={"text": "Water"}):
            result = _ib_fetch_quiet("item", {"id": "Water"})
        assert result == {"text": "Water"}

    def test_failure_silent(self, capsys):
        from infinite_craft_cli.cli import _ib_fetch_quiet
        with patch("infinite_craft_cli.cli._ib_request", return_value=None):
            result = _ib_fetch_quiet("item", {"id": "Water"})
        assert result is None
        captured = capsys.readouterr()
        assert "failed" not in captured.out
