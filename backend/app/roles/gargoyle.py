"""Gargoyle: a werewolf outside the pack that learns one identity a night.

It is the hidden-wolf archetype. Three of its four properties fall out of the
markers it declares rather than out of engine code:

* ``no_wolf_pack`` keeps it out of the discussion, the kill vote, the wolf
  channel, the teammate lists and the ``CAMP`` roster — in both directions, so
  it may shoot a teammate it cannot identify.
* Not declaring ``werewolf_kill`` means it never joins the nightly attack; it
  declares the shared ``hidden_wolf_kill`` instead, which only opens once the
  pack it cannot see is gone.
* Declaring no explode contract, and sitting outside the pack, means it can
  neither self-destruct during the day nor swallow the badge at the election.

The fourth is its own: a nightly identity check, recorded as a private fact so
the answer shows up in its own prompt and nowhere else. The Seer still reads it
as a werewolf because the camp is ``werewolf`` — no special case anywhere.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.hidden_wolf import hidden_wolf_role_spec

#: Private-fact namespace holding every identity it has learned, in order.
ROLE_CHECK_NAMESPACE = "role_checks"


def gargoyle_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_gargoyle_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "check" or command.target_seat is None:
        return ()
    if command.target_seat == context.actor_seat:
        return (RuleViolation(
            "gargoyle_self_check", "the Gargoyle cannot check itself",
        ),)
    checked = context.facts.get("checked_seats") or ()
    if isinstance(checked, (list, tuple)) and command.target_seat in checked:
        return (RuleViolation(
            "gargoyle_repeat_check", "the same seat cannot be checked twice",
        ),)
    return ()


def _gargoyle_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int,
    role_label: str | None, **common: object,
) -> GameEffect:
    if command.action_type == "check" and command.target_seat is not None:
        answer = f"，其身份是「{role_label}」" if role_label else ""
        thought = f"决定查验 {command.target_seat} 号玩家{answer}：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不查验：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "GARGOYLE_REASONING",
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


def resolve_gargoyle_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "check" or command.target_seat is None:
        return (_gargoyle_reasoning_effect(context, command, 1, None, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    role_label = selected.get("role_label")
    if type(role_label) is not str:
        raise TypeError("selected target role label must be a string")
    checked = tuple(context.facts.get("checked_seats") or ())
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.RECORD_PRIVATE_FACT,
            context.action_key,
            payload={
                "target": context.actor_seat,
                "namespace": ROLE_CHECK_NAMESPACE,
                "fact": {"target": command.target_seat, "role": role_label},
            },
            visibility=("ACTOR",), target_seat=context.actor_seat,
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload={
                "target": context.actor_seat, "key": "checked_seats",
                "value": [*checked, command.target_seat],
            },
            sort_key=(2,), **common,
        ),
        _gargoyle_reasoning_effect(
            context, command, 3, role_label, **common,
        ),
    )


GARGOYLE_CHECK_CONTRACT = ActionContract(
    contract_id="gargoyle_check",
    schedule_point=SchedulePoint.NIGHT_SEER_ACTION,
    # After the Seer's own check (order 30): the two never conflict, but a
    # stable order keeps the journal and the audience feed readable.
    order=35,
    action_types=("check", "pass"),
    actions_requiring_target=frozenset({"check"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.SET_PRIVATE_DATA,
        EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"role_label"}),
    is_applicable=gargoyle_applicable,
    validate=validate_gargoyle_action,
    resolve=resolve_gargoyle_action,
)

GARGOYLE_SPEC = hidden_wolf_role_spec(
    "wolf-killer-gargoyle", "Gargoyle",
    extra_contracts=(GARGOYLE_CHECK_CONTRACT,),
    extra_effects=GARGOYLE_CHECK_CONTRACT.allowed_effects,
    initial_private_data={"checked_seats": (), ROLE_CHECK_NAMESPACE: ()},
    instructions=(
        "你是石像鬼（狼人阵营，但**不属于狼窝**）：你不参与狼队的夜间讨论与狼刀投票，"
        "其他狼也不知道你是谁，你同样看不到他们是谁——你只知道场上还剩几只狼。"
        "因此你有可能亲手刀死自己的狼队友，这是这个板子的核心风险。"
        "你不能自爆，也不能吞警徽。"
        "当其他狼全部出局后，你会获得带刀权，每晚可以独自刀人。"
        "每晚你可以查验一名存活玩家的**具体身份**（不能查自己，也不能重复查同一人），"
        "结果只有你自己知道。"
        "被预言家查验时你会显示为狼人。"
        "战术提示：前期利用身份查验锁定神职，但不要过早暴露；"
        "你的查验结果无法自证，起跳预言家时要注意编造可信的验人逻辑；"
        "带刀阶段要避开你已知的狼队友。"
    ),
)
class Gargoyle(BaseRole):
    """Gargoyle: its check and its lone kill are both pipeline contracts."""

    pass
