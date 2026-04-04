"""Tests for bulk operations: do_crawl, do_exhaust, do_permute, do_cross, _combine_pairs."""

import asyncio
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from tests.conftest import MockElement, make_mock_game

# Run with pytest when invoked directly by Bazel
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


class TestCombinePairs:
    def test_empty_pairs(self, capsys):
        from infinite_craft_cli.cli import _combine_pairs
        game = make_mock_game()
        run_async(_combine_pairs(game, []))
        captured = capsys.readouterr()
        assert "Done" in captured.out
        assert "0 new" in captured.out

    def test_successful_pairs(self, capsys):
        from infinite_craft_cli.cli import _combine_pairs
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(_combine_pairs(game, [(a, b)]))
        captured = capsys.readouterr()
        assert "Steam" in captured.out

    def test_error_handling(self, capsys):
        from infinite_craft_cli.cli import _combine_pairs
        game = make_mock_game()
        game.pair.side_effect = Exception("timeout")
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        run_async(_combine_pairs(game, [(a, b)]))
        captured = capsys.readouterr()
        assert "Error" in captured.out

    def test_nothing_result_counted(self, capsys):
        from infinite_craft_cli.cli import _combine_pairs
        game = make_mock_game()
        nothing = MagicMock()
        nothing.name = None
        game.pair.return_value = nothing
        a = MockElement("Water", "💧")
        b = MockElement("Water", "💧")
        run_async(_combine_pairs(game, [(a, b)]))
        captured = capsys.readouterr()
        assert "1 nothing" in captured.out


class TestConfirmAndRunPairs:
    def test_below_threshold_no_prompt(self, capsys):
        from infinite_craft_cli.cli import _confirm_and_run_pairs
        game = make_mock_game()
        nothing = MagicMock()
        nothing.name = None
        game.pair.return_value = nothing
        pairs = [(MockElement("A"), MockElement("B"))]
        run_async(_confirm_and_run_pairs(game, pairs))
        captured = capsys.readouterr()
        assert "Warning" not in captured.out

    def test_above_threshold_prompts(self, capsys):
        from infinite_craft_cli.cli import _confirm_and_run_pairs, _BULK_WARN_THRESHOLD
        game = make_mock_game()
        pairs = [(MockElement(f"A{i}"), MockElement(f"B{i}"))
                 for i in range(_BULK_WARN_THRESHOLD + 1)]
        with patch("builtins.input", return_value="n"):
            run_async(_confirm_and_run_pairs(game, pairs))
        captured = capsys.readouterr()
        assert "Warning" in captured.out
        assert "Cancelled" in captured.out

    def test_user_confirms(self, capsys):
        from infinite_craft_cli.cli import _confirm_and_run_pairs, _BULK_WARN_THRESHOLD
        game = make_mock_game()
        nothing = MagicMock()
        nothing.name = None
        game.pair.return_value = nothing
        pairs = [(MockElement(f"A{i}"), MockElement(f"B{i}"))
                 for i in range(_BULK_WARN_THRESHOLD + 1)]
        with patch("builtins.input", return_value="y"):
            run_async(_confirm_and_run_pairs(game, pairs))
        captured = capsys.readouterr()
        assert "Done" in captured.out

    def test_eof_cancels(self, capsys):
        from infinite_craft_cli.cli import _confirm_and_run_pairs, _BULK_WARN_THRESHOLD
        game = make_mock_game()
        pairs = [(MockElement(f"A{i}"), MockElement(f"B{i}"))
                 for i in range(_BULK_WARN_THRESHOLD + 1)]
        with patch("builtins.input", side_effect=EOFError):
            run_async(_confirm_and_run_pairs(game, pairs))
        captured = capsys.readouterr()
        assert "Cancelled" in captured.out


class TestDoPermute:
    def test_no_matches(self, capsys):
        from infinite_craft_cli.cli import do_permute
        game = make_mock_game()
        run_async(do_permute(game, "zzz"))
        captured = capsys.readouterr()
        assert "No elements match" in captured.out

    def test_single_match(self, capsys):
        from infinite_craft_cli.cli import do_permute
        game = make_mock_game([MockElement("Water", "💧")])
        run_async(do_permute(game, "water"))
        captured = capsys.readouterr()
        assert "Need at least two" in captured.out

    def test_generates_correct_pairs(self, capsys):
        from infinite_craft_cli.cli import do_permute
        game = make_mock_game([
            MockElement("Water", "💧"),
            MockElement("Wind", "🌬️"),
            MockElement("Wave", "🌊"),
        ])
        nothing = MagicMock()
        nothing.name = None
        game.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_permute(game, "w*"))
        captured = capsys.readouterr()
        assert "3 elements match" in captured.out
        assert "3 unique pairs" in captured.out


class TestDoCross:
    def test_left_no_matches(self, capsys):
        from infinite_craft_cli.cli import do_cross
        game = make_mock_game()
        run_async(do_cross(game, "zzz", "water"))
        captured = capsys.readouterr()
        assert "No elements match: zzz" in captured.out

    def test_right_no_matches(self, capsys):
        from infinite_craft_cli.cli import do_cross
        game = make_mock_game()
        run_async(do_cross(game, "water", "zzz"))
        captured = capsys.readouterr()
        assert "No elements match: zzz" in captured.out

    def test_overlap_excluded(self, capsys):
        from infinite_craft_cli.cli import do_cross
        # Both queries match the same single element
        game = make_mock_game([MockElement("Water", "💧")])
        run_async(do_cross(game, "water", "water"))
        captured = capsys.readouterr()
        assert "No valid pairs" in captured.out

    def test_generates_cross_product(self, capsys):
        from infinite_craft_cli.cli import do_cross
        game = make_mock_game([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Earth", "🌍"),
        ])
        nothing = MagicMock()
        nothing.name = None
        game.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_cross(game, "water", "fire"))
        captured = capsys.readouterr()
        assert "1 unique pairs" in captured.out


class TestDoExhaust:
    def test_combines_with_all(self, capsys):
        from infinite_craft_cli.cli import do_exhaust
        game = make_mock_game()  # 4 base elements
        nothing = MagicMock()
        nothing.name = None
        game.pair.return_value = nothing
        run_async(do_exhaust(game, "Water"))
        captured = capsys.readouterr()
        assert "3 elements" in captured.out  # 4 total minus self

    def test_skips_self(self, capsys):
        from infinite_craft_cli.cli import do_exhaust
        game = make_mock_game([MockElement("Water", "💧")])
        nothing = MagicMock()
        nothing.name = None
        game.pair.return_value = nothing
        run_async(do_exhaust(game, "Water"))
        captured = capsys.readouterr()
        assert "0 elements" in captured.out


class TestDoCrawl:
    def test_no_new_discoveries_stops(self, capsys):
        from infinite_craft_cli.cli import do_crawl
        game = make_mock_game()
        nothing = MagicMock()
        nothing.name = None
        game.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_crawl(game, "Water", "Fire"))
        captured = capsys.readouterr()
        assert "No new discoveries" in captured.out
        assert "Final pool" in captured.out

    def test_grows_pool(self, capsys):
        from infinite_craft_cli.cli import do_crawl
        game = make_mock_game()
        call_count = 0

        async def mock_pair(a, b):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockElement("Steam", "💨")
            nothing = MagicMock()
            nothing.name = None
            return nothing

        game.pair = mock_pair
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_crawl(game, "Water", "Fire"))
        captured = capsys.readouterr()
        assert "Steam" in captured.out
