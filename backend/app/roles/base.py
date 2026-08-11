from __future__ import annotations
import logging
from app.models.game import GameState
from app.models.actions import VoteAction
from app.core.conversation_log import ConversationLog
from app.agents.llm_client import LLMClient
from app.agents.prompt_builder import PromptBuilder
from app.agents.output_parser import OutputParser, StrictCapabilityError, ToolCallError
from app.core.action_validator import ActionValidationError, ActionValidator
from app.models.contracts import AcceptedAction, ActionRequest
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
        self.action_validator = ActionValidator()
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
            return await self._speak_with_tools(state, prompt, context, conversation_log)

        # Other contexts (e.g. werewolf chat handled elsewhere) use plain LLM
        raw = await self._invoke_llm(prompt)
        return self.output_parser.parse_speech(raw)

    async def _speak_with_tools(
        self, state: GameState, prompt: str, context: str,
        conversation_log: ConversationLog,
    ) -> str | None:
        """Invoke LLM with speech tools, validate the tool call, return the speech text.
        Retries once on tool-calling failure, then generates a fallback speech so the
        player is never silently skipped. Only returns None for legitimate eligibility
        failures (dead player, wrong phase, already gave last words).
        """
        tools = self.prompt_builder.get_speech_tools()

        # ── Pre-validate: only reject when player legitimately cannot speak ──
        if context == "day_speech":
            valid, reason = self._validate_speak(state)
            if not valid:
                logger.warning(f"Seat {self.seat} speak validation failed: {reason}")
                return None

        elif context == "last_words":
            valid, reason = self._validate_last_words(state)
            if not valid:
                logger.warning(f"Seat {self.seat} last_words validation failed: {reason}")
                return None
            self._last_words_used = True

        # ── Attempt 1: normal tool-calling prompt ──
        try:
            tool_result = await self._invoke_llm_with_tools(prompt, tools)
        except Exception as e:
            logger.error(
                f"Seat {self.seat}: LLM invocation failed with exception for "
                f"context={context} (attempt 1): {e}"
            )
            tool_result = None

        # ── Attempt 2 (retry): stronger prompt emphasising tool call ──
        if tool_result is None:
            logger.warning(
                f"Seat {self.seat}: LLM did not call any tool for context={context} "
                f"(attempt 1). Retrying with stronger instructions."
            )
            retry_prompt = (
                prompt
                + "\n\n【系统紧急提示】你刚才没有调用发言函数！这是严重的违规。"
                + "请立即在内心思考后调用 speak 函数（遗言用 last_words 函数），"
                + "在函数参数中输入至少15字的实质发言内容。"
                + "直接输出文本无效！不调用函数等于放弃发言！"
            )
            try:
                tool_result = await self._invoke_llm_with_tools(retry_prompt, tools)
            except Exception as e:
                logger.error(
                    f"Seat {self.seat}: LLM invocation failed with exception for "
                    f"context={context} (attempt 2): {e}"
                )
                tool_result = None

        # ── Validate and return ──
        try:
            return self._validate_tool_result(tool_result, state, context)
        except Exception as e:
            logger.error(
                f"Seat {self.seat}: Unexpected error validating tool result for "
                f"context={context}: {e}. Falling back to generated speech."
            )
            return self._generate_fallback_speech(state, context)

    def _validate_tool_result(
        self, tool_result, state: GameState, context: str,
    ) -> str:
        """Validate and extract speech text from a tool call result.
        Returns fallback speech on any validation failure.
        """
        if tool_result is None:
            logger.error(
                f"Seat {self.seat}: Both LLM attempts failed to call a tool for "
                f"context={context}. Generating fallback speech to prevent silent skip."
            )
            return self._generate_fallback_speech(state, context)

        fn_name = tool_result.function_name
        args = tool_result.arguments if isinstance(tool_result.arguments, dict) else {}
        text = args.get("text", "").strip()

        # Validate: context must match function name
        if context == "day_speech" and fn_name != "speak":
            logger.warning(
                f"Seat {self.seat}: 校验失败——发言阶段调用了 {fn_name} 而非 speak，"
                f"fallback to auto speech"
            )
            return self._generate_fallback_speech(state, context)

        if context == "last_words" and fn_name != "last_words":
            logger.warning(
                f"Seat {self.seat}: 校验失败——遗言阶段调用了 {fn_name} 而非 last_words，"
                f"fallback to auto speech"
            )
            return self._generate_fallback_speech(state, context)

        if not text:
            logger.warning(
                f"Seat {self.seat}: {fn_name} called with empty text, "
                f"fallback to auto speech"
            )
            return self._generate_fallback_speech(state, context)

        # Reject speeches that are too short — "过" or trivial replies are never acceptable
        MIN_SPEECH_LENGTH = 15
        if context in ("day_speech", "last_words") and len(text) < MIN_SPEECH_LENGTH:
            logger.warning(
                f"Seat {self.seat}: {fn_name} text too short ({len(text)} chars), "
                f"minimum is {MIN_SPEECH_LENGTH}. Text was: '{text}'. "
                f"Fallback to auto speech."
            )
            return self._generate_fallback_speech(state, context)

        logger.info(f"Seat {self.seat}: {fn_name} validated OK, text length={len(text)}")
        return text

    def _generate_fallback_speech(self, state: GameState, context: str) -> str:
        """Generate a minimal but valid speech when the LLM fails to produce one.
        Ensures the player never silently disappears from the conversation.
        """
        cn_name = self.prompt_builder._cn_name(self.role_name)
        alive_others = [s for s in state.alive_players() if s != self.seat]

        if context == "last_words":
            if alive_others:
                suspects = "、".join(f"{s}号" for s in alive_others[:3])
                return (
                    f"我是{self.seat}号{cn_name}，我出局了。"
                    f"我怀疑的玩家是{suspects}，希望好人能仔细分析，找出狼人。"
                )
            return f"我是{self.seat}号{cn_name}，我出局了。希望好人阵营加油，找出最后的狼人。"

        # Day speech fallback
        if alive_others:
            # Pick a plausible suspect — prefer players with suspicious behavior
            import random
            suspect = random.choice(alive_others)
            return (
                f"我是{self.seat}号，我目前比较关注{suspect}号玩家的发言，"
                f"希望能听到更多信息来做出判断。前面几位的发言我都认真听了，"
                f"我会在后续投票中给出我的决定。"
            )
        return (
            f"我是{self.seat}号，现在场上人很少了，我需要仔细分析一下之前的发言，"
            f"慎重做出今天的投票决定。"
        )

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
        if state.phase.value not in ("speech", "vote_resolution"):
            return False, (
                f"校验失败：当前游戏阶段为「{state.phase.value}」，不是发言阶段。"
                f"发言只能在发言阶段（speech）或平票补充发言（vote_resolution）中进行。"
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

    # ── Thought recording ───────────────────────────────────────

    # ── Voting ──────────────────────────────────────────────────

    async def vote(
        self, state: GameState, conversation_log: ConversationLog, context: str
    ) -> VoteAction:
        prompt = self.prompt_builder.build_vote_prompt(
            state, self.seat, self.role_name, conversation_log, context
        )
        raw = await self._invoke_llm(prompt)
        return self.output_parser.parse_vote_action(raw, self.seat)

    # ── LLM invocation ──────────────────────────────────────────

    async def request_action(
        self,
        state: GameState,
        conversation_log: ConversationLog,
        request: ActionRequest,
    ) -> AcceptedAction:
        """Request, strictly parse, and atomically accept one issued action."""
        prompt = self._build_contract_action_prompt(state, conversation_log, request)
        messages = [
            SystemMessage(content=self.prompt_builder.get_system_prompt()),
            HumanMessage(content=prompt),
        ]
        try:
            return await self._request_action_with_transport(
                state, request, messages, self._invoke_strict_action
            )
        except StrictCapabilityError:
            return await self._request_action_with_transport(
                state, request, messages, self._invoke_json_action
            )

    def _build_contract_action_prompt(
        self,
        state: GameState,
        conversation_log: ConversationLog,
        request: ActionRequest,
    ) -> str:
        context_by_contract = {
            "werewolf_kill": "night_kill",
            "seer_check": "night_check",
            "witch_action": "witch_save",
            "hunter_shoot": "hunter_shoot",
            "exile_vote": "exile_vote",
        }
        context = context_by_contract.get(request.contract.contract_id, "action")
        extra = {}
        if request.contract.contract_id == "witch_action":
            extra["wolf_target"] = getattr(state, "last_wolf_kill_target", None)
        return self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, context, **extra
        )

    async def _request_action_with_transport(
        self, state, request, messages, invoke
    ) -> AcceptedAction:
        active_messages = messages
        for attempt in range(2):
            try:
                command = await invoke(active_messages, request)
                return self.action_validator.validate_and_accept(
                    state, request, command.model_dump()
                )
            except ActionValidationError:
                if attempt == 1:
                    return self.action_validator.safe_fallback(state, request)
                active_messages = [
                    *messages,
                    HumanMessage(
                        content=(
                            "The previous action was invalid. Submit the required "
                            "action again using the issued schema."
                        )
                    ),
                ]
        raise AssertionError("unreachable")

    async def _invoke_strict_action(self, messages, request):
        model = self.llm_client.get_model_with_action_tool(request.contract)
        response = await model.ainvoke(messages)
        return self.output_parser.parse_strict_action_response(response, request.contract)

    async def _invoke_json_action(self, messages, request):
        response = await self.llm_client.get_model().ainvoke(messages)
        content = response.content if hasattr(response, "content") else str(response)
        if not isinstance(content, str):
            raise ActionValidationError("JSON action response must be text")
        return self.output_parser.parse_action_payload(content, request.contract)

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

        has_tool_calls = (
            hasattr(response, "tool_calls") and response.tool_calls
        )
        logger.info(
            f"Seat {self.seat} LLM response: "
            f"has_tool_calls={has_tool_calls}"
        )

        return self.output_parser.parse_tool_call(response)
