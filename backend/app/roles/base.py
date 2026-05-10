from __future__ import annotations
import logging
from app.models.game import GameState
from app.models.actions import VoteAction
from app.core.conversation_log import ConversationLog
from app.agents.llm_client import LLMClient
from app.agents.prompt_builder import PromptBuilder
from app.agents.output_parser import OutputParser, ToolCallError
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)


class BaseRole:
    """Base class for all Werewolf roles. Handles LLM interaction for speech and voting."""

    def __init__(
        self,
        seat: int,
        role_name: str,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
    ):
        self.seat = seat
        self.role_name = role_name
        self.prompt_builder = prompt_builder
        self.llm_client = llm_client
        self.output_parser = OutputParser()
        self._last_words_used = False

    @property
    def is_good(self) -> bool:
        return "werewolf" not in self.role_name

    def get_skills(self) -> list[str]:
        """Return list of skill names this role has. Override in subclasses."""
        return []

    # ── Speech (with tool calling + validation) ─────────────────

    async def speak(
        self, state: GameState, conversation_log: ConversationLog, context: str
    ) -> str | None:
        """Generate speech. Uses tool calling with validation for day_speech and last_words.
        Returns the speech text, or None if validation failed.
        """
        prompt = self.prompt_builder.build_speech_prompt(
            state, self.seat, self.role_name, conversation_log, context
        )

        # Use tool calling for speech/last_words contexts
        if context in ("day_speech", "last_words"):
            return await self._speak_with_tools(state, prompt, context)

        # Other contexts (e.g. werewolf chat handled elsewhere) use plain LLM
        raw = await self._invoke_llm(prompt)
        return self.output_parser.parse_speech(raw)

    async def _speak_with_tools(
        self, state: GameState, prompt: str, context: str,
    ) -> str | None:
        """Invoke LLM with speech tools, validate the tool call, return the speech text.
        For day_speech, returns "过" on failure instead of None to avoid silent skip.
        For last_words, returns None on failure (last words are optional).
        """
        tools = self.prompt_builder.get_speech_tools()
        tool_result = await self._invoke_llm_with_tools(prompt, tools)

        if tool_result is None:
            logger.warning(
                f"Seat {self.seat}: LLM did not call any tool for context={context}. "
                f"Model may have output text directly instead of using the function."
            )
            return "过" if context == "day_speech" else None

        fn_name = tool_result.function_name
        args = tool_result.arguments
        text = args.get("text", "").strip()

        # Validate: context must match function name
        if context == "day_speech":
            if fn_name == "last_words":
                logger.warning(
                    f"Seat {self.seat}: 校验失败——发言阶段调用了 last_words 而非 speak，回退为「过」"
                )
                return "过"
            valid, reason = self._validate_speak(state)
            if not valid:
                logger.warning(f"Seat {self.seat} speak validation failed: {reason}")
                return "过"

        elif context == "last_words":
            if fn_name == "speak":
                logger.warning(
                    f"Seat {self.seat}: 校验失败——遗言阶段调用了 speak 而非 last_words"
                )
                return None
            valid, reason = self._validate_last_words(state)
            if not valid:
                logger.warning(f"Seat {self.seat} last_words validation failed: {reason}")
                return None
            self._last_words_used = True

        else:
            logger.warning(f"Seat {self.seat}: unknown tool function called: {fn_name}")
            return None

        if not text:
            logger.warning(f"Seat {self.seat}: {fn_name} called with empty text")
            return None

        logger.info(f"Seat {self.seat}: {fn_name} validated OK, text length={len(text)}")
        return text

    # ── Validation ──────────────────────────────────────────────

    def _validate_speak(self, state: GameState) -> tuple[bool, str]:
        """Validate that this player is allowed to speak in day speech phase.
        Returns (is_valid, failure_reason).
        """
        player = state.players.get(self.seat)
        if player is None:
            return False, f"校验失败：{self.seat}号玩家不存在于游戏中"
        if not player.is_alive:
            return False, (
                f"校验失败：{self.seat}号玩家已出局，无法发言。"
                f"只有存活玩家才能进行白天发言。"
            )
        if state.phase.value != "speech":
            return False, (
                f"校验失败：当前游戏阶段为「{state.phase.value}」，不是发言阶段。"
                f"发言只能在发言阶段（speech）进行。"
            )
        return True, ""

    def _validate_last_words(self, state: GameState) -> tuple[bool, str]:
        """Validate that this player is allowed to give last words.
        Returns (is_valid, failure_reason).

        Eligibility:
        - First-night deaths (wolf_kill, poison) in round 1
        - Exiled players (any round)
        - Must not have already given last words
        """
        if self._last_words_used:
            return False, (
                f"校验失败：{self.seat}号玩家已发表过遗言，不能再次发表。"
                f"每位玩家只能发表一次遗言。"
            )

        player = state.players.get(self.seat)
        if player is None:
            return False, f"校验失败：{self.seat}号玩家不存在于游戏中"

        # Find this player's death record
        death = None
        for d in state.death_history:
            if d.player_seat == self.seat:
                death = d
                break

        if death is None:
            return False, (
                f"校验失败：{self.seat}号玩家尚未出局，不能发表遗言。"
                f"遗言只有出局玩家才能发表。"
            )

        cause = death.cause
        round_num = death.round_number
        is_first_night_death = cause in ("wolf_kill", "poison") and round_num == 1
        is_exile = cause == "exile"

        if not (is_first_night_death or is_exile):
            cause_cn_map = {
                "wolf_kill": "被狼杀", "poison": "被毒",
                "exile": "被放逐", "hunter_shot": "被猎人带走",
            }
            cause_cn = cause_cn_map.get(cause, cause)
            return False, (
                f"校验失败：{self.seat}号玩家因「{cause_cn}」出局，不符合遗言条件。"
                f"只有第一夜死亡（被狼刀或被毒）和被放逐的玩家可以发表遗言。"
            )

        return True, ""

    # ── Voting ──────────────────────────────────────────────────

    async def vote(
        self, state: GameState, conversation_log: ConversationLog, context: str
    ) -> VoteAction:
        prompt = self.prompt_builder.build_vote_prompt(
            state, self.seat, self.role_name, conversation_log, context
        )
        raw = await self._invoke_llm(prompt)
        vote = self.output_parser.parse_vote_action(raw, self.seat)
        return vote

    # ── LLM invocation ──────────────────────────────────────────

    async def _invoke_llm(self, prompt: str) -> str:
        model = self.llm_client.get_model()
        messages = [
            SystemMessage(content=self.prompt_builder.get_system_prompt()),
            HumanMessage(content=prompt),
        ]
        response = await model.ainvoke(messages)
        return response.content if hasattr(response, "content") else str(response)

    async def _invoke_llm_with_tools(self, prompt: str, tools: list[dict]):
        """Invoke LLM with tool definitions. Returns ToolCallResult or None."""
        model = self.llm_client.get_model_with_tools(tools)
        messages = [
            SystemMessage(content=self.prompt_builder.get_system_prompt()),
            HumanMessage(content=prompt),
        ]
        response = await model.ainvoke(messages)

        # Log full response for debugging
        content_preview = (
            response.content[:200] if hasattr(response, "content") and response.content
            else "(empty)"
        )
        has_tool_calls = (
            hasattr(response, "tool_calls") and response.tool_calls
        )
        logger.info(
            f"Seat {self.seat} LLM response: "
            f"has_tool_calls={has_tool_calls}, "
            f"content_preview={content_preview}"
        )

        return self.output_parser.parse_tool_call(response)
