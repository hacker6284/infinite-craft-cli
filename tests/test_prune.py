"""Tests for /prune: orphan detection and Infinibrowser fillability checks."""

import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestIncludedElementNames:
    def test_bases_always_included(self):
        from infinite_craft_cli.cli import _included_element_names
        included = _included_element_names({})
        assert included == {"Water", "Fire", "Wind", "Earth"}

    def test_closes_over_constituents(self):
        from infinite_craft_cli.cli import _included_element_names
        recipes = {"Steam": [["Fire", "Water"]]}
        included = _included_element_names(recipes)
        assert included == {"Water", "Fire", "Wind", "Earth", "Steam"}


class TestOrphanCandidates:
    def test_orphan_with_no_lineage(self, tmp_path):
        from infinite_craft_cli.cli import _orphan_candidates
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Ghost", emoji="👻", is_first_discovery=False)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            assert [e.name for e in _orphan_candidates(storage)] == ["Ghost"]

    def test_terminal_constituent_not_orphan(self, tmp_path):
        from infinite_craft_cli.cli import _orphan_candidates, _record_recipe
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Magma", emoji="🔴", is_first_discovery=False)
        storage.add(name="Lava", emoji="🌋", is_first_discovery=False)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            _record_recipe("Lava", "Magma", "Water")
            assert "Magma" not in [e.name for e in _orphan_candidates(storage)]


class TestIbCanFill:
    def test_item_not_found(self):
        from infinite_craft_cli.cli import _ib_can_fill
        mock_resp = MagicMock(status_code=404)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("infinite_craft_cli.cli._get_sync_session", return_value=mock_session):
            assert _ib_can_fill("Ghost") is False

    def test_api_error_returns_none(self):
        from infinite_craft_cli.cli import _ib_can_fill
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("timeout")
        with patch("infinite_craft_cli.cli._get_sync_session", return_value=mock_session):
            assert _ib_can_fill("Steam") is None

    def test_recipe_available(self):
        from infinite_craft_cli.cli import _ib_can_fill
        item_resp = MagicMock(status_code=200, ok=True)
        item_resp.json.return_value = {"text": "Steam"}
        recipe_resp = MagicMock(status_code=200, ok=True)
        recipe_resp.json.return_value = {"steps": [{"a": {}, "b": {}, "result": {}}]}
        mock_session = MagicMock()
        mock_session.get.side_effect = [item_resp, recipe_resp]
        with patch("infinite_craft_cli.cli._get_sync_session", return_value=mock_session):
            assert _ib_can_fill("Steam") is True

    def test_recipe_not_found(self):
        from infinite_craft_cli.cli import _ib_can_fill
        item_resp = MagicMock(status_code=200, ok=True)
        item_resp.json.return_value = {"text": "Ghost"}
        recipe_resp = MagicMock(status_code=404)
        mock_session = MagicMock()
        mock_session.get.side_effect = [item_resp, recipe_resp]
        with patch("infinite_craft_cli.cli._get_sync_session", return_value=mock_session):
            assert _ib_can_fill("Ghost") is False


class TestPruneOrphans:
    def test_nothing_to_prune(self, capsys, tmp_path):
        from infinite_craft_cli.cli import _prune_orphans
        from infinite_craft_cli.storage import DiscoveryStorage

        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        recipes_path = tmp_path / "recipes.json"
        recipes_path.write_text("{}", encoding="utf-8")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(recipes_path)):
            _prune_orphans(storage)
        assert "Nothing to prune" in capsys.readouterr().out

    def test_prunes_unfillable_orphan(self, capsys, tmp_path):
        from infinite_craft_cli.cli import _prune_orphans
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Ghost", emoji="👻", is_first_discovery=False)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            with patch("infinite_craft_cli.cli._ib_can_fill", return_value=False):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    _prune_orphans(storage)
        assert storage.get_by_name("Ghost") is None
        assert "Pruned 1" in capsys.readouterr().out

    def test_keeps_fillable_orphan(self, capsys, tmp_path):
        from infinite_craft_cli.cli import _prune_orphans
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            with patch("infinite_craft_cli.cli._ib_can_fill", return_value=True):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    _prune_orphans(storage)
        assert storage.get_by_name("Steam") is not None
        captured = capsys.readouterr().out
        assert "Pruned 0" in captured
        assert "1 fillable" in captured

    def test_cancelled_during_sleep_stops_early(self, capsys, tmp_path):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import _prune_orphans
        from infinite_craft_cli.storage import DiscoveryStorage

        cli._cancelled = False
        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Ghost", emoji="👻", is_first_discovery=False)
        storage.add(name="Phantom", emoji="👻", is_first_discovery=False)

        async def cancel_on_sleep(seconds, step=0.1):
            cli._cancelled = True
            return True

        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            with patch("infinite_craft_cli.cli._ib_can_fill", return_value=False):
                with patch(
                    "infinite_craft_cli.cli._sleep_cancellable_async",
                    side_effect=cancel_on_sleep,
                ):
                    _prune_orphans(storage)
        captured = capsys.readouterr()
        assert "Stopped early" in captured.out

    def test_skips_on_api_error(self, capsys, tmp_path):
        from infinite_craft_cli.cli import _prune_orphans
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Ghost", emoji="👻", is_first_discovery=False)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            with patch("infinite_craft_cli.cli._ib_can_fill", return_value=None):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    _prune_orphans(storage)
        assert storage.get_by_name("Ghost") is not None
        assert "skipped" in capsys.readouterr().out.lower()

    def test_prune_cancel_marks_notified_for_worker(self, capsys, tmp_path):
        """Prune cancel summary must suppress duplicate Skipped from worker."""
        import asyncio
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import _api_worker, _prune_orphans_async
        from infinite_craft_cli.storage import DiscoveryStorage

        cli._cancelled = False
        cli._skip_summary_shown = False
        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Ghost", emoji="👻", is_first_discovery=False)

        async def cancel_on_sleep(seconds, step=0.1):
            cli._cancelled = True
            return True

        async def dispatch(_c, s, _l):
            with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
                await _prune_orphans_async(s)

        async def run():
            with (
                patch(
                    "infinite_craft_cli.cli._ib_can_fill_async",
                    new_callable=AsyncMock,
                    return_value=False,
                ),
                patch(
                    "infinite_craft_cli.cli._sleep_cancellable_async",
                    side_effect=cancel_on_sleep,
                ),
                patch("infinite_craft_cli.cli._dispatch_line", side_effect=dispatch),
            ):
                cli._command_queue = ["/prune"]
                await _api_worker(AsyncMock(), storage)

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "Stopped early" in out
        assert out.count("Skipped.") == 0
        cli._reset_cancelled()