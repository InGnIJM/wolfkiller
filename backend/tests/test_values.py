"""The engine's vocabulary for death causes and win reasons is a code fact.

Both sets used to exist only as ``str`` plus a comment in ``models/actions.py``,
so nothing could enumerate them: the public DTO, the old-archive replay
whitelist, the legacy-state reconstruction and the frontend each kept a
hand-written copy. They drifted — a missing ``charm`` / ``knight_duel`` /
``wolves_outnumber_good`` turned ``GET /api/games/{id}`` (and ``/logs``) into
HTTP 500 while 100% branch coverage stayed green, because branch coverage proves
a branch was walked, never that a value crossed the contract boundary.

The catalogues below are the anchor. Note what they deliberately are **not**:
they are never passed into the persistence or effect layers. Those check
``type(value) is str`` exactly (``persistence/checkpoint_codec.py:100``,
``core/effect_applier.py:24``), so an enum member — whose ``type()`` is the enum,
not ``str`` — would raise instead of round-tripping. Producers therefore keep
writing plain strings, and this module checks them instead.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.core.night_settlement import _DELAYED_DEATH_CAUSES, _DELAY_CAUSES
from app.core.sheriff_flow import _PRE_ELECTION_DEATH_CAUSES
from app.models.actions import _DAYTIME_LAST_WORDS_CAUSES
from app.models.values import DeathCause, WinReason
from app.roles.hunter import _SHOOT_REASONS
from app.roles.knight import _DUEL_CAUSE

DEATH_CAUSES = frozenset(cause.value for cause in DeathCause)
WIN_REASONS = frozenset(reason.value for reason in WinReason)

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _production_modules() -> list[Path]:
    return [
        path for path in sorted(APP_ROOT.rglob("*.py"))
        if "/test/" not in path.as_posix() and not path.name.startswith("test_")
    ]


def test_death_causes_are_frozen() -> None:
    assert DEATH_CAUSES == {
        "wolf_kill", "poison", "hunter_shot", "exile", "self_explode",
        "knight_duel", "charm",
    }


def test_win_reasons_are_frozen() -> None:
    assert WIN_REASONS == {
        "all_gods_dead", "all_villagers_dead", "all_wolves_dead",
        "wolves_outnumber_good",
    }


def test_the_vocabulary_stays_plain_strings_on_the_wire() -> None:
    """Members carry the wire value and JSON-serialize as plain strings.

    Producers still have to write ``.value``: ``type(member)`` is the enum class,
    not ``str``, so the persistence and effect layers' ``type(value) is str``
    checks reject a member outright. That is the documented reason these
    catalogues anchor gates instead of travelling themselves.
    """
    assert DeathCause.CHARM == "charm"
    assert json.loads(json.dumps(WinReason.WOLVES_OUTNUMBER_GOOD)) == "wolves_outnumber_good"
    assert type(DeathCause.KNIGHT_DUEL.value) is str
    assert type(DeathCause.KNIGHT_DUEL) is not str


def test_every_rule_subset_stays_inside_the_catalogue() -> None:
    """A rule that lists causes must not be able to invent one."""
    assert set(_SHOOT_REASONS) <= DEATH_CAUSES
    assert set(_PRE_ELECTION_DEATH_CAUSES) <= DEATH_CAUSES
    assert set(_DELAY_CAUSES) <= DEATH_CAUSES
    assert set(_DELAYED_DEATH_CAUSES.values()) <= DEATH_CAUSES
    assert set(_DAYTIME_LAST_WORDS_CAUSES) <= DEATH_CAUSES
    assert _DUEL_CAUSE in DEATH_CAUSES


def _literal_keyword_arguments(name: str) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for path in _production_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != name:
                    continue
                if isinstance(keyword.value, ast.Constant) and type(keyword.value.value) is str:
                    found.append((path, keyword.value.value))
    return found


def test_inline_death_cause_literals_are_declared() -> None:
    """Every ``cause=`` argument in production code is a declared cause."""
    undeclared = [
        (path.name, value) for path, value in _literal_keyword_arguments("cause")
        if value not in DEATH_CAUSES
    ]

    assert undeclared == []


def test_win_result_producers_only_use_declared_reasons() -> None:
    """A module that builds a ``WinResult`` may only pass declared reasons.

    Scoped to ``WinResult`` producers on purpose: ``reason=`` is not a unique
    vocabulary (the sheriff badge loss and the vote telemetry use their own),
    so a blanket scan would report unrelated values.
    """
    checked: list[str] = []
    undeclared: list[tuple[str, str]] = []
    for path in _production_modules():
        source = path.read_text(encoding="utf-8")
        if "WinResult(" not in source:
            continue
        checked.append(path.name)
        for found_path, value in _literal_keyword_arguments("reason"):
            if found_path == path and value not in WIN_REASONS:
                undeclared.append((path.name, value))

    assert undeclared == []
    # The scan must keep seeing the real producers, or it silently stops guarding.
    assert "rule_engine.py" in checked


@pytest.mark.parametrize("cause", sorted(DEATH_CAUSES))
def test_declared_causes_are_unique_strings(cause: str) -> None:
    assert type(cause) is str
