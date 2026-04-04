"""Tests for recipe functions: _load_recipes, _save_recipes, _record_recipe, do_recipe, do_unfilled."""

import json
import os
import sys
import pytest
from unittest.mock import patch, mock_open

from tests.conftest import MockElement, make_mock_game

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestLoadRecipes:
    def test_file_exists(self, tmp_path):
        from infinite_craft_cli.cli import _load_recipes
        recipes = {"Steam": [["Fire", "Water"]]}
        path = tmp_path / "recipes.json"
        path.write_text(json.dumps(recipes))
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = _load_recipes()
        assert result == recipes

    def test_file_not_exists(self, tmp_path):
        from infinite_craft_cli.cli import _load_recipes
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "missing.json")):
            result = _load_recipes()
        assert result == {}

    def test_empty_file(self, tmp_path):
        from infinite_craft_cli.cli import _load_recipes
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = _load_recipes()
        assert result == {}


class TestSaveRecipes:
    def test_saves_json(self, tmp_path):
        from infinite_craft_cli.cli import _save_recipes
        path = tmp_path / "recipes.json"
        recipes = {"Steam": [["Fire", "Water"]]}
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            _save_recipes(recipes)
        loaded = json.loads(path.read_text())
        assert loaded == recipes

    def test_uses_indent(self, tmp_path):
        from infinite_craft_cli.cli import _save_recipes
        path = tmp_path / "recipes.json"
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            _save_recipes({"A": [["B", "C"]]})
        content = path.read_text()
        assert "\n" in content  # indented, not one line


class TestRecordRecipe:
    def test_new_recipe(self, tmp_path):
        from infinite_craft_cli.cli import _record_recipe, _load_recipes
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            _record_recipe("Steam", "Water", "Fire")
            recipes = _load_recipes()
        assert "Steam" in recipes
        assert ["Fire", "Water"] in recipes["Steam"]  # sorted

    def test_duplicate_not_added(self, tmp_path):
        from infinite_craft_cli.cli import _record_recipe, _load_recipes
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            _record_recipe("Steam", "Water", "Fire")
            _record_recipe("Steam", "Fire", "Water")  # same pair reversed
            recipes = _load_recipes()
        assert len(recipes["Steam"]) == 1

    def test_sorted_order(self, tmp_path):
        from infinite_craft_cli.cli import _record_recipe, _load_recipes
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            _record_recipe("Mud", "Water", "Earth")
            recipes = _load_recipes()
        # Elements stored in alphabetical order
        assert recipes["Mud"][0] == ["Earth", "Water"]

    def test_multiple_recipes_for_same_result(self, tmp_path):
        from infinite_craft_cli.cli import _record_recipe, _load_recipes
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            _record_recipe("Steam", "Water", "Fire")
            _record_recipe("Steam", "Ice", "Lava")
            recipes = _load_recipes()
        assert len(recipes["Steam"]) == 2


class TestDoRecipe:
    def _setup_recipes(self, tmp_path):
        """Set up a recipe chain: Water + Fire = Steam, Steam + Earth = Mud"""
        path = tmp_path / "recipes.json"
        recipes = {
            "Steam": [["Fire", "Water"]],
            "Mud": [["Earth", "Steam"]],
        }
        path.write_text(json.dumps(recipes))
        return path

    def test_base_element(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        game = make_mock_game([MockElement("Water", "💧")])
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_recipe(game, "Water")
        assert "base element" in result

    def test_not_found(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        game = make_mock_game()
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_recipe(game, "Nonexistent")
        assert "not found" in result

    def test_no_recipe_known(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        game = make_mock_game([MockElement("Lava", "🌋")])
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_recipe(game, "Lava")
        assert "No recipe known" in result

    def test_single_step_recipe(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        game = make_mock_game([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Steam", "💨"),
        ])
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_recipe(game, "Steam")
        assert "1 steps" in result
        assert "Water" in result
        assert "Fire" in result

    def test_multi_step_recipe(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        game = make_mock_game([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Earth", "🌍"),
            MockElement("Steam", "💨"),
            MockElement("Mud", ""),
        ])
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_recipe(game, "Mud")
        assert "2 steps" in result

    def test_title_case_lookup(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        game = make_mock_game([MockElement("Steam", "💨")])
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_recipe(game, "steam")
        assert "1 steps" in result


class TestDoUnfilled:
    def test_all_filled(self, tmp_path):
        from infinite_craft_cli.cli import do_unfilled
        game = make_mock_game([
            MockElement("Water", "💧"),
            MockElement("Steam", "💨"),
        ])
        path = tmp_path / "recipes.json"
        path.write_text(json.dumps({"Steam": [["Fire", "Water"]]}))
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_unfilled(game)
        assert "All elements have recipes" in result

    def test_some_missing(self, tmp_path):
        from infinite_craft_cli.cli import do_unfilled
        game = make_mock_game([
            MockElement("Water", "💧"),
            MockElement("Steam", "💨"),
            MockElement("Lava", "🌋"),
        ])
        path = tmp_path / "recipes.json"
        path.write_text(json.dumps({"Steam": [["Fire", "Water"]]}))
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_unfilled(game)
        assert "1 elements without recipes" in result
        assert "Lava" in result

    def test_base_elements_excluded(self, tmp_path):
        from infinite_craft_cli.cli import do_unfilled
        game = make_mock_game()  # only base elements
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_unfilled(game)
        assert "All elements have recipes" in result
