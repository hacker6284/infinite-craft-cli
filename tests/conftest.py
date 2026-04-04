"""Shared test fixtures for infinite-craft-cli tests."""

import json
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock


class MockElement:
    """Lightweight mock for Element."""

    def __init__(self, name, emoji="", is_first_discovery=False):
        self.name = name
        self.emoji = emoji
        self.is_first_discovery = is_first_discovery

    def __str__(self):
        if self.emoji:
            return f"{self.emoji} {self.name}"
        return self.name

    def __eq__(self, other):
        return isinstance(other, MockElement) and self.name == other.name

    def __hash__(self):
        return hash(self.name)


def make_mock_storage(discoveries=None):
    """Create a mock DiscoveryStorage object."""
    if discoveries is None:
        discoveries = [
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
        ]

    storage = MagicMock()
    storage.get_all.return_value = list(discoveries)

    def get_by_name(name):
        for e in discoveries:
            if e.name == name:
                return e
        return None

    storage.get_by_name.side_effect = get_by_name
    storage.add.return_value = None
    storage.reload.return_value = None

    return storage


def make_mock_client():
    """Create a mock InfiniteCraftClient object."""
    client = AsyncMock()
    client.pair = AsyncMock()
    return client


# Keep for backwards compat
def make_mock_game(discoveries=None):
    """Deprecated: use make_mock_storage() and make_mock_client() instead."""
    return make_mock_storage(discoveries)


@pytest.fixture
def mock_storage():
    return make_mock_storage()


@pytest.fixture
def mock_client():
    return make_mock_client()


@pytest.fixture
def mock_storage_with_extras():
    """Storage with more discoveries for testing search/match."""
    return make_mock_storage([
        MockElement("Water", "💧"),
        MockElement("Fire", "🔥"),
        MockElement("Wind", "🌬️"),
        MockElement("Earth", "🌍"),
        MockElement("Steam", "💨"),
        MockElement("Lava", "🌋"),
        MockElement("Mud", ""),
        MockElement("Dust", ""),
        MockElement("Waterfall", "🏞️", is_first_discovery=True),
        MockElement("Firewall", "🧱", is_first_discovery=True),
    ])


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory and patch data.py paths."""
    return tmp_path


@pytest.fixture
def recipes_file(tmp_path):
    """Create a temporary recipes file."""
    path = tmp_path / "recipes.json"
    return path


@pytest.fixture
def discoveries_file(tmp_path):
    """Create a temporary discoveries file."""
    path = tmp_path / "discoveries.json"
    return path
