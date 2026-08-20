from __future__ import annotations
import asyncio
import logging
import time
from typing import TYPE_CHECKING
from app.models.game import GameState
from app.models.actions import VoteAction
from app.core.conversation_log import ConversationLog
from app.agents.llm_client import LLMClient
from app.agents.output_parser import OutputParser, StrictCapabilityError, ToolCallError
from app.core.action_validator import ActionValidationError, ActionValidator
from app.core.effect_applier import EffectApplier, EffectPermission, derive_effect_id
from app.models.contracts import AcceptedAction, ActionCommand, ActionRequest
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext,
    ActionContract as PipelineActionContract,
    EffectKind,
    GameEffect,
    SchedulePoint,
)
from langchain_core.messages import SystemMessage, HumanMessage

if TYPE_CHECKING:
    from app.agents.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

_VOTE_CONTRACT = PipelineActionContract(
    contract_id="exile_vote",
    schedule_point=SchedulePoint.VOTE_ACTION,
    order=0,
    action_types=("vote", "abstain"),
    actions_requiring_target=frozenset({"vote"}),
    fallback_action_type="abstain",
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
)


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
                f"context={context} (attempt 1): {e}",
                exc_info=True,
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
                    f"context={context} (attempt 2): {e}",
                    exc_info=True,
                )
                tool_result = None

        # ── Validate and return ──
        try:
            return self._validate_tool_result(tool_result, state, context)
        except Exception as e:
            logger.error(
                f"Seat {self.seat}: Unexpected error validating tool result for "
                f"context={context}: {e}. Falling back to generated speech.",
                exc_info=True,
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
                state, request, messages, self._invoke_strict_action,
                conversation_log, "strict", 1,
            )
        except StrictCapabilityError:
            return await self._request_action_with_transport(
                state, request, messages, self._invoke_json_action,
                conversation_log, "json", 2,
            )

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
        retried: bool, parse_result: str,
    ) -> None:
        log_telemetry = getattr(conversation_log, "log_vote_telemetry", None)
        if request.contract.contract_id == "exile_vote" and callable(log_telemetry):
            log_telemetry(
                request.round_id, self.seat, transport=transport, attempt=attempt,
                prompt_chars=prompt_chars, elapsed_ms=elapsed_ms, retried=retried,
                parse_result=parse_result,
            )

    def _action_timeout_seconds(self) -> float:
        value = getattr(self.llm_client, "action_timeout_seconds", 45.0)
        return float(value) if isinstance(value, (int, float)) and value > 0 else 45.0

    async def _request_action_with_transport(
        self, state, request, messages, invoke, conversation_log: ConversationLog,
        transport: str, first_attempt: int,
    ) -> AcceptedAction:
        active_messages = messages
        for attempt in range(2):
            attempt_number = first_attempt + attempt
            prompt_chars = sum(len(message.content) for message in active_messages)
            started = time.monotonic()
            try:
                command = await asyncio.wait_for(
                    invoke(active_messages, request), timeout=self._action_timeout_seconds(),
                )
                accepted = self._accept_command(state, request, command)
            except asyncio.TimeoutError:
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1, parse_result="timeout_fallback",
                )
                fallback = ActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None, reasoning="timeout fallback",
                )
                return self._accept_command(state, request, fallback)
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
                )
                logger.warning(
                    f"Seat {self.seat}: action validation failed for "
                    f"contract={request.contract.contract_id} "
                    f"(attempt {attempt + 1}/2): {error}"
                )
                if attempt == 1:
                    fallback = ActionCommand(
                        action_type=request.contract.fallback_action_type,
                        target_seat=None,
                        reasoning="safe fallback",
                    )
                    return self._accept_command(state, request, fallback)
                active_messages = [
                    *messages,
                    HumanMessage(
                        content=(
                            "The previous action was invalid. Submit the required "
                            "action again using the issued schema."
                        )
                    ),
                ]
            except Exception:
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1, parse_result="invoke_error",
                )
                raise
            else:
                self._vote_telemetry(
                    conversation_log, request, transport=transport, attempt=attempt_number,
                    prompt_chars=prompt_chars, elapsed_ms=int((time.monotonic() - started) * 1000),
                    retried=attempt_number > 1, parse_result="accepted",
                )
                return accepted
        raise AssertionError("unreachable")  # pragma: no cover - transport always returns or re-raises within two attempts

    def _accept_command(self, state: GameState, request: ActionRequest,
                        command: ActionCommand) -> AcceptedAction:
        """Validate a vote command through the pure pipeline validator and
        record its acceptance atomically through EffectApplier — the only
        state-writing path in the codebase."""
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
            raise ActionValidationError(violations[0].message)
        accept = GameEffect(
            effect_id=derive_effect_id(context.action_key, 0),
            kind=EffectKind.ACCEPT_ACTION,
            source_action_key=context.action_key,
            payload={
                "actor_seat": self.seat,
                "contract_id": _VOTE_CONTRACT.contract_id,
                "window_id": request.idempotency_key,
                "round_number": state.round_number,
            },
            expected_revision=context.revision,
            sort_key=(0,),
        )
        permission = EffectPermission(
            self.seat, frozenset(), frozenset(),
            frozenset(state.alive_players()) | {self.seat}, frozenset(),
        )
        EffectApplier().apply(state, (accept,), permission)
        return AcceptedAction(request=request, command=command)

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
            # The provider ignored the forced tool call (e.g. answers with the
            # tool call as plain text instead of native tool_calls). The strict
            # contract is not honored, so degrade to the JSON transport instead
            # of retrying the same broken strict path.
            raise StrictCapabilityError(
                "strict transport returned no native tool call"
            )
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
