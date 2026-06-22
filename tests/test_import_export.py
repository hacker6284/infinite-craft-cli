"""Tests for import/export: do_import, _import_from_infinibrowser, _import_from_save, do_export."""

import gzip
import json
import sys
import pytest
from unittest.mock import patch, AsyncMock

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
        with patch(
            "infinite_craft_cli.cli._import_from_infinibrowser_async",
            new_callable=AsyncMock,
            return_value="ok",
        ) as mock_ib:
            result = do_import(storage, "Steam")
        mock_ib.assert_awaited_once_with(storage, "Steam")


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
            with patch("infinite_craft_cli.cli._record_recipes_batch") as mock_record:
                with patch("sys.stdout") as mock_stdout:
                    mock_stdout.isatty.return_value = False
                    result = _import_from_infinibrowser(storage, "Steam")
        assert "3" in result  # 3 elements imported
        mock_record.assert_called_once()
        storage.add_batch.assert_called_once()
        batch = storage.add_batch.call_args[0][0]
        names = {entry[0] for entry in batch}
        assert names == {"Water", "Fire", "Steam"}


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
        storage.add_batch.return_value = 3
        path = tmp_path / "save.ic"
        items = [
            {"id": 0, "text": "Water", "emoji": "💧", "recipes": []},
            {"id": 1, "text": "Fire", "emoji": "🔥", "recipes": []},
            {"id": 2, "text": "Steam", "emoji": "💨", "recipes": [[0, 1]]},
        ]
        self._write_save(path, items)
        with patch("infinite_craft_cli.cli._record_recipes_batch") as mock_record:
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = _import_from_save(storage, str(path))
        assert "3" in result  # 3 elements loaded
        storage.add_batch.assert_called_once()
        mock_record.assert_called_once_with([("Steam", "Water", "Fire")])

    def test_rejects_oversized_compressed_save(self, tmp_path):
        from infinite_craft_cli.cli import _import_from_save, _MAX_IC_COMPRESSED_BYTES

        storage = make_mock_storage()
        path = tmp_path / "big.ic"
        path.write_bytes(b"x" * (_MAX_IC_COMPRESSED_BYTES + 1))
        result = _import_from_save(storage, str(path))
        assert "too large" in result.lower()

    def test_rejects_oversized_decompressed_save(self, tmp_path):
        from infinite_craft_cli.cli import _import_from_save, _MAX_IC_DECOMPRESSED_BYTES

        storage = make_mock_storage()
        path = tmp_path / "big_decompressed.ic"
        big_text = "x" * (_MAX_IC_DECOMPRESSED_BYTES + 1)
        save = {
            "name": "Test",
            "version": "1.0",
            "created": 0,
            "updated": 0,
            "instances": [],
            "items": [{"id": 0, "text": big_text, "emoji": "", "recipes": []}],
        }
        with gzip.open(str(path), "wt", encoding="utf-8") as f:
            json.dump(save, f)
        result = _import_from_save(storage, str(path))
        assert "decompressed save too large" in result.lower()

    def test_rejects_too_many_items(self, tmp_path):
        from infinite_craft_cli.cli import _import_from_save, _MAX_IC_ITEMS

        storage = make_mock_storage()
        path = tmp_path / "many.ic"
        items = [{"id": i, "text": f"Elem{i}", "emoji": ""} for i in range(_MAX_IC_ITEMS + 1)]
        self._write_save(path, items)
        result = _import_from_save(storage, str(path))
        assert "too many items" in result.lower()

    def test_sanitizes_control_chars_in_element_names(self, tmp_path):
        from infinite_craft_cli.cli import _import_from_save

        storage = make_mock_storage()
        storage.add_batch.return_value = 1
        path = tmp_path / "dirty.ic"
        dirty = "Evil\x1b[31mName"
        items = [{"id": 0, "text": dirty, "emoji": "💀", "recipes": []}]
        self._write_save(path, items)
        with patch("infinite_craft_cli.cli._record_recipes_batch"):
            _import_from_save(storage, str(path))
        batch = storage.add_batch.call_args[0][0]
        assert "\x1b" not in batch[0][0]
        assert "Evil" in batch[0][0]

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

    def test_includes_referenced_terminals_for_filled_recipes(self, tmp_path):
        """Elements referenced by recipes but lacking their own recipes (terminals
        from /fill or /import) must be included so the using recipes can be emitted.
        """
        from infinite_craft_cli.cli import do_export
        storage = make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Earth", "🌍"),
            MockElement("Wind", "🌬️"),
            MockElement("Mystery", "❓"),  # terminal, no recipe
            MockElement("X", "✨"),
        ])
        path = tmp_path / "export.ic"
        recipes = {"X": [["Mystery", "Water"]]}
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "recipes.json")):
            (tmp_path / "recipes.json").write_text(json.dumps(recipes))
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = do_export(storage, str(path))
        # In this setup there are no pure orphans (Mystery is referenced by a
        # recipe), so the export should not mention any excluded elements.
        assert "excluded" not in result.lower()
        with gzip.open(str(path), "rt") as f:
            save = json.load(f)
        names = {i["text"] for i in save["items"]}
        assert "X" in names
        assert "Mystery" in names
        x_item = [i for i in save["items"] if i["text"] == "X"][0]
        assert "recipes" in x_item
        assert len(x_item["recipes"]) == 1
        # ids must be integers and resolve to the right names via the items list
        id_to_name = {i["id"]: i["text"] for i in save["items"]}
        for pair in x_item["recipes"]:
            assert all(isinstance(pid, int) for pid in pair)
            assert id_to_name[pair[0]] in ("Mystery", "Water")
            assert id_to_name[pair[1]] in ("Mystery", "Water")
