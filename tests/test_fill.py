"""Tests for _fill_missing_recipes_async."""

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
    def test_fetches_and_records_recipes(self, capsys):
        """Successfully fetches lineage from Infinibrowser and records recipes."""
        from infinite_craft_cli.cli import _fill_missing_recipes_async
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
            with patch("infinite_craft_cli.cli._ib_fetch", side_effect=[item_data, lineage_data]):
                with patch("infinite_craft_cli.cli._record_recipes_batch") as mock_record:
                    with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                        run_async(_fill_missing_recipes_async(storage))
        mock_record.assert_called_once()
        captured = capsys.readouterr()
        assert "Fetched 1 lineages" in captured.out

    def test_adds_intermediate_elements(self, capsys):
        """Elements from lineage steps that aren't in storage get added."""
        from infinite_craft_cli.cli import _fill_missing_recipes_async
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
            with patch("infinite_craft_cli.cli._ib_fetch", side_effect=[item_data, lineage_data]):
                with patch("infinite_craft_cli.cli._record_recipes_batch"):
                    with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                        run_async(_fill_missing_recipes_async(storage))
        # Magma is an intermediate element not originally in storage — the
        # lineage fold's element batch (insert-or-ignore) must include it
        batch = storage.add_batch.call_args[0][0]
        assert ("Magma", "🔴", False) in batch

    def test_keyboard_interrupt_stops_early(self, capsys):
        """Ctrl+C during fetching stops early and reports partial progress."""
        from infinite_craft_cli.cli import _fill_missing_recipes_async
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Steam", "💨"),
            MockElement("Mud", ""),
        ])
        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            with patch("infinite_craft_cli.cli._ib_fetch", side_effect=KeyboardInterrupt):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    run_async(_fill_missing_recipes_async(storage))
        captured = capsys.readouterr()
        assert "Stopped early" in captured.out
