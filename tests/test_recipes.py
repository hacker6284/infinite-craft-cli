"""Tests for recipe functions: _load_recipes, _save_recipes, _record_recipe, do_recipe, do_unfilled."""

import json
import os
import sys
import pytest
from unittest.mock import patch, mock_open

from tests.conftest import MockElement, make_mock_storage

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
        storage = make_mock_storage([MockElement("Water", "💧")])
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_recipe(storage, "Water")
        assert "base element" in result

    def test_not_found(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        storage = make_mock_storage()
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_recipe(storage, "Nonexistent")
        assert "not found" in result

    def test_no_recipe_known(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        storage = make_mock_storage([MockElement("Lava", "🌋")])
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_recipe(storage, "Lava")
        assert "No recipe known" in result

    def test_single_step_recipe(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Steam", "💨"),
        ])
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_recipe(storage, "Steam")
        assert "1 steps" in result
        assert "Water" in result
        assert "Fire" in result

    def test_multi_step_recipe(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        storage = make_mock_storage([
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
                result = do_recipe(storage, "Mud")
        assert "2 steps" in result

    def test_title_case_lookup(self, tmp_path):
        from infinite_craft_cli.cli import do_recipe
        storage = make_mock_storage([MockElement("Steam", "💨")])
        path = self._setup_recipes(tmp_path)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_recipe(storage, "steam")
        assert "1 steps" in result


class TestDoUnfilled:
    def test_all_filled(self, tmp_path):
        from infinite_craft_cli.cli import do_unfilled
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Steam", "💨"),
        ])
        path = tmp_path / "recipes.json"
        path.write_text(json.dumps({"Steam": [["Fire", "Water"]]}))
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_unfilled(storage)
        assert "All elements have recipes" in result

    def test_some_missing(self, tmp_path):
        from infinite_craft_cli.cli import do_unfilled
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Steam", "💨"),
            MockElement("Lava", "🌋"),
        ])
        path = tmp_path / "recipes.json"
        path.write_text(json.dumps({"Steam": [["Fire", "Water"]]}))
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_unfilled(storage)
        assert "1 elements without recipes" in result
        assert "Lava" in result

    def test_base_elements_excluded(self, tmp_path):
        from infinite_craft_cli.cli import do_unfilled
        storage = make_mock_storage()  # only base elements
        path = tmp_path / "recipes.json"
        path.write_text("{}")
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            result = do_unfilled(storage)
        assert "All elements have recipes" in result


class TestDoRecipeWithTerminals:
    """Regression tests for terminals (constituents with no recipe of their own)
    introduced by /fill or /import. The BFS must still trace using them as roots.
    """

    def test_recipe_with_terminal_constituent(self, tmp_path):
        """Target recipe uses a non-base, non-reciped 'Mystery' element."""
        from infinite_craft_cli.cli import do_recipe
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Mystery", "❓"),
            MockElement("X", "✨"),
        ])
        path = tmp_path / "recipes.json"
        # X is made from Mystery (terminal) + Water (base). No recipe for Mystery.
        recipes = {"X": [["Mystery", "Water"]]}
        path.write_text(json.dumps(recipes))
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_recipe(storage, "X")
        assert "Recipe for" in result
        assert "1 steps" in result
        assert "Mystery" in result
        assert "Water" in result
        assert "X" in result
        assert "Cannot trace" not in result

    def test_chain_with_terminal_at_bottom(self, tmp_path):
        """M = Mystery (terminal) + Fire; Target = M + Earth."""
        from infinite_craft_cli.cli import do_recipe
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Mystery", ""),
            MockElement("M", ""),
            MockElement("Target", ""),
        ])
        path = tmp_path / "recipes.json"
        recipes = {
            "M": [["Mystery", "Fire"]],
            "Target": [["M", "Earth"]],
        }
        path.write_text(json.dumps(recipes))
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_recipe(storage, "Target")
        assert "Recipe for" in result
        assert "2 steps" in result
        assert "Mystery" in result
        assert "Target" in result
        assert "Cannot trace" not in result

    def test_unresolvable_middle_still_fails(self, tmp_path):
        """A middle element that has a (non-empty) recipe entry but whose own
        inputs are unresolvable (not bases and not terminals) should still
        cause 'Cannot trace' for anything that depends on it.

        This documents the boundary of the terminal relaxation: only names
        that are absent from recipes (or have empty lists) are treated as
        extra roots.
        """
        from infinite_craft_cli.cli import do_recipe
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Earth", "🌍"),
            MockElement("Wind", "🌬️"),
            MockElement("M", ""),
            MockElement("Target", ""),
        ])
        path = tmp_path / "recipes.json"
        # M depends on Broken; Broken has a recipe entry but it is a self-ref
        # (has key + non-empty list, so *not* a terminal). Thus M (and Target)
        # cannot be reached from bases + terminals.
        recipes = {
            "M": [["Broken", "Water"]],
            "Broken": [["Broken", "Fire"]],
            "Target": [["M", "Earth"]],
        }
        path.write_text(json.dumps(recipes))
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(path)):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_recipe(storage, "Target")
            assert "Cannot trace full lineage" in result
            # Also verify for the middle itself (still inside the recipes patch)
            result_m = do_recipe(storage, "M")
            assert "Cannot trace full lineage" in result_m
