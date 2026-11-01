from __future__ import annotations
from collections.abc import Mapping

from app.models.game import DEFAULT_WITCH_SELF_SAVE
from app.roles.base import BaseRole
from app.core.effect_applier import derive_effect_id
from app.roles.reasoning import reasoning_effect
from app.core.reflect import REFLECT_POISON_RESOURCE, reflect_effects
from app.core.time_lock import is_locked, lock_hit_effect
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)

def witch_applicable(context: ActionContext) -> bool:
    # A living witch still receives nightly information after both potions are spent.
    return context.actor_alive


def validate_witch_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "save" and context.resources.get("antidote", 0) <= 0:
        return (RuleViolation("antidote_unavailable", "antidote is unavailable"),)
    if command.action_type == "save" and command.target_seat != context.facts.get("wolf_kill_target"):
        return (RuleViolation("invalid_save_target", "save target must be the wolf kill target"),)
    if command.action_type == "save" and command.target_seat == context.actor_seat:
        # The policy travels in the context, so the hook carries no board branch.
        if context.facts.get("witch_self_save", DEFAULT_WITCH_SELF_SAVE) == "never":
            return (RuleViolation(
                "witch_self_save_forbidden",
                "the witch can never save herself in this game",
            ),)
        if context.round_number != 1:
            return (RuleViolation(
                "self_save_first_night_only",
                "the witch can only save herself on the first night",
            ),)
    if command.action_type == "poison" and context.resources.get("poison", 0) <= 0:
        return (RuleViolation("poison_unavailable", "poison is unavailable"),)
    return ()


def _witch_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "save" and command.target_seat is not None:
        thought = f"决定使用解药救 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    elif command.action_type == "poison" and command.target_seat is not None:
        thought = f"决定使用毒药毒杀 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不使用药水：{command.reasoning or '无理由'}"
    return reasoning_effect(
        context, command, ordinal, event_type="WITCH_REASONING",
        thought=thought, **common,
    )


def resolve_witch_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    if command.action_type == "pass":
        return (_witch_reasoning_effect(
            context, command, 1,
            expected_revision=context.revision,
            source_event_id=context.source_event_id,
        ),)
    resource = "antidote" if command.action_type == "save" else "poison"
    outcome = EffectKind.SUBMIT_PROTECTION if command.action_type == "save" else EffectKind.SUBMIT_DAMAGE
    target = command.target_seat
    selected = context.facts.get("selected_target")
    # The Time Wolf's lock redirects a *poison* to the witch herself; the antidote
    # is not in the card's list, so a save is never redirected.
    locked = command.action_type == "poison" and is_locked(selected)
    if locked:
        target = context.actor_seat
    outcome_payload = {"target": target, "amount": 1}
    if command.action_type == "save":
        outcome_payload["source"] = "witch_antidote"
    if command.action_type == "poison":
        outcome_payload["cause"] = "poison"
    common = {"expected_revision": context.revision, "source_event_id": context.source_event_id}
    emit_type = "WITCH_SAVE" if command.action_type == "save" else "WITCH_POISON"
    effects = [
        GameEffect(derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "resource": resource, "amount": 1},
            preconditions={"resource_equals": {"resource": resource, "value": 1}},
            sort_key=(1,), **common),
        GameEffect(derive_effect_id(context.action_key, 2), outcome, context.action_key,
            target_seat=target, payload=outcome_payload, sort_key=(2,), **common),
    ]
    if command.action_type == "save":
        effects.append(
            GameEffect(
                derive_effect_id(context.action_key, 3),
                EffectKind.SET_PRIVATE_DATA,
                context.action_key,
                target_seat=context.actor_seat,
                payload={"target": context.actor_seat, "key": "saved_seat", "value": target},
                visibility=("ACTOR",),
                sort_key=(3,),
                **common,
            )
        )
    idx = len(effects) + 1
    effects.append(_witch_reasoning_effect(context, command, idx, **common))
    idx += 1
    outcome_event: dict[str, object] = {
        "target_seat": target, "seat": context.actor_seat,
    }
    if locked:
        # The thought keeps the model's original target; this names the redirect
        # so the god view can tell intent from settlement.
        outcome_event["intended_seat"] = command.target_seat
    effects.append(
        GameEffect(derive_effect_id(context.action_key, idx), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": emit_type, "payload": outcome_event},
            visibility=("PUBLIC",), sort_key=(idx,), **common),
    )
    if locked:
        effects.append(lock_hit_effect(context, selected, len(effects) + 1))
    elif command.action_type == "poison" and isinstance(selected, Mapping):
        effects.extend(reflect_effects(
            context, selected, REFLECT_POISON_RESOURCE, len(effects) + 1,
        ))
    return tuple(effects)


WITCH_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-witch", display_name="Witch", camp_id="good",
    schema_version=2,
    contracts=(ActionContract(
        contract_id="witch_action", schedule_point=SchedulePoint.NIGHT_WITCH_ACTION, order=20,
        action_types=("save", "poison", "pass"),
        actions_requiring_target=frozenset({"save", "poison"}), fallback_action_type="pass",
        allowed_effects=frozenset({
            EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_PROTECTION,
            EffectKind.SUBMIT_DAMAGE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
            EffectKind.SET_RESOURCE,
        }),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        selected_target_fact_namespaces=frozenset({"resource_labels", "status_labels"}),
        per_window_limit=1, per_round_limit=1,
        is_applicable=witch_applicable, validate=validate_witch_action, resolve=resolve_witch_action,
    ),),
    initial_resources={"antidote": 1, "poison": 1},
    initial_private_data={"wolf_kill_target": None, "saved_seat": None},
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_PROTECTION,
        EffectKind.SUBMIT_DAMAGE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
        EffectKind.SET_RESOURCE,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions=(
        "The Witch has one antidote and one poison, each usable once per game. "
        "While alive, you learn the werewolf-kill target every night, even after "
        "either or both potions are spent; a null target means no werewolf attack. "
        "If both potions are spent, choose pass and use the target as information. "
        "Each night use at most one potion: "
        "the antidote may save only that night's werewolf-kill target, and whether it "
        "may be used on yourself is the game setting PROJECTED_CONTEXT carries as "
        "`witch_self_save` — `first_night_only` allows a first-night self-save, `never` "
        "forbids it on every night; the setting always wins over any general habit, "
        "while poison may "
        "target one living player and ignores Guard protection. If the antidote and Guard "
        "protection both target that werewolf-kill target, the target dies by double-save "
        "penetration. "
        "Tactical advice: "
        "1) Antidote usage: Using the antidote on night 1 is NOT mandatory. While saving protects a potential good player, it also risks saving a self-killing werewolf (自刀狼) and exhausts your only save early. In games where witch_self_save is 'never', saving on night 1 leaves you permanently defenseless. Holding the antidote (choose pass) to protect a confirmed Seer or key god on later nights, or guarding against deceptive self-kills, is an equally valid tactical choice. Weigh the casualty risk against potion conservation. "
        "2) Identity protection: If special werewolves with daytime kill capabilities (such as the Werewolf King) "
        "are present, revealing your Witch identity prematurely during daytime speech carries a severe "
        "risk of being immediately eliminated, losing your remaining potions and night Intel. "
        "Consider hiding your identity or only taking the lead when you are prepared to poison key threats."
    ),
)


class Witch(BaseRole):
    """Witch role: one antidote, one poison. Only one potion per night."""
