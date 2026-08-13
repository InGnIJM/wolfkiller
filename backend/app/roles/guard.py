from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect,
    RoleSpec, RuleViolation, SchedulePoint,
)


def guard_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_guard_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "pass" or command.target_seat is None:
        return ()
    if context.facts.get("last_guarded") == command.target_seat:
        return (RuleViolation("consecutive_guard", "the same seat cannot be guarded two consecutive nights"),)
    return ()


def resolve_guard_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    if command.action_type == "pass":
        return ()
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_PROTECTION,
            context.action_key, target_seat=command.target_seat,
            payload={"target": command.target_seat, "amount": 1},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "key": "last_guarded",
                     "value": command.target_seat},
            sort_key=(2,), **common,
        ),
    )


GUARD_SPEC = RoleSpec(
    role_id="wolf-killer-guard",
    display_name="Guard",
    camp_id="good",
    contracts=(ActionContract(
        contract_id="guard_action",
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=50,
        action_types=("guard", "pass"),
        actions_requiring_target=frozenset({"guard"}),
        fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.SUBMIT_PROTECTION, EffectKind.SET_PRIVATE_DATA}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        is_applicable=guard_applicable,
        validate=validate_guard_action,
        resolve=resolve_guard_action,
    ),),
    initial_private_data={"last_guarded": None},
    allowed_effects=frozenset({EffectKind.SUBMIT_PROTECTION, EffectKind.SET_PRIVATE_DATA}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions="Guard one living player each night; the same seat cannot be guarded twice in a row.",
)
