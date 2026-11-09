from __future__ import annotations
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
import logging
import random
import time
from typing import TYPE_CHECKING
from app.models.game import GamePhase, GameState
from app.models.actions import VoteAction, is_last_words_eligible
from app.core.conversation_log import ConversationLog
from app.agents.llm_client import (
    LLMClient,
    LLMUsage,
    _content_text,
    extract_usage,
    merge_usage,
    timeout_errors,
    transient_provider_errors,
)
from app.agents.failure_taxonomy import normalize_failure_code
from app.agents.output_parser import (
    OutputParser,
    StrictCapabilityError,
    ToolCallResult,
    extract_tool_call_xml,
)
from app.agents.retry import (
    KIND_REPAIR,
    RetryPolicy,
    UnusableModelOutput,
    delay_for,
    retry_plan,
)
from app.core.action_validator import ActionValidationError, ActionValidator
from app.core.vote_service import eligible_exile_targets
from app.core.sheriff_flow import unannounced_death_seats
from app.models.contracts import AcceptedAction, ActionCommand, ActionRequest
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext,
    ActionContract as PipelineActionContract,
    SchedulePoint,
)
from langchain_core.messages import SystemMessage, HumanMessage

if TYPE_CHECKING:
    from app.agents.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

# Phases in which a death is a *daytime* death. The LAST_WORDS phase announces
# night deaths only, so anything else that asks for last words is speaking for a
# daytime death — which is what lets a duel victim speak while the knight's own
# death, sharing the same cause, stays silent.
_DAYTIME_PHASES = frozenset({
    GamePhase.SPEECH, GamePhase.VOTE_CASTING, GamePhase.VOTE_RESOLUTION,
})


def _validation_failure_code(error: ActionValidationError) -> str:
    """Return a stable, non-secret classification for an invalid action."""
    return error.code


def _provider_failure_code(error: Exception) -> str:
    """Classify a transient provider error regardless of which SDK raised it.

    Delegates to the one shared vocabulary: the same HTTP 503 used to be
    ``provider_connection_error`` here and ``model_invocation_error`` on the
    night path, so one outage read as two unrelated faults.
    """
    return normalize_failure_code(error)

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


class _ActionTransientRetry(_ActionTransportTimeout):
    """A retryable provider fault: back off, then re-ask the same way.

    Subclasses ``_ActionTransportTimeout`` so every existing handler keeps
    working, but the driver catches it first: a connection reset or a 429 says
    nothing about the request *shape*, so switching to the JSON transport would
    only spend the retry budget on a different prompt for the same broken pipe.
    """


#: Contracts served by the dedicated vote client when one was configured.
#: ``sheriff_vote`` runs on the SheriffDirector night path and never reaches
#: ``request_action``, so it cannot join this set from here.
_VOTE_MODEL_CONTRACTS = frozenset({"exile_vote"})

#: ``finish_reason`` values meaning the answer was cut by the output budget.
_TRUNCATED_FINISH_REASONS = frozenset({
    "length", "max_tokens", "max_completion_tokens",
})


def _is_truncated_response(response) -> bool:
    """Whether the provider cut the answer off at the output token limit."""
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict) and not isinstance(metadata, Mapping):
        return False
    reason = metadata.get("finish_reason", metadata.get("stop_reason"))
    return reason in _TRUNCATED_FINISH_REASONS


#: Backoff before re-asking an action. The provider SDK has already spent its own
#: retries inside the failed call, so re-firing immediately lands in the very
#: same outage window -- which is how glm-5.3-flash recovered 0 of 30
#: rate-limited ballots. Tests zero the base delay out through the client's
#: ``action_retry_backoff_seconds``, mirroring the timeout attributes.
_VOTE_RETRY_POLICY = RetryPolicy(
    attempts=3, base_delay_seconds=0.5, max_delay_seconds=8.0, jitter_ratio=0.25,
)


#: Retries for one speech turn. A seat used to get two attempts, and the second
#: only re-emphasised the format: that fits a missing tool call but not a dropped
#: connection, which needs the very same request again. A too-short or empty
#: answer got no second chance at all and went straight to the template, which is
#: how a flaky provider turned into 268 of 275 speeches being generated text.
#: Classifying the failure fixes both, and one extra attempt costs far less than
#: a silently muted seat.
_SPEECH_RETRY_POLICY = RetryPolicy(
    attempts=4, base_delay_seconds=0.5, max_delay_seconds=8.0, jitter_ratio=0.25,
    retry_unknown_errors=True,
)


def _immediate_retry_errors() -> tuple[type[BaseException], ...]:
    """Failures where waiting before the next attempt only wastes the turn.

    The provider was merely slow, not stuck in an outage window, so the retry
    fires immediately -- the same call the vote ladder makes for its own
    deadlines. (``asyncio.TimeoutError`` is ``TimeoutError`` on 3.11+, hence the
    union.) Built on demand because the SDK groups are lazy.
    """
    return (asyncio.TimeoutError, *timeout_errors())


#: Appended to a speech request whose previous answer was unusable. Only ever
#: added to the in-flight request: the prompt builder's own output is what the
#: training-corpus capture and the replay digests are keyed on.
_SPEECH_REPAIR_REMINDER = (
    "\n\n【系统紧急提示】你刚才的回复无法使用（没有调用发言函数，"
    "或发言内容为空、过短、用错了函数）。这是严重的违规。"
    "请立即调用正确的发言函数（遗言用 last_words 函数），"
    "在函数参数中输入至少15字的实质发言内容。直接输出文本无效！"
)


def _speech_failure_codes(error: BaseException) -> tuple[str, str | None]:
    """The ``(failure_code, timeout_type)`` pair to record for a speech failure.

    Three kinds of failure reach the speech ladder and they must not all read as
    "fallback": a local deadline, a provider fault (a 503 that emptied the
    account pool, say), and an answer we could not use. The first two keep the
    vocabulary the vote ladder already records, so one game log can be grepped
    across both paths; the third carries the validator's own code.
    """
    if isinstance(error, asyncio.TimeoutError):
        return "request_timeout", "local_deadline"
    if isinstance(error, timeout_errors()):
        return "request_timeout", "provider_timeout"
    return normalize_failure_code(error), None


def _capture_messages(capture, messages, *, tool_schema=None, tool_name=None) -> None:
    """Hand the messages about to be sent to the prompt-capture sink.

    Duck-typed on purpose: roles only ever call ``record``, so they never import
    capture internals. ``capture`` is ``None`` whenever capture is off, which is
    the common case in tests and in every non-instrumented code path.
    """
    if capture is not None:
        capture.record(messages, tool_schema=tool_schema, tool_name=tool_name)


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
        # Optional shared client for ballot contracts (exile_vote). The game
        # service assigns it after construction when the game was created
        # with a dedicated vote model; it is a runtime attribute and is
        # never persisted.
        self.vote_llm_client = None
        # The client currently serving ``request_action``'s retry ladder.
        # Set by the entry point for the duration of one logical call so the
        # whole ladder -- timeouts, capability learning, telemetry -- reads
        # the same client the request was routed to.
        self._active_action_client = None
        self.output_parser = OutputParser()
        # The game service replaces this with its checkpointed RNG.
        self._rng = random
        self.action_validator = ActionValidator()
        self._last_words_used = False
        self._speech_used_fallback = False
        # Set by ``_invoke_strict_action`` when a tool-bound request came back as
        # plain JSON content instead of a native tool call. The caller reads it
        # to label the telemetry row, exactly like ``_speech_used_fallback``.
        self._action_tool_content_salvaged = False
        # Token usage of the current logical call, merged across the whole retry
        # ladder, plus the last single attempt's own usage. The durable wrappers
        # read the merged value after the call (the ledger can only record one
        # usage per logical request) and the per-attempt telemetry rows read the
        # single-attempt value. Both reset at the start of ``speak`` /
        # ``request_action`` so a later call can never inherit them.
        self._last_call_usage: LLMUsage | None = None
        self._last_attempt_usage: LLMUsage | None = None
        # High-water mark of ``cached_prompt_tokens`` across the attempts of
        # the in-flight logical request. A retry that comes back under that
        # mark is a provider-side cache miss — the same prompt should have hit
        # the earlier commit — so the telemetry row flags it instead of a
        # blind rewarm that would pay the same write cost as the retry itself.
        self._call_best_cached: int | None = None
        self._last_attempt_cache_regressed = False

    # ── Speech (with tool calling + validation) ─────────────────

    async def speak(
        self, state: GameState, conversation_log: ConversationLog, context: str,
        *, capture=None, on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> str | None:
        """Generate speech. Uses tool calling with validation for day_speech and last_words.
        Returns the speech text, or None if validation failed.
        """
        # One logical call = one usage total; the ladder below accumulates into it.
        self._last_call_usage = None
        self._last_attempt_usage = None
        self._call_best_cached = None
        self._last_attempt_cache_regressed = False
        prompt = self.prompt_builder.build_speech_prompt(
            state, self.seat, self.role_name, conversation_log, context
        )

        # Use tool calling for speech/last_words contexts
        if context in ("day_speech", "last_words", "sheriff_campaign"):
            return await self._speak_with_tools(
                state, prompt, context, conversation_log, capture=capture, on_chunk=on_chunk,
            )

        # Other contexts (e.g. werewolf chat handled elsewhere) use plain LLM
        started = time.monotonic()
        raw = await self._invoke_llm(prompt, capture=capture, on_chunk=on_chunk)
        speech = self.output_parser.parse_speech(raw)
        self._log_llm_call(
            conversation_log,
            round_num=state.round_number, phase=state.phase.value,
            call_kind="speech_plain", contract_id=context,
            transport="text", attempt=1, prompt_chars=len(prompt),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            retried=False,
            parse_result="ok" if speech else "parse_failed",
            usage=self._last_call_usage,
        )
        return speech

    async def _speak_with_tools(
        self, state: GameState, prompt: str, context: str,
        conversation_log: ConversationLog, *, capture=None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
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
        if context in ("day_speech", "last_words", "sheriff_campaign"):
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

        elif context == "sheriff_campaign":
            valid, reason = self._validate_sheriff_campaign(state)
            if not valid:
                logger.warning(f"Seat {self.seat} sheriff campaign validation failed: {reason}")
                return None

        elif context == "last_words":
            valid, reason = self._validate_last_words(state)
            if not valid:
                logger.warning(f"Seat {self.seat} last_words validation failed: {reason}")
                return None
            self._last_words_used = True

        # ── Ask, and keep re-asking while it is still worth it ──
        #
        # Everything that used to jump straight to the template now goes through
        # one classifier: a transport fault is retried with the same request, an
        # unusable answer with a repair reminder. Only a spent budget falls back,
        # so a flaky provider costs extra tokens instead of a muted seat.
        started = time.monotonic()
        self._speech_used_fallback = False
        policy = self._backoff_policy(_SPEECH_RETRY_POLICY)
        active_prompt = prompt
        speech: str | None = None
        # Why the ladder gave up, recorded on the fallback row. A speech failure
        # used to leave no trace beyond "fallback", which made an outage
        # indistinguishable from a model that simply refused to answer: one real
        # game produced 270 template speeches out of 275 and the log could not
        # say whether the cause was the provider or the model.
        failure_code: str | None = None
        timeout_type: str | None = None
        # ``retry_plan`` owns the budget: it reports ``None`` once the attempt
        # count reaches the policy's limit, which is what ends this loop.
        attempt_number = 0
        while True:
            attempt_number += 1
            try:
                tool_result = await self._invoke_llm_with_tools(
                    active_prompt, tools, capture=capture, on_chunk=on_chunk,
                )
                speech = self._validate_tool_result(tool_result, state, context)
            except Exception as error:
                speech = None
                failure_code, timeout_type = _speech_failure_codes(error)
                logger.warning(
                    f"Seat {self.seat}: speech attempt {attempt_number} failed for "
                    f"context={context} ({failure_code}): {error}"
                )
                plan = retry_plan(policy, error, attempt_number)
                if plan is None:
                    logger.error(
                        f"Seat {self.seat}: speech attempt {attempt_number} for "
                        f"context={context} is final ({failure_code}); falling back"
                    )
                    break
                kind, delay = plan
                if isinstance(error, _immediate_retry_errors()):
                    delay = 0.0
                await asyncio.sleep(delay)
                if kind == KIND_REPAIR:
                    active_prompt = prompt + _SPEECH_REPAIR_REMINDER
            else:
                break

        if speech is None:
            logger.error(
                f"Seat {self.seat}: all {attempt_number} speech attempts failed for "
                f"context={context}. Generating fallback speech so the seat still "
                f"takes its turn."
            )
            speech = self._generate_fallback_speech(state, context)
        self._log_llm_call(
            conversation_log,
            round_num=state.round_number, phase=state.phase.value,
            call_kind="speech", contract_id=context,
            transport="speech_tool", attempt=attempt_number,
            prompt_chars=len(prompt),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            retried=attempt_number > 1,
            parse_result="fallback" if self._speech_used_fallback else "ok",
            failure_code=failure_code,
            timeout_type=timeout_type,
            usage=self._last_call_usage,
        )
        return speech

    def _validate_tool_result(
        self, tool_result, state: GameState, context: str,
    ) -> str:
        """Validate and extract speech text, or raise :class:`UnusableModelOutput`.

        Raising rather than substituting the template is what lets the ladder
        re-ask. A missing tool call, the wrong function or a too-short answer are
        all worth one more, stricter attempt before the seat loses its voice;
        returning the template here is exactly how those failures became silent.
        """
        if tool_result is None:
            logger.warning(
                f"Seat {self.seat}: no speech tool call for context={context}"
            )
            raise UnusableModelOutput(
                "speech tool was not called", code="speech_tool_not_called",
            )

        fn_name = tool_result.function_name
        args = tool_result.arguments if isinstance(tool_result.arguments, dict) else {}
        text = args.get("text", "").strip()

        # Validate: context must match function name. Unknown contexts keep the
        # historical behaviour of not constraining the tool name at all.
        if context in {"day_speech", "sheriff_campaign", "last_words"}:
            expected = "last_words" if context == "last_words" else "speak"
            if fn_name != expected:
                logger.warning(
                    f"Seat {self.seat}: 校验失败——{context} 阶段调用了 {fn_name} "
                    f"而非 {expected}"
                )
                raise UnusableModelOutput(
                    f"{context} called {fn_name} instead of {expected}",
                    code="speech_wrong_function",
                )

        if not text:
            logger.warning(
                f"Seat {self.seat}: {fn_name} called with empty text"
            )
            raise UnusableModelOutput(
                f"{fn_name} called with empty text", code="speech_empty_text",
            )

        # Reject speeches that are too short — "过" or trivial replies are never acceptable
        MIN_SPEECH_LENGTH = 15
        if (
            context in ("day_speech", "last_words", "sheriff_campaign")
            and len(text) < MIN_SPEECH_LENGTH
        ):
            logger.warning(
                f"Seat {self.seat}: {fn_name} text too short ({len(text)} chars), "
                f"minimum is {MIN_SPEECH_LENGTH}. Text was: '{text}'."
            )
            raise UnusableModelOutput(
                f"{fn_name} text too short ({len(text)} chars)",
                code="speech_text_too_short",
            )

        return text

    def _generate_fallback_speech(self, state: GameState, context: str) -> str:
        """Generate a minimal but valid speech when the LLM fails to produce one.

        Ensures the player never silently disappears from the conversation.

        The text names the seat and nothing else. It used to splice in
        ``RoleSpec.display_name``, which turned a failed call into a public
        identity reveal: a fallback last words read 「我是12号Wolf King，我出局了」
        in games configured ``reveal_on_death=false``, and every later prompt
        quoted it back. The day-speech branch never carried the name, so only
        the last-words branch leaked.
        """
        self._speech_used_fallback = True
        alive_others = [s for s in state.alive_players() if s != self.seat]

        if context == "last_words":
            if alive_others:
                suspects = "、".join(f"{s}号" for s in alive_others[:3])
                return (
                    f"我是{self.seat}号，我出局了。"
                    f"我怀疑的玩家是{suspects}，希望好人能仔细分析，找出狼人。"
                )
            return f"我是{self.seat}号，我出局了。希望好人阵营加油，找出最后的狼人。"

        # Day speech fallback
        if alive_others:
            # Pick a plausible suspect — prefer players with suspicious behavior
            suspect = self._rng.choice(alive_others)
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

    def _validate_sheriff_campaign(self, state: GameState) -> tuple[bool, str]:
        player = state.players.get(self.seat)
        if player is None:
            return False, f"校验失败：{self.seat}号玩家不存在于游戏中"
        if not player.is_alive and self.seat not in unannounced_death_seats(state):
            return False, f"校验失败：{self.seat}号玩家已出局，无法竞选发言。"
        if state.phase.value != "sheriff_election":
            return False, (
                f"校验失败：当前游戏阶段为「{state.phase.value}」，不是警长竞选。"
            )
        return True, ""

    def _validate_last_words(self, state: GameState) -> tuple[bool, str]:
        """Validate that this player is allowed to give last words.
        Returns (is_valid, failure_reason).

        Eligibility:
        - First-night deaths (wolf_kill, poison) in round 1
        - Exiled players (any round)
        - Daytime skill deaths the rule text grants words to (a duel victim)
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
        if not is_last_words_eligible(
            cause, round_num, daytime=state.phase in _DAYTIME_PHASES,
        ):
            cause_cn_map = {
                "wolf_kill": "被狼杀", "poison": "被毒",
                "exile": "被放逐", "hunter_shot": "被猎人带走",
                "knight_duel": "骑士裁决",
            }
            cause_cn = cause_cn_map.get(cause, cause)
            return False, (
                f"校验失败：{self.seat}号玩家因「{cause_cn}」出局，不符合遗言条件。"
                f"只有第一夜死亡（被狼刀或被毒）、被放逐的玩家，"
                f"以及白天技能出局中规则允许的死者可以发表遗言。"
            )

        return True, ""

    # ── Thought recording ───────────────────────────────────────

    # ── Voting ──────────────────────────────────────────────────

    async def vote(
        self, state: GameState, conversation_log: ConversationLog, context: str,
        *, capture=None,
    ) -> VoteAction:
        prompt = self.prompt_builder.build_vote_prompt(
            state, self.seat, self.role_name, conversation_log, context
        )
        raw = await self._invoke_llm(prompt, capture=capture)
        return self.output_parser.parse_vote_action(raw, self.seat)

    # ── LLM invocation ──────────────────────────────────────────

    def _resolve_action_client(self, request: ActionRequest):
        """The client this issued action is routed to.

        A game created with a dedicated vote model hands every role the same
        shared client; only ballot contracts use it, so speeches and night
        actions keep the seat's own model. ``vote_llm_client`` is ``None``
        otherwise and this collapses to the seat client.
        """
        contract_id = getattr(
            getattr(request, "contract", None), "contract_id", None,
        )
        if self.vote_llm_client is not None and contract_id in _VOTE_MODEL_CONTRACTS:
            return self.vote_llm_client
        return self.llm_client

    def _client(self):
        """The client serving the in-flight action call (or the seat's)."""
        return self._active_action_client or self.llm_client

    async def request_action(
        self,
        state: GameState,
        conversation_log: ConversationLog,
        request: ActionRequest,
        *, capture=None,
    ) -> AcceptedAction:
        """Request, strictly parse, and atomically accept one issued action."""
        # One logical call = one usage total; the three-stage ladder accumulates.
        self._last_call_usage = None
        self._last_attempt_usage = None
        self._call_best_cached = None
        self._last_attempt_cache_regressed = False
        self._active_action_client = self._resolve_action_client(request)
        try:
            return await self._request_action_ladder(
                state, conversation_log, request, capture=capture,
            )
        finally:
            self._active_action_client = None

    async def _request_action_ladder(
        self,
        state: GameState,
        conversation_log: ConversationLog,
        request: ActionRequest,
        *, capture=None,
    ) -> AcceptedAction:
        prompt = self._build_contract_action_prompt(state, conversation_log, request)
        messages = [
            SystemMessage(content=self.prompt_builder.get_system_prompt()),
            HumanMessage(content=prompt),
        ]
        if getattr(self._client(), "is_system_one", False):
            # A decision model cannot answer chat prompts at all: the ballot
            # becomes one Choice question whose options are exactly the seats
            # the validator accepts, plus abstain. The same ladder still
            # provides timeout/transient retries and the abstain fallback.
            return await self._request_action_system_one(
                state, request, messages, conversation_log, capture=capture,
            )
        supports_strict = getattr(
            self._client(), "supports_strict_actions", True,
        )
        supports_tools = getattr(
            self._client(), "supports_action_tools", supports_strict,
        )
        if supports_tools is False:
            try:
                return await self._request_action_with_transport(
                    state, request, messages, self._invoke_json_action,
                    conversation_log, "json", 1, capture=capture,
                )
            except _ActionTransientRetry:
                retry_messages = messages
            except _ActionTransportTimeout:
                retry_messages = self._build_vote_retry_messages(
                    state, conversation_log,
                )
            try:
                return await self._request_action_with_transport(
                    state, request, retry_messages, self._invoke_json_action,
                    conversation_log, "json", 2, capture=capture,
                )
            except _ActionTransientRetry:
                last_timed_out = False
            except _ActionTransportTimeout:
                last_timed_out = True
            return await self._request_action_with_transport(
                state, request,
                self._final_retry_messages(state, conversation_log, messages, last_timed_out),
                self._invoke_json_action, conversation_log, "json", 3, capture=capture,
            )
        tool_transport = "strict" if supports_strict else "tool"
        if getattr(self._client(), "prefers_json_actions", False):
            # This provider already showed it will not honour an action tool, so
            # attempt 1 goes straight to the JSON transport instead of spending a
            # round trip rediscovering it. The vote prompt carries the schema, so
            # nothing is lost but the wasted call.
            retry_invoke, retry_transport = self._invoke_json_action, "json"
        else:
            # A capability rejection or a deadline wants the smaller JSON
            # transport; a transient provider fault does not. The transport was
            # never the problem there, so the retry keeps it and only the prompt
            # compacts.
            retry_invoke, retry_transport = self._invoke_strict_action, tool_transport
        try:
            return await self._request_action_with_transport(
                state, request, messages, retry_invoke,
                conversation_log, retry_transport, 1, capture=capture,
            )
        except _ActionTransientRetry:
            # A broken pipe says nothing about the prompt: re-ask through the
            # same transport with the same prefix, so the prefix cache and the
            # retry's own latency both stay paid for.
            retry_messages = messages
        except _ActionTransportTimeout:
            retry_invoke, retry_transport = self._invoke_json_action, "json"
            retry_messages = self._build_vote_retry_messages(
                state, conversation_log,
            )
        except StrictCapabilityError:
            # Either the provider rejected the tool schema (400/422) or it
            # answered in content instead of calling the tool. Both are
            # properties of the provider, not of this ballot, so remember it and
            # let the rest of the game skip the discovery. The transport
            # changes; the prompt stays the original so the prefix cache still
            # serves it (the full prompt carries the JSON instruction anyway).
            note_unsupported = getattr(
                self._client(), "note_action_tools_unsupported", None,
            )
            if callable(note_unsupported):
                note_unsupported()
            retry_invoke, retry_transport = self._invoke_json_action, "json"
            retry_messages = messages
        try:
            return await self._request_action_with_transport(
                state, request, retry_messages, retry_invoke,
                conversation_log, retry_transport, 2, capture=capture,
            )
        except _ActionTransientRetry:
            last_timed_out = False
        except _ActionTransportTimeout:
            retry_invoke, retry_transport = self._invoke_json_action, "json"
            last_timed_out = True
        else:
            raise AssertionError("unreachable")  # pragma: no cover
        return await self._request_action_with_transport(
            state, request,
            self._final_retry_messages(
                state, conversation_log, messages, last_timed_out,
            ),
            retry_invoke, conversation_log, retry_transport, 3, capture=capture,
        )

    async def _request_action_system_one(
        self, state, request, messages, conversation_log: ConversationLog,
        *, capture=None,
    ) -> AcceptedAction:
        """Run the ballot through a System One (Jev) decide call.

        Retries ask the identical question — there is no transport to degrade
        to and the options cannot change mid-window — so the three-attempt
        ladder is just the shared ``_request_action_with_transport`` driver.
        """
        async def invoke(active_messages, req):
            return await self._invoke_system_one_action(
                state, active_messages, req,
            )

        try:
            return await self._request_action_with_transport(
                state, request, messages, invoke,
                conversation_log, "system_one", 1, capture=capture,
            )
        except _ActionTransportTimeout:
            # Covers the transient subclass too: the question is the same.
            pass
        try:
            return await self._request_action_with_transport(
                state, request, messages, invoke,
                conversation_log, "system_one", 2, capture=capture,
            )
        except _ActionTransportTimeout:
            pass
        return await self._request_action_with_transport(
            state, request, messages, invoke,
            conversation_log, "system_one", 3, capture=capture,
        )

    async def _invoke_system_one_action(self, state, messages, request):
        """Map one ballot to a typed Choice question and back.

        ``criteria`` enumerates the contract's legal options only, so the
        returned choice is always playable; ``_accept_command`` still validates
        it as the final gate. ``reasoning`` is synthesized from Jev's
        calibrated confidence — the model produces no text of its own.
        """
        client = self._client()
        abstain = request.contract.fallback_action_type
        criteria = {
            str(seat): f"放逐{seat}号玩家"
            for seat in sorted(eligible_exile_targets(state))
        }
        criteria[abstain] = "弃权：不投票放逐任何人"
        questions = {
            request.contract.contract_id: {
                "type": "choice",
                "instructions": (
                    "狼人杀放逐投票：根据局势选出要放逐出局的玩家座位号；"
                    "没有明确判断时选择弃权。"
                ),
                "criteria": criteria,
            },
        }
        state_text = "\n\n".join(str(message.content) for message in messages)
        response = await client.ainvoke_system_one(state_text, questions)
        self._record_usage(response.usage)
        answer = response.answers.get(request.contract.contract_id) or {}
        choice = answer.get("choice")
        reasoning = self._system_one_reasoning(choice, answer)
        if choice == abstain:
            return ActionCommand(
                action_type=abstain, target_seat=None, reasoning=reasoning,
            )
        try:
            target = int(choice)
        except (TypeError, ValueError):
            raise ActionValidationError(
                "system-one answer did not pick a declared option",
                code="action_validation_error",
            ) from None
        return ActionCommand(
            action_type="vote", target_seat=target, reasoning=reasoning,
        )

    @staticmethod
    def _system_one_reasoning(choice, answer: dict) -> str:
        """Readable stand-in for the ``reasoning`` field Jev cannot write."""
        confidence = answer.get("confidence")
        if not isinstance(confidence, (int, float)):
            return "JEV 决策"
        label = "弃权" if choice == "abstain" else f"{choice}号"
        return f"JEV 决策：{label}（置信度 {confidence:.0%}）"

    def _final_retry_messages(
        self, state: GameState, conversation_log: ConversationLog,
        messages: list, last_timed_out: bool,
    ) -> list:
        """Last-chance prompt plus a strict format reminder.

        A timeout is the one failure a *shorter* prompt helps: only then does
        the last attempt rebuild the compact context. Every other failure mode
        re-asks with the original exchange so the shared prefix cache still
        serves the retry.
        """
        base = (
            self._build_vote_retry_messages(state, conversation_log)
            if last_timed_out
            else messages
        )
        return [
            *base,
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
            # One row per attempt, so it reports that attempt's own tokens — not
            # the ladder total (which the ledger records once).
            usage=self._last_attempt_usage,
            cache_regressed=self._last_attempt_cache_regressed,
        )

    def _log_llm_call(
        self, conversation_log: ConversationLog, *, round_num: int, phase: str,
        call_kind: str, contract_id: str | None, transport: str | None,
        attempt: int, prompt_chars: int, elapsed_ms: int, retried: bool,
        parse_result: str | None, failure_code: str | None = None,
        timeout_type: str | None = None, window_id: str | None = None,
        usage: LLMUsage | None = None, cache_regressed: bool = False,
    ) -> None:
        """Write one universal LLM call record used by benchmark analysis."""
        log_call = getattr(conversation_log, "log_llm_call", None)
        if not callable(log_call):
            return
        log_call(
            round_num, self.seat, phase,
            call_kind=call_kind, contract_id=contract_id,
            transport=transport, attempt=attempt,
            model_id=getattr(self._client(), "model_name", None),
            prompt_chars=prompt_chars, elapsed_ms=elapsed_ms,
            retried=retried, parse_result=parse_result,
            failure_code=failure_code, timeout_type=timeout_type,
            window_id=window_id,
            # Token columns used to be NULL on every speech/vote row, which is
            # why the report's token completeness rate sat at 42%.
            prompt_tokens=(usage.prompt_tokens if usage is not None else None),
            completion_tokens=(usage.completion_tokens if usage is not None else None),
            total_tokens=(usage.total_tokens if usage is not None else None),
            cached_prompt_tokens=(
                usage.cached_prompt_tokens if usage is not None else None
            ),
            cache_regressed=cache_regressed,
        )

    def _action_timeout_seconds(self, *, retried: bool, final: bool = False) -> float:
        """Deadline for one action attempt, never shorter than the first.

        The ladder used to hand its *last* chance a smaller budget than its first
        (30s against 90s by default). A provider slow enough to need a retry was
        therefore the one most likely to be cut off by the local deadline, which
        turned "ask again" into "give up sooner". Later attempts may be granted
        more room, never less.
        """
        base = getattr(self._client(), "action_timeout_seconds", 180.0)
        floor = float(base) if isinstance(base, (int, float)) and base > 0 else 180.0
        if final:
            attribute, fallback = "action_final_retry_timeout_seconds", 120.0
        elif retried:
            attribute, fallback = "action_retry_timeout_seconds", 240.0
        else:
            return floor
        value = getattr(self._client(), attribute, fallback)
        if not isinstance(value, (int, float)) or value <= 0:
            value = fallback
        return max(floor, float(value))

    def _backoff_policy(self, base_policy: RetryPolicy) -> RetryPolicy:
        """``base_policy`` with this client's backoff, so tests can zero it out.

        The delays are read off the client (like the three timeout attributes
        above) because the suite must never actually wait on a retry -- and the
        refusal floor is measured in tens of seconds, so a test that fed a 503
        would otherwise stall. ``replace`` rather than a fresh ``RetryPolicy``:
        the ladder's classification fields (``retry_on`` / ``repair_on`` /
        ``retry_unknown_errors``) have to survive the copy, or turning one on
        would silently do nothing.
        """
        return replace(
            base_policy,
            base_delay_seconds=self._client_seconds(
                "action_retry_backoff_seconds", base_policy.base_delay_seconds,
            ),
            refused_delay_seconds=self._client_seconds(
                "action_refused_backoff_seconds", base_policy.refused_delay_seconds,
            ),
        )

    def _client_seconds(self, attribute: str, default: float) -> float:
        """One non-negative client-provided delay, falling back to ``default``."""
        value = getattr(self._client(), attribute, default)
        if type(value) not in (int, float) or value < 0:
            return float(default)
        return float(value)

    def _retry_backoff_seconds(self, attempt: int, error: Exception) -> float:
        """Backoff before re-asking an action, honouring a provider hint."""
        return delay_for(self._backoff_policy(_VOTE_RETRY_POLICY), error, attempt)

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
        transport: str, first_attempt: int, *, capture=None,
    ) -> AcceptedAction:
        active_messages = messages
        # The JSON transport carries the schema inside the prompt itself, so only
        # the native tool transports have a tool name worth recording.
        capture_tool_name = (
            None if transport == "json" else request.contract.resolved_tool_name
        )
        for attempt_number in range(first_attempt, 4):
            prompt_chars = sum(len(message.content) for message in active_messages)
            started = time.monotonic()
            self._action_tool_content_salvaged = False
            try:
                _capture_messages(capture, active_messages, tool_name=capture_tool_name)
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
            except timeout_errors():
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
            except transient_provider_errors() as error:
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
                    # Wait first: the SDK already exhausted its own retries inside
                    # the call that just failed, so an immediate re-ask would land
                    # in the same outage window. Timeouts deliberately skip this --
                    # there the model was merely slow, and waiting only eats the
                    # vote window.
                    await asyncio.sleep(
                        self._retry_backoff_seconds(attempt_number, error)
                    )
                    raise _ActionTransientRetry from None
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
                # Re-ask on the original exchange: the model just gave an
                # unusable answer, not a reason to rebuild context. Appending
                # the reminder keeps the whole shared prefix warm for the
                # next ballot; the compact prompt stays reserved for timeouts.
                failure_code = _validation_failure_code(error)
                if failure_code == "action_response_truncated":
                    reminder = (
                        "上次推理超出输出上限、未能给出结果。"
                        "请立即直接输出符合契约的JSON对象，不要再展开推理。"
                    )
                elif failure_code == "action_payload_missing_field" and error.missing_fields:
                    reminder = (
                        "The previous action was missing required field(s): "
                        + ", ".join(error.missing_fields)
                        + ". Submit the required action again using the issued schema."
                    )
                else:
                    reminder = (
                        "The previous action was invalid. Submit the required "
                        "action again using the issued schema."
                    )
                active_messages = [*messages, HumanMessage(content=reminder)]
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
                    retried=attempt_number > 1,
                    parse_result=(
                        "strict_content_fallback"
                        if self._action_tool_content_salvaged else "accepted"
                    ),
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
        # Legal ballot targets are the alive seats that can still be exiled;
        # the vote domain owns that rule so a role only needs runtime statuses.
        context = ActionContext(
            game_id=state.game_id,
            revision=revision,
            facts={
                "alive_seats": tuple(sorted(eligible_exile_targets(state))),
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
            model = self._client().get_model_with_action_tool(request.contract)
            response = await LLMClient._acomplete(model, messages)
        except Exception as error:
            mapped = LLMClient.map_strict_capability_error(error)
            if mapped is not error:
                raise mapped from error
            raise
        self._record_usage(extract_usage(response))
        if not getattr(response, "tool_calls", None):
            # Reasoning models (e.g. MiMo) answer tool-bound requests with a
            # Hermes-style <tool_call> block as plain content. Accept it when
            # it names the issued contract and validates; incomplete blocks
            # fall through to the JSON transport for repair.
            content = _content_text(getattr(response, "content", ""))
            xml_call = extract_tool_call_xml(content)
            if xml_call is not None and xml_call[0] == request.contract.resolved_tool_name:
                try:
                    return self.output_parser.parse_action_payload(
                        dict(xml_call[1]), request.contract,
                    )
                except ActionValidationError:
                    pass
            # A gateway that binds the tool without forcing it (``forced_tool_choice``
            # is off for opencode Zen/custom-openai) leaves the model free to answer in the
            # content block. Parsing that content here is what the JSON transport
            # would do next anyway, so salvage it before paying a whole extra
            # round-trip: go-deepseek otherwise burned p50 28s per salvaged call.
            try:
                salvaged = self.output_parser.parse_action_payload(
                    content, request.contract,
                )
            except ActionValidationError:
                pass
            else:
                self._action_tool_content_salvaged = True
                # The provider answered in content instead of calling the tool.
                # Salvaging saves this ballot a round-trip, but the provider is
                # still not honouring the action tool: remember it, so the rest
                # of this seat's game starts from the JSON channel (the doc's
                # "learn once" contract). Without this, every later ballot pays
                # the same discovery cost.
                note_unsupported = getattr(
                    self._client(), "note_action_tools_unsupported", None,
                )
                if callable(note_unsupported):
                    note_unsupported()
                return salvaged
            if _is_truncated_response(response):
                # An answer cut at the output budget carries no tool call and
                # reads as "ignored the tool" -- but it is the budget's fault,
                # not the transport's, so it must not downgrade the transport
                # or get mistaken for a capability rejection.
                raise ActionValidationError(
                    "action response exceeded the output token limit",
                    code="action_response_truncated",
                )
            # The provider ignored the action tool. Degrade to JSON instead
            # of retrying the same unsupported native-tool path.
            raise StrictCapabilityError(
                "action tool transport returned no native tool call"
            )
        return self.output_parser.parse_strict_action_response(response, request.contract)

    async def _invoke_json_action(self, messages, request):
        client = self._client()
        action_model = getattr(type(client), "get_action_model", None)
        model = (
            client.get_action_model()
            if callable(action_model) else client.get_model()
        )
        response = await LLMClient._acomplete(model, messages)
        self._record_usage(extract_usage(response))
        content = _content_text(getattr(response, "content", ""))
        if not content:
            if _is_truncated_response(response):
                raise ActionValidationError(
                    "action response exceeded the output token limit",
                    code="action_response_truncated",
                )
            raise ActionValidationError(
                "JSON action response must be text", code="action_response_not_text",
            )
        return self.output_parser.parse_action_payload(content, request.contract)

    async def _invoke_llm(
        self, prompt: str, *, capture=None, on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        model = self.llm_client.get_model()
        messages = [
            SystemMessage(content=self.prompt_builder.get_system_prompt()),
            HumanMessage(content=prompt),
        ]
        _capture_messages(capture, messages)
        response = await asyncio.wait_for(
            LLMClient._acomplete(model, messages, on_chunk=on_chunk),
            timeout=self._action_timeout_seconds(retried=False, final=False),
        )
        self._record_usage(extract_usage(response))
        return response.content if hasattr(response, "content") else str(response)

    async def _invoke_llm_with_tools(
        self, prompt: str, tools: list[dict], *, capture=None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ):
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
            # Capture the rewritten prompt, not the one handed in: the fallback
            # suffix above is part of what the model actually reads.
            _capture_messages(
                capture, messages,
                tool_schema=function["parameters"], tool_name=tool_name,
            )
            try:
                result = await asyncio.wait_for(
                    gateway(
                        messages,
                        tool_name=tool_name,
                        schema=function["parameters"],
                        on_chunk=on_chunk,
                    ),
                    timeout=self._action_timeout_seconds(retried=False, final=False),
                )
            except TypeError:
                result = await asyncio.wait_for(
                    gateway(
                        messages,
                        tool_name=tool_name,
                        schema=function["parameters"],
                    ),
                    timeout=self._action_timeout_seconds(retried=False, final=False),
                )
            # The JSON gateway reports usage itself; the ledger and the telemetry
            # rows both read what this attempt cost.
            self._record_usage(getattr(result, "usage", None))
            return ToolCallResult(
                function_name=tool_name,
                arguments=result.payload,
            )
        model = self.llm_client.get_model_with_tools(tools)
        messages = [
            SystemMessage(content=self.prompt_builder.get_system_prompt()),
            HumanMessage(content=prompt),
        ]
        # This transport binds the tools to the model instead of inlining them.
        # The speech path always sends exactly one, so record that one.
        _capture_messages(
            capture, messages,
            tool_schema=tools[0]["function"]["parameters"],
            tool_name=tools[0]["function"]["name"],
        )
        response = await asyncio.wait_for(
            LLMClient._acomplete(model, messages, on_chunk=on_chunk),
            timeout=self._action_timeout_seconds(retried=False, final=False),
        )

        has_tool_calls = (
            hasattr(response, "tool_calls") and response.tool_calls
        )
        logger.info(
            f"Seat {self.seat} LLM response: "
            f"has_tool_calls={has_tool_calls}"
        )
        # A streamed answer comes back as plain text, so it carries no usage —
        # that attempt stays `usage_known=0` rather than reporting a fake zero.
        self._record_usage(extract_usage(response))

        return self.output_parser.parse_tool_call(response)

    def _record_usage(self, usage: LLMUsage | None) -> None:
        """Accumulate one attempt's token usage.

        ``_last_attempt_usage`` labels the per-attempt telemetry row;
        ``_last_call_usage`` is the ladder total the durable wrappers hand to the
        ledger (one usage per logical request, so the attempts must be summed —
        the JSON fallback's tokens are real cost and used to be dropped).
        """
        cached = getattr(usage, "cached_prompt_tokens", None)
        self._last_attempt_cache_regressed = (
            cached is not None
            and self._call_best_cached is not None
            and cached < self._call_best_cached
        )
        if cached is not None:
            self._call_best_cached = max(self._call_best_cached or 0, cached)
        self._last_attempt_usage = usage
        self._last_call_usage = merge_usage(self._last_call_usage, usage)
