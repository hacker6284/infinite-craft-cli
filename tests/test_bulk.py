"""Tests for bulk operations: do_crawl, do_exhaust, do_permute, do_cross, _combine_pairs."""

import asyncio
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from tests.conftest import MockElement, make_mock_storage, make_mock_client

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro, *, timeout: float = 8.0):
    # unified to asyncio.run + wait_for (matches ux/cli; avoids deprecation on py>=3.10)
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


@pytest.fixture(autouse=True)
def clear_caches(request):
    import infinite_craft_cli.cli as cli

    def _clear():
        try:
            cli._reset_test_state()
        except Exception:
            pass
        # (pair_cache and history cleared by _reset_test_state; no extra direct)

    _clear()
    request.addfinalizer(_clear)
    yield
    _clear()


class TestConfirmAndRunPairs:
    def test_non_tty_skips_confirmation(self, capsys):
        from infinite_craft_cli.cli import _confirm_and_run_pairs, _BULK_WARN_THRESHOLD

        client = make_mock_client()
        storage = make_mock_storage()
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        pairs = [
            (MockElement(f"A{i}"), MockElement(f"B{i}"))
            for i in range(_BULK_WARN_THRESHOLD + 1)
        ]
        with patch("sys.stdin.isatty", return_value=False):
            run_async(_confirm_and_run_pairs(client, storage, pairs))
        captured = capsys.readouterr()
        assert "pairs" in captured.out
        assert "Done" in captured.out
        assert "Cancelled" not in captured.out


class TestDoPermutate:
    def test_permutate_stops_on_natural_convergence(self, capsys):
        # v2.0: no round cap — permutate stops when a round adds nothing new
        # (the pair cache makes round 2 a no-op here).
        from infinite_craft_cli.cli import do_permutate

        client = make_mock_client()
        discoveries = [
            MockElement("Water", "💧"),
            MockElement("Wind", "🌬️"),
        ]
        storage = make_mock_storage(list(discoveries))

        async def mock_pair(a, b):
            return MockElement("Steam", "💨")

        client.pair = mock_pair

        def add_side_effect(**kwargs):
            name = kwargs.get("name")
            if name and not any(e.name == name for e in discoveries):
                discoveries.append(MockElement(name, kwargs.get("emoji", "")))
            storage.get_all.return_value = list(discoveries)
            return None

        storage.add.side_effect = add_side_effect
        with patch("infinite_craft_cli.cli._record_recipes_batch"):
            run_async(do_permutate(client, storage, "w*"))
        captured = capsys.readouterr()
        assert "Reached max rounds" not in captured.out
        assert "Permutate done after" in captured.out


class TestDoExhaust:
    def test_combines_with_all(self, capsys):
        from infinite_craft_cli.cli import do_exhaust

        client = make_mock_client()
        storage = make_mock_storage()
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        run_async(do_exhaust(client, storage, "Water"))
        captured = capsys.readouterr()
        assert "3 pairs" in captured.out


class TestDoWith:
    def test_regex_query(self, capsys):
        from infinite_craft_cli.cli import do_with

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Fire", "🔥"),
                MockElement("Firewall", "🧱"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        run_async(do_with(client, storage, "Water", "/^fi/"))
        captured = capsys.readouterr()
        assert "pairs" in captured.out.lower() or "Combining" in captured.out


class TestDoCross:
    def test_generates_cross_product(self, capsys):
        from infinite_craft_cli.cli import do_cross

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Fire", "🔥"),
                MockElement("Wind", "🌬️"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        run_async(do_cross(client, storage, "/^fi/", "/^wa/"))
        captured = capsys.readouterr()
        assert "unique pair" in captured.out or "pair" in captured.out.lower()


class TestDoPermute:
    def test_generates_correct_pairs(self, capsys):
        from infinite_craft_cli.cli import do_permute

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Wind", "🌬️"),
                MockElement("Fire", "🔥"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        run_async(do_permute(client, storage, "w*"))
        captured = capsys.readouterr()
        assert "pair" in captured.out.lower()


class TestDoCrawl:
    def test_no_new_discoveries_stops(self, capsys):
        from infinite_craft_cli.cli import do_crawl

        client = make_mock_client()
        storage = make_mock_storage()
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipes_batch"):
            run_async(do_crawl(client, storage, "Water", "Fire"))
        captured = capsys.readouterr()
        assert "No new discoveries" in captured.out
        assert "Final pool" in captured.out

    def test_grows_pool(self, capsys):
        from infinite_craft_cli.cli import do_crawl

        client = make_mock_client()
        storage = make_mock_storage()
        call_count = 0

        async def mock_pair(a, b):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockElement("Steam", "💨")
            nothing = MagicMock()
            nothing.name = None
            return nothing

        client.pair = mock_pair
        with patch("infinite_craft_cli.cli._record_recipes_batch"):
            run_async(do_crawl(client, storage, "Water", "Fire"))
        captured = capsys.readouterr()
        assert "Steam" in captured.out
