"""Pure White: a good god that learns one exact identity a night.

From the second night on, the check is also a sentence: a werewolf it names
dies the same night (``verify_kill``). Both facts it needs — the target's camp
and its exact role — travel through ``selected_target``, which reaches the pure
hooks only and never the model's prompt, so the check stays private even though
the hook reads the answer.

The answer itself is recorded twice on purpose: ``private_checks`` carries the
camp in the shape every other checker uses (and the projector already reads it),
while ``role_checks`` carries the exact identity through the namespace the
Gargoyle introduced. Without that second record the role would learn nothing
its prompt could show.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

#: Private-fact namespace holding the exact identity of every seat checked.
ROLE_CHECK_NAMESPACE = "role_checks"
#: Private-fact namespace holding the camp of every seat checked.
CHECK_NAMESPACE = "private_checks"
_VERIFY_CAUSE = "verify_kill"
_WEREWOLF_CAMP = "werewolf"
#: The first night is a plain look; the sentence starts with the second.
_FIRST_KILL_ROUND = 2


def pure_white_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_pure_white_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "check" and command.target_seat == context.actor_seat:
        return (RuleViolation(
            "self_check_forbidden", "the Pure White cannot check itself",
        ),)
    return ()


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, *,
    kills: bool, **common: object,
) -> GameEffect:
    if command.action_type == "check" and command.target_seat is not None:
        verdict = "，其身份是狼人，当夜处决" if kills else ""
        thought = (
            f"决定查验 {command.target_seat} 号玩家{verdict}："
            f"{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不查验：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "PURE_WHITE_REASONING",
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seat": command.target_seat,
                "reasoning": command.reasoning,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_pure_white_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "check" or command.target_seat is None:
        return (_reasoning_effect(context, command, 1, kills=False, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    camp = selected.get("camp_label")
    if type(camp) is not str:
        raise TypeError("selected target camp label must be a string")
    identity = selected.get("role_label")
    if type(identity) is not str:
        raise TypeError("selected target role label must be a string")
    kills = camp == _WEREWOLF_CAMP and context.round_number >= _FIRST_KILL_ROUND
    actor, target = context.actor_seat, command.target_seat
    effects = [
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.RECORD_PRIVATE_FACT,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "namespace": CHECK_NAMESPACE,
                "fact": {"target": target, "camp": camp},
            },
            visibility=("ACTOR",), sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.RECORD_PRIVATE_FACT,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "namespace": ROLE_CHECK_NAMESPACE,
                "fact": {"target": target, "role": identity},
            },
            visibility=("ACTOR",), sort_key=(2,), **common,
        ),
    ]
    if kills:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": _VERIFY_CAUSE},
            sort_key=(3,), **common,
        ))
    effects.append(_reasoning_effect(
        context, command, len(effects) + 1, kills=kills, **common,
    ))
    return tuple(effects)


PURE_WHITE_CHECK_CONTRACT = ActionContract(
    contract_id="pure_white_check",
    schedule_point=SchedulePoint.NIGHT_SEER_ACTION,
    # Before the Seer's own check (30) so the order stays readable in the
    # audience feed; the two never share a board anyway.
    order=20,
    action_types=("check", "pass"),
    actions_requiring_target=frozenset({"check"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"camp_label", "role_label"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=pure_white_applicable,
    validate=validate_pure_white_action,
    resolve=resolve_pure_white_action,
)

PURE_WHITE_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-pure-white",
    display_name="Pure White",
    camp_id="good",
    contracts=(PURE_WHITE_CHECK_CONTRACT,),
    initial_private_data={CHECK_NAMESPACE: (), ROLE_CHECK_NAMESPACE: ()},
    allowed_effects=frozenset({
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "Pure White is a good-camp god. Every night it may check one other living player and "
        "learn that player's EXACT identity (the specific role card, not just the camp). From "
        "the second night on, if the checked player is a werewolf, that werewolf dies the same "
        "night — the kill is automatic and cannot be declined. A check that lands on a good "
        "player never kills anyone. The answer is private: only you receive it. Use it by "
        "talking, since you have no other way to prove what you saw. Checking the same seat "
        "twice is allowed but rarely useful. Weigh each night against win probability: the "
        "sooner you name a wolf the more nights the good camp gets out of it, but naming the "
        "wrong seat wastes the only information source you have."
    ),
)


class PureWhite(BaseRole):
    """Pure White: its nightly check is the pipeline contract."""
