from __future__ import annotations
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole


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
