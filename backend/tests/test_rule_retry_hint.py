"""The repair hint for the scheduler's second attempt is contract-aware.

The old constant told every non-pass action to fill ``target_seat``. That is
backwards for the contracts whose non-pass action takes no target at all
(``awakened_wolf_king_explode``, ``order_rewind``): the model added a seat, the
validator answered ``target_forbidden``, and the retry repeated the same
mistake. The hint now derives its instruction from the frozen contract, and the
``RANDOM_HINT`` reminder only appears when a first-night hint can exist.
"""

from __future__ import annotations

import pytest

from app.agents.game_rules import target_selection_rule_for
from app.models.pipeline import ActionContract, SchedulePoint
from app.roles.awakened_wolf_king import AWAKENED_WOLF_KING_EXPLODE_CONTRACT
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT
from app.services.game_service import _rule_retry_hint, _with_repair_reminder


def no_target_contract() -> ActionContract:
    return ActionContract(
        "no_target_choice", SchedulePoint.DAY_ACTION, 20, ("explode", "pass"),
        frozenset(), "pass",
    )


def single_target_contract() -> ActionContract:
    return ActionContract(
        "single_choice", SchedulePoint.NIGHT_ACTION, 10,
        ("save", "poison", "pass"), frozenset({"save", "poison"}), "pass",
    )


def multi_target_contract() -> ActionContract:
    return ActionContract(
        "multi_choice", SchedulePoint.NIGHT_ACTION, 20, ("pool", "pass"),
        frozenset(), "pass", multi_target_actions={"pool": 3},
    )


def test_no_target_hint_requires_null_and_never_a_seat():
    hint = _rule_retry_hint(no_target_contract())

    assert hint.startswith("\n\n【规则校验失败】")
    assert "target_seat 写成 null" in hint
    assert "非 pass 动作必须" not in hint
    # A no-target contract never receives RANDOM_HINT (`_fallback_target`
    # returns None), so the hint must not ask the model to copy one.
    assert "RANDOM_HINT" not in hint


def test_single_target_hint_names_the_action_and_the_null_branch():
    hint = _rule_retry_hint(single_target_contract())

    assert "（poison、save）必须把目标座位号写进 target_seat" in hint
    assert "其余动作（包括 pass）必须把 target_seat 写成 null" in hint
    assert "RANDOM_HINT" in hint


def test_multi_target_hint_mentions_target_seats_and_the_exact_count():
    hint = _rule_retry_hint(multi_target_contract())

    assert "target_seats" in hint
    assert "3 个互不相同的存活座位" in hint
    assert "target_seat 留空" in hint
    assert "RANDOM_HINT" in hint


def test_the_awakened_wolf_king_retry_no_longer_instructs_adding_a_target():
    """Integration check on the real contract that failed in the 2026-10-26 game."""
    hint = _rule_retry_hint(AWAKENED_WOLF_KING_EXPLODE_CONTRACT)

    assert "target_seat" in hint and "null" in hint
    assert "必须把目标座位号写进 target_seat" not in hint

    merged = _with_repair_reminder(
        [{"role": "user", "content": "ROLE_CONTRACT=..."}], hint,
    )
    assert len(merged) == 1
    assert merged[-1]["content"] == "ROLE_CONTRACT=..." + hint


def test_the_werewolf_kill_retry_still_asks_for_the_seat():
    hint = _rule_retry_hint(WEREWOLF_KILL_CONTRACT)

    assert "（kill）必须把目标座位号写进 target_seat" in hint
    assert "其余动作（包括 pass）必须把 target_seat 写成 null" in hint
    assert "RANDOM_HINT" in hint


def test_the_hint_agrees_with_the_rendered_target_rule():
    """One source of truth: both texts derive from the contract's own actions."""
    for contract in (
        no_target_contract(), single_target_contract(), multi_target_contract(),
    ):
        hint = _rule_retry_hint(contract)
        rule = target_selection_rule_for(contract)
        if contract.multi_target_actions:
            assert "target_seats" in rule and "target_seats" in hint
        elif contract.actions_requiring_target:
            assert "need exactly one target" in rule
            assert "target_seat" in hint
        else:
            assert "no action in this contract takes a target" in rule
            assert "target_seat 写成 null" in hint


def test_a_multi_contract_that_also_names_a_single_target_action_keeps_both_rules():
    """The registry forbids this shape, but the hint must still be total.

    A hand-built contract must never produce a rule that silently forgets the
    single-target action (the two renderers share this branch).
    """
    contract = ActionContract(
        "mixed_choice", SchedulePoint.NIGHT_ACTION, 20, ("pool", "act", "pass"),
        frozenset({"act"}), "pass", multi_target_actions={"pool": 3},
    )

    hint = _rule_retry_hint(contract)
    rule = target_selection_rule_for(contract)

    assert "需要单目标的动作（act）必须把座位号写进 target_seat" in hint
    assert "the single-target action(s) ``act`` need one legal live seat" in rule


def test_a_pass_only_contract_renders_without_naming_actions():
    contract = ActionContract(
        "pass_only", SchedulePoint.DAWN_REACTION, 40, ("pass",), frozenset(), "pass",
    )

    rule = target_selection_rule_for(contract)

    assert rule.endswith(
        "Send ``target_seat``: null for every action; a seat number there is "
        "rejected by the engine."
    )
    assert "(including" not in rule
    assert "RANDOM_HINT" not in _rule_retry_hint(contract)


def test_the_rendered_rule_requires_an_exact_contract():
    with pytest.raises(TypeError, match="exact ActionContract"):
        target_selection_rule_for(object())

