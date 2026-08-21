"""Tests for the hive-mind relay tier: cache order, fail-open, /relay."""

import asyncio
import sys
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import MockElement, make_mock_storage, make_mock_client

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro, *, timeout: float = 8.0):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


@pytest.fixture(autouse=True)
def clear_caches(request):
    import infinite_craft_cli.cli as cli

    def _clear():
        try:
            cli._reset_test_state()
        except Exception:
            pass

    _clear()
    request.addfinalizer(_clear)
    yield
    _clear()


def _relay_on(cli):
    cli._relay_user_on = True
    cli._relay_reachable = True


def _nul_key(cli, a, b):
    ka, kb = cli.craft.pair_key(a, b)
    return f"{ka}\0{kb}"


class TestCachedPairRelayTier:
    def test_relay_hit_skips_neal(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        key = _nul_key(cli, "Fire", "Water")
        with patch.object(
            cli.relay_client, "lookup", return_value={key: ("Steam", "💨")}
        ) as look:
            result = run_async(
                cli._cached_pair(client, storage, MockElement("Fire"), MockElement("Water"))
            )
        assert result.name == "Steam"
        assert result.emoji == "💨"
        client.pair.assert_not_awaited()
        assert cli._relay_hits == 1
        look.assert_called_once()

    def test_relay_nothing_hit(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        key = _nul_key(cli, "A", "B")
        with patch.object(cli.relay_client, "lookup", return_value={key: (None, "")}):
            result = run_async(
                cli._cached_pair(client, storage, MockElement("A"), MockElement("B"))
            )
        assert result.name is None
        client.pair.assert_not_awaited()

    def test_relay_miss_falls_through_and_contributes(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        fresh = MockElement("Steam", "💨")
        client.pair.return_value = fresh
        with patch.object(cli.relay_client, "lookup", return_value={}), patch.object(
            cli.relay_client, "contribute", return_value=1
        ) as contrib:
            result = run_async(
                cli._cached_pair(client, storage, MockElement("Fire"), MockElement("Water"))
            )
            # contribute is fire-and-forget; drain background tasks
            async def drain():
                for t in list(cli._relay_bg_tasks):
                    await t

            run_async(drain()) if cli._relay_bg_tasks else None
        assert result.name == "Steam"
        client.pair.assert_awaited_once()
        assert contrib.called

    def test_relay_unreachable_fails_open(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        fresh = MockElement("Steam")
        client.pair.return_value = fresh
        with patch.object(cli.relay_client, "lookup", return_value=None):
            result = run_async(
                cli._cached_pair(client, storage, MockElement("Fire"), MockElement("Water"))
            )
        assert result.name == "Steam"
        assert cli._relay_reachable is False
        # Next call skips the relay entirely (no more lookup attempts).
        with patch.object(cli.relay_client, "lookup") as look:
            run_async(
                cli._cached_pair(client, storage, MockElement("X"), MockElement("Y"))
            )
            look.assert_not_called()


class TestCombinePairsHiveSweep:
    def test_batch_sweep_promotes_hive_hits(self, capsys):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        fresh = MockElement("Fresh Thing")
        client.pair.return_value = fresh
        pairs = [
            (MockElement("A"), MockElement("B")),
            (MockElement("Y"), MockElement("Z")),
        ]
        key = _nul_key(cli, "Y", "Z")
        with patch.object(
            cli.relay_client, "lookup", return_value={key: ("Hive Thing", "")}
        ) as look:
            run_async(cli._combine_pairs(client, storage, pairs))
        captured = capsys.readouterr()
        first = [l for l in captured.out.splitlines() if "[1/2]" in l]
        assert first and "Hive Thing" in first[0]
        # Only the true miss reached neal.
        assert client.pair.await_count == 1
        assert cli._relay_hits == 1
        # One batched call for the sweep, not one per pair (the per-pair
        # _cached_pair lookups only fire for pairs the sweep missed).
        assert look.call_count >= 1
        assert sorted(look.call_args_list[0].args[0]) == [("A", "B"), ("Y", "Z")]

    def test_sweep_skipped_when_relay_off(self):
        import infinite_craft_cli.cli as cli

        cli._relay_user_on = False
        client = make_mock_client()
        storage = make_mock_storage()
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing
        pairs = [
            (MockElement("A"), MockElement("B")),
            (MockElement("C"), MockElement("D")),
        ]
        with patch.object(cli.relay_client, "lookup") as look:
            run_async(cli._combine_pairs(client, storage, pairs))
            look.assert_not_called()


class TestRelayCommand:
    def test_toggle_and_status(self):
        import infinite_craft_cli.cli as cli

        storage = make_mock_storage()
        cli._relay_user_on = True
        cli._relay_reachable = True

        async def run():
            off = cli.do_relay("", storage)
            assert "off" in off
            assert not cli._relay_user_on
            status = cli.do_relay("status", storage)
            assert "off" in status
            on = cli.do_relay("on", storage)
            assert "on" in on
            assert cli._relay_user_on
            bad = cli.do_relay("bogus", storage)
            assert "Usage" in bad

        run_async(run())

    def test_ic_relay_env_disables_default(self):
        import infinite_craft_cli.cli as cli

        # conftest sets IC_RELAY=off for the whole suite
        assert cli._relay_default_on() is False
        cli._reset_test_state()
        assert cli._relay_user_on is False


class TestReseedEntries:
    def test_kernel_reseed_shape(self):
        import infinite_craft_cli.cli as cli

        entries = cli.craft.relay_reseed_entries(
            [("Steam", "💨", False)],
            {"Steam": [("Water", "Fire")]},
        )
        assert [tuple(t) for t in entries] == [("Fire", "Water", "Steam", "💨")]
