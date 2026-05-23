from __future__ import annotations
import logging
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole

logger = logging.getLogger(__name__)


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
        if action.thinking:
            self._record_thought(action.thinking, state, conversation_log, "night_check")
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
            if action.thinking:
                self._record_thought(action.thinking, state, conversation_log, "night_check")
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
