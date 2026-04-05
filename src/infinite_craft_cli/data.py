"""User data directory management for Infinite Craft CLI."""

import os


def get_data_dir() -> str:
    """Return the data directory path, creating it if needed.

    Respects INFINITE_CRAFT_DATA env var, otherwise defaults to ~/.infinite-craft-cli/.
    """
    override = os.environ.get("INFINITE_CRAFT_DATA")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    data_dir = os.path.join(os.path.expanduser("~"), ".infinite-craft-cli")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


_DATA_DIR = get_data_dir()

DISCOVERIES_PATH = os.path.join(_DATA_DIR, "discoveries.json")
RECIPES_PATH = os.path.join(_DATA_DIR, "recipes.json")
EXPORT_PATH = os.path.join(_DATA_DIR, "export.ic")
