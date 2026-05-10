from __future__ import annotations
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole


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
        """Decide whether to use poison, and on whom."""
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "witch_poison",
            wolf_target=wolf_target,
        )
        raw = await self._invoke_llm(prompt)
        return self.output_parser.parse_night_action(raw, self.seat)
