"""Tests for DiscoveryStorage."""

import json
import sys
import pytest
from unittest.mock import patch

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

    def test_corrupt_discoveries_raises_clear_error(self, tmp_path):
        path = tmp_path / "discoveries.json"
        path.write_text('[{"name": "Water"')  # truncated (repair heuristic cannot salvage)
        with pytest.raises(ValueError, match="discoveries file is corrupted"):
            DiscoveryStorage(str(path))


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


class TestRemove:
    def test_removes_element(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        assert storage.remove("Steam") is True
        assert storage.get_by_name("Steam") is None
        assert len(storage.get_all()) == 4

    def test_persists_removal(self, tmp_path):
        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        storage.remove("Steam")
        data = json.loads(path.read_text())
        assert not any(d["name"] == "Steam" for d in data)

    def test_missing_returns_false(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        assert storage.remove("Nope") is False


class TestAddBatch:
    def test_skips_duplicates(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        count = storage.add_batch([
            ("Steam", "💨", False),
            ("Steam", "💨", False),
            ("Mud", "🟤", False),
        ])
        assert count == 2
        assert len(storage.get_all()) == 6
        assert storage.get_by_name("Steam") is not None
        assert storage.get_by_name("Mud") is not None

    def test_single_disk_write(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        with patch.object(storage, "_save", wraps=storage._save) as mock_save:
            storage.add_batch([("Steam", "💨", False), ("Mud", "🟤", False)])
            mock_save.assert_called_once()

    def test_skips_existing_elements(self, tmp_path):
        storage = DiscoveryStorage(str(tmp_path / "d.json"))
        with patch.object(storage, "_save", wraps=storage._save) as mock_save:
            count = storage.add_batch([("Water", "💧", False), ("Steam", "💨", False)])
        assert count == 1
        mock_save.assert_called_once()


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
