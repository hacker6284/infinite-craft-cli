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

        with patch.object(cli.relay_client, "take_bounties", return_value=(bounties, 0)), \
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
        with patch.object(cli.relay_client, "take_bounties", return_value=(bounties, 0)), \
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
            status = run_async(cli._bounty_cycle(client, storage))
            take.assert_not_called()
        assert status == "blocked"

    def test_cycle_returns_worked_with_rate_to_spare(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()  # mock limiter reports 60 free
        client.pair.return_value = MockElement("Dust", "")
        storage = make_mock_storage()
        bounties = [{"kind": "pair", "first": "Earth", "second": "Wind"}]
        with patch.object(cli.relay_client, "take_bounties", return_value=(bounties, 0)), \
             patch.object(cli.relay_client, "contribute", return_value=1):
            status = run_async(cli._bounty_cycle(client, storage))
        # Full batch served with rate left → the worker will poll again now.
        assert status == "worked"

    def test_cycle_blocked_when_rate_limited_claims_nothing(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        # Window drained → no slot available.
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "take_bounties") as take:
            status = run_async(cli._bounty_cycle(client, storage))
            take.assert_not_called()  # don't claim work we can't do
        assert status == "blocked"

    def test_cycle_returns_empty_when_board_empty(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "take_bounties", return_value=([], 0)):
            status = run_async(cli._bounty_cycle(client, storage))
        assert status == "empty"

    def test_cycle_stops_serving_when_rate_runs_out_midbatch(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Dust", "")
        # First availability check True (serve pair 1), then drained.
        client._rate_limiter.chrome_snapshot.side_effect = [
            (60, 60, 1000),  # cycle entry gate
            (60, 60, 1000),  # before pair 1 → serve
            (0, 60, 500),    # before pair 2 → stop
        ]
        storage = make_mock_storage()
        bounties = [
            {"kind": "pair", "first": "Earth", "second": "Wind"},
            {"kind": "pair", "first": "Fire", "second": "Water"},
        ]
        with patch.object(cli.relay_client, "take_bounties", return_value=(bounties, 0)), \
             patch.object(cli.relay_client, "contribute", return_value=1):
            status = run_async(cli._bounty_cycle(client, storage))
        assert client.pair.await_count == 1  # only the first, then backed off
        assert status == "blocked"

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


class TestHiveAwareWait:
    def test_returns_when_hive_fills_the_miss(self):
        """The payoff path: rate-limited, but the fleet fills the pair — we
        resolve it from cache for free instead of spending a slot."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)  # drained
        storage = make_mock_storage()
        a, b = MockElement("A"), MockElement("B")
        key = _nul_key(cli, "A", "B")
        with patch.object(cli.relay_client, "lookup", return_value={key: ("AB", "")}):
            run_async(cli._hive_aware_wait(client, [(a, b)], [(a, b)]))
        assert cli.craft.pair_key("A", "B") in cli._pair_cache  # filled, no slot spent

    def test_returns_immediately_when_a_slot_is_free(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()  # 60 free
        a, b = MockElement("A"), MockElement("B")
        with patch.object(cli.relay_client, "lookup") as look:
            run_async(cli._hive_aware_wait(client, [(a, b)], [(a, b)]))
            look.assert_not_called()  # slot available → no need to drain/wait

    def test_noop_when_relay_off(self):
        import infinite_craft_cli.cli as cli

        cli._relay_user_on = False
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)
        a, b = MockElement("A"), MockElement("B")
        with patch.object(cli.relay_client, "lookup") as look:
            run_async(cli._hive_aware_wait(client, [(a, b)], [(a, b)]))
            look.assert_not_called()

    def test_returns_when_batch_already_cached(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)
        cli._pair_cache[cli.craft.pair_key("A", "B")] = MockElement("AB")
        a, b = MockElement("A"), MockElement("B")
        with patch.object(cli.relay_client, "lookup") as look:
            run_async(cli._hive_aware_wait(client, [(a, b)], [(a, b)]))
            look.assert_not_called()  # nothing unresolved → don't wait


class TestReviewFixes:
    def test_cycle_serving_nothing_reports_blocked_not_worked(self):
        """F1: a cycle that claims work but serves none (non-429 neal outage)
        must not return 'worked', or the eager worker skips its backoff."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()  # 60 free
        client.pair.side_effect = RuntimeError("neal 503")  # generic outage
        storage = make_mock_storage()
        bounties = [{"kind": "pair", "first": "Earth", "second": "Wind"}]
        with patch.object(cli.relay_client, "take_bounties", return_value=(bounties, 0)), \
             patch.object(cli.relay_client, "contribute", return_value=1) as contrib:
            status = run_async(cli._bounty_cycle(client, storage))
        assert cli._bounties_worked == 0
        assert not contrib.called
        assert status == "blocked"  # → worker backs off, no budget burst

    def test_cycle_claims_only_available_slots(self):
        """F3: with 3 free slots, claim at most 3 (not 5) so we don't lock
        bounties we'd abandon to their claim-TTL."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (3, 60, 1000)
        client.pair.return_value = MockElement("X", "")
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "take_bounties", return_value=([], 0)) as take, \
             patch.object(cli.relay_client, "contribute", return_value=1):
            run_async(cli._bounty_cycle(client, storage))
        take.assert_called_once_with(3)  # kernel bounty_claim_limit(left=3)

    def test_worker_eager_polls_after_worked_then_backs_off(self):
        """Eager cadence: 'worked' → immediate re-poll (sleep 0); anything
        else → the poll interval."""
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        client = make_mock_client()
        storage = make_mock_storage()
        poll = cli.craft.bounty_poll_interval_ms() / 1000.0

        def last_sleep_for(status_val):
            sleeps = []

            async def fake_sleep(secs):
                sleeps.append(secs)
                raise asyncio.CancelledError()  # break after the first sleep

            with patch.object(cli, "_bounty_cycle", new=AsyncMock(return_value=status_val)), \
                 patch("infinite_craft_cli.cli.asyncio.sleep", side_effect=fake_sleep):
                with pytest.raises(asyncio.CancelledError):
                    run_async(cli._bounty_worker(client, storage))
            return sleeps[-1]

        assert last_sleep_for("worked") == 0  # immediate re-poll
        assert last_sleep_for("empty") == poll  # back off
        assert last_sleep_for("blocked") == poll

    def test_hive_aware_wait_ticks_then_exits_on_cooldown(self):
        """F4/coverage: the load-bearing loop path — no slot, hive doesn't
        fill — sleeps a tick and re-loops, exiting when cooldown trips."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)  # never a slot
        storage = make_mock_storage()
        a, b = MockElement("A"), MockElement("B")
        ticks = {"n": 0}

        async def fake_tick(_secs):
            ticks["n"] += 1
            if ticks["n"] >= 2:
                cli._cooldown_until = cli.time.time() + 3600  # trip cooldown → exit
            return False  # not cancelled

        # hive never fills (lookup returns no hit for this pair)
        with patch.object(cli.relay_client, "lookup", return_value={}), \
             patch.object(cli, "_sleep_cancellable_async", side_effect=fake_tick):
            run_async(cli._hive_aware_wait(client, [(a, b)], [(a, b)]))
        assert ticks["n"] >= 2  # it actually looped-and-slept, then exited



class TestBountySyncHeartbeat:
    """The demand heartbeat: leases are renewed only while a run is alive,
    so termination on every exit path is the load-bearing property — a
    leaked heartbeat renews zombie bounties other users spend real rate on."""

    def _pairs(self, n):
        return [(MockElement(f"A{i}"), MockElement(f"B{i}")) for i in range(n)]

    # ── the coroutine's own exit conditions ──────────────────────────

    def test_heartbeat_stops_on_relay_drop(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()  # limiter reports (60, 60) → horizon 120
        pairs = self._pairs(130)  # 10 misses beyond the horizon
        with patch.object(cli.relay_client, "sync_bounties", return_value=None) as sync:
            run_async(cli._bounty_sync_heartbeat(client, pairs, [0]))
        sync.assert_called_once()
        assert cli._relay_reachable is False  # drop marked, tier restored

    def test_heartbeat_syncs_exact_beyond_horizon_slice(self):
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        _relay_on(cli)
        client = make_mock_client()
        pairs = self._pairs(130)
        resp = {"results": {}, "posted": 10, "renewed": 0}
        with patch.object(cli.relay_client, "sync_bounties", return_value=resp) as sync, \
             patch.object(cli, "_sleep_cancellable_async", new=AsyncMock(return_value=True)):
            run_async(cli._bounty_sync_heartbeat(client, pairs, [0]))
        sync.assert_called_once()
        sent = sync.call_args.args[0]
        # Exactly the prioritized tail past horizon = left + max = 120.
        assert sent == [(f"A{i}", f"B{i}") for i in range(120, 130)]

    def test_heartbeat_noop_when_misses_fit_horizon(self):
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        _relay_on(cli)
        client = make_mock_client()
        pairs = self._pairs(5)  # well inside the horizon
        with patch.object(cli.relay_client, "sync_bounties") as sync, \
             patch.object(cli, "_sleep_cancellable_async", new=AsyncMock(return_value=True)):
            run_async(cli._bounty_sync_heartbeat(client, pairs, [0]))
        sync.assert_not_called()

    def test_heartbeat_exits_on_cooldown(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        cli._cooldown_until = cli.time.time() + 3600
        with patch.object(cli.relay_client, "sync_bounties") as sync:
            run_async(cli._bounty_sync_heartbeat(client, self._pairs(130), [0]))
        sync.assert_not_called()

    def test_one_shot_posting_is_gone(self):
        import infinite_craft_cli.cli as cli

        assert not hasattr(cli.relay_client, "post_bounties")

    # ── _combine_pairs owns the task: cancelled on EVERY exit path ───

    def _run_combine_with_fake_heartbeat(self, cli, client, storage, pairs,
                                         expect_error=None):
        state = {}

        async def fake_hb(_client, _pairs, _run_pos):
            state["started"] = True
            try:
                await asyncio.sleep(3600)  # would outlive any run
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        with patch.object(cli, "_bounty_sync_heartbeat", new=fake_hb), \
             patch.object(cli.relay_client, "lookup", return_value={}), \
             patch.object(cli.relay_client, "contribute", return_value=1):
            if expect_error is not None:
                with pytest.raises(expect_error):
                    run_async(cli._combine_pairs(client, storage, pairs))
            else:
                run_async(cli._combine_pairs(client, storage, pairs))
        return state

    def test_run_cancels_heartbeat_on_normal_completion(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "")
        storage = make_mock_storage()
        state = self._run_combine_with_fake_heartbeat(
            cli, client, storage, self._pairs(3)
        )
        assert state.get("started") and state.get("cancelled")

    def test_run_cancels_heartbeat_on_user_cancel(self):
        """Cancel before the first batch: the loop breaks without ever
        yielding, so the heartbeat is cancelled before its body first runs —
        assert the real property (no live heartbeat survives the run)."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "")
        storage = make_mock_storage()
        cli._cancelled = True  # cancel before the first batch

        async def scenario():
            with patch.object(cli.relay_client, "lookup", return_value={}), \
                 patch.object(cli.relay_client, "contribute", return_value=1), \
                 patch.object(cli.relay_client, "sync_bounties", return_value=None):
                await cli._combine_pairs(client, storage, self._pairs(3))
            return [
                t for t in asyncio.all_tasks()
                if not t.done() and "_bounty_sync_heartbeat" in repr(t)
            ]

        assert run_async(scenario()) == []

    def test_run_cancels_heartbeat_on_429_cooldown(self):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.client import NealRateLimited

        _relay_on(cli)
        client = make_mock_client()
        client.pair.side_effect = NealRateLimited()
        storage = make_mock_storage()
        state = self._run_combine_with_fake_heartbeat(
            cli, client, storage, self._pairs(3)
        )
        assert state.get("started") and state.get("cancelled")

    def test_run_cancels_heartbeat_on_unexpected_exception(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "")
        storage = make_mock_storage()
        storage.add.side_effect = RuntimeError("disk full")
        state = self._run_combine_with_fake_heartbeat(
            cli, client, storage, self._pairs(3), expect_error=RuntimeError
        )
        assert state.get("started") and state.get("cancelled")

    # ── idle worker pacing on the relay hint ─────────────────────────

    def test_local_refusal_clears_stale_hint(self):
        """A worker refused locally (preempted / no slots) must not keep
        spinning on an old 2s hint — local refusals reset it to the default."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        cli._bounty_poll_ms = 2000
        cli._current_command = "/permute *"  # preempted
        assert run_async(cli._bounty_cycle(client, storage)) == "blocked"
        assert cli._bounty_poll_ms == 0
        cli._current_command = ""
        cli._bounty_poll_ms = 2000
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)
        assert run_async(cli._bounty_cycle(client, storage)) == "blocked"
        assert cli._bounty_poll_ms == 0

    def test_cycle_stores_poll_hint(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "take_bounties", return_value=([], 2500)):
            status = run_async(cli._bounty_cycle(client, storage))
        assert status == "empty"
        assert cli._bounty_poll_ms == 2500

    def test_worker_sleeps_poll_hint_when_idle(self):
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        client = make_mock_client()
        storage = make_mock_storage()
        cli._bounty_poll_ms = 2000
        sleeps = []

        async def fake_sleep(secs):
            sleeps.append(secs)
            raise asyncio.CancelledError()

        with patch.object(cli, "_bounty_cycle", new=AsyncMock(return_value="empty")), \
             patch("infinite_craft_cli.cli.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                run_async(cli._bounty_worker(client, storage))
        assert sleeps[-1] == 2.0  # the hint, not the 10s kernel default

class TestRelayRecovery:
    """The 2.4.1 field bug: 'unreachable' was a one-way door, so one relay
    nap silenced idle serving forever. The worker now probes /health on the
    kernel retry cadence and restores the tier."""

    def test_worker_probe_restores_tier(self):
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        cli._relay_user_on = True
        cli._relay_reachable = False  # a past failure deafened the tier

        async def fake_sleep(secs):
            raise asyncio.CancelledError()  # one worker iteration

        with patch.object(cli.relay_client, "ping", return_value={"ok": True}) as ping, \
             patch.object(cli, "_bounty_cycle", new=AsyncMock(return_value="blocked")), \
             patch("infinite_craft_cli.cli.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                run_async(cli._bounty_worker(make_mock_client(), make_mock_storage()))
        ping.assert_called_once()
        assert cli._relay_reachable is True

    def test_failed_probe_stays_down_and_respects_cadence(self):
        import infinite_craft_cli.cli as cli

        cli._relay_user_on = True
        cli._relay_reachable = False
        with patch.object(cli.relay_client, "ping", return_value=None) as ping:
            run_async(cli._relay_retry_probe())
            assert cli._relay_reachable is False
            # Second probe inside the retry window: no ping fired.
            run_async(cli._relay_retry_probe())
        ping.assert_called_once()

    def test_no_probe_when_tier_healthy_or_user_off(self):
        import infinite_craft_cli.cli as cli

        with patch.object(cli.relay_client, "ping") as ping:
            cli._relay_user_on = True
            cli._relay_reachable = True
            run_async(cli._relay_retry_probe())
            cli._relay_user_on = False
            cli._relay_reachable = False
            run_async(cli._relay_retry_probe())
        ping.assert_not_called()
