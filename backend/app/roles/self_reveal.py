"""Public self-reveal shared by the Fool Seer and the Iron Villager.

The card is shown and nothing else changes: the seat stays alive, keeps its
vote, and can still be exiled. The one-shot marker is a resource, not a
per-game counter, because a ``pass`` is a real answer and must not void the card.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RuleViolation, SchedulePoint,
)

REVEAL_RESOURCE = "reveal"
REVEAL_ACTION = "reveal"


def reveal_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.resources.get(REVEAL_RESOURCE, 0) > 0


def validate_reveal_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != REVEAL_ACTION:
        return ()
    if context.resources.get(REVEAL_RESOURCE, 0) <= 0:
        return (RuleViolation("reveal_unavailable", "the card has already been shown"),)
    if command.target_seat is not None:
        return (RuleViolation("target_forbidden", "showing the card takes no target"),)
    return ()


def _reveal_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    event_type = str(common.pop("event_type", "SELF_REVEAL"))
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == REVEAL_ACTION:
        thought = f"决定翻牌自曝身份：{command.reasoning or '无理由'}"
    else:
        thought = f"决定暂不翻牌：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": event_type,
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


def resolve_reveal_action(
    context: ActionContext, command: ActionCommand, *, event_type: str,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
        "event_type": event_type,
    }
    if command.action_type != REVEAL_ACTION:
        return (_reveal_reasoning_effect(context, command, 1, **common),)
    actor = context.actor_seat
    identity = context.facts.get("actor_identity") or {}
    role = identity.get("role_id", context.actor_role_id) if isinstance(identity, Mapping) else context.actor_role_id
    camp = identity.get("camp_id", "") if isinstance(identity, Mapping) else ""
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": REVEAL_RESOURCE, "amount": 1},
            preconditions={"resource_equals": {"resource": REVEAL_RESOURCE, "value": 1}},
            sort_key=(1,), **{key: value for key, value in common.items() if key != "event_type"},
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "PLAYER_REVEALED",
                "payload": {"seat_number": actor, "role": role, "camp": camp},
            },
            visibility=("PUBLIC",),
            expected_revision=context.revision,
            source_event_id=context.source_event_id,
            sort_key=(2,),
        ),
        _reveal_reasoning_effect(context, command, 3, **common),
    )


def reveal_contract(
    *, contract_id: str, order: int,
    resolve: object,
) -> ActionContract:
    return ActionContract(
        contract_id=contract_id,
        schedule_point=SchedulePoint.DAY_ACTION,
        order=order,
        action_types=(REVEAL_ACTION, "pass"),
        actions_requiring_target=frozenset(),
        fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        per_window_limit=1,
        is_applicable=reveal_applicable,
        validate=validate_reveal_action,
        resolve=resolve,
    )
