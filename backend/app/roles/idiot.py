from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec, SchedulePoint,
)
from app.roles.base import BaseRole

# Statuses the vote domain reads (see app.core.vote_service).
_NO_VOTE = "no_vote"
_EXILE_IMMUNE = "exile_immune"


def idiot_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason == "exile"
        and context.actor_alive
        and context.resources.get("flip", 0) > 0
    )


def react_idiot_flip(context: ActionContext) -> tuple[GameEffect, ...]:
    """Flip the card: survive the exile, lose the ballot, become immune to
    further exiles. Pure and deterministic — no model call is involved.

    Response windows do not consult ``is_applicable`` before reacting, so the
    hook re-checks it and stays silent once the card has already been used."""
    if not idiot_applicable(context):
        return ()
    identity = context.facts.get("actor_identity") or {}
    seat = context.actor_seat
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": "flip", "amount": 1},
            preconditions={"resource_equals": {"resource": "flip", "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.ADD_STATUS,
            context.action_key, target_seat=seat,
            payload={"target": seat, "status": _NO_VOTE},
            preconditions={"status_present": {"status": _NO_VOTE, "present": False}},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.ADD_STATUS,
            context.action_key, target_seat=seat,
            payload={"target": seat, "status": _EXILE_IMMUNE},
            preconditions={"status_present": {"status": _EXILE_IMMUNE, "present": False}},
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


IDIOT_SPEC = RoleSpec(
    role_id="wolf-killer-idiot", display_name="Idiot", camp_id="good",
    contracts=(ActionContract(
        contract_id="idiot_flip", schedule_point=SchedulePoint.EXILE_VERDICT,
        order=30, action_types=("flip",),
        actions_requiring_target=frozenset(), fallback_action_type="flip",
        allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        response_event_types=frozenset({"EXILE_PENDING"}),
        response_reasons=frozenset({"exile"}),
        per_window_limit=1, per_game_limit=1,
        is_applicable=idiot_applicable, react=react_idiot_flip,
    ),),
    initial_resources={"flip": 1},
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "Idiot is a good-camp god role with no night action. If the day vote exiles Idiot, "
        "the card is flipped automatically: Idiot's identity becomes public, Idiot stays in "
        "the game and may keep speaking, but permanently loses the right to vote and can no "
        "longer be exiled by vote. Being killed at night, poisoned, or taken along by another "
        "player's skill still eliminates Idiot normally. Idiot counts as a god for win checks."
    ),
)


class Idiot(BaseRole):
    """Idiot role: the exile flip is handled by the pipeline contract; the
    daytime instance only speaks and votes like a villager (until flipped)."""
    pass
