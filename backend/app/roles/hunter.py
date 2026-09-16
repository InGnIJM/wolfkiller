from __future__ import annotations
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole
from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)


_SHOOT_REASONS = frozenset({"wolf_kill", "exile", "hunter_shot", "self_explode"})


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
    if command.action_type == "shoot" and command.target_seat is not None:
        thought = f"决定开枪带走 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定不开枪：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "HUNTER_REASONING",
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
            payload={"event_type": "HUNTER_SHOT", "payload": {"target_seat": command.target_seat}},
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _hunter_reasoning_effect(context, command, 4, **common),
    )


HUNTER_SPEC = RoleSpec(
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
        "你出局（被狼刀、被放逐、被猎人或被白狼王自爆带走）时触发开枪权；被女巫毒死不能开枪。开枪是可选的，"
        "不是强制的：你可以 shoot 一名存活玩家将其带走，也可以 pass 不开枪。"
        "是否开枪完全由你根据当前局势自行判断——只有当你对某名存活玩家的"
        "狼人身份有较高把握时才开枪；没有把握或担心误伤好人时，应选择 pass。"
    ),
)


class Hunter(BaseRole):
    """Hunter role: can shoot one player on death (not if poisoned)."""

    def get_skills(self) -> list[str]:
        return ["shoot"]

    async def shoot(
        self, state: GameState, conversation_log: ConversationLog
    ) -> NightAction:
        """Decide who to shoot when dying. Cannot shoot if killed by poison."""
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "hunter_shoot"
        )
        raw = await self._invoke_llm(prompt)
        action = self.output_parser.parse_night_action(raw, self.seat)
        return action
