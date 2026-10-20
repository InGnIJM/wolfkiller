"""Crow: a good god who curses one seat for the next exile tally.

The mark is not a public status and it is not swept at dawn. The exile tally
adds one ballot for it, then the write entry clears it. A day that never
reaches a tally leaves the mark for the next one.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.target_memory import is_consecutive_repeat, target_memory

CURSE_KEY = "last_cursed"
CURSE_STATUS = "cursed"
_CURSE = "curse"
_REASONING_EVENT = "CROW_REASONING"


def crow_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_crow_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != _CURSE or command.target_seat is None:
        return ()
    if is_consecutive_repeat(context, CURSE_KEY, command.target_seat):
        return (RuleViolation(
            "consecutive_target", "the same seat cannot be cursed two nights running",
        ),)
    return ()


def _crow_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == _CURSE and command.target_seat is not None:
        thought = f"决定诅咒 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不诅咒：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _REASONING_EVENT,
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seat": command.target_seat,
                "reasoning": command.reasoning,
                "degraded": command.degraded,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_crow_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != _CURSE or command.target_seat is None:
        return (_crow_reasoning_effect(context, command, 1, **common),)
    target = command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.ADD_STATUS,
            context.action_key, target_seat=target,
            payload={"target": target, "status": CURSE_STATUS},
            preconditions={"status_present": {"status": CURSE_STATUS, "present": False}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload=target_memory(context.actor_seat, CURSE_KEY, target, context.round_number),
            sort_key=(2,), **common,
        ),
        _crow_reasoning_effect(context, command, 3, **common),
    )


CROW_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-crow",
    display_name="Crow",
    camp_id="good",
    opening_narration=("乌鸦请睁眼", "请诅咒一名玩家。"),
    opening_points=frozenset({"night_action"}),
    contracts=(ActionContract(
        contract_id="crow_curse",
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=15,
        action_types=(_CURSE, "pass"),
        actions_requiring_target=frozenset({_CURSE}),
        fallback_action_type="pass",
        allowed_effects=frozenset({
            EffectKind.ADD_STATUS, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
        }),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        per_window_limit=1,
        per_round_limit=1,
        is_applicable=crow_applicable,
        validate=validate_crow_action,
        resolve=resolve_crow_action,
    ),),
    initial_private_data={CURSE_KEY: None},
    allowed_effects=frozenset({
        EffectKind.ADD_STATUS, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions=(
        "Each night you may curse one living player, including yourself. You may also "
        "pass. You cannot curse the same seat on two consecutive nights. The curse adds "
        "one ballot to that seat on the next exile tally, including a tie-break re-vote, "
        "and then it is gone. It does not add a ballot to a sheriff election. If the day "
        "ends before the exile tally, the curse waits for the next one."
    ),
)


class Crow(BaseRole):
    """Daytime instance: the curse is a pipeline contract."""

    pass
