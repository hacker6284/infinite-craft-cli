"""Tests for DiscoveryStorage."""

import json
import sys
import pytest

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.storage import DiscoveryStorage


class TestInitialization:
    def test_creates_file_with_starters(self, tmp_path):
        path = str(tmp_path / "discoveries.json")
        storage = DiscoveryStorage(path)
        elements = storage.get_all()
        names = [e.name for e in elements]
        assert "Water" in names
        assert "Fire" in names
        assert "Wind" in names
        assert "Earth" in names
        assert len(elements) == 4

    def test_file_written_to_disk(self, tmp_path):
        path = tmp_path / "discoveries.json"
        DiscoveryStorage(str(path))
        assert path.exists()
        data = json.loads(path.read_text())
        assert len(data) == 4

    def test_loads_existing_file(self, tmp_path):
        path = tmp_path / "discoveries.json"
        data = [
            {"name": "Water", "emoji": "💧", "is_first_discovery": False},
            {"name": "Steam", "emoji": "💨", "is_first_discovery": True},
        ]
        path.write_text(json.dumps(data))
        storage = DiscoveryStorage(str(path))
        assert len(storage.get_all()) == 2
        assert storage.get_by_name("Steam") is not None

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "deep" / "nested" / "discoveries.json")
        storage = DiscoveryStorage(path)
        assert len(storage.get_all()) == 4


class TestGetByName:
    def test_found(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        elem = storage.get_by_name("Water")
        assert elem is not None
        assert elem.name == "Water"
        assert elem.emoji == "💧"

    def test_not_found(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        assert storage.get_by_name("Nonexistent") is None


class TestAdd:
    def test_new_element(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        result = storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        assert result is not None
        assert result.name == "Steam"
        assert len(storage.get_all()) == 5

    def test_duplicate_returns_none(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        result = storage.add(name="Water", emoji="💧", is_first_discovery=False)
        assert result is None
        assert len(storage.get_all()) == 4

    def test_persists_to_disk(self, tmp_path):
        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Steam", emoji="💨", is_first_discovery=True)
        data = json.loads(path.read_text())
        assert any(d["name"] == "Steam" for d in data)

    def test_available_via_get_by_name(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        assert storage.get_by_name("Steam") is not None

    def test_default_optional_fields(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        result = storage.add(name="Mystery")
        assert result is not None
        assert result.emoji is None
        assert result.is_first_discovery is None


class TestReload:
    def test_picks_up_external_changes(self, tmp_path):
        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        assert len(storage.get_all()) == 4

        # Externally modify the file
        data = json.loads(path.read_text())
        data.append({"name": "External", "emoji": "🌀", "is_first_discovery": False})
        path.write_text(json.dumps(data))

        storage.reload()
        assert len(storage.get_all()) == 5
        assert storage.get_by_name("External") is not None


class TestConcurrency:
    def test_add_rereads_from_disk(self, tmp_path):
        """add() should pick up external changes before checking for duplicates."""
        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        assert len(storage.get_all()) == 4

        # Another process adds an element directly to the file
        data = json.loads(path.read_text())
        data.append({"name": "Steam", "emoji": "💨", "is_first_discovery": False})
        path.write_text(json.dumps(data))

        # Our storage doesn't know about Steam yet in memory,
        # but add() should re-read and detect the duplicate
        result = storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        assert result is None  # duplicate detected after re-read
        assert len(storage.get_all()) == 5  # has the externally-added Steam

    def test_add_preserves_external_additions(self, tmp_path):
        """add() should not overwrite elements added by other processes."""
        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))

        # Another process adds Steam
        data = json.loads(path.read_text())
        data.append({"name": "Steam", "emoji": "💨", "is_first_discovery": False})
        path.write_text(json.dumps(data))

        # We add Lava — should preserve Steam from disk
        storage.add(name="Lava", emoji="🌋", is_first_discovery=False)
        data = json.loads(path.read_text())
        names = [d["name"] for d in data]
        assert "Steam" in names
        assert "Lava" in names
        assert len(data) == 6  # 4 starters + Steam + Lava

    def test_lock_file_created(self, tmp_path):
        """A .lock file should be created alongside the data file."""
        path = tmp_path / "d.json"
        lock_path = tmp_path / "d.json.lock"
        DiscoveryStorage(str(path))
        # Lock file is created during _save/_load
        assert lock_path.exists()
