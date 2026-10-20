"""Cupid: links two players on the first night. A mixed pair is a third party.

The link is optional. The two lovers learn each other's seat and not their
identity. When one leaves, the other follows once, by ``love_death``.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.core.win_attribution import LOVER_PAIR_KEY, LOVER_PEER_KEY
from app.models.values import DeathCause
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

LINK_ACTION = "link"
PAIR_SIZE = 2
LOVE_MARKER = "love_available"
LOVE_CAUSE = "love_death"
_REASONING_EVENT = "CUPID_REASONING"
_CHAIN_REASONS = frozenset(cause.value for cause in DeathCause)


def cupid_applicable(context: ActionContext) -> bool:
    return (
        context.actor_alive
        and context.round_number == 1
        and context.resources.get(LOVE_MARKER, 0) > 0
    )


def chain_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.resources.get(LOVE_MARKER, 0) > 0


def validate_cupid_link(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    return ()


def _cupid_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    seats = tuple(command.target_seats or ())
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == LINK_ACTION and seats:
        thought = (
            f"决定连线 {'、'.join(str(seat) for seat in seats)} 号玩家"
            f"：{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不连线：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _REASONING_EVENT,
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seat": seats[0] if len(seats) == 1 else None,
                "reasoning": command.reasoning,
                "degraded": command.degraded,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_cupid_link(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    seats = tuple(command.target_seats or ())
    if command.action_type != LINK_ACTION or len(seats) != PAIR_SIZE:
        return (_cupid_reasoning_effect(context, command, 1, **common),)
    first, second = seats
    actor = context.actor_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload={"target": actor, "key": LOVER_PAIR_KEY, "value": [first, second]},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=first,
            payload={
                "target": first, "key": LOVER_PEER_KEY,
                "value": {"seat": second, "round": context.round_number},
            },
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=second,
            payload={
                "target": second, "key": LOVER_PEER_KEY,
                "value": {"seat": first, "round": context.round_number},
            },
            sort_key=(3,), **common,
        ),
        _cupid_reasoning_effect(context, command, 4, **common),
    )


def _current_pair(context: ActionContext) -> tuple[int, ...]:
    value = context.facts.get(LOVER_PAIR_KEY)
    if type(value) not in (list, tuple):
        return ()
    return tuple(seat for seat in value if type(seat) is int)


def _chain_victim(context: ActionContext) -> int | None:
    if context.resources.get(LOVE_MARKER, 0) <= 0:
        return None
    trigger = context.trigger_event
    dead = trigger.get("target_seat") if isinstance(trigger, Mapping) else None
    if type(dead) is not int:
        return None
    pair = _current_pair(context)
    if dead not in pair:
        return None
    alive = context.facts.get("alive_seats", ())
    if type(alive) is not tuple:
        return None
    survivors = [seat for seat in pair if seat != dead and seat in alive]
    return survivors[0] if len(survivors) == 1 else None


def react_cupid_chain(context: ActionContext) -> tuple[GameEffect, ...]:
    if context.source_event_id is None:
        return ()
    victim = _chain_victim(context)
    if victim is None:
        return ()
    seat = context.actor_seat
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": LOVE_MARKER, "amount": 1},
            preconditions={"resource_equals": {"resource": LOVE_MARKER, "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.MARK_DEATH,
            context.action_key, target_seat=victim,
            payload={"target": victim, "cause": LOVE_CAUSE},
            sort_key=(2,), **common,
        ),
    )


CUPID_LINK_CONTRACT = ActionContract(
    contract_id="cupid_link",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    order=4,
    action_types=(LINK_ACTION, "pass"),
    actions_requiring_target=frozenset(),
    multi_target_actions={LINK_ACTION: PAIR_SIZE},
    fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=cupid_applicable,
    validate=validate_cupid_link,
    resolve=resolve_cupid_link,
)

CUPID_CHAIN_CONTRACT = ActionContract(
    contract_id="cupid_chain",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=48,
    action_types=("chain",),
    actions_requiring_target=frozenset(),
    fallback_action_type="chain",
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.MARK_DEATH}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_CHAIN_REASONS,
    per_window_limit=1,
    per_game_limit=1,
    is_applicable=chain_applicable,
    react=react_cupid_chain,
)

CUPID_SPEC = RoleSpec(
    tags=frozenset(),
    role_id="wolf-killer-cupid",
    display_name="Cupid",
    camp_id="third_party",
    opening_narration=("丘比特请睁眼", "请选择两名玩家连为挚友。"),
    opening_points=frozenset({"night_action"}),
    contracts=(CUPID_LINK_CONTRACT, CUPID_CHAIN_CONTRACT),
    initial_resources={LOVE_MARKER: 1},
    initial_private_data={LOVER_PAIR_KEY: ()},
    bond_slot=LOVER_PAIR_KEY,
    allowed_effects=frozenset({
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
        EffectKind.CONSUME_RESOURCE, EffectKind.MARK_DEATH,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "On the first night, before the wolves talk, you may link two different living "
        "players, and you may include yourself. You may also pass. You do not learn "
        "their identities. If both are good, you win with the good side. If both are "
        "werewolves, you win with the werewolves. If they are mixed, you and both of "
        "them are a third party: while any of that side is alive the other two sides "
        "cannot win, and the third party wins only when everyone else is gone. When one "
        "lover leaves the game the other follows, once, whatever the cause. Your own "
        "camp id is third_party until the link moves you."
    ),
)


class Cupid(BaseRole):
    """Daytime instance: the link and the chain are pipeline contracts."""

    pass
