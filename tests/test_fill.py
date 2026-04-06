"""Tests for _fill_missing_recipes."""

import sys
import pytest
from unittest.mock import patch, MagicMock, call

from tests.conftest import MockElement, make_mock_storage

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


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
                    with patch("time.sleep"):
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
                with patch("time.sleep"):
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
                with patch("time.sleep"):
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
                    with patch("time.sleep"):
                        _fill_missing_recipes(storage)
        # Magma is an intermediate element not originally in storage — should be added
        storage.add.assert_any_call(name='Magma', emoji='🔴', is_first_discovery=False)

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
                with patch("time.sleep"):
                    _fill_missing_recipes(storage)
        captured = capsys.readouterr()
        assert "Stopped early" in captured.out
