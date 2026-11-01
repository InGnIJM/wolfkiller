from __future__ import annotations
from collections.abc import Mapping
from app.roles.base import BaseRole
from app.core.effect_applier import derive_effect_id
from app.roles.reasoning import reasoning_effect
from app.core.reflect import REFLECT_CHECK_RESOURCE, reflect_effects
from app.core.time_lock import SELF_CHECK_CAMP, is_locked, lock_hit_effect
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)

def seer_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_seer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "check" and command.target_seat == context.actor_seat:
        return (RuleViolation("self_check_forbidden", "seer cannot check self"),)
    return ()


def _seer_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "check" and command.target_seat is not None:
        thought = f"决定查验 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不查验：{command.reasoning or '无理由'}"
    return reasoning_effect(
        context, command, ordinal, event_type="SEER_REASONING",
        thought=thought, **common,
    )


def resolve_seer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    if command.action_type == "pass":
        return (_seer_reasoning_effect(
            context, command, 1,
            expected_revision=context.revision,
            source_event_id=context.source_event_id,
        ),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    camp = selected.get("camp_label")
    if type(camp) is not str:
        raise TypeError("selected target camp label must be a string")
    checked = command.target_seat
    locked = is_locked(selected)
    if locked:
        # The Time Wolf's lock: the check is cast at the seer herself.
        checked, camp = context.actor_seat, SELF_CHECK_CAMP
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1),
            EffectKind.RECORD_PRIVATE_FACT,
            context.action_key,
            payload={
                "target": context.actor_seat,
                "namespace": "private_checks",
                "fact": {"target": checked, "camp": camp},
            },
            visibility=("ACTOR",),
            expected_revision=context.revision,
            target_seat=context.actor_seat,
            source_event_id=context.source_event_id,
            sort_key=(1,),
        ),
        _seer_reasoning_effect(
            context, command, 2,
            expected_revision=context.revision,
            source_event_id=context.source_event_id,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3),
            EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "SEER_CHECK",
                "payload": {
                    "target_seat": checked,
                    "result": camp,
                    "seat": context.actor_seat,
                    **({"intended_seat": command.target_seat} if locked else {}),
                },
            },
            visibility=("PUBLIC",),
            expected_revision=context.revision,
            source_event_id=context.source_event_id,
            sort_key=(3,),
        ),
    ) + (
        (lock_hit_effect(context, selected, 4),) if locked
        else reflect_effects(context, selected, REFLECT_CHECK_RESOURCE, 4)
    )


SEER_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-seer",
    display_name="Seer",
    camp_id="good",
    # The identity-check point is shared, so every skill sitting on it declares
    # its own line; the driver's beat is only the fallback for a role that does
    # not (it names the Seer, which such a board may not even have).
    opening_narration=("预言家请睁眼", "请查验一名玩家的身份。"),
    schema_version=2,
    contracts=(ActionContract(
        contract_id="seer_check",
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
        is_applicable=seer_applicable,
        validate=validate_seer_action,
        resolve=resolve_seer_action,
    ),),
    initial_private_data={"private_checks": ()},
    allowed_effects=frozenset({
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT,
        EffectKind.SET_RESOURCE, EffectKind.SUBMIT_DAMAGE,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions=(
        "Each night the Seer may check one other living player's camp. The engine returns "
        "only that checked camp result as the Seer's private fact. "
        "Tactical advice: Your checks are the good camp's scarcest information, so use them to "
        "take the lead: claim early and give a clear verdict (a gold-water or a kill) with your "
        "planned check order, because a Seer who stays silent loses the table to a counterclaiming "
        "wolf. State which seats you will check next so the vote has something to follow, and "
        "leave your checks in your last words if you may die. If a counterclaim appears, compare "
        "the claims by their logic and by who each side protects instead of asserting that you "
        "are simply the real one."
    ),
)


class Seer(BaseRole):
    """Seer role: checks one player's alignment each night."""
