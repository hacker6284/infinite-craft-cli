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


class TestServeHive:
    """Pull-and-serve, triggered by the beat's work bit — no polling."""

    def test_serves_then_contributes_via_neal(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Dust", "")
        storage = make_mock_storage()
        work = [{"kind": "pair", "first": "Earth", "second": "Wind"}]
        with patch.object(cli.relay_client, "pull_work", return_value=work), \
             patch.object(cli.relay_client, "contribute", return_value=1) as contrib:
            status = run_async(cli._serve_hive(client, storage))
        client.pair.assert_awaited_once()
        assert client.pair.await_args.kwargs.get("fleet") is True
        assert contrib.called
        assert cli._bounties_worked == 1
        assert cli._bounty_progress is None  # reset in finally
        assert status == "worked"

    def test_always_hits_neal_even_when_locally_cached(self):
        """Poison-containment: a locally-cached pair must still be re-derived
        from neal when served, never answered from the local cache."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Real", "")
        storage = make_mock_storage()
        cli._pair_cache[cli.craft.pair_key("Earth", "Wind")] = MockElement("Stale", "")
        work = [{"kind": "pair", "first": "Earth", "second": "Wind"}]
        contributed = []
        with patch.object(cli.relay_client, "pull_work", return_value=work), \
             patch.object(cli.relay_client, "contribute",
                          side_effect=lambda e: contributed.extend(e) or 1):
            run_async(cli._serve_hive(client, storage))
        client.pair.assert_awaited_once()
        assert contributed[0][2] == "Real"

    def test_noop_when_preempted(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        cli._current_command = "/permute *"
        with patch.object(cli.relay_client, "pull_work") as pull:
            status = run_async(cli._serve_hive(client, storage))
            pull.assert_not_called()
        assert status == "blocked"

    def test_blocked_when_rate_limited_pulls_nothing(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "pull_work") as pull:
            status = run_async(cli._serve_hive(client, storage))
            pull.assert_not_called()  # don't claim work we can't do
        assert status == "blocked"

    def test_pull_sized_by_kernel_claim_limit(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (3, 60, 1000)
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "pull_work", return_value=[]) as pull:
            status = run_async(cli._serve_hive(client, storage))
        pull.assert_called_once_with(3)  # kernel bounty_claim_limit(left=3)
        assert status == "empty"

    def test_stops_serving_when_rate_runs_out_midbatch(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Dust", "")
        client._rate_limiter.chrome_snapshot.side_effect = [
            (60, 60, 1000),  # entry gate
            (60, 60, 1000),  # before item 1 → serve
            (0, 60, 500),    # before item 2 → stop
        ]
        storage = make_mock_storage()
        work = [
            {"kind": "pair", "first": "Earth", "second": "Wind"},
            {"kind": "pair", "first": "Fire", "second": "Water"},
        ]
        with patch.object(cli.relay_client, "pull_work", return_value=work), \
             patch.object(cli.relay_client, "contribute", return_value=1):
            status = run_async(cli._serve_hive(client, storage))
        assert client.pair.await_count == 1
        assert status == "blocked"

    def test_serving_nothing_reports_blocked_not_worked(self):
        """A pass that pulls work but serves none (non-429 outage) must not
        report 'worked'."""
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.side_effect = RuntimeError("neal 503")
        storage = make_mock_storage()
        work = [{"kind": "pair", "first": "Earth", "second": "Wind"}]
        with patch.object(cli.relay_client, "pull_work", return_value=work), \
             patch.object(cli.relay_client, "contribute", return_value=1) as contrib:
            status = run_async(cli._serve_hive(client, storage))
        assert cli._bounties_worked == 0
        assert not contrib.called
        assert status == "blocked"

    def test_unreachable_pull_marks_tier_down(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "pull_work", return_value=None):
            status = run_async(cli._serve_hive(client, storage))
        assert status == "unreachable"
        assert cli._relay_reachable is False

    def test_worker_preempted_by_running_command(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        cli._current_command = "/permute *"
        assert cli._bounty_preempted() is True
        cli._current_command = ""
        assert cli._bounty_preempted() is False


class TestBeatWorker:
    """THE one timer: liveness out, work bit back, recovery built in."""

    def _run_iters(self, cli, client, storage, n):
        """Run n beat-loop iterations, then cancel via the sleep."""
        calls = {"n": 0}

        async def fake_sleep(secs):
            calls["n"] += 1
            assert secs == cli.craft.beat_interval_ms() / 1000.0
            if calls["n"] >= n:
                raise asyncio.CancelledError()

        with patch("infinite_craft_cli.cli.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                run_async(cli._beat_worker(client, storage))

    def test_beat_failure_marks_unreachable_but_beats_continue(self):
        import infinite_craft_cli.cli as cli

        cli._relay_user_on = True
        cli._relay_reachable = True
        client = make_mock_client()
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "beat", return_value=None) as beat:
            self._run_iters(cli, client, storage, 2)
        assert beat.call_count == 2  # kept beating while down — beats ARE the probe
        assert cli._relay_reachable is False

    def test_beat_success_restores_tier(self):
        import infinite_craft_cli.cli as cli

        cli._relay_user_on = True
        cli._relay_reachable = False  # a past failure deafened the tier
        client = make_mock_client()
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "beat", return_value=(True, False)):
            self._run_iters(cli, client, storage, 1)
        assert cli._relay_reachable is True

    def test_work_bit_triggers_serve(self):
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        cli._relay_user_on = True
        cli._relay_reachable = True
        client = make_mock_client()
        storage = make_mock_storage()
        serve = AsyncMock(return_value="worked")
        with patch.object(cli.relay_client, "beat", return_value=(True, True)), \
             patch.object(cli, "_serve_hive", new=serve):
            self._run_iters(cli, client, storage, 1)
        serve.assert_awaited_once()

    def test_no_serve_without_work_bit(self):
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        cli._relay_user_on = True
        cli._relay_reachable = True
        client = make_mock_client()
        storage = make_mock_storage()
        serve = AsyncMock()
        with patch.object(cli.relay_client, "beat", return_value=(True, False)), \
             patch.object(cli, "_serve_hive", new=serve):
            self._run_iters(cli, client, storage, 1)
        serve.assert_not_awaited()

    def test_beat_carries_run_id_and_cooldown(self):
        import infinite_craft_cli.cli as cli

        cli._relay_user_on = True
        cli._relay_reachable = True
        cli._run_id = "sess-7"
        cli._cooldown_until = cli.time.time() + 3600
        client = make_mock_client()
        storage = make_mock_storage()
        with patch.object(cli.relay_client, "beat", return_value=(True, False)) as beat:
            self._run_iters(cli, client, storage, 1)
        neal_ok, run_id, cooled_ms = beat.call_args.args
        assert neal_ok is False  # cooling → we cannot usefully reach neal
        assert run_id == "sess-7"
        assert cooled_ms > 0


class TestHiveWaitForSlots:
    def test_returns_immediately_when_slots_cover_and_skips_sync_without_wait(self):
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        _relay_on(cli)
        client = make_mock_client()  # 60 free
        a, b = MockElement("A"), MockElement("B")
        sync = AsyncMock()
        with patch.object(cli, "_hive_run_sync", new=sync):
            run_async(cli._hive_wait_for_slots(client, [(a, b)], [(a, b)]))
        sync.assert_not_awaited()  # no wait happened → start-of-run sync suffices

    def test_returns_when_batch_already_cached(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)
        a, b = MockElement("A"), MockElement("B")
        cli._pair_cache[cli.craft.pair_key("A", "B")] = MockElement("C")
        run_async(cli._hive_wait_for_slots(client, [(a, b)], [(a, b)]))

    def test_noop_when_relay_off(self):
        import infinite_craft_cli.cli as cli

        cli._relay_user_on = False
        client = make_mock_client()
        a, b = MockElement("A"), MockElement("B")
        run_async(cli._hive_wait_for_slots(client, [(a, b)], [(a, b)]))

    def test_sleeps_until_slot_frees_then_syncs_before_spend(self):
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.side_effect = [
            (0, 60, 500),   # no slot → wait
            (60, 60, 1000), # slot freed → sync-before-spend, return
        ]
        client._rate_limiter._timestamps = []
        a, b = MockElement("A"), MockElement("B")
        sync = AsyncMock()
        with patch.object(cli, "_hive_run_sync", new=sync), \
             patch.object(cli, "_sleep_cancellable_async", new=AsyncMock(return_value=False)):
            run_async(cli._hive_wait_for_slots(client, [(a, b)], [(a, b)]))
        sync.assert_awaited_once()  # one wake → one full catch-up sync

    def test_exits_on_cooldown_mid_wait(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)
        client._rate_limiter._timestamps = []
        a, b = MockElement("A"), MockElement("B")
        waits = {"n": 0}

        async def fake_sleep(_secs):
            waits["n"] += 1
            if waits["n"] >= 2:
                cli._cooldown_until = cli.time.time() + 3600
            return False

        with patch.object(cli, "_sleep_cancellable_async", side_effect=fake_sleep), \
             patch.object(cli.relay_client, "lookup", return_value={}), \
             patch.object(cli.relay_client, "sync_bounties", return_value={"results": {}, "posted": 0}):
            run_async(cli._hive_wait_for_slots(client, [(a, b)], [(a, b)]))
        assert waits["n"] >= 2

    def test_fleet_fill_unblocks_without_a_slot(self):
        """3.0.1 field fix: a rate-limited batch whose pairs the fleet has
        answered must resolve within ~a beat, not wait for a local slot."""
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        _relay_on(cli)
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)  # never a slot
        client._rate_limiter._timestamps = []
        a, b = MockElement("A"), MockElement("B")
        key = cli.craft.pair_key("A", "B")
        cli._run_posted_once = True  # keep the 🐝 line out of this test
        fills = [
            {"results": {}, "posted": 1},
            {"results": {f"{key[0]}\0{key[1]}": ("Fleet Thing", "🐝")}, "posted": 0},
        ]
        with patch.object(cli.relay_client, "sync_bounties", side_effect=fills) as sync, \
             patch.object(cli, "_sleep_cancellable_async", new=AsyncMock(return_value=False)):
            run_async(cli._hive_wait_for_slots(client, [(a, b)], [(a, b)]))
        assert sync.call_count == 2  # the ONE full catch-up call, each wake
        got = cli._pair_cache.get(key)
        assert got is not None and got.name == "Fleet Thing"
        assert cli._relay_hits == 1  # the bee ticks live, mid-wait


class TestHiveRunSync:
    def _pairs(self, n):
        return [(MockElement(f"A{i}"), MockElement(f"B{i}")) for i in range(n)]

    def test_head_looked_up_tail_offered_with_run_id(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        cli._run_id = "sess-1"
        client = make_mock_client()  # 60 free
        pairs = self._pairs(130)
        with patch.object(cli.relay_client, "lookup", return_value={}) as look, \
             patch.object(cli.relay_client, "sync_bounties",
                          return_value={"results": {}, "posted": 70}) as sync:
            run_async(cli._hive_run_sync(client, pairs))
        # Head = the 60 we can spend on now; tail = everything else, offered.
        assert look.call_args.args[0] == [(f"A{i}", f"B{i}") for i in range(60)]
        sent, run_id = sync.call_args.args
        assert sent == [(f"A{i}", f"B{i}") for i in range(60, 130)]
        assert run_id == "sess-1"

    def test_no_offer_when_slots_cover_everything(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        pairs = self._pairs(5)
        with patch.object(cli.relay_client, "lookup", return_value={}) as look, \
             patch.object(cli.relay_client, "sync_bounties") as sync:
            run_async(cli._hive_run_sync(client, pairs))
        assert len(look.call_args.args[0]) == 5
        sync.assert_not_called()

    def test_posted_line_prints_once_per_run(self, capsys):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        cli._run_id = "sess-1"
        client = make_mock_client()
        client._rate_limiter.chrome_snapshot.return_value = (0, 60, 500)
        pairs = self._pairs(3)
        with patch.object(cli.relay_client, "sync_bounties",
                          return_value={"results": {}, "posted": 3}):
            run_async(cli._hive_run_sync(client, pairs))
            run_async(cli._hive_run_sync(client, pairs))
        out = capsys.readouterr().out
        assert out.count("posted 3 bounties") == 1

    def test_relay_drop_marks_unreachable(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        with patch.object(cli.relay_client, "lookup", return_value=None):
            run_async(cli._hive_run_sync(client, self._pairs(5)))
        assert cli._relay_reachable is False

    def test_polling_api_is_gone(self):
        import infinite_craft_cli.cli as cli

        assert not hasattr(cli.relay_client, "take_bounties")
        assert not hasattr(cli.relay_client, "post_bounties")
        assert not hasattr(cli.relay_client, "state_provider")


class TestRunIdLifecycle:
    """Dropping the run id from beats IS cancellation — it must clear on
    every exit path of _combine_pairs, and be asserted while it runs."""

    def _pairs(self, n):
        return [(MockElement(f"A{i}"), MockElement(f"B{i}")) for i in range(n)]

    def _run(self, cli, client, storage, pairs, expect_error=None):
        seen = {}

        async def spy_sync(_client, _remaining):
            seen["run_id"] = cli._run_id

        with patch.object(cli, "_hive_run_sync", new=spy_sync), \
             patch.object(cli.relay_client, "lookup", return_value={}), \
             patch.object(cli.relay_client, "contribute", return_value=1):
            if expect_error is not None:
                with pytest.raises(expect_error):
                    run_async(cli._combine_pairs(client, storage, pairs))
            else:
                run_async(cli._combine_pairs(client, storage, pairs))
        return seen

    def test_run_id_set_during_run_cleared_after_completion(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "")
        storage = make_mock_storage()
        seen = self._run(cli, client, storage, self._pairs(3))
        assert seen.get("run_id")  # asserted while running
        assert cli._run_id == ""  # dropped on exit → board lapses

    def test_run_id_cleared_on_user_cancel(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "")
        storage = make_mock_storage()
        cli._cancelled = True
        self._run(cli, client, storage, self._pairs(3))
        assert cli._run_id == ""

    def test_run_id_cleared_on_429_cooldown(self):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.client import NealRateLimited

        _relay_on(cli)
        client = make_mock_client()
        client.pair.side_effect = NealRateLimited()
        storage = make_mock_storage()
        self._run(cli, client, storage, self._pairs(3))
        assert cli._run_id == ""

    def test_run_id_cleared_on_unexpected_exception(self):
        import infinite_craft_cli.cli as cli

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "")
        storage = make_mock_storage()
        storage.add.side_effect = RuntimeError("disk full")
        self._run(cli, client, storage, self._pairs(3), expect_error=RuntimeError)
        assert cli._run_id == ""

class TestStressRegressions:
    """Regressions from the pre-release subagent stress round."""

    def test_beat_worker_survives_malformed_beat_return(self):
        """S6c: THE timer must survive a contract-violating beat() return —
        a crash here would silently kill the hive tier for the session."""
        import infinite_craft_cli.cli as cli

        cli._relay_user_on = True
        cli._relay_reachable = True
        client = make_mock_client()
        storage = make_mock_storage()
        calls = {"n": 0}

        async def fake_sleep(secs):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise asyncio.CancelledError()

        with patch.object(cli.relay_client, "beat", return_value={}) as beat, \
             patch("infinite_craft_cli.cli.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                run_async(cli._beat_worker(client, storage))
        assert beat.call_count == 2  # loop survived the bad return and beat again

    def test_run_id_asserted_before_preprocessing(self):
        """S7: the household gate keys off the runId in our beats, so the id
        must ride from before the hive sweep/prioritization, not after."""
        import infinite_craft_cli.cli as cli
        from unittest.mock import AsyncMock

        _relay_on(cli)
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "")
        storage = make_mock_storage()
        seen = {}

        async def spy_sweep(_client, _pairs):
            seen["run_id_at_sweep"] = cli._run_id
            return 0

        pairs = [(MockElement(f"A{i}"), MockElement(f"B{i}")) for i in range(3)]
        with patch.object(cli, "_hive_sweep", new=spy_sweep), \
             patch.object(cli, "_hive_run_sync", new=AsyncMock()), \
             patch.object(cli.relay_client, "contribute", return_value=1):
            run_async(cli._combine_pairs(client, storage, pairs))
        assert seen.get("run_id_at_sweep")  # non-empty during pre-processing
        assert cli._run_id == ""  # and cleared after
