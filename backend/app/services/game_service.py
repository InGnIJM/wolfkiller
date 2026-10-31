import asyncio
import hashlib
import inspect
import json
import logging
import os
import random
import shutil
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from app.config import config as app_config
from app.models.game import (
    DEFAULT_VICTORY, DEFAULT_WITCH_SELF_SAVE, GameState, GameConfig, GamePhase, PlayerState,
)
from app.core.game_engine import GameEngine
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.agents.llm_client import (
    LLMClient, LLMClientConfig, classify_provider_error, derive_strict_base_url,
    env_default_client_config, merge_usage,
)
from app.agents.retry import (
    KIND_REPAIR,
    KIND_TRANSPORT,
    RetryPolicy,
    UnusableModelOutput,
    call_with_retry,
    delay_for,
    retry_plan,
)
from app.agents.failure_taxonomy import normalize_failure_code
from app.agents.model_health import AbortPolicy, HealthEvent, resolve_abort_policy
from app.agents.providers.registry import ProviderRegistry
from app.stores.app_settings_store import get_app_settings_store
from app.stores.model_config_store import get_model_config_store
from app.stores.model_key_crypto import KeyDecryptionError, ModelKeyCrypto
from app.agents.prompt_builder import PromptBuilder
from app.agents.prompt_renderer import PromptRenderer
from app.agents.output_parser import coerce_payload_to_schema
from app.agents.game_rules import NIGHT_SYSTEM_PROMPT as _SYSTEM_PROMPT
from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.night_flow import NightDirector
from app.core.wolf_pack import in_wolf_pack
from app.core.prompt_capture import (
    PromptCapture,
    PromptSink,
    build_prompt_record,
    capture_enabled_for_game,
    prompt_capture_marker_exists,
    resolve_capture_setting,
)
from app.core.role_runtime import (
    adopt_resource_declaration, resource_declaration_marker,
)
from app.agents.invocation import (
    CALL_KIND_PIPELINE, FailureBucket, exhausted_bucket,
)
from app.core.model_health_guard import (
    CIRCUIT_OPEN_CODE, ModelHealthGuard, ProviderOutageFailed, ProviderOutagePaused,
)
from app.core.scheduler import DecisionPaused, Scheduler
from app.core.sheriff_flow import ballot_weight
from app.models.pipeline import (
    DEGRADED_REASONING, ActionCommand as PipelineActionCommand, ActionContract,
    SchedulePoint,
)
from app.models.contracts import AcceptedAction, ActionCommand as ContractActionCommand
from app.api.websocket.public_events import PublicNightSubstep, PublicVoteEvent
from app.roles.registry import builtin_registry
from app.api.websocket.ws_handler import WSManager
from app.services.game_manifest import GameManifest, default_game_name
from app.services.game_summary import build_and_write_summary
from app.persistence.checkpoint_codec import CheckpointCodec, CheckpointError
from app.persistence import recovery_compat
from app.persistence.repository import (
    GameReferencedByBenchmark, GameRepository, InvalidExecutionTransition,
)
from app.services.audience_projector import AudienceProjector
from app.services.durable_step_coordinator import DurableStepCoordinator
from app.services.model_invocation_service import (
    ModelInvocationService, ModelRequestUnavailable,
)

logger = logging.getLogger(__name__)

# A game in one of these states has a durable checkpoint that may still be
# resumable; ``recovery_blocked`` is included because a block is a diagnosis,
# not a verdict — the user can fix the cause and retry.
_RECOVERABLE_STATUSES = frozenset({"paused", "interrupted", "failed", "recovery_blocked"})

# Refusals the recovery path itself may produce once the checkpoint decoded and
# the registry verdict was resumable. Anything else is reported as corruption.
_RECOVERY_FAILURE_CODES = frozenset({
    "model_config_missing", "model_key_unavailable", "registry_incompatible",
    "checkpoint_version_unsupported", "checkpoint_corrupt",
})


def _decode_failure_code(message: str) -> str:
    """Map a codec refusal onto a stable, readable block code."""
    if "unsupported checkpoint version" in message:
        return "checkpoint_version_unsupported"
    return "checkpoint_corrupt"


def _shuffle_model_assignments(values: list[int]) -> None:
    """Shuffle model seats without consuming the role allocator's PRNG state."""
    random.SystemRandom().shuffle(values)


def _camp_value(value: object) -> str:
    """Normalize a stored camp (plain str or str-Enum) into its public label."""
    return value.value if isinstance(value, Enum) else str(value)


def _plain_json(value):
    """Detach the scheduler's frozen JSON containers for repository writes."""
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain_json(item) for item in value), key=repr)
    return value


PUBLIC_NIGHT_SUBSTEPS = frozenset({
    "werewolf_open",
    "werewolf_vote",
    "werewolf_target",
    "werewolf_close",
    "witch_open",
    "witch_action",
    "witch_close",
    "seer_open",
    "seer_check",
    "seer_close",
})

# Domain event types a daytime pipeline window may emit that are already
# published through a dedicated checkpoint: deaths through the
# ``night_death:`` / ``night_reaction:`` / ``day_interrupted:`` /
# ``day_reaction:`` / ``exile_reaction:`` / ``delayed_death:`` steps, the card
# flip through ``exile_cancelled:`` (which synthesises ``EXILE_CANCELLED`` and
# ``PLAYER_REVEALED`` from state). Replaying them with the window would
# publish every one of them twice.
_DAY_POINT_SKIPPED_EVENTS = frozenset({
    "PLAYER_DIED", "EXILE_CANCELLED", "PLAYER_REVEALED",
})

_NIGHT_ACTOR_POINTS = frozenset({
    SchedulePoint.NIGHT_ACTION,
    SchedulePoint.NIGHT_WITCH_ACTION,
    SchedulePoint.NIGHT_WITCH_ASSIST_PICK,
    SchedulePoint.NIGHT_WITCH_ASSIST_VOTE,
    SchedulePoint.NIGHT_SEER_ACTION,
})


def _pending_window_rows(engine: GameEngine, event_id: str) -> list[dict[str, object]]:
    """Rows for the events the engine handed to the step being committed.

    The engine fills its slot right before the commit — a daytime window batch
    or a settlement's own events — so the step is the only place they can be
    published from. Deaths are skipped: the same step announces them from the
    ledger by seat, and a settlement's deaths are already in the label.
    """
    rows: list[dict[str, object]] = []
    for index, (event_type, payload) in enumerate(
        getattr(engine, "_pending_point_events", ()),
    ):
        if event_type in _DAY_POINT_SKIPPED_EVENTS:
            continue
        row_payload = _plain_json(payload)
        row_payload.setdefault("round_number", engine.state.round_number)
        rows.append({
            "event_id": f"domain:{event_id}:{index}",
            "event_type": event_type,
            "payload": row_payload,
            "visibility": ["PUBLIC"],
            "schema_version": 1,
        })
    return rows


def _model_failure_code(error: Exception) -> str:
    """Classify a night/action provider error through the one shared vocabulary.

    It used to answer ``model_invocation_error`` for anything that was not a
    timeout or a 400, so the HTTP 503 that emptied the upstream account pool was
    recorded under a different name here than on the daytime ballot path.

    An error that carries its own stable token wins: the parse layer knows
    *which* field was missing or whether a tool call ever arrived, and that
    detail is the difference between a gateway that answered nothing and a
    model that answered the wrong shape.
    """
    return normalize_failure_code(error)


def _pipeline_command(payload: object, contract) -> PipelineActionCommand:
    """Validate one model payload into a pipeline command.

    A contract that declares multi-target actions additionally gets the shared
    schema coercion first: text-extracted payloads reach this boundary as
    strings (``"3,5,7"`` / ``"[3,5,7]"``), and the strict command model would
    reject them outright. Single-target contracts keep the historical path byte
    for byte, so no existing behaviour moves.
    """
    # ``degraded`` 是引擎自己盖的章，模型答复不得自带。它是**声明**字段，
    # 所以 ``extra="forbid"`` 拦不住它；而一个自封的降级会让本人历史被跳过、
    # 上帝视角把真实决策显示成「系统代行」。载荷恒是 mapping
    # （``StructuredResponse.payload`` 就是 ``dict``），所以这里不需要类型分支。
    payload = {
        key: value for key, value in dict(payload).items() if key != "degraded"
    }
    if getattr(contract, "multi_target_actions", None):
        payload = coerce_payload_to_schema(dict(payload), contract.json_schema())
    return PipelineActionCommand.model_validate(payload)


def _durable_command_payload(command: PipelineActionCommand) -> dict[str, object]:
    """Serialise a command for the durable ledger **without** losing ``degraded``.

    ``ActionCommand.degraded`` is ``exclude=True`` on purpose: ``stable_digest()``
    hashes ``model_dump()``, and a field in the dump would reorder two otherwise
    identical fallback commands inside one aggregate contract. But the durable
    provider path *is* the production path whenever a repository is attached, and
    it round-trips every command through ``model_dump()`` + ``model_validate`` —
    so without this helper the flag died exactly where it matters, and the write
    entry went on counting a fallback as a spent card (one provider outage could
    silently void the hunter's gun).
    """
    payload = command.model_dump()
    if command.degraded:
        # Written only when true, so archived rows keep their historical byte
        # shape and a row without the key still decodes back to ``False``.
        payload["degraded"] = True
    return payload


def _exception_diagnostics(error: BaseException) -> dict[str, object]:
    response = getattr(error, "response", None)
    status_code = getattr(error, "status_code", None)
    if not isinstance(status_code, int):
        candidate = getattr(response, "status_code", None)
        status_code = candidate if isinstance(candidate, int) else None

    body = getattr(error, "body", None)
    body_error = body.get("error") if isinstance(body, Mapping) else None
    if not isinstance(body_error, Mapping):
        body_error = {}
    metadata = body_error.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}

    structured_message = body_error.get("message")
    if isinstance(structured_message, str) and structured_message:
        message = structured_message[:1000]
    else:
        # Fall back to the exception's own text before the generic label. The
        # parse layer says exactly what was wrong ("structured response omitted
        # required fields: target_seat"), and this used to replace it with
        # "model invocation failed" -- so a game log could not distinguish an
        # empty gateway reply from a schema miss, which is the one question a
        # failure log is read to answer.
        message = (str(error) or "").strip()[:1000] or (
            f"{type(error).__name__}: model invocation failed"
        )

    details: dict[str, object] = {
        "exception_type": type(error).__name__,
        "message": message,
    }
    if status_code is not None:
        details["status_code"] = status_code
    error_code = body_error.get("code")
    if isinstance(error_code, (str, int)) and not isinstance(error_code, bool):
        details["error_code"] = error_code
    provider_name = metadata.get("provider_name")
    if isinstance(provider_name, str) and provider_name:
        details["provider_name"] = provider_name[:200]
    provider_raw = metadata.get("raw")
    if isinstance(provider_raw, str) and provider_raw:
        details["provider_raw"] = provider_raw[:1000]
    return details


def _model_error_diagnostics(error: Exception, llm_client) -> dict[str, object]:
    primary = _exception_diagnostics(error)
    profile = getattr(getattr(llm_client, "provider_profile", None), "profile_id", None)
    model_id = getattr(llm_client, "model_name", None)
    details: dict[str, object] = {
        "provider_profile": profile if isinstance(profile, str) else "unknown",
        "model_id": model_id if isinstance(model_id, str) else "unknown",
        "failure_code": _model_failure_code(error),
        **primary,
    }
    cause_chain: list[dict[str, object]] = []
    cause = error.__cause__ or error.__context__
    seen = {id(error)}
    while cause is not None and id(cause) not in seen and len(cause_chain) < 8:
        seen.add(id(cause))
        cause_chain.append(_exception_diagnostics(cause))
        cause = cause.__cause__ or cause.__context__
    if cause_chain:
        details["cause_chain"] = cause_chain
    details["stack"] = [
        {
            "file": frame.filename,
            "line": frame.lineno,
            "function": frame.name,
        }
        for frame in traceback.extract_tb(error.__traceback__)[-30:]
    ]
    return details


def _summed_usage(usages: "list[object]"):
    """Sum one ladder's per-rung usage into a single record (``None`` if unreported)."""
    merged = None
    for item in usages:
        merged = merge_usage(merged, item)
    return merged


def _night_call_meta(state, tool_name: str) -> tuple[str, str]:
    """Return ``(call_kind, phase)`` for one night-director model call.

    The phase comes from the live game state, not from the schedule point: the
    sheriff election, the badge hand-off and the speech-side decision all run
    through the same ``night_invoke`` closure but in ``sheriff_election`` /
    ``vote_resolution`` / ``speech``. Unknown/empty phase falls back to
    ``night`` so telemetry never breaks the game.
    """
    raw_phase = getattr(state, "phase", None) if state is not None else None
    phase = getattr(raw_phase, "value", raw_phase)
    if not isinstance(phase, str) or not phase:
        phase = "night"
    call_kind = "action" if tool_name.startswith("sheriff_") else "night"
    return call_kind, phase


def _log_llm_telemetry(
    game_logger, game_id: str, round_number: int, seat: int, *,
    tool_name: str, model_id: Optional[str], prompt_chars: int,
    elapsed_ms: int = 0, usage=None, attempts: int = 1,
    parse_result: str, failure_code: Optional[str] = None,
    call_kind: str = "night", transport: str = "night_json",
    phase: str = "night",
) -> None:
    """Write one model-call record; telemetry must never break the game flow.

    Both the wolf channel and the generic role pipeline come through here: the
    summary reads ``llm_calls.log`` and nothing else, so a path that skips this
    row is invisible in its call count, token total and failure rate (the
    pipeline's skill actions were missing exactly that way, while the durable
    ledger had them all).
    """
    try:
        game_logger.log_llm_call(
            game_id, round_number, phase, seat,
            call_kind=call_kind, contract_id=tool_name, transport=transport,
            attempt=attempts, model_id=model_id, prompt_chars=prompt_chars,
            elapsed_ms=elapsed_ms,
            prompt_tokens=(usage.prompt_tokens if usage else None),
            completion_tokens=(usage.completion_tokens if usage else None),
            total_tokens=(usage.total_tokens if usage else None),
            retried=attempts > 1, parse_result=parse_result,
            failure_code=failure_code,
        )
    except Exception:
        logger.exception(
            "Failed to persist llm telemetry for game=%s seat=%s",
            game_id, seat,
        )


#: Night turns used to be asked exactly once: the pipeline is the only model
#: path with no application-level retry, so a single connection reset forfeited
#: a wolf kill or a whole discussion turn. The ladder now pairs backoff with a
#: repair attempt, so an empty or malformed answer no longer costs the turn
#: outright. Three attempts on top of the SDK's own retries bounds a night call
#: at 3 x the night transport timeout plus backoff.
_NIGHT_RETRY_POLICY = RetryPolicy(
    attempts=3, base_delay_seconds=0.5, max_delay_seconds=8.0, jitter_ratio=0.25,
    retry_unknown_errors=True,
)


#: Retries for a pipeline-action failure. A transient fault is resent; an
#: unusable payload is re-asked with a repair reminder. This provider runs on a
#: worker thread, so a blocking sleep is safe here.
_PIPELINE_RETRY_POLICY = RetryPolicy(
    attempts=3, base_delay_seconds=0.5, max_delay_seconds=8.0, jitter_ratio=0.25,
    retry_unknown_errors=True,
)


@dataclass(frozen=True)
class _NightTurn:
    """One night-turn model answer plus the attempts it actually took."""

    payload: dict[str, object]
    usage: object | None
    elapsed_ms: int
    attempts: int


def _with_repair_reminder(messages, suffix: str) -> list:
    """Return ``messages`` with the repair instruction appended to the last turn.

    The reminder is merged into the final message rather than added as a new one:
    two consecutive user turns are rejected or silently reordered by some
    gateways, and every transport here already ends on a user turn.

    Message objects are copied, not mutated, and keep their type: the night flow
    hands over plain dicts while the action pipeline hands over LangChain
    messages, and flattening the latter into dicts would quietly drop the
    ``additional_kwargs`` the transport carries.

    Only the in-flight provider call sees this. The durable request digest is
    computed from the original messages before the call runs, so a repaired
    attempt never changes the request's identity, and a recovery replay simply
    repairs again if it has to.
    """
    active = list(messages)
    if not active:
        return [{"role": "user", "content": suffix.lstrip("\n")}]
    last = active[-1]
    if isinstance(last, dict):
        merged: object = {**last, "content": f"{last.get('content') or ''}{suffix}"}
    else:
        merged = last.model_copy(
            update={"content": f"{getattr(last, 'content', '') or ''}{suffix}"},
        )
    return [*active[:-1], merged]


def _rule_retry_hint(contract: ActionContract) -> str:
    """Repair instruction appended to the scheduler's second provider attempt.

    ``Scheduler._command`` asks once, validates the answer against the
    contract, and asks again only after a rule rejection; the hint is what makes
    the second ask a *repair* instead of an identical replay. The text is
    contract-aware: a contract whose non-pass action takes no target
    (``awakened_wolf_king_explode``, ``order_rewind``) must be told to send
    ``null``, not to invent a seat the validator then rejects with
    ``target_forbidden``. ``RANDOM_HINT`` only exists for first-night target
    contracts, so that reminder is conditional too.
    """
    required = sorted(
        set(contract.action_types) & set(contract.actions_requiring_target)
    )
    multi = sorted(contract.multi_target_actions)
    if multi:
        count = contract.multi_target_actions[multi[0]]
        body = (
            "多目标动作（" + "、".join(multi) + "）必须把 "
            f"{count} 个互不相同的存活座位写进 target_seats，并把 target_seat 留空（null）"
        )
        if required:
            body += (
                "；需要单目标的动作（" + "、".join(required)
                + "）必须把座位号写进 target_seat"
            )
        body += "；其余动作一律把 target_seat 写成 null"
    elif required:
        body = (
            "需要目标的动作（" + "、".join(required) + "）必须把目标座位号写进 target_seat；"
            "其余动作（包括 pass）必须把 target_seat 写成 null"
        )
    else:
        body = (
            "本契约的所有动作都不需要目标：包括非 pass 动作在内，"
            "一律把 target_seat 写成 null，不要填任何座位号"
        )
    hint = "\n\n【规则校验失败】上一次输出的动作没有通过引擎校验：" + body
    if required or multi:
        hint += "；使用 RANDOM_HINT 时也要把该号码抄进对应字段，留空不会替你选择"
    return hint + "。请重新输出同一契约的 JSON。"


def _pipeline_request_id(action_key: str, attempt: int) -> str:
    """Durable request id for one pipeline action attempt.

    The first attempt keeps the historical ``pipeline:<action_key>`` key, so
    already-resolved rows from interrupted games still replay exactly. A rule
    rejection increments ``attempt``; that attempt must get a distinct id or
    :meth:`ModelInvocationService.invoke_sync` returns the cached rejected
    answer and the "re-ask once" path never reaches the model.
    """
    if attempt > 0:
        return f"pipeline:{action_key}:retry:{attempt}"
    return f"pipeline:{action_key}"


def _night_turn(
    llm_client, messages, *, tool_name: str, schema: dict[str, object],
) -> _NightTurn:
    """Ask the model for one night-turn payload, backing off on transients.

    The retry lives *inside* the durable provider call on purpose. The ledger
    then still records exactly one logical attempt, so a recovered retry never
    leaves the request marked ``failed`` -- and
    :class:`~app.services.model_invocation_service.ModelInvocationService`
    refuses to redispatch a failed request id, which would poison that night
    turn for the rest of the game.

    An unusable answer is retried too, not accepted as final. A dropped
    connection and an empty or malformed payload fail for different reasons, but
    both used to cost the seat its whole night action: the transport fault got
    one blind resend and the malformed payload got none at all. The repair
    attempt re-asks the same schema with an explicit format reminder.
    """
    started = time.monotonic()
    attempts = 0
    repair_suffix = (
        "\n\n【系统提示】上一次回复的内容无法解析。"
        f"请只调用 {tool_name} 函数，参数严格遵守给定 schema，不要输出任何其他文字。"
    )
    active_messages = messages

    def call(kind: str):
        nonlocal attempts, active_messages
        attempts += 1
        if kind == KIND_REPAIR:
            active_messages = _with_repair_reminder(messages, repair_suffix)
        produced = llm_client.invoke_json(
            active_messages, tool_name=tool_name, schema=schema,
        )
        value = getattr(produced, "payload", None)
        if not isinstance(value, Mapping):
            # The transport worked and the model answered something unusable:
            # a repair attempt can still save the turn, so classify it as an
            # output failure rather than letting it fail the seat outright.
            raise UnusableModelOutput("model gateway response is not a JSON object")
        return produced, dict(value)

    produced, payload = call_with_retry(call, _NIGHT_RETRY_POLICY)
    return _NightTurn(
        payload=payload,
        usage=getattr(produced, "usage", None),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        attempts=attempts,
    )


def _saved_client_config(
    config_id: str, env_config: LLMClientConfig,
) -> tuple[LLMClientConfig, dict]:
    config = get_model_config_store().get(config_id)
    if config is None:
        raise ValueError("unknown model config")
    crypto = ModelKeyCrypto()
    if config.api_key_encrypted:
        try:
            api_key = crypto.decrypt(config.api_key_encrypted)
        except KeyDecryptionError:
            raise ValueError("model config api key is invalid") from None
    else:
        api_key = env_config.api_key
    client_config = LLMClientConfig(
        base_url=config.base_url,
        api_key=api_key,
        model_id=config.model_id,
        temperature=(
            config.temperature
            if config.temperature is not None
            else env_config.temperature
        ),
        max_tokens=env_config.max_tokens,
        action_max_tokens=env_config.action_max_tokens,
        strict_base_url=derive_strict_base_url(
            config.base_url, config.strict_base_url,
        ),
        action_timeout_seconds=env_config.action_timeout_seconds,
        action_retry_timeout_seconds=env_config.action_retry_timeout_seconds,
        action_final_retry_timeout_seconds=env_config.action_final_retry_timeout_seconds,
        provider_profile=config.provider_profile,
        headers=tuple(config.headers.items()),
    )
    resolved_profile = ProviderRegistry().resolve(
        client_config.provider_profile,
        client_config.base_url,
        client_config.model_id,
    ).profile_id
    return client_config, {
        "config_id": config.id,
        "name": config.name,
        "model_id": config.model_id,
        "base_url": config.base_url,
        "provider_profile": resolved_profile,
    }


def resolve_model_assignments(
    model_assignments: Optional[list[dict]],
    total_players: int,
    *,
    shuffle: Optional[Callable[[list[int]], None]] = None,
) -> tuple[dict[int, LLMClientConfig], list[dict]]:
    """Resolve, validate and randomly assign model configurations to seats.

    Resolution is deliberately completed before shuffling or constructing any
    clients, so a missing config or undecryptable key cannot leave a partial
    game behind. The returned display snapshot contains no credentials.
    """
    if (
        isinstance(total_players, bool)
        or not isinstance(total_players, int)
        or total_players <= 0
    ):
        raise ValueError("total players must be a positive integer")
    env_config = replace(env_default_client_config(), provider_profile="auto")
    if model_assignments is None:
        model_assignments = [{"config_id": None, "count": total_players}]
    if not isinstance(model_assignments, list):
        raise ValueError("model assignments must be a list")
    if not model_assignments:
        raise ValueError("model assignments must be non-empty")

    normalized: list[tuple[str | None, int]] = []
    seen_config_ids: set[str | None] = set()
    for entry in model_assignments:
        if not isinstance(entry, dict):
            raise ValueError("model assignment entry must be an object")
        config_id = entry.get("config_id")
        count = entry.get("count")
        if config_id is not None and not isinstance(config_id, str):
            raise ValueError("model assignment config_id must be a string or null")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("model assignment count must be a positive integer")
        if config_id in seen_config_ids:
            raise ValueError("model assignment config_id values must be unique")
        seen_config_ids.add(config_id)
        normalized.append((config_id, count))
    if sum(count for _, count in normalized) != total_players:
        raise ValueError("model assignment count sum must equal total players")

    resolved: list[tuple[LLMClientConfig, dict, int]] = []
    for config_id, count in normalized:
        if config_id is None:
            resolved_profile = ProviderRegistry().resolve(
                env_config.provider_profile, env_config.base_url, env_config.model_id,
            ).profile_id
            resolved.append((env_config, {
                "config_id": None,
                "name": "环境默认 (.env)",
                "model_id": env_config.model_id,
                "base_url": env_config.base_url,
                "provider_profile": resolved_profile,
            }, count))
        else:
            client_config, metadata = _saved_client_config(config_id, env_config)
            resolved.append((client_config, metadata, count))

    expanded = [
        assignment_index
        for assignment_index, (_, _, count) in enumerate(resolved)
        for _ in range(count)
    ]
    (shuffle or _shuffle_model_assignments)(expanded)

    seat_configs: dict[int, LLMClientConfig] = {}
    seats_by_assignment: list[list[int]] = [[] for _ in resolved]
    for seat, assignment_index in enumerate(expanded, start=1):
        seat_configs[seat] = resolved[assignment_index][0]
        seats_by_assignment[assignment_index].append(seat)

    snapshot = [
        {
            **metadata,
            "count": count,
            "seats": sorted(seats_by_assignment[index]),
        }
        for index, (_, metadata, count) in enumerate(resolved)
    ]
    return seat_configs, snapshot


def resolve_model_config(
    model_assignments: Optional[list[dict]],
    total_players: int,
) -> tuple[LLMClientConfig, list[dict]]:
    """Compatibility wrapper for callers that still request one model."""
    if model_assignments is not None and (
        not isinstance(model_assignments, list) or len(model_assignments) != 1
    ):
        raise ValueError("single model config requires exactly one assignment")
    seat_configs, snapshot = resolve_model_assignments(
        model_assignments, total_players,
    )
    return seat_configs[1], snapshot


_MODEL_PARAMETER_FIELDS = (
    "base_url", "model_id", "temperature", "max_tokens", "strict_base_url",
    "action_max_tokens", "action_timeout_seconds",
    "action_retry_timeout_seconds", "action_final_retry_timeout_seconds",
)


def _model_parameters(config: LLMClientConfig) -> dict[str, object]:
    profile = ProviderRegistry().resolve(
        config.provider_profile, config.base_url, config.model_id,
    ).profile_id
    values = {name: getattr(config, name) for name in _MODEL_PARAMETER_FIELDS}
    values["provider_profile"] = profile
    return values


def _parameter_digest(parameters: Mapping[str, object]) -> str:
    raw = json.dumps(
        dict(parameters), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _freeze_model_runtime(
    seat_configs: Mapping[int, LLMClientConfig], model_snapshot: list[dict],
) -> list[dict[str, object]]:
    frozen: list[dict[str, object]] = []
    for entry in model_snapshot:
        seats = entry.get("seats")
        if not isinstance(seats, list) or not seats:
            raise ValueError("model snapshot has no seats")
        first = seats[0]
        if type(first) is not int or first not in seat_configs:
            raise ValueError("model snapshot has an invalid seat")
        parameters = _model_parameters(seat_configs[first])
        if any(
            type(seat) is not int
            or seat not in seat_configs
            or _model_parameters(seat_configs[seat]) != parameters
            for seat in seats
        ):
            raise ValueError("model snapshot parameters are inconsistent")
        frozen.append({
            "config_id": entry.get("config_id"),
            "seats": list(seats),
            "parameters": parameters,
            "parameters_digest": _parameter_digest(parameters),
        })
    return frozen


def _prompt_digest() -> str:
    return hashlib.sha256(_SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def _accepts_parameter(func, name: str) -> bool:
    """Whether a role callable declares ``name`` (or swallows anything).

    Production roles are ``BaseRole`` subclasses and take both extras. Test
    doubles often keep the historical three-argument shape, so the durable
    wrapper adapts to both rather than forcing every double to grow a parameter.
    """
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return any(
        parameter_name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter_name, parameter in parameters.items()
    )


def _accepts_capture(func) -> bool:
    """Whether a role callable can take the prompt-capture sink."""
    return _accepts_parameter(func, "capture")


def _accepts_chunk(func) -> bool:
    """Whether a role callable can take the speech streaming callback."""
    return _accepts_parameter(func, "on_chunk")


def _capture_adapter(func):
    """Adapt a role callable to ``(state, conversation_log, payload, sink, on_chunk)``.

    ``on_chunk`` must travel through this adapter: without it the durable
    ``speak`` wrapper could not accept the engine's callback, the engine's
    ``TypeError`` fallback fired on every speech, and ``SPEECH_STREAM`` was
    silently never published in production.
    """
    takes_capture = _accepts_capture(func)
    takes_chunk = _accepts_chunk(func)

    async def call(state, conversation_log, payload, sink, on_chunk=None):
        extra: dict[str, object] = {}
        if takes_capture:
            extra["capture"] = sink
        if takes_chunk and on_chunk is not None:
            extra["on_chunk"] = on_chunk
        return await func(state, conversation_log, payload, **extra)
    return call
    return call


def _usage_mapping(value: object) -> dict[str, int] | None:
    if value is None:
        return None
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    if any(type(getattr(value, name, None)) is not int for name in fields):
        return None
    return {name: int(getattr(value, name)) for name in fields}


class GameService:
    """Manages game lifecycle: creation, execution, state access, and event broadcasting.

    Game metadata is persisted to data/games/index.json so the game list
    survives backend restarts.  Completed games can be browsed and their
    logs replayed without the engine still running.
    """

    def __init__(
        self,
        ws_manager: WSManager,
        event_bus: EventBus,
        memory_service=None,
        data_dir: str = "data",
        repository: GameRepository | None = None,
        abort_policy: AbortPolicy | None = None,
    ):
        self.ws_manager = ws_manager
        self.event_bus = event_bus
        self.memory_service = memory_service
        self.data_dir = data_dir
        self._games: dict[str, GameState] = {}
        self._engines: dict[str, GameEngine] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._model_snapshots: dict[str, list[dict]] = {}
        self._llm_clients: dict[str, dict[int, LLMClient]] = {}
        self._manifest = GameManifest(data_dir=data_dir)
        self.repository = repository
        self._registry_snapshot = builtin_registry.freeze()
        self._checkpoint_codec = CheckpointCodec(self._registry_snapshot)
        self._projector = AudienceProjector()
        self._coordinator = (
            None if repository is None else DurableStepCoordinator(
                repository, self._checkpoint_codec, self._projector,
            )
        )
        self._model_invocations = (
            None if repository is None else ModelInvocationService(repository)
        )
        self._durable_contexts: dict[str, dict[str, int]] = {}
        # ``(game_id, action_key)`` pairs already paused once for an exhausted
        # one-shot contract. The benchmark executor resumes paused games
        # automatically, so without this bound a permanently broken provider
        # would pause the same point forever instead of degrading it.
        self._paused_points: set[tuple[str, str]] = set()
        #: Provider-health guards, one per live game. The engine calls
        #: ``check(seat)`` at its turn boundaries; the guard is fed by the
        #: ``llm_calls`` telemetry observer. Runtime-only: a resumed game gets a
        #: fresh guard, so a fixed provider is not judged by pre-restart samples.
        self._health_guards: dict[str, ModelHealthGuard] = {}
        self._abort_policy = abort_policy or resolve_abort_policy()
        # Runtime-only, like the other per-game maps here: capture handles must
        # never reach a checkpoint, because the orchestration codec matches field
        # sets exactly and any extra field stops old games from resuming.
        self._prompt_captures: dict[str, PromptCapture | None] = {}
        # What the operator switch said when this game was created. Recovery
        # paths never write here, which is what keeps an interrupted game from
        # starting to capture in the middle (see ``_latch_capture_request``).
        self._capture_requests: dict[str, bool] = {}
        # The pipeline provider runs on an ``asyncio.to_thread`` worker while the
        # speech and vote paths run on the event loop, so this map is touched from
        # two threads. Two handles for one game would mean two descriptors and two
        # independent write locks, which interleaves records into corrupt lines.
        self._prompt_captures_lock = threading.Lock()
        self._recovery_warnings: dict[str, list[str]] = {}
        self._clock_tasks: dict[str, asyncio.Task] = {}
        self._clock_state: dict[str, dict[str, int | float | None]] = {}
        # One guard per game for every control path that can start an engine.
        # Never popped: dropping a lock while it is held would let a second one
        # be created for the same game.
        self._control_locks: dict[str, asyncio.Lock] = {}
        # Set by ``aclose`` so consumption-wait loops can escape: a paused game
        # parks executor workers in them forever, and they must exit before
        # ``loop.shutdown_default_executor`` can join them.
        self._closed = threading.Event()

        # Restore completed games so list / detail endpoints still work
        self._load_persisted_games()
        self._load_native_games()

        self.event_bus.subscribe(BusEvent.PHASE_CHANGED, self._on_phase_changed)
        self.event_bus.subscribe(BusEvent.PLAYER_DIED, self._on_player_died)
        self.event_bus.subscribe(BusEvent.SPEECH_MADE, self._on_speech_made)
        self.event_bus.subscribe(BusEvent.SPEAKING, self._on_speaking)
        self.event_bus.subscribe(BusEvent.SPEECH_STREAM, self._on_speech_stream)
        self.event_bus.subscribe(BusEvent.SPEECH_THINKING, self._on_speech_thinking)
        self.event_bus.subscribe(BusEvent.ACTING, self._on_acting)
        self.event_bus.subscribe(BusEvent.VOTE_CAST, self._on_vote_cast)
        self.event_bus.subscribe(BusEvent.GAME_OVER, self._on_game_over)
        self.event_bus.subscribe(BusEvent.NIGHT_SUBSTEP, self._on_night_substep)

    # ── Persistence helpers ────────────────────────────────────────

    def _load_persisted_games(self) -> None:
        """Reconstruct lightweight GameState for every completed game on disk."""
        entries = self._manifest.load_or_rebuild(registry=builtin_registry.freeze())
        for game_id, meta in entries.items():
            # Skip games still running (engine will re-register them)
            if meta.get("phase") == "game_over" or meta.get("winner"):
                state = self._reconstruct_state(game_id, meta)
                if state is not None:
                    self._games[game_id] = state
        logger.info(f"Restored {len(self._games)} completed games from disk")

    def _load_native_games(self) -> None:
        if self.repository is None:
            return
        for record in self.repository.list_games():
            game_id = str(record["game_id"])
            try:
                checkpoint = self.repository.load_checkpoint(game_id)
            except Exception:
                logger.exception("Cannot read checkpoint for game %s", game_id)
                if record["execution_status"] in {"paused", "interrupted", "failed"}:
                    self._mark_recovery_blocked(game_id, "checkpoint_corrupt")
                continue
            if checkpoint is None:
                continue
            try:
                state, _ = self._checkpoint_codec.decode(checkpoint["checkpoint"])
            except CheckpointError as error:
                # The codec only checks the document's internal consistency, so
                # this is genuine corruption — a checkpoint written under an
                # older role registry decodes and stays resumable.
                logger.exception("Cannot decode checkpoint for game %s", game_id)
                if record["execution_status"] in {"paused", "interrupted", "failed"}:
                    self._mark_recovery_blocked(
                        game_id, _decode_failure_code(str(error)),
                    )
                continue
            except Exception:
                logger.exception("Cannot decode checkpoint for game %s", game_id)
                if record["execution_status"] in {"paused", "interrupted", "failed"}:
                    self._mark_recovery_blocked(game_id, "checkpoint_corrupt")
                continue
            self._games[state.game_id] = state
            snapshot = record.get("model_snapshot")
            self._model_snapshots[state.game_id] = (
                list(snapshot) if isinstance(snapshot, list) else []
            )
            self._durable_contexts[state.game_id] = {
                "storage_revision": int(checkpoint["storage_revision"]),
                "execution_generation": int(record["execution_generation"]),
            }

    def get_execution_info(self, game_id: str) -> dict[str, object]:
        if self.repository is None:
            state = self._games.get(game_id)
            if state is None:
                raise KeyError(game_id)
            completed = state.phase is GamePhase.GAME_OVER
            return {
                "execution_status": "completed" if completed else "running",
                "recoverable": False, "recovery_block_code": "legacy_archive",
                "interruption_count": 0, "benchmark_run_id": None,
                "pause_code": None, "pause_reason": None, "pause_detail": None,
            }
        record = self.repository.get_game(game_id)
        if record is None:
            raise KeyError(game_id)
        status = str(record["execution_status"])
        block = record["recovery_block_code"]
        pause_code = record.get("pause_code")
        return {
            "execution_status": status,
            "recoverable": status in _RECOVERABLE_STATUSES,
            "recovery_block_code": block,
            "interruption_count": int(record["interruption_count"]),
            "benchmark_run_id": record["benchmark_run_id"],
            "pause_code": pause_code if isinstance(pause_code, str) else None,
            "pause_reason": self._pause_reason(
                pause_code if isinstance(pause_code, str) else None,
            ),
            "pause_detail": record.get("pause_detail") or None,
        }

    def _note_recovery_warning(self, game_id: str, code: str) -> None:
        """Record a drift the resumed game continues through.

        Buffered until the engine exists so it can be written to the game's own
        log next to the recovery verdict, and never as a public event: the
        audience timeline must not gain a line for a backend version detail.
        """
        logger.warning("Recovery drift for game %s: %s", game_id, code)
        self._recovery_warnings.setdefault(game_id, []).append(code)

    def _write_recovery_audit(
        self, engine: GameEngine, state: GameState,
        verdict: recovery_compat.RecoveryAssessment, *, force: bool,
        from_status: str, execution_generation: int,
    ) -> None:
        warnings = tuple(self._recovery_warnings.pop(state.game_id, ()))
        phase = state.phase.value if hasattr(state.phase, "value") else str(state.phase)
        engine.game_logger.log_operation(
            state.game_id, "recovery", state.round_number, phase,
            data={
                "level": verdict.level,
                "code": verdict.code,
                "reasons": list(verdict.reasons),
                "forced": bool(force),
                "warnings": list(warnings),
                "from_status": from_status,
                "execution_generation": execution_generation,
                "frozen_registry_digest": state.registry_digest,
                "live_registry_digest": self._registry_snapshot.digest,
            },
        )

    def _adopt_recovered_resources(
        self, state: GameState, verdict: recovery_compat.RecoveryAssessment,
    ) -> None:
        """Re-point the resource setup marker at the code now running.

        Only reached once a resume has been accepted. This is a metadata write,
        never a resource effect: the seats keep the values they already have, so
        a spent potion stays spent and a declaration that grew does not hand out
        the difference. Without it the next scheduling point would reject the
        game with ``role resource configuration changed`` — the game would look
        resumable and then die on its first night.

        When the game never set up resources (no seated role declared any) there
        is nothing to preserve, so the marker is left alone and the next
        scheduling point runs the setup under the current declaration.
        """
        identity = state.registry_digest
        if not identity or verdict.level != recovery_compat.DRIFT:
            return
        marker = resource_declaration_marker(
            state, self._registry_snapshot.specs, identity,
        )
        runtime = getattr(state, "_pipeline_runtime", None)
        stored = getattr(runtime, "resource_setup_digest", None)
        if marker is None or stored is None or stored == marker:
            return
        adopt_resource_declaration(state, marker)

    def _mark_recovery_blocked(self, game_id: str, code: str) -> None:
        if self.repository is None:
            raise ValueError(code)
        record = self.repository.get_game(game_id)
        if record is None:
            raise KeyError(game_id)
        current = str(record["execution_status"])
        if current != "recovery_blocked":
            self.repository.transition_execution(
                game_id,
                expected=("paused", "interrupted", "failed"),
                target="recovery_blocked",
                recovery_block_code=code,
            )

    def _clock_running_increment_ms(
        self, values: dict[str, int | float | None], *, running: bool,
    ) -> int:
        started = values.get("running_since")
        if running and isinstance(started, (int, float)):
            return max(0, int((time.monotonic() - float(started)) * 1000))
        return 0

    def live_active_elapsed_ms(self, game_id: str) -> int | None:
        """Persisted active time plus the in-memory running interval."""
        values = self._clock_state.get(game_id)
        if values is None:
            if self.repository is None:
                return None
            clock = self.repository.get_runtime_clock(game_id) or {}
            elapsed = clock.get("active_elapsed_ms")
            return elapsed if type(elapsed) is int else None
        persisted = values.get("active_elapsed_ms")
        base = persisted if type(persisted) is int else 0
        return base + self._clock_running_increment_ms(values, running=True)

    def arm_benchmark_timeout(self, game_id: str, timeout_seconds: int) -> None:
        """Remember the benchmark wall-clock budget for heartbeat enforcement."""
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            return
        values = self._clock_state.setdefault(game_id, {
            "active_elapsed_ms": 0,
            "remaining_window_ms": None,
            "running_since": None,
        })
        values["timeout_ms"] = timeout_seconds * 1000

    def _persist_runtime_clock(self, game_id: str, *, running: bool) -> None:
        if self.repository is None:
            return
        context = self._durable_contexts.get(game_id)
        if context is None:
            return
        values = self._clock_state.setdefault(game_id, {
            "active_elapsed_ms": 0,
            "running_since": time.monotonic() if running else None,
            "remaining_window_ms": None,
        })
        increment = self._clock_running_increment_ms(values, running=running)
        if increment:
            values["active_elapsed_ms"] = int(values["active_elapsed_ms"] or 0) + increment
            values["running_since"] = time.monotonic()
        self.repository.save_runtime_clock(
            game_id,
            active_elapsed_ms=int(values["active_elapsed_ms"] or 0),
            remaining_window_ms=(
                int(values["remaining_window_ms"])
                if isinstance(values["remaining_window_ms"], int) else None
            ),
            execution_generation=context["execution_generation"],
        )

    def _start_clock_heartbeat(self, game_id: str) -> None:
        existing = self._clock_tasks.get(game_id)
        if existing is not None and not existing.done():
            return

        async def heartbeat() -> None:
            try:
                while True:
                    await asyncio.sleep(1)
                    record = None if self.repository is None else self.repository.get_game(game_id)
                    if record is None or record["execution_status"] not in {"running", "paused"}:
                        return
                    if record["execution_status"] == "running":
                        self._persist_runtime_clock(game_id, running=True)
                        timeout_ms = self._clock_state.get(game_id, {}).get("timeout_ms")
                        elapsed = self.live_active_elapsed_ms(game_id)
                        if (
                            type(timeout_ms) is int
                            and type(elapsed) is int
                            and elapsed >= timeout_ms
                        ):
                            await self.cancel_benchmark_game(
                                game_id,
                                recovery_block_code="benchmark_game_timeout",
                            )
                            return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime clock heartbeat failed for game=%s", game_id)

        self._clock_tasks[game_id] = asyncio.create_task(heartbeat())

    async def pause_game(self, game_id: str) -> dict[str, object]:
        return await self._pause_game(game_id, allow_benchmark=False)

    async def pause_benchmark_game(self, game_id: str) -> dict[str, object]:
        return await self._pause_game(game_id, allow_benchmark=True)

    async def _pause_game(
        self, game_id: str, *, allow_benchmark: bool,
    ) -> dict[str, object]:
        if self.repository is None:
            engine = self._engines.get(game_id)
            if engine is None:
                raise KeyError(game_id)
            engine.pause()
            return self.get_execution_info(game_id)
        record = self.repository.get_game(game_id)
        if record is None:
            raise KeyError(game_id)
        if record["benchmark_run_id"] is not None and not allow_benchmark:
            raise ValueError("game_managed_by_benchmark")
        status = str(record["execution_status"])
        if status == "paused":
            return self.get_execution_info(game_id)
        if status != "running":
            raise InvalidExecutionTransition(
                f"cannot transition game from {status} to paused"
            )
        engine = self._engines.get(game_id)
        if engine is None:
            raise InvalidExecutionTransition("running game has no active runner")
        engine.pause()
        try:
            self._persist_runtime_clock(game_id, running=True)
            self._clock_state.setdefault(game_id, {})["running_since"] = None
            self.repository.transition_execution(
                game_id, expected=("running",), target="paused",
            )
        except BaseException:
            engine.resume()
            raise
        return self.get_execution_info(game_id)

    async def resume_game(self, game_id: str, *, force: bool = False) -> dict[str, object]:
        return await self._control_guarded(
            game_id,
            lambda: self._resume_game(game_id, allow_benchmark=False, force=force),
        )

    async def resume_benchmark_game(self, game_id: str, *, force: bool = False) -> dict[str, object]:
        return await self._control_guarded(
            game_id,
            lambda: self._resume_game(game_id, allow_benchmark=True, force=force),
        )

    async def _control_guarded(self, game_id: str, work):
        """Serialize every control path that can start an engine for one game.

        Two overlapping requests used to pass the status gate together, both
        decode the checkpoint and both register an engine: the first task became
        an orphan that kept calling models and writing checkpoints, and the
        shared durable context let a stale writer win the revision CAS.

        The ``locked()`` probe is the authoritative concurrency gate: nothing
        awaits between it and the acquire, so no other coroutine can slip past
        it. A request that arrives after the holder finished is not concurrent —
        it reads the fresh status under the lock and reports it.
        """
        lock = self._control_locks.setdefault(game_id, asyncio.Lock())
        if lock.locked():
            raise InvalidExecutionTransition("game control is already in progress")
        async with lock:
            return await work()

    async def _resume_game(
        self, game_id: str, *, allow_benchmark: bool, force: bool = False,
    ) -> dict[str, object]:
        if self.repository is None:
            engine = self._engines.get(game_id)
            if engine is None:
                raise KeyError(game_id)
            engine.resume()
            return self.get_execution_info(game_id)
        record = self.repository.get_game(game_id)
        if record is None:
            raise KeyError(game_id)
        if record["benchmark_run_id"] is not None and not allow_benchmark:
            raise ValueError("game_managed_by_benchmark")
        status = str(record["execution_status"])
        if status == "running":
            return self.get_execution_info(game_id)
        task = self._tasks.get(game_id)
        engine = self._engines.get(game_id)
        if status == "paused" and engine is not None and task is not None and not task.done():
            self.repository.transition_execution(
                game_id, expected=("paused",), target="running",
            )
            self._clock_state.setdefault(game_id, {
                "active_elapsed_ms": 0, "remaining_window_ms": None,
            })["running_since"] = time.monotonic()
            guard = self._health_guards.get(game_id)
            if guard is not None:
                # A manual resume is the operator's assertion that the provider
                # was fixed; clear the samples so the first post-fix call is not
                # judged by pre-fix failures.
                guard.reset()
            engine.resume()
            self._broadcast_resume(game_id)
            self._start_clock_heartbeat(game_id)
            return self.get_execution_info(game_id)
        if status == "paused":
            return await self._recover_game(
                game_id, expected_statuses=("paused",),
                allow_benchmark=allow_benchmark, force=force,
            )
        raise InvalidExecutionTransition(
            f"cannot transition game from {status} to running"
        )

    async def recover_game(
        self, game_id: str, *,
        expected_statuses: tuple[str, ...] = ("interrupted", "failed", "recovery_blocked"),
        force: bool = False,
    ) -> dict[str, object]:
        return await self._control_guarded(
            game_id,
            lambda: self._recover_game(
                game_id, expected_statuses=expected_statuses,
                allow_benchmark=False, force=force,
            ),
        )

    async def recover_benchmark_game(
        self, game_id: str, *,
        expected_statuses: tuple[str, ...] = ("interrupted", "failed", "recovery_blocked"),
        force: bool = False,
    ) -> dict[str, object]:
        return await self._control_guarded(
            game_id,
            lambda: self._recover_game(
                game_id, expected_statuses=expected_statuses,
                allow_benchmark=True, force=force,
            ),
        )

    async def _recover_game(
        self, game_id: str, *, expected_statuses: tuple[str, ...],
        allow_benchmark: bool, force: bool = False,
    ) -> dict[str, object]:
        if self.repository is None:
            raise ValueError("legacy_archive")
        record = self.repository.get_game(game_id)
        if record is None:
            raise KeyError(game_id)
        if record["benchmark_run_id"] is not None and not allow_benchmark:
            raise ValueError("game_managed_by_benchmark")
        status = str(record["execution_status"])
        if status == "running":
            return self.get_execution_info(game_id)
        if status not in expected_statuses:
            code = record.get("recovery_block_code")
            if status == "recovery_blocked" and isinstance(code, str):
                raise ValueError(code)
            raise InvalidExecutionTransition(
                f"cannot transition game from {status} to running"
            )
        checkpoint = await asyncio.to_thread(self.repository.load_checkpoint, game_id)
        if checkpoint is None:
            self._mark_recovery_blocked(game_id, "checkpoint_missing")
            raise ValueError("checkpoint_missing")
        document = checkpoint["checkpoint"]
        try:
            state, orchestration = self._checkpoint_codec.decode(document)
        except CheckpointError as error:
            code = _decode_failure_code(str(error))
            self._mark_recovery_blocked(game_id, code)
            raise ValueError(code) from error

        # Judge the archive against the running code once, here: the checkpoint
        # codec deliberately only checks the document's internal consistency, so
        # a role-declaration change reaches this point instead of being refused
        # as "corrupt". Incompatible games stay refused; drift is resumable but
        # asks for confirmation, because the game continues under new rules.
        verdict = recovery_compat.assess(document, state, self._registry_snapshot)
        if not verdict.resumable:
            self._mark_recovery_blocked(game_id, str(verdict.code))
            raise ValueError(str(verdict.code))
        if verdict.needs_confirmation and not force:
            raise ValueError(str(verdict.code))
        self._adopt_recovered_resources(state, verdict)

        try:
            engine, clients = self._build_recovered_engine(
                state, orchestration, record,
            )
        except ValueError as error:
            code = str(error)
            if code not in _RECOVERY_FAILURE_CODES:
                code = "checkpoint_corrupt"
            self._mark_recovery_blocked(game_id, code)
            raise ValueError(code) from error

        updated = self.repository.transition_execution(
            game_id, expected=expected_statuses, target="running",
            increment_generation=True,
        )
        self._broadcast_resume(game_id)
        self._write_recovery_audit(
            engine, state, verdict, force=force, from_status=status,
            execution_generation=int(updated["execution_generation"]),
        )
        context = self._durable_contexts.setdefault(game_id, {})
        context["storage_revision"] = int(checkpoint["storage_revision"])
        context["execution_generation"] = int(updated["execution_generation"])
        snapshot = record.get("model_snapshot")
        model_snapshot = list(snapshot) if isinstance(snapshot, list) else []
        engine._checkpoint_hook = self._checkpoint_hook(engine, model_snapshot)
        self._games[game_id] = state
        self._engines[game_id] = engine
        self._model_snapshots[game_id] = model_snapshot
        self._llm_clients[game_id] = clients
        clock = self.repository.get_runtime_clock(game_id) or {}
        self._clock_state[game_id] = {
            "active_elapsed_ms": int(clock.get("active_elapsed_ms", 0)),
            "remaining_window_ms": clock.get("remaining_window_ms"),
            "running_since": time.monotonic(),
        }
        task = asyncio.create_task(engine.restore(
            state, orchestration, self._checkpoint_codec,
        ))
        self._tasks[game_id] = task
        self._attach_task_watcher(game_id, engine, task)
        self._start_clock_heartbeat(game_id)
        return self.get_execution_info(game_id)

    def _reconstruct_state(
        self, game_id: str, meta: dict,
    ) -> Optional[GameState]:
        """Build a minimal GameState from manifest metadata + game.log."""
        log_path = os.path.join(self.data_dir, "games", game_id, "game.log")
        if not os.path.exists(log_path):
            return None

        config = GameConfig(**meta.get("config", {})) if meta.get("config") else GameConfig()

        state = GameState(
            game_id=game_id,
            phase=GamePhase(meta.get("phase", "game_over")),
            round_number=meta.get("round_number", 0),
            config=config,
        )
        # The manifest records the winning camp but never the reason: that only
        # exists in the log's `game_over` record, which is read below. Leaving the
        # reason absent (rather than an empty string) keeps "unknown" out of the
        # public vocabulary -- a value the response model would reject, turning
        # the detail endpoint into HTTP 500.
        state.win_result = {
            "winning_camp": meta["winner"],
            "reason": None,
        } if meta.get("winner") else None

        # Reconstruct players + events from log
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return state

        seen_seats: set[int] = set()

        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            data = rec.get("data") or {}
            op = rec.get("operation")
            seat = rec.get("seat")

            # ── Discover players ──────────────────────────────
            if op == "role_init":
                for seat_str, pdata in (data.get("players") or {}).items():
                    s = int(seat_str)
                    seen_seats.add(s)
                    state.players[s] = PlayerState(
                        seat_number=s,
                        role=pdata.get("role", ""),
                        camp=pdata.get("camp", ""),
                        is_alive=pdata.get("is_alive", True),
                    )

            # Fallback: extract seats from werewolf votes or any seat-bearing event
            if not state.players:
                if op == "werewolf_kill":
                    for v in data.get("votes") or []:
                        s = v.get("player_seat")
                        if s and s not in seen_seats:
                            seen_seats.add(s)
                            state.players[s] = PlayerState(
                                seat_number=s, role="?", camp="?",
                            )
                if isinstance(seat, int) and seat > 0 and seat not in seen_seats:
                    seen_seats.add(seat)
                    state.players[seat] = PlayerState(
                        seat_number=seat, role="?", camp="?",
                    )

            # ── Apply deaths ──────────────────────────────────
            if op == "night_deaths":
                for d in (data.get("deaths") or []):
                    ds = d.get("player_seat")
                    if ds and ds in state.players:
                        state.players[ds].mark_dead()

            elif op == "vote_result":
                exiled = data.get("exiled")
                if exiled and exiled in state.players:
                    state.players[exiled].mark_dead()

            elif op == "game_over":
                # `reason` is optional in the public contract: an archive whose
                # log predates the field (or lost it) reports the camp only.
                state.win_result = {
                    "winning_camp": data.get("winner"),
                    "reason": data.get("reason") or None,
                }

        # If manifest or config says N players but we found fewer, fill in placeholder seats
        total = meta.get("player_count", 0) or config.total_players
        if total > 0 and len(state.players) < total:
            for s in range(1, total + 1):
                if s not in state.players:
                    state.players[s] = PlayerState(
                        seat_number=s, role="?", camp="?",
                    )

        return state

    def _persist_game(self, state: GameState) -> None:
        """Write current game metadata to the manifest."""
        runtime = getattr(state, "_pipeline_runtime", None)
        state_revision = (
            state.state_revision if runtime is None else runtime.revision
        )
        self._manifest.update_game(
            state.game_id,
            phase=state.phase.value,
            round_number=state.round_number,
            alive_count=len(state.alive_players()),
            pipeline_version=state.pipeline_version or None,
            registry_digest=state.registry_digest or None,
            spec_versions=dict(state.spec_versions) if state.spec_versions else None,
            effect_schema_version=state.effect_schema_version or None,
            state_revision=state_revision,
            last_consistent_checkpoint=state.last_consistent_checkpoint,
        )

    async def create_game(
        self,
        num_werewolves: Optional[int] = None,
        num_villagers: Optional[int] = None,
        num_seers: Optional[int] = None,
        num_witches: Optional[int] = None,
        num_hunters: Optional[int] = None,
        role_counts: Optional[dict[str, int]] = None,
        reveal_on_death: bool = False,
        enable_sheriff: bool = False,
        witch_self_save: str = DEFAULT_WITCH_SELF_SAVE,
        victory: str = DEFAULT_VICTORY,
        model_assignments: Optional[list[dict]] = None,
        *,
        commentary_enabled: bool = False,
        commentary_config_id: str | None = None,
        game_id_override: str | None = None,
        source: str = "native",
        benchmark_run_id: str | None = None,
        benchmark_item_index: int | None = None,
        model_seat_assignments: Mapping[int, str | None] | None = None,
        role_by_seat: Mapping[int, str] | None = None,
        runtime_seed: int | None = None,
    ) -> str:
        if benchmark_run_id is None and benchmark_item_index is not None:
            raise ValueError("benchmark_item_index requires benchmark_run_id")
        if benchmark_run_id is not None and source != "benchmark":
            raise ValueError("benchmark games must use benchmark source")
        game_id = game_id_override or (
            str(uuid.uuid4()) if self.repository is not None
            else str(uuid.uuid4())[:8]
        )
        config = GameConfig(
            role_counts=role_counts,
            num_werewolves=num_werewolves,
            num_villagers=num_villagers,
            num_seers=num_seers,
            num_witches=num_witches,
            num_hunters=num_hunters,
            reveal_on_death=reveal_on_death,
            enable_sheriff=enable_sheriff,
            witch_self_save=witch_self_save,
            victory=victory,
        )
        if runtime_seed is not None and (type(runtime_seed) is not int or runtime_seed < 0):
            raise ValueError("runtime seed must be a non-negative integer")
        if role_by_seat is not None:
            builtin_registry.validate_role_assignments(
                config.role_counts, config.total_players, role_by_seat,
            )
            role_by_seat = dict(role_by_seat)
        runtime_rng = random.Random(runtime_seed) if runtime_seed is not None else None

        # Validate and decrypt every assignment before creating any runtime or
        # persistence side effect. Model placement uses a separate shuffle from
        # role placement and is frozen for the lifetime of this game.
        assignment_shuffle = None
        if model_seat_assignments is not None:
            expected_seats = set(range(1, config.total_players + 1))
            if set(model_seat_assignments) != expected_seats:
                raise ValueError("model seat assignments must cover every seat")
            ordered_ids: list[str | None] = []
            for seat in sorted(model_seat_assignments):
                config_id = model_seat_assignments[seat]
                if config_id is not None and not isinstance(config_id, str):
                    raise ValueError("model seat config_id must be a string or null")
                if config_id not in ordered_ids:
                    ordered_ids.append(config_id)
            model_assignments = [
                {
                    "config_id": config_id,
                    "count": sum(
                        assigned == config_id
                        for assigned in model_seat_assignments.values()
                    ),
                }
                for config_id in ordered_ids
            ]
            positions = {config_id: index for index, config_id in enumerate(ordered_ids)}
            desired = [
                positions[model_seat_assignments[seat]]
                for seat in range(1, config.total_players + 1)
            ]

            def assignment_shuffle(values: list[int]) -> None:
                values[:] = desired

        if assignment_shuffle is None:
            seat_configs, model_snapshot = resolve_model_assignments(
                model_assignments, config.total_players,
            )
        else:
            seat_configs, model_snapshot = resolve_model_assignments(
                model_assignments, config.total_players,
                shuffle=assignment_shuffle,
            )
        frozen_model_runtime = _freeze_model_runtime(
            seat_configs, model_snapshot,
        )
        if commentary_config_id is not None:
            env_cfg = replace(env_default_client_config(), provider_profile="auto")
            _saved_client_config(commentary_config_id, env_cfg)
        commentary_meta = {
            "enabled": bool(commentary_enabled),
            "commentary_config_id": commentary_config_id,
        }
        clients_by_seat: dict[int, LLMClient] = {}
        self._llm_clients[game_id] = clients_by_seat
        try:
            for seat, client_config in sorted(seat_configs.items()):
                # Seats share LangChain's httpx pool otherwise: closing them
                # at game end would kill a sibling game's live connections.
                clients_by_seat[seat] = LLMClient(
                    config=client_config, dedicated_http_clients=True,
                )
        except BaseException:
            await self._close_game_clients(game_id)
            raise

        def client_provider(seat: int) -> LLMClient:
            try:
                return clients_by_seat[seat]
            except KeyError:
                raise ValueError(f"unknown model seat: {seat}") from None

        health_guard = self._make_health_guard(
            game_id, clients_by_seat, benchmark=benchmark_run_id is not None,
        )
        try:
            prompt_builder = PromptBuilder()
            role_options = {}
            if role_by_seat is not None or runtime_rng is not None:
                role_options = {"role_by_seat": role_by_seat, "rng": runtime_rng}
            roles = self._create_roles(config, prompt_builder, client_provider, **role_options)
            self._instrument_roles(game_id, roles)

            # Build the registry-driven pipeline scheduler for night actions and
            # day death reactions. Every call is routed through the actor's
            # stable, seat-owned client.
            snapshot = builtin_registry.freeze()
            renderer = PromptRenderer()
            engine_ref = None

            def _night_invoke(messages, tool_name, schema, seat):
                llm_client = client_provider(seat)
                prompt_chars = sum(
                    len(str(message.get("content") or "")) for message in messages
                )
                meta_state = engine_ref.state if engine_ref is not None else None
                call_kind, phase = _night_call_meta(meta_state, tool_name)
                try:
                    result = None
                    if self._model_invocations is None:
                        result = _night_turn(
                            llm_client, messages, tool_name=tool_name, schema=schema,
                        )
                        payload = result.payload
                    else:
                        engine_state = engine_ref.state if engine_ref is not None else None
                        round_number = 0 if engine_state is None else engine_state.round_number
                        frozen_request = {
                            "kind": "night_json", "round_number": round_number,
                            "tool_name": tool_name, "seat": seat,
                            "messages": messages, "schema": schema,
                        }
                        digest = self._model_invocations.request_digest(frozen_request)
                        generation = self._durable_contexts[game_id]["execution_generation"]

                        def provider_call():
                            turn = _night_turn(
                                llm_client, messages,
                                tool_name=tool_name, schema=schema,
                            )
                            nonlocal result
                            result = turn
                            return turn.payload, _usage_mapping(turn.usage)

                        durable = self._model_invocations.invoke_sync(
                            game_id=game_id,
                            request_id=f"night:{round_number}:{tool_name}:{seat}:{digest[:16]}",
                            actor_seat=seat,
                            action_position=f"round:{round_number}:night:{tool_name}:seat:{seat}",
                            frozen_request=frozen_request,
                            provider_profile=self._client_identity(llm_client)[0],
                            model_id=self._client_identity(llm_client)[1],
                            execution_generation=generation,
                            provider_call=provider_call, recovery=generation > 1,
                        )
                        self._wait_model_consumption_allowed_sync(game_id)
                        payload = durable.normalized_result
                        capture = self._prompt_capture(game_id)
                        if capture is not None and not durable.reused:
                            # The night flow already hands over OpenAI-shaped
                            # messages; the sink normalises and keeps them as-is.
                            sink = PromptSink()
                            sink.record(messages, tool_schema=schema, tool_name=tool_name)
                            capture.flush(build_prompt_record(
                                request_id=(
                                    f"night:{round_number}:{tool_name}:{seat}:{digest[:16]}"
                                ),
                                call_kind=call_kind, round_number=round_number,
                                phase=phase, seat=seat, contract_id=tool_name,
                                transport="night_json", attempt=1,
                                model_id=self._client_identity(llm_client)[1],
                                # The only digest that covers the messages, which
                                # is what makes this record independently verifiable.
                                request_digest=digest, sink=sink,
                            ))
                    # Unreachable by construction: the non-durable branch takes a
                    # payload from ``_night_turn``, which raises on anything that
                    # is not a mapping, and the durable branch gets one from
                    # ``invoke_sync``, whose ``_normalize_result`` enforces the
                    # same. Kept as a trust-boundary guard across that call.
                    if not isinstance(payload, Mapping):  # pragma: no cover
                        raise ValueError("model gateway response is not a JSON object")
                except Exception as error:
                    if engine_ref is not None:
                        schedule_point = (
                            "night_wolf_vote"
                            if tool_name == "werewolf_kill"
                            else "night_wolf_discussion"
                        )
                        try:
                            engine_ref.game_logger.log_model_error(
                                game_id,
                                engine_ref.state.round_number,
                                phase,
                                seat,
                                contract_id=tool_name,
                                schedule_point=schedule_point,
                                attempt=0,
                                **_model_error_diagnostics(error, llm_client),
                            )
                        except Exception:
                            logger.exception(
                                "Failed to persist wolf model error for game=%s seat=%s",
                                game_id, seat,
                            )
                        _log_llm_telemetry(
                            engine_ref.game_logger, game_id,
                            engine_ref.state.round_number, seat,
                            tool_name=tool_name,
                            model_id=getattr(llm_client, "model_name", None),
                            prompt_chars=prompt_chars,
                            parse_result="error",
                            failure_code=normalize_failure_code(error),
                            call_kind=call_kind, phase=phase,
                        )
                    raise
                if engine_ref is not None:
                    _log_llm_telemetry(
                        engine_ref.game_logger, game_id,
                        engine_ref.state.round_number, seat,
                        tool_name=tool_name,
                        model_id=getattr(llm_client, "model_name", None),
                        prompt_chars=prompt_chars,
                        elapsed_ms=(result.elapsed_ms if result is not None else 0),
                        usage=(result.usage if result is not None else None),
                        attempts=(result.attempts if result is not None else 1),
                        parse_result="ok",
                        call_kind=call_kind, phase=phase,
                    )
                return json.dumps(dict(payload), ensure_ascii=False)

            director = NightDirector(snapshot, _night_invoke)

            scheduler = Scheduler(
                snapshot,
                ContextProjector(),
                ActionValidator(),
                ActionResolver(),
                EffectApplier(),
                self._command_provider(snapshot, renderer, client_provider, director),
            )

            # Create engine
            engine = GameEngine(
                game_id=game_id,
                config=config,
                event_bus=self.event_bus,
                roles=roles,
                memory_service=self.memory_service,
                data_dir=self.data_dir,
                pipeline_scheduler=scheduler,
                director=director,
                health_guard=health_guard,
            )
            self._attach_health_sink(game_id, health_guard, engine)
            engine_ref = engine
            if runtime_rng is not None:
                engine._rng = runtime_rng
            for role in roles.values():
                role._rng = engine._rng
            # Stamp the pipeline snapshot version so archives can be validated
            # and migrated against the exact registry that ran the game.
            engine.state.pipeline_version = "v2"
            engine.state.registry_digest = snapshot.digest
            engine.state.spec_versions = {
                role_id: spec.schema_version for role_id, spec in snapshot.specs.items()
            }
            engine.state.effect_schema_version = 1

            self._engines[game_id] = engine
            self._games[game_id] = engine.state
            self._model_snapshots[game_id] = model_snapshot

            # Persist before launching the engine, then duplicate the immutable
            # assignment in game.log so index reconstruction can recover it.
            self._manifest.add_game(
                game_id,
                {
                    "role_counts": dict(config.role_counts),
                    "reveal_on_death": config.reveal_on_death,
                    "enable_sheriff": config.enable_sheriff,
                    "witch_self_save": config.witch_self_save,
                    "victory": config.victory,
                },
                model_snapshot=model_snapshot,
                model_snapshot_version=2,
                name=default_game_name(config.total_players),
            )
            engine.game_logger.log_operation(
                game_id,
                "model_assignment",
                0,
                "waiting",
                data={
                    "model_snapshot_version": 2,
                    "model_snapshot": model_snapshot,
                },
            )

            if self.repository is not None:
                self.repository.create_game(
                    game_id=game_id,
                    name=default_game_name(config.total_players),
                    config={
                        "role_counts": dict(config.role_counts),
                        "reveal_on_death": config.reveal_on_death,
                        "enable_sheriff": config.enable_sheriff,
                        "witch_self_save": config.witch_self_save,
                        "victory": config.victory,
                        "commentary": commentary_meta,
                        "model_runtime_version": 1,
                        "model_runtime": frozen_model_runtime,
                        "prompt_digest": _prompt_digest(),
                        "engine_recovery_version": 1,
                    },
                    execution_status="running", source=source,
                    model_snapshot=model_snapshot,
                    benchmark_run_id=benchmark_run_id,
                    benchmark_item_index=benchmark_item_index,
                )
                self._durable_contexts[game_id] = {
                    "storage_revision": 0, "execution_generation": 1,
                }
                self._clock_state[game_id] = {
                    "active_elapsed_ms": 0,
                    "remaining_window_ms": None,
                    "running_since": time.monotonic(),
                }
                self.repository.save_runtime_clock(
                    game_id, active_elapsed_ms=0, remaining_window_ms=None,
                    execution_generation=1,
                )
                engine._checkpoint_hook = self._checkpoint_hook(
                    engine, model_snapshot,
                )

            # Resolve the training-corpus switch once, for this new game only.
            # Recovery paths never latch, so they cannot start capturing mid-way.
            self._latch_capture_request(game_id)
            task = asyncio.create_task(engine.start())
            self._tasks[game_id] = task
            if self.repository is not None:
                self._start_clock_heartbeat(game_id)
        except BaseException:
            self._tasks.pop(game_id, None)
            self._health_guards.pop(game_id, None)
            self._engines.pop(game_id, None)
            self._games.pop(game_id, None)
            self._model_snapshots.pop(game_id, None)
            self._close_prompt_capture(game_id)
            await self._close_game_clients(game_id)
            try:
                self._manifest.remove_game(game_id)
            except Exception:
                logger.debug("Failed to remove partial game manifest", exc_info=True)
            game_dir = os.path.join(self.data_dir, "games", game_id)
            if os.path.isdir(game_dir):
                try:
                    shutil.rmtree(game_dir)
                except OSError:
                    logger.debug("Failed to remove partial game directory", exc_info=True)
            raise

        def _watch_engine(done: asyncio.Task) -> None:
            """Surface engine crashes instead of silently freezing the game."""
            clock_task = self._clock_tasks.pop(game_id, None)
            if clock_task is not None and not clock_task.done():
                clock_task.cancel()
            # Release the capture descriptor here too: a long-lived process would
            # otherwise hold one open fd per game it has ever run.
            self._close_prompt_capture(game_id)
            asyncio.create_task(self._close_game_clients(game_id))
            if done.cancelled():
                return
            exc = done.exception()
            if exc is None:
                current_state = self._games.get(game_id)
                if (
                    self.repository is not None
                    and current_state is not None
                    and current_state.phase is GamePhase.GAME_OVER
                ):
                    try:
                        self.repository.transition_execution(
                            game_id, expected=("running", "paused"),
                            target="completed",
                        )
                    except (KeyError, InvalidExecutionTransition):
                        logger.debug("Game completion status was already finalized", exc_info=True)
                return
            logger.error(
                "Game engine crashed for %s",
                game_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            state = self._games.get(game_id)
            if state is None:
                return
            state.phase = GamePhase.ERROR
            if self.repository is not None:
                try:
                    self.repository.transition_execution(
                        game_id, expected=("running", "paused"), target="failed",
                    )
                except (KeyError, InvalidExecutionTransition):
                    logger.debug("Game failure status was already finalized", exc_info=True)
            self._manifest.update_game(game_id, phase=GamePhase.ERROR.value)
            engine.game_logger.log_phase_change(
                game_id, GamePhase.ERROR.value, state.round_number,
            )

            async def _broadcast_error() -> None:
                await self.event_bus.publish(
                    BusEvent.PHASE_CHANGED,
                    game_id=game_id,
                    phase=GamePhase.ERROR.value,
                    round_number=state.round_number,
                    state=state,
                )

            asyncio.create_task(_broadcast_error())

        task.add_done_callback(_watch_engine)

        logger.info(f"Game created: {game_id}, {config.total_players} players")
        return game_id

    def _checkpoint_hook(self, engine: GameEngine, model_snapshot: list[dict]):
        async def commit(step_key: str) -> None:
            coordinator = self._coordinator
            if coordinator is None:
                return
            context = self._durable_contexts[engine.game_id]
            domain_events = self._checkpoint_domain_events(
                engine, step_key,
            )
            resolved_requests = [
                str(item["request_id"])
                for item in self.repository.list_model_requests(engine.game_id)
                if item.get("status") == "resolved"
            ]
            receipt = await coordinator.acommit(
                state=engine.state,
                orchestration=engine.export_orchestration(
                    self._checkpoint_codec,
                    model_assignments=model_snapshot,
                ),
                expected_storage_revision=context["storage_revision"],
                execution_generation=context["execution_generation"],
                step_key=step_key,
                input_facts={"step_key": step_key},
                result_facts={
                    "phase": engine.state.phase.value,
                    "round_number": engine.state.round_number,
                    "public_state_digest": engine._public_state_digest(engine.state),
                },
                domain_events=domain_events,
                consumed_model_request_ids=resolved_requests,
                derived_jobs=(
                    ({"job_key": f"summary:{engine.game_id}", "job_type": "summary"},)
                    if engine.state.phase is GamePhase.GAME_OVER else ()
                ),
            )
            context["storage_revision"] = receipt.storage_revision
        return commit

    @staticmethod
    def _checkpoint_domain_events(
        engine: GameEngine, step_key: str,
    ) -> list[dict[str, object]]:
        event_id = hashlib.sha256(
            f"{engine.game_id}\0{step_key}".encode("utf-8")
        ).hexdigest()
        label = step_key.split(":", 1)[-1]
        if label.startswith("night_point:"):
            pending = engine._pending_night_batch
            if pending is not None and pending.raw_results:
                events = []
                for index, event in enumerate(pending.raw_results[-1].events):
                    row = _plain_json(event)
                    # Night deaths are announced once through the dedicated
                    # ``night_death:{round}:{seat}`` checkpoints, so the
                    # settlement commit must not project them a second time.
                    if row.get("event_type") == "PLAYER_DIED":
                        continue
                    payload = row.get("payload")
                    if isinstance(payload, dict):
                        payload.setdefault("round_number", engine.state.round_number)
                    row["event_id"] = f"domain:{event_id}:{index}"
                    row["schema_version"] = 1
                    events.append(row)
                if events:
                    return events
        if label.startswith("day_point:"):
            events = _pending_window_rows(engine, event_id)
            if events:
                return events
        if label.startswith("night_assist:"):
            events = _pending_window_rows(engine, event_id)
            if events:
                return events
        payload: dict[str, object]
        event_type: str
        if label == "game_initialized":
            # The opening transitions (role_deal → night) are broadcast without a
            # checkpoint of their own — roles are not dealt yet when they happen —
            # so this step is the first durable beat of the game and carries the
            # phase the table is actually in. Without it the spectator stream had
            # no phase row at all until the first dawn, and the first night was
            # labelled "waiting" for its whole length.
            return [{
                "event_id": f"domain:{event_id}:0",
                "event_type": "GAME_INITIALIZED",
                "payload": {
                    "players": engine.state.get_public_state()["players"],
                    "config": {
                        "role_counts": dict(engine.state.config.role_counts),
                        "reveal_on_death": engine.state.config.reveal_on_death,
                        "enable_sheriff": engine.state.config.enable_sheriff,
                        "witch_self_save": engine.state.config.witch_self_save,
                        "victory": engine.state.config.victory,
                    },
                },
                "visibility": ["PUBLIC"],
                "schema_version": 1,
            }, {
                "event_id": f"domain:{event_id}:1",
                "event_type": "PHASE_CHANGED",
                "payload": {
                    "phase": engine.state.phase.value,
                    "round_number": engine.state.round_number,
                },
                "visibility": ["PUBLIC"],
                "schema_version": 1,
            }]
        elif (
            label.startswith("phase:")
            and engine.state.phase is GamePhase.GAME_OVER
            and isinstance(engine.state.win_result, Mapping)
        ):
            event_type = "GAME_OVER"
            payload = dict(engine.state.win_result)
        elif label.startswith("phase:"):
            event_type = "PHASE_CHANGED"
            payload = {
                "phase": engine.state.phase.value,
                "round_number": engine.state.round_number,
            }
        elif label.startswith("vote_result:"):
            parts = label.split(":")
            exiled_seat = None if parts[-1] == "none" else int(parts[-1])
            counts: dict[str, int] = {}
            weighted: dict[str, int] = {}
            # The engine freezes the sheriff's 1.5-vote basis on the ballot
            # window; ask it for the weight so the published tally is the one
            # that decided the exile, not one re-read after the sheriff died.
            # Non-engine callers (projection unit tests) keep the legacy read.
            weight_for = getattr(engine, "vote_ballot_weight", None)
            for vote in engine.state.votes:
                if vote.target_seat is not None:
                    key = str(vote.target_seat)
                    counts[key] = counts.get(key, 0) + 1
                    # The exile is decided by ``ballot_weight`` — one unit per
                    # ballot, or 3 for the sheriff against 2 for everyone else
                    # once a sheriff sits — so a head count can disagree with
                    # the outcome it is published next to. Both numbers travel:
                    # how many hands went up, and what the decision was made of.
                    weight = (
                        weight_for(vote.voter_seat)
                        if callable(weight_for)
                        else ballot_weight(engine.state, vote.voter_seat)
                    )
                    weighted[key] = weighted.get(key, 0) + weight
            event_type = "VOTE_RESULT"
            payload = {
                "round_number": engine.state.round_number,
                "exiled_seat": exiled_seat,
                "counts": dict(sorted(counts.items(), key=lambda item: int(item[0]))),
                "weighted_counts": dict(
                    sorted(weighted.items(), key=lambda item: int(item[0]))
                ),
            }
        elif label.startswith("speaking:"):
            parts = label.split(":")
            seat_token = parts[2] if len(parts) > 2 else ""
            if seat_token.isdigit() and int(seat_token) > 0:
                event_type = "SPEAKING"
                payload = {
                    "round_number": engine.state.round_number,
                    "seat": int(seat_token),
                }
            else:
                event_type = "STEP_COMMITTED"
                payload = {"position": label}
        elif label.startswith("speech:") or label.startswith("last_words:") or label.startswith("sheriff_campaign:") or label.startswith("sheriff_pk:"):
            event_type = "SPEECH_MADE"
            speech = engine.state.speeches[-1] if engine.state.speeches else None
            payload = {} if speech is None else dict(speech.to_dict())
            if speech is not None and (
                label.startswith("sheriff_campaign:") or label.startswith("sheriff_pk:")
            ):
                payload["phase"] = "sheriff_election"
        elif label.startswith("sheriff_run"):
            parts = label.split(":")
            event_type = "SHERIFF_RUN"
            payload = {
                "round_number": engine.state.round_number,
                "seat": int(parts[2]) if len(parts) > 2 else 0,
                "choice": parts[3] if len(parts) > 3 else "pass",
            }
        elif label.startswith("sheriff_withdraw"):
            parts = label.split(":")
            event_type = "SHERIFF_WITHDRAW"
            payload = {
                "round_number": engine.state.round_number,
                "seat": int(parts[2]) if len(parts) > 2 else 0,
                "choice": parts[3] if len(parts) > 3 else "stay",
            }
        elif label.startswith("sheriff_vote_technical_abstain:"):
            # ``...:<round>:<step>:<seat>:<failure_code>``. The matching
            # ``SHERIFF_VOTE`` step still carries ``target_seat=None``; this
            # sibling is what tells the god view the empty ballot was cast by
            # the engine, not a voluntary pass. Reuse the existing
            # ``technical_abstain`` audience event so no DTO/frontend changes.
            parts = label.split(":")
            event_type = "TECHNICAL_ABSTAIN"
            payload = {
                "round_number": engine.state.round_number,
                "voter_seat": int(parts[-2]),
                "failure_code": parts[-1],
            }
        elif label.startswith("sheriff_vote"):
            parts = label.split(":")
            raw_target = parts[4] if len(parts) > 4 else "none"
            event_type = "SHERIFF_VOTE"
            payload = {
                "round_number": engine.state.round_number,
                "voter_seat": int(parts[3]) if len(parts) > 3 else 0,
                "target_seat": None if raw_target == "none" else int(raw_target),
                "kind": parts[2] if len(parts) > 2 else "vote",
            }
        elif label.startswith("sheriff_side"):
            parts = label.split(":")
            event_type = "SHERIFF_SIDE"
            payload = {
                "round_number": engine.state.round_number,
                "seat": int(parts[2]) if len(parts) > 2 else 0,
                "side": parts[3] if len(parts) > 3 else "",
            }
        elif label.startswith("sheriff_elected:"):
            parts = label.split(":")
            raw_seat = parts[2] if len(parts) > 2 else "none"
            event_type = "SHERIFF_ELECTED"
            payload = {
                "round_number": engine.state.round_number,
                "seat": None if raw_seat == "none" else int(raw_seat),
                "reason": parts[3] if len(parts) > 3 else "done",
            }
        elif label.startswith("sheriff_badge:"):
            parts = label.split(":")
            to_raw = parts[-1] if parts else "tear"
            event_type = "SHERIFF_BADGE"
            payload = {
                "round_number": engine.state.round_number,
                "from_seat": int(parts[2]) if len(parts) > 2 else 0,
                "to_seat": None if to_raw == "tear" else int(to_raw),
            }
            # The reason is not in the label: like a narration's text it lives in
            # the engine's one-shot slot, written just before this very step.
            badge_reason = getattr(engine, "_pending_badge_reasoning", None)
            if isinstance(badge_reason, str) and badge_reason:
                payload["reasoning"] = badge_reason
        elif label.startswith("sheriff_explode:"):
            seat = int(label.split(":")[-1])
            deaths = [item for item in engine.state.death_history if item.player_seat == seat]
            death_payload = deaths[-1].to_dict() if deaths else {}
            return [
                {
                    "event_id": f"domain:{event_id}:0",
                    "event_type": "PLAYER_DIED",
                    "payload": death_payload,
                    "visibility": ["PUBLIC"],
                    "schema_version": 1,
                },
                {
                    "event_id": f"domain:{event_id}:1",
                    "event_type": "SHERIFF_ELECTED",
                    "payload": {
                        "round_number": engine.state.round_number,
                        "seat": None,
                        "reason": "explode",
                    },
                    "visibility": ["PUBLIC"],
                    "schema_version": 1,
                },
            ]
        elif label.startswith("vote_received:"):
            event_type = "VOTE_CAST"
            parts = label.split(":")
            seat = int(parts[-1])
            votes = [item for item in engine.state.votes if item.voter_seat == seat]
            payload = ({
                **votes[-1].to_dict(), "round_number": engine.state.round_number,
            } if votes else {})
        elif label.startswith("vote_technical_abstain:"):
            # ``<label>:<round>:<vote_round>:<seat>:<failure_code>``: the ballot
            # the system cast for a seat whose model failed. The recorded vote
            # carries no target, so this step is the only place the god view can
            # learn the seat was not silent by choice.
            parts = label.split(":")
            event_type = "TECHNICAL_ABSTAIN"
            payload = {
                "round_number": engine.state.round_number,
                "voter_seat": int(parts[-2]),
                "failure_code": parts[-1],
            }
        elif label.startswith("narration:"):
            # The narration text lives only in the engine's pending slot: it is
            # written immediately before this step, exactly like the daytime
            # window batch.
            pending = getattr(engine, "_pending_narration", None)
            event_type = "NARRATION"
            payload = dict(pending) if isinstance(pending, Mapping) else {}
        elif label.startswith("night_death:"):
            event_type = "PLAYER_DIED"
            seat = int(label.split(":")[-1])
            deaths = [item for item in engine.state.death_history if item.player_seat == seat]
            payload = deaths[-1].to_dict() if deaths else {}
        elif label.startswith("delayed_death:"):
            event_type = "PLAYER_DIED"
            seat = int(label.split(":")[-1])
            deaths = [
                item for item in engine.state.death_history
                if item.player_seat == seat
                and item.round_number == engine.state.round_number
            ]
            payload = deaths[-1].to_dict() if deaths else {}
        elif label.startswith("exile_reaction:"):
            # ``<label>:<round>:<exiled seat>:<seat-seat...>`` names every seat
            # this window killed: the exiled seat itself plus whoever the
            # response window settled (a hunter's victim, a charmed victim).
            # Reading the seats off the label keeps the announcement
            # cause-agnostic instead of whitelisting causes.
            seats = [int(part) for part in label.split(":")[-1].split("-") if part]
            deaths = [
                item for item in engine.state.death_history
                if item.round_number == engine.state.round_number
                and item.player_seat in seats
            ]
            # The response window settles its damage through the applier, so the
            # step also carries whatever marks the settlement left on a spared
            # seat (a delayable role keeps the poison mark for the next day).
            events = _pending_window_rows(engine, event_id)
            events.extend({
                "event_id": f"domain:{event_id}:{len(events) + index}",
                "event_type": "PLAYER_DIED",
                "payload": death.to_dict(),
                "visibility": ["PUBLIC"],
                "schema_version": 1,
            } for index, death in enumerate(deaths))
            if events:
                return events
            event_type = "STEP_COMMITTED"
            payload = {"position": label}
        elif label.startswith("night_reaction:"):
            # ``<label>:<round>:<seat-seat...>`` names the seats the night's
            # response window settled once the deaths had been read out (a shot
            # fired by a seat the night killed, and whoever it took with it).
            # The window runs after the announcement, so this step is where its
            # public events and its deaths reach the audience.
            seats = [int(part) for part in label.split(":")[-1].split("-") if part]
            deaths = [
                item for item in engine.state.death_history
                if item.round_number == engine.state.round_number
                and item.player_seat in seats
            ]
            events = _pending_window_rows(engine, event_id)
            events.extend({
                "event_id": f"domain:{event_id}:{len(events) + index}",
                "event_type": "PLAYER_DIED",
                "payload": death.to_dict(),
                "visibility": ["PUBLIC"],
                "schema_version": 1,
            } for index, death in enumerate(deaths))
            if events:
                return events
            event_type = "STEP_COMMITTED"
            payload = {"position": label}
        elif label.startswith("day_interrupted:") or label.startswith("day_reaction:"):
            # ``<label>:<round>:<vote_round>:<actor>:<seat-seat...>`` names the
            # seats settled by a daytime interruption (and its reaction window).
            seats = [int(part) for part in label.split(":")[-1].split("-") if part]
            deaths = [
                item for item in engine.state.death_history
                if item.round_number == engine.state.round_number
                and item.player_seat in seats
            ]
            events = _pending_window_rows(engine, event_id)
            events.extend({
                "event_id": f"domain:{event_id}:{len(events) + index}",
                "event_type": "PLAYER_DIED",
                "payload": death.to_dict(),
                "visibility": ["PUBLIC"],
                "schema_version": 1,
            } for index, death in enumerate(deaths))
            if events:
                return events
            event_type = "STEP_COMMITTED"
            payload = {"position": label}
        elif label.startswith("exile_cancelled:"):
            seat = int(label.split(":")[-1])
            player = engine.state.players.get(seat)
            events = [{
                "event_id": f"domain:{event_id}:0",
                "event_type": "EXILE_CANCELLED",
                "payload": {"target_seat": seat, "round_number": engine.state.round_number},
                "visibility": ["PUBLIC"],
                "schema_version": 1,
            }]
            if player is not None and player.revealed_role is not None:
                events.append({
                    "event_id": f"domain:{event_id}:1",
                    "event_type": "PLAYER_REVEALED",
                    "payload": {
                        "seat_number": seat, "role": player.revealed_role,
                        "camp": _camp_value(player.camp),
                    },
                    "visibility": ["PUBLIC"],
                    "schema_version": 1,
                })
            return events
        elif (
            label.startswith("night_complete:")
            and isinstance(engine.state.win_result, Mapping)
        ):
            event_type = "GAME_OVER"
            payload = dict(engine.state.win_result)
        elif label.startswith("night_complete:"):
            # Every night after the first ends through this step, and so does the
            # first night's fork into the sheriff election — the phase transition
            # itself is broadcast on the bus, but only a step label reaches the
            # spectator stream. Without this row the audience never saw a `dawn`
            # marker (the death announcement stayed labelled "night") nor the
            # `sheriff_election` one.
            event_type = "PHASE_CHANGED"
            payload = {
                "phase": engine.state.phase.value,
                "round_number": engine.state.round_number,
            }
        elif label.startswith("wolf_discussion:"):
            pending = getattr(engine, "_pending_night_batch", None)
            history = getattr(pending, "discussion_history", ()) if pending is not None else ()
            seat = int(label.split(":")[2])
            prefix = f"{seat}号："
            last = history[-1] if history else ""
            if last.startswith(prefix) and not last.endswith("（跳过）"):
                text = last[len(prefix):]
                # The engine appends the turn's next-day plan to the line it
                # keeps in the channel; the god view gets it as its own field
                # instead of a parenthesis glued to the chat text.
                day_plan = ""
                marker = text.rfind("（次日计划：")
                if marker >= 0 and text.endswith("）"):
                    day_plan = text[marker + len("（次日计划："):-1]
                    text = text[:marker]
                event_type = "WOLF_CHAT_MESSAGE"
                payload = {
                    "seat": seat, "text": text,
                    "round_number": engine.state.round_number,
                }
                if day_plan:
                    payload["day_plan"] = day_plan
            else:
                event_type = "STEP_COMMITTED"
                payload = {"position": label}
        elif label.startswith("wolf_vote:"):
            pending = getattr(engine, "_pending_night_batch", None)
            votes = getattr(pending, "wolf_votes", ()) if pending is not None else ()
            seat = int(label.split(":")[2])
            match = [item for item in votes if item.seat == seat]
            if match:
                vote = match[-1]
                event_type = "WOLF_VOTE"
                payload = {
                    "seat": vote.seat, "target_seat": vote.target_seat,
                    "reasoning": vote.reasoning,
                    "round_number": engine.state.round_number,
                    # 引擎代投的狼票必须自报家门，否则实时上帝视角把它读成
                    # 这匹狼自己弃票（game.log 的回放路径已经带了同一个键）。
                    # 只在为真时写：旧检查点没有这个键，投影器按过滤集处理。
                    **({"degraded": True} if vote.degraded else {}),
                }
            else:
                event_type = "STEP_COMMITTED"
                payload = {"position": label}
        else:
            event_type = "STEP_COMMITTED"
            payload = {"position": label}
        return [{
            "event_id": f"domain:{event_id}", "event_type": event_type,
            "payload": payload, "visibility": ["PUBLIC"] if event_type != "STEP_COMMITTED" else [],
            "schema_version": 1,
        }]

    def _resolve_recovery_model_configs(
        self, state: GameState, record: Mapping[str, object],
    ) -> dict[int, LLMClientConfig]:
        config_doc = record.get("config")
        if not isinstance(config_doc, Mapping):
            raise ValueError("model_config_missing")
        if config_doc.get("engine_recovery_version") != 1:
            raise ValueError("checkpoint_version_unsupported")
        # Prompt and parameter drift are recorded, not enforced: refusing them
        # made a resumed game impossible after any prompt wording change or a
        # temperature tweak, and neither can corrupt a committed state. What the
        # game already committed stays authoritative; the rest of the game runs
        # under the current prompt and model settings.
        if config_doc.get("prompt_digest") != _prompt_digest():
            self._note_recovery_warning(state.game_id, "prompt_drift")
        runtime = config_doc.get("model_runtime")
        if config_doc.get("model_runtime_version") != 1 or not isinstance(runtime, list):
            raise ValueError("model_config_missing")

        env_config = replace(env_default_client_config(), provider_profile="auto")
        result: dict[int, LLMClientConfig] = {}
        for entry in runtime:
            if not isinstance(entry, Mapping):
                raise ValueError("model_config_missing")
            config_id = entry.get("config_id")
            seats = entry.get("seats")
            parameters = entry.get("parameters")
            if (
                config_id is not None and type(config_id) is not str
            ) or not isinstance(seats, list) or not seats or not isinstance(parameters, Mapping):
                raise ValueError("model_config_missing")
            if entry.get("parameters_digest") != _parameter_digest(parameters):
                raise ValueError("checkpoint_corrupt")
            if config_id is None:
                current = env_config
            else:
                try:
                    current, _ = _saved_client_config(config_id, env_config)
                except ValueError as error:
                    message = str(error)
                    if "unknown" in message:
                        raise ValueError("model_config_missing") from error
                    raise ValueError("model_key_unavailable") from error
            current_parameters = _model_parameters(current)
            if current_parameters != dict(parameters):
                # The game keeps the model parameters it was created with (only
                # ``api_key`` and ``headers`` are read live, because they cannot
                # be stored); the difference is recorded, not enforced.
                self._note_recovery_warning(state.game_id, "model_drift")
            try:
                frozen = LLMClientConfig(
                    api_key=current.api_key,
                    base_url=str(parameters["base_url"]),
                    model_id=str(parameters["model_id"]),
                    temperature=float(parameters["temperature"]),
                    max_tokens=int(parameters["max_tokens"]),
                    strict_base_url=str(parameters["strict_base_url"]),
                    action_max_tokens=int(parameters["action_max_tokens"]),
                    action_timeout_seconds=float(parameters["action_timeout_seconds"]),
                    action_retry_timeout_seconds=float(parameters["action_retry_timeout_seconds"]),
                    action_final_retry_timeout_seconds=float(parameters["action_final_retry_timeout_seconds"]),
                    provider_profile=str(parameters["provider_profile"]),
                    # Taken from the live config, exactly like ``api_key``:
                    # headers may carry relay credentials, so they stay out of
                    # the checkpoint and its digest, and a resumed game picks
                    # up whatever the model config currently holds.
                    headers=current.headers,
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("checkpoint_corrupt") from error
            for seat in seats:
                if type(seat) is not int or seat not in state.players or seat in result:
                    raise ValueError("checkpoint_corrupt")
                result[seat] = frozen
        if set(result) != set(state.players):
            raise ValueError("model_config_missing")
        return result

    @staticmethod
    def _client_identity(client: object) -> tuple[str, str]:
        profile = getattr(getattr(client, "provider_profile", None), "profile_id", None)
        model = getattr(client, "model_name", None)
        return (
            profile if isinstance(profile, str) and profile else "unknown",
            model if isinstance(model, str) and model else "unknown",
        )

    def _make_health_guard(
        self, game_id: str, clients: Mapping[int, object], *, benchmark: bool,
    ) -> ModelHealthGuard:
        """Build the per-game guard from the frozen per-seat clients.

        A benchmark game uses ``fail`` instead of ``pause``: the benchmark
        executor auto-resumes paused games, so a provider outage expressed as a
        pause would loop until the game timeout. A live game pauses and lets an
        operator fix the provider, then resume from the same checkpoint.
        """
        policy = (
            self._abort_policy.for_benchmark() if benchmark else self._abort_policy
        )
        seat_keys = {
            seat: getattr(client, "provider_key", f"unknown|seat:{seat}")
            for seat, client in clients.items()
        }
        guard = ModelHealthGuard(
            policy=policy, seat_keys=seat_keys, mode=policy.mode,
        )
        self._health_guards[game_id] = guard
        return guard

    def _attach_health_sink(
        self, game_id: str, guard: ModelHealthGuard, engine: GameEngine,
    ) -> None:
        """Write provider state transitions to the game log (god view only)."""
        def sink(event: HealthEvent) -> None:
            try:
                phase = getattr(engine.state.phase, "value", engine.state.phase)
                engine.game_logger.log_operation(
                    game_id, "provider_health", engine.state.round_number,
                    phase if isinstance(phase, str) else "unknown",
                    data=event.detail(),
                )
            except Exception:
                logger.debug("provider_health audit write failed", exc_info=True)
        guard.set_event_sink(sink)

    @staticmethod
    def _pause_reason(code: str | None) -> str | None:
        if code is None:
            return None
        return {
            CIRCUIT_OPEN_CODE: "模型通道连续失败，已自动暂停",
            "one_shot_budget_exhausted": "一次性技能模型预算耗尽，已暂停",
            "manual": "手动暂停",
        }.get(code, f"已暂停（{code}）")

    def _broadcast_pause(
        self, game_id: str, code: str | None, detail: Mapping[str, object] | None,
    ) -> None:
        """Best-effort live hint; the durable row is the source of truth."""
        try:
            asyncio.create_task(self.ws_manager.broadcast(
                game_id, "paused_state", paused=True, code=code,
                reason=self._pause_reason(code),
            ))
        except Exception:
            logger.debug("paused_state broadcast failed", exc_info=True)

    def _broadcast_resume(self, game_id: str) -> None:
        try:
            asyncio.create_task(self.ws_manager.broadcast(
                game_id, "paused_state", paused=False,
            ))
        except Exception:
            logger.debug("paused_state resume broadcast failed", exc_info=True)

    async def _wait_model_consumption_allowed(self, game_id: str) -> None:
        if self.repository is None:
            return
        while True:
            if self._closed.is_set():
                raise ModelRequestUnavailable("service is shutting down")
            record = self.repository.get_game(game_id)
            if record is None:
                raise ModelRequestUnavailable("game was deleted before result consumption")
            status = record["execution_status"]
            if status == "running":
                return
            if status != "paused":
                raise ModelRequestUnavailable(
                    f"game in {status} state cannot consume model results"
                )
            await asyncio.sleep(0.05)

    def _wait_model_consumption_allowed_sync(self, game_id: str) -> None:
        if self.repository is None:
            return
        while True:
            if self._closed.is_set():
                raise ModelRequestUnavailable("service is shutting down")
            record = self.repository.get_game(game_id)
            if record is None:
                raise ModelRequestUnavailable("game was deleted before result consumption")
            status = record["execution_status"]
            if status == "running":
                return
            if status != "paused":
                raise ModelRequestUnavailable(
                    f"game in {status} state cannot consume model results"
                )
            time.sleep(0.05)

    def _prompt_capture_setting(self) -> object:
        """Stored operator override for prompt capture, or ``None``.

        Read when a game is created, never on the recovery paths — see
        :meth:`_latch_capture_request`. The environment switch remains the
        fallback whenever no interface setting exists.
        """
        return get_app_settings_store().get_prompt_capture_enabled()

    def _latch_capture_request(self, game_id: str) -> None:
        """Resolve the operator switch for a brand-new game and latch it.

        Only the creation path calls this. A recovered game keeps whatever its
        marker says, so flipping the switch cannot make an interrupted game
        start capturing half-way through.
        """
        enabled, _source = resolve_capture_setting(self._prompt_capture_setting())
        with self._prompt_captures_lock:
            self._capture_requests[game_id] = enabled

    def _prompt_capture(self, game_id: str) -> PromptCapture | None:
        """Return this game's capture handle, deciding the switch once per game.

        Capture latches at game creation: a game that started with capture on
        keeps capturing across a resume even if the switch is later flipped off,
        and a game that started with it off never starts mid-way. The latched
        request plus the on-disk marker are the two halves of that decision.
        """
        # The whole check-then-create runs under the lock: two threads racing here
        # would otherwise each open their own descriptor on the same file.
        with self._prompt_captures_lock:
            if game_id in self._prompt_captures:
                return self._prompt_captures[game_id]
            requested = self._capture_requests.get(game_id, False)
            enabled = capture_enabled_for_game(
                requested, prompt_capture_marker_exists(self.data_dir, game_id),
            )
            capture = PromptCapture(self.data_dir, game_id) if enabled else None
            self._prompt_captures[game_id] = capture
            return capture

    def _close_prompt_capture(self, game_id: str) -> None:
        """Release this game's capture descriptor. Idempotent."""
        with self._prompt_captures_lock:
            capture = self._prompt_captures.pop(game_id, None)
            # The latch is runtime-only too: once the handle is gone, the on-disk
            # marker is the only thing that may still turn capture on.
            self._capture_requests.pop(game_id, None)
        if capture is not None:
            capture.close()

    def _instrument_roles(self, game_id: str, roles: Mapping[int, object]) -> None:
        invocations = self._model_invocations
        if invocations is None:
            return
        for seat, role in roles.items():
            original_speak = getattr(role, "speak")
            original_request_action = getattr(role, "request_action")
            # Adapt once per role: the capture sink is optional for callables that
            # predate it, and probing the signature per call would be wasteful.
            call_speak = _capture_adapter(original_speak)
            call_request_action = _capture_adapter(original_request_action)

            async def durable_speak(
                state, conversation_log, context, *, on_chunk=None,
                _seat=seat, _role=role, _call=call_speak,
            ):
                position = f"round:{state.round_number}:speech:{context}:seat:{_seat}"
                request_id = f"speech:{state.round_number}:{context}:{_seat}"
                profile, model = self._client_identity(getattr(_role, "llm_client", None))
                generation = self._durable_contexts[game_id]["execution_generation"]
                capture = self._prompt_capture(game_id)
                sink = None if capture is None else PromptSink()

                async def provider_call():
                    # ``on_chunk`` must survive the wrapper: dropping it made the
                    # engine's TypeError fallback fire on every speech, so the live
                    # typewriter stream never ran in production. A cache hit does
                    # not replay chunks (no provider call happens at all).
                    value = await _call(state, conversation_log, context, sink, on_chunk)
                    return (
                        {"text": value, "is_none": value is None},
                        # The role's own ladder accumulates its tokens; without
                        # this every speech request was stored with NULL usage
                        # (the report's completeness rate sat at 42%).
                        _usage_mapping(getattr(_role, "_last_call_usage", None)),
                    )

                result = await invocations.invoke(
                    game_id=game_id, request_id=request_id, actor_seat=_seat,
                    action_position=position,
                    frozen_request={
                        "kind": "speech", "round_number": state.round_number,
                        "phase": state.phase.value, "context": context,
                        "seat": _seat,
                    },
                    provider_profile=profile, model_id=model,
                    execution_generation=generation,
                    provider_call=provider_call, recovery=generation > 1,
                )
                await self._wait_model_consumption_allowed(game_id)
                if capture is not None and not result.reused:
                    capture.flush(build_prompt_record(
                        request_id=request_id, call_kind="speech",
                        round_number=state.round_number, phase=state.phase.value,
                        seat=_seat, contract_id=context, transport="speech_tool",
                        attempt=sink.attempt_count, model_id=model,
                        request_digest=None, sink=sink,
                    ))
                return None if result.normalized_result.get("is_none") is True else result.normalized_result.get("text")

            async def durable_request_action(
                state, conversation_log, request, *,
                _seat=seat, _role=role, _call=call_request_action,
            ):
                profile, model = self._client_identity(getattr(_role, "llm_client", None))
                generation = self._durable_contexts[game_id]["execution_generation"]
                capture = self._prompt_capture(game_id)
                sink = None if capture is None else PromptSink()

                async def provider_call():
                    accepted = await _call(
                        state, conversation_log, request, sink,
                    )
                    return ({
                        "command": accepted.command.model_dump(),
                        "technical_failure_code": accepted.technical_failure_code,
                        "timeout_type": accepted.timeout_type,
                    }, _usage_mapping(getattr(_role, "_last_call_usage", None)))

                result = await invocations.invoke(
                    game_id=game_id,
                    request_id=f"action:{request.idempotency_key}",
                    actor_seat=_seat,
                    action_position=request.idempotency_key,
                    frozen_request={
                        "kind": "contract_action",
                        "role_id": request.role_id,
                        "contract_id": request.contract.contract_id,
                        "round_number": request.round_id,
                        "phase": request.phase.value,
                        "seat": _seat,
                    },
                    provider_profile=profile, model_id=model,
                    execution_generation=generation,
                    provider_call=provider_call, recovery=generation > 1,
                )
                await self._wait_model_consumption_allowed(game_id)
                if capture is not None and not result.reused:
                    capture.flush(build_prompt_record(
                        request_id=f"action:{request.idempotency_key}",
                        call_kind="action",
                        round_number=request.round_id, phase=request.phase.value,
                        seat=_seat, contract_id=request.contract.contract_id,
                        # The transport is chosen inside the role; a non-null
                        # tool_name in the record is what marks a native-tool run.
                        transport=None, attempt=sink.attempt_count,
                        model_id=model, request_digest=None, sink=sink,
                    ))
                row = result.normalized_result
                command = row.get("command")
                if not isinstance(command, Mapping):
                    raise ModelRequestUnavailable("cached action has no command")
                return AcceptedAction(
                    request=request,
                    command=ContractActionCommand.model_validate(dict(command)),
                    technical_failure_code=(
                        row.get("technical_failure_code")
                        if isinstance(row.get("technical_failure_code"), str) else None
                    ),
                    timeout_type=(
                        row.get("timeout_type")
                        if isinstance(row.get("timeout_type"), str) else None
                    ),
                )

            setattr(role, "speak", durable_speak)
            setattr(role, "request_action", durable_request_action)

    def _build_recovered_engine(
        self, state: GameState, orchestration: Mapping[str, object],
        record: Mapping[str, object],
    ) -> tuple[GameEngine, dict[int, LLMClient]]:
        # Registry compatibility is settled once in ``_recover_game`` (see
        # recovery_compat); a role-declaration change must not stop the resume
        # here, because the engine binds the live specs for the remaining game.
        seat_configs = self._resolve_recovery_model_configs(state, record)
        clients: dict[int, LLMClient] = {}
        try:
            for seat, client_config in sorted(seat_configs.items()):
                clients[seat] = LLMClient(
                    config=client_config, dedicated_http_clients=True,
                )
        except BaseException:
            # LLMClient construction is synchronous, so close any partially
            # created transports on the caller's event loop before surfacing.
            for client in clients.values():
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            raise ValueError("model_key_unavailable")

        def client_provider(seat: int) -> LLMClient:
            try:
                return clients[seat]
            except KeyError:
                raise ValueError("checkpoint_corrupt") from None

        health_guard = self._make_health_guard(
            state.game_id, clients,
            benchmark=record.get("benchmark_run_id") is not None,
        )
        prompt_builder = PromptBuilder()
        roles = {}
        for seat, player in sorted(state.players.items()):
            try:
                spec = builtin_registry.require(player.role)
                roles[seat] = spec.role_factory(
                    seat, player.role, prompt_builder, client_provider(seat),
                )
            except ValueError as error:
                if str(error) == "checkpoint_corrupt":
                    raise
                # A backstop: recovery_compat already refuses a game whose role
                # the registry lost, so this only fires if it is bypassed.
                raise ValueError("registry_incompatible") from error
            except KeyError as error:
                raise ValueError("registry_incompatible") from error
        self._instrument_roles(state.game_id, roles)

        snapshot = self._registry_snapshot
        engine_box: dict[str, GameEngine] = {}

        def night_invoke(messages, tool_name, schema, seat):
            client = client_provider(seat)
            call_kind, phase = _night_call_meta(state, tool_name)
            frozen_request = {
                "kind": "night_json", "round_number": state.round_number,
                "tool_name": tool_name, "seat": seat,
                "messages": messages, "schema": schema,
            }
            digest = self._model_invocations.request_digest(frozen_request)
            generation = self._durable_contexts[state.game_id]["execution_generation"]

            turn_box: dict[str, _NightTurn] = {}

            def provider_call():
                turn = _night_turn(
                    client, messages, tool_name=tool_name, schema=schema,
                )
                turn_box["turn"] = turn
                return turn.payload, _usage_mapping(turn.usage)

            durable = self._model_invocations.invoke_sync(
                game_id=state.game_id,
                request_id=(
                    f"night:{state.round_number}:{tool_name}:{seat}:{digest[:16]}"
                ),
                actor_seat=seat,
                action_position=(
                    f"round:{state.round_number}:night:{tool_name}:seat:{seat}"
                ),
                frozen_request=frozen_request,
                provider_profile=self._client_identity(client)[0],
                model_id=self._client_identity(client)[1],
                execution_generation=generation,
                provider_call=provider_call, recovery=True,
            )
            self._wait_model_consumption_allowed_sync(state.game_id)
            payload = durable.normalized_result
            capture = self._prompt_capture(state.game_id)
            if capture is not None and not durable.reused:
                # Mirrors the create path: the same record shape, so a resumed
                # night decision is indistinguishable from a fresh one.
                sink = PromptSink()
                sink.record(messages, tool_schema=schema, tool_name=tool_name)
                capture.flush(build_prompt_record(
                    request_id=(
                        f"night:{state.round_number}:{tool_name}:{seat}:{digest[:16]}"
                    ),
                    call_kind=call_kind, round_number=state.round_number,
                    phase=phase, seat=seat, contract_id=tool_name,
                    transport="night_json", attempt=1,
                    model_id=self._client_identity(client)[1],
                    request_digest=digest, sink=sink,
                ))
            if not isinstance(payload, Mapping):
                raise ValueError("model gateway response is not a JSON object")
            engine = engine_box.get("engine")
            if engine is not None:
                turn = turn_box.get("turn")
                _log_llm_telemetry(
                    engine.game_logger, state.game_id, state.round_number, seat,
                    tool_name=tool_name,
                    model_id=getattr(client, "model_name", None),
                    prompt_chars=sum(
                        len(str(item.get("content") or "")) for item in messages
                    ),
                    elapsed_ms=(turn.elapsed_ms if turn is not None else 0),
                    usage=(turn.usage if turn is not None else None),
                    attempts=(turn.attempts if turn is not None else 1),
                    parse_result="ok",
                    call_kind=call_kind, phase=phase,
                )
            return json.dumps(dict(payload), ensure_ascii=False)

        director = NightDirector(snapshot, night_invoke)
        renderer = PromptRenderer()
        scheduler = Scheduler(
            snapshot, ContextProjector(), ActionValidator(), ActionResolver(),
            EffectApplier(),
            self._command_provider(snapshot, renderer, client_provider, director),
        )
        engine = GameEngine(
            game_id=state.game_id, config=state.config,
            event_bus=self.event_bus, roles=roles,
            memory_service=self.memory_service, data_dir=self.data_dir,
            pipeline_scheduler=scheduler, director=director,
            health_guard=health_guard,
        )
        self._attach_health_sink(state.game_id, health_guard, engine)
        engine_box["engine"] = engine
        for role in roles.values():
            role._rng = engine._rng
        # Validate orchestration before the execution status changes. restore()
        # performs the same load once more inside its owned runner task.
        engine.load_restored_state(state, orchestration, self._checkpoint_codec)
        return engine, clients

    def _attach_task_watcher(
        self, game_id: str, engine: GameEngine, task: asyncio.Task,
    ) -> None:
        def watch(done: asyncio.Task) -> None:
            clock = self._clock_tasks.pop(game_id, None)
            if clock is not None and not clock.done():
                clock.cancel()
            asyncio.create_task(self._close_game_clients(game_id))
            if done.cancelled():
                return
            error = done.exception()
            state = self._games.get(game_id)
            if error is None:
                self._health_guards.pop(game_id, None)
                if (
                    self.repository is not None and state is not None
                    and state.phase is GamePhase.GAME_OVER
                ):
                    try:
                        self.repository.transition_execution(
                            game_id, expected=("running", "paused"), target="completed",
                        )
                    except (KeyError, InvalidExecutionTransition):
                        logger.debug("Recovered game was already finalized", exc_info=True)
                return
            if isinstance(error, ProviderOutageFailed):
                # Benchmark games fail-fast: the executor would auto-resume a
                # pause and loop on the same dead provider. The phase is left
                # untouched — this is an operational outage, not rule corruption.
                logger.error(
                    "Game failed on a provider outage: %s (%s)", game_id, error.code,
                )
                self._health_guards.pop(game_id, None)
                if self.repository is not None:
                    try:
                        self.repository.transition_execution(
                            game_id, expected=("running", "paused"), target="failed",
                            recovery_block_code=error.code,
                        )
                    except (KeyError, InvalidExecutionTransition):
                        logger.debug("Provider-outage failure already finalized", exc_info=True)
                return
            if isinstance(error, DecisionPaused):
                # Recoverable pause: a one-shot contract ran out of model budget,
                # or the provider circuit opened. The point is re-run after a
                # resume, so the game keeps its phase and becomes `paused` —
                # marking it `failed` (or `ERROR`) would end the game and forfeit
                # the card/position the pause exists to protect.
                code = getattr(error, "code", "decision_budget_exhausted")
                detail = dict(getattr(error, "detail", {}) or {})
                logger.warning(
                    "Game paused: %s code=%s", game_id, code,
                )
                if self.repository is not None:
                    try:
                        self.repository.transition_execution(
                            game_id, expected=("running", "paused"), target="paused",
                            pause_code=code, pause_detail=detail,
                        )
                    except (KeyError, InvalidExecutionTransition):
                        logger.debug("Recovered game pause already finalized", exc_info=True)
                self._broadcast_pause(game_id, code, detail)
                return
            logger.error(
                "Recovered game engine crashed for %s", game_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            self._health_guards.pop(game_id, None)
            if state is not None:
                state.phase = GamePhase.ERROR
            if self.repository is not None:
                try:
                    self.repository.transition_execution(
                        game_id, expected=("running", "paused"), target="failed",
                    )
                except (KeyError, InvalidExecutionTransition):
                    logger.debug("Recovered game failure already finalized", exc_info=True)

        task.add_done_callback(watch)

    def _create_roles(
        self, config: GameConfig, prompt_builder: PromptBuilder, client_provider,
        *, role_by_seat: Mapping[int, str] | None = None,
        rng: random.Random | None = None,
    ) -> dict:
        return builtin_registry.create_roles(
            config.role_counts,
            config.total_players,
            prompt_builder,
            llm_client_factory=client_provider,
            role_by_seat=role_by_seat,
            rng=rng,
        )

    @staticmethod
    def _fallback_target(
        request, context, history: str, *, rng=None, state=None, registry=None,
    ) -> int | None:
        """Choose one contract-valid first-night fallback target, if needed.

        Each candidate is validated against the context projected for its *own*
        command, exactly as the scheduler does before an accept. A validate hook
        that reads ``selected_target`` facts (the Awakened Wolf King's claw asks
        for the teammate's ``resource_labels``) cannot answer against the
        unprojected base context: it raised ``ValueError``, the provider reported
        that as a model failure and the scheduler turned it into a fatal
        ``PipelinePaused`` — the game died on its first night.
        """
        if (
            context.round_number != 1
            or history.strip()
            or request.role_id == "wolf-killer-witch"
        ):
            return None
        alive = context.facts.get("alive_seats") if isinstance(context.facts, Mapping) else None
        if type(alive) is not tuple:
            return None
        target_actions = sorted(request.contract.actions_requiring_target)
        if not target_actions:
            return None
        action_type = target_actions[0]
        validator = ActionValidator()
        projector = (
            ContextProjector()
            if isinstance(state, GameState) and registry is not None else None
        )
        candidates = []
        for seat in alive:
            command = PipelineActionCommand(
                action_type=action_type, target_seat=seat, reasoning="server fallback",
            )
            try:
                rule_context = (
                    context if projector is None
                    else projector.project_selected_target(
                        state, request, context, command, registry,
                    )
                )
                violations = validator.validate(rule_context, request.contract, command)
            except Exception:
                # The hint is a convenience, never a rule: a candidate the engine
                # cannot decide is skipped instead of ending the game.
                logger.debug(
                    "Fallback hint candidate skipped: target=%s contract=%s",
                    seat, getattr(request.contract, "contract_id", "?"),
                    exc_info=True,
                )
                continue
            if not violations:
                candidates.append(seat)
        return (rng or random).choice(candidates) if candidates else None

    def _pause_once_or_degrade(
        self, game_id: str, point_key: str, contract: object,
    ) -> None:
        """Pause the game instead of forfeiting an exhausted one-shot contract.

        ``exhausted_bucket`` owns the rule: ``per_game_limit == 1`` contracts
        (hunter's gun, wolf king's claw, idiot's flip, crimson envoy's explode)
        are paused, everything else degrades as before. A degraded one-shot is a
        silent forfeit — the placeholder consumes the card's quota, so the seat
        never gets its shot and the game plays on as if it had declined.

        Once per point: the benchmark executor resumes a paused game
        automatically, so a permanently broken provider must not be able to pause
        the same point forever. The second exhaustion at the same point degrades.
        """
        if self.repository is None:
            return
        if exhausted_bucket(contract, call_kind=CALL_KIND_PIPELINE) is not FailureBucket.PAUSE:
            return
        key = (game_id, point_key)
        if key in self._paused_points:
            return
        self._paused_points.add(key)
        raise DecisionPaused(
            "one-shot contract exhausted its model budget at "
            f"{getattr(contract, 'contract_id', '?')}",
            code="one_shot_budget_exhausted",
            detail={
                "contract_id": getattr(contract, "contract_id", None),
                "point": point_key,
            },
        )

    def _pack_ballot_point(self, request, context) -> bool:
        """True when the pack's own kill vote answers this request.

        Gated on *pack* membership, not on the schedule point. Both the pack's
        shared kill and a hidden wolf's lone kill are scheduled at
        ``NIGHT_WOLF_VOTE``, but only the pack balloted during the discussion:
        ``NightDirector.collected_vote`` knows nothing about a ``no_wolf_pack``
        seat, so matching on the point alone handed the hidden wolf the safe
        fallback every night and its documented lone kill never reached a model.
        """
        if request.contract.schedule_point is not SchedulePoint.NIGHT_WOLF_VOTE:
            return False
        state = getattr(self._engines.get(context.game_id), "state", None)
        if not isinstance(state, GameState):
            # Nothing live to consult: keep the historical hand-off.
            return True
        return in_wolf_pack(state, request.actor_seat)

    def _command_provider(self, snapshot, renderer: PromptRenderer, client_provider, director):
        """LLM-backed command provider for the pipeline scheduler.

        Renders a prompt from the frozen registry spec, projected context and
        the game's conversation history, then parses the response into an
        ActionCommand. Parse failures degrade to the contract's safe fallback
        so a point never stalls on a malformed model response.
        """
        def provider(request, context, attempt, *, prompt_sink=None, usage_sink=None):
            if request.contract.contract_id == "awakened_witch_assist_tally":
                # The tally is a deterministic engine resolution, not a model
                # decision: the votes are already in ``assist_votes``. Returning
                # a synthetic command keeps the write entry and the point journal
                # intact without inventing a model request or a token bill.
                return PipelineActionCommand(
                    action_type="resolve", target_seat=None, reasoning="",
                )
            if self._pack_ballot_point(request, context):
                # A collected wolf ballot carries its own provenance: the real
                # votes keep ``degraded=False`` and only the engine-produced
                # placeholder below is marked, so the pack's aggregate never
                # reads a genuine kill as a system action.
                command = director.collected_vote(request.actor_seat)
                return command if command is not None else PipelineActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None,
                    reasoning=DEGRADED_REASONING,
                    degraded=True,
                )
            role_spec = snapshot.acting_spec(request.role_id, request.contract)
            engine = self._engines.get(context.game_id)
            history = ""
            if engine is not None:
                # Visibility follows the camp, not the role id: passing the id here
                # made the wolf channel empty for every wolf whose id is not the
                # string "werewolf" -- and for all of them once the entry took a camp.
                # Pack membership needs the live state; a caller that has none
                # keeps the long-standing camp-only visibility.
                state = getattr(engine, "state", None)
                records = engine.conversation_log.get_conversations_for_role(
                    request.actor_seat, role_spec.camp_id,
                    in_pack=(
                        in_wolf_pack(state, request.actor_seat)
                        if isinstance(state, GameState) else True
                    ),
                )
                history = "\n".join(
                    f"[{record.round_number}|{record.phase}|"
                    f"{record.speaker_seat if record.speaker_seat is not None else ''}] {record.content}"
                    for record in records[-120:]
                )
            fallback_target = self._fallback_target(
                request, context, history,
                rng=engine._rng if engine is not None else None,
                state=getattr(engine, "state", None),
                registry=snapshot,
            )
            if fallback_target is not None:
                context = replace(context, facts={
                    **context.facts, "RANDOM_HINT": fallback_target,
                })
            try:
                prompt = renderer.render(role_spec, request.contract, context, history)
            except Exception:
                # A render fault (overlarge prompt, malformed context) is one
                # seat's action problem, not pipeline corruption: degrade like
                # any other unusable answer instead of killing the game.
                logger.warning(
                    "Action prompt render failed for seat=%s contract=%s "
                    "point=%s; degrading to safe fallback",
                    request.actor_seat, request.contract.contract_id,
                    request.contract.schedule_point.value,
                    exc_info=True,
                )
                self._pause_once_or_degrade(
                    context.game_id, request.action_key, request.contract,
                )
                return PipelineActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None,
                    reasoning=DEGRADED_REASONING,
                    degraded=True,
                )
            messages = [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
            if attempt > 0:
                # Scheduler attempt 1 only exists after a rule rejection. Merge
                # the repair request into the last user turn; a second user
                # turn would be rejected or reordered by some gateways.
                messages = _with_repair_reminder(
                    messages, _rule_retry_hint(request.contract),
                )
            if (
                engine is not None
                and request.contract.schedule_point in _NIGHT_ACTOR_POINTS
            ):
                engine.announce_actor_blocking(request.actor_seat)
            llm_client = None
            command = None
            failure: Exception | None = None
            if prompt_sink is not None:
                # The rendered prompt already embeds OUTPUT_ACTION_COMMAND_SCHEMA,
                # so only the tool name is worth recording. One record covers the
                # whole ladder: a repair attempt only appends a reminder to the
                # same conversation.
                prompt_sink.record(
                    messages, tool_name=request.contract.resolved_tool_name,
                )
            guard = self._health_guards.get(context.game_id)
            if guard is not None:
                # Above the ``try`` on purpose: a paused provider must reach the
                # engine task, not be swallowed as a per-action fallback.
                guard.check(request.actor_seat)
            try:
                llm_client = client_provider(request.actor_seat)
            except Exception as error:
                # A client that cannot even be built has no request to re-ask.
                if guard is not None:
                    guard.record_client_failure(request.actor_seat, error)
                failure = error
            else:
                active_messages = messages
                # ``retry_plan`` owns the budget: it reports ``None`` once the
                # attempt count reaches the policy's limit, which is what ends
                # this loop. ``attempt`` is the scheduler's own 0/1 index and
                # stays the value reported in the game log, so the inner counter
                # must not shadow it.
                try_number = 0
                ladder_started = time.monotonic()
                ladder_usage: list[object] = []
                while True:
                    try_number += 1
                    try:
                        result = llm_client.invoke_action(
                            active_messages, request.contract,
                        )
                        command = _pipeline_command(result.payload, request.contract)
                        failure = None
                        ladder_usage.append(getattr(result, "usage", None))
                        if usage_sink is not None:
                            # Every rung of the ladder costs tokens; the durable
                            # hop records one usage per logical request, so they
                            # are summed here rather than dropped.
                            usage_sink.append(getattr(result, "usage", None))
                        break
                    except Exception as error:
                        failure = error
                        plan = retry_plan(_PIPELINE_RETRY_POLICY, error, try_number)
                        if plan is None:
                            break
                        # Wait first: the SDK already spent its own retries inside
                        # the call that just failed, so re-firing immediately
                        # would land in the very same outage window.
                        kind, delay = plan
                        if kind == KIND_REPAIR:
                            active_messages = _with_repair_reminder(
                                messages,
                                "\n\n【系统提示】上一次回复的内容无法解析。"
                                f"请只调用 {request.contract.resolved_tool_name} 函数，"
                                "参数严格遵守给定 schema，不要输出任何其他文字。",
                            )
                        time.sleep(delay)
            if failure is not None:
                logger.warning(
                    "LLM action command failed for seat=%s contract=%s point=%s; "
                    "degrading to safe fallback",
                    request.actor_seat, request.contract.contract_id,
                    request.contract.schedule_point.value,
                    exc_info=True,
                )
                if engine is not None:
                    try:
                        phase = getattr(context.phase, "value", context.phase)
                        engine.game_logger.log_model_error(
                            context.game_id,
                            context.round_number,
                            phase if isinstance(phase, str) else "unknown",
                            request.actor_seat,
                            contract_id=request.contract.contract_id,
                            schedule_point=request.contract.schedule_point.value,
                            attempt=attempt,
                            **_model_error_diagnostics(failure, llm_client),
                        )
                    except Exception:
                        logger.exception(
                            "Failed to persist model error for game=%s seat=%s",
                            context.game_id, request.actor_seat,
                        )
                command = None
            telemetry_logger = getattr(engine, "game_logger", None)
            if telemetry_logger is not None:
                pipeline_phase = getattr(context.phase, "value", context.phase)
                if not isinstance(pipeline_phase, str) or not pipeline_phase:
                    pipeline_phase = "night"
                # The pipeline's own calls used to leave no telemetry row at all:
                # the summary reads ``llm_calls.log`` and nothing else, so every
                # skill action (a guard, a check, a save, a duel, a verdict) was
                # missing from its call count, its token total and its failure
                # rate, even though the durable ledger had them. One row per
                # logical request, with the ladder's summed usage. A stub engine
                # without a logger simply records nothing.
                _log_llm_telemetry(
                    telemetry_logger, context.game_id, context.round_number,
                    request.actor_seat,
                    tool_name=request.contract.contract_id,
                    model_id=getattr(llm_client, "model_name", None),
                    prompt_chars=sum(
                        len(str(item.content or "")) for item in messages
                    ),
                    elapsed_ms=int((time.monotonic() - ladder_started) * 1000),
                    usage=_summed_usage(ladder_usage),
                    attempts=try_number,
                    parse_result=("ok" if failure is None else "error"),
                    failure_code=(
                        None if failure is None else _model_failure_code(failure)
                    ),
                    call_kind="action", transport="action_tool",
                    phase=pipeline_phase,
                )
            if command is None or command.action_type not in request.contract.action_types:
                # Provider failure, unusable answer, or an action type outside the
                # contract: all three are the engine deciding for the seat, so the
                # placeholder must say so instead of reading like a player's reason.
                # A one-shot contract is the exception: degrading it forfeits the
                # card, so the game pauses and the point is re-run after a resume.
                self._pause_once_or_degrade(
                    context.game_id, request.action_key, request.contract,
                )
                return PipelineActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None,
                    reasoning=DEGRADED_REASONING,
                    degraded=True,
                )
            return command

        if self._model_invocations is None:
            return provider

        def durable_provider(request, context, attempt):
            if request.contract.contract_id == "awakened_witch_assist_tally":
                # The tally is a deterministic engine resolution, not a model
                # decision. The provider already synthesises the command; it
                # must bypass the durable ledger too, or the "no model request"
                # contract gets a 0ms request/attempt with unknown usage.
                return provider(request, context, attempt)
            if self._pack_ballot_point(request, context):
                return provider(request, context, attempt)
            engine_context = self._durable_contexts.get(context.game_id)
            if engine_context is None:
                return provider(request, context, attempt)
            client = client_provider(request.actor_seat)
            frozen_request = {
                "kind": "pipeline_action",
                "action_key": request.action_key,
                "actor_seat": request.actor_seat,
                "role_id": request.role_id,
                "contract_id": request.contract.contract_id,
                "contract_digest": request.contract.stable_digest(),
                "round_number": request.round_number,
                "phase": request.phase,
                "window_id": request.window_id,
                "context_revision": request.context_revision,
            }
            generation = engine_context["execution_generation"]
            capture = self._prompt_capture(context.game_id)
            sink = None if capture is None else PromptSink()

            def provider_call():
                usage_sink: list[object] = []
                command = provider(
                    request, context, attempt, prompt_sink=sink, usage_sink=usage_sink,
                )
                merged = None
                for item in usage_sink:
                    merged = merge_usage(merged, item)
                return _durable_command_payload(command), _usage_mapping(merged)

            request_id = _pipeline_request_id(request.action_key, attempt)
            result = self._model_invocations.invoke_sync(
                game_id=context.game_id,
                request_id=request_id,
                actor_seat=request.actor_seat,
                action_position=request.action_key,
                frozen_request=frozen_request,
                provider_profile=self._client_identity(client)[0],
                model_id=self._client_identity(client)[1],
                execution_generation=generation,
                provider_call=provider_call, recovery=generation > 1,
            )
            self._wait_model_consumption_allowed_sync(context.game_id)
            if capture is not None and not result.reused:
                capture.flush(build_prompt_record(
                    request_id=request_id,
                    call_kind="pipeline",
                    round_number=request.round_number, phase=request.phase,
                    seat=request.actor_seat,
                    contract_id=request.contract.contract_id,
                    transport=None, attempt=sink.attempt_count,
                    model_id=self._client_identity(client)[1],
                    request_digest=None, sink=sink,
                ))
            return PipelineActionCommand.model_validate(result.normalized_result)

        return durable_provider

    def get_game_model_snapshot(self, game_id: str) -> list[dict]:
        """Return the persisted model snapshot for a game (display-only)."""
        return self._model_snapshots.get(game_id, [])

    def get_game_state(self, game_id: str) -> Optional[GameState]:
        return self._games.get(game_id)

    async def wait_game(self, game_id: str) -> dict[str, object]:
        """Wait for one runner without exposing task dictionaries to callers."""
        if game_id not in self._games:
            raise KeyError(game_id)
        task = self._tasks.get(game_id)
        if task is not None and not task.done():
            await asyncio.shield(task)
        return self.get_execution_info(game_id)

    async def cancel_benchmark_game(
        self, game_id: str, *, recovery_block_code: str | None = None,
    ) -> dict[str, object]:
        """Stop a benchmark-owned game and durably isolate late results."""
        if self.repository is None:
            raise ValueError("benchmark persistence is unavailable")
        record = self.repository.get_game(game_id)
        if record is None:
            raise KeyError(game_id)
        if record["benchmark_run_id"] is None:
            raise ValueError("game_not_managed_by_benchmark")
        status = str(record["execution_status"])
        if status == "cancelled":
            return self.get_execution_info(game_id)
        engine = self._engines.get(game_id)
        if engine is not None:
            await engine.stop()
        task = self._tasks.get(game_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if status == "running":
            self._persist_runtime_clock(game_id, running=True)
        self.repository.transition_execution(
            game_id,
            expected=("running", "paused", "interrupted", "recovery_blocked", "failed"),
            target="cancelled",
            increment_generation=True,
            recovery_block_code=recovery_block_code,
        )
        await self._close_game_clients(game_id)
        return self.get_execution_info(game_id)

    def list_games(self) -> list[str]:
        return list(self._games.keys())

    def is_lobby_game(self, game_id: str) -> bool:
        if self.repository is None:
            return True
        record = self.repository.get_game(game_id)
        if record is None:
            return True
        return record.get("benchmark_run_id") is None and record.get("source") != "benchmark"

    def get_game_folder_id(self, game_id: str) -> str | None:
        if self.repository is None:
            return None
        return self.repository.get_game_folder(game_id)

    def _require_repository(self) -> GameRepository:
        if self.repository is None:
            raise RuntimeError("folder persistence is unavailable")
        return self.repository

    def list_folders(self) -> list[dict[str, object]]:
        return self._require_repository().list_folders()

    def create_folder(self, name: str) -> dict[str, object]:
        return self._require_repository().create_folder(name)

    def rename_folder(self, folder_id: str, name: str) -> dict[str, object]:
        return self._require_repository().rename_folder(folder_id, name)

    def delete_folder(self, folder_id: str) -> None:
        self._require_repository().delete_folder(folder_id)

    def _reject_benchmark_lobby_mutation(self, game_id: str) -> None:
        if self.is_lobby_game(game_id):
            return
        record = None if self.repository is None else self.repository.get_game(game_id)
        run_id = record.get("benchmark_run_id") if record else None
        raise GameReferencedByBenchmark(str(run_id or "benchmark"))

    def assign_game_folder(self, game_id: str, folder_id: str | None) -> None:
        if game_id not in self._games:
            raise KeyError(game_id)
        self._reject_benchmark_lobby_mutation(game_id)
        self._require_repository().set_game_folder(game_id, folder_id)

    @staticmethod
    def _dedupe_game_ids(game_ids: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for game_id in game_ids:
            if game_id in seen:
                continue
            seen.add(game_id)
            unique.append(game_id)
        return unique

    def batch_move_games(
        self, game_ids: list[str], folder_id: str | None,
    ) -> dict[str, list]:
        moved: list[str] = []
        failed: list[dict[str, str]] = []
        for game_id in self._dedupe_game_ids(game_ids):
            try:
                self.assign_game_folder(game_id, folder_id)
                moved.append(game_id)
            except GameReferencedByBenchmark as error:
                failed.append({
                    "game_id": game_id, "code": "game_referenced_by_benchmark",
                    "message": str(error),
                })
            except KeyError:
                failed.append({
                    "game_id": game_id, "code": "not_found",
                    "message": "Game or folder not found",
                })
            except RuntimeError as error:
                failed.append({
                    "game_id": game_id, "code": "unavailable",
                    "message": str(error),
                })
        return {"moved": moved, "failed": failed}

    async def batch_delete_games(self, game_ids: list[str]) -> dict[str, list]:
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        for game_id in self._dedupe_game_ids(game_ids):
            try:
                self._reject_benchmark_lobby_mutation(game_id)
                await self.delete_game(game_id)
                deleted.append(game_id)
            except GameReferencedByBenchmark as error:
                failed.append({
                    "game_id": game_id, "code": "game_referenced_by_benchmark",
                    "message": str(error),
                })
            except KeyError:
                failed.append({
                    "game_id": game_id, "code": "not_found",
                    "message": "Game not found",
                })
            except OSError as error:
                failed.append({
                    "game_id": game_id, "code": "archive_busy",
                    "message": str(error),
                })
        return {"deleted": deleted, "failed": failed}

    def get_display_name(self, game_id: str) -> str:
        if self.repository is not None:
            record = self.repository.get_game(game_id)
            if record is not None and isinstance(record.get("name"), str):
                return str(record["name"])
        entry = self._manifest.get_entry(game_id) or {}
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return game_id[:8]

    def rename_game(self, game_id: str, name: str) -> None:
        if game_id not in self._games:
            raise KeyError(game_id)
        if self.repository is not None:
            self.repository.rename_game(game_id, name)
        self._manifest.update_game(game_id, name=name)

    @staticmethod
    async def _close_clients(clients) -> None:
        """Close each object in one client collection at most once."""
        values = clients.values() if isinstance(clients, Mapping) else clients
        closed: set[int] = set()
        for client in values:
            if id(client) in closed:
                continue
            closed.add(id(client))
            try:
                await client.aclose()
            except Exception:
                logger.debug("Failed to close LLM client", exc_info=True)

    async def _close_game_clients(self, game_id: str) -> None:
        """Atomically forget and close a game's clients exactly once.

        Popping before awaiting makes concurrent end/delete/shutdown cleanup
        idempotent. The shared close routine also handles repeated identities.
        """
        clients = self._llm_clients.pop(game_id, {})
        await self._close_clients(clients)

    async def _discard_game_runtime(self, game_id: str) -> None:
        engine = self._engines.get(game_id)
        if engine is not None:
            await engine.stop()
        task = self._tasks.get(game_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning("Engine task did not stop cleanly for %s", game_id)
        await self.ws_manager.close_game(game_id)
        self._close_prompt_capture(game_id)
        game_dir = os.path.join(self.data_dir, "games", game_id)
        if os.path.exists(game_dir):
            shutil.rmtree(game_dir)
        if engine is not None:
            close_logger = getattr(getattr(engine, "game_logger", None), "close", None)
            if callable(close_logger):
                close_logger()
        self._games.pop(game_id, None)
        self._health_guards.pop(game_id, None)
        self._engines.pop(game_id, None)
        self._tasks.pop(game_id, None)
        self._model_snapshots.pop(game_id, None)
        clock = self._clock_tasks.pop(game_id, None)
        if clock is not None and not clock.done():
            clock.cancel()
        self._clock_state.pop(game_id, None)
        self._durable_contexts.pop(game_id, None)
        await self._close_game_clients(game_id)
        self._manifest.remove_game(game_id)

    async def delete_game(self, game_id: str) -> None:
        if game_id not in self._games:
            raise KeyError(game_id)
        # Games restored from a legacy manifest may have no repository row.
        # Only soft-delete when a live row exists; orphaned games are removed
        # from the manifest and disk directly. Benchmark ownership is still
        # enforced inside mark_game_deleted for rows that do exist.
        if self.repository is not None and self.repository.get_game(game_id) is not None:
            self.repository.mark_game_deleted(game_id)
        await self._discard_game_runtime(game_id)

    async def delete_benchmark_owned_game(self, run_id: str, game_id: str) -> None:
        if self.repository is None:
            raise RuntimeError("benchmark persistence is unavailable")
        record = self.repository.get_game(game_id)
        if record is None:
            raise KeyError(game_id)
        if record.get("benchmark_run_id") != run_id:
            raise GameReferencedByBenchmark(str(record.get("benchmark_run_id") or "benchmark"))
        if game_id in self._engines or (
            game_id in self._tasks and not self._tasks[game_id].done()
        ):
            try:
                await self.cancel_benchmark_game(game_id)
            except (ValueError, InvalidExecutionTransition, KeyError):
                pass
        self.repository.release_benchmark_game(run_id, game_id)
        await self._discard_game_runtime(game_id)

    async def aclose(self) -> None:
        """Close every LLM client this service created.

        Headless callers (benchmark scripts) must invoke this before exit:
        unclosed httpx/openai clients crash interpreter finalization on
        Windows (segfault, reported as exit code 2816).
        """
        # First: parked consumption waits must see this before the executor
        # joins its workers, or shutdown deadlocks on paused games.
        self._closed.set()
        clocks = list(self._clock_tasks.values())
        for clock in clocks:
            clock.cancel()
        if clocks:
            await asyncio.gather(*clocks, return_exceptions=True)
        self._clock_tasks.clear()

        tasks: list[asyncio.Task] = []
        for game_id, task in list(self._tasks.items()):
            if task.done():
                continue
            engine = self._engines.get(game_id)
            if engine is not None:
                try:
                    await engine.stop()
                except Exception:
                    logger.exception("Failed to stop game=%s", game_id)
            if self.repository is not None:
                record = self.repository.get_game(game_id)
                if record is not None and record["execution_status"] == "running":
                    try:
                        self._persist_runtime_clock(game_id, running=True)
                        self.repository.transition_execution(
                            game_id, expected=("running",), target="interrupted",
                        )
                    except Exception:
                        logger.exception("Failed to interrupt game=%s during shutdown", game_id)
            task.cancel()
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for game_id in list(self._llm_clients):
            await self._close_game_clients(game_id)

    # ── Event Handlers ─────────────────────────────────────────

    def _known_game_id(self, kwargs: dict) -> Optional[str]:
        """Return an existing target id, dropping untrusted event envelopes."""
        game_id = kwargs.get("game_id")
        if not isinstance(game_id, str) or not game_id or game_id not in self._games:
            logger.warning(
                "Dropping public event for missing or unknown game_id: %r", game_id,
            )
            return None
        return game_id

    async def _on_phase_changed(self, **kwargs) -> None:
        game_id = self._known_game_id(kwargs)
        state = kwargs.get("state")
        if game_id is None or state is None or state.game_id != game_id:
            if state is not None and game_id is not None:
                logger.warning(
                    "Dropping public phase event with mismatched game_id: %r != %r",
                    game_id, state.game_id,
                )
            return
        self._games[game_id] = state
        self._persist_game(state)
        await self.ws_manager.broadcast(
            game_id, "phase_change",
            phase=kwargs.get("phase", ""),
            round_number=kwargs.get("round_number", 0),
            state=state.get_public_state(),
        )

    async def _on_player_died(self, **kwargs) -> None:
        game_id = self._known_game_id(kwargs)
        death = kwargs.get("death")
        if game_id is None or death is None:
            return
        death_dict = death.to_dict() if hasattr(death, "to_dict") else death
        await self.ws_manager.broadcast(game_id, "player_died", death=death_dict)

    async def _on_speaking(self, **kwargs) -> None:
        game_id = self._known_game_id(kwargs)
        seat = kwargs.get("seat")
        if (
            game_id is None
            or isinstance(seat, bool)
            or not isinstance(seat, int)
            or seat < 1
        ):
            return
        await self.ws_manager.broadcast(game_id, "speaking", seat=seat)

    async def _on_speech_stream(self, **kwargs) -> None:
        await self._notify_live_text("speech_stream", kwargs)

    async def _on_speech_thinking(self, **kwargs) -> None:
        # Live-only: the thinking tail is never written to the audience table.
        await self._notify_live_text("speech_thinking", kwargs)

    async def _notify_live_text(self, msg_type: str, kwargs: dict) -> None:
        game_id = self._known_game_id(kwargs)
        seat = kwargs.get("seat")
        text = kwargs.get("text")
        if (
            game_id is None
            or isinstance(seat, bool)
            or not isinstance(seat, int)
            or seat < 1
            or not isinstance(text, str)
        ):
            return
        self.ws_manager.queue_v2_notice(game_id, msg_type, seat=seat, text=text)
        await self.ws_manager.broadcast(game_id, msg_type, seat=seat, text=text)

    async def _on_acting(self, **kwargs) -> None:
        game_id = self._known_game_id(kwargs)
        seat = kwargs.get("seat")
        if (
            game_id is None
            or isinstance(seat, bool)
            or not isinstance(seat, int)
            or seat < 1
        ):
            return
        self.ws_manager.queue_v2_notice(game_id, "acting", seat=seat)
        await self.ws_manager.broadcast(game_id, "acting", seat=seat)

    async def _on_speech_made(self, **kwargs) -> None:
        game_id = self._known_game_id(kwargs)
        speech = kwargs.get("speech")
        if game_id is None or speech is None:
            return
        speech_dict = speech.to_dict() if hasattr(speech, "to_dict") else speech
        await self.ws_manager.broadcast(game_id, "speech", speech=speech_dict)

    async def _on_vote_cast(self, **kwargs) -> None:
        game_id = self._known_game_id(kwargs)
        vote = kwargs.get("vote")
        if game_id is None or vote is None:
            return
        public_vote = PublicVoteEvent.from_internal(
            vote=vote,
            round_number=self._games[game_id].round_number,
            **{
                key: value for key, value in kwargs.items()
                if key not in {"game_id", "vote"}
            },
        )
        if public_vote is None:
            return
        await self.ws_manager.broadcast(
            game_id, "vote_cast", vote=public_vote.to_payload(),
        )

    async def _on_game_over(self, **kwargs) -> None:
        game_id = self._known_game_id(kwargs)
        win_result = kwargs.get("win_result")
        if game_id is None or win_result is None:
            return
        try:
            wr_dict = win_result.to_dict() if hasattr(win_result, "to_dict") else win_result
            state = self._games[game_id]
            self._manifest.update_game(
                game_id,
                phase="game_over",
                winner=wr_dict.get("winning_camp"),
            )
            try:
                build_and_write_summary(self.data_dir, game_id)
            except Exception:
                logger.exception(
                    "Failed to write benchmark summary for game=%s", game_id,
                )
            await self.ws_manager.broadcast(
                game_id, "game_over", win_result=wr_dict,
                state=state.get_public_state(),
            )
        finally:
            await self._close_game_clients(game_id)

    async def _on_night_substep(self, **kwargs) -> None:
        game_id = self._known_game_id(kwargs)
        step = kwargs.get("step")
        round_number = kwargs.get("round_number")
        if (
            game_id is None
            or not isinstance(step, str)
            or step not in PUBLIC_NIGHT_SUBSTEPS
            or not isinstance(round_number, int)
            or isinstance(round_number, bool)
            or round_number < 1
        ):
            return
        payload = PublicNightSubstep.from_internal(
            step=step,
            round_number=round_number,
            **{
                key: value for key, value in kwargs.items()
                if key not in {"game_id", "step", "round_number"}
            },
        )
        await self.ws_manager.broadcast(
            game_id, "night_substep", **payload.to_payload(),
        )
