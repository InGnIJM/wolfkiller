import asyncio
import json
import logging
import os
import random
import shutil
import time
import traceback
import uuid
from collections.abc import Mapping
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
from app.api.websocket.public_events import PublicNightSubstep, PublicVoteEvent
from app.roles.registry import builtin_registry
from app.api.websocket.ws_handler import WSManager
from app.services.game_manifest import GameManifest, default_game_name
from app.services.game_summary import build_and_write_summary

logger = logging.getLogger(__name__)

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


def resolve_model_config(
    model_assignments: Optional[list[dict]],
    total_players: int,
) -> tuple[LLMClientConfig, list[dict]]:
    """Resolve stage-one model assignment into an explicit client config.

    Stage one accepts exactly one assignment whose count equals the total
    player count. `config_id=None` selects the .env environment default.
    Returns (client_config, display_snapshot); the snapshot never contains
    the api key.
    """
    env_config = replace(env_default_client_config(), provider_profile="auto")
    if model_assignments is None:
        return env_config, []
    if not isinstance(model_assignments, list) or len(model_assignments) != 1:
        raise ValueError("model assignments must contain exactly one entry")
    entry = model_assignments[0]
    if not isinstance(entry, dict):
        raise ValueError("model assignment entry must be an object")
    config_id = entry.get("config_id")
    count = entry.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count != total_players:
        raise ValueError("model assignment count must equal total players")
    if config_id is None:
        resolved_profile = ProviderRegistry().resolve(
            env_config.provider_profile, env_config.base_url, env_config.model_id,
        ).profile_id
        return env_config, [{
            "config_id": None,
            "name": "环境默认 (.env)",
            "model_id": env_config.model_id,
            "base_url": env_config.base_url,
            "provider_profile": resolved_profile,
        }]
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
    snapshot = [{
        "config_id": config.id,
        "name": config.name,
        "model_id": config.model_id,
        "base_url": config.base_url,
        "provider_profile": resolved_profile,
    }]
    return client_config, snapshot


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
    ):
        self.ws_manager = ws_manager
        self.event_bus = event_bus
        self.memory_service = memory_service
        self.data_dir = data_dir
        self._games: dict[str, GameState] = {}
        self._engines: dict[str, GameEngine] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._model_snapshots: dict[str, list[dict]] = {}
        self._manifest = GameManifest(data_dir=data_dir)

        # Restore completed games so list / detail endpoints still work
        self._load_persisted_games()

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
    ) -> str:
        game_id = str(uuid.uuid4())[:8]
        config = GameConfig(
            role_counts=role_counts,
            num_werewolves=num_werewolves,
            num_villagers=num_villagers,
            num_seers=num_seers,
            num_witches=num_witches,
            num_hunters=num_hunters,
            reveal_on_death=reveal_on_death,
        )

        # Resolve the stage-one model assignment into an explicit client config
        client_config, model_snapshot = resolve_model_config(
            model_assignments, config.total_players,
        )
        self._model_snapshots[game_id] = model_snapshot

        def client_provider(seat: int) -> LLMClient:
            return LLMClient(config=client_config)

        prompt_builder = PromptBuilder()
        roles = self._create_roles(config, prompt_builder, client_provider)

        # Build the registry-driven pipeline scheduler for night actions and
        # day death reactions; a single per-game model answers all pipeline calls.
        snapshot = builtin_registry.freeze()
        renderer = PromptRenderer()
        llm_client = client_provider(0)
        engine_ref = None

        def _night_invoke(messages, tool_name, schema, seat):
            prompt_chars = sum(
                len(str(message.get("content") or "")) for message in messages
            )
            try:
                result = llm_client.invoke_json(
                    messages,
                    tool_name=tool_name,
                    schema=schema,
                )
                payload = getattr(result, "payload", None)
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
                    elapsed_ms=getattr(result, "elapsed_ms", 0),
                    usage=getattr(result, "usage", None),
                    attempts=getattr(result, "llm_attempts", 1),
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

        # Persist to disk immediately so the game shows up after restart
        self._manifest.add_game(
            game_id,
            {
                "role_counts": dict(config.role_counts),
                "reveal_on_death": config.reveal_on_death,
            },
            model_snapshot=model_snapshot,
            name=default_game_name(config.total_players),
        )

        task = asyncio.create_task(engine.start())
        self._tasks[game_id] = task

        def _watch_engine(done: asyncio.Task) -> None:
            """Surface engine crashes instead of silently freezing the game."""
            if done.cancelled():
                return
            exc = done.exception()
            if exc is None:
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

    def _create_roles(
        self, config: GameConfig, prompt_builder: PromptBuilder, client_provider,
    ) -> dict:
        return builtin_registry.create_roles(
            config.role_counts,
            config.total_players,
            prompt_builder,
            llm_client_factory=lambda: client_provider(0),
        )

    @staticmethod
    def _fallback_target(request, context, history: str) -> int | None:
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
        return random.choice(candidates) if candidates else None

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
            fallback_target = self._fallback_target(request, context, history)
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
        return provider

    def get_game_model_snapshot(self, game_id: str) -> list[dict]:
        """Return the persisted model snapshot for a game (display-only)."""
        return self._model_snapshots.get(game_id, [])

    def get_game_state(self, game_id: str) -> Optional[GameState]:
        return self._games.get(game_id)

    def list_games(self) -> list[str]:
        return list(self._games.keys())

    def get_display_name(self, game_id: str) -> str:
        entry = self._manifest.get_entry(game_id) or {}
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return game_id[:8]

    def rename_game(self, game_id: str, name: str) -> None:
        if game_id not in self._games:
            raise KeyError(game_id)
        self._manifest.update_game(game_id, name=name)

    async def delete_game(self, game_id: str) -> None:
        if game_id not in self._games:
            raise KeyError(game_id)
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
        self._manifest.remove_game(game_id)

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
