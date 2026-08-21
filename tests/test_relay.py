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
            # contribute is fire-and-forget on the running loop — drain it
            # INSIDE the same loop, or it dies with the loop (CI caught this).
            async def scenario():
                result = await cli._cached_pair(
                    client, storage, MockElement("Fire"), MockElement("Water")
                )
                for t in list(cli._relay_bg_tasks):
                    await t
                return result

            result = run_async(scenario())
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
            # do_relay("on") spawns the warmup task — keep it off the network.
            with patch.object(cli, "_relay_spawn_warmup"):
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


class TestCooldown:
    def test_429_trips_cooldown_and_gates_neal(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.side_effect = cli.NealRateLimited()

        with patch.object(cli.relay_client, "lookup", return_value={}):
            with pytest.raises(cli.NealRateLimited):
                run_async(
                    cli._cached_pair(client, storage, MockElement("Fire"), MockElement("Water"))
                )
        assert cli._cooling()
        assert cli._cooldown_strikes == 1

        # While cooling, a fresh miss never reaches neal — it raises at the gate.
        client.pair.reset_mock()
        client.pair.side_effect = None
        client.pair.return_value = MockElement("Steam")
        with patch.object(cli.relay_client, "lookup", return_value={}):
            with pytest.raises(cli.NealRateLimited):
                run_async(
                    cli._cached_pair(client, storage, MockElement("A"), MockElement("B"))
                )
        client.pair.assert_not_awaited()

    def test_second_trip_while_cooling_is_a_noop(self):
        """M1: concurrent 429s from one ban must not inflate the strike count."""
        import infinite_craft_cli.cli as cli

        cli._trip_cooldown()
        first_until = cli._cooldown_until
        assert cli._cooldown_strikes == 1
        cli._trip_cooldown()  # still cooling → ignored
        assert cli._cooldown_strikes == 1
        assert cli._cooldown_until == first_until

    def test_strikes_double_after_cooldown_expires(self):
        import infinite_craft_cli.cli as cli

        cli._trip_cooldown()
        first_dur = cli._cooldown_until - cli.time.time()
        # Simulate the first cooldown fully elapsing, then a new ban.
        cli._cooldown_until = 0.0
        cli._trip_cooldown()
        assert cli._cooldown_strikes == 2
        assert (cli._cooldown_until - cli.time.time()) > first_dur  # 2h → 4h

    def test_adopted_cooldown_is_clamped_to_max(self):
        """M2: a garbage relay can't park us offline past the 8h kernel max."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        # Relay claims we're cooled until the year 9999.
        cli.relay_client.last_hive["cooledUntil"] = 253402300799000
        cli._relay_apply_hive(client)
        max_ms = cli.craft.cooldown_duration_ms(3)
        assert cli._cooling()
        assert cli._cooldown_until <= cli.time.time() + max_ms / 1000.0 + 1

    def test_effective_max_restored_on_relay_off_and_drop(self):
        """M3: dropping the tier restores the full per-IP budget."""
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.ratelimit import RateLimiter

        client = make_mock_client()
        client._rate_limiter = RateLimiter(max_requests=60)
        cli._active_client = client
        client._rate_limiter.set_effective_max(20)
        assert client._rate_limiter.chrome_snapshot()[1] == 20
        # Relay goes unreachable → restore.
        cli._relay_mark_unreachable()
        assert client._rate_limiter.chrome_snapshot()[1] == 60
        # Shrink again, then /relay off → restore.
        client._rate_limiter.set_effective_max(15)
        cli._relay_user_on = True
        cli._relay_reachable = True
        with patch.object(cli, "_relay_spawn_warmup"):
            cli.do_relay("off", make_mock_storage())
        assert client._rate_limiter.chrome_snapshot()[1] == 60


class TestBudgetSplit:
    def test_hive_peers_shrink_effective_max(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        # Real limiter so set_effective_max has something to gate.
        from infinite_craft_cli.ratelimit import RateLimiter
        client._rate_limiter = RateLimiter(max_requests=60)
        storage = make_mock_storage()
        key = _nul_key(cli, "Fire", "Water")

        def lookup_with_peers(pairs):
            cli.relay_client.last_hive["peers"] = 3
            return {key: ("Steam", "")}

        with patch.object(cli.relay_client, "lookup", side_effect=lookup_with_peers):
            run_async(
                cli._cached_pair(client, storage, MockElement("Fire"), MockElement("Water"))
            )
        # 60 / 3 peers = 20.
        assert client._rate_limiter.chrome_snapshot()[1] == 20

    def test_sibling_cooldown_adopted_from_envelope(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        future = (cli.time.time() + 3600) * 1000
        key = _nul_key(cli, "Fire", "Water")

        def lookup_with_cool(pairs):
            cli.relay_client.last_hive["cooledUntil"] = future
            return {key: ("Steam", "")}

        with patch.object(cli.relay_client, "lookup", side_effect=lookup_with_cool):
            run_async(
                cli._cached_pair(client, storage, MockElement("Fire"), MockElement("Water"))
            )
        assert cli._cooling()


class TestBountyWorker:
    def test_cycle_serves_then_contributes_via_neal(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Dust", "")
        storage = make_mock_storage()
        bounties = [{"kind": "pair", "first": "Earth", "second": "Wind"}]

        with patch.object(cli.relay_client, "take_bounties", return_value=bounties), \
             patch.object(cli.relay_client, "contribute", return_value=1) as contrib:
            run_async(cli._bounty_cycle(client, storage))

        client.pair.assert_awaited_once()
        assert client.pair.await_args.kwargs.get("fleet") is True
        assert contrib.called
        assert cli._bounties_worked == 1
        assert cli._bounty_progress is None  # reset in finally

    def test_bounty_always_hits_neal_even_when_locally_cached(self):
        """Poison-containment: a pair we already hold locally must still be
        re-derived from neal when served as a bounty, never answered from
        the local cache."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Real", "")
        storage = make_mock_storage()
        # Pre-seed local cache with a (possibly poisoned) value.
        cli._pair_cache[cli.craft.pair_key("Earth", "Wind")] = MockElement("Stale", "")
        bounties = [{"kind": "pair", "first": "Earth", "second": "Wind"}]

        contributed = []
        with patch.object(cli.relay_client, "take_bounties", return_value=bounties), \
             patch.object(cli.relay_client, "contribute", side_effect=lambda e: contributed.extend(e) or 1):
            run_async(cli._bounty_cycle(client, storage))

        client.pair.assert_awaited_once()  # hit neal despite the cache
        # Contributed the FRESH neal value, not the stale cache entry.
        assert contributed[0][2] == "Real"

    def test_cycle_noop_when_preempted(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        cli._current_command = "/permute *"
        with patch.object(cli.relay_client, "take_bounties") as take:
            run_async(cli._bounty_cycle(client, storage))
            take.assert_not_called()

    def test_worker_preempted_by_running_command(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        cli._current_command = "/permute *"
        assert cli._bounty_preempted() is True
        cli._current_command = ""
        assert cli._bounty_preempted() is False

    def test_worker_preempted_while_cooling(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        cli._current_command = ""
        assert cli._bounty_preempted() is False
        cli._trip_cooldown()  # never serve the hive while banned
        assert cli._bounty_preempted() is True

    def test_worker_preempted_when_relay_off(self):
        import infinite_craft_cli.cli as cli

        cli._current_command = ""
        cli._relay_user_on = False
        assert cli._bounty_preempted() is True
