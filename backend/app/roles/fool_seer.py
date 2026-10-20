"""Fool Seer: a good god whose check matches the Seer's, and who may flip.

The check is its own contract. Reusing the Seer's would change that contract's
digest and drift every archive that seated a Seer.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.core.reflect import REFLECT_CHECK_RESOURCE, reflect_effects
from app.core.time_lock import SELF_CHECK_CAMP, is_locked, lock_hit_effect
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.self_reveal import REVEAL_RESOURCE, resolve_reveal_action, reveal_contract

_REASONING_EVENT = "FOOL_SEER_REASONING"
_CHECK_EVENT = "FOOL_SEER_CHECK"


def resolve_fool_seer_reveal(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    return resolve_reveal_action(
        context, command, event_type="FOOL_SEER_REVEAL_REASONING",
    )


def fool_seer_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_fool_seer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "check" and command.target_seat == context.actor_seat:
        return (RuleViolation("self_check_forbidden", "the check cannot target the checker"),)
    return ()


def _fool_seer_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "check" and command.target_seat is not None:
        thought = f"决定查验 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不查验：{command.reasoning or '无理由'}"
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


def resolve_fool_seer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_fool_seer_reasoning_effect(context, command, 1, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    camp = selected.get("camp_label")
    if type(camp) is not str:
        raise TypeError("selected target camp label must be a string")
    checked = command.target_seat
    locked = is_locked(selected)
    if locked:
        checked, camp = context.actor_seat, SELF_CHECK_CAMP
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.RECORD_PRIVATE_FACT,
            context.action_key,
            payload={
                "target": context.actor_seat,
                "namespace": "private_checks",
                "fact": {"target": checked, "camp": camp},
            },
            visibility=("ACTOR",),
            target_seat=context.actor_seat,
            sort_key=(1,), **common,
        ),
        _fool_seer_reasoning_effect(context, command, 2, **common),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": _CHECK_EVENT,
                "payload": {
                    "target_seat": checked,
                    "result": camp,
                    "seat": context.actor_seat,
                    **({"intended_seat": command.target_seat} if locked else {}),
                },
            },
            visibility=("PUBLIC",),
            sort_key=(3,), **common,
        ),
    ) + (
        (lock_hit_effect(context, selected, 4),) if locked
        else reflect_effects(context, selected, REFLECT_CHECK_RESOURCE, 4)
    )


FOOL_SEER_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-fool-seer",
    display_name="Fool Seer",
    camp_id="good",
    opening_narration=("愚言家请睁眼", "请查验一名玩家的身份。"),
    opening_points=frozenset({SchedulePoint.NIGHT_SEER_ACTION.value}),
    schema_version=2,
    contracts=(
        ActionContract(
            contract_id="fool_seer_check",
            schedule_point=SchedulePoint.NIGHT_SEER_ACTION,
            order=30,
            action_types=("check", "pass"),
            actions_requiring_target=frozenset({"check"}),
            fallback_action_type="pass",
            allowed_effects=frozenset({
                EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT,
                EffectKind.SET_RESOURCE, EffectKind.SUBMIT_DAMAGE,
            }),
            visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
            selected_target_fact_namespaces=frozenset({
                "camp_label", "resource_labels", "status_labels",
            }),
            per_window_limit=1,
            per_round_limit=1,
            is_applicable=fool_seer_applicable,
            validate=validate_fool_seer_action,
            resolve=resolve_fool_seer_action,
        ),
        reveal_contract(
            contract_id="fool_seer_reveal",
            order=15,
            resolve=resolve_fool_seer_reveal,
        ),
    ),
    initial_resources={REVEAL_RESOURCE: 1},
    initial_private_data={"private_checks": ()},
    allowed_effects=frozenset({
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT,
        EffectKind.SET_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.CONSUME_RESOURCE,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions=(
        "Each night you may check one other living player's current allegiance. The "
        "answer is good, werewolf, or third_party, and it is your private fact. During "
        "the day, before a speech, you may flip your card once and show the table who "
        "you are. Flipping does not kill you, does not take your vote, and does not "
        "protect you from exile."
    ),
)


class FoolSeer(BaseRole):
    """Daytime instance: the check and the flip are pipeline contracts."""

    pass
