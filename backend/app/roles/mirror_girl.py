"""Mirror Girl: a god who learns one seat's *exact* identity every night.

Official card: 「每晚可以查验一名玩家的具体身份。」 The 2023 announcement counts her
among the gods (女巫、猎人、守卫和魔镜少女), so ``camp=good`` / ``tags={"god"}``.

Mechanically she is the Gargoyle's check on the good side: the answer is a
``role_checks`` private fact that the projector reads back into her own prompt.
What makes the card more than a copy is the *selected target's* ``role_label``:
it comes from the projector's selected-target row, which honours a ``disguise`` —
so a seat that imitated someone shows the imitated identity here, exactly as the
rules say ("立刻获得目标的身份").
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

#: Same namespace the Gargoyle writes, read back by the same projector branch.
ROLE_CHECK_NAMESPACE = "role_checks"
_EVENT = "MIRROR_GIRL_REASONING"


def mirror_girl_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_mirror_girl_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "inspect" and command.target_seat == context.actor_seat:
        return (RuleViolation("self_inspect_forbidden", "the mirror cannot inspect its owner"),)
    return ()


def _mirror_girl_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int,
    role_label: str | None, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "inspect" and command.target_seat is not None:
        answer = f"，其身份是「{role_label}」" if role_label else ""
        thought = f"决定查验 {command.target_seat} 号玩家{answer}：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不查验：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _EVENT,
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


def resolve_mirror_girl_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "inspect" or command.target_seat is None:
        return (_mirror_girl_reasoning_effect(context, command, 1, None, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    role_label = selected.get("role_label")
    if type(role_label) is not str:
        raise TypeError("selected target role label must be a string")
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.RECORD_PRIVATE_FACT,
            context.action_key,
            payload={
                "target": context.actor_seat, "namespace": ROLE_CHECK_NAMESPACE,
                "fact": {"target": command.target_seat, "role": role_label},
            },
            visibility=("ACTOR",), target_seat=context.actor_seat,
            sort_key=(1,), **common,
        ),
        _mirror_girl_reasoning_effect(context, command, 2, role_label, **common),
    )


MIRROR_INSPECT_CONTRACT = ActionContract(
    contract_id="mirror_inspect",
    schedule_point=SchedulePoint.NIGHT_SEER_ACTION,
    # Just after the Gargoyle (35): the shared identity-check point, stable order.
    order=37,
    action_types=("inspect", "pass"),
    actions_requiring_target=frozenset({"inspect"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"role_label"}),
    per_window_limit=1, per_round_limit=1,
    is_applicable=mirror_girl_applicable,
    validate=validate_mirror_girl_action,
    resolve=resolve_mirror_girl_action,
)

MIRROR_GIRL_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-mirror-girl", display_name="Mirror Girl", camp_id="good",
    opening_narration=("魔镜少女请睁眼", "请查验一名存活玩家的具体身份。"),
    contracts=(MIRROR_INSPECT_CONTRACT,),
    initial_private_data={ROLE_CHECK_NAMESPACE: ()},
    allowed_effects=MIRROR_INSPECT_CONTRACT.allowed_effects,
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "Mirror Girl is a good-camp god. Each night you may inspect one other living "
        "player and learn their exact role (not just their camp); the answer is private to "
        "you. A player who has imitated someone else shows the imitated role to your "
        "mirror, so an answer that contradicts what you already know is itself a clue. "
        "You cannot prove an inspection to the table, so choose when to reveal what you "
        "know — a claimed result is only as convincing as the story around it."
    ),
)


class MirrorGirl(BaseRole):
    """Daytime instance: the nightly inspection is a pipeline contract."""

    pass
