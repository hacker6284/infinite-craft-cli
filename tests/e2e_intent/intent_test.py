"""Owner-intent E2E suite.

Oracle = the owner's stated ruling text from the 2026-07-22 query-semantics
grilling (see tests/e2e_intent/README.md), not the implementation and not
tests/parity/ (which is host-vs-host). Each scenario in intent_fixtures.json
pins one ruling against the real infinite_craft_cli.cli functions dispatched
by the REPL. Scenarios marked "pending-v0.2.0" describe rulings the current
kernel does not implement yet (blocked on a future sudoc compiler version)
and are skipped with a visible, ruling-tagged reason rather than omitted.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from infinite_craft_cli.cli import _match_elements, _validate_command_line

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


FIXTURES_PATH = Path(__file__).resolve().parent / "intent_fixtures.json"
FIXTURES = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class _FixtureElement:
    """Minimal stand-in for infinite_craft_cli.element.Element — just the
    3 fields _match_elements' storage.get_all() consumers read."""

    name: str
    emoji: str
    is_first_discovery: bool


class _FixtureStorage:
    """Minimal stand-in for DiscoveryStorage — only get_all() is used by
    _match_elements."""

    def __init__(self, elements: list[_FixtureElement]):
        self._elements = elements

    def get_all(self):
        return list(self._elements)


def _storage_for(element_set_name: str) -> _FixtureStorage:
    rows = FIXTURES["element_sets"][element_set_name]
    return _FixtureStorage(
        [_FixtureElement(name=n, emoji=e, is_first_discovery=bool(f)) for n, e, f in rows]
    )


ACTIVE = [s for s in FIXTURES["scenarios"] if s["status"] == "active"]
PENDING = [s for s in FIXTURES["scenarios"] if s["status"] == "pending-v0.2.0"]
assert len(ACTIVE) + len(PENDING) == len(FIXTURES["scenarios"])


def _id(scenario: dict) -> str:
    return scenario["id"]


@pytest.mark.parametrize("scenario", ACTIVE, ids=_id)
def test_active_scenario(scenario: dict) -> None:
    if scenario["surface"] == "search":
        storage = _storage_for(scenario["elements"])
        matches, err = _match_elements(storage, scenario["query"])
        assert err == scenario["expect"]["error"], scenario.get("note", scenario["id"])
        assert sorted(e.name for e in matches) == scenario["expect"]["names"], scenario.get(
            "note", scenario["id"]
        )
    elif scenario["surface"] == "validate":
        assert scenario["validator"] == "command_line"
        err = _validate_command_line(scenario["line"])
        assert err == scenario["expect"]["error"], scenario.get("note", scenario["id"])
    else:
        pytest.fail(f"unknown surface: {scenario['surface']!r}")


@pytest.mark.parametrize("scenario", PENDING, ids=[s["id"] for s in PENDING])
def test_pending_scenario(scenario: dict) -> None:
    pytest.skip(f"{scenario['ruling']}: {scenario['skip_reason']}")
