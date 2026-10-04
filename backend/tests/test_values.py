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
import re
from pathlib import Path

import pytest

from app.core.night_settlement import _DELAYED_DEATH_CAUSES, _DELAY_CAUSES
from app.core.sheriff_flow import (
    _POST_ELECTION_DEATH_CAUSES, _PRE_ELECTION_DEATH_CAUSES,
)
from app.models.actions import _DAYTIME_LAST_WORDS_CAUSES
from app.models.game import GamePhase
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
        "knight_duel", "charm", "wolf_king_shot", "reflect",
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


def test_pre_and_post_election_death_causes_partition_the_catalogue() -> None:
    """开选前/后的死因白名单必须恰好划分整个目录。

    子集门禁只能抓「凭空发明一个死因」，抓不到「漏掉一个」：`charm` 漏在开选前
    白名单里时，首夜被毒的狼美人带走的殉情者会被竞选提示词写成「警下、有投票
    权」，而所有子集断言依然全绿。骑士决斗只发生在全体发言之后，开选前不可能
    出现，因此显式排除。
    """
    assert (
        _PRE_ELECTION_DEATH_CAUSES | _POST_ELECTION_DEATH_CAUSES | {"knight_duel"}
        == DEATH_CAUSES
    )
    assert not (_PRE_ELECTION_DEATH_CAUSES & _POST_ELECTION_DEATH_CAUSES)


FRONTEND_LABELS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "labels.ts"
)


def _frontend_labelled_keys(namespace: str) -> set[str]:
    text = FRONTEND_LABELS.read_text(encoding="utf-8")
    body = text.split(f"export const {namespace}:")[1].split("\n};")[0]
    return set(re.findall(r"^\s*'?([A-Za-z_][\w-]*)'?:\s*'", body, re.M))


def test_frontend_labels_cover_every_declared_value() -> None:
    """界面文案必须覆盖后端目录里的每个取值。

    前后端是两种语言，各自的联合类型无法互相引用 —— 此前正是这条缝让新取值只在
    后端出现而界面照旧：`wolves_outnumber_good`（22 局）与 `sheriff_election`
    （29 局）都是直接把机器值渲染给了用户。这条门禁让「后端加一个取值」必然要求
    「前端补一条文案」，否则后端测试套件变红。
    """
    assert _frontend_labelled_keys("DEATH_CAUSE_LABELS") == DEATH_CAUSES
    assert _frontend_labelled_keys("WIN_REASON_LABELS") == WIN_REASONS
    assert _frontend_labelled_keys("PHASE_LABELS") == {
        phase.value for phase in GamePhase
    }


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
