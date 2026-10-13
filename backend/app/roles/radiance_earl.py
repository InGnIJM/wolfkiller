"""Radiance Earl: shelters one other player from the night's damage.

Official card: 「从第二晚开始，每晚可以选择一名其他玩家进行流光庇护，被流光庇护的玩家可以
免疫夜间伤害（包括女巫的毒药以及摄梦人的梦游伤害）。不能连续两晚对同一个人使用技能。」

The shelter is the Dream Weaver's: ``SUBMIT_PROTECTION(source="radiance")`` lands in
``night_settlement``'s any-damage tier, so it stops the knife *and* the vial (a plain
guard shield would not). Two differences from the Dream Weaver follow from the card:
it may be declined, and a repeat on consecutive nights is *forbidden* (rejected by the
validator) rather than lethal.

The Earl also leaves a one-night ``radiance_shielded`` mark on whoever it sheltered.
The target never sees it — the mark is only readable by a hook through
``selected_target.status_labels`` — but it is how the Eclipse Maid learns, when she
devours someone, that the seat was sheltered (and that she must pay for it).

The Earl acts at ``order=2``, ahead of the Maid (3): the mark has to exist before the
Maid looks. The price of that ordering is a documented simplification — devouring the
Earl cannot take back a shelter it has already cast this night.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import RADIANCE_SHIELDED_STATUS
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.target_memory import is_consecutive_repeat, target_memory

#: Private slot: who the Earl sheltered last, and on which night.
LAST_SHIELDED_KEY = "last_shielded"
_EVENT = "RADIANCE_EARL_REASONING"


def radiance_applicable(context: ActionContext) -> bool:
    """From the second night on, while alive."""
    return context.actor_alive and context.round_number >= 2


def validate_radiance_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "shield" or command.target_seat is None:
        return ()
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_shield_forbidden", "the Earl shelters another player"),)
    if is_consecutive_repeat(context, LAST_SHIELDED_KEY, command.target_seat):
        return (RuleViolation(
            "consecutive_shield_forbidden",
            "the same seat cannot be sheltered on two consecutive nights",
        ),)
    return ()


def _radiance_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "shield" and command.target_seat is not None:
        thought = f"决定庇护 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不庇护任何人：{command.reasoning or '无理由'}"
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


def resolve_radiance_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "shield" or command.target_seat is None:
        return (_radiance_reasoning_effect(context, command, 1, **common),)
    target = command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_PROTECTION,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "source": "radiance"},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.ADD_STATUS,
            context.action_key, target_seat=target,
            payload={"target": target, "status": RADIANCE_SHIELDED_STATUS},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload=target_memory(
                context.actor_seat, LAST_SHIELDED_KEY, target, context.round_number,
            ),
            sort_key=(3,), **common,
        ),
        _radiance_reasoning_effect(context, command, 4, **common),
    )


RADIANCE_SHIELD_CONTRACT = ActionContract(
    contract_id="radiance_shield",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # Ahead of the Eclipse Maid (3): the shelter mark must exist before she looks.
    order=2,
    action_types=("shield", "pass"),
    actions_requiring_target=frozenset({"shield"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.SUBMIT_PROTECTION, EffectKind.ADD_STATUS,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    per_window_limit=1, per_round_limit=1,
    is_applicable=radiance_applicable,
    validate=validate_radiance_action,
    resolve=resolve_radiance_action,
)

RADIANCE_EARL_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-radiance-earl", display_name="Radiance Earl", camp_id="good",
    opening_narration=("流光伯爵请睁眼", "请选择今晚要庇护的玩家。"),
    contracts=(RADIANCE_SHIELD_CONTRACT,),
    initial_private_data={LAST_SHIELDED_KEY: None},
    allowed_effects=RADIANCE_SHIELD_CONTRACT.allowed_effects,
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "你是流光伯爵（好人神职）：从第二晚开始，每晚可以选择一名**其他**存活玩家进行流光庇护，"
        "被庇护的玩家当夜免疫一切夜间伤害——狼刀、女巫的毒药和摄梦人的梦游伤害都伤不到他。"
        "你可以选择不使用；不能连续两晚庇护同一个人（中间隔一晚则可以）。"
        "被庇护的玩家本人不会知道自己被庇护。"
        "如果有人吞噬你的技能，或吞噬了你当晚庇护的玩家，吞噬者会付出代价（流光反噬）。"
        "战术提示：庇护是强力的保护，优先庇护你判断的真预言家或其他关键神职；"
        "你自己无法被庇护，所以注意隐藏身份。"
    ),
)


class RadianceEarl(BaseRole):
    """Daytime instance: the nightly shelter is a pipeline contract."""

    pass
