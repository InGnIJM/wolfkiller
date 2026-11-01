"""Crimson Envoy: a werewolf that trades itself for a silenced night.

Its daytime self-destruct is the White Wolf King's shape minus the passenger:
it takes nobody along, but every *living good player* is sealed for the coming
night — guard, seer, witch, hunter, dream weavers, all of them. The seal is an
ordinary runtime status (``night_settlement.SILENCED_STATUS``), so nothing in
the engine has to know this card exists: the scheduler withholds a silenced
seat's contract requests, and the engine withholds the pack's attack when a wolf
is sealed.

The second half of the card — surviving its own exile when it is the last wolf —
lives in ``CRIMSON_LINGER_CONTRACT`` below, on the generic ``EXILE_VERDICT``
window the Idiot already uses.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import (
    DELAYED_DEATH_STATUS, LINGERING_EXILE_STATUS, SILENCED_STATUS,
)
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf
from app.roles.wolf_claw import (
    AWAKENED_CLAW_GIFT_CONTRACT, AWAKENED_CLAW_GIFT_KEY,
)

EXPLODE_CAUSE = "self_explode"
#: One-shot markers, declared as resources so the shared kill contract can stay
#: byte-identical with every other wolf's.
EXPLODE_RESOURCE = "explode"
LINGER_RESOURCE = "linger"


def _living_good_seats(context: ActionContext) -> tuple[int, ...]:
    """Every living seat outside the wolf camp, in seat order.

    ``camp_members`` is the pack roster the CAMP namespace hands a werewolf;
    ``alive_seats`` filters out teammates who died earlier. Reading both keeps
    the hook pure — it never touches game state.
    """
    members = context.facts.get("camp_members") or ()
    alive = context.facts.get("alive_seats") or ()
    if not isinstance(members, (list, tuple)) or not isinstance(alive, (list, tuple)):
        raise TypeError("camp_members and alive_seats must be sequences")
    pack = set(members)
    return tuple(
        seat for seat in alive
        if type(seat) is int and seat not in pack
    )


def crimson_envoy_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.resources.get(EXPLODE_RESOURCE, 0) > 0


def validate_crimson_envoy_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "explode":
        return ()
    if context.resources.get(EXPLODE_RESOURCE, 0) <= 0:
        return (RuleViolation(
            "explode_unavailable", "the self-destruct is no longer available",
        ),)
    return ()


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "explode":
        thought = f"决定自爆并封印当晚所有好人技能：{command.reasoning or '无理由'}"
    else:
        thought = f"决定暂不自爆：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "CRIMSON_ENVOY_REASONING",
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


def resolve_crimson_envoy_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_reasoning_effect(context, command, 1, **common),)
    actor = context.actor_seat
    silenced = _living_good_seats(context)
    effects: list[GameEffect] = [
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": EXPLODE_RESOURCE, "amount": 1},
            preconditions={
                "resource_equals": {"resource": EXPLODE_RESOURCE, "value": 1},
            },
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "amount": 1, "cause": EXPLODE_CAUSE},
            sort_key=(2,), **common,
        ),
    ]
    ordinal = 3
    for seat in silenced:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.ADD_STATUS,
            context.action_key, target_seat=seat,
            payload={"target": seat, "status": SILENCED_STATUS},
            preconditions={
                "status_present": {"status": SILENCED_STATUS, "present": False},
            },
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
    for seat in silenced:
        # The seal is invisible on the wire otherwise: ``ADD_STATUS`` itself
        # publishes nothing, and the god view has to show which seats are out.
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "STATUS_ADDED",
                "payload": {
                    "seat": seat, "status": SILENCED_STATUS,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(ordinal,), **common,
        ))
        ordinal += 1
    effects.extend((
        GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "CRIMSON_EXPLODE",
                "payload": {
                    "seat": actor, "silenced_seats": list(silenced),
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(ordinal,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, ordinal + 1), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "DAY_INTERRUPTED",
                "payload": {
                    "seat": actor, "cause": EXPLODE_CAUSE,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(ordinal + 1,), **common,
        ),
        _reasoning_effect(context, command, ordinal + 2, **common),
    ))
    return tuple(effects)


CRIMSON_EXPLODE_CONTRACT = ActionContract(
    contract_id="crimson_explode",
    schedule_point=SchedulePoint.DAY_ACTION,
    order=20,
    # No passenger: the envoy takes nobody along, so the command carries no
    # target at all.
    action_types=("explode", "pass"),
    actions_requiring_target=frozenset(),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    # No per-game limit: a "pass" is also an accepted action and the envoy must
    # be asked again before every later speaker. The one-shot is the resource.
    per_window_limit=1,
    is_applicable=crimson_envoy_applicable,
    validate=validate_crimson_envoy_action,
    resolve=resolve_crimson_envoy_action,
)


def _last_wolf_standing(context: ActionContext) -> bool:
    """True when no other werewolf is still alive.

    ``camp_members`` is the pack roster (dead teammates included, that is the
    point of the projector's "known identities persist" rule) and
    ``alive_seats`` says who is still up, so the comparison needs no state read.
    """
    members = context.facts.get("camp_members") or ()
    alive = context.facts.get("alive_seats") or ()
    if not isinstance(members, (list, tuple)) or not isinstance(alive, (list, tuple)):
        return False
    living = set(alive)
    return all(
        seat == context.actor_seat or seat not in living
        for seat in members
        if type(seat) is int
    )


def crimson_linger_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason == "exile"
        and context.actor_alive
        and context.resources.get(LINGER_RESOURCE, 0) > 0
        and _last_wolf_standing(context)
    )


def react_crimson_linger(context: ActionContext) -> tuple[GameEffect, ...]:
    """Survive the exile and die after the next dawn instead.

    Response windows never consult ``is_applicable`` before reacting, so the
    hook re-checks it and stays silent once the card is spent — exactly the
    Idiot's shape, on the window the engine already provides.
    """
    if not crimson_linger_applicable(context):
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
            payload={"target": seat, "resource": LINGER_RESOURCE, "amount": 1},
            preconditions={
                "resource_equals": {"resource": LINGER_RESOURCE, "value": 1},
            },
            sort_key=(1,), **common,
        ),
        # Two marks, not one: ``delayed_death`` is what the day's sweep looks
        # for, ``lingering_exile`` is what turns that pending death's cause back
        # into ``exile``. The seat was exiled — it just dies later.
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.ADD_STATUS,
            context.action_key, target_seat=seat,
            payload={"target": seat, "status": DELAYED_DEATH_STATUS},
            preconditions={
                "status_present": {"status": DELAYED_DEATH_STATUS, "present": False},
            },
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.ADD_STATUS,
            context.action_key, target_seat=seat,
            payload={"target": seat, "status": LINGERING_EXILE_STATUS},
            preconditions={
                "status_present": {"status": LINGERING_EXILE_STATUS, "present": False},
            },
            sort_key=(3,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 4), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "EXILE_CANCELLED",
                "payload": {"target_seat": seat, "round_number": context.round_number},
            },
            visibility=("PUBLIC",), sort_key=(4,), **common,
        ),
    )


CRIMSON_LINGER_CONTRACT = ActionContract(
    contract_id="crimson_linger",
    schedule_point=SchedulePoint.EXILE_VERDICT,
    order=20,
    action_types=("linger",),
    actions_requiring_target=frozenset(),
    fallback_action_type="linger",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    response_event_types=frozenset({"EXILE_PENDING"}),
    response_reasons=frozenset({"exile"}),
    per_window_limit=1,
    per_game_limit=1,
    is_applicable=crimson_linger_applicable,
    react=react_crimson_linger,
)

CRIMSON_ENVOY_SPEC = RoleSpec(
    tags=frozenset({"wolf"}),
    role_id="wolf-killer-crimson-envoy",
    display_name="Crimson Envoy",
    camp_id="werewolf",
    contracts=(
        WEREWOLF_KILL_CONTRACT, CRIMSON_EXPLODE_CONTRACT,
        CRIMSON_LINGER_CONTRACT, AWAKENED_CLAW_GIFT_CONTRACT,
    ),
    initial_resources={EXPLODE_RESOURCE: 1, LINGER_RESOURCE: 1},
    initial_private_data={AWAKENED_CLAW_GIFT_KEY: None},
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    instructions=(
        "Crimson Envoy is a werewolf: at night it joins the wolf channel, the shared "
        "discussion and the shared kill vote like any other werewolf. During the day, before "
        "each player's speech in the speech phase, it is asked once whether to self-destruct. "
        "Self-destructing ends the day at once (no remaining speeches, no exile vote) and "
        "seals EVERY living good player's skill for the coming night: the guard cannot "
        "protect, the seer cannot check, the witch cannot use a potion, the hunter cannot "
        "react. It takes nobody along and is used only once. It cannot be used after the "
        "speeches are over. The other half of the card is passive: if Crimson Envoy is the "
        "last werewolf and the vote exiles it, it survives and dies after the next dawn "
        "instead, which buys the wolf team one more night — but it dies before that day's "
        "vote, so it cannot vote again. Weigh every window: exploding early cancels a day the "
        "good camp could have used, while exploding late may waste the one night it buys."
    ),
)


class CrimsonEnvoy(Werewolf):
    """Crimson Envoy: the daytime self-destruct and the lasting exile are both
    pipeline contracts; the daytime instance speaks and votes like a werewolf."""

