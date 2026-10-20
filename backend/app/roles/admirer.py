"""Admirer: a villager whose victory follows the crush's current allegiance.

Picking is the same shape as the Lonely Girl. The difference is the win check:
the crush's side is read after a link has moved it, so admiring a lover who was
pulled into the third party joins that third party.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.core.win_attribution import CRUSH_KEY
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

CRUSH_RESOURCE = "crush_available"
_CRUSH = "crush"
_REASONING_EVENT = "ADMIRER_REASONING"


def admirer_applicable(context: ActionContext) -> bool:
    return (
        context.actor_alive
        and context.round_number == 1
        and context.resources.get(CRUSH_RESOURCE, 0) > 0
    )


def validate_admirer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != _CRUSH:
        return ()
    if command.target_seat is None:
        return (RuleViolation("crush_target_required", "the crush must be named"),)
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_target", "the crush cannot be the admirer"),)
    return ()


def _admirer_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == _CRUSH and command.target_seat is not None:
        thought = f"决定暗恋 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"没有选择暗恋对象：{command.reasoning or '无理由'}"
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


def resolve_admirer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != _CRUSH or command.target_seat is None:
        return (_admirer_reasoning_effect(context, command, 1, **common),)
    actor, target = context.actor_seat, command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": CRUSH_RESOURCE, "amount": 1},
            preconditions={"resource_equals": {"resource": CRUSH_RESOURCE, "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "key": CRUSH_KEY,
                "value": {"seat": target, "round": context.round_number},
            },
            sort_key=(2,), **common,
        ),
        _admirer_reasoning_effect(context, command, 3, **common),
    )


ADMIRER_SPEC = RoleSpec(
    tags=frozenset({"villager"}),
    role_id="wolf-killer-admirer",
    display_name="Admirer",
    camp_id="good",
    opening_narration=("暗恋者请睁眼", "请选择一名玩家作为你的暗恋对象。"),
    opening_points=frozenset({"night_action"}),
    contracts=(ActionContract(
        contract_id="admirer_crush",
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=6,
        action_types=(_CRUSH, "pass"),
        actions_requiring_target=frozenset({_CRUSH}),
        fallback_action_type="pass",
        allowed_effects=frozenset({
            EffectKind.CONSUME_RESOURCE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
        }),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        per_window_limit=1,
        per_game_limit=1,
        is_applicable=admirer_applicable,
        validate=validate_admirer_action,
        resolve=resolve_admirer_action,
    ),),
    initial_resources={CRUSH_RESOURCE: 1},
    initial_private_data={CRUSH_KEY: None},
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "You count as a villager until your crush's side moves. On the first night you "
        "choose one other living player. You may also pass. You learn nothing about "
        "them, and they learn nothing about you. Your victory follows the crush's "
        "current side: if a link has pulled the crush into the third party, you are in "
        "that third party too. If you pass, you stay a villager."
    ),
)


class Admirer(BaseRole):
    """Daytime instance: the crush is a pipeline contract."""

    pass
