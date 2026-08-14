from __future__ import annotations
import logging
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole
from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)

logger = logging.getLogger(__name__)


def witch_applicable(context: ActionContext) -> bool:
    return context.actor_alive and any(context.resources.get(name, 0) for name in ("antidote", "poison"))


def validate_witch_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "save" and context.resources.get("antidote", 0) <= 0:
        return (RuleViolation("antidote_unavailable", "antidote is unavailable"),)
    if command.action_type == "save" and command.target_seat != context.facts.get("wolf_kill_target"):
        return (RuleViolation("invalid_save_target", "save target must be the wolf kill target"),)
    if command.action_type == "poison" and context.resources.get("poison", 0) <= 0:
        return (RuleViolation("poison_unavailable", "poison is unavailable"),)
    return ()


def _witch_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.action_type == "save" and command.target_seat is not None:
        thought = f"决定使用解药救 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    elif command.action_type == "poison" and command.target_seat is not None:
        thought = f"决定使用毒药毒杀 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不使用药水：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "WITCH_REASONING",
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
    outcome_payload = {"target": target, "amount": 1}
    if command.action_type == "poison":
        outcome_payload["cause"] = "poison"
    common = {"expected_revision": context.revision, "source_event_id": context.source_event_id}
    emit_type = "WITCH_SAVE" if command.action_type == "save" else "WITCH_POISON"
    return (
        GameEffect(derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "resource": resource, "amount": 1},
            preconditions={"resource_equals": {"resource": resource, "value": 1}},
            sort_key=(1,), **common),
        GameEffect(derive_effect_id(context.action_key, 2), outcome, context.action_key,
            target_seat=target, payload=outcome_payload, sort_key=(2,), **common),
        GameEffect(derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": emit_type, "payload": {"target_seat": target}},
            visibility=("PUBLIC",), sort_key=(3,), **common),
        _witch_reasoning_effect(context, command, 4, **common),
    )


WITCH_SPEC = RoleSpec(
    role_id="wolf-killer-witch", display_name="Witch", camp_id="good",
    schema_version=2,
    contracts=(ActionContract(
        contract_id="witch_action", schedule_point=SchedulePoint.NIGHT_WITCH_ACTION, order=20,
        action_types=("save", "poison", "pass"),
        actions_requiring_target=frozenset({"save", "poison"}), fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_PROTECTION, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}), per_window_limit=1, per_round_limit=1,
        is_applicable=witch_applicable, validate=validate_witch_action, resolve=resolve_witch_action,
    ),),
    initial_resources={"antidote": 1, "poison": 1},
    initial_private_data={"wolf_kill_target": None},
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_PROTECTION, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions="Use at most one available potion during the night action window.",
)


class Witch(BaseRole):
    """Witch role: one antidote, one poison. Only one potion per night."""

    def get_skills(self) -> list[str]:
        return ["save", "poison"]

    async def save(
        self, state: GameState, conversation_log: ConversationLog, wolf_target: int
    ) -> bool:
        """Decide whether to use antidote to save the wolf kill target."""
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "witch_save",
            wolf_target=wolf_target,
        )
        raw = await self._invoke_llm(prompt)
        action = self.output_parser.parse_night_action(raw, self.seat)
        return action.action_type == "save"

    async def poison(
        self, state: GameState, conversation_log: ConversationLog, wolf_target: int
    ) -> NightAction:
        """Decide whether to use poison, and on whom.
        Validates that the target is alive. If the LLM chooses a dead player,
        retries with a stern warning. If it still fails, returns pass to avoid
        wasting the poison on an invalid target.
        """
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "witch_poison",
            wolf_target=wolf_target,
        )

        # ── Attempt 1 ──
        raw = await self._invoke_llm(prompt)
        action = self.output_parser.parse_night_action(raw, self.seat)
        # ── Validate target is alive ──
        action = self._validate_poison_target(state, action)

        # ── Attempt 2 (retry) if first attempt targeted a dead player ──
        if action.action_type == "pass" and self._was_rejected(action):
            logger.warning(
                f"Seat {self.seat} (Witch): First poison attempt targeted dead player. "
                f"Retrying with stern warning."
            )
            retry_prompt = (
                prompt
                + "\n\n【🚨 系统拒绝 — 你刚才选择的目标已经死亡！】\n"
                + "你刚才试图毒杀的玩家已经出局了！毒死人是无效操作！\n"
                + f"存活玩家是：{'、'.join(f'{s}号' for s in state.alive_players())}\n"
                + "请重新选择：从以上存活玩家中选择一个目标，或者选择弃权（target_seat=0）。\n"
                + "再次毒死人将强制放弃毒药使用权！"
            )
            raw = await self._invoke_llm(retry_prompt)
            action = self.output_parser.parse_night_action(raw, self.seat)
            action = self._validate_poison_target(state, action)

        return action

    def _validate_poison_target(
        self, state: GameState, action: NightAction,
    ) -> NightAction:
        """Check that the poison target (if any) is alive.
        Returns a pass action if the target is dead, so poison is not wasted.
        """
        if action.action_type != "poison":
            return action

        target_seat = action.target_seat
        if target_seat is None or target_seat == 0:
            return action

        target = state.players.get(target_seat)
        if target is None:
            logger.warning(
                f"Seat {self.seat} (Witch): Poison target {target_seat} does not exist. "
                f"Forcing pass to avoid wasting poison."
            )
            return NightAction(
                player_seat=self.seat, action_type="pass",
                reasoning=f"目标{target_seat}号不存在，放弃毒药",
            )

        if not target.is_alive:
            logger.warning(
                f"Seat {self.seat} (Witch): Poison target {target_seat} is already dead. "
                f"Forcing pass to avoid wasting poison."
            )
            return NightAction(
                player_seat=self.seat, action_type="pass",
                reasoning=f"目标{target_seat}号已出局，放弃毒药",
            )

        return action

    def _was_rejected(self, action: NightAction) -> bool:
        """Check if the action was rejected due to dead/nonexistent target."""
        reasoning = action.reasoning or ""
        return "已出局" in reasoning or "不存在" in reasoning
