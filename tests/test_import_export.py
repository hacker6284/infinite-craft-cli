"""Tests for import/export: do_import, _import_from_infinibrowser, _import_from_save, do_export."""

import gzip
import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import MockElement, make_mock_storage

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestDoImport:
    def test_ic_extension_routes_to_save(self):
        from infinite_craft_cli.cli import do_import
        storage = make_mock_storage()
        with patch("infinite_craft_cli.cli._import_from_save", return_value="ok") as mock_save:
            result = do_import(storage, "file.ic")
        mock_save.assert_called_once_with(storage, "file.ic")

    def test_path_separator_routes_to_save(self):
        from infinite_craft_cli.cli import do_import
        storage = make_mock_storage()
        with patch("infinite_craft_cli.cli._import_from_save", return_value="ok") as mock_save:
            result = do_import(storage, "/path/to/something")
        mock_save.assert_called_once()

    def test_plain_name_routes_to_infinibrowser(self):
        from infinite_craft_cli.cli import do_import
        storage = make_mock_storage()
        with patch("infinite_craft_cli.cli._import_from_infinibrowser", return_value="ok") as mock_ib:
            result = do_import(storage, "Steam")
        mock_ib.assert_called_once_with(storage, "Steam")


class TestImportFromInfinibrowser:
    def test_not_found(self, capsys):
        from infinite_craft_cli.cli import _import_from_infinibrowser
        storage = make_mock_storage()
        with patch("infinite_craft_cli.cli._ib_fetch", return_value={"code": 404}):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = _import_from_infinibrowser(storage, "Nonexistent")
        assert "Not found" in result

    def test_fetch_failure(self):
        from infinite_craft_cli.cli import _import_from_infinibrowser
        storage = make_mock_storage()
        with patch("infinite_craft_cli.cli._ib_fetch", return_value=None):
            result = _import_from_infinibrowser(storage, "Water")
        assert result == ""

    def test_no_lineage(self, capsys):
        from infinite_craft_cli.cli import _import_from_infinibrowser
        storage = make_mock_storage()
        item_data = {"text": "Water", "emoji": "💧", "depth": 0}
        lineage_data = {"steps": []}
        with patch("infinite_craft_cli.cli._ib_fetch", side_effect=[item_data, lineage_data]):
            result = _import_from_infinibrowser(storage, "Water")
        assert "No lineage" in result

    def test_successful_import(self, capsys):
        from infinite_craft_cli.cli import _import_from_infinibrowser
        storage = make_mock_storage()
        item_data = {"text": "Steam", "emoji": "💨", "depth": 1}
        lineage_data = {"steps": [{
            "a": {"id": "Water", "emoji": "💧"},
            "b": {"id": "Fire", "emoji": "🔥"},
            "result": {"id": "Steam", "emoji": "💨"},
        }]}
        with patch("infinite_craft_cli.cli._ib_fetch", side_effect=[item_data, lineage_data]):
            with patch("infinite_craft_cli.cli._record_recipe") as mock_record:
                with patch("sys.stdout") as mock_stdout:
                    mock_stdout.isatty.return_value = False
                    result = _import_from_infinibrowser(storage, "Steam")
        assert "3" in result  # 3 elements imported
        mock_record.assert_called_once()
        # All 3 elements should be added to storage
        assert storage.add.call_count == 3


class TestImportFromSave:
    def _write_save(self, path, items):
        save = {
            "name": "Test",
            "version": "1.0",
            "created": 0,
            "updated": 0,
            "instances": [],
            "items": items,
        }
        with gzip.open(str(path), "wt", encoding="utf-8") as f:
            json.dump(save, f)

    def test_valid_save(self, tmp_path):
        from infinite_craft_cli.cli import _import_from_save
        storage = make_mock_storage()
        storage.add.return_value = MockElement("Water", "💧")
        path = tmp_path / "save.ic"
        items = [
            {"id": 0, "text": "Water", "emoji": "💧", "recipes": []},
            {"id": 1, "text": "Fire", "emoji": "🔥", "recipes": []},
            {"id": 2, "text": "Steam", "emoji": "💨", "recipes": [[0, 1]]},
        ]
        self._write_save(path, items)
        with patch("infinite_craft_cli.cli._record_recipe") as mock_record:
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = _import_from_save(storage, str(path))
        assert "3" in result  # 3 elements loaded
        mock_record.assert_called_once_with("Steam", "Water", "Fire")

    def test_empty_items(self, tmp_path):
        from infinite_craft_cli.cli import _import_from_save
        storage = make_mock_storage()
        path = tmp_path / "empty.ic"
        self._write_save(path, [])
        result = _import_from_save(storage, str(path))
        assert "No items" in result

    def test_malformed_file(self, tmp_path):
        from infinite_craft_cli.cli import _import_from_save
        storage = make_mock_storage()
        path = tmp_path / "bad.ic"
        path.write_bytes(b"not gzip data")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = _import_from_save(storage, str(path))
        assert "Error" in result


class TestDoExport:
    def test_exports_base_elements(self, tmp_path):
        from infinite_craft_cli.cli import do_export
        storage = make_mock_storage()
        path = tmp_path / "export.ic"
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "recipes.json")):
            (tmp_path / "recipes.json").write_text("{}")
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_export(storage, str(path))
        assert "4" in result  # 4 base elements
        assert path.exists()

    def test_excludes_elements_without_recipes(self, tmp_path):
        from infinite_craft_cli.cli import do_export
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Earth", "🌍"),
            MockElement("Wind", "🌬️"),
            MockElement("Steam", "💨"),  # no recipe
        ])
        path = tmp_path / "export.ic"
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "recipes.json")):
            (tmp_path / "recipes.json").write_text("{}")
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_export(storage, str(path))
        assert "excluded" in result

    def test_output_format(self, tmp_path):
        from infinite_craft_cli.cli import do_export
        storage = make_mock_storage()
        path = tmp_path / "export.ic"
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "recipes.json")):
            (tmp_path / "recipes.json").write_text("{}")
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                do_export(storage, str(path))
        with gzip.open(str(path), "rt") as f:
            save = json.load(f)
        assert "items" in save
        assert "version" in save
        assert "created" in save
        assert len(save["items"]) == 4

    def test_recipes_use_local_ids(self, tmp_path):
        from infinite_craft_cli.cli import do_export
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Earth", "🌍"),
            MockElement("Wind", "🌬️"),
            MockElement("Steam", "💨"),
        ])
        path = tmp_path / "export.ic"
        recipes = {"Steam": [["Fire", "Water"]]}
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "recipes.json")):
            (tmp_path / "recipes.json").write_text(json.dumps(recipes))
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                do_export(storage, str(path))
        with gzip.open(str(path), "rt") as f:
            save = json.load(f)
        steam_item = [i for i in save["items"] if i["text"] == "Steam"][0]
        assert "recipes" in steam_item
        # Recipes should reference local integer IDs, not names
        for pair in steam_item["recipes"]:
            assert all(isinstance(x, int) for x in pair)
