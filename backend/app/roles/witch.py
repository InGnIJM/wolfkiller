from __future__ import annotations
import logging
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole

logger = logging.getLogger(__name__)


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
