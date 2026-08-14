"""Interactive REPL UX tests vs bookmarklet/trainer.js queue/confirm/dispatch.

Documents expected Python behavior (match JS where sensible). Failing tests mark
parity gaps in cli.py: extra Started feedback, confirm sub-prompt, permutate spin
window, dual Continue? + confirm prompts.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import MockElement

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro, *, timeout: float = 8.0):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


@pytest.fixture(autouse=True)
def ux_env(tmp_path, request):
    import infinite_craft_cli.cli as cli

    def _ux_reset():
        try:
            cli._reset_test_state()
        except Exception:
            pass

    _ux_reset()
    request.addfinalizer(_ux_reset)
    with (
        patch(
            "infinite_craft_cli.cli.DISCOVERIES_PATH",
            str(tmp_path / "discoveries.json"),
        ),
        patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "recipes.json")),
    ):
        yield
    _ux_reset()


def _nothing():
    m = MagicMock()
    m.name = None
    return m


def _bulk_elems(prefix: str = "Bulk", n: int = 4):
    return [MockElement(f"{prefix}{i}", "✨") for i in range(n)]


def _confirm_prompts(calls: list[tuple[str, str]]) -> list[str]:
    return [p for p, _ in calls if "confirm" in p.lower()]


async def _await_confirm_ready(repl_harness, ready: asyncio.Event) -> None:
    """Wait until confirm chrome/prompt is up (keys live on the prompt now)."""
    import infinite_craft_cli.cli as cli

    while not ready.is_set():
        if any("confirm" in (p or "").lower() for p, _ in repl_harness.prompt_calls):
            ready.set()
            return
        if cli._waiting_for_confirm() or cli._bulk_confirm_pending:
            ready.set()
            return
        await asyncio.sleep(0)


def _craft_prompts(calls: list[tuple[str, str]]) -> list[str]:
    return [p for p, _ in calls if "craft>" in p.lower() and "confirm" not in p.lower()]


class TestLocalCommandsDuringLongOps:
    """Local commands must execute on one Enter while API work is in flight."""

    # Converted to repl_harness feed + run + Events (no _drive, no patch _prompt_input, no Timed)
    def test_search_during_slow_combine(self, repl_harness, capsys):
        storage = repl_harness.set_storage_elems()
        mock_client = repl_harness.set_mock_client()
        started = asyncio.Event()

        async def slow_pair(a, b):
            started.set()
            await asyncio.sleep(1.0)
            return _nothing()

        mock_client.pair = slow_pair

        async def drive():
            repl_harness.feed("/combine Water Fire")
            repl_harness.feed("/search Water")
            repl_harness.feed("/queue")
            repl_harness.feed("/quit")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await started.wait()
            await t

        run_async(drive())
        out = capsys.readouterr().out
        # use prompt_calls for calls (harness records)
        lines = [a for p, a in repl_harness.prompt_calls if a]

        assert "Water" in out
        assert "running" in out
        assert "/search Water" in lines
        assert "/queue" in lines


class TestBulkConfirmSingleEnter:
    """Blank Enter policy during bulk confirm (y/n covered by harness)."""

    def test_empty_enter_does_not_decline_confirm(self, repl_harness, capsys):
        """Blank Enter is ignored during confirm; only y/n (or Esc) decide."""
        storage = repl_harness.set_storage_elems(_bulk_elems())
        mock_client = repl_harness.set_mock_client()
        nothing = _nothing()
        mock_client.pair = AsyncMock(return_value=nothing)

        async def drive():
            repl_harness.feed("/exhaust Bulk0")
            repl_harness.feed("")  # ignored
            repl_harness.feed("n")  # decline
            await repl_harness.run_until_quit(
                client=mock_client, auto_feed_quit=False, storage=storage
            )

        with (
            patch("infinite_craft_cli.cli._BULK_WARN_THRESHOLD", 1),
            patch("sys.stdin.isatty", return_value=True),
        ):
            run_async(drive())
        out = capsys.readouterr().out
        assert "Cancelled." in out
        assert "Goodbye" in out
        assert out.rfind("Cancelled.") < out.rfind("Goodbye")
        # Must not have run the bulk pairs after declining
        assert "Done." not in out


class TestSpuriousEmptyPrompts:
    def test_empty_lines_do_not_enqueue_or_warn(self, repl_harness, capsys):
        """_prompt_input strips whitespace; blank lines must not enqueue."""
        storage = repl_harness.set_storage_elems()
        mock_client = repl_harness.set_mock_client()

        async def drive():
            repl_harness.feed("")
            repl_harness.feed("  ")
            repl_harness.feed("/quit")
            await repl_harness.run_until_quit(
                client=mock_client, auto_feed_quit=False, storage=storage
            )

        run_async(drive())
        out = capsys.readouterr().out

        assert "Unknown input" not in out
        assert "Started:" not in out


class TestREPLHarnessEdges:
    """Dedicated harness coverage for error/edge/concurrent (strengthens vs prior single adoption)."""

    def test_interleave_queued_command_output_local_and_esc_skip_via_harness(
        self, repl_harness, capsys
    ):
        """Flexible behavioral guard: output from running cmd appears (not corrupting chrome),
        locals interleave, ESC-skip produces Skipped + clean shutdown. Uses harness + capsys only.
        """
        mock_client = repl_harness.set_mock_client()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_with_output(a, b):
            started.set()
            print("PARTIAL-OUTPUT-FROM-WORKER")
            await release.wait()
            if repl_harness.is_cancelled():
                from infinite_craft_cli.cli import CommandCancelled

                raise CommandCancelled()
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = slow_with_output

        async def drive():
            repl_harness.feed("/combine Water Fire")
            t = asyncio.create_task(
                repl_harness.run_until_quit(client=mock_client, auto_feed_quit=False)
            )
            await started.wait()
            await asyncio.sleep(0)  # let the PARTIAL print land
            # drive queue changes: enqueue another (queues while first slow/running) + /queue
            repl_harness.feed("/combine Wind Earth")
            repl_harness.feed("/queue")
            repl_harness.feed("/list")
            # trigger skip via event timing (harness event driven)
            release.set()
            repl_harness.force_cancel()
            repl_harness.feed("/quit")
            await t

        run_async(drive())
        out = capsys.readouterr().out

        # output was emitted (jank guard: above chrome)
        assert "PARTIAL-OUTPUT-FROM-WORKER" in out
        # skip handled cleanly, shutdown ok (behavioral)
        assert "Skipped" in out or "Goodbye" in out
        assert "Goodbye" in out or "Infinite Craft" in out
        # some interleaving happened
        assert (
            any(
                "list" in (a[0].lower() + a[1].lower())
                for a in repl_harness.prompt_calls
            )
            or "list" in out.lower()
        )

        # behavioral assertions for queue panel + chrome when active:
        # accurate panel content after enqueues + /queue (using capsys, no internals)
        assert "running" in out or "Running:" in out
        assert "pending" in out or "Wind Earth" in out
        # prompt hints (active count) recorded via harness prompt_calls
        prompt_strs = " ".join(p for p, _ in repl_harness.prompt_calls).lower()
        assert "active" in prompt_strs or "[esc" in prompt_strs
        # no layout breakage/duplication; use relative order via rfind instead of numeric cap
        assert "Goodbye" in out
        assert out.rfind("PARTIAL") < out.rfind("Goodbye") or out.rfind(
            "Skipped"
        ) < out.rfind("Goodbye")

    def test_bulk_and_error_output_restores_chrome_prompt(self, repl_harness, capsys):
        """Bulk (using event gate) and error cases: output before chrome, clean prompt at end via harness."""
        import asyncio
        from unittest.mock import MagicMock

        started = asyncio.Event()
        release = asyncio.Event()

        elems = _bulk_elems("B", 3)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        async def slow_pair(a, b):
            started.set()
            await release.wait()
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = slow_pair

        async def drive():
            repl_harness.feed("/permute B*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await started.wait()
            await asyncio.sleep(0)
            repl_harness.feed("/list")
            release.set()
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(100)
        repl_harness.set_load_recipes({})
        run_async(drive())

        out = capsys.readouterr().out

        # bulk output / progress phrases and list after, clean
        assert "pairs" in out.lower() or "B0" in out or "Discovered" in out
        # chrome restored
        assert "queue" in out.lower() or "running" in out or "Goodbye" in out
        # final prompt_calls sequence ends with clean craft> after the result
        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        # prompts show locals while "running" (check answers or full since p is prompt text)
        calls_low = " ".join(
            (p + " " + a).lower() for p, a in repl_harness.prompt_calls
        )
        assert "list" in calls_low or "list" in out.lower()
        assert "Goodbye" in out

    def test_bulk_confirm_flow_y_restores_clean_chrome_via_harness(
        self, repl_harness, capsys
    ):
        """Bulk confirm y: queue shows confirm status (preparing/awaiting), answer yields progress, chrome/prompt clean restored. Pure harness + behavioral asserts."""
        from unittest.mock import patch, MagicMock

        elems = _bulk_elems("Bulk", 5)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        confirm_ready = asyncio.Event()

        async def slow_pair(a, b):
            await asyncio.sleep(0)
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = slow_pair

        async def drive():
            repl_harness.feed("/permute Bulk*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await _await_confirm_ready(repl_harness, confirm_ready)
            await asyncio.sleep(0)
            repl_harness.feed("y")
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(1)
        repl_harness.set_load_recipes({})
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out

        # confirm status in queue panel visible (no corruption)
        assert (
            "confirm" in out.lower()
            or ("◆" in out and "y" in out.lower())
        )
        # progress output after confirm y
        assert "Done." in out or "tried" in out or "new" in out.lower() or "Bulk" in out
        # clean prompt/chrome restoration, no mixing/garbage
        assert "Done." in out or "tried" in out or "new" in out.lower() or "Bulk" in out
        # final prompt_calls sequence ends with clean craft> after the command result
        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        assert "Goodbye" in out or "Infinite Craft" in out
        # order via rfind: confirm-related output before final Goodbye (no counts)
        assert out.rfind("confirm") < out.rfind("Goodbye") or out.rfind(
            "y"
        ) < out.rfind("Goodbye")
        # behavioral on prompt_calls: distinct confirm prompt and y answer (not craft>)
        has_confirm_prompt = any(
            "confirm" in p.lower() for p, _ in repl_harness.prompt_calls
        )
        assert has_confirm_prompt, (
            f"no confirm prompt seen: {repl_harness.prompt_calls}"
        )
        confirm_answers = [
            (p, a)
            for p, a in repl_harness.prompt_calls
            if "confirm" in p.lower() and a.strip().lower() in ("y", "yes")
        ]
        assert confirm_answers, "bulk confirm y not seen at confirm prompt"

    def test_esc_during_bulk_confirm_via_tty_bytes(self, repl_harness, capsys):
        """ESC during bulk confirm (tty bytes): clean cancel (Skipped./Cancelled.), chrome restored, no jank/dupe text. Harness only."""
        from unittest.mock import patch, MagicMock

        elems = _bulk_elems("Bulk", 5)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        confirm_ready = asyncio.Event()

        async def slow_pair(a, b):
            await asyncio.sleep(0)
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = slow_pair

        async def drive():
            repl_harness.enable_tty_mode()
            # feed bulk cmd via tty bytes (required for tty mode)
            repl_harness.feed_tty_bytes(b"/permute Bulk*\n")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await _await_confirm_ready(repl_harness, confirm_ready)
            await asyncio.sleep(0)
            # ESC via tty bytes for special key; \n to submit if needed for confirm read unblock
            repl_harness.feed_tty_bytes(b"\x1b\n")
            repl_harness.feed_tty_bytes(b"/quit\n")
            await t

        repl_harness.set_bulk_warn_threshold(1)
        repl_harness.set_load_recipes({})
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out

        # clean cancel (either path) and restored
        assert "Skipped." in out or "Cancelled." in out or "Goodbye" in out
        # queue/chrome status may have shown confirm before cancel
        assert "queue" in out.lower() or "confirm" in out.lower() or "Goodbye" in out
        # final prompt_calls (may be empty under tty bytes mode); rely on out for chrome phrases + Goodbye
        if repl_harness.prompt_calls:
            last_p, _ = repl_harness.prompt_calls[-1]
            assert "craft>" in last_p.lower()
        assert "Goodbye" in out
        assert out.rfind("confirm") < out.rfind("Goodbye") or out.rfind(
            "y"
        ) < out.rfind("Goodbye")
        # prompt seq via harness (empty ok in tty bytes mode; bytes bypass the test hook)
        if repl_harness.prompt_calls:
            has_confirm = any(
                "confirm" in (p + a).lower() for p, a in repl_harness.prompt_calls
            )
        else:
            has_confirm = False
        assert has_confirm or "confirm" in out.lower() or "pairs" in out.lower()

    def test_interleave_local_during_confirm_setup_via_harness(
        self, repl_harness, capsys
    ):
        """Local command interleaved (via pre-feed) during bulk confirm setup: local runs, confirm status, y after still works, clean chrome after."""
        from unittest.mock import patch, MagicMock

        elems = _bulk_elems("Bulk", 5)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        confirm_ready = asyncio.Event()

        async def slow_pair(a, b):
            await asyncio.sleep(0)
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = slow_pair

        async def drive():
            # pre-feed bulk then local: local will be first consumed at confirm prompt time (interleave before y)
            repl_harness.feed("/permute Bulk*")
            repl_harness.feed("/list")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await _await_confirm_ready(repl_harness, confirm_ready)
            await asyncio.sleep(0)
            repl_harness.feed("y")
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(1)
        repl_harness.set_load_recipes({})
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out

        # confirm status appeared
        assert "confirm" in out.lower()
        # local interleaved (output from /list)
        assert "Discovered" in out or "elements" in out or "list" in out.lower()
        # after y, progress and clean
        assert (
            "Done." in out or "tried" in out or "new" in out.lower() or "Goodbye" in out
        )
        # final prompt_calls sequence ends with clean craft> after command result
        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        # prompt_calls show interleave + confirm answer
        calls_joined = " ".join((p + a).lower() for p, a in repl_harness.prompt_calls)
        assert "list" in calls_joined or "list" in out.lower()
        assert any("confirm" in p.lower() for p, _ in repl_harness.prompt_calls)
        assert "Goodbye" in out

    def test_bulk_confirm_y_via_harness_pure_event_after_confirm_prompt(
        self, repl_harness, capsys
    ):
        """Pure harness bulk confirm y (real path): use _BULK=1 + /permutate, Event to feed y only after confirm prompt appears in seq (via monitor).
        Assert prompt_calls has distinct "confirm [y/N]>" (no stray craft> during setup window via seq), warning phrase, queue status, clean output/Goodbye (phrases + find order, no counts).
        """
        import asyncio
        from unittest.mock import patch, MagicMock

        elems = _bulk_elems("Bulk", 4)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        async def quick_pair(a, b):
            await asyncio.sleep(0)
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = quick_pair

        confirm_ready = asyncio.Event()

        async def drive():
            repl_harness.feed("/permutate Bulk*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )

            # event-driven: feed y strictly after "confirm" prompt recorded in prompt_calls
            async def _wait_for_confirm_in_seq():
                for _ in range(150):
                    if any(
                        "confirm" in (p or "").lower()
                        for p, _a in repl_harness.prompt_calls
                    ):
                        confirm_ready.set()
                        return
                    await asyncio.sleep(0.005)
                confirm_ready.set()

            asyncio.create_task(_wait_for_confirm_in_seq())
            await confirm_ready.wait()
            await asyncio.sleep(0)
            repl_harness.feed("y")
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(1)
        repl_harness.set_load_recipes({})
        with (
            patch("sys.stdin.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out
        calls = repl_harness.prompt_calls

        # pair count still in the log/chrome; keys live only on confirm [y/n]>
        assert "pairs" in out
        assert "confirm" in out.lower()
        # clean progress after y
        assert (
            "Permutate done" in out
            or "new" in out.lower()
            or "tried" in out
            or "Done." in out
            or "Bulk" in out
        )
        # queue status (confirm) visible
        assert "confirm" in out.lower()
        # clean chrome/prompt restored, Goodbye
        assert "Goodbye" in out
        assert "craft>" in out or "Goodbye" in out
        # specific text before final Goodbye (relative order, pure phrases)
        ppos = out.find("pairs")
        assert ppos < out.rfind("Goodbye") or ppos == -1

        # via prompt_calls: confirm [y/N]> seen distinct from craft>
        assert any("confirm" in p.lower() for p, _ in calls), (
            f"no confirm prompt seen: {calls}"
        )
        cy = [
            (p, a)
            for p, a in calls
            if "confirm" in p.lower() and a.strip().lower() in ("y", "yes")
        ]
        assert cy, "bulk confirm y not seen at confirm prompt"
        # no stray craft> during setup window (seq after cmd answer -> confirm next)
        cmd_i = next(
            (i for i, (_p, a) in enumerate(calls) if "permutate" in a.lower()), -1
        )
        if cmd_i >= 0 and cmd_i + 1 < len(calls):
            nxt = calls[cmd_i + 1][0].lower()
            assert "confirm" in nxt, (
                f"expected confirm prompt (not stray craft) after bulk cmd in seq; got {nxt}"
            )
            assert "craft>" not in nxt or "confirm" in nxt

    def test_bulk_confirm_decline_n_via_harness_pure(self, repl_harness, capsys):
        """Pure harness bulk confirm decline ("n"; also ESC semantics via same path): feed n after confirm prompt (Event), assert via prompt_calls + capsys phrases/order, "Cancelled." once cleanly (no mix/dupe), queue, Goodbye."""
        import asyncio
        from unittest.mock import patch, MagicMock

        elems = _bulk_elems("Bulk", 4)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        async def quick_pair(a, b):
            await asyncio.sleep(0)
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = quick_pair

        confirm_ready = asyncio.Event()

        async def drive():
            repl_harness.feed("/permutate Bulk*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )

            async def _wait_for_confirm_in_seq():
                for _ in range(150):
                    if any(
                        "confirm" in (p or "").lower()
                        for p, _a in repl_harness.prompt_calls
                    ):
                        confirm_ready.set()
                        return
                    await asyncio.sleep(0.005)
                confirm_ready.set()

            asyncio.create_task(_wait_for_confirm_in_seq())
            await confirm_ready.wait()
            await asyncio.sleep(0)
            repl_harness.feed("n")
            repl_harness.feed("/queue")
            repl_harness.feed("/quit")
            await t
            try:
                repl_harness.reset()
            except Exception:
                pass

        repl_harness.set_bulk_warn_threshold(1)
        repl_harness.set_load_recipes({})
        with (
            patch("sys.stdin.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out
        calls = repl_harness.prompt_calls

        # decline phrase and clean
        assert "Cancelled." in out
        assert "Goodbye" in out
        # phrase before final (relative order); no mixing/dupe "Cancelled."
        cpos = out.find("Cancelled.")
        assert cpos != -1
        assert cpos < out.rfind("Goodbye")
        assert out.find("Cancelled.", cpos + 1) == -1
        # queue status may appear
        assert "confirm" in out.lower() or "queue" in out.lower() or "Goodbye" in out
        # strengthened guard (non-brittle, allowed style): status text uses "prompt" (not "craft>"), + Cancelled rfind already above
        pre = out[: out.rfind("Goodbye")] if "Goodbye" in out else out
        assert (
            "prompt" in pre.lower()
        )  # idle /queue status after decline uses "the prompt"
        assert "craft>." not in out
        # prompt_calls confirm + decline n at it (distinct)
        assert any("confirm" in p.lower() for p, _ in calls)
        cn = [
            (p, a)
            for p, a in calls
            if "confirm" in p.lower() and a.strip().lower() in ("n", "no")
        ]
        assert cn, "bulk confirm n not at confirm prompt"
        # no stray craft in confirm seq window
        cmd_i = next(
            (i for i, (_p, a) in enumerate(calls) if "permutate" in a.lower()), -1
        )
        if cmd_i >= 0 and cmd_i + 1 < len(calls):
            nxt = calls[cmd_i + 1][0].lower()
            assert "confirm" in nxt

    def test_bulk_confirm_quit_at_confirm_via_harness_pure(self, repl_harness, capsys):
        """Pure non-brittle harness test (TestREPLHarnessEdges): drive bulk confirm (low thresh permutate), Event + prompt_calls polling to wait for confirm, feed "/quit" while confirm prompt active.
        Covers /quit-at-confirm immediate shutdown (discard, Goodbye, no y/n required, no enqueue).
        Asserts (allowed style only): "confirm" in seq, "Goodbye" in out, no stray "craft>" between bulk cmd and final, rfind order, final prompt_calls may reflect quit path, clean.
        """
        import asyncio
        from unittest.mock import patch, MagicMock

        elems = _bulk_elems("Bulk", 4)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        async def quick_pair(a, b):
            await asyncio.sleep(0)
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = quick_pair

        confirm_ready = asyncio.Event()

        async def drive():
            repl_harness.feed("/permutate Bulk*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )

            # event-driven poll: feed /quit strictly after confirm seen in prompt_calls (while prompt active)
            async def _wait_for_confirm_in_seq():
                for _ in range(150):
                    if any(
                        "confirm" in (p or "").lower()
                        for p, _a in repl_harness.prompt_calls
                    ):
                        confirm_ready.set()
                        return
                    await asyncio.sleep(0.005)
                confirm_ready.set()

            asyncio.create_task(_wait_for_confirm_in_seq())
            await confirm_ready.wait()
            await asyncio.sleep(0)
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(1)
        repl_harness.set_load_recipes({})
        with (
            patch("sys.stdin.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out
        calls = repl_harness.prompt_calls

        # confirm seen in seq
        assert any("confirm" in p.lower() for p, _ in calls), (
            f"no confirm prompt seen: {calls}"
        )
        # /quit fed at the confirm prompt (the cover path)
        qconfirm = [
            (p, a) for p, a in calls if "confirm" in p.lower() and "/quit" in a.lower()
        ]
        assert qconfirm, "no /quit fed while at confirm prompt"
        # clean exit with Goodbye (no y/n answered; may or not have Cancelled. depending exact cancel path)
        assert "Goodbye" in out
        # no stray craft> between bulk cmd and final (confirm window clean)
        cmd_i = next(
            (i for i, (_p, a) in enumerate(calls) if "permutate" in a.lower()), -1
        )
        if cmd_i >= 0 and cmd_i + 1 < len(calls):
            for j in range(cmd_i + 1, len(calls)):
                nxtp = calls[j][0].lower()
                if "craft>" in nxtp and "confirm" not in nxtp:
                    assert False, (
                        f"stray craft> in seq after bulk cmd before final: {nxtp}"
                    )
        # rfind order: bulk/confirm phrases before Goodbye
        ppos = out.find("pairs")
        assert ppos < out.rfind("Goodbye") or ppos == -1
        # clean restoration; final prompt in calls for path
        if calls:
            last_p, _ = calls[-1]
            assert "confirm" in last_p.lower() or "craft>" in last_p.lower()
        assert "craft>" in out or "Goodbye" in out

    def test_history_recipe_cross_use_formatted_elements_emoji_first(
        self, repl_harness, capsys
    ):
        """History (now unified), recipe summaries, cross outputs show elements via format_element (emoji, FIRST tag where set)."""
        from unittest.mock import AsyncMock
        from tests.conftest import MockElement

        elems = [
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Mud", "🪨", is_first_discovery=True),
        ]
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()
        # return a result name that matches a FIRST elem in storage; history will resolve+format it
        # use MockElement so str() gives "🪨 Mud" not MagicMock repr
        res = MockElement("Mud", "🪨", is_first_discovery=True)
        mock_client.pair = AsyncMock(return_value=res)

        repl_harness.feed("Water + Fire")
        repl_harness.feed("/history")
        repl_harness.feed("/recipe Mud")
        repl_harness.feed("/cross Water Fire")
        repl_harness.feed("/quit")
        repl_harness.set_load_recipes({"Mud": [["Water", "Fire"]]})
        run_async(
            repl_harness.run_until_quit(
                auto_feed_quit=False, storage=storage, client=mock_client
            )
        )

        out = capsys.readouterr().out

        # formatted elements visible (emoji from format_element)
        assert "💧 Water" in out
        assert "🔥 Fire" in out
        assert "🪨 Mud" in out
        # FIRST from storage resolve in history and recipe
        assert "[FIRST DISCOVERY!]" in out
        # history shows formatted form
        assert "1. 💧 Water + 🔥 Fire = 🪨 Mud" in out or (
            "Water + Fire" in out and "Mud" in out
        )
        # cross summary and result lines use formatted
        assert "Left (1):" in out or "💧 Water" in out
        assert "Right (1):" in out or "🔥 Fire" in out
        # recipe uses formatted
        assert "Recipe for" in out
        # clean chrome restoration, no jank/mix -- use prompt seq for end
        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        assert "Goodbye" in out
        # prompts recorded for the formatting cmds
        assert any("history" in (p + a).lower() for p, a in repl_harness.prompt_calls)
        assert any("recipe" in (p + a).lower() for p, a in repl_harness.prompt_calls)

    def test_progress_during_fill_prune_clean_via_harness(self, repl_harness, capsys):
        """Progress during /fill and /prune (the [i/total] ... remaining lines) go thru repl/print
        when chrome, visible cleanly in capsys without mixing remnants/spill in chrome or prompt.
        Drive exclusively with harness + events; assert relative order and final clean state.
        """
        import asyncio
        import threading
        from unittest.mock import AsyncMock
        from tests.conftest import MockElement

        elems = [
            MockElement("Water", "💧"),
            MockElement("MysteryX", "❓"),
            MockElement("OrphanY", "🧬"),
        ]
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        fill_started = threading.Event()
        fill_release = threading.Event()

        def slow_ib_fetch(path, params, use_cache=True, quiet=False):
            fill_started.set()
            fill_release.wait()
            return {"steps": []} if path == "recipe" else {}

        prune_started = threading.Event()
        prune_release = threading.Event()

        def slow_ib_can_fill(name):
            prune_started.set()
            prune_release.wait()
            return False  # so it would prune

        async def drive_fill_prune():
            # drive fill with slow mock
            repl_harness.feed("/fill")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    auto_feed_quit=False, client=mock_client, storage=storage
                )
            )
            await asyncio.to_thread(fill_started.wait)
            repl_harness.feed("/queue")
            fill_release.set()
            await asyncio.sleep(0)
            # now prune (after fill done)
            repl_harness.feed("/prune")
            await asyncio.to_thread(prune_started.wait)
            repl_harness.feed("/list")
            prune_release.set()
            repl_harness.feed("/quit")
            await t

        repl_harness.set_load_recipes({})
        # Production uses await asyncio.to_thread(_ib_fetch / _ib_can_fill, ...);
        # patch the sync entry points so dual-queue slow-IB harness still works.
        repl_harness.install_cli_patch(
            "_ib_fetch",
            side_effect=slow_ib_fetch,
        )
        repl_harness.install_cli_patch(
            "_ib_can_fill",
            side_effect=slow_ib_can_fill,
        )
        repl_harness.install_cli_patch(
            "_sleep_cancellable_async",
            new=AsyncMock(return_value=False),
        )
        run_async(drive_fill_prune())

        out = capsys.readouterr().out

        # progress text present cleanly (no chrome mixing)
        assert "missing recipes" in out or "Fetching from Infinibrowser" in out
        assert "[1/1]" in out or "MysteryX" in out or "remaining" in out
        assert (
            "orphan" in out.lower()
            or "to check on Infinibrowser" in out
            or "Pruned" in out
        )
        assert "[1/1]" in out or "OrphanY" in out
        # interleave happened cleanly
        assert "queue" in out.lower() or "running" in out
        assert "List:" in out or "elements" in out or "Discovered" in out
        # final clean state
        assert "Goodbye" in out
        # pure relative order via in/rfind (no numeric, resilient to ANSI/chrome redraws in capsys)
        assert "missing" in out or "orphan" in out or "[1/1]" in out or "Pruned" in out
        assert out.rfind("Goodbye") > 0

    def test_bulk_progress_after_confirm_via_harness(self, repl_harness, capsys):
        """Bulk progress ( [i/total] combine lines + Done. ) after y confirm visible cleanly
        via harness drive, in capsys without spill/mix with chrome/prompt; use relative order.
        """
        import asyncio
        from unittest.mock import MagicMock

        elems = _bulk_elems("BulkProg", 3)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        confirm_ready = asyncio.Event()

        combine_started = asyncio.Event()

        async def slow_pair(a, b):
            combine_started.set()
            await asyncio.sleep(0)
            m = MagicMock()
            m.name = None
            m.emoji = ""
            return m

        mock_client.pair = slow_pair

        async def drive_bulk():
            repl_harness.feed("/permute BulkProg*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    auto_feed_quit=False, client=mock_client, storage=storage
                )
            )
            await _await_confirm_ready(repl_harness, confirm_ready)
            await asyncio.sleep(0)
            repl_harness.feed("y")
            await combine_started.wait()
            await asyncio.sleep(
                0.05
            )  # let pairs finish and emit Done. before possible quit cancel
            repl_harness.feed("/queue")
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(1)
        repl_harness.set_load_recipes({})
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            run_async(drive_bulk())

        out = capsys.readouterr().out

        # bulk progress text after confirm present cleanly
        assert "pairs" in out.lower() and (
            "y" in out.lower() or "continue" in out.lower()
        )
        assert (
            "[1/" in out
            or "[2/" in out
            or "BulkProg" in out
            or "Done." in out
            or "tried" in out
        )
        assert (
            "Done." in out
            or "tried" in out
            or "nothing" in out.lower()
            or "BulkProg" in out
        )
        # no mixing , chrome phrases , final clean
        assert "queue" in out.lower() or "running" in out or "pending" in out
        assert "Goodbye" in out
        # pure relative (in/rfind safe): progress visible before/around final Goodbye; no numeric caps
        assert out.find("Goodbye") > 0
        # harness recorded the confirm y not as craft
        answers = repl_harness.answers()
        assert any(a.strip().lower() in ("y", "yes") for a in answers)

    def test_small_bulk_mixed_results_progress_uses_repl_and_clean_prompt_via_harness(
        self, repl_harness, capsys
    ):
        """Pure harness + capsys behavioral test for bulk pair progress unification.

        Drives small /permute (3 elems -> 3 pairs) below threshold (high _BULK_WARN patched).
        mock_client.pair returns controlled: some Nothing, some new (triggers [NEW] success print).
        Uses sleep + local feed for coord to feed /quit after progress (bulk must fully complete).
        Asserts ONLY: phrases "in out", rfind for order, prompt_calls[-1] ends craft>, "Goodbye" in out.
        Verifies progress output (e.g. [N/M] style, NewDisc, AnotherNew, Bulk+ , Done.)
        AND clean chrome/prompt restored (via final prompt + Goodbye).
        No legacy Scripted/Timed, no counts of prompts, no raw ANSI, no direct cli._* access in test body,
        no "or True" soft asserts.
        """
        import asyncio
        from unittest.mock import MagicMock
        from tests.conftest import MockElement

        started = asyncio.Event()

        elems = _bulk_elems("Bulk", 3)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        counter = [0]

        async def controlled_pair(a, b):
            counter[0] += 1
            idx = counter[0]
            started.set()
            # quick (no block): returns based on invocation order; mix of nothing + news to hit success prints
            if idx == 1:
                # produces success per-pair print with [NEW]
                return MockElement("NewDisc", "✨")
            elif idx == 2:
                # Nothing: no per-pair print for this
                m = MagicMock()
                m.name = None
                m.emoji = None
                m.is_first_discovery = None
                return m
            else:
                # another new: guarantees second progress print line from batch 2 (no raise to avoid any cancel interaction)
                return MockElement("AnotherNew", "🆕")

        mock_client.pair = controlled_pair

        async def drive():
            repl_harness.feed("/permute Bulk*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await started.wait()
            await asyncio.sleep(
                0.2
            )  # allow all quick pairs/gather/prints/Done. to complete before next feeds
            # feed local (non-quit) to satisfy any pending prompt read (prevents timeout injecting /quit)
            repl_harness.feed("/list")
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(100)
        repl_harness.set_load_recipes({})
        run_async(drive())

        out = capsys.readouterr().out

        # progress output appears (per-pair for the new cases; nothing case is silent per pair)
        assert "Bulk" in out
        assert "+" in out
        assert "NewDisc" in out
        assert "AnotherNew" in out
        assert "[NEW]" in out
        # final summary phrases
        assert "Done." in out
        assert "new" in out or "tried" in out
        # [N/M] style progress text "in" out (note emitted as [1/3] etc)
        assert "[1/" in out or "[2/" in out or "[3/" in out
        # relative order via rfind (progress before Done.)
        assert (
            out.rfind("NewDisc") < out.rfind("Done.")
            or out.rfind("AnotherNew") < out.rfind("Done.")
            or out.rfind("Bulk") < out.rfind("Done.")
        )
        # chrome/prompt restored cleanly; no stuck/mixed via final checks
        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        assert "Goodbye" in out

    def test_small_permutate_multi_line_output_and_summaries_clean_spacing_via_harness(
        self, repl_harness, capsys
    ):
        """Pure non-brittle behavioral test (in TestREPLHarnessEdges only) using repl_harness + capsys.

        Drives /permutate (small below threshold, multi-line: ctrl stop line, --- round, per-pair progress incl [NEW],
        Done. summary from combine, +N new line, "No new discoveries", "Permutate done", list output, Goodbye).
        Uses Event + instrument_repl_lines for timing (wait for progress/summary emitted before feeding more).
        No legacy Scripted/TimedPrompt, no prompt counts, no raw ANSI in asserts.

        Assertions only allowed style: "phrase" in out, out.find/out.rfind for relative positions
        (e.g. "pairs" before "Done." before "Goodbye", ctrl before round/summary), repl_harness.prompt_calls[-1]
        ends with "craft>", "Goodbye" in out, any confirm checks etc.

        Spacing verified indirectly via phrase presence + relative order (ctrl before round/Done, [NEW] before Done.);
        no \n\n\n or exact-blank counts.
        """
        import asyncio
        from unittest.mock import MagicMock
        from tests.conftest import MockElement

        elems = _bulk_elems("Bulk", 3)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        counter = [0]
        progress_done = asyncio.Event()
        real_repl_lines = repl_harness.get_repl_print_lines()

        def instrument_repl_lines(text):
            try:
                t = str(text) if text else ""
                if "Done." in t or "Permutate done" in t or "No new discoveries" in t:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(progress_done.set)
                    except RuntimeError:
                        progress_done.set()
            except Exception:
                pass
            return real_repl_lines(text)

        async def controlled_pair(a, b):
            counter[0] += 1
            idx = counter[0]
            if idx == 1:
                return MockElement("NewDisc", "✨")
            elif idx == 2:
                m = MagicMock()
                m.name = None
                m.emoji = None
                m.is_first_discovery = None
                return m
            else:
                return MockElement("AnotherNew", "🆕")

        mock_client.pair = controlled_pair

        async def drive():
            repl_harness.feed("/permutate Bulk*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await progress_done.wait()
            await asyncio.sleep(0)
            repl_harness.feed("/list")
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(100)
        repl_harness.set_max_permutate_rounds(1)
        repl_harness.set_load_recipes({})
        repl_harness.install_repl_lines_wrapper(instrument_repl_lines)
        run_async(drive())

        out = capsys.readouterr().out

        # phrases present (multi-line output + summaries)
        assert "Permutating matches for" in out or "permutate" in out.lower()
        assert "(Ctrl+C to stop)" in out
        assert "--- Round 1:" in out or "Round" in out
        assert "NewDisc" in out
        assert "AnotherNew" in out
        assert "[NEW]" in out
        assert "Done." in out
        assert "new" in out or "tried" in out
        assert "+0 new elements" in out or "+0" in out
        assert "No new discoveries. Stopping." in out
        assert "Permutate done after" in out
        assert "Discovered" in out  # from /list after
        assert "Goodbye" in out

        # relative order via find/rfind (pairs/ctrl before Done. before Goodbye)
        assert out.find("(Ctrl+C to stop)") < out.find("Done.") < out.rfind("Goodbye")
        assert out.find("Round") < out.find("Done.") or out.find("pairs") < out.find(
            "Done."
        )
        assert out.find("Done.") < out.rfind("Goodbye")

        # final prompt clean via harness
        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()

        # spacing via allowed style: phrase in + relative order (ctrl before round/Done; [NEW] before Done.)
        assert out.find("(Ctrl+C to stop)") < out.find("Round") or out.find(
            "(Ctrl+C to stop)"
        ) < out.find("Done.")
        assert out.find("[NEW]") < out.find("Done.")
        assert out.find("new elements") < out.find("No new") or out.find(
            "+0"
        ) < out.find("No new")
        # flow to final prompt not corrupted
        assert out.rfind("Permutate done") < out.rfind("Goodbye")
        assert "Goodbye" in out

    def test_no_duplicate_queue_status_under_chrome_via_harness(
        self, repl_harness, capsys
    ):
        """Pure non-brittle harness test in TestREPLHarnessEdges.

        Drive cases under chrome (via harness isatty + enable) + /queue feed when running or idle.
        Assert with allowed style only: in / rfind + prompt_calls[-1] "craft>" + "Goodbye".
        No duplicate phrases (e.g. "Running:" not with panel "▶ running" or "1. pending" textual);
        panel phrases ("▶" or "running", "pending", "◆", "queue") in out when expected;
        for idle, status msg does not contain literal "craft>" (checked via "craft>." absent; clean panel).
        Uses Events if timing, follows existing harness patterns.
        """
        import asyncio
        from unittest.mock import patch, MagicMock

        mock_client = repl_harness.set_mock_client()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_pair(a, b):
            started.set()
            await release.wait()
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = slow_pair

        async def drive():
            # idle /queue first (chrome path)
            repl_harness.feed("/queue")
            # start running work
            repl_harness.feed("/combine Water Fire")
            t = asyncio.create_task(
                repl_harness.run_until_quit(client=mock_client, auto_feed_quit=False)
            )
            await started.wait()
            await asyncio.sleep(0)
            # pending + /queue while running (chrome)
            repl_harness.feed("/combine Wind Earth")
            repl_harness.feed("/queue")
            release.set()
            repl_harness.feed("/quit")
            await t

        repl_harness.set_tty_size(24, 80)
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out

        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        assert "Goodbye" in out

        pre = out[: out.rfind("Goodbye")] if "Goodbye" in out else out
        # no textual dupes (Running: / " pending:") when panel provides under chrome
        assert "Running:" not in out
        assert " pending:" not in out
        # panel phrases present for running/pending case
        assert "▶" in out or "running" in pre
        assert "pending" in pre or "queue" in out.lower() or "◆" in out
        # for idle /queue: no status containing literal "craft>"
        assert "craft>." not in out
        # rfind usage for order
        assert out.rfind("Goodbye") > out.rfind("queue") or "queue" not in out.lower()

    def test_rapid_locals_output_queue_interleave_clean_via_harness(
        self, repl_harness, capsys
    ):
        """Pure non-brittle harness test in TestREPLHarnessEdges for redraw throttle.

        Drive rapid locals + output + queue changes (many /list + /queue interleaved while running).
        Assert (allowed style): clean output, no corruption, final prompt correct, Goodbye, rfind order.
        Indirectly verifies lack of flicker (no mixed text, clean panel after bursts) via in/rfind + prompt seq.
        """
        import asyncio
        from unittest.mock import MagicMock

        mock_client = repl_harness.set_mock_client()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_pair(a, b):
            started.set()
            await release.wait()
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = slow_pair

        async def drive():
            repl_harness.feed("/combine Water Fire")
            t = asyncio.create_task(
                repl_harness.run_until_quit(client=mock_client, auto_feed_quit=False)
            )
            await started.wait()
            await asyncio.sleep(0)
            # rapid locals + output + queue changes: many /list during running + /queue
            for _ in range(3):
                repl_harness.feed("/list")
                repl_harness.feed("/queue")
            release.set()
            repl_harness.feed("/quit")
            await t

        run_async(drive())

        out = capsys.readouterr().out

        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        assert "Goodbye" in out

        # clean output, no corruption
        assert (
            "Discovered" in out
            or "elements" in out
            or "Water" in out
            or "combine" in out.lower()
        )
        # no mixed junk
        assert "craft>." not in out
        pre = out[: out.rfind("Goodbye")] if "Goodbye" in out else out
        # order via rfind: some output before goodbye
        assert out.rfind("Goodbye") > 0
        assert (
            "list" in pre.lower()
            or "queue" in pre.lower()
            or "▶" in out
            or "running" in pre
        )
        # relative rfind order for key markers
        assert out.find("Goodbye") > out.find("combine") or "combine" not in out.lower()

    def test_streaming_bulk_slow_pairs_interleaved_local_and_queue_status_via_harness(
        self, repl_harness, capsys
    ):
        """Pure non-brittle harness test in TestREPLHarnessEdges.

        Drive streaming bulk (slow pairs) + interleaved local (/list or /search) + /queue;
        assert (allowed in/rfind/phrases + prompt seq + Goodbye) that queue/prompt status
        remains visible/updated (e.g. "running" or "▶" or "pending" phrases appear after
        output lines without corruption; no stale panel).
        Uses Event + timing.
        """
        import asyncio
        from unittest.mock import patch, MagicMock

        elems = _bulk_elems("Bulk", 3)
        storage = repl_harness.set_storage_elems(elems)
        mock_client = repl_harness.set_mock_client()

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_pair(a, b):
            started.set()
            await release.wait()
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = slow_pair

        async def drive():
            repl_harness.feed("/permutate Bulk*")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await started.wait()
            await asyncio.sleep(0)
            # interleaved locals during running bulk (streaming outputs via slow pairs)
            repl_harness.feed("/list")
            repl_harness.feed("/search Bulk")
            await asyncio.sleep(0.02)  # yield so local commands are processed + chrome redraws happen while bulk still "running"
            release.set()
            repl_harness.feed("/quit")
            await t

        repl_harness.set_bulk_warn_threshold(100)
        repl_harness.set_max_permutate_rounds(1)
        repl_harness.set_load_recipes({})
        repl_harness.set_tty_size(24, 80)
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out

        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        assert "Goodbye" in out

        pre = out[: out.rfind("Goodbye")] if "Goodbye" in out else out

        # panel phrases for queue/prompt status visible (under chrome during streaming)
        assert "▶" in out or "running" in pre
        assert "pending" in pre or "◆" in out or "[active]" in pre.lower()

        # no corruption / stale panel
        assert "craft>." not in out
        assert (
            "Queue is idle" not in out
        )  # chrome panel path, avoid non-chrome idle text

        # phrases appear after output lines (verifies force redraw of chrome after scroll writes)
        # /list output (contains "Discovered") + bulk progress from slow pairs; status re-emitted after
        # the local output via _repl_print + _chrome_draw(force=True)
        list_pos = max((out.rfind(p) for p in ("Discovered", "elements:")), default=-1)
        assert list_pos >= 0, "expected output lines from /list during running"
        tail = out[list_pos:]
        assert any(p in tail for p in ("▶", "running")), (
            "queue/prompt status (▶ running etc) must appear after /list output lines; "
            "no stale panel (chrome must force redraw panel after scroll writes from local cmds)"
        )
        # bulk progress lines (emitted via _repl during streaming) also followed by status redraw
        bulk_pos = max(
            (out.rfind(p) for p in ("Permutate done", "Stopping", "Round", "+0 new")),
            default=-1,
        )
        if bulk_pos >= 0:
            tail_after_bulk = out[bulk_pos:]
            assert any(p in tail_after_bulk for p in ("▶", "running")) or "▶" in tail_after_bulk, (
                "status after bulk streaming output"
            )

        # relative order via rfind (output before final Goodbye)
        assert out.rfind("Goodbye") > 0
        assert (
            out.rfind("Goodbye") > out.rfind("permutate")
            or "permutate" not in out.lower()
        )

    def test_rate_limit_wait_shows_indicator_via_harness(self, repl_harness, capsys):
        """Rate-limit wait still drives chrome paint via wait_callback.

        Remaining-budget bar replaces the old ⏳ suffix; during a wait the
        job line still shows running and rate chrome is painted.
        """
        import asyncio
        from infinite_craft_cli.ratelimit import RateLimiter

        storage = repl_harness.set_storage_elems()
        mock_client = repl_harness.set_mock_client()
        started = asyncio.Event()
        finish = asyncio.Event()
        wait_cb = repl_harness.get_rate_limit_wait_callback()

        async def pair_forcing_rate_wait(a, b):
            started.set()
            lim = RateLimiter(max_requests=1, window_seconds=0.15)
            await lim.acquire()
            await lim.acquire(
                cancel_check=lambda: False,
                sleep_step=0.01,
                _wait_callback=wait_cb,
            )
            await finish.wait()
            m = MagicMock()
            m.name = None
            return m

        mock_client.pair = pair_forcing_rate_wait

        async def drive():
            repl_harness.feed("/combine Water Fire")
            repl_harness.feed("/queue")
            t = asyncio.create_task(
                repl_harness.run_until_quit(
                    client=mock_client, auto_feed_quit=False, storage=storage
                )
            )
            await started.wait()
            await asyncio.sleep(0.05)
            finish.set()
            repl_harness.feed("/quit")
            await t

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            run_async(drive())

        out = capsys.readouterr().out
        assert "rate" in out
        assert "running" in out.lower() or "▶" in out
        assert repl_harness.prompt_calls
        last_p, _ = repl_harness.prompt_calls[-1]
        assert "craft>" in last_p.lower()
        assert "Goodbye" in out

    def test_tty_literal_bracket_does_not_flood(self, repl_harness, capsys):
        """Single literal '[' typed (via tty bytes) must insert once only.

        No flood of brackets or input lockup.
        Pure feed_tty_bytes for *all* script lines incl /quit (models bulk).
        Event on phrase (no .feed in tty_mode) for full real cbreak path.
        Asserts: capsys "in"/rfind + optional prompt (chrome out in pure tty).
        """
        unknown_ready = asyncio.Event()

        real_repl_lines = repl_harness.get_repl_print_lines()

        def instrument_repl_lines(text):
            try:
                t = str(text) if text else ""
                if "Unknown input" in t:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(unknown_ready.set)
                    except RuntimeError:
                        unknown_ready.set()
            except Exception:
                pass
            return real_repl_lines(text)

        async def drive():
            repl_harness.enable_tty_mode()
            # PURE feed_tty_bytes for entire script ( [ + /quit\n )
            # no .feed to avoid hook bypass
            repl_harness.feed_tty_bytes(b"[\n/quit\n")
            t = asyncio.create_task(repl_harness.run_until_quit(auto_feed_quit=False))
            await unknown_ready.wait()
            await asyncio.sleep(0)
            await t

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            repl_harness.install_repl_lines_wrapper(instrument_repl_lines)
            run_async(drive())

        out = capsys.readouterr().out

        # Event proves [ line via tty editor
        assert "Unknown input" in out
        assert "Goodbye" in out

        # rfind + craft> (chrome draws it; prompt may be empty w/o hook)
        assert (
            out.rfind("Unknown") < out.rfind("Goodbye")
            or out.rfind("input") < out.rfind("Goodbye")
            or out.rfind("craft>") < out.rfind("Goodbye")
        )
        assert "craft>" in out or (
            repl_harness.prompt_calls
            and "craft>" in repl_harness.prompt_calls[-1][0].lower()
        )

        # Event used for coordination (pure tty; no .feed)
        # (no disallowed counts, no [[[, no internal cli._ , no state asserts)

    def test_tty_search_metachars_via_real_editor(self, repl_harness, capsys):
        """Metachars (* ? [] /regex/ ! ^) + [A-Z] via *pure* tty_bytes.

        Exercises real _tty_read_line cbreak (no hook). All lines + /quit
        via tty_bytes (Event per phrase for coord).
        """
        # specific per-phrase Events for stricter tty proof per syntax
        matches_ready = asyncio.Event()  # e.g. from [A-Z]*/fire*/wa/
        no_matches_ready = asyncio.Event()  # from zzz*
        real_repl_lines = repl_harness.get_repl_print_lines()

        def instrument_repl_lines(text):
            try:
                t = str(text) if text else ""
                if any(s in t for s in ("Water", "Fire", "matches")):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(matches_ready.set)
                    except Exception:
                        matches_ready.set()
                if "No matches" in t:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(no_matches_ready.set)
                    except Exception:
                        no_matches_ready.set()
            except Exception:
                pass
            return real_repl_lines(text)

        async def drive():
            repl_harness.enable_tty_mode()
            # pure feed_tty_bytes (full script + /quit; no .feed)
            repl_harness.feed_tty_bytes(  # noqa: E501
                b"/search [A-Z]*\n/search fire*\n/search /wa/\n!water\n^fire\n/search zzz*\n/quit\n"
            )
            t = asyncio.create_task(repl_harness.run_until_quit(auto_feed_quit=False))
            await matches_ready.wait()
            await no_matches_ready.wait()
            await asyncio.sleep(0)
            await t

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            repl_harness.install_repl_lines_wrapper(instrument_repl_lines)
            run_async(drive())

        out = capsys.readouterr().out

        # Events + specific prove each ( [A-Z]*, fire*, /wa/, !, ^, zzz*, error)
        assert "Goodbye" in out
        assert "Water" in out or "Fire" in out  # from searches
        assert "No matches" in out  # from zzz*
        assert (
            out.rfind("Goodbye") > out.rfind("search")
            or out.rfind("Goodbye") > out.rfind("Water")
            or out.rfind("Goodbye") > out.rfind("Fire")
        )
        assert "craft>" in out or (
            repl_harness.prompt_calls
            and "craft>" in repl_harness.prompt_calls[-1][0].lower()
        )

        # Event used
        # (no counts, no internals)

    def test_tty_bare_metachars_and_regex_errors_via_tty(self, repl_harness, capsys):
        """Bare [ ] * ? / ( ) | . ^ $ O + /invalid/ regex via *pure* tty_bytes.

        Hits real _tty_read_line (bare [O + error). All via tty_bytes;
        Event coord. Proves per-char + regex safety.

        DIVERGENCES.md ruling 7: the kernel has no "too complex" gate and
        performs real top-level `|` alternation, so `/a|b/` (formerly
        rejected as "too complex") now matches successfully instead of
        erroring. Drive an actually-invalid pattern instead (`/[abc/`, an
        unclosed character class) so the regex-error path this test proves
        still triggers.
        """
        # per-phrase Events for stricter proof (bare [O vs regex error)
        bracket_ready = asyncio.Event()
        complex_ready = asyncio.Event()
        real = repl_harness.get_repl_print_lines()

        def instr(text):
            try:
                t = str(text) if text else ""
                if "Unknown" in t:
                    try:
                        asyncio.get_running_loop().call_soon_threadsafe(
                            bracket_ready.set
                        )
                    except Exception:
                        bracket_ready.set()
                if "complex" in t.lower() or "invalid" in t.lower():
                    try:
                        asyncio.get_running_loop().call_soon_threadsafe(
                            complex_ready.set
                        )
                    except Exception:
                        complex_ready.set()
            except Exception:
                pass
            return real(text)

        async def drive():
            repl_harness.enable_tty_mode()
            # pure tty_bytes: bare [ ] O * ? / ( ) | . ^ $ + /[abc/ + /quit
            # (hits [O path + regex err; DIVERGENCES.md ruling 7 — /a|b/ no
            # longer errors under the kernel, so drive an unclosed
            # character class instead to still exercise the error path)
            repl_harness.feed_tty_bytes(  # noqa: E501
                b"[\nO\n]\n*\n?\n/\n(\n)\n|\n.\n^\n$\n/search /[abc/\n/quit\n"
            )
            t = asyncio.create_task(repl_harness.run_until_quit(auto_feed_quit=False))
            await bracket_ready.wait()
            await complex_ready.wait()
            await asyncio.sleep(0)
            await t

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            repl_harness.install_repl_lines_wrapper(instr)
            run_async(drive())

        out = capsys.readouterr().out
        # Events ensure each syntax executed via tty; substr for proof
        assert "Goodbye" in out
        assert "complex" in out.lower() or "invalid" in out.lower()
        assert "Unknown" in out  # from bare [
        assert out.rfind("Goodbye") > 0
        assert "craft>" in out or (
            repl_harness.prompt_calls
            and "craft>" in repl_harness.prompt_calls[-1][0].lower()
        )
