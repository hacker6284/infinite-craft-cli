"""Tests for _fill_missing_recipes."""

import asyncio
import sys
import pytest
from unittest.mock import patch, AsyncMock

from tests.conftest import MockElement, make_mock_storage

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro):
    return asyncio.run(coro)


class TestFillMissingRecipes:
    def test_all_have_recipes(self, capsys):
        """When all elements already have recipes, prints message and returns."""
        from infinite_craft_cli.cli import _fill_missing_recipes
        storage = make_mock_storage()
        # Base elements don't need recipes, so with only base elements, nothing is missing
        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            _fill_missing_recipes(storage)
        captured = capsys.readouterr()
        assert "All elements have recipes" in captured.out

    def test_fetches_and_records_recipes(self, capsys):
        """Successfully fetches lineage from Infinibrowser and records recipes."""
        from infinite_craft_cli.cli import _fill_missing_recipes
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Steam", "💨"),
        ])
        # Steam has no recipe initially
        recipes_state = [{}]  # mutable so _load_recipes returns updated state
        def load_recipes():
            return dict(recipes_state[0])

        item_data = {"text": "Steam", "emoji": "💨", "depth": 1}
        lineage_data = {"steps": [{
            "a": {"id": "Water", "emoji": "💧"},
            "b": {"id": "Fire", "emoji": "🔥"},
            "result": {"id": "Steam", "emoji": "💨"},
        }]}

        with patch("infinite_craft_cli.cli._load_recipes", side_effect=load_recipes):
            with patch("infinite_craft_cli.cli._ib_fetch_quiet", side_effect=[item_data, lineage_data]):
                with patch("infinite_craft_cli.cli._record_recipe") as mock_record:
                    with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                        _fill_missing_recipes(storage)
        mock_record.assert_called_once_with("Steam", "Water", "Fire")
        captured = capsys.readouterr()
        assert "Fetched 1 lineages" in captured.out

    def test_handles_not_found(self, capsys):
        """Elements not found on Infinibrowser are counted as failed."""
        from infinite_craft_cli.cli import _fill_missing_recipes
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Nonexistent", "❓"),
        ])
        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            with patch("infinite_craft_cli.cli._ib_fetch_quiet", return_value={"code": 404}):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    _fill_missing_recipes(storage)
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or "Fetched 0" in captured.out

    def test_handles_fetch_failure(self, capsys):
        """When Infinibrowser returns None, element is counted as failed."""
        from infinite_craft_cli.cli import _fill_missing_recipes
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Broken", "💔"),
        ])
        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            with patch("infinite_craft_cli.cli._ib_fetch_quiet", return_value=None):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    _fill_missing_recipes(storage)
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or "Fetched 0" in captured.out

    def test_adds_intermediate_elements(self, capsys):
        """Elements from lineage steps that aren't in storage get added."""
        from infinite_craft_cli.cli import _fill_missing_recipes
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Lava", "🌋"),
        ])
        item_data = {"text": "Lava", "emoji": "🌋", "depth": 2}
        lineage_data = {"steps": [
            {
                "a": {"id": "Earth", "emoji": "🌍"},
                "b": {"id": "Fire", "emoji": "🔥"},
                "result": {"id": "Magma", "emoji": "🔴"},
            },
            {
                "a": {"id": "Magma", "emoji": "🔴"},
                "b": {"id": "Water", "emoji": "💧"},
                "result": {"id": "Lava", "emoji": "🌋"},
            },
        ]}

        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            with patch("infinite_craft_cli.cli._ib_fetch_quiet", side_effect=[item_data, lineage_data]):
                with patch("infinite_craft_cli.cli._record_recipe"):
                    with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                        _fill_missing_recipes(storage)
        # Magma is an intermediate element not originally in storage — should be added
        storage.add.assert_any_call(name='Magma', emoji='🔴', is_first_discovery=False)

    def test_cancelled_during_sleep_stops_early(self, capsys):
        """SIGINT cancel flag during rate-limit sleep stops promptly."""
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import _fill_missing_recipes

        cli._cancelled = False
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Steam", "💨"),
            MockElement("Mud", "🟤"),
        ])
        item_data = {"text": "Steam", "emoji": "💨", "depth": 1}
        lineage_data = {"steps": [{
            "a": {"id": "Water", "emoji": "💧"},
            "b": {"id": "Fire", "emoji": "🔥"},
            "result": {"id": "Steam", "emoji": "💨"},
        }]}

        async def cancel_on_sleep(seconds, step=0.1):
            cli._cancelled = True
            return True

        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            with patch(
                "infinite_craft_cli.cli._ib_fetch_quiet",
                side_effect=[item_data, lineage_data, item_data, lineage_data],
            ):
                with patch("infinite_craft_cli.cli._record_recipe"):
                    with patch(
                        "infinite_craft_cli.cli._sleep_cancellable_async",
                        side_effect=cancel_on_sleep,
                    ):
                        _fill_missing_recipes(storage)
        captured = capsys.readouterr()
        assert "Stopped early" in captured.out

    def test_fill_cancel_marks_notified_for_worker(self, capsys):
        """Fill cancel summary must suppress duplicate Skipped from worker."""
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import _api_worker, _fill_missing_recipes_async

        cli._cancelled = False
        cli._skip_summary_shown = False
        storage = make_mock_storage([
            MockElement("Steam", "💨"),
        ])

        async def cancel_on_sleep(seconds, step=0.1):
            cli._cancelled = True
            return True

        item_data = {"text": "Steam", "emoji": "💨"}
        lineage_data = {"steps": [{
            "a": {"id": "Water", "emoji": "💧"},
            "b": {"id": "Fire", "emoji": "🔥"},
            "result": {"id": "Steam", "emoji": "💨"},
        }]}

        async def dispatch(_c, s, _l):
            await _fill_missing_recipes_async(s)

        async def run():
            with (
                patch("infinite_craft_cli.cli._load_recipes", return_value={}),
                patch(
                    "infinite_craft_cli.cli._ib_fetch_quiet_async",
                    new_callable=AsyncMock,
                    side_effect=[item_data, lineage_data],
                ),
                patch("infinite_craft_cli.cli._record_recipe"),
                patch(
                    "infinite_craft_cli.cli._sleep_cancellable_async",
                    side_effect=cancel_on_sleep,
                ),
                patch("infinite_craft_cli.cli._dispatch_line", side_effect=dispatch),
            ):
                cli._command_queue = ["/fill"]
                await _api_worker(AsyncMock(), storage)

        run_async(run())
        out = capsys.readouterr().out
        assert "Stopped early" in out
        assert out.count("Skipped.") == 0
        cli._reset_cancelled()

    def test_keyboard_interrupt_stops_early(self, capsys):
        """Ctrl+C during fetching stops early and reports partial progress."""
        from infinite_craft_cli.cli import _fill_missing_recipes
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Steam", "💨"),
            MockElement("Mud", ""),
        ])
        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            with patch("infinite_craft_cli.cli._ib_fetch_quiet", side_effect=KeyboardInterrupt):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    _fill_missing_recipes(storage)
        captured = capsys.readouterr()
        assert "Stopped early" in captured.out
