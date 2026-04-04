"""User data directory management for Infinite Craft CLI."""

import os


def get_data_dir() -> str:
    """Return the path to ~/.infinite-craft-cli/, creating it if needed."""
    data_dir = os.path.join(os.path.expanduser("~"), ".infinite-craft-cli")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


_DATA_DIR = get_data_dir()

DISCOVERIES_PATH = os.path.join(_DATA_DIR, "discoveries.json")
RECIPES_PATH = os.path.join(_DATA_DIR, "recipes.json")
EXPORT_PATH = os.path.join(_DATA_DIR, "export.ic")
