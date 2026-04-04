"""Shared test fixtures for infinite-craft-cli tests."""

import json
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock


class MockElement:
    """Lightweight mock for infinitecraft.Element."""

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


def make_mock_game(discoveries=None):
    """Create a mock InfiniteCraft game object."""
    if discoveries is None:
        discoveries = [
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
        ]

    game = MagicMock()
    game.discoveries = list(discoveries)
    game.get_discoveries.return_value = list(discoveries)

    def get_discovery(name):
        for e in discoveries:
            if e.name == name:
                return e
        return None

    game.get_discovery.side_effect = get_discovery
    game.pair = AsyncMock()
    game._update_discoveries = MagicMock()
    game._get_raw_discoveries.return_value = [
        {"name": e.name, "emoji": e.emoji, "is_first_discovery": e.is_first_discovery}
        for e in discoveries
    ]

    return game


@pytest.fixture
def mock_game():
    return make_mock_game()


@pytest.fixture
def mock_game_with_extras():
    """Game with more discoveries for testing search/match."""
    return make_mock_game([
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
