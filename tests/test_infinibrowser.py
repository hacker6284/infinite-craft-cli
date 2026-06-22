"""Tests for fetch_json (shared HTTP helper) and Infinibrowser wrappers."""

import sys
import pytest
from unittest.mock import patch, MagicMock

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


@pytest.fixture(autouse=True)
def clear_cache():
    from infinite_craft_cli.client import clear_fetch_cache
    clear_fetch_cache()
    yield
    clear_fetch_cache()


class TestFetchJson:
    def test_successful_request(self):
        from infinite_craft_cli.client import fetch_json
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"text": "Water"}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session):
            result = fetch_json("https://example.com/api", {"id": "Water"})
        assert result == {"text": "Water"}

    def test_caches_result(self):
        from infinite_craft_cli.client import fetch_json
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"text": "Water"}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session):
            fetch_json("https://example.com/api", {"id": "Water"})
            fetch_json("https://example.com/api", {"id": "Water"})
        mock_session.get.assert_called_once()

    def test_different_params_not_cached(self):
        from infinite_craft_cli.client import fetch_json
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session):
            fetch_json("https://example.com/api", {"id": "Water"})
            fetch_json("https://example.com/api", {"id": "Fire"})
        assert mock_session.get.call_count == 2

    def test_error_returns_none(self):
        from infinite_craft_cli.client import fetch_json
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("timeout")
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session):
            result = fetch_json("https://example.com/api", {"id": "Water"})
        assert result is None

    def test_error_not_cached(self):
        from infinite_craft_cli.client import fetch_json
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("timeout")
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session):
            fetch_json("https://example.com/fail", {"id": "Fail"})
        # Second call should try again
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"text": "Fail"}
        mock_session2 = MagicMock()
        mock_session2.get.return_value = mock_resp
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session2):
            result = fetch_json("https://example.com/fail", {"id": "Fail"})
        assert result is not None

    def test_no_params(self):
        from infinite_craft_cli.client import fetch_json
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session):
            result = fetch_json("https://example.com/health")
        assert result == {"ok": True}

    def test_passes_timeout(self):
        from infinite_craft_cli.client import fetch_json
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session):
            fetch_json("https://example.com/api", timeout=30)
        mock_session.get.assert_called_once_with("https://example.com/api", params=None, timeout=30)

    def test_use_cache_false_bypasses_cache(self):
        from infinite_craft_cli.client import fetch_json
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"steps": []}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("infinite_craft_cli.client._get_sync_session", return_value=mock_session):
            fetch_json("https://example.com/api", {"id": "X"})
            # Second call with use_cache=False should hit the network again
            fetch_json("https://example.com/api", {"id": "X"}, use_cache=False)
        assert mock_session.get.call_count == 2


class TestIbFetch:
    def test_success(self):
        from infinite_craft_cli.cli import _ib_fetch
        with patch("infinite_craft_cli.cli.fetch_json", return_value={"text": "Water"}):
            result = _ib_fetch("item", {"id": "Water"})
        assert result == {"text": "Water"}

    def test_failure_prints_error(self, capsys):
        from infinite_craft_cli.cli import _ib_fetch
        with patch("infinite_craft_cli.cli.fetch_json", return_value=None):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = _ib_fetch("item", {"id": "Water"})
        assert result is None


class TestIbFetchQuiet:
    def test_success(self):
        from infinite_craft_cli.cli import _ib_fetch_quiet
        with patch("infinite_craft_cli.cli.fetch_json", return_value={"text": "Water"}):
            result = _ib_fetch_quiet("item", {"id": "Water"})
        assert result == {"text": "Water"}

    def test_failure_silent(self, capsys):
        from infinite_craft_cli.cli import _ib_fetch_quiet
        with patch("infinite_craft_cli.cli.fetch_json", return_value=None):
            result = _ib_fetch_quiet("item", {"id": "Water"})
        assert result is None
        captured = capsys.readouterr()
        assert "failed" not in captured.out
