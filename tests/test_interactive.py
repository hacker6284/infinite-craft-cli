"""Tests for interactive mode command parsing and dispatch."""

import asyncio
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import MockElement, make_mock_client, make_mock_storage

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30.0))


@pytest.fixture(autouse=True)
def clear_caches(tmp_path, request):
    import infinite_craft_cli.cli as cli

    def _clear():
        try:
            cli._reset_test_state()
        except Exception:
            pass

    _clear()
    request.addfinalizer(_clear)
    # Use a temp discoveries file so we don't load real user data
    with patch(
        "infinite_craft_cli.cli.DISCOVERIES_PATH", str(tmp_path / "discoveries.json")
    ):
        yield
    _clear()


class TestHostDispatch:
    """Thin host dispatch/rejection smokes (not full interactive matrix)."""

    def test_plus_calls_do_combine(self, capsys):
        from infinite_craft_cli.cli import interactive_mode

        mock_client = make_mock_client()
        result = MockElement("Steam", "💨")
        mock_client.pair = AsyncMock(return_value=result)

        with patch("infinite_craft_cli.cli.InfiniteCraftClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "infinite_craft_cli.cli.DiscoveryStorage",
                return_value=make_mock_storage(),
            ):
                with patch("infinite_craft_cli.cli._record_recipes_batch"):
                    with patch(
                        "infinite_craft_cli.cli._prompt_input",
                        side_effect=["Water + Fire", "/quit"],
                    ):
                        run_async(interactive_mode())
        out = capsys.readouterr().out
        assert "Steam" in out

    def test_spaced_plus_pipe_rejected(self, capsys):
        from infinite_craft_cli.cli import interactive_mode

        mock_client = make_mock_client()
        with patch("infinite_craft_cli.cli.InfiniteCraftClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "infinite_craft_cli.cli.DiscoveryStorage",
                return_value=make_mock_storage(),
            ):
                with patch(
                    "infinite_craft_cli.cli._prompt_input",
                    side_effect=["Water + | Fire", "/quit"],
                ):
                    run_async(interactive_mode())
        out = capsys.readouterr().out
        # v2.0: spaced `+ |` is a script parse error (size bar after combine)
        assert "Script error" in out

    def test_unknown_slash_rejected(self, capsys):
        from infinite_craft_cli.cli import interactive_mode

        mock_client = make_mock_client()
        with patch("infinite_craft_cli.cli.InfiniteCraftClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "infinite_craft_cli.cli.DiscoveryStorage",
                return_value=make_mock_storage(),
            ):
                with patch(
                    "infinite_craft_cli.cli._prompt_input",
                    side_effect=["/notacommand", "/quit"],
                ):
                    run_async(interactive_mode())
        out = capsys.readouterr().out
        assert "Unknown command" in out


class TestEnqueueRejection:
    """Host enqueue validation messages (kernel owns rules; host must surface them)."""

    @pytest.mark.parametrize(
        "bad_line,expected_substr",
        [
            ("/combine Water + Fire", "positional args"),
            # v2.0 always-script: shorthand lines are scripts; parse errors
            # come from the kernel script parser.
            ("Water + | Fire", "Script error"),
            ("Water +", "Script error"),
            ("/permutate", "Usage: /permutate <query>"),
            ("/notacommand", "Unknown command"),
            ("Water +| Fire", "+| was removed"),
            ("x := a* , b* * c*", "Script error"),
        ],
    )
    def test_invalid_commands_rejected_at_enqueue(
        self, bad_line, expected_substr, capsys
    ):
        from infinite_craft_cli.cli import _enqueue_command_line
        import infinite_craft_cli.cli as cli

        storage = make_mock_storage()
        assert not _enqueue_command_line(bad_line, MagicMock(), storage)
        out = capsys.readouterr().out
        assert expected_substr in out
        assert cli._command_queue == []

    def test_multiword_element_reference_enqueues(self, capsys):
        # v2.0: "Water Fire" is a (single) element reference script — it
        # parses and enqueues; resolution failures surface at runtime.
        from infinite_craft_cli.cli import _enqueue_command_line

        storage = make_mock_storage()
        with patch("infinite_craft_cli.cli._ensure_lane_worker"):
            assert _enqueue_command_line("Water Fire", MagicMock(), storage)

    def test_duplicate_rejection(self, capsys):
        from infinite_craft_cli.cli import _enqueue_command_line
        import infinite_craft_cli.cli as cli

        storage = make_mock_storage()
        client = MagicMock()
        with patch("infinite_craft_cli.cli._ensure_lane_worker"):
            assert _enqueue_command_line("/combine Water Fire", client, storage)
            assert not _enqueue_command_line("/combine Water Fire", client, storage)
        out = capsys.readouterr().out
        assert "Already queued" in out

    def test_queue_depth_cap(self, capsys):
        from infinite_craft_cli.cli import _enqueue_command_line
        import infinite_craft_cli.cli as cli

        storage = make_mock_storage()
        client = MagicMock()
        with (
            patch("infinite_craft_cli.cli._ensure_lane_worker"),
            patch("infinite_craft_cli.cli._MAX_QUEUE_DEPTH", 2),
        ):
            assert _enqueue_command_line("/combine Water Fire", client, storage)
            assert _enqueue_command_line("/combine Wind Earth", client, storage)
            assert not _enqueue_command_line("/combine Fire Earth", client, storage)
        out = capsys.readouterr().out
        assert "Queue full" in out


class TestCommandQueueHelpers:
    """Host-facing helpers that are not pure kernel re-exports."""

    def test_do_target_set_show_clear(self):
        from infinite_craft_cli.cli import do_target

        assert "No target" in do_target("")
        assert "Target set" in do_target("Steam")
        assert "Steam" in do_target("")
        assert "cleared" in do_target("clear").lower()
