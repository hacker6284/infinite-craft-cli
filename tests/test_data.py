"""Tests for infinite_craft_cli.data module."""

import os
import sys
import pytest

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_get_data_dir_returns_absolute_path():
    from infinite_craft_cli.data import get_data_dir
    result = get_data_dir()
    assert os.path.isabs(result)


def test_get_data_dir_contains_expected_name():
    from infinite_craft_cli.data import get_data_dir
    result = get_data_dir()
    assert ".infinite-craft-cli" in result


def test_get_data_dir_creates_directory():
    from infinite_craft_cli.data import get_data_dir
    result = get_data_dir()
    assert os.path.isdir(result)


def test_get_data_dir_is_idempotent():
    from infinite_craft_cli.data import get_data_dir
    assert get_data_dir() == get_data_dir()


def test_discoveries_path_ends_correctly():
    from infinite_craft_cli.data import DISCOVERIES_PATH
    assert DISCOVERIES_PATH.endswith("discoveries.json")


def test_recipes_path_ends_correctly():
    from infinite_craft_cli.data import RECIPES_PATH
    assert RECIPES_PATH.endswith("recipes.json")


def test_export_path_ends_correctly():
    from infinite_craft_cli.data import EXPORT_PATH
    assert EXPORT_PATH.endswith("export.ic")


def test_all_paths_share_data_dir():
    from infinite_craft_cli.data import DISCOVERIES_PATH, RECIPES_PATH, EXPORT_PATH, get_data_dir
    data_dir = get_data_dir()
    assert DISCOVERIES_PATH.startswith(data_dir)
    assert RECIPES_PATH.startswith(data_dir)
    assert EXPORT_PATH.startswith(data_dir)
