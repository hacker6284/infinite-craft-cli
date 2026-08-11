"""Tests for recipe functions: do_recipe terminals."""

import json
import sys
import pytest
from unittest.mock import patch

from tests.conftest import MockElement, make_mock_storage

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


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
