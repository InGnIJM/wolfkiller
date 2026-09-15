import asyncio
import hashlib
import json
import logging
import os
import random
import shutil
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from app.config import config as app_config
from app.models.game import GameState, GameConfig, GamePhase, PlayerState
from app.core.game_engine import GameEngine
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.agents.llm_client import (
    LLMClient, LLMClientConfig, derive_strict_base_url, env_default_client_config,
)
from app.agents.providers.registry import ProviderRegistry
from app.stores.model_config_store import get_model_config_store
from app.stores.model_key_crypto import KeyDecryptionError, ModelKeyCrypto
from app.agents.prompt_builder import PromptBuilder
from app.agents.prompt_renderer import PromptRenderer
from app.agents.game_rules import NIGHT_SYSTEM_PROMPT as _SYSTEM_PROMPT
from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.night_flow import NightDirector
from app.core.scheduler import Scheduler
from app.models.pipeline import ActionCommand as PipelineActionCommand, SchedulePoint
from app.models.contracts import AcceptedAction, ActionCommand as ContractActionCommand
from app.api.websocket.public_events import PublicNightSubstep, PublicVoteEvent
from app.roles.registry import builtin_registry
from app.api.websocket.ws_handler import WSManager
from app.services.game_manifest import GameManifest, default_game_name
from app.services.game_summary import build_and_write_summary
from app.persistence.checkpoint_codec import CheckpointCodec, CheckpointError
from app.persistence.repository import (
    GameReferencedByBenchmark, GameRepository, InvalidExecutionTransition,
)
from app.services.audience_projector import AudienceProjector
from app.services.durable_step_coordinator import DurableStepCoordinator
from app.services.model_invocation_service import (
    ModelInvocationService, ModelRequestUnavailable,
)

logger = logging.getLogger(__name__)


def _shuffle_model_assignments(values: list[int]) -> None:
    """Shuffle model seats without consuming the role allocator's PRNG state."""
    random.SystemRandom().shuffle(values)


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


def _model_failure_code(error: Exception, status_code: int | None) -> str:
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if "timeout" in type(cause).__name__.lower():
            return "provider_timeout"
        cause = cause.__cause__ or cause.__context__
    if status_code == 400:
        return "provider_bad_request"
    return "model_invocation_error"


_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _is_retryable_model_error(error: Exception) -> bool:
    """Transient provider failures get one immediate retry before fallback."""
    diagnostics = _exception_diagnostics(error)
    status_code = diagnostics.get("status_code")
    if isinstance(status_code, int) and status_code in _RETRYABLE_STATUS_CODES:
        return True
    failure_code = _model_failure_code(
        error, status_code if isinstance(status_code, int) else None,
    )
    if failure_code == "provider_timeout":
        return True
    return "connection" in type(error).__name__.lower()


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
        message = f"{type(error).__name__}: model invocation failed"

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
    status_code = primary.get("status_code")
    profile = getattr(getattr(llm_client, "provider_profile", None), "profile_id", None)
    model_id = getattr(llm_client, "model_name", None)
    details: dict[str, object] = {
        "provider_profile": profile if isinstance(profile, str) else "unknown",
        "model_id": model_id if isinstance(model_id, str) else "unknown",
        "failure_code": _model_failure_code(
            error, status_code if isinstance(status_code, int) else None,
        ),
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


def _log_night_llm_telemetry(
    game_logger, game_id: str, round_number: int, seat: int, *,
    tool_name: str, model_id: Optional[str], prompt_chars: int,
    elapsed_ms: int = 0, usage=None, attempts: int = 1,
    parse_result: str, failure_code: Optional[str] = None,
) -> None:
    """Write one wolf-channel LLM call record; telemetry must never break the night flow."""
    try:
        game_logger.log_llm_call(
            game_id, round_number, "night", seat,
            call_kind="night", contract_id=tool_name, transport="night_json",
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
            "Failed to persist wolf llm telemetry for game=%s seat=%s",
            game_id, seat,
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
        self._clock_tasks: dict[str, asyncio.Task] = {}
        self._clock_state: dict[str, dict[str, int | float | None]] = {}

        # Restore completed games so list / detail endpoints still work
        self._load_persisted_games()
        self._load_native_games()

        self.event_bus.subscribe(BusEvent.PHASE_CHANGED, self._on_phase_changed)
        self.event_bus.subscribe(BusEvent.PLAYER_DIED, self._on_player_died)
        self.event_bus.subscribe(BusEvent.SPEECH_MADE, self._on_speech_made)
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
            }
        record = self.repository.get_game(game_id)
        if record is None:
            raise KeyError(game_id)
        status = str(record["execution_status"])
        block = record["recovery_block_code"]
        return {
            "execution_status": status,
            "recoverable": status in {"paused", "interrupted"} and block is None,
            "recovery_block_code": block,
            "interruption_count": int(record["interruption_count"]),
            "benchmark_run_id": record["benchmark_run_id"],
        }

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
        started = values.get("running_since")
        if running and isinstance(started, (int, float)):
            elapsed = max(0, int((time.monotonic() - float(started)) * 1000))
            values["active_elapsed_ms"] = int(values["active_elapsed_ms"] or 0) + elapsed
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

    async def resume_game(self, game_id: str) -> dict[str, object]:
        return await self._resume_game(game_id, allow_benchmark=False)

    async def resume_benchmark_game(self, game_id: str) -> dict[str, object]:
        return await self._resume_game(game_id, allow_benchmark=True)

    async def _resume_game(
        self, game_id: str, *, allow_benchmark: bool,
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
            engine.resume()
            self._start_clock_heartbeat(game_id)
            return self.get_execution_info(game_id)
        if status == "paused":
            return await self._recover_game(
                game_id, expected_statuses=("paused",),
                allow_benchmark=allow_benchmark,
            )
        raise InvalidExecutionTransition(
            f"cannot transition game from {status} to running"
        )

    async def recover_game(
        self, game_id: str, *,
        expected_statuses: tuple[str, ...] = ("interrupted",),
    ) -> dict[str, object]:
        return await self._recover_game(
            game_id, expected_statuses=expected_statuses,
            allow_benchmark=False,
        )

    async def recover_benchmark_game(
        self, game_id: str, *,
        expected_statuses: tuple[str, ...] = ("interrupted",),
    ) -> dict[str, object]:
        return await self._recover_game(
            game_id, expected_statuses=expected_statuses,
            allow_benchmark=True,
        )

    async def _recover_game(
        self, game_id: str, *, expected_statuses: tuple[str, ...],
        allow_benchmark: bool,
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
        try:
            state, orchestration = self._checkpoint_codec.decode(checkpoint["checkpoint"])
        except CheckpointError as error:
            message = str(error)
            if "unsupported checkpoint version" in message:
                code = "checkpoint_version_unsupported"
            elif "registry" in message or "contract" in message:
                code = "registry_mismatch"
            else:
                code = "checkpoint_corrupt"
            self._mark_recovery_blocked(game_id, code)
            raise ValueError(code) from error

        try:
            engine, clients = self._build_recovered_engine(
                state, orchestration, record,
            )
        except ValueError as error:
            code = str(error)
            if code not in {
                "model_config_missing", "model_config_changed",
                "model_key_unavailable", "prompt_version_mismatch",
                "checkpoint_version_unsupported", "registry_mismatch",
                "checkpoint_corrupt",
            }:
                code = "checkpoint_corrupt"
            self._mark_recovery_blocked(game_id, code)
            raise ValueError(code) from error

        updated = self.repository.transition_execution(
            game_id, expected=expected_statuses, target="running",
            increment_generation=True,
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
        state.win_result = {
            "winning_camp": meta["winner"],
            "reason": "",
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
                state.win_result = {
                    "winning_camp": data.get("winner"),
                    "reason": data.get("reason", ""),
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
        model_assignments: Optional[list[dict]] = None,
        *,
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
        clients_by_seat: dict[int, LLMClient] = {}
        self._llm_clients[game_id] = clients_by_seat
        try:
            for seat, client_config in sorted(seat_configs.items()):
                clients_by_seat[seat] = LLMClient(config=client_config)
        except BaseException:
            await self._close_game_clients(game_id)
            raise

        def client_provider(seat: int) -> LLMClient:
            try:
                return clients_by_seat[seat]
            except KeyError:
                raise ValueError(f"unknown model seat: {seat}") from None

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
                try:
                    result = None
                    if self._model_invocations is None:
                        result = llm_client.invoke_json(
                            messages, tool_name=tool_name, schema=schema,
                        )
                        payload = getattr(result, "payload", None)
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
                            produced = llm_client.invoke_json(
                                messages, tool_name=tool_name, schema=schema,
                            )
                            value = getattr(produced, "payload", None)
                            if not isinstance(value, Mapping):
                                raise ValueError("model gateway response is not a JSON object")
                            return dict(value), _usage_mapping(getattr(produced, "usage", None))

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
                    if not isinstance(payload, Mapping):
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
                                "night",
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
                        _log_night_llm_telemetry(
                            engine_ref.game_logger, game_id,
                            engine_ref.state.round_number, seat,
                            tool_name=tool_name,
                            model_id=getattr(llm_client, "model_name", None),
                            prompt_chars=prompt_chars,
                            parse_result="error",
                            failure_code=type(error).__name__,
                        )
                    raise
                if engine_ref is not None:
                    _log_night_llm_telemetry(
                        engine_ref.game_logger, game_id,
                        engine_ref.state.round_number, seat,
                        tool_name=tool_name,
                        model_id=getattr(llm_client, "model_name", None),
                        prompt_chars=prompt_chars,
                        elapsed_ms=getattr(result, "elapsed_ms", 0) if result is not None else 0,
                        usage=getattr(result, "usage", None) if result is not None else None,
                        attempts=getattr(result, "llm_attempts", 1) if result is not None else 1,
                        parse_result="ok",
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
            )
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

            task = asyncio.create_task(engine.start())
            self._tasks[game_id] = task
            if self.repository is not None:
                self._start_clock_heartbeat(game_id)
        except BaseException:
            self._tasks.pop(game_id, None)
            self._engines.pop(game_id, None)
            self._games.pop(game_id, None)
            self._model_snapshots.pop(game_id, None)
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
                    payload = row.get("payload")
                    if isinstance(payload, dict):
                        payload.setdefault("round_number", engine.state.round_number)
                    row["event_id"] = f"domain:{event_id}:{index}"
                    row["schema_version"] = 1
                    events.append(row)
                if events:
                    return events
        payload: dict[str, object]
        event_type: str
        if label == "game_initialized":
            event_type = "GAME_INITIALIZED"
            payload = {
                "players": engine.state.get_public_state()["players"],
                "config": {
                    "role_counts": dict(engine.state.config.role_counts),
                    "reveal_on_death": engine.state.config.reveal_on_death,
                },
            }
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
            for vote in engine.state.votes:
                if vote.target_seat is not None:
                    key = str(vote.target_seat)
                    counts[key] = counts.get(key, 0) + 1
            event_type = "VOTE_RESULT"
            payload = {
                "round_number": engine.state.round_number,
                "exiled_seat": exiled_seat,
                "counts": dict(sorted(counts.items(), key=lambda item: int(item[0]))),
            }
        elif label.startswith("speech:") or label.startswith("last_words:"):
            event_type = "SPEECH_MADE"
            speech = engine.state.speeches[-1] if engine.state.speeches else None
            payload = {} if speech is None else speech.to_dict()
        elif label.startswith("vote_received:"):
            event_type = "VOTE_CAST"
            parts = label.split(":")
            seat = int(parts[-1])
            votes = [item for item in engine.state.votes if item.voter_seat == seat]
            payload = ({
                **votes[-1].to_dict(), "round_number": engine.state.round_number,
            } if votes else {})
        elif label.startswith("night_death:"):
            event_type = "PLAYER_DIED"
            seat = int(label.split(":")[-1])
            deaths = [item for item in engine.state.death_history if item.player_seat == seat]
            payload = deaths[-1].to_dict() if deaths else {}
        elif label.startswith("exile_reaction:"):
            deaths = [
                item for item in engine.state.death_history
                if item.round_number == engine.state.round_number
                and item.cause in {"exile", "hunter_shot"}
            ]
            if deaths:
                return [
                    {
                        "event_id": f"domain:{event_id}:{index}",
                        "event_type": "PLAYER_DIED",
                        "payload": death.to_dict(),
                        "visibility": ["PUBLIC"],
                        "schema_version": 1,
                    }
                    for index, death in enumerate(deaths)
                ]
            event_type = "STEP_COMMITTED"
            payload = {"position": label}
        elif (
            label.startswith("night_complete:")
            and isinstance(engine.state.win_result, Mapping)
        ):
            event_type = "GAME_OVER"
            payload = dict(engine.state.win_result)
        elif label.startswith("wolf_discussion:"):
            pending = getattr(engine, "_pending_night_batch", None)
            history = getattr(pending, "discussion_history", ()) if pending is not None else ()
            seat = int(label.split(":")[2])
            prefix = f"{seat}号："
            last = history[-1] if history else ""
            if last.startswith(prefix) and not last.endswith("（跳过）"):
                text = last[len(prefix):]
                plan = text.rfind("（次日计划：")
                if plan >= 0:
                    text = text[:plan]
                event_type = "WOLF_CHAT_MESSAGE"
                payload = {
                    "seat": seat, "text": text,
                    "round_number": engine.state.round_number,
                }
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
        if config_doc.get("prompt_digest") != _prompt_digest():
            raise ValueError("prompt_version_mismatch")
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
                raise ValueError("model_config_changed")
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

    async def _wait_model_consumption_allowed(self, game_id: str) -> None:
        if self.repository is None:
            return
        while True:
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

    def _instrument_roles(self, game_id: str, roles: Mapping[int, object]) -> None:
        invocations = self._model_invocations
        if invocations is None:
            return
        for seat, role in roles.items():
            original_speak = getattr(role, "speak")
            original_request_action = getattr(role, "request_action")

            async def durable_speak(
                state, conversation_log, context, *,
                _seat=seat, _role=role, _call=original_speak,
            ):
                position = f"round:{state.round_number}:speech:{context}:seat:{_seat}"
                request_id = f"speech:{state.round_number}:{context}:{_seat}"
                profile, model = self._client_identity(getattr(_role, "llm_client", None))
                generation = self._durable_contexts[game_id]["execution_generation"]

                async def provider_call():
                    value = await _call(state, conversation_log, context)
                    return ({"text": value, "is_none": value is None}, None)

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
                return None if result.normalized_result.get("is_none") is True else result.normalized_result.get("text")

            async def durable_request_action(
                state, conversation_log, request, *,
                _seat=seat, _role=role, _call=original_request_action,
            ):
                profile, model = self._client_identity(getattr(_role, "llm_client", None))
                generation = self._durable_contexts[game_id]["execution_generation"]

                async def provider_call():
                    accepted = await _call(state, conversation_log, request)
                    return ({
                        "command": accepted.command.model_dump(),
                        "technical_failure_code": accepted.technical_failure_code,
                        "timeout_type": accepted.timeout_type,
                    }, None)

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
        if state.registry_digest != self._registry_snapshot.digest:
            raise ValueError("registry_mismatch")
        seat_configs = self._resolve_recovery_model_configs(state, record)
        clients: dict[int, LLMClient] = {}
        try:
            for seat, client_config in sorted(seat_configs.items()):
                clients[seat] = LLMClient(config=client_config)
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
                raise ValueError("registry_mismatch") from error
            except KeyError as error:
                raise ValueError("registry_mismatch") from error
        self._instrument_roles(state.game_id, roles)

        snapshot = self._registry_snapshot
        engine_box: dict[str, GameEngine] = {}

        def night_invoke(messages, tool_name, schema, seat):
            client = client_provider(seat)
            frozen_request = {
                "kind": "night_json", "round_number": state.round_number,
                "tool_name": tool_name, "seat": seat,
                "messages": messages, "schema": schema,
            }
            digest = self._model_invocations.request_digest(frozen_request)
            generation = self._durable_contexts[state.game_id]["execution_generation"]

            def provider_call():
                produced = client.invoke_json(
                    messages, tool_name=tool_name, schema=schema,
                )
                value = getattr(produced, "payload", None)
                if not isinstance(value, Mapping):
                    raise ValueError("model gateway response is not a JSON object")
                return dict(value), _usage_mapping(getattr(produced, "usage", None))

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
            if not isinstance(payload, Mapping):
                raise ValueError("model gateway response is not a JSON object")
            engine = engine_box.get("engine")
            if engine is not None:
                _log_night_llm_telemetry(
                    engine.game_logger, state.game_id, state.round_number, seat,
                    tool_name=tool_name,
                    model_id=getattr(client, "model_name", None),
                    prompt_chars=sum(
                        len(str(item.get("content") or "")) for item in messages
                    ),
                    elapsed_ms=0, usage=None, attempts=1,
                    parse_result="ok",
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
        )
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
            logger.error(
                "Recovered game engine crashed for %s", game_id,
                exc_info=(type(error), error, error.__traceback__),
            )
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
    def _fallback_target(request, context, history: str, *, rng=None) -> int | None:
        """Choose one contract-valid first-night fallback target, if needed."""
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
        candidates = [
            seat for seat in alive
            if not validator.validate(
                context, request.contract,
                PipelineActionCommand(action_type=action_type, target_seat=seat, reasoning="server fallback"),
            )
        ]
        return (rng or random).choice(candidates) if candidates else None

    def _command_provider(self, snapshot, renderer: PromptRenderer, client_provider, director):
        """LLM-backed command provider for the pipeline scheduler.

        Renders a prompt from the frozen registry spec, projected context and
        the game's conversation history, then parses the response into an
        ActionCommand. Parse failures degrade to the contract's safe fallback
        so a point never stalls on a malformed model response.
        """
        def provider(request, context, attempt):
            if request.contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE:
                command = director.collected_vote(request.actor_seat)
                return command if command is not None else PipelineActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None,
                    reasoning="系统异常，本轮未行动",
                )
            role_spec = snapshot.require(request.role_id)
            engine = self._engines.get(context.game_id)
            history = ""
            if engine is not None:
                records = engine.conversation_log.get_conversations_for_role(
                    request.actor_seat, request.role_id,
                )
                history = "\n".join(
                    f"[{record.round_number}|{record.phase}|"
                    f"{record.speaker_seat if record.speaker_seat is not None else ''}] {record.content}"
                    for record in records[-120:]
                )
            fallback_target = self._fallback_target(
                request, context, history,
                rng=engine._rng if engine is not None else None,
            )
            if fallback_target is not None:
                context = replace(context, facts={
                    **context.facts, "RANDOM_HINT": fallback_target,
                })
            prompt = renderer.render(role_spec, request.contract, context, history)
            messages = [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
            llm_client = None
            command = None
            failure: Exception | None = None
            try:
                llm_client = client_provider(request.actor_seat)
                result = llm_client.invoke_action(messages, request.contract)
                command = PipelineActionCommand.model_validate(result.payload)
            except Exception as error:
                failure = error
                if _is_retryable_model_error(error):
                    # Transient provider blips (timeout/rate limit/connection)
                    # get one immediate retry so a night action is not
                    # silently forfeited on a single flaky call.
                    try:
                        result = llm_client.invoke_action(messages, request.contract)
                        command = PipelineActionCommand.model_validate(result.payload)
                        failure = None
                    except Exception as retry_error:
                        failure = retry_error
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
            if command is None or command.action_type not in request.contract.action_types:
                return PipelineActionCommand(
                    action_type=request.contract.fallback_action_type,
                    target_seat=None,
                    reasoning="系统异常，本轮未行动",
                )
            return command

        if self._model_invocations is None:
            return provider

        def durable_provider(request, context, attempt):
            if request.contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE:
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

            def provider_call():
                command = provider(request, context, attempt)
                return command.model_dump(), None

            result = self._model_invocations.invoke_sync(
                game_id=context.game_id,
                request_id=f"pipeline:{request.action_key}",
                actor_seat=request.actor_seat,
                action_position=request.action_key,
                frozen_request=frozen_request,
                provider_profile=self._client_identity(client)[0],
                model_id=self._client_identity(client)[1],
                execution_generation=generation,
                provider_call=provider_call, recovery=generation > 1,
            )
            self._wait_model_consumption_allowed_sync(context.game_id)
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

    async def cancel_benchmark_game(self, game_id: str) -> dict[str, object]:
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
        game_dir = os.path.join(self.data_dir, "games", game_id)
        if os.path.exists(game_dir):
            shutil.rmtree(game_dir)
        self._games.pop(game_id, None)
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
        if self.repository is not None:
            # Make the game disappear atomically before best-effort derived
            # file cleanup. Benchmark ownership is checked in this transaction.
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
