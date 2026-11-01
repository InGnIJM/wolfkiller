from __future__ import annotations
from app.roles.base import BaseRole
from app.core.effect_applier import derive_effect_id
from app.roles.reasoning import reasoning_effect
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)


# Ways out that still let the hunter fire — the official card is explicit and
# exclusive:
#
#   「**当且仅当**猎人被狼人出局**或**被投票放逐时，猎人可以亮出自己的身份牌并
#     指定猎捕一名玩家。」
#
# "被狼人出局" is read strictly as the pack's own kill, so the set is exactly
# these two. Everything else stays out, including causes this set used to carry
# with no authority behind them: ``self_explode`` (dragged along by the White
# Wolf King), ``wolf_king_shot`` (the Wolf King's claw), and ``hunter_shot`` (a
# second hunter's gun) — plus ``poison`` and ``charm``, which the rules never
# allowed anyway.
_SHOOT_REASONS = frozenset({"wolf_kill", "exile"})


def hunter_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason in _SHOOT_REASONS
        and context.resources.get("gun", 0) > 0
    )


def validate_hunter_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "shoot" and context.resources.get("gun", 0) <= 0:
        return (RuleViolation("gun_unavailable", "hunter gun is unavailable"),)
    return ()


def _hunter_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "shoot" and command.target_seat is not None:
        thought = f"决定开枪带走 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定不开枪：{command.reasoning or '无理由'}"
    return reasoning_effect(
        context, command, ordinal, event_type="HUNTER_REASONING",
        thought=thought, **common,
    )


def resolve_hunter_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    if command.action_type == "pass":
        return (_hunter_reasoning_effect(
            context, command, 1,
            expected_revision=context.revision,
            source_event_id=context.source_event_id,
        ),)
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "resource": "gun", "amount": 1},
            preconditions={"resource_equals": {"resource": "gun", "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=command.target_seat,
            payload={"target": command.target_seat, "amount": 1, "cause": "hunter_shot"},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": "HUNTER_SHOT", "payload": {
                "target_seat": command.target_seat, "seat": context.actor_seat,
            }},
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _hunter_reasoning_effect(context, command, 4, **common),
    )


HUNTER_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-hunter", display_name="Hunter", camp_id="good",
    contracts=(ActionContract(
        contract_id="hunter_shoot", schedule_point=SchedulePoint.DAWN_REACTION,
        order=40, action_types=("shoot", "pass"),
        actions_requiring_target=frozenset({"shoot"}), fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=_SHOOT_REASONS, per_window_limit=1, per_game_limit=1,
        is_applicable=hunter_applicable, validate=validate_hunter_action,
        resolve=resolve_hunter_action,
    ),), initial_resources={"gun": 1},
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions=(
        "只有两种出局方式触发开枪权：夜间被狼刀、白天被放逐。被女巫毒死、被狼王爪带走、"
        "被白狼王自爆带走、被骑士决斗出局、被狼美人殉情带走，都不能开枪。开枪是可选的，"
        "不是强制的：你可以 shoot 一名存活玩家将其带走，也可以 pass 不开枪。"
        "是否开枪完全由你根据当前局势自行判断——只有当你对某名存活玩家的"
        "狼人身份有较高把握时才开枪；没有把握或担心误伤好人时，应选择 pass。"
        "战术提示：猎人拥有强力的反击威慑，但这份威慑只在被狼刀或被放逐时才兑现。"
        "在适当时机展现强硬姿态或亮出枪口威慑，有助于压制悍跳与冲锋狼人、稳固好人阵营防线。"
    ),
)


class Hunter(BaseRole):
    """Hunter role: can shoot one player on death (not if poisoned)."""
