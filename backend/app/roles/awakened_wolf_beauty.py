"""Awakened Wolf Beauty: the charm that dies in her place.

The ordinary Wolf Beauty drags the charmed player along when she leaves; this
one is the mirror image — the first time she would leave the game, the charmed
player is *replaced* into the grave and she survives, after which the skill is
spent for good. The game has to see three different things:

* a **night** death, which the settlement redirects (``stand_in`` / the redirect
  in ``core/night_settlement.py``) — no card flip, no last words;
* a **day** exile, which the ``EXILE_VERDICT`` react below cancels before the
  engine ever marks her dead: she flips her card, the stand-in dies, and the
  stand-in's own death reaction runs (he gets last words, a hunter among them
  still shoots);
* an **unreplaceable** hit — the Awakened Hunter's night shot — which the
  settlement honours instead, leaving this skill unspent but useless: she dies.

``stand_in_used`` is what makes the effect once-per-game. The charm may be
re-cast every other night (``last_illusion`` holds the round), which is the other
half of the official text: the skill is not a one-shot, its *survival* is.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import (
    STAND_IN_CAUSE, STAND_IN_RESOURCE, STAND_IN_USED_RESOURCE,
)
from app.core.sheriff_flow import NO_SELF_EXPLODE_RESOURCE
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.target_memory import target_memory
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf
from app.roles.wolf_claw import (
    AWAKENED_CLAW_GIFT_CONTRACT, AWAKENED_CLAW_GIFT_KEY,
)

#: Private slot holding ``{"seat": n, "round": r}`` for the last charm, the same
#: shape every other nightly role writes (see ``roles/target_memory.py``).
LAST_ILLUSION_KEY = "last_illusion"
_CHARM = "charm"
_REASONING_EVENT = "AWAKENED_BEAUTY_REASONING"
_ILLUSION_EVENT = "WRAITH_ILLUSION"


def awakened_beauty_applicable(context: ActionContext) -> bool:
    """Every *other* night, until the replacement has happened.

    The cadence is read from the recorded round, never from "was there a
    record": a night the model failed, passed or was silenced leaves the slot
    untouched, and comparing tonight against that night would misread a skipped
    night as last night (the bug ``roles/target_memory.py`` documents). A record
    the hook cannot date — a bare seat, or one without a round — counts as last
    night and keeps the window shut rather than guessing.
    """
    if not context.actor_alive or context.resources.get(STAND_IN_USED_RESOURCE, 0) > 0:
        return False
    value = context.facts.get(LAST_ILLUSION_KEY)
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    last = value.get("round")
    return type(last) is int and context.round_number - last >= 2


def validate_awakened_beauty_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != _CHARM:
        return ()
    if command.target_seat is None:
        return (RuleViolation("charm_target_required", "the illusion needs a target"),)
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_target", "the illusion cannot charm its own caster"),)
    return ()


def _awakened_beauty_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == _CHARM and command.target_seat is not None:
        thought = f"决定魅惑 {command.target_seat} 号玩家作为替身：{command.reasoning or '无理由'}"
    else:
        thought = f"决定本晚不发动挽歌幻象：{command.reasoning or '无理由'}"
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


def resolve_awakened_beauty_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != _CHARM or command.target_seat is None:
        return (_awakened_beauty_reasoning_effect(context, command, 1, **common),)
    actor, target = context.actor_seat, command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": STAND_IN_RESOURCE, "value": target},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload=target_memory(actor, LAST_ILLUSION_KEY, target, context.round_number),
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": _ILLUSION_EVENT,
                "payload": {
                    "seat": actor, "target_seat": target,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _awakened_beauty_reasoning_effect(context, command, 4, **common),
    )


def _stand_in_seat(context: ActionContext) -> int | None:
    """The seat that would replace this one: alive, someone else, still charmed."""
    if context.resources.get(STAND_IN_USED_RESOURCE, 0) > 0:
        return None
    seat = context.resources.get(STAND_IN_RESOURCE, 0)
    if type(seat) is not int or seat <= 0 or seat == context.actor_seat:
        return None
    alive = context.facts.get("alive_seats", ())
    if type(alive) is not tuple or seat not in alive:
        return None
    return seat


def awakened_beauty_stand_in_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason == "exile"
        # The verdict runs before the engine marks the exiled seat dead, so this
        # react is the last moment the flip can still cancel the exile.
        and context.actor_alive
        and _stand_in_seat(context) is not None
    )


def react_awakened_beauty_stand_in(context: ActionContext) -> tuple[GameEffect, ...]:
    """Send the stand-in instead: flip the card, cancel the exile, spend the skill.

    Pure and deterministic — the replacement is the rules' answer, not a choice.
    Response windows do not consult ``is_applicable`` before reacting, so the
    hook re-checks it and stays silent for every state the skill does not cover
    (no charm, a dead stand-in, or a second exile after the effect was spent).
    """
    if not awakened_beauty_stand_in_applicable(context):
        return ()
    substitute = _stand_in_seat(context)
    identity = context.facts.get("actor_identity") or {}
    seat = context.actor_seat
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.MARK_DEATH,
            context.action_key, target_seat=substitute,
            payload={"target": substitute, "cause": STAND_IN_CAUSE},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": STAND_IN_RESOURCE, "value": 0},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": STAND_IN_USED_RESOURCE, "value": 1},
            sort_key=(3,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 4), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "PLAYER_REVEALED",
                "payload": {
                    "seat_number": seat,
                    "role": identity.get("role_id", context.actor_role_id),
                    "camp": identity.get("camp_id", ""),
                },
            },
            visibility=("PUBLIC",), sort_key=(4,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 5), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "EXILE_CANCELLED",
                "payload": {"target_seat": seat, "round_number": context.round_number},
            },
            visibility=("PUBLIC",), sort_key=(5,), **common,
        ),
    )


AWAKENED_BEAUTY_CHARM_CONTRACT = ActionContract(
    contract_id="awakened_beauty_charm",
    # The witch stage runs after the wolf kill vote, so the illusion lands
    # "after taking part in the kill" and still settles with the same night.
    schedule_point=SchedulePoint.NIGHT_WITCH_ACTION,
    order=55,
    action_types=(_CHARM, "pass"),
    actions_requiring_target=frozenset({_CHARM}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.SET_RESOURCE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    per_window_limit=1,
    is_applicable=awakened_beauty_applicable,
    validate=validate_awakened_beauty_action,
    resolve=resolve_awakened_beauty_action,
)

AWAKENED_BEAUTY_STAND_IN_CONTRACT = ActionContract(
    contract_id="awakened_beauty_stand_in",
    schedule_point=SchedulePoint.EXILE_VERDICT,
    order=35,
    action_types=("stand_in",),
    actions_requiring_target=frozenset(),
    fallback_action_type="stand_in",
    allowed_effects=frozenset({
        EffectKind.MARK_DEATH, EffectKind.SET_RESOURCE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    response_event_types=frozenset({"EXILE_PENDING"}),
    response_reasons=frozenset({"exile"}),
    # No ``per_game_limit``: the registry requires a one-shot contract to spend a
    # resource, and what this contract spends is the ``stand_in`` marker itself
    # (a seat number, zeroed by an explicit SET_RESOURCE). The once-per-game
    # guarantee is the marker plus ``stand_in_used``, both asserted in the tests.
    per_window_limit=1,
    is_applicable=awakened_beauty_stand_in_applicable,
    react=react_awakened_beauty_stand_in,
)


AWAKENED_WOLF_BEAUTY_SPEC = RoleSpec(
    role_id="wolf-killer-awakened-wolf-beauty", display_name="Awakened Wolf Beauty",
    camp_id="werewolf",
    contracts=(
        WEREWOLF_KILL_CONTRACT,
        AWAKENED_BEAUTY_CHARM_CONTRACT,
        AWAKENED_BEAUTY_STAND_IN_CONTRACT,
        AWAKENED_CLAW_GIFT_CONTRACT,
    ),
    initial_private_data={LAST_ILLUSION_KEY: None, AWAKENED_CLAW_GIFT_KEY: None},
    # ``stand_in`` is the seat the illusion would send instead; ``stand_in_used``
    # records that the replacement already happened. Both start at zero, so the
    # charm contract reads a declared key rather than a missing one.
    initial_resources={
        STAND_IN_RESOURCE: 0,
        STAND_IN_USED_RESOURCE: 0,
        # Same two markers the ordinary Wolf Beauty declares: the pack may never
        # pick her as its own knife target, and she can never self-destruct.
        "self_kill_forbidden": 1,
        NO_SELF_EXPLODE_RESOURCE: 1,
    },
    allowed_effects=frozenset({
        EffectKind.SUBMIT_DAMAGE, EffectKind.SET_RESOURCE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.MARK_DEATH, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    tags=frozenset({"wolf"}),
    max_count=1,
    # The illusion shares the witch's point, so she declares her own line there.
    opening_narration=("觉醒狼美人请睁眼", "请魅惑一名玩家作为替身（每隔一晚一次）。"),
    instructions=(
        "Awakened Wolf Beauty is a werewolf: at night it joins the wolf channel and the "
        "shared kill vote like any other werewolf, and it can never be the pack's own kill "
        "target nor self-destruct. After the kill vote it may separately charm one living "
        "player as its stand-in, but only every *other* night — a night after a charm night "
        "cannot charm again. The first time the game would eliminate you, the charmed player "
        "is sent instead: you survive, and the skill is spent for good, so the second attempt "
        "on your life lands normally. If the stand-in is already dead when you are eliminated, "
        "or you never charmed anyone, nothing replaces you. A night elimination is silent, "
        "but an exile makes you flip your card first — the stand-in dies, you keep playing, "
        "and he gets the last words you no longer have. The one hit that ignores the illusion "
        "is the night shot of a hunter who died in the night, so the stand-in also protects "
        "you from every other kind of damage that night. Charm where the replacement hurts "
        "the good camp most, and remember that keeping the skill unspent keeps you able to "
        "survive exactly one elimination."
    ),
)


class AwakenedWolfBeauty(Werewolf):
    """Werewolf whose illusion and replacement are handled by pipeline contracts;
    the daytime instance speaks and votes like any other werewolf."""

    def get_skills(self) -> list[str]:
        return ["kill", "charm"]
