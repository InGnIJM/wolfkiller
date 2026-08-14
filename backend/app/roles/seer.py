from __future__ import annotations
import logging
from collections.abc import Mapping
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


def seer_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_seer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "check" and command.target_seat == context.actor_seat:
        return (RuleViolation("self_check_forbidden", "seer cannot check self"),)
    return ()


def resolve_seer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    if command.action_type == "pass":
        return ()
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    camp = selected.get("camp_label")
    if type(camp) is not str:
        raise TypeError("selected target camp label must be a string")
    return (GameEffect(
        derive_effect_id(context.action_key, 1),
        EffectKind.RECORD_PRIVATE_FACT,
        context.action_key,
        payload={
            "target": context.actor_seat,
            "namespace": "private_checks",
            "fact": {"target": command.target_seat, "camp": camp},
        },
        visibility=("ACTOR",),
        expected_revision=context.revision,
        target_seat=context.actor_seat,
        source_event_id=context.source_event_id,
        sort_key=(1,),
    ), GameEffect(
        derive_effect_id(context.action_key, 2),
        EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "SEER_CHECK",
            "payload": {"target_seat": command.target_seat, "result": camp},
        },
        visibility=("PUBLIC",),
        expected_revision=context.revision,
        source_event_id=context.source_event_id,
        sort_key=(2,),
    ),)


SEER_SPEC = RoleSpec(
    role_id="wolf-killer-seer",
    display_name="Seer",
    camp_id="good",
    schema_version=2,
    contracts=(ActionContract(
        contract_id="seer_check",
        schedule_point=SchedulePoint.NIGHT_SEER_ACTION,
        order=30,
        action_types=("check", "pass"),
        actions_requiring_target=frozenset({"check"}),
        fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        selected_target_fact_namespaces=frozenset({"camp_label"}),
        per_window_limit=1,
        per_round_limit=1,
        is_applicable=seer_applicable,
        validate=validate_seer_action,
        resolve=resolve_seer_action,
    ),),
    initial_private_data={"private_checks": ()},
    allowed_effects=frozenset({EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions="Check one other living player's camp during the night action window.",
)


class Seer(BaseRole):
    """Seer role: checks one player's alignment each night."""

    def get_skills(self) -> list[str]:
        return ["check"]

    async def check(
        self, state: GameState, conversation_log: ConversationLog
    ) -> NightAction:
        """Decide which player to check tonight.
        Validates that the target is alive and not self. Retries once on
        invalid target, then falls back to a random alive player.
        """
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "night_check"
        )

        # ── Attempt 1 ──
        raw = await self._invoke_llm(prompt)
        action = self.output_parser.parse_night_action(raw, self.seat)
        action = self._validate_check_target(state, action)

        # ── Attempt 2 (retry) if first attempt was invalid ──
        if self._is_invalid_check(action):
            logger.warning(
                f"Seat {self.seat} (Seer): First check attempt invalid "
                f"(target={action.target_seat}). Retrying with stern warning."
            )
            retry_prompt = (
                prompt
                + f"\n\n【系统紧急提示】你刚才的查验目标无效！"
                + f"你必须从存活玩家中选择一个具体的座位号。"
                + f"不能查验自己（{self.seat}号），不能查验已出局玩家。"
                + f"请立即重新输出正确的JSON！"
            )
            raw = await self._invoke_llm(retry_prompt)
            action = self.output_parser.parse_night_action(raw, self.seat)
            action = self._validate_check_target(state, action)

        # ── Fallback: pick a random alive player ──
        if self._is_invalid_check(action):
            alive = [s for s in state.alive_players() if s != self.seat]
            if alive:
                import random
                fallback = random.choice(alive)
                logger.warning(
                    f"Seat {self.seat} (Seer): Both check attempts failed. "
                    f"Falling back to random alive player {fallback}."
                )
                return NightAction(
                    player_seat=self.seat,
                    action_type="check",
                    target_seat=fallback,
                    reasoning="系统兜底选择——两次查验尝试均返回无效目标，随机选择一名存活玩家查验",
                )
            # No one to check — should not happen in normal game
            return NightAction(player_seat=self.seat, action_type="pass",
                               reasoning="无存活玩家可查验")

        return action

    def _validate_check_target(
        self, state: GameState, action: NightAction,
    ) -> NightAction:
        """Ensure the check target is alive, not self, and exists."""
        target = action.target_seat
        if target is None or target == 0:
            return action  # already invalid, will be caught by _is_invalid_check

        if target == self.seat:
            logger.warning(f"Seat {self.seat} (Seer): Tried to check self")
            action.target_seat = None
            return action

        target_player = state.players.get(target)
        if target_player is None:
            logger.warning(f"Seat {self.seat} (Seer): Target {target} doesn't exist")
            action.target_seat = None
            return action

        if not target_player.is_alive:
            logger.warning(f"Seat {self.seat} (Seer): Target {target} is dead")
            action.target_seat = None
            return action

        return action

    def _is_invalid_check(self, action: NightAction) -> bool:
        """Returns True if the check action has no valid target."""
        return (
            action.action_type != "check"
            or action.target_seat is None
            or action.target_seat == 0
        )
