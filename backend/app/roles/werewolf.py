from __future__ import annotations
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole


class Werewolf(BaseRole):
    """Werewolf role: can kill at night. Wolves coordinate through their kill votes."""

    def get_skills(self) -> list[str]:
        return ["kill"]

    async def kill(
        self, state: GameState, conversation_log: ConversationLog
    ) -> NightAction:
        """Decide kill target. Can see previous wolves' votes via conversation log."""
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "night_kill"
        )
        raw = await self._invoke_llm(prompt)
        action = self.output_parser.parse_night_action(raw, self.seat)
        return action
