"""End-to-end regression tests using real DiscoveryStorage with mock HTTP."""

import asyncio
import gzip
import json
import os
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from infinite_craft_cli.element import Element
from infinite_craft_cli.storage import DiscoveryStorage

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def clear_caches():
    import infinite_craft_cli.cli as cli
    cli._pair_cache.clear()
    cli._history.clear()
    yield
    cli._pair_cache.clear()
    cli._history.clear()


def _make_mock_client_returning(results):
    """Create a mock client that returns elements from a list in order."""
    client = AsyncMock()
    client.pair = AsyncMock(side_effect=results)
    return client


class TestCombinePersistence:
    """Verify that combining elements persists the result to disk."""

    def test_combine_saves_result_to_disk(self, tmp_path):
        from infinite_craft_cli.cli import do_combine

        discoveries_path = str(tmp_path / "discoveries.json")
        storage = DiscoveryStorage(discoveries_path)
        assert storage.get_by_name("Steam") is None

        result_elem = Element(name="Steam", emoji="💨", is_first_discovery=False)
        client = AsyncMock()
        client.pair = AsyncMock(return_value=result_elem)

        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_combine(client, storage, "Water", "Fire"))

        # Verify in-memory
        assert storage.get_by_name("Steam") is not None
        assert storage.get_by_name("Steam").emoji == "💨"

        # Verify on disk — reload from scratch
        storage2 = DiscoveryStorage(discoveries_path)
        assert storage2.get_by_name("Steam") is not None
        assert storage2.get_by_name("Steam").emoji == "💨"

    def test_first_discovery_persists(self, tmp_path):
        from infinite_craft_cli.cli import do_combine

        discoveries_path = str(tmp_path / "discoveries.json")
        storage = DiscoveryStorage(discoveries_path)

        result_elem = Element(name="Unicorn", emoji="🦄", is_first_discovery=True)
        client = AsyncMock()
        client.pair = AsyncMock(return_value=result_elem)

        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_combine(client, storage, "Water", "Fire"))

        storage2 = DiscoveryStorage(discoveries_path)
        unicorn = storage2.get_by_name("Unicorn")
        assert unicorn is not None
        assert unicorn.is_first_discovery is True

    def test_nothing_result_not_persisted(self, tmp_path):
        from infinite_craft_cli.cli import do_combine

        discoveries_path = str(tmp_path / "discoveries.json")
        storage = DiscoveryStorage(discoveries_path)

        result_elem = Element(name=None)
        client = AsyncMock()
        client.pair = AsyncMock(return_value=result_elem)

        run_async(do_combine(client, storage, "Water", "Water"))

        # Only the 4 starters should exist
        storage2 = DiscoveryStorage(discoveries_path)
        assert len(storage2.get_all()) == 4


class TestBulkPersistence:
    """Verify bulk operations persist results to disk."""

    def test_combine_pairs_saves_all_results(self, tmp_path, capsys):
        from infinite_craft_cli.cli import _combine_pairs

        discoveries_path = str(tmp_path / "discoveries.json")
        storage = DiscoveryStorage(discoveries_path)

        results = [
            Element(name="Steam", emoji="💨", is_first_discovery=False),
            Element(name="Mud", emoji="", is_first_discovery=False),
        ]
        client = AsyncMock()
        client.pair = AsyncMock(side_effect=results)

        pairs = [
            (Element(name="Water", emoji="💧"), Element(name="Fire", emoji="🔥")),
            (Element(name="Water", emoji="💧"), Element(name="Earth", emoji="🌍")),
        ]

        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(_combine_pairs(client, storage, pairs))

        storage2 = DiscoveryStorage(discoveries_path)
        assert storage2.get_by_name("Steam") is not None
        assert storage2.get_by_name("Mud") is not None


class TestExportImportRoundTrip:
    """Verify export then import preserves all elements and recipes."""

    def test_round_trip(self, tmp_path):
        from infinite_craft_cli.cli import do_export, _import_from_save, _record_recipe

        discoveries_path = str(tmp_path / "discoveries.json")
        recipes_path = str(tmp_path / "recipes.json")
        export_path = str(tmp_path / "export.ic")

        # Set up storage with elements
        storage = DiscoveryStorage(discoveries_path)
        storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        storage.add(name="Mud", emoji="", is_first_discovery=False)

        # Record recipes
        with patch("infinite_craft_cli.cli.RECIPES_PATH", recipes_path):
            _record_recipe("Steam", "Water", "Fire")
            _record_recipe("Mud", "Water", "Earth")

            # Export
            do_export(storage, export_path)

        # Import into fresh storage
        discoveries_path2 = str(tmp_path / "discoveries2.json")
        storage2 = DiscoveryStorage(discoveries_path2)
        recipes_path2 = str(tmp_path / "recipes2.json")

        with patch("infinite_craft_cli.cli.RECIPES_PATH", recipes_path2):
            _import_from_save(storage2, export_path)

        # Verify all elements present
        storage2.reload()
        original_names = {e.name for e in storage.get_all()}
        imported_names = {e.name for e in storage2.get_all()}
        # Imported should have all elements that had recipes + base elements
        for name in ["Water", "Fire", "Earth", "Wind", "Steam", "Mud"]:
            assert name in imported_names, f"{name} missing after round-trip"

        # Verify recipes were imported
        with open(recipes_path2) as f:
            imported_recipes = json.load(f)
        assert "Steam" in imported_recipes
        assert "Mud" in imported_recipes


class TestRecipeIntegration:
    """Verify combining records recipes that do_recipe can trace."""

    def test_combine_then_recipe(self, tmp_path, capsys):
        from infinite_craft_cli.cli import do_combine, do_recipe

        discoveries_path = str(tmp_path / "discoveries.json")
        recipes_path = str(tmp_path / "recipes.json")
        storage = DiscoveryStorage(discoveries_path)

        result_elem = Element(name="Steam", emoji="💨", is_first_discovery=False)
        client = AsyncMock()
        client.pair = AsyncMock(return_value=result_elem)

        with patch("infinite_craft_cli.cli.RECIPES_PATH", recipes_path):
            run_async(do_combine(client, storage, "Water", "Fire"))

            # Now do_recipe should be able to trace Steam's lineage
            result = do_recipe(storage, "Steam")

        assert "Recipe for" in result
        assert "Water" in result
        assert "Fire" in result
        assert "Steam" in result


class TestInfinibrowserImportPersistence:
    """Verify importing from Infinibrowser persists all lineage elements."""

    def test_import_persists_to_disk(self, tmp_path):
        from infinite_craft_cli.cli import _import_from_infinibrowser

        discoveries_path = str(tmp_path / "discoveries.json")
        recipes_path = str(tmp_path / "recipes.json")
        storage = DiscoveryStorage(discoveries_path)

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

        with patch("infinite_craft_cli.cli._ib_fetch", side_effect=[item_data, lineage_data]):
            with patch("infinite_craft_cli.cli.RECIPES_PATH", recipes_path):
                _import_from_infinibrowser(storage, "Lava")

        # Reload from disk and verify
        storage2 = DiscoveryStorage(discoveries_path)
        assert storage2.get_by_name("Magma") is not None
        assert storage2.get_by_name("Magma").emoji == "🔴"
        assert storage2.get_by_name("Lava") is not None

        # Verify recipes persisted
        with open(recipes_path) as f:
            recipes = json.load(f)
        assert "Magma" in recipes
        assert "Lava" in recipes
