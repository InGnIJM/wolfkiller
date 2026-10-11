"""Lonely Girl: she wins with the player she worships.

The official board keeps her simple, and the simplicity is the whole design:

* she is a **villager** for every camp-based rule — the win check's villager
  count, the seer's reading, the exile ballot. ``tags={"villager"}`` and
  ``camp_id="good"`` buy all of that with no code;
* the *only* thing that follows her idol is the result. That is answered once,
  seat by seat, in ``core/win_attribution.py`` — the camp that ends the game is
  still decided by the living counts, so nothing about the ending moves.

Her pick is the first beat of the first night ("第一夜最开始时最先选择"), which
is why the contract sits at ``NIGHT_ACTION`` with the lowest order. She never
learns anything about the idol, and the idol never learns about her: the choice
is written into her own private slot and read back only for her own prompt.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.core.win_attribution import IDOL_KEY
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

#: The one-shot marker: the choice is made once and never again.
WORSHIP_RESOURCE = "worship"
_WORSHIP = "worship"
_REASONING_EVENT = "LONELY_GIRL_REASONING"


def lonely_girl_applicable(context: ActionContext) -> bool:
    """First night only, once, while the choice is still unmade."""
    return (
        context.actor_alive
        and context.round_number == 1
        and context.resources.get(WORSHIP_RESOURCE, 0) > 0
    )


def validate_lonely_girl_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != _WORSHIP:
        return ()
    if command.target_seat is None:
        return (RuleViolation("worship_target_required", "the idol must be named"),)
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_target", "the idol cannot be the worshipper"),)
    return ()


def _lonely_girl_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == _WORSHIP and command.target_seat is not None:
        thought = f"决定崇拜 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"没有选择崇拜对象：{command.reasoning or '无理由'}"
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


def resolve_lonely_girl_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != _WORSHIP or command.target_seat is None:
        return (_lonely_girl_reasoning_effect(context, command, 1, **common),)
    actor, target = context.actor_seat, command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": WORSHIP_RESOURCE, "amount": 1},
            preconditions={"resource_equals": {"resource": WORSHIP_RESOURCE, "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "key": IDOL_KEY,
                "value": {"seat": target, "round": context.round_number},
            },
            sort_key=(2,), **common,
        ),
        _lonely_girl_reasoning_effect(context, command, 3, **common),
    )


LONELY_GIRL_SPEC = RoleSpec(
    tags=frozenset({"villager"}),
    role_id="wolf-killer-lonely-girl", display_name="Lonely Girl", camp_id="good",
    contracts=(ActionContract(
        contract_id="idol_worship", schedule_point=SchedulePoint.NIGHT_ACTION,
        # 第一夜最开始：before the Nightmare (10) and therefore before every
        # other night role.
        order=5, action_types=(_WORSHIP, "pass"),
        actions_requiring_target=frozenset({_WORSHIP}), fallback_action_type="pass",
        allowed_effects=frozenset({
            EffectKind.CONSUME_RESOURCE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
        }),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        per_window_limit=1, per_game_limit=1,
        is_applicable=lonely_girl_applicable,
        validate=validate_lonely_girl_action,
        resolve=resolve_lonely_girl_action,
    ),),
    initial_resources={WORSHIP_RESOURCE: 1},
    initial_private_data={IDOL_KEY: None},
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    opening_narration=("孤独少女请睁眼", "请选择一名玩家作为你的崇拜对象。"),
    instructions=(
        "Lonely Girl is a plain villager: you count as a villager for the win check, the "
        "seer reads you as a good player, and the vote treats you like any other villager. "
        "On the very first night, before anyone else acts, you choose one other living "
        "player as your idol — the choice is permanent and nobody, including the idol, is "
        "told about it. Your victory follows the idol's camp rather than your own: if the "
        "idol ends the game on the winning side, you win with them, even when your own camp "
        "lost, and if the idol's side loses you lose too. You cannot win on your own, so the "
        "choice is a wager on which side will eventually take the game. Play the day "
        "according to that bet: push the camp you believe will win, without ever revealing "
        "that your allegiance is conditional."
    ),
)


class LonelyGirl(BaseRole):
    """Daytime instance: the worship pick is a pipeline contract, so the role
    itself only speaks and votes like the villager it is."""

    pass
