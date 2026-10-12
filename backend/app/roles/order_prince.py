"""Order Prince: turn back time over the day's first exile vote.

After the first exile vote of a day has been counted — and before anybody is
exiled — the Prince may flip his card, voiding that vote and sending the table
back to the ballot. He gets one extra speech first. Once per game.

The card lives entirely on the generic ``VOTE_ACTION`` window the engine opens
once per day after the first tally: the hook flips the card (``PLAYER_REVEALED``),
spends the one-shot ``rewind`` resource and announces ``VOTE_REWOUND``. The
engine only recognises that generic event, so it never names this role; it
reuses the tie-break re-vote machinery (supplemental speech, fresh window, widened
candidate set) to actually rewind.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

REWIND_RESOURCE = "rewind"
_EVENT = "ORDER_PRINCE_REASONING"


def order_prince_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.resources.get(REWIND_RESOURCE, 0) > 0


def validate_order_prince_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "rewind" and context.resources.get(REWIND_RESOURCE, 0) <= 0:
        return (RuleViolation(
            "rewind_unavailable", "the time reversal has already been used",
        ),)
    return ()


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "rewind":
        thought = f"决定翻牌逆转时空，重新投票：{command.reasoning or '无理由'}"
    else:
        thought = f"决定不发动逆转：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _EVENT,
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


def resolve_order_prince_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "rewind":
        return (_reasoning_effect(context, command, 1, **common),)
    seat = context.actor_seat
    identity = context.facts.get("actor_identity") or {}
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": REWIND_RESOURCE, "amount": 1},
            preconditions={
                "resource_equals": {"resource": REWIND_RESOURCE, "value": 1},
            },
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "PLAYER_REVEALED",
                "payload": {
                    "seat_number": seat,
                    "role": identity.get("role_id", context.actor_role_id),
                    "camp": identity.get("camp_id", ""),
                },
            },
            visibility=("PUBLIC",), sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "VOTE_REWOUND",
                "payload": {"seat": seat, "round_number": context.round_number},
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _reasoning_effect(context, command, 4, **common),
    )


ORDER_REWIND_CONTRACT = ActionContract(
    contract_id="order_rewind",
    schedule_point=SchedulePoint.VOTE_ACTION,
    order=10,
    action_types=("rewind", "pass"),
    actions_requiring_target=frozenset(),
    fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    # No per-game limit: a "pass" is an accepted action too and the Prince must
    # be asked again on every later day. The one-shot is the resource.
    per_window_limit=1,
    is_applicable=order_prince_applicable,
    validate=validate_order_prince_action,
    resolve=resolve_order_prince_action,
)

ORDER_PRINCE_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-order-prince",
    display_name="Order Prince",
    camp_id="good",
    contracts=(ORDER_REWIND_CONTRACT,),
    initial_resources={REWIND_RESOURCE: 1},
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "Order Prince is a good-camp god role with no night action. Each day, right after "
        "the FIRST exile vote has been counted and before anyone is exiled, you are asked "
        "whether to flip your card and turn time back: your identity becomes public, that "
        "vote is voided, you get one extra speech, and the whole table votes again. The "
        "re-vote may name any living player; if it ties, nobody is exiled. It works once "
        "per game and cannot be used on a tie-break re-vote. Passing keeps the ability for "
        "a later day. Weigh the cost: you reveal yourself and the wolves see the new "
        "ballot too."
    ),
)


class OrderPrince(BaseRole):
    """Order Prince: the time reversal is a pipeline contract on the vote window;
    the daytime instance only speaks and votes like a villager."""
