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


class TestCombinePairs:
    # LEGACY internal direct calls to _combine etc (keep per review rules; high-level UX use harness)
    def test_empty_pairs(self, capsys):
        from infinite_craft_cli.cli import _combine_pairs

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(_combine_pairs(client, storage, []))
        captured = capsys.readouterr()
        assert "Done" in captured.out
        assert "0 new" in captured.out

    def test_successful_pairs(self, capsys):
        from infinite_craft_cli.cli import _combine_pairs

        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(_combine_pairs(client, storage, [(a, b)]))
        captured = capsys.readouterr()
        assert "Steam" in captured.out
        storage.add.assert_any_call(name="Steam", emoji="💨", is_first_discovery=False)

    def test_error_handling(self, capsys):
        from infinite_craft_cli.cli import _combine_pairs

        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.side_effect = Exception("timeout")
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        run_async(_combine_pairs(client, storage, [(a, b)]))
        captured = capsys.readouterr()
        assert "Error" in captured.out

    def test_nothing_result_counted(self, capsys):
        from infinite_craft_cli.cli import _combine_pairs

        client = make_mock_client()
        storage = make_mock_storage()
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        a = MockElement("Water", "💧")
        b = MockElement("Water", "💧")
        run_async(_combine_pairs(client, storage, [(a, b)]))
        captured = capsys.readouterr()
        assert "1 nothing" in captured.out


class TestAwaitConfirmation:
    def test_noninteractive_falls_back_to_prompt(self):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import _await_confirmation

        async def run():
            cli._interactive_mode_active = False
            with patch(
                "infinite_craft_cli.cli._prompt_input", new_callable=AsyncMock
            ) as mock_prompt:
                mock_prompt.return_value = "y"
                result = await _await_confirmation("  Continue? [y/N] ")
            return result, mock_prompt

        result, mock_prompt = run_async(run())
        assert result == "y"
        mock_prompt.assert_awaited_once_with("  Continue? [y/N] ")


class TestConfirmAndRunPairs:
    def test_below_threshold_no_prompt(self, capsys):
        from infinite_craft_cli.cli import _confirm_and_run_pairs

        client = make_mock_client()
        storage = make_mock_storage()
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        pairs = [(MockElement("A"), MockElement("B"))]
        run_async(_confirm_and_run_pairs(client, storage, pairs))
        captured = capsys.readouterr()
        assert "Warning" not in captured.out

    def test_above_threshold_prompts(self, capsys):
        from infinite_craft_cli.cli import _confirm_and_run_pairs, _BULK_WARN_THRESHOLD

        client = make_mock_client()
        storage = make_mock_storage()
        pairs = [
            (MockElement(f"A{i}"), MockElement(f"B{i}"))
            for i in range(_BULK_WARN_THRESHOLD + 1)
        ]
        with patch("sys.stdin.isatty", return_value=True):
            with patch("infinite_craft_cli.cli._await_confirmation", return_value="n"):
                run_async(_confirm_and_run_pairs(client, storage, pairs))
        captured = capsys.readouterr()
        assert "pairs" in captured.out
        assert "press y" in captured.out
        assert "Cancelled" in captured.out

    def test_user_confirms(self, capsys):
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
        with patch("sys.stdin.isatty", return_value=True):
            with patch("infinite_craft_cli.cli._await_confirmation", return_value="y"):
                run_async(_confirm_and_run_pairs(client, storage, pairs))
        captured = capsys.readouterr()
        assert "Done" in captured.out

    def test_eof_cancels(self, capsys):
        from infinite_craft_cli.cli import _confirm_and_run_pairs, _BULK_WARN_THRESHOLD

        client = make_mock_client()
        storage = make_mock_storage()
        pairs = [
            (MockElement(f"A{i}"), MockElement(f"B{i}"))
            for i in range(_BULK_WARN_THRESHOLD + 1)
        ]

        async def raise_eof(_prompt):
            raise EOFError

        with patch("sys.stdin.isatty", return_value=True):
            with patch(
                "infinite_craft_cli.cli._await_confirmation", side_effect=raise_eof
            ):
                run_async(_confirm_and_run_pairs(client, storage, pairs))
        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

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
            with patch("infinite_craft_cli.cli._await_confirmation") as mock_confirm:
                run_async(_confirm_and_run_pairs(client, storage, pairs))
        captured = capsys.readouterr()
        mock_confirm.assert_not_called()
        assert "pairs" in captured.out
        assert "press y" in captured.out
        assert "Done" in captured.out
        assert "Cancelled" not in captured.out


class TestDoPermute:
    def test_no_matches(self, capsys):
        from infinite_craft_cli.cli import do_permute

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(do_permute(client, storage, "zzz"))
        captured = capsys.readouterr()
        assert "No elements match" in captured.out

    def test_single_match(self, capsys):
        from infinite_craft_cli.cli import do_permute

        client = make_mock_client()
        storage = make_mock_storage([MockElement("Water", "💧")])
        run_async(do_permute(client, storage, "water"))
        captured = capsys.readouterr()
        assert "Need at least two" in captured.out

    def test_generates_correct_pairs(self, capsys):
        from infinite_craft_cli.cli import do_permute

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Wind", "🌬️"),
                MockElement("Wave", "🌊"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_permute(client, storage, "w*"))
        captured = capsys.readouterr()
        assert "3 elements match" in captured.out
        assert "3 unique pairs" in captured.out


class TestDoWith:
    def test_no_matches(self, capsys):
        from infinite_craft_cli.cli import do_with

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(do_with(client, storage, "Water", "zzz"))
        captured = capsys.readouterr()
        assert "No elements match: zzz" in captured.out

    def test_self_only_match(self, capsys):
        from infinite_craft_cli.cli import do_with

        client = make_mock_client()
        storage = make_mock_storage([MockElement("Water", "💧")])
        run_async(do_with(client, storage, "Water", "water"))
        captured = capsys.readouterr()
        assert "No other elements match" in captured.out

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
        assert "Combining" in captured.out
        assert "2 elements" in captured.out

    def test_exclude_filter(self, capsys):
        from infinite_craft_cli.cli import do_with

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Firewall", "🧱"),
                MockElement("Fire", "🔥"),
                MockElement("Wind", "🌬️"),
                MockElement("Earth", "🌍"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        run_async(do_with(client, storage, "Water", "!fire*"))
        captured = capsys.readouterr()
        assert "Combining" in captured.out
        assert "2 elements" in captured.out

    def test_invalid_regex(self, capsys):
        from infinite_craft_cli.cli import do_with

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(do_with(client, storage, "Water", "/[invalid/"))
        captured = capsys.readouterr()
        assert "Invalid regex pattern" in captured.out


class TestDoCross:
    def test_invalid_regex(self, capsys):
        from infinite_craft_cli.cli import do_cross

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(do_cross(client, storage, "/[invalid/", "water"))
        captured = capsys.readouterr()
        assert "Invalid regex pattern" in captured.out

    def test_complex_regex_cross_combines_matching_elements(self, capsys):
        # DIVERGENCES.md ruling 7: no "too complex" gate, and "/(a|aa)+/"
        # is a real quantified alternation group meaning "contains a run
        # of 'a'". Against the default base elements (Water, Fire, Wind,
        # Earth) the left query matches Water and Earth; the right query
        # "water" substring-matches Water. Water+Water is skipped as a
        # self-pair, leaving exactly one pair (Earth, Water) — so this
        # cross now actually runs instead of hitting the old "No elements
        # match" no-op path.
        from infinite_craft_cli.cli import do_cross

        client = make_mock_client()
        client.pair.return_value = MockElement("Mud", "")
        storage = make_mock_storage()
        run_async(do_cross(client, storage, "/(a|aa)+/", "water"))
        captured = capsys.readouterr()
        assert "No elements match" not in captured.out
        assert "Error" not in captured.out
        assert "Left (2): 💧 Water, 🌍 Earth" in captured.out
        assert "Right (1): 💧 Water" in captured.out
        assert "1 unique pairs" in captured.out
        assert "🌍 Earth + 💧 Water = Mud" in captured.out

    def test_left_no_matches(self, capsys):
        from infinite_craft_cli.cli import do_cross

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(do_cross(client, storage, "zzz", "water"))
        captured = capsys.readouterr()
        assert "No elements match: zzz" in captured.out

    def test_right_no_matches(self, capsys):
        from infinite_craft_cli.cli import do_cross

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(do_cross(client, storage, "water", "zzz"))
        captured = capsys.readouterr()
        assert "No elements match: zzz" in captured.out

    def test_overlap_excluded(self, capsys):
        from infinite_craft_cli.cli import do_cross

        # Both queries match the same single element
        client = make_mock_client()
        storage = make_mock_storage([MockElement("Water", "💧")])
        run_async(do_cross(client, storage, "water", "water"))
        captured = capsys.readouterr()
        assert "No valid pairs" in captured.out

    def test_generates_cross_product(self, capsys):
        from infinite_craft_cli.cli import do_cross

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Fire", "🔥"),
                MockElement("Earth", "🌍"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_cross(client, storage, "water", "fire"))
        captured = capsys.readouterr()
        assert "1 unique pairs" in captured.out


class TestDoExhaust:
    def test_combines_with_all(self, capsys):
        from infinite_craft_cli.cli import do_exhaust

        client = make_mock_client()
        storage = make_mock_storage()  # 4 base elements
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        run_async(do_exhaust(client, storage, "Water"))
        captured = capsys.readouterr()
        assert "3 pairs" in captured.out  # 4 total minus self

    def test_no_matches(self, capsys):
        from infinite_craft_cli.cli import do_exhaust

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(do_exhaust(client, storage, "zzz"))
        captured = capsys.readouterr()
        assert "No elements match" in captured.out

    def test_invalid_regex(self, capsys):
        from infinite_craft_cli.cli import do_exhaust

        client = make_mock_client()
        storage = make_mock_storage()
        run_async(do_exhaust(client, storage, "/[invalid/"))
        captured = capsys.readouterr()
        assert "Invalid regex pattern" in captured.out

    def test_no_valid_pairs(self, capsys):
        from infinite_craft_cli.cli import do_exhaust

        client = make_mock_client()
        storage = make_mock_storage([MockElement("Water", "💧")])
        run_async(do_exhaust(client, storage, "water"))
        captured = capsys.readouterr()
        assert "No valid pairs" in captured.out

    def test_multi_match_query(self, capsys):
        from infinite_craft_cli.cli import do_exhaust

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Wind", "🌬️"),
                MockElement("Fire", "🔥"),
                MockElement("Earth", "🌍"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_exhaust(client, storage, "w*"))
        captured = capsys.readouterr()
        assert "2 element(s) matching" in captured.out
        assert (
            "5 pairs" in captured.out
        )  # Water+Wind/Fire/Earth + Wind+Fire/Earth (deduped)


class TestDoPermutate:
    def test_stops_when_no_new_discoveries(self, capsys):
        from infinite_craft_cli.cli import do_permutate

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Wind", "🌬️"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_permutate(client, storage, "w*"))
        captured = capsys.readouterr()
        assert "Round 1" in captured.out
        assert "No new discoveries" in captured.out
        assert "Permutate done" in captured.out

    def test_single_match(self, capsys):
        from infinite_craft_cli.cli import do_permutate

        client = make_mock_client()
        storage = make_mock_storage([MockElement("Water", "💧")])
        run_async(do_permutate(client, storage, "water"))
        captured = capsys.readouterr()
        assert "Need at least two" in captured.out

    def test_grows_across_rounds(self, capsys):
        from infinite_craft_cli.cli import do_permutate

        client = make_mock_client()
        discoveries = [
            MockElement("Water", "💧"),
            MockElement("Wind", "🌬️"),
        ]
        storage = make_mock_storage(list(discoveries))
        call_count = 0

        async def mock_pair(a, b):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockElement("Wave", "🌊")
            nothing = MagicMock()
            nothing.name = None
            return nothing

        client.pair = mock_pair

        def add_side_effect(**kwargs):
            name = kwargs.get("name")
            if name and not any(e.name == name for e in discoveries):
                discoveries.append(MockElement(name, kwargs.get("emoji", "")))
            storage.get_all.return_value = list(discoveries)
            return None

        storage.add.side_effect = add_side_effect
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_permutate(client, storage, "w*"))
        captured = capsys.readouterr()
        assert "Round 1" in captured.out
        assert "Round 2" in captured.out
        assert "Permutate done" in captured.out

    def test_cancelled_shows_stopped(self, capsys):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import do_permutate

        cli._reset_cancelled()
        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Wind", "🌬️"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing

        async def cancel_after_first(*_args, **_kwargs):
            # LEGACY internal poke for cancel during permutate (narrow bulk test; mark per review)
            cli._cancelled = True
            return nothing

        with patch(
            "infinite_craft_cli.cli._combine_pairs", side_effect=cancel_after_first
        ):
            run_async(do_permutate(client, storage, "w*"))
        captured = capsys.readouterr()
        assert (
            "Cancelled." in captured.out
            or "Stopped early." in captured.out
            or "Stopped." in captured.out
        )
        assert "Permutate done" not in captured.out
        assert captured.out.count("Skipped.") == 0

    def test_invalid_regex(self, capsys):
        from infinite_craft_cli.cli import do_permutate

        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Wind", "🌬️"),
            ]
        )
        run_async(do_permutate(client, storage, "/[invalid/"))
        captured = capsys.readouterr()
        assert "Invalid regex pattern" in captured.out

    def test_max_permutate_rounds_cap(self, capsys):
        from infinite_craft_cli.cli import do_permutate

        client = make_mock_client()
        discoveries = [
            MockElement("Water", "💧"),
            MockElement("Wind", "🌬️"),
        ]
        storage = make_mock_storage(list(discoveries))
        round_num = 0

        async def mock_pair(a, b):
            nonlocal round_num
            round_num += 1
            return MockElement(f"Wind{round_num}", "🌬️")

        client.pair = mock_pair

        def add_side_effect(**kwargs):
            name = kwargs.get("name")
            if name and not any(e.name == name for e in discoveries):
                discoveries.append(MockElement(name, kwargs.get("emoji", "")))
            storage.get_all.return_value = list(discoveries)
            return None

        storage.add.side_effect = add_side_effect
        with patch("infinite_craft_cli.cli._MAX_PERMUTATE_ROUNDS", 2):
            with patch("infinite_craft_cli.cli._record_recipe"):
                run_async(do_permutate(client, storage, "w*"))
        captured = capsys.readouterr()
        assert "Reached max rounds (2)" in captured.out

    def test_bulk_confirm_once(self, capsys):
        from infinite_craft_cli.cli import do_permutate

        client = make_mock_client()
        discoveries = [
            MockElement("Elem0", "✨"),
            MockElement("Elem1", "✨"),
            MockElement("Elem2", "✨"),
        ]
        storage = make_mock_storage(list(discoveries))
        call_count = 0

        async def mock_pair(a, b):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockElement("ElemNew", "✨")
            nothing = MagicMock()
            nothing.name = None
            return nothing

        client.pair = mock_pair

        def add_side_effect(**kwargs):
            name = kwargs.get("name")
            if name and not any(e.name == name for e in discoveries):
                discoveries.append(MockElement(name, kwargs.get("emoji", "")))
            storage.get_all.return_value = list(discoveries)
            return None

        storage.add.side_effect = add_side_effect
        with patch("sys.stdin.isatty", return_value=True):
            with patch("infinite_craft_cli.cli._BULK_WARN_THRESHOLD", 1):
                with patch(
                    "infinite_craft_cli.cli._await_confirmation", return_value="y"
                ) as mock_confirm:
                    with patch("infinite_craft_cli.cli._record_recipe"):
                        run_async(do_permutate(client, storage, "elem*"))
        captured = capsys.readouterr()
        assert "pairs per round" in captured.out
        assert "Round 2" in captured.out
        mock_confirm.assert_called_once()


class TestDoCrawl:
    def test_no_new_discoveries_stops(self, capsys):
        from infinite_craft_cli.cli import do_crawl

        client = make_mock_client()
        storage = make_mock_storage()
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        with patch("infinite_craft_cli.cli._record_recipe"):
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
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(do_crawl(client, storage, "Water", "Fire"))
        captured = capsys.readouterr()
        assert "Steam" in captured.out

    def test_crawl_cancelled_no_duplicate_skipped(self, capsys):
        """Crawl cancel summary must suppress duplicate Skipped from worker."""
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import _api_worker, do_crawl

        # LEGACY direct _cancelled sets for narrow crawl cancel internal test (keep+mark)
        cli._cancelled = False
        cli._skip_summary_shown = False
        client = make_mock_client()
        storage = make_mock_storage()

        async def cancel_on_pair(_client, _storage, _a, _b):
            cli._cancelled = True
            nothing = MagicMock()
            nothing.name = None
            return nothing

        async def dispatch(_c, s, _l):
            await do_crawl(_c, s, "Water", "Fire")

        async def run():
            with (
                patch(
                    "infinite_craft_cli.cli._cached_pair", side_effect=cancel_on_pair
                ),
                patch("infinite_craft_cli.cli._record_recipe"),
                patch("infinite_craft_cli.cli._dispatch_line", side_effect=dispatch),
            ):
                cli._command_queue = ["/crawl Water Fire"]
                await _api_worker(client, storage)

        run_async(run())
        out = capsys.readouterr().out
        assert "Stopped early." in out or "Stopped." in out or "Cancelled." in out
        assert out.count("Skipped.") == 0
        cli._reset_cancelled()
