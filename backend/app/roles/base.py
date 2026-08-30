from __future__ import annotations
import asyncio
import logging
import time
from typing import TYPE_CHECKING
from app.models.game import GameState
from app.models.actions import VoteAction, is_last_words_eligible
from app.core.conversation_log import ConversationLog
from app.agents.llm_client import LLMClient
from app.agents.output_parser import (
    OutputParser,
    StrictCapabilityError,
    ToolCallError,
    ToolCallResult,
)
from app.core.action_validator import ActionValidationError, ActionValidator
from app.models.contracts import AcceptedAction, ActionCommand, ActionRequest
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext,
    ActionContract as PipelineActionContract,
    SchedulePoint,
)
from langchain_core.messages import SystemMessage, HumanMessage
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

if TYPE_CHECKING:
    from app.agents.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

def _validation_failure_code(error: ActionValidationError) -> str:
    """Return a stable, non-secret classification for an invalid action."""
    return error.code


def _provider_failure_code(error: Exception) -> str:
    if isinstance(error, RateLimitError):
        return "provider_rate_limit"
    if isinstance(error, InternalServerError):
        return "provider_server_error"
    return "provider_connection_error"

_VOTE_CONTRACT = PipelineActionContract(
    contract_id="exile_vote",
    schedule_point=SchedulePoint.VOTE_ACTION,
    order=0,
    action_types=("vote", "abstain"),
    actions_requiring_target=frozenset({"vote"}),
    fallback_action_type="abstain",
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
)


class _ActionTransportTimeout(RuntimeError):
    """Signals that the primary action transport should use its fallback."""


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
        self._speech_used_fallback = False

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
        started = time.monotonic()
        raw = await self._invoke_llm(prompt)
        speech = self.output_parser.parse_speech(raw)
        self._log_llm_call(
            conversation_log,
            round_num=state.round_number, phase=state.phase.value,
            call_kind="speech_plain", contract_id=context,
            transport="text", attempt=1, prompt_chars=len(prompt),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            retried=False,
            parse_result="ok" if speech else "parse_failed",
        )
        return speech

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
        expected_tool_name = (
            "last_words" if context == "last_words" else "speak"
        )
        if context in ("day_speech", "last_words"):
            tools = [
                tool for tool in tools
                if tool.get("function", {}).get("name") == expected_tool_name
            ]

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
        started = time.monotonic()
        self._speech_used_fallback = False
        attempts = 0
        try:
            attempts += 1
            tool_result = await self._invoke_llm_with_tools(prompt, tools)
        except Exception as e:
            logger.error(
                f"Seat {self.seat}: LLM invocation failed with exception for "
                f"context={context} (attempt 1): {e}",
                exc_info=True,
            )
            tool_result = None

        # ── Attempt 2 (retry): stronger prompt emphasising tool call ──
        if tool_result is None:
            attempts += 1
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
                    f"context={context} (attempt 2): {e}",
                    exc_info=True,
                )
                tool_result = None

        # ── Validate and return ──
        try:
            speech = self._validate_tool_result(tool_result, state, context)
        except Exception as e:
            logger.error(
                f"Seat {self.seat}: Unexpected error validating tool result for "
                f"context={context}: {e}. Falling back to generated speech.",
                exc_info=True,
            )
            speech = self._generate_fallback_speech(state, context)
        self._log_llm_call(
            conversation_log,
            round_num=state.round_number, phase=state.phase.value,
            call_kind="speech", contract_id=context,
            transport="speech_tool", attempt=attempts,
            prompt_chars=len(prompt),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            retried=attempts > 1,
            parse_result="fallback" if self._speech_used_fallback else "ok",
        )
        return speech

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
        self._speech_used_fallback = True
        from app.roles.registry import builtin_registry
        try: cn_name = builtin_registry.freeze().specs[self.role_name].display_name
        except KeyError: cn_name = "玩家"
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
        if not is_last_words_eligible(cause, round_num):
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
        supports_strict = getattr(
            self.llm_client, "supports_strict_actions", True,
        )
        supports_tools = getattr(
            self.llm_client, "supports_action_tools", supports_strict,
        )
        if supports_tools is False:
            try:
                return await self._request_action_with_transport(
                    state, request, messages, self._invoke_json_action,
                    conversation_log, "json", 1,
                )
            except _ActionTransportTimeout:
                retry_messages = self._build_vote_retry_messages(
                    state, conversation_log,
                )
            try:
                return await self._request_action_with_transport(
                    state, request, retry_messages, self._invoke_json_action,
                    conversation_log, "json", 2,
                )
            except _ActionTransportTimeout:
                pass
            return await self._request_action_with_transport(
                state, request, self._final_retry_messages(state, conversation_log),
                self._invoke_json_action, conversation_log, "json", 3,
            )
        tool_transport = "strict" if supports_strict else "tool"
        try:
            return await self._request_action_with_transport(
                state, request, messages, self._invoke_strict_action,
                conversation_log, tool_transport, 1,
            )
        except _ActionTransportTimeout:
            retry_messages = self._build_vote_retry_messages(
                state, conversation_log,
            )
        except StrictCapabilityError:
            retry_messages = self._build_vote_retry_messages(
                state, conversation_log,
            )
        try:
            return await self._request_action_with_transport(
                state, request, retry_messages, self._invoke_json_action,
                conversation_log, "json", 2,
            )
        except _ActionTransportTimeout:
            pass
        return await self._request_action_with_transport(
            state, request, self._final_retry_messages(state, conversation_log),
            self._invoke_json_action, conversation_log, "json", 3,
        )

    def _final_retry_messages(
        self, state: GameState, conversation_log: ConversationLog,
    ) -> list:
        """Last-chance prompt: compact context plus a strict format reminder."""
        return [
            *self._build_vote_retry_messages(state, conversation_log),
            HumanMessage(content=(
                "The previous attempts failed. Respond with exactly one JSON "
                "object that matches the issued schema, and nothing else."
            )),
        ]

    def _build_vote_retry_messages(
        self, state: GameState, conversation_log: ConversationLog,
    ) -> list:
        prompt = self.prompt_builder.build_vote_retry_prompt(
            state, self.seat, self.role_name, conversation_log,
        )
        return [
            SystemMessage(content=self.prompt_builder.get_system_prompt()),
            HumanMessage(content=prompt),
        ]

    def _build_contract_action_prompt(
        self,
        state: GameState,
        conversation_log: ConversationLog,
        request: ActionRequest,
    ) -> str:
        if request.contract.contract_id != "exile_vote":
            raise ValueError(f"unsupported contract: {request.contract.contract_id}")
        return self.prompt_builder.build_vote_prompt(
            state, self.seat, self.role_name, conversation_log, "exile_vote"
        )

    def _vote_telemetry(
        self, conversation_log: ConversationLog, request: ActionRequest, *,
        transport: str, attempt: int, prompt_chars: int, elapsed_ms: int,
        retried: bool, parse_result: str, failure_code: str | None = None,
        timeout_type: str | None = None,
    ) -> None:
        log_telemetry = getattr(conversation_log, "log_vote_telemetry", None)
        if request.contract.contract_id == "exile_vote" and callable(log_telemetry):
            log_telemetry(
                request.round_id, self.seat, transport=transport, attempt=attempt,
                prompt_chars=prompt_chars, elapsed_ms=elapsed_ms, retried=retried,
                parse_result=parse_result, failure_code=failure_code,
                timeout_type=timeout_type, window_id=request.idempotency_key,
            )
        self._log_llm_call(
            conversation_log,
            round_num=request.round_id, phase=request.phase.value,
            call_kind="action", contract_id=request.contract.contract_id,
            transport=transport, attempt=attempt, prompt_chars=prompt_chars,
            elapsed_ms=elapsed_ms, retried=retried, parse_result=parse_result,
            failure_code=failure_code, timeout_type=timeout_type,
            window_id=request.idempotency_key,
        )

    def _log_llm_call(
        self, conversation_log: ConversationLog, *, round_num: int, phase: str,
        call_kind: str, contract_id: str | None, transport: str | None,
        attempt: int, prompt_chars: int, elapsed_ms: int, retried: bool,
        parse_result: str | None, failure_code: str | None = None,
        timeout_type: str | None = None, window_id: str | None = None,
    ) -> None:
        """Write one universal LLM call record used by benchmark analysis."""
        log_call = getattr(conversation_log, "log_llm_call", None)
        if not callable(log_call):
            return
        log_call(
            round_num, self.seat, phase,
            call_kind=call_kind, contract_id=contract_id,
            transport=transport, attempt=attempt,
            model_id=getattr(self.llm_client, "model_name", None),
            prompt_chars=prompt_chars, elapsed_ms=elapsed_ms,
            retried=retried, parse_result=parse_result,
            failure_code=failure_code, timeout_type=timeout_type,
            window_id=window_id,
        )

    def _action_timeout_seconds(self, *, retried: bool, final: bool = False) -> float:
        if final:
            attribute = "action_final_retry_timeout_seconds"
            fallback = 30.0
        elif retried:
            attribute = "action_retry_timeout_seconds"
            fallback = 90.0
        else:
            attribute = "action_timeout_seconds"
            fallback = 90.0
        value = getattr(self.llm_client, attribute, fallback)
        return float(value) if isinstance(value, (int, float)) and value > 0 else fallback

    def _log_vote_technical_abstain(
        self, conversation_log: ConversationLog, request: ActionRequest,
        *, failure_code: str, timeout_type: str | None = None,
    ) -> None:
        log_abstain = getattr(conversation_log, "log_vote_technical_abstain", None)
        if request.contract.contract_id == "exile_vote" and callable(log_abstain):
            log_abstain(
                request.round_id, self.seat, failure_code=failure_code,
                timeout_type=timeout_type, window_id=request.idempotency_key,
            )

    async def _request_action_with_transport(
        self, state, request, messages, invoke, conversation_log: ConversationLog,
        transport: str, first_attempt: int,
    ) -> AcceptedAction:
        active_messages = messages
        for attempt_number in range(first_attempt, 4):
            prompt_chars = sum(len(message.content) for message in active_messages)
            started = time.monotonic()
            try:
                command = await asyncio.wait_for(
                    invoke(active_messages, request),
                    timeout=self._action_timeout_seconds(
                        retried=attempt_number > 1, final=attempt_number > 2,
                    ),
                )
                accepted = self._accept_command(state, request, command)
            except asyncio.TimeoutError:
                should_retry = attempt_number <= 2
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1,
                    parse_result="timeout_retry" if should_retry else "timeout_fallback",
                    failure_code="request_timeout", timeout_type="local_deadline",
                )
                if should_retry:
                    raise _ActionTransportTimeout from None
                self._log_vote_technical_abstain(
                    conversation_log, request, failure_code="request_timeout",
                    timeout_type="local_deadline",
                )
                fallback = ActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None, reasoning="系统超时，代投弃权",
                )
                return self._accept_command(
                    state, request, fallback,
                    technical_failure_code="request_timeout",
                    timeout_type="local_deadline",
                )
            except APITimeoutError:
                should_retry = attempt_number <= 2
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1,
                    parse_result="timeout_retry" if should_retry else "timeout_fallback",
                    failure_code="request_timeout", timeout_type="provider_timeout",
                )
                if should_retry:
                    raise _ActionTransportTimeout from None
                self._log_vote_technical_abstain(
                    conversation_log, request, failure_code="request_timeout",
                    timeout_type="provider_timeout",
                )
                fallback = ActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None, reasoning="系统超时，代投弃权",
                )
                return self._accept_command(
                    state, request, fallback,
                    technical_failure_code="request_timeout",
                    timeout_type="provider_timeout",
                )
            except (RateLimitError, InternalServerError, APIConnectionError) as error:
                should_retry = attempt_number <= 2
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1,
                    parse_result=(
                        "invoke_error_retry" if should_retry else "invoke_error_fallback"
                    ),
                    failure_code=_provider_failure_code(error),
                )
                if should_retry:
                    raise _ActionTransportTimeout from None
                self._log_vote_technical_abstain(
                    conversation_log, request,
                    failure_code=_provider_failure_code(error),
                )
                fallback = ActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None, reasoning="系统异常，代投弃权",
                )
                return self._accept_command(
                    state, request, fallback,
                    technical_failure_code=_provider_failure_code(error),
                )
            except StrictCapabilityError:
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1, parse_result="strict_capability_fallback",
                )
                raise
            except ActionValidationError as error:
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1, parse_result="invalid",
                    failure_code=_validation_failure_code(error),
                )
                logger.warning(
                    f"Seat {self.seat}: action validation failed for "
                    f"contract={request.contract.contract_id} "
                    f"(attempt {attempt_number}): {error}"
                )
                if attempt_number == 3:
                    self._log_vote_technical_abstain(
                        conversation_log, request,
                        failure_code=_validation_failure_code(error),
                    )
                    fallback = ActionCommand(
                        action_type=request.contract.fallback_action_type,
                        target_seat=None,
                        reasoning="系统异常，代投弃权",
                    )
                    return self._accept_command(
                        state, request, fallback,
                        technical_failure_code=_validation_failure_code(error),
                    )
                if callable(getattr(
                    self.prompt_builder, "build_vote_retry_prompt", None,
                )):
                    active_messages = [
                        *self._build_vote_retry_messages(state, conversation_log),
                        HumanMessage(content=(
                            "The previous action was invalid. Submit the required "
                            "action again using the issued schema."
                        )),
                    ]
                else:
                    active_messages = [
                        *messages,
                        HumanMessage(content=(
                            "The previous action was invalid. Submit the required "
                            "action again using the issued schema."
                        )),
                    ]
            except Exception:
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1, parse_result="invoke_error",
                    failure_code="invoke_error",
                )
                raise
            else:
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1, parse_result="accepted",
                )
                return accepted
        raise AssertionError("unreachable")  # pragma: no cover - transport always returns or re-raises within the attempt window

    def _accept_command(
        self, state: GameState, request: ActionRequest, command: ActionCommand,
        *, technical_failure_code: str | None = None,
        timeout_type: str | None = None,
    ) -> AcceptedAction:
        """Validate an untrusted model decision without changing game state."""
        runtime = getattr(state, "_pipeline_runtime", None)
        revision = 0 if runtime is None else runtime.revision
        player = state.players.get(self.seat)
        context = ActionContext(
            game_id=state.game_id,
            revision=revision,
            facts={
                "alive_seats": tuple(sorted(state.alive_players())),
                "phase": state.phase.value,
                "round_number": state.round_number,
            },
            contract_id=_VOTE_CONTRACT.contract_id,
            contract_version=_VOTE_CONTRACT.schema_version,
            contract_digest=_VOTE_CONTRACT.stable_digest(),
            round_number=state.round_number,
            phase=state.phase.value,
            window_id=request.idempotency_key,
            schedule_point=_VOTE_CONTRACT.schedule_point,
            actor_seat=self.seat,
            actor_role_id=self.role_name,
            actor_alive=player is not None and player.is_alive,
            action_key=request.idempotency_key,
        )
        pipeline_command = PipelineActionCommand(
            action_type=command.action_type,
            target_seat=command.target_seat,
            reasoning=command.reasoning,
        )
        violations = self.action_validator.validate(context, _VOTE_CONTRACT, pipeline_command)
        if violations:
            raise ActionValidationError(
                violations[0].message, code=violations[0].code,
            )
        return AcceptedAction(
            request=request, command=command,
            technical_failure_code=technical_failure_code,
            timeout_type=timeout_type,
        )

    async def _invoke_strict_action(self, messages, request):
        try:
            model = self.llm_client.get_model_with_action_tool(request.contract)
            response = await model.ainvoke(messages)
        except Exception as error:
            mapped = LLMClient.map_strict_capability_error(error)
            if mapped is not error:
                raise mapped from error
            raise
        if not getattr(response, "tool_calls", None):
            # The provider ignored the action tool (e.g. answered with a tool
            # call as plain text). Degrade to JSON instead of retrying the same
            # unsupported native-tool path.
            raise StrictCapabilityError(
                "action tool transport returned no native tool call"
            )
        return self.output_parser.parse_strict_action_response(response, request.contract)

    async def _invoke_json_action(self, messages, request):
        action_model = getattr(type(self.llm_client), "get_action_model", None)
        model = (
            self.llm_client.get_action_model()
            if callable(action_model) else self.llm_client.get_model()
        )
        response = await model.ainvoke(messages)
        content = response.content if hasattr(response, "content") else str(response)
        if not isinstance(content, str):
            raise ActionValidationError(
                "JSON action response must be text", code="action_response_not_text",
            )
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
        gateway = getattr(self.llm_client, "ainvoke_json", None)
        if callable(gateway) and len(tools) == 1:
            function = tools[0]["function"]
            tool_name = function["name"]
            fallback_prompt = (
                prompt
                + "\n\n如果当前接口无法调用函数，只输出一个JSON对象："
                + '{"text":"你的5至200字中文发言"}。不要添加解释或代码块。'
            )
            messages = [
                SystemMessage(content=self.prompt_builder.get_system_prompt()),
                HumanMessage(content=fallback_prompt),
            ]
            result = await gateway(
                messages,
                tool_name=tool_name,
                schema=function["parameters"],
            )
            return ToolCallResult(
                function_name=tool_name,
                arguments=result.payload,
            )

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
