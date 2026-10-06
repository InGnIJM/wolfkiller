"""The shared "not two nights running" memory.

Five roles forbid the same target on two *consecutive* nights. Each used to
compare tonight's choice against the bare seat it last resolved, which confused
"the night before" with "the last night I happened to act": a passed, silenced
or failed night left the record frozen, so the next real choice was measured
against a night that was not last night. For the forbidding roles that rejected
a legal choice (and then its blind retry, costing a second night). For Dream
Weaver it was worse — a repeat two nights apart read as "two nights running"
and killed the seat it was written to shield.
"""

from __future__ import annotations

import pytest

from app.models.pipeline import ActionContext
from app.roles.target_memory import (
    is_consecutive_repeat, last_target, target_memory,
)

KEY = "last_guarded"


def context(memory: object, *, round_number: int = 4) -> ActionContext:
    return ActionContext(
        "g", 0, {KEY: memory}, config_version="a" * 64,
        contract_id="guard_action", contract_version=1,
        contract_digest="b" * 64, round_number=round_number, phase="night",
        window_id="w", schedule_point="night_action", actor_seat=1,
        actor_role_id="wolf-killer-guard", actor_alive=True, action_key="a",
    )


def test_memory_records_who_and_when() -> None:
    assert target_memory(1, KEY, 7, 3) == {
        "target": 1, "key": KEY, "value": {"seat": 7, "round": 3},
    }


@pytest.mark.parametrize(("memory", "expected"), [
    (None, None),
    (5, 5),
    ({"seat": 5, "round": 3}, 5),
    ({"seat": "bad"}, None),
    ({"round": 3}, None),
    ("bad", None),
    (7, 7),
])
def test_last_target_reads_both_shapes(memory, expected) -> None:
    assert last_target(context(memory), KEY) == expected


def test_repeat_one_night_apart_is_consecutive() -> None:
    assert is_consecutive_repeat(context({"seat": 7, "round": 3}, round_number=4), KEY, 7)


def test_repeat_after_a_skipped_night_is_not_consecutive() -> None:
    assert not is_consecutive_repeat(
        context({"seat": 7, "round": 2}, round_number=4), KEY, 7,
    )


def test_a_pre_round_archive_keeps_the_old_reading() -> None:
    """Values written before the round was stored can only mean "last night"."""
    assert is_consecutive_repeat(context(7), KEY, 7)
    assert not is_consecutive_repeat(context(7), KEY, 8)
    assert not is_consecutive_repeat(context(None), KEY, 7)


def test_a_different_target_is_never_a_repeat() -> None:
    assert not is_consecutive_repeat(
        context({"seat": 7, "round": 3}, round_number=4), KEY, 8,
    )
