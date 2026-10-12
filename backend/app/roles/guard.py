from app.core.effect_applier import derive_effect_id
from app.core.time_lock import is_locked, lock_hit_effect
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect,
    RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.target_memory import is_consecutive_repeat, target_memory

#: Private slot: who the Guard protected last, and on which night.
LAST_GUARDED_KEY = "last_guarded"
_GUARD_EFFECTS = frozenset({
    EffectKind.SUBMIT_PROTECTION, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    # Marks a Time Wolf lock that took effect (core/time_lock.py).
    EffectKind.SET_RESOURCE,
})


def guard_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_guard_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "pass" or command.target_seat is None:
        return ()
    if is_consecutive_repeat(context, LAST_GUARDED_KEY, command.target_seat):
        return (RuleViolation("consecutive_guard", "the same seat cannot be guarded two consecutive nights"),)
    return ()


def _guard_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "guard" and command.target_seat is not None:
        thought = f"决定守护 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不守护：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "GUARD_REASONING",
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


def resolve_guard_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_guard_reasoning_effect(context, command, 1, **common),)
    # The Time Wolf's lock: a guard cast at the locked seat protects the guard himself.
    selected = context.facts.get("selected_target")
    locked = is_locked(selected)
    target = context.actor_seat if locked else command.target_seat
    effects = (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_PROTECTION,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "source": "guard"},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload=target_memory(
                context.actor_seat, LAST_GUARDED_KEY, target, context.round_number,
            ),
            sort_key=(2,), **common,
        ),
        _guard_reasoning_effect(context, command, 3, **common),
        GameEffect(
            derive_effect_id(context.action_key, 4), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": "GUARD_PROTECT", "payload": {"target_seat": target}},
            visibility=("PUBLIC",), sort_key=(4,), **common,
        ),
    )
    return effects + ((lock_hit_effect(context, selected, 5),) if locked else ())


GUARD_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-guard",
    display_name="Guard",
    camp_id="good",
    opening_narration=("守卫请睁眼", "请选择今晚要守护的玩家。"),
    contracts=(ActionContract(
        contract_id="guard_action",
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=50,
        action_types=("guard", "pass"),
        actions_requiring_target=frozenset({"guard"}),
        fallback_action_type="pass",
        allowed_effects=_GUARD_EFFECTS,
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        # Only to learn whether the Time Wolf locked the seat (core/time_lock.py).
        selected_target_fact_namespaces=frozenset({"status_labels"}),
        is_applicable=guard_applicable,
        validate=validate_guard_action,
        resolve=resolve_guard_action,
    ),),
    initial_private_data={LAST_GUARDED_KEY: None},
    allowed_effects=_GUARD_EFFECTS,
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions=(
        "Each night Guard may protect one living player or pass; the same seat cannot be "
        "protected on two consecutive nights (a night the Guard spent passing is not one of "
        "them). Guard protection only cancels werewolf-kill damage; "
        "poison and hunter shots ignore it. If Guard protection and Witch antidote both target "
        "the same werewolf-kill target, that target dies by double-save penetration. "
        "Tactical advice: The Guard is a critical unverified god. Focus on discerning the real Seer "
        "and protecting key targets from wolf kills while avoiding double-save conflicts with the Witch. "
        "During the day, avoid revealing your identity lightly and stay vigilant against deceptive gold-water claims."
    ),
)


class Guard(BaseRole):
    """Guard role: nightly protection is handled by the pipeline contract;

    the daytime instance only speaks and votes like a villager.
    """
    pass
