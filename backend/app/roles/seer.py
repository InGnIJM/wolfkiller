from __future__ import annotations
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole


class Seer(BaseRole):
    """Seer role: checks one player's alignment each night."""

    def get_skills(self) -> list[str]:
        return ["check"]

    async def check(
        self, state: GameState, conversation_log: ConversationLog
    ) -> NightAction:
        """Decide which player to check tonight."""
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "night_check"
        )
        raw = await self._invoke_llm(prompt)
        return self.output_parser.parse_night_action(raw, self.seat)
