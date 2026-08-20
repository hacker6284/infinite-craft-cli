"""Script language v0.6 — CLI driver tests (spec 2026-08-20)."""

from unittest.mock import MagicMock, patch

import asyncio

from tests.conftest import MockElement, make_mock_client, make_mock_storage


def run_async(coro, *, timeout: float = 8.0):
    async def _with_timeout():
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_with_timeout())


def _growing_storage(initial=None):
    """Mock storage whose add() actually grows the discovery list."""
    discoveries = list(
        initial
        if initial is not None
        else [
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
        ]
    )
    storage = make_mock_storage(list(discoveries))

    def add_side_effect(**kwargs):
        name = kwargs.get("name")
        if name and not any(e.name == name for e in discoveries):
            discoveries.append(
                MockElement(name, kwargs.get("emoji", ""), bool(kwargs.get("is_first_discovery")))
            )
        storage.get_all.return_value = list(discoveries)
        return None

    storage.add.side_effect = add_side_effect

    def get_by_name(name):
        for e in discoveries:
            if e.name == name:
                return e
        return None

    storage.get_by_name.side_effect = get_by_name
    return storage


def _run(line, client=None, storage=None):
    from infinite_craft_cli.cli import _run_script

    client = client or make_mock_client()
    storage = storage or _growing_storage()
    with patch("infinite_craft_cli.cli._record_recipes_batch"), patch(
        "infinite_craft_cli.cli._record_recipe", create=True
    ), patch("infinite_craft_cli.cli._load_recipes", return_value={}):
        run_async(_run_script(client, storage, line))
    return client, storage


class TestScriptBasics:
    def test_legacy_combine_line(self, capsys):
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "💨")
        _run("water + fire", client=client)
        out = capsys.readouterr().out
        assert "Steam" in out
        assert "[NEW]" in out

    def test_pure_statement_echoes_matches(self, capsys):
        _run("f*")
        out = capsys.readouterr().out
        assert "Fire" in out

    def test_walrus_binds_and_echoes_count(self, capsys):
        _run("x := f* , water ; x")
        out = capsys.readouterr().out
        assert "x = 2 elements" in out
        assert "Water" in out and "Fire" in out

    def test_new_register_after_combine(self, capsys):
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "💨")
        _run("water + fire ; []", client=client)
        out = capsys.readouterr().out
        assert out.count("Steam") >= 2  # combine echo + [] listing

    def test_combine_arity_error_aborts(self, capsys):
        client = make_mock_client()
        _run("! + water ; fire", client=client)
        out = capsys.readouterr().out
        assert "Script aborted" in out
        assert "use , to collect or * to cross" in out
        client.pair.assert_not_called()

    def test_static_error_runs_nothing(self, capsys):
        client = make_mock_client()
        _run("x := a* , b* * c*", client=client)
        out = capsys.readouterr().out
        assert "Script error" in out
        client.pair.assert_not_called()

    def test_unresolved_element_aborts_before_later_statements(self, capsys):
        client = make_mock_client()
        _run("xyzzy ; water + fire", client=client)
        out = capsys.readouterr().out
        assert "Element not found: xyzzy" in out
        client.pair.assert_not_called()


class TestScriptControlFlow:
    def test_foreach_binder(self, capsys):
        _run("w* @e { e , fire }")
        out = capsys.readouterr().out
        assert out.count("Fire") >= 2  # once per w* element (Water, Wind)
        assert "Water" in out and "Wind" in out

    def test_until_loop_converges_via_pair_cache(self, capsys):
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "💨")
        _run("{ water + fire } -> |[]| == 0", client=client)
        out = capsys.readouterr().out
        assert "condition met after 2 iterations" in out

    def test_while_loop_skips_when_false(self, capsys):
        client = make_mock_client()
        _run("{ water + fire } ~ |zzz*| > 0", client=client)
        out = capsys.readouterr().out
        assert "condition false, body skipped" in out
        client.pair.assert_not_called()

    def test_ternary(self, capsys):
        _run("|w*| >= 2 ? {water} : {fire}")
        out = capsys.readouterr().out
        assert "Water" in out


class TestStressTestRegressions:
    """Fixes from the pre-2.0 stress test (R1, S1, S2)."""

    def test_script_command_passes_enqueue_validation(self, capsys):
        # R1: /script was rejected by kernel validation before dispatch.
        from infinite_craft_cli.cli import _enqueue_command_line

        storage = make_mock_storage()
        with patch("infinite_craft_cli.cli._ensure_lane_worker"):
            assert _enqueue_command_line("/script run.ice", MagicMock(), storage)
        out = capsys.readouterr().out
        assert "Unknown" not in out

    def test_braced_loop_body_walrus_visible_to_cond(self, capsys):
        # S1: the spec's own idiom — bind in a braced body, test in the cond.
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "💨")
        _run("{ n := [ water + fire ] } -> |n| == 0", client=client)
        out = capsys.readouterr().out
        assert "Element not found: n" not in out
        assert "condition met after 2 iterations" in out

    def test_newset_around_pure_expr_is_empty(self, capsys):
        # S2: [ pure ] is the empty set, not an error.
        _run("[ w* ]")
        out = capsys.readouterr().out
        assert "Not a pure set expression" not in out
        assert "No matches found." in out

    def test_loop_scope_dropped_after_loop(self, capsys):
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "💨")
        _run("{ n := [ water + fire ] } -> |n| == 0 ; n", client=client)
        out = capsys.readouterr().out
        assert "Element not found: n" in out


class TestRound2Regressions:
    """Fixes from stress-test round two (BUG-1, BUG-2)."""

    def test_walrus_ack_suppressed_inside_loops(self, capsys):
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "💨")
        _run("{ n := [ water + fire ] } -> |n| == 0", client=client)
        out = capsys.readouterr().out
        assert "n = " not in out  # no per-iteration ack flood
        assert "condition met" in out

    def test_walrus_ack_still_prints_at_top_level(self, capsys):
        _run("x := f*")
        out = capsys.readouterr().out
        assert "x = 1 element" in out

    def test_script_crawl_prints_generation_telemetry(self, capsys):
        client = make_mock_client()
        client.pair.return_value = MockElement("Steam", "💨")
        _run("water ++ fire", client=client)
        out = capsys.readouterr().out
        assert "Gen 1:" in out
        assert "joined the pool" in out


class TestScriptFile:
    def test_script_slash_command_reads_file(self, tmp_path, capsys):
        from infinite_craft_cli.cli import _dispatch_line

        path = tmp_path / "demo.ice"
        path.write_text("f*\n", encoding="utf-8")
        client = make_mock_client()
        storage = _growing_storage()
        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            run_async(_dispatch_line(client, storage, f"/script {path}"))
        out = capsys.readouterr().out
        assert "Fire" in out

    def test_regex_statement_line_dispatches_as_script(self, capsys):
        from infinite_craft_cli.cli import _dispatch_line

        client = make_mock_client()
        storage = _growing_storage()
        with patch("infinite_craft_cli.cli._load_recipes", return_value={}):
            run_async(_dispatch_line(client, storage, "/fire/"))
        out = capsys.readouterr().out
        assert "Fire" in out
