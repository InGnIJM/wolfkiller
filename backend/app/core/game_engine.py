from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import random
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Optional

from app.models.game import Camp, GameState, GamePhase, GameConfig, PlayerState
from app.models.actions import (
    VoteAction, SpeechRecord, DeathReport, WinResult, is_last_words_eligible,
)
from app.models.conversation import ConversationScope
from app.models.contracts import ActionContract, ActionRequest
from app.core.state_machine import GameStateMachine, GameEvent as SM_Event
from app.core.rule_engine import RuleEngine
from app.core.win_attribution import winner_seats
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.night_flow import WolfVote, build_briefing
from app.core.conversation_log import ConversationLog
from app.core.game_logger import GameLogger
from app.roles.registry import builtin_registry
from app.config import PipelineMode, config as app_config
from app.core.effect_applier import CommitResult
from app.core.night_settlement import is_silenced, resolve_delayed_deaths
from app.core.wolf_pack import in_wolf_pack
from app.core.vote_service import VoteService
from app.core.sheriff_flow import (
    SHERIFF_TECHNICAL_ABSTAIN,
    SHERIFF_VOTE_FAILURE_CODE,
    SheriffDirector,
    apply_badge,
    badge_targets,
    ballot_weight,
    clear_office,
    decide_after_runs,
    decide_after_withdraw,
    decide_tally,
    badge_loss_message,
    election_seats,
    eligible_sheriff_voters,
    night_batch_deaths,
    night_death_seats,
    set_sheriff,
    sheriff_active,
    should_run_election,
    day_speech_order,
    speech_sides,
    unannounced_death_seats,
)
from app.core.point_journal import PendingEvent, PointCheckpoint, PointKey, WorkCursor, point_journal
from app.core.registry_identity import frozen_registry_digest
from app.core.role_pipeline import PipelineResult, RolePipeline
from app.core.role_runtime import role_private_data_view
from app.core.scheduler import PipelinePaused, PointResult, Scheduler
from app.models.pipeline import IssuedActionRequest, SchedulePoint
from app.models.vote import CastVoteArgs, VoteError, VoteStatus

logger = logging.getLogger(__name__)

VOTE_CONTRACT = ActionContract(
    contract_id="exile_vote",
    phase=GamePhase.VOTE_CASTING,
    action_types=("vote", "abstain"),
    actions_requiring_target=frozenset({"vote"}),
    resolution_priority=0,
    fallback_action_type="abstain",
    tool_name="cast_vote",
)

_EVENT_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")

# The check point resolves before the vial point, and the reason is the one-shot
# bounce (``core/reflect.py``).
#
# When a single night delivers both a check and a vial to the same bouncing
# seat, the official handbook fixes the outcome — the checker dies and the
# vial-holder does not — and states it as a flat fact rather than as "whichever
# came first". Ordering the check first makes the check claim the one-shot
# ``reflect_used`` marker, so the vial's bounce never fires: the official
# outcome falls out of the ordinary one-shot rule, with no board-specific
# branch and no new scheduling machinery.
#
# Nothing else depends on the relative order — neither contract reads the
# other's result — so outside the bounce this is observationally a no-op. The
# stage numbers are unchanged, so checkpoints written before the swap resume.
_NIGHT_POINTS = (
    SchedulePoint.NIGHT_ACTION,
    SchedulePoint.NIGHT_WOLF_VOTE,
    SchedulePoint.NIGHT_SEER_ACTION,
    SchedulePoint.NIGHT_WITCH_ACTION,
    SchedulePoint.NIGHT_COMMIT,
)

# Staged night batch stages at which each pipeline point executes; a batch
# with `stage > point_stage` must carry that point's result in raw_results.
_NIGHT_POINT_STAGES = (1, 5, 7, 9, 10)

# Slot of the POST_SPEECH_ACTION window that opens once every living seat has
# spoken and before the exile vote starts.
_POST_SPEECH_SLOT = "post_speech"

# Slot of the VOTE_ACTION window that opens once per day, right after the first
# exile vote has been counted and before anybody is exiled.
_VOTE_VERDICT_SLOT = "vote_verdict"

# A reaction window may kill a seat whose own death-response contract (the
# Awakened Wolf King's gifted claw, the Wolf King's claw, ...) still owes the
# table an answer. Each wave seeds the deaths the previous window settled; the
# cap keeps a malformed chain from hanging the engine, and an overflow is
# recorded as a telemetry fault instead of being skipped silently.
_MAX_REACTION_WAVES = 8


def _wolf_kill_target(pending_damage: tuple[object, ...]) -> Optional[int]:
    """Extract the current night's wolf kill target from pending damage.

    Only wolf-inflicted damage is recorded so the witch's antidote targets
    the actual victim; every other damage cause is ignored.
    """
    for item in pending_damage:
        if not isinstance(item, Mapping) or item.get("cause") != "wolf_kill":
            continue
        target = item.get("target")
        if type(target) is int and target >= 1:
            return target
    return None


def _discussion_consensus(wolves: list[int], history: list[str], leads: dict[int, int]) -> bool:
    """True when every wolf has voiced (or skipped) and all voiced targets agree."""
    if not leads:
        return False
    skipped = {
        seat for seat in wolves
        if any(line == f"{seat}号：（跳过）" for line in history)
    }
    return (
        all(seat in leads or seat in skipped for seat in wolves)
        and len(set(leads.values())) == 1
    )


# Discussion budget: each living wolf may speak up to six turns per night,
# and the channel always keeps going for at least two full rounds so a
# unanimous first round cannot end the meeting before the seats catch up.
_TURNS_PER_WOLF = 6
_MIN_DISCUSSION_ROUNDS = 2

#: First stage of the deferred night announcement (see
#: ``GameEngine._announce_pending_night_deaths``): the night-completion path
#: parks the slot here when the sheriff election owes the announcement.
_ANNOUNCE_STAGE = 10
# Deaths a pure react contract can produce without a settlement row.  The
# ledger merge below must not resurrect a stale settlement death just because
# the current round number matches.
_REACT_ONLY_DEATH_CAUSES = frozenset({
    "bond_death", "love_death", "charm", "stand_in",
})


@dataclass(frozen=True)
class _PendingDeath:
    seat: int
    cause: str
    round_number: int

    def __post_init__(self) -> None:
        if type(self.seat) is not int or not 1 <= self.seat <= 2_147_483_647: raise ValueError("invalid pending death seat")
        if type(self.round_number) is not int or not 0 <= self.round_number <= 2_147_483_647: raise ValueError("invalid pending death round")
        if type(self.cause) is not str or _EVENT_TOKEN.fullmatch(self.cause) is None: raise ValueError("invalid pending death cause")
        try: self.cause.encode("utf-8", errors="strict")
        except UnicodeError: raise ValueError("invalid pending death cause") from None  # pragma: no cover - token regex guarantees ASCII


@dataclass(frozen=True)
class _PendingWin:
    winning_camp: str
    reason: str

    def __post_init__(self) -> None:
        for value, name in ((self.winning_camp, "camp"), (self.reason, "reason")):
            if type(value) is not str or _EVENT_TOKEN.fullmatch(value) is None: raise ValueError(f"invalid pending win {name}")
            try: value.encode("utf-8", errors="strict")
            except UnicodeError: raise ValueError(f"invalid pending win {name}") from None  # pragma: no cover - token regex guarantees ASCII


@dataclass(frozen=True)
class _PendingNightCompletion:
    result: PipelineResult
    deaths: tuple[_PendingDeath, ...] | None = None
    event_cursor: int = 0
    stage: int = 0
    win_result: _PendingWin | None = None
    win_checked: bool = False
    win_invalid: bool = False

    def __post_init__(self) -> None:
        if type(self.result) is not PipelineResult: raise TypeError("result must be exact PipelineResult")
        if self.deaths is not None and (type(self.deaths) is not tuple or any(type(item) is not _PendingDeath for item in self.deaths)): raise TypeError("invalid pending deaths")
        if type(self.event_cursor) is not int or self.event_cursor < 0 or self.deaths is None and self.event_cursor or self.deaths is not None and self.event_cursor > len(self.deaths): raise ValueError("invalid event cursor")
        if type(self.stage) is not int or not 0 <= self.stage <= 14: raise ValueError("invalid completion stage")
        if self.win_result is not None and type(self.win_result) is not _PendingWin: raise TypeError("invalid pending win")
        if type(self.win_checked) is not bool or type(self.win_invalid) is not bool: raise TypeError("invalid win flags")


@dataclass(frozen=True)
class _PendingNightBatch:
    round_number: int
    stage: int
    discussion_history: tuple[str, ...]
    wolf_votes: tuple[WolfVote, ...]
    raw_results: tuple[PointResult, ...]
    discussion_leads: tuple[tuple[int, int], ...] = ()
    wolf_random_hint: int | None = None

    def __post_init__(self) -> None:
        if type(self.round_number) is not int or not 1 <= self.round_number <= 2_147_483_647:
            raise ValueError("invalid batch round")
        if type(self.stage) is not int or not 0 <= self.stage <= 12:
            raise ValueError("invalid batch stage")
        if type(self.discussion_history) is not tuple or any(type(item) is not str for item in self.discussion_history):
            raise TypeError("invalid discussion history")
        if type(self.wolf_votes) is not tuple or any(type(item) is not WolfVote for item in self.wolf_votes):
            raise TypeError("invalid wolf votes")
        if type(self.raw_results) is not tuple or any(type(item) is not PointResult for item in self.raw_results):
            raise TypeError("invalid batch results")
        if type(self.discussion_leads) is not tuple:
            raise TypeError("invalid discussion leads")
        for pair in self.discussion_leads:
            if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not int or type(pair[1]) is not int:
                raise TypeError("invalid discussion leads")
            if not 1 <= pair[0] <= 2_147_483_647 or not 1 <= pair[1] <= 2_147_483_647:
                raise ValueError("invalid discussion lead bounds")
        if self.wolf_random_hint is not None and (
            type(self.wolf_random_hint) is not int or self.wolf_random_hint <= 0
        ):
            raise ValueError("invalid wolf random hint")
        points_done = sum(
            1 for point_stage in _NIGHT_POINT_STAGES if self.stage > point_stage
        )
        if len(self.raw_results) != points_done:
            raise ValueError("invalid batch results")


class GameEngine:
    """Central orchestrator for a Werewolf game. All operations are encapsulated as methods."""

    def __init__(
        self,
        game_id: str | None = None,
        config: GameConfig | None = None,
        event_bus: EventBus | None = None,
        roles: dict[int, object] | None = None,
        memory_service: object | None = None,
        data_dir: str = "data",
        pipeline_scheduler: object | None = None,
        director: object | None = None,
        checkpoint_hook=None,
        fault_injector=None,
        health_guard=None,
        vote_warmup: bool = False,
        vote_warmup_max_wait_seconds: float | None = 3.0,
    ):
        self.game_id = game_id or str(uuid.uuid4())[:8]
        self.config = config or GameConfig()
        self.sm = GameStateMachine()
        self.rule_engine = RuleEngine()
        self.event_bus = event_bus or EventBus()
        self.roles = roles or {}
        self.memory_service = memory_service
        self._health_guard = health_guard
        self.game_logger = GameLogger(
            data_dir=data_dir,
            on_llm_call=(
                None if health_guard is None else health_guard.record_telemetry
            ),
        )
        self.conversation_log = ConversationLog(logger=self.game_logger, game_id=self.game_id)
        self.state = GameState(game_id=self.game_id, config=self.config)
        self._vote_service = VoteService(self.state)
        self._active_vote_window_id: str | None = None
        self._phase_delay: float = 2.0
        vote_concurrency = app_config.game.vote_concurrency
        self._vote_concurrency = (
            vote_concurrency
            if type(vote_concurrency) is int and vote_concurrency > 0 else 5
        )
        vote_phase_timeout = app_config.game.vote_phase_timeout_seconds
        self._vote_phase_timeout_seconds = (
            float(vote_phase_timeout)
            if type(vote_phase_timeout) in (int, float) and vote_phase_timeout > 0
            else 500.0
        )
        # Routing-cache experiment (WOLFKILLER_BURST_WARMUP): the first ballot
        # of a casting phase runs ahead so the parallel rest can reuse the
        # prefix it just committed on the shared backend. The head start is
        # capped: the gateway either commits the prefix within a few seconds
        # or the extra wait buys nothing more (WOLFKILLER_BURST_WARMUP_MAX_WAIT_SECONDS,
        # ``None`` waits for the whole first ballot).
        self._vote_warmup = bool(vote_warmup)
        self._vote_warmup_max_wait_seconds = (
            vote_warmup_max_wait_seconds
            if vote_warmup_max_wait_seconds is None
            or (
                type(vote_warmup_max_wait_seconds) in (int, float)
                and vote_warmup_max_wait_seconds > 0
            )
            else 3.0
        )
        self._running = False
        self._paused = False
        self._last_words_given: set[tuple[int, int]] = set()
        self._pipeline_scheduler = pipeline_scheduler
        self._director = director
        self._sheriff_director = SheriffDirector(
            getattr(director, "_invoke", None), self.conversation_log,
        )
        self._pending_night_completion: _PendingNightCompletion | None = None
        self._pending_night_batch: _PendingNightBatch | None = None
        # Public events of the daytime pipeline window that just ran, handed to
        # the very next durable step: the day windows have no ``night_point:``
        # style checkpoint of their own, and without this channel their events
        # reach the JSONL log but never the spectator stream. Deliberately *not*
        # part of the engine checkpoint codec (its field set is exact), because
        # it is always written immediately before its own commit.
        self._pending_point_events: tuple[tuple[str, Mapping[str, object]], ...] = ()
        # The narration the last ``_narrate`` wrote, for the same reason: the
        # step that publishes it commits immediately after this assignment.
        self._pending_narration: dict[str, object] | None = None
        # The dying sheriff's own reason for transferring or tearing the badge,
        # carried to the ``sheriff_badge:`` step the same way a narration is.
        # Deliberately not part of the engine checkpoint codec: it is always
        # written immediately before its own commit and never survives a restart.
        self._pending_badge_reasoning: str | None = None
        self._night_task: asyncio.Task | None = None
        self._rng = random.Random()
        self._checkpoint_hook = checkpoint_hook
        self._fault_injector = fault_injector
        self._checkpoint_counter = 0
        self._checkpoint_lock = asyncio.Lock()
        self._actor_loop: asyncio.AbstractEventLoop | None = None

    @staticmethod
    def _public_state_digest(state: GameState) -> str:
        document = json.dumps(
            state.get_public_state(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        return hashlib.sha256(document.encode("utf-8")).hexdigest()

    async def run_schedule_point(self, point: SchedulePoint) -> PipelineResult:
        return await self.run_schedule_points((point,))

    async def run_schedule_points(self, points: tuple[SchedulePoint, ...]) -> PipelineResult:
        if self._pipeline_scheduler is None: raise ValueError("pipeline scheduler is required")
        pipeline = RolePipeline(self._pipeline_scheduler)
        result = await asyncio.to_thread(pipeline.run_points, self.state, points)
        if type(result) is not PipelineResult: raise TypeError("pipeline must return exact PipelineResult")
        await self._durable_checkpoint(
            "schedule:" + ",".join(point.value for point in points)
        )
        return result

    @property
    def phase_delay(self) -> float:
        return self._phase_delay

    @phase_delay.setter
    def phase_delay(self, seconds: float) -> None:
        self._phase_delay = max(0.1, seconds)

    # =================================================================
    # Game Lifecycle
    # =================================================================

    async def start(self) -> None:
        """Compatibility entry point for a newly created game."""
        await self.create_new()

    async def create_new(self) -> None:
        if self._night_task is not None and not self._night_task.done():
            raise ValueError("night execution is active")
        self._night_task = None
        self._running = True
        self.sm.reset()
        previous_state = self.state
        self.state = GameState(game_id=self.game_id, config=self.config)
        self.state.pipeline_version = previous_state.pipeline_version
        self.state.registry_digest = previous_state.registry_digest
        self.state.spec_versions = dict(previous_state.spec_versions)
        self.state.effect_schema_version = previous_state.effect_schema_version
        self._vote_service = VoteService(self.state)
        self._active_vote_window_id = None
        # Collaborators retain this log; reset its contents, not its identity.
        self.conversation_log.records.clear()
        self._pending_night_completion = None
        self._pending_night_batch = None
        self._pending_point_events = ()
        self._pending_narration = None
        self._pending_badge_reasoning = None
        self._checkpoint_counter = 0

        self.sm.transition(SM_Event.START)
        await self._broadcast_phase_change(checkpoint=False)

        self._assign_roles()
        self.sm.transition(SM_Event.ROLES_ASSIGNED)
        await self._broadcast_phase_change(checkpoint=False)
        await self._durable_checkpoint("game_initialized")

        await self._game_loop()

    async def _durable_checkpoint(self, label: str) -> None:
        if self._checkpoint_hook is None:
            return
        async with self._checkpoint_lock:
            step_key = f"{self._checkpoint_counter:08d}:{label}"
            result = self._checkpoint_hook(step_key)
            if hasattr(result, "__await__"):
                await result
            self._checkpoint_counter += 1

    def export_orchestration(self, codec, *, model_assignments=None) -> dict[str, object]:
        from app.persistence.engine_checkpoint import encode_engine_orchestration
        return encode_engine_orchestration(
            self, codec, model_assignments=model_assignments,
        )

    def load_restored_state(self, state: GameState, orchestration, codec) -> None:
        """Rebind dependencies around decoded state without running new-game reset logic."""
        if type(state) is not GameState or state.game_id != self.game_id:
            raise ValueError("restored state does not belong to this engine")
        if self._night_task is not None and not self._night_task.done():
            raise ValueError("night execution is active")
        self._night_task = None
        self.state = state
        self.config = state.config
        self.sm.reset()
        self.sm.set_state(state.phase)
        self._vote_service = VoteService(self.state)
        # The codec replaces records on the shared log, keeping director bindings live.
        from app.persistence.engine_checkpoint import restore_engine_orchestration
        restore_engine_orchestration(self, codec, orchestration)
        self._running = False

    async def restore(self, state: GameState, orchestration, codec) -> None:
        """Continue a decoded checkpoint; this path never calls ``start``."""
        self.load_restored_state(state, orchestration, codec)
        self._running = True
        await self._game_loop()

    async def stop(self) -> None:
        self._running = False

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def _guard_turn(self, seat: int) -> None:
        """Stop before a model call when the provider is systemically down.

        Runs at the engine's turn boundaries, above the broad ``except
        Exception`` handlers in the night/speech/vote directors. Raising inside
        those handlers would be swallowed and turn a dead provider into a
        template speech or a technical abstention.
        """
        guard = self._health_guard
        if guard is not None:
            guard.check(seat)

    # =================================================================
    # Role Assignment
    # =================================================================

    def _assign_roles(self) -> None:
        """Assign roles to player state based on the pre-built roles dict (already shuffled)."""
        specs = builtin_registry.freeze().specs
        players_dict: dict = {}
        for seat, role_instance in sorted(self.roles.items()):
            role_name = role_instance.role_name
            try: spec = specs[role_name]
            except KeyError: raise ValueError(f"unknown role: {role_name}") from None
            player = PlayerState(seat_number=seat, role=role_name, camp=spec.camp_id)
            self.state.players[seat] = player
            players_dict[str(seat)] = {
                "role": role_name,
                "camp": spec.camp_id,
                "is_alive": True,
            }
        self.game_logger.log_operation(
            self.game_id, "game_config", 0, "role_deal",
            data={
                "role_counts": dict(self.config.role_counts),
                "reveal_on_death": self.config.reveal_on_death,
                "enable_sheriff": self.config.enable_sheriff,
            },
        )
        self.game_logger.log_role_init(self.game_id, players_dict)

    # =================================================================
    # Main Loop
    # =================================================================

    async def _game_loop(self) -> None:
        self._actor_loop = asyncio.get_running_loop()
        while self._running and not self.sm.is_terminal():
            await self._wait_if_paused()
            phase = self.sm.get_state()

            started = time.monotonic()
            if phase == GamePhase.NIGHT:
                await self._execute_night()
            elif phase == GamePhase.SHERIFF_ELECTION:
                await self._execute_sheriff_election()
            elif phase == GamePhase.DAWN:
                await self._execute_dawn()
            elif phase == GamePhase.LAST_WORDS:
                await self._execute_last_words()
            elif phase == GamePhase.SPEECH:
                await self._execute_speech_round()
            elif phase == GamePhase.VOTE_CASTING:
                await self._execute_vote_casting()
            elif phase == GamePhase.VOTE_RESOLUTION:
                await self._execute_vote_resolution()
            self._log_stage_telemetry("phase", phase.value, started)

    def _log_stage_telemetry(self, scope: str, stage: str, started: float) -> None:
        """Record one engine segment duration for benchmark analysis."""
        try:
            self.game_logger.log_operation(
                self.game_id, "stage_telemetry", self.state.round_number,
                self.state.phase.value,
                data={"scope": scope, "stage": stage,
                      "elapsed_ms": int((time.monotonic() - started) * 1000)},
            )
        except Exception:
            logger.exception(
                "Failed to persist stage telemetry for game=%s stage=%s",
                self.game_id, stage,
            )

    async def _wait_if_paused(self) -> None:
        while self._paused and self._running:
            await asyncio.sleep(0.1)

    # =================================================================
    # Night Phase
    # =================================================================

    def _prepare_night(self) -> None:
        self.state.round_number += 1
        self.state.night_actions.clear()
        self.state.last_wolf_kill_target = None
        self.state.sheriff_office.speech_side = None

    async def _execute_night(self) -> None:
        task = self._night_task
        if task is None or task.done():
            task = asyncio.create_task(self._night_owner())
            self._night_task = task
        await asyncio.shield(task)

    async def _night_owner(self) -> None:
        try: await self._execute_night_owned()
        finally:
            task = asyncio.current_task()
            if self._night_task is task: self._night_task = None

    async def _execute_night_owned(self) -> None:
        if self._pending_night_completion is not None:
            await self._resume_pipeline_night(); return
        if self._pipeline_scheduler is None: raise ValueError("pipeline scheduler is required")
        if self._director is None: raise ValueError("night director is required")
        if self._pending_night_batch is None:
            self._prepare_night()
            self._pending_night_batch = _PendingNightBatch(self.state.round_number, 0, (), (), ())
        await self._execute_staged_night()

    async def _narrate(self, title: str, text: str, phase: str = "night") -> None:
        self.game_logger.log_narration(self.game_id, self.state.round_number, phase, title, text)
        # A narration is a public beat of the timeline, but it is not a pipeline
        # event: without a step of its own the god view skipped straight from the
        # night to the speeches while the text stayed in the log.
        self._pending_narration = {
            "round_number": self.state.round_number, "title": title, "text": text,
        }
        await self._durable_checkpoint(f"narration:{self.state.round_number}")

    async def _narrate_role_openings(
        self, requests: tuple[IssuedActionRequest, ...],
    ) -> None:
        """Read the night script off the roles that actually acted.

        A role carries its own line (``RoleSpec.opening_narration``), so adding a
        role with a window never means touching this file. One line per role, in
        the point's execution order, and none for a role that was skipped: the
        request list already proves liveness and tonight's applicability.
        """
        scheduler = self._pipeline_scheduler
        registry = None if scheduler is None else getattr(scheduler, "registry", None)
        specs = None if registry is None else getattr(registry, "specs", None)
        if not isinstance(specs, Mapping):
            return
        seen: set[str] = set()
        for request in requests:
            if request.role_id in seen:
                continue
            seen.add(request.role_id)
            spec = specs.get(request.role_id)
            line = None if spec is None else getattr(spec, "opening_narration", None)
            # Malformed or absent lines are skipped rather than raising: the
            # script is presentation, and a stale archive must still play.
            if type(line) is not tuple or len(line) != 2:
                continue
            await self._narrate(line[0], line[1])

    async def _narrate_declared_openers(self, point: SchedulePoint) -> Optional[int]:
        """Play the opening lines the seats about to act at ``point`` declare.

        Used where the engine owns a beat that names a role: ``NIGHT_SEER_ACTION``
        is shared by every identity-checking skill, and the witch's point is shared
        by the Wolf Beauty's charm, so a board whose skill sits there used to hear
        the wrong role's line (a pure-white board heard the seer, a wolf-beauty-only
        board heard the witch). Each actor says its own line instead. Returns how
        many actors declared **none** — the caller's signal that its generic beat
        still has an owner, which is how the witch keeps her dynamic line (it
        carries tonight's knife fact, so it cannot be a static declaration) while a
        role that declares its own never hears it. The beat is also the fallback
        for a role added later that declares nothing, so nothing goes silent.
        ``None`` means the registry is unavailable and the caller cannot judge, so
        its beat must play: never silence a script we cannot evaluate.
        """
        scheduler = self._pipeline_scheduler
        specs = getattr(getattr(scheduler, "registry", None), "specs", None)
        if not isinstance(specs, Mapping):
            return None
        undeclared = 0
        for seat in self._night_openers(point):
            spec = specs.get(self.state.players[seat].role)
            line = None if spec is None else getattr(spec, "opening_narration", None)
            # Malformed or absent lines are skipped rather than raising: the
            # script is presentation, and a stale archive must still play.
            if type(line) is not tuple or len(line) != 2:
                undeclared += 1
                continue
            await self._narrate(line[0], line[1])
        return undeclared

    async def _log_stage_audience(self, result: PointResult) -> None:
        observation = RolePipeline.observe_v2(result)
        self._log_audience_events(
            PipelineResult((), (), observation.state_digest, observation.public_events, PipelineMode.V2),
            "night",
        )

    def _brew_plan(self) -> tuple[int, Mapping[str, object]] | None:
        """The current round's Awakened Witch brew plan, if any.

        The engine does not know the role: the plan is a generic private slot
        written by the brew contract. Its presence is what makes the three
        assist sub-steps run at all.
        """
        for seat in sorted(self.state.players):
            data = role_private_data_view(self.state, seat)
            plan = data.get("brew_plan")
            if isinstance(plan, Mapping) and plan.get("round") == self.state.round_number:
                return seat, plan
        return None

    @staticmethod
    def _assist_seats(plan: Mapping[str, object]) -> tuple[int, ...]:
        seats = plan.get("assist_seats") or ()
        if not isinstance(seats, (list, tuple)) or any(type(seat) is not int for seat in seats):
            raise PipelinePaused("invalid assist plan")
        return tuple(seats)

    def _assist_chat_records(
        self, seats: tuple[int, ...], round_number: int,
    ) -> list[object]:
        wanted = set(seats)
        return [
            record for record in self.conversation_log.records
            if record.scope is ConversationScope.NIGHT_INTEL
            and record.round_number == round_number
            and record.speaker_seat in wanted
            and set(record.visible_to or ()) == wanted
        ]

    async def _run_assist_chat(self, plan: Mapping[str, object]) -> None:
        seats = self._assist_seats(plan)
        # The official rule only allows the assistants to talk when there is
        # more than one of them; a lone assistant simply votes.
        if len(seats) < 2:
            return
        target = plan.get("target")
        if type(target) is not int or target <= 0:
            raise PipelinePaused("invalid assist plan")
        for seat in seats:
            records = self._assist_chat_records(seats, self.state.round_number)
            if any(record.speaker_seat == seat for record in records):
                continue
            history = tuple(
                f"{record.speaker_seat}号：{record.content}" for record in records
            )
            self._guard_turn(seat)
            await self.announce_actor(seat)
            text = await asyncio.to_thread(
                self._director.assist_discussion_turn,
                self.state, seat, target, seats, history,
            )
            if not text:
                continue
            self.conversation_log.add_night_intel(
                text, self.state.round_number, list(seats), speaker_seat=seat,
            )
            payload = {
                "round_number": self.state.round_number,
                "seat": seat, "target_seat": target, "text": text,
            }
            self._log_audience_pairs(
                (("AWAKENED_WITCH_ASSIST_CHAT", payload),), self.state.phase.value,
            )
            self._pending_point_events = (("AWAKENED_WITCH_ASSIST_CHAT", payload),)
            await self._durable_checkpoint(
                f"night_assist_chat:{self.state.round_number}:{seat}:{len(history)}"
            )

    async def _run_assist_point(self, point: SchedulePoint) -> None:
        scheduler = self._pipeline_scheduler
        if scheduler is not None and scheduler.point_completed(self.state, point):
            # The audience events were committed with the completing checkpoint;
            # replaying the point would insert them a second time.
            return
        raw = await self._execute_v2_point(point)
        observation = RolePipeline.observe_v2(raw)
        events = self._audience_events_from(observation.public_events)
        self._log_audience_pairs(events, self.state.phase.value)
        self._pending_point_events = events
        await self._durable_checkpoint(f"night_assist:{point.value}")

    async def _run_assist_steps(self) -> None:
        found = self._brew_plan()
        if found is None:
            return
        _, plan = found
        if not self._assist_seats(plan):
            await self._run_assist_point(SchedulePoint.NIGHT_WITCH_ASSIST_PICK)
            found = self._brew_plan()
            if found is None:
                return
            _, plan = found
        seats = self._assist_seats(plan)
        if not seats:
            return
        await self._run_assist_chat(plan)
        await self._run_assist_point(SchedulePoint.NIGHT_WITCH_ASSIST_VOTE)
        await self._run_assist_point(SchedulePoint.NIGHT_WITCH_ASSIST_TALLY)

    async def _execute_staged_night(self) -> None:
        pending = self._pending_night_batch
        director = self._director
        state = self.state

        if pending.stage == 0:
            # Kept as a barrier (old archives persist these stage numbers): the
            # night's role openings are narrated in stage 1, once the first point
            # has run. Its request list is the only thing that knows which roles
            # are alive *and* unlocked tonight, so a skill that starts on a later
            # night is never called early.
            #
            # The night's opening beat belongs *before* that point: stage 1 has to
            # narrate after its point runs, so leaving "天黑请闭眼" on the wolves'
            # beat (stage 2) made the audience hear a role called to open its eyes
            # before the night was even declared (95 of ~200 games).
            title, text = director.narration("night_open")
            await self._narrate(title, text)
            pending = replace(pending, stage=1); self._pending_night_batch = pending

        if pending.stage == 1:
            raw = await self._execute_v2_point(_NIGHT_POINTS[0])
            await self._narrate_role_openings(raw.requests)
            await self._log_stage_audience(raw)
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=2)
            self._pending_night_batch = pending
            await self._durable_checkpoint(
                f"night_point:{self.state.round_number}:{_NIGHT_POINTS[0].value}"
            )

        # Resolved *after* the night's first schedule point has run: role
        # resources are established inside ``Scheduler.issue`` (the only setup
        # call site) and no point runs before the night starts, so deriving the
        # pack earlier reads ``no_wolf_pack`` as absent and counts a hidden wolf
        # as a pack member — chat, kill vote, seal check and roster line alike.
        # Stage 2's beat needs the same answer (it must not promise a discussion
        # the pack will not hold), so the derivation sits above it; stage 2 still
        # runs after stage 1, which is the only thing this requires.
        wolves = [
            seat for seat in sorted(state.players)
            if in_wolf_pack(state, seat) and state.players[seat].is_alive
        ]

        if pending.stage == 2:
            # "开始讨论今晚的行动" is a promise: the pack talks only when more
            # than one of its members is alive and none of them is silenced.
            # Stage 3 re-decides the same question for real, right after.
            # Hidden wolves are not in ``wolves``: calling them with the pack's
            # line made the audience hear "狼人请睁眼" after the den was empty.
            if wolves:
                title, text = director.wolf_open_narration(
                    discusses=len(wolves) > 1 and not self._pack_sealed(wolves),
                )
                await self._narrate(title, text)
            pending = replace(pending, stage=3); self._pending_night_batch = pending

        if pending.stage == 3:
            if self._pack_sealed(wolves):
                # One silenced pack member seals the whole camp for the night.
                # The marks are applied during this night's first point, so the
                # check happens here, after it ran. Stage 5 still executes: with
                # no collected votes every wolf degrades to its fallback action
                # and the aggregate produces no damage at all.
                title, text = director.narration("pack_sealed")
                await self._narrate(title, text)
            elif len(wolves) > 1:
                history = list(pending.discussion_history)
                leads = dict(pending.discussion_leads)
                if pending.wolf_random_hint is None and not history:
                    briefing = build_briefing(self.conversation_log, wolves[0], self.state.round_number)
                    if not briefing.public_lines:
                        pending = replace(
                            pending, wolf_random_hint=self._rng.choice(sorted(state.alive_players())),
                        )
                        self._pending_night_batch = pending
                # Serial discussion rounds: each wolf speaks in turn and sees
                # everything said before, so the channel reads like a real
                # conversation. Consensus can only end the meeting after the
                # minimum full rounds; a full skipped round also stops it.
                max_turns = _TURNS_PER_WOLF * len(wolves)
                min_turns = _MIN_DISCUSSION_ROUNDS * len(wolves)
                while len(history) < max_turns:
                    seat = wolves[len(history) % len(wolves)]
                    briefing = build_briefing(self.conversation_log, seat, self.state.round_number)
                    self._guard_turn(seat)
                    await self.announce_actor(seat)
                    result = await asyncio.to_thread(
                        director.wolf_discussion_turn, state, seat, tuple(history), briefing,
                        pending.wolf_random_hint,
                    )
                    if result.spoke:
                        line = f"{seat}号：{result.text}"
                        channel = result.text
                        if result.day_plan:
                            line += f"（次日计划：{result.day_plan}）"
                            channel += f"｜次日计划：{result.day_plan}"
                        history.append(line)
                        if result.preferred_target is not None:
                            leads[seat] = result.preferred_target
                        self.conversation_log.add_werewolf_channel(
                            channel, self.state.round_number,
                            speaker_seat=seat, speaker_role=state.players[seat].role,
                        )
                        self.game_logger.log_audience_action(
                            self.game_id, state.round_number, "night",
                            "WOLF_CHAT_MESSAGE",
                            {"seat": seat, "text": result.text, "day_plan": result.day_plan},
                        )
                    else:
                        history.append(f"{seat}号：（跳过）")
                    pending = replace(pending, discussion_history=tuple(history),
                                      discussion_leads=tuple(sorted(leads.items())))
                    self._pending_night_batch = pending
                    await self._durable_checkpoint(
                        f"wolf_discussion:{self.state.round_number}:{seat}:{len(history)}"
                    )
                    if (len(history) >= len(wolves)
                            and all(line.endswith("（跳过）") for line in history[-len(wolves):])):
                        break
                    if len(history) >= min_turns and _discussion_consensus(wolves, history, leads):
                        break
            pending = replace(pending, stage=4); self._pending_night_batch = pending

        if pending.stage == 4:
            if wolves and not self._pack_sealed(wolves):
                discussion = tuple(line for line in pending.discussion_history if not line.endswith("（跳过）"))
                # Resume-safe sequential voting: each wolf sees all earlier
                # votes from this same night before casting its own vote.
                already_voted = {vote.seat for vote in pending.wolf_votes}
                remaining = [seat for seat in wolves if seat not in already_voted]
                votes = list(pending.wolf_votes)
                for seat in remaining:
                    briefing = build_briefing(self.conversation_log, seat, self.state.round_number)
                    self._guard_turn(seat)
                    await self.announce_actor(seat)
                    result = await asyncio.to_thread(
                        director.wolf_vote_turn, state, seat, discussion, tuple(votes), briefing,
                        pending.wolf_random_hint,
                    )
                    votes.append(result)
                    self.game_logger.log_audience_action(
                        self.game_id, state.round_number, "night", "WOLF_VOTE",
                        {"seat": seat, "target_seat": result.target_seat,
                         "reasoning": result.reasoning, "degraded": result.degraded},
                    )
                    pending = replace(pending, wolf_votes=tuple(votes))
                    self._pending_night_batch = pending
                    await self._durable_checkpoint(
                        f"wolf_vote:{self.state.round_number}:{seat}"
                    )
                director.record_votes(tuple(votes))
            else:
                director.record_votes(())
            pending = replace(pending, stage=5); self._pending_night_batch = pending

        if pending.stage == 5:
            if (
                not wolves
                and self._contract_point_enabled(
                    SchedulePoint.NIGHT_WOLF_VOTE, alive_only=True,
                )
            ):
                title, text = director.narration("lone_wolf_open")
                await self._narrate(title, text)
            raw = await self._execute_v2_point(_NIGHT_POINTS[1])
            await self._log_stage_audience(raw)
            runtime = getattr(state, "_pipeline_runtime", None)
            if runtime is not None:
                state.last_wolf_kill_target = _wolf_kill_target(tuple(runtime.pending_damage))
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=6)
            self._pending_night_batch = pending
            await self._durable_checkpoint(
                f"night_point:{self.state.round_number}:{_NIGHT_POINTS[1].value}"
            )

        if pending.stage == 6:
            # The identity-check line, one stage ahead of its own point (stage 7).
            # That point is shared by every skill that reads an identity, so the
            # roles about to act there say their own line; the driver's beat (which
            # names the Seer) plays only while an actor on the point declares none
            # — a fallback for a role added later, never for one that speaks.
            undeclared = await self._narrate_declared_openers(
                SchedulePoint.NIGHT_SEER_ACTION,
            )
            if undeclared != 0 and self._night_openers(SchedulePoint.NIGHT_SEER_ACTION):
                title, text = director.narration("seer_open")
                await self._narrate(title, text)
            pending = replace(pending, stage=7); self._pending_night_batch = pending

        if pending.stage == 7:
            raw = await self._execute_v2_point(_NIGHT_POINTS[2])
            await self._log_stage_audience(raw)
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=8)
            self._pending_night_batch = pending
            await self._durable_checkpoint(
                f"night_point:{self.state.round_number}:{_NIGHT_POINTS[2].value}"
            )

        if pending.stage == 8:
            # The medication/charm line, one stage ahead of its own point
            # (stage 9). The actors say their own lines; the Witch declares none
            # because her line carries tonight's knife (stage 5 already resolved
            # it), so the beat plays whenever an undeclared actor is on the point
            # — which is exactly the Witch, and a fallback for a role added later.
            undeclared = await self._narrate_declared_openers(
                SchedulePoint.NIGHT_WITCH_ACTION,
            )
            if undeclared != 0 and self._night_openers(SchedulePoint.NIGHT_WITCH_ACTION):
                title, text = director.witch_narration(state.last_wolf_kill_target)
                await self._narrate(title, text)
            pending = replace(pending, stage=9); self._pending_night_batch = pending

        if pending.stage == 9:
            raw = await self._execute_v2_point(_NIGHT_POINTS[3])
            await self._log_stage_audience(raw)
            # Keep the batch at stage 9 with three raw results while the
            # Awakened Witch's sub-points run: appending the fourth result to a
            # batch still at stage 9 violates ``_PendingNightBatch``'s
            # ``len(raw_results) == points_done`` invariant. The result is
            # appended in the same ``replace`` that advances to stage 10.
            await self._run_assist_steps()
            pending = replace(
                pending, raw_results=pending.raw_results + (raw,), stage=10,
            )
            self._pending_night_batch = pending
            await self._durable_checkpoint(
                f"night_point:{self.state.round_number}:{_NIGHT_POINTS[3].value}"
            )

        if pending.stage == 10:
            # The night's closing batch settles the damage. On a night the
            # election owes an announcement its death windows stay shut here:
            # they open once the deaths are public, so the seat they kill decides
            # with the badge (and the first speeches) in hand.
            raw = await self._execute_v2_point(
                _NIGHT_POINTS[4],
                defer_event_types=(
                    ("PLAYER_DIED",) if self._defers_night_announcement() else ()
                ),
            )
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=11)
            self._pending_night_batch = pending
            await self._log_stage_audience(raw)
            await self._durable_checkpoint(
                f"night_point:{self.state.round_number}:{_NIGHT_POINTS[4].value}"
            )

        if pending.stage == 11:
            # A night the election owes an announcement is read out by the
            # election path instead (official first day: badge first, deaths
            # after), so the dawn beat belongs to ``_announce_pending_night_deaths``.
            if not self._defers_night_announcement():
                deaths = [d.player_seat for d in state.death_history if d.round_number == state.round_number]
                title, text = director.dawn_narration(deaths)
                await self._narrate(title, text, phase="dawn")
            pending = replace(pending, stage=12); self._pending_night_batch = pending

        observations = tuple(RolePipeline.observe_v2(raw) for raw in pending.raw_results)
        result = PipelineResult(
            tuple(item for value in observations for item in value.accepted_actions),
            tuple(item for value in observations for item in value.effects),
            observations[-1].state_digest,
            tuple(item for value in observations for item in value.public_events),
            PipelineMode.V2,
        )
        self._pending_night_completion = _PendingNightCompletion(result)
        self._pending_night_batch = None
        await self._resume_pipeline_night()

    def _night_openers(self, point: SchedulePoint) -> tuple[int, ...]:
        """Living, unsilenced seats whose spec declares a contract at ``point``.

        The engine's own beats for the Seer and the Witch are scheduled one
        stage *before* their point runs, so they cannot read that point's
        request list the way ``_narrate_role_openings`` does. They read the
        registry instead, and reproduce the scheduler's gate from its two
        ingredients: both contracts are applicable on ``actor_alive`` alone
        (``seer_applicable`` / ``witch_applicable``), and the scheduler skips a
        requested seat that carries tonight's silence mark. No role name is
        involved, so a death or a seal simply stops the line.
        """
        scheduler = self._pipeline_scheduler
        specs = getattr(getattr(scheduler, "registry", None), "specs", None)
        living = tuple(sorted(
            seat for seat, player in self.state.players.items() if player.is_alive
        ))
        if not isinstance(specs, Mapping):
            # Nothing to evaluate the gate against: keep the beat rather than
            # going silent on a caller we cannot judge.
            return living
        runtime = getattr(self.state, "_pipeline_runtime", None)
        statuses = {} if runtime is None else runtime.statuses
        openers: list[int] = []
        for seat in living:
            if is_silenced(statuses, seat):
                continue
            spec = specs.get(self.state.players[seat].role)
            if spec is None:
                continue
            # A role that names the points its line is always true at (a dormant
            # copied skill must not wake it) is only an opener there.
            points = getattr(spec, "opening_points", None)
            if points and point.value not in points:
                continue
            if any(contract.schedule_point is point for contract in spec.contracts):
                openers.append(seat)
        return tuple(openers)

    def _pack_sealed(self, wolves: list[int]) -> bool:
        """True when this night's silence mark covers any living pack member.

        The rule is 「恐惧到狼人则整个狼人阵营当夜无法袭击」, so the whole camp is
        withheld from the kill whenever one member carries the mark. Deliberately
        role-agnostic: the engine only reads the same durable runtime status the
        settlement sweeps, never which skill applied it.
        """
        runtime = getattr(self.state, "_pipeline_runtime", None)
        if runtime is None:
            return False
        return any(is_silenced(runtime.statuses, seat) for seat in wolves)

    async def _execute_v2_point(
        self, point: SchedulePoint, *, defer_event_types: tuple[str, ...] = (),
    ) -> PointResult:
        pipeline = RolePipeline(self._pipeline_scheduler)
        result = await asyncio.to_thread(
            pipeline.execute_v2_point, self.state, point,
            defer_event_types=defer_event_types,
        )
        self._log_hook_faults(point, result.faults)
        return result

    def _log_hook_faults(self, point: SchedulePoint, faults) -> None:
        """Persist scheduler hook faults (e.g. slow rules) for benchmark analysis."""
        for fault in faults:
            payload = dict(fault)
            try:
                self.game_logger.log_operation(
                    self.game_id, "stage_telemetry", self.state.round_number,
                    self.state.phase.value,
                    data={"scope": "hook", "stage": point.value, **payload},
                )
            except Exception:
                logger.exception(
                    "Failed to persist hook fault for game=%s point=%s",
                    self.game_id, point.value,
                )

    def _pipeline_night_deaths(self, result: PipelineResult) -> tuple[_PendingDeath, ...]:
        deaths, seen = [], set()
        try:
            for event in result.public_events:
                if not isinstance(event, Mapping) or set(event) != {"event_type", "payload", "visibility"}:
                    raise ValueError
                event_type, payload, visibility = event["event_type"], event["payload"], event["visibility"]
                if type(event_type) is not str or not event_type or not isinstance(payload, Mapping): raise ValueError
                event_type.encode("utf-8", errors="strict")
                if type(visibility) is not tuple or "PUBLIC" not in visibility or any(type(item) is not str for item in visibility): raise ValueError
                if event_type != "PLAYER_DIED": continue
                if set(payload) != {"seat", "cause", "round_number"}: raise ValueError
                seat, cause, round_number = payload["seat"], payload["cause"], payload["round_number"]
                if type(seat) is not int or not 1 <= seat <= 2_147_483_647 or seat in seen: raise ValueError
                if type(cause) is not str or _EVENT_TOKEN.fullmatch(cause) is None: raise ValueError
                cause.encode("utf-8", errors="strict")
                if type(round_number) is not int or round_number != self.state.round_number: raise ValueError
                player = self.state.players.get(seat)
                matches = [item for item in self.state.death_history if type(item) is DeathReport and
                    (item.player_seat, item.cause, item.round_number) == (seat, cause, round_number)]
                if player is None or player.is_alive or len(matches) != 1: raise ValueError
                seen.add(seat); deaths.append(_PendingDeath(seat, cause, round_number))
        except (KeyError, TypeError, UnicodeError, ValueError):
            raise PipelinePaused("invalid pipeline night event") from None
        # React-contract deaths (bond_death / love_death) are recorded in the
        # death ledger by the write entry, but they do not travel through the
        # night batch's PLAYER_DIED result events. Fold the current round's
        # not-yet-seen ledger rows in, so the morning narration, summary and
        # public death list include them.
        ledger = (
            item for item in self.state.death_history
            if type(item) is DeathReport
        )
        for item in sorted(ledger, key=lambda death: death.player_seat):
            if item.round_number != self.state.round_number:
                continue
            if item.cause not in _REACT_ONLY_DEATH_CAUSES:
                continue
            if item.player_seat in seen:
                continue
            player = self.state.players.get(item.player_seat)
            if player is None or player.is_alive:
                continue
            seen.add(item.player_seat)
            deaths.append(_PendingDeath(item.player_seat, item.cause, item.round_number))
        return tuple(deaths)

    def _audience_events_from(
        self, raw_events: Iterable[Mapping[str, object]],
    ) -> tuple[tuple[str, object], ...]:
        """Validate PUBLIC pipeline-shaped events, dropping the deaths.

        A death never travels this way: every step announces the deaths of its
        own window from the ledger, by seat.
        """
        events = []
        try:
            for event in raw_events:
                if not isinstance(event, Mapping) or set(event) != {"event_type", "payload", "visibility"}:
                    raise ValueError
                event_type, payload, visibility = event["event_type"], event["payload"], event["visibility"]
                if type(event_type) is not str or not event_type or not isinstance(payload, Mapping): raise ValueError
                event_type.encode("utf-8", errors="strict")
                if _EVENT_TOKEN.fullmatch(event_type) is None: raise ValueError
                if type(visibility) is not tuple or "PUBLIC" not in visibility or any(type(item) is not str for item in visibility): raise ValueError
                if event_type == "PLAYER_DIED": continue
                events.append((event_type, payload))
        except (KeyError, TypeError, UnicodeError, ValueError):
            raise PipelinePaused("invalid pipeline audience event") from None
        return tuple(events)

    def _pipeline_audience_events(self, result: PipelineResult) -> tuple[tuple[str, object], ...]:
        """Collect validated non-death PUBLIC pipeline events for the audience log."""
        return self._audience_events_from(result.public_events)

    def _log_audience_pairs(self, events: tuple[tuple[str, object], ...], phase: str) -> None:
        for event_type, payload in events:
            self.game_logger.log_audience_action(
                self.game_id, self.state.round_number, phase, event_type, payload,
            )
            thought = payload.get("thought")
            seat = payload.get("seat")
            # A degraded payload is the engine's own placeholder, not something the
            # seat decided. Writing it into the private history would make the next
            # prompt show the model "its own" past reasoning ("决定今晚不使用药水：
            # 系统异常，本轮未行动") for a decision it never made — the seat then
            # plays on a memory that never happened. The god view still sees the
            # action: the public event is logged above either way.
            if payload.get("degraded") is not True and type(thought) is str and type(seat) is int and seat > 0:
                player = self.state.players.get(seat)
                if player is not None:
                    self.conversation_log.add_thought(
                        seat, player.role, thought, self.state.round_number, phase,
                    )
            channel = payload.get("channel")
            if type(channel) is str:
                self.conversation_log.add_werewolf_channel(
                    channel, self.state.round_number,
                )

    def _log_audience_events(self, result: PipelineResult, phase: str) -> None:
        self._log_audience_pairs(self._pipeline_audience_events(result), phase)

    def _announce_settlement(self, settlement: CommitResult) -> None:
        """Log a settlement's public events and hand them to the next step.

        Every settlement site commits a step right after this call, and that step
        publishes whatever is left in the slot. Deaths stay out of it (the same
        step announces them from the ledger by seat). The slot is always
        *overwritten*, never appended to, so the window events a ``day_point:``
        step already published can never be published a second time.
        """
        events = self._audience_events_from(settlement.events)
        self._log_audience_pairs(events, self.state.phase.value)
        self._pending_point_events = events

    def get_night_deaths(self) -> list[DeathReport]:
        """Get all deaths from the current round."""
        return [d for d in self.state.death_history if d.round_number == self.state.round_number]

    async def _resume_pipeline_night(self) -> None:
        pending = self._pending_night_completion
        if pending.deaths is None:
            pending = replace(pending, deaths=self._pipeline_night_deaths(pending.result))
            self._pending_night_completion = pending
        # Official first day: the badge is decided first and the judge reads the
        # night's deaths afterwards, so the first night of a sheriff game settles
        # here but is announced by the election path (``stage 10`` below). The
        # rest of this method is unchanged for every other night.
        defer = self._defers_night_announcement()
        if not defer:
            await self._publish_pending_night_deaths()
            pending = self._pending_night_completion
        await self._maybe_reassign_badge()
        if pending.stage == 0:
            if not defer:
                self.game_logger.log_deaths(self.game_id, self.state.round_number,
                    [DeathReport(item.seat, item.cause, item.round_number).to_dict() for item in pending.deaths])
            pending = replace(pending, stage=1); self._pending_night_completion = pending
        if pending.stage == 1:
            if self.memory_service: self.memory_service.save_memories(self.state)
            pending = replace(pending, stage=2); self._pending_night_completion = pending
        if pending.stage == 2:
            if pending.win_invalid: raise PipelinePaused("invalid pipeline win result")
            if not pending.win_checked:
                value = self.rule_engine.check_win(self.state)
                if value is not None and type(value) is not WinResult:
                    pending = replace(pending, win_checked=True, win_invalid=True); self._pending_night_completion = pending
                    raise PipelinePaused("invalid pipeline win result")
                snapshot = None if value is None else _PendingWin(value.winning_camp, value.reason)
                pending = replace(pending, win_checked=True, win_result=snapshot)
                self._pending_night_completion = pending
            if pending.win_result is not None and defer:
                # A decided game never reaches the election, so nobody else can
                # read the deaths out: announce them here, before the ending.
                self._pending_night_completion = replace(pending, stage=_ANNOUNCE_STAGE)
                await self._announce_pending_night_deaths()
                pending = self._pending_night_completion
            pending = replace(pending, stage=3); self._pending_night_completion = pending
        if pending.stage == 3:
            if pending.win_result is not None:
                # The per-seat winners are recomputed from the state rather than
                # carried in the pending snapshot: that snapshot is persisted
                # with an exact field set, and it only ever holds camp+reason.
                # The state cannot move between the check and this stage, so the
                # recomputation is the same answer the rule engine gave.
                self.state.win_result = self._pending_win_result(pending.win_result).to_dict(); self.state.phase = GamePhase.GAME_OVER
                self.sm.set_state(GamePhase.GAME_OVER)
            pending = replace(pending, stage=4); self._pending_night_completion = pending
        if pending.stage == 4:
            if pending.win_result is not None:
                logged = self._pending_win_result(pending.win_result)
                self.game_logger.log_game_over(self.game_id, self.state.round_number,
                    logged.winning_camp, logged.reason, winners=logged.winners)
            pending = replace(pending, stage=5); self._pending_night_completion = pending
        if pending.stage == 5:
            if pending.win_result is not None:
                await self.event_bus.publish(BusEvent.GAME_OVER, game_id=self.game_id,
                    win_result=self._pending_win_result(pending.win_result))
            pending = replace(pending, stage=6); self._pending_night_completion = pending
        if pending.stage == 6:
            if pending.win_result is None and self.sm.get_state() is GamePhase.NIGHT:
                event = (
                    SM_Event.SHERIFF_ELECTION_START
                    if should_run_election(self.state)
                    else SM_Event.NIGHT_ACTIONS_COMPLETE
                )
                try: self.sm.transition(event)
                except BaseException:
                    if self.sm.get_state() in {GamePhase.DAWN, GamePhase.SHERIFF_ELECTION}:
                        self.state.phase = self.sm.get_state()
                        pending = replace(pending, stage=7); self._pending_night_completion = pending
                    raise
            pending = replace(pending, stage=7); self._pending_night_completion = pending
        if pending.stage == 7:
            self.state.phase = self.sm.get_state()
            pending = replace(pending, stage=8); self._pending_night_completion = pending
        if pending.stage == 8:
            self.game_logger.log_phase_change(self.game_id, self.state.phase.value, self.state.round_number)
            pending = replace(pending, stage=9); self._pending_night_completion = pending
        if pending.stage == 9:  # pragma: no branch - stages advance sequentially to 9
            await self.event_bus.publish(BusEvent.PHASE_CHANGED, game_id=self.game_id,
                phase=self.state.phase.value, round_number=self.state.round_number, state=self.state)
            # A night the election owes an announcement keeps its slot, parked at
            # stage 10, so the election path can still read the batch out; every
            # other night (and a night that already ended the game) releases it.
            self._pending_night_completion = (
                replace(pending, stage=_ANNOUNCE_STAGE)
                if defer and pending.win_result is None
                else None
            )
            await self._durable_checkpoint(
                f"night_complete:{self.state.round_number}:{self.state.phase.value}"
            )

    def _still_on_the_table(self, seat: int) -> bool:
        """True while the seat plays: alive, or dead but not yet announced.

        The election runs before the judge reads the night's deaths, so a seat
        whose death is recorded but unannounced still campaigns and votes; a seat
        that died *during* the election (a self-explode) is off the table at once.
        """
        player = self.state.players.get(seat)
        if player is None:
            return False
        return player.is_alive or seat in unannounced_death_seats(self.state)

    def _defers_night_announcement(self) -> bool:
        """True when the night's deaths wait for the sheriff election.

        Official first day: the election runs before the judge reads the deaths,
        so the first night of a sheriff game must not publish or narrate them
        here. State-only and stable across a resume — the phase is still NIGHT
        for every stage of this method that runs before the transition.
        """
        return self.state.round_number == 1 and should_run_election(self.state)

    async def _publish_pending_night_deaths(self) -> None:
        """Publish the night's death batch: bus, reveal, ``night_death:`` step."""
        pending = self._pending_night_completion
        while pending is not None and pending.event_cursor < len(pending.deaths):
            snapshot = pending.deaths[pending.event_cursor]
            await self.event_bus.publish(BusEvent.PLAYER_DIED, game_id=self.game_id,
                death=DeathReport(snapshot.seat, snapshot.cause, snapshot.round_number))
            self._reveal_on_death(snapshot.seat)
            pending = replace(pending, event_cursor=pending.event_cursor + 1)
            self._pending_night_completion = pending
            await self._durable_checkpoint(
                f"night_death:{self.state.round_number}:{snapshot.seat}"
            )

    async def _announce_pending_night_deaths(self) -> None:
        """Finish a night the election owes an announcement (stages 10..13).

        Called from the election's own completion sites, and from the win path of
        ``_resume_pipeline_night`` when the night already ended the game. The
        stage lives in the persisted completion slot and every stage transition
        is committed, so a crash between two stages resumes at the next one
        instead of repeating a narration or a death. The slot is left at the
        "announcement done" stage: the caller decides whether to release it.
        """
        pending = self._pending_night_completion
        if pending is None or pending.stage < _ANNOUNCE_STAGE:
            return
        if pending.deaths is None:
            # Defensive: the completion path always fills the batch before it
            # parks the slot, but a slot restored from a hand-built checkpoint
            # may still owe the derivation.
            pending = replace(pending, deaths=self._pipeline_night_deaths(pending.result))
            self._pending_night_completion = pending
        if pending.stage == _ANNOUNCE_STAGE:
            title, text = self._director.dawn_narration(
                [item.seat for item in pending.deaths],
            )
            await self._narrate(title, text, phase="dawn")
            await self._advance_announcement(_ANNOUNCE_STAGE + 1)
        if self._pending_night_completion.stage == _ANNOUNCE_STAGE + 1:
            await self._publish_pending_night_deaths()
            await self._advance_announcement(_ANNOUNCE_STAGE + 2)
        if self._pending_night_completion.stage == _ANNOUNCE_STAGE + 2:
            self.game_logger.log_deaths(
                self.game_id, self.state.round_number,
                [DeathReport(item.seat, item.cause, item.round_number).to_dict()
                 for item in self._pending_night_completion.deaths],
            )
            await self._advance_announcement(_ANNOUNCE_STAGE + 3)
        if self._pending_night_completion.stage == _ANNOUNCE_STAGE + 3:
            await self._run_deferred_night_reactions()
            await self._advance_announcement(_ANNOUNCE_STAGE + 4)

    async def _run_deferred_night_reactions(self) -> None:
        """Open the night's death windows now that the deaths are public.

        Seeded with the night's own batch, exactly like an exile's window: the
        seat the night killed decides here — a shot, a revenge — with the badge
        and the day's opening speeches already in hand. The engine knows nothing
        about which contract answers; a night with no deaths opens no window.
        """
        pending = self._pending_night_completion
        if pending is None or not pending.deaths:
            return
        commit = self._synthetic_commit(
            f"night:{self.state.round_number}",
            tuple(
                self._died_event(
                    DeathReport(item.seat, item.cause, item.round_number),
                )
                for item in pending.deaths
            ),
        )
        await self._run_response_point(SchedulePoint.DAWN_REACTION, commit)
        reaction = await self._settle_and_publish()
        reaction += await self._run_reaction_waves(slot_prefix="night_react")
        # Pure react deaths (the scent phantom's bond, the cupid's love chain)
        # are MARK_DEATH effects, not settlement rows: _settle_and_publish only
        # publishes damage deaths, so without this pass their ledger rows never
        # reach the morning log or summary.  Exclude the seats the settlement
        # already published, and append the extra seats to the completion slot
        # so a resume at this stage does not publish them twice.
        settled = {death.player_seat for death in reaction}
        before = {death.seat for death in pending.deaths}
        extra = tuple(
            death for death in sorted(
                self.state.death_history, key=lambda item: item.player_seat,
            )
            if death.round_number == self.state.round_number
            and death.player_seat not in before
            and death.player_seat not in settled
            and death.cause in _REACT_ONLY_DEATH_CAUSES
        )
        if extra:
            await self._publish_deaths(extra)
            self._announce_delayed_deaths(extra)
            merged = tuple(
                _PendingDeath(death.player_seat, death.cause, death.round_number)
                for death in extra
            )
            self._pending_night_completion = replace(
                self._pending_night_completion, deaths=pending.deaths + merged,
                event_cursor=len(pending.deaths) + len(merged),
            )
            for death in extra:
                await self._durable_checkpoint(
                    f"react_death:{self.state.round_number}:{death.player_seat}"
                )
        await self._durable_checkpoint(
            f"night_reaction:{self.state.round_number}:{self._seat_list(reaction)}"
        )

    async def _advance_announcement(self, stage: int) -> None:
        """Move the completion slot to the next announcement stage, durably."""
        pending = self._pending_night_completion
        if pending is None:
            return
        self._pending_night_completion = replace(pending, stage=stage)
        await self._durable_checkpoint(
            f"night_announce:{self.state.round_number}:{stage}"
        )

    def _log_sheriff_audience(self, event_type: str, payload: dict[str, object]) -> None:
        self.game_logger.log_audience_action(
            self.game_id, self.state.round_number, self.state.phase.value,
            event_type, payload,
        )

    async def _publish_public_speech(
        self, seat: int, role: str, text: str, phase: str, *, log: bool = False,
    ) -> None:
        record = SpeechRecord(seat, text, self.state.round_number, phase)
        self.state.speeches.append(record)
        self.conversation_log.add_public_speech(
            seat, role, text, self.state.round_number, phase,
        )
        await self.event_bus.publish(
            BusEvent.SPEECH_MADE, game_id=self.game_id, speech=record,
        )
        if log:
            self.game_logger.log_speech(
                self.game_id, self.state.round_number, phase, seat, text,
            )

    async def _execute_sheriff_election(self) -> None:
        """Hardcoded sheriff campaign. Agents only pick the bound tool."""
        if self.sm.get_state() is not GamePhase.SHERIFF_ELECTION:
            raise RuntimeError("sheriff election requires SHERIFF_ELECTION")
        self.state.phase = GamePhase.SHERIFF_ELECTION
        if self.state.sheriff_election_complete:
            # Resume path: the election finished before the interruption, so the
            # deferred announcement (idempotent, stage-driven) is what is left.
            await self._announce_pending_night_deaths()
            self._pending_night_completion = None
            self.sm.transition(SM_Event.SHERIFF_ELECTION_COMPLETE)
            await self._broadcast_phase_change()
            return
        office = self.state.sheriff_office
        director = self._sheriff_director
        if office.step in {"", "run"}:
            office.step = "run"
            ran: set[int] = set(office.candidates)
            # The election runs before the deaths are read out, so the first
            # night's victims are still on the table: they choose whether to run,
            # speak, and vote like every other seat (official rule — the badge is
            # decided first).
            seats = frozenset(election_seats(self.state))
            pending_seats = [seat for seat in sorted(seats) if seat not in ran]
            for seat in pending_seats:
                player = self.state.players[seat]
                wolf = player.camp == Camp.WEREWOLF
                self._guard_turn(seat)
                choice = await asyncio.to_thread(
                    director.campaign_turn, self.state, seat, wolf=wolf,
                )
                if choice == "explode":
                    await self._election_explode(seat)
                    return
                if choice == "run":
                    ran.add(seat)
                office.candidates = set(ran)
                self._log_sheriff_audience("SHERIFF_RUN", {
                    "round_number": self.state.round_number,
                    "seat": seat,
                    "choice": choice,
                })
                await self._durable_checkpoint(
                    f"sheriff_run:{self.state.round_number}:{seat}:{choice}"
                )
            outcome = decide_after_runs(frozenset(ran), seats)
            if outcome != "campaign":
                winner = next(iter(ran)) if outcome == "auto" else None
                await self._finish_election(winner, outcome)
                return
            office.active = set(ran)
            office.step = "campaign"
        if office.step == "campaign":
            for seat in sorted(office.active):
                player = self.state.players.get(seat)
                if player is None or not self._still_on_the_table(seat):
                    continue
                speech_text = await self.speak(seat, "sheriff_campaign")
                if speech_text:
                    await self._publish_public_speech(
                        seat, player.role, speech_text, "sheriff_election",
                        log=True,
                    )
                await self._durable_checkpoint(
                    f"sheriff_campaign:{self.state.round_number}:{seat}"
                )
            office.step = "withdraw"
        if office.step == "withdraw":
            withdraw_seats = [
                seat for seat in sorted(office.candidates)
                if self.state.players.get(seat) is not None
                and self._still_on_the_table(seat)
            ]

            def decide_withdraw(seat: int) -> str:
                player = self.state.players[seat]
                return director.withdraw_turn(
                    self.state, seat, wolf=player.camp == Camp.WEREWOLF,
                )

            choices, timed_out = await self._collect_sheriff_decisions(
                withdraw_seats, decide_withdraw, timeout_result="stay",
            )
            if timed_out:
                self._log_sheriff_collect_timeout(
                    "withdraw", withdraw_seats, timed_out,
                )
            remaining: set[int] = set()
            for seat in withdraw_seats:
                if not self._still_on_the_table(seat):
                    continue
                choice = choices[seat]
                if isinstance(choice, BaseException):
                    raise choice
                if choice == "explode":
                    await self._election_explode(seat)
                    return
                if choice != "withdraw":
                    remaining.add(seat)
                self._log_sheriff_audience("SHERIFF_WITHDRAW", {
                    "round_number": self.state.round_number,
                    "seat": seat,
                    "choice": choice,
                })
                await self._durable_checkpoint(
                    f"sheriff_withdraw:{self.state.round_number}:{seat}:{choice}"
                )
            office.active = remaining
            outcome = decide_after_withdraw(
                frozenset(office.candidates), frozenset(remaining),
                frozenset(election_seats(self.state)),
            )
            if outcome != "vote":
                winner = next(iter(remaining)) if outcome == "auto" else None
                await self._finish_election(winner, outcome)
                return
            office.step = "vote"
        if office.step == "vote":
            if await self._sheriff_vote_round(office.active, "vote"):
                return
        elif office.step != "pk":
            return
        for seat in sorted(office.pk_seats):
            player = self.state.players.get(seat)
            if player is None or not self._still_on_the_table(seat):
                continue
            speech_text = await self.speak(seat, "sheriff_campaign")
            if speech_text:
                await self._publish_public_speech(
                    seat, player.role, speech_text, "sheriff_election",
                    log=True,
                )
            await self._durable_checkpoint(
                f"sheriff_pk:{self.state.round_number}:{seat}"
            )
        await self._sheriff_vote_round(office.pk_seats, "pk", final_tie=True)

    async def _collect_sheriff_decisions(
        self,
        seats: Sequence[int],
        decide: Callable[[int], object],
        *,
        timeout_result: object,
    ) -> tuple[dict[int, object], frozenset[int]]:
        """Ask every seat its sheriff decision concurrently.

        Mirrors the vote-casting window: the model calls run in parallel under
        ``_vote_concurrency`` — with the same optional warmup head start —
        and the whole stage is bounded by ``_vote_phase_timeout_seconds``.
        Everything the calls produce is a *value*: logs, state changes and
        checkpoints are applied by the caller in seat order, so the durable
        stream keeps the deterministic seat ordering it had when the stage
        was sequential. A seat that never answered is reported as
        ``timeout_result`` and listed in the returned ``timed_out`` set; a
        raised exception is stored as the seat's result and re-raised by the
        caller when it reaches that seat in order.
        """
        if not seats:
            return {}, frozenset()
        semaphore = asyncio.Semaphore(self._vote_concurrency)

        async def ask(seat: int) -> object:
            async with semaphore:
                self._guard_turn(seat)
                return await asyncio.to_thread(decide, seat)

        tasks: dict[int, asyncio.Task[object]] = {}
        # The phase budget covers the warmup head start too — same contract as
        # ``wait_for(collect_all)`` in the casting phase: a warmup that hangs
        # cannot stall the stage past the deadline.
        deadline = (
            asyncio.get_running_loop().time()
            + float(self._vote_phase_timeout_seconds)
        )

        def budget() -> float:
            return max(0.0, deadline - asyncio.get_running_loop().time())

        warmup_seat = (
            seats[0] if self._vote_warmup and len(seats) > 1 else None
        )
        if warmup_seat is not None:
            tasks[warmup_seat] = asyncio.create_task(ask(warmup_seat))
            # Same head-start contract as the casting phase: the warmup either
            # commits the shared prefix within a few seconds or the extra wait
            # buys nothing more (``None`` waits for the whole first call, still
            # bounded by the phase budget).
            head_start = self._vote_warmup_max_wait_seconds
            await asyncio.wait(
                {tasks[warmup_seat]},
                timeout=(
                    budget() if head_start is None
                    else min(float(head_start), budget())
                ),
            )
        for seat in seats:
            if seat != warmup_seat:
                tasks[seat] = asyncio.create_task(ask(seat))
        await asyncio.wait(set(tasks.values()), timeout=budget())
        results: dict[int, object] = {}
        timed_out: set[int] = set()
        for seat, task in tasks.items():
            if task.done():
                error = task.exception()
                results[seat] = error if error is not None else task.result()
            else:
                # A to_thread worker cannot be interrupted, but cancelling
                # drops its result here; the durable model request still
                # completes and is reused if this seat is asked again on a
                # resume with the identical prompt.
                task.cancel()
                timed_out.add(seat)
                results[seat] = timeout_result
        return results, frozenset(timed_out)

    def _log_sheriff_collect_timeout(
        self, kind: str, seats: Sequence[int], timed_out: frozenset[int],
    ) -> None:
        missing = sorted(timed_out)
        logger.warning(
            "Sheriff %s decisions timed out after %.1fs; fallback applies to %s",
            kind, self._vote_phase_timeout_seconds, missing,
        )
        self.game_logger.log_sheriff_phase_timeout(
            self.game_id, self.state.round_number, kind=kind,
            timeout_seconds=self._vote_phase_timeout_seconds,
            completed_seats=[seat for seat in seats if seat not in timed_out],
            missing_seats=missing,
        )

    async def _sheriff_vote_round(
        self, candidates: set[int], step: str, *, final_tie: bool = False,
    ) -> bool:
        """Return True if the election already finished (including explode)."""
        director = self._sheriff_director
        office = self.state.sheriff_office
        voters = eligible_sheriff_voters(
            frozenset(office.candidates), frozenset(election_seats(self.state)),
        )
        counts: dict[int, int] = {}
        ballots: dict[int, int | None] = {}
        legal = set(candidates)
        ballot_targets = tuple(sorted(legal))
        voter_seats = [
            seat for seat in sorted(voters)
            if self.state.players.get(seat) is not None
            and self._still_on_the_table(seat)
        ]

        def decide_vote(seat: int) -> int | str | None:
            player = self.state.players[seat]
            return director.vote_turn(
                self.state, seat, ballot_targets,
                wolf=player.camp == Camp.WEREWOLF,
            )

        choices, timed_out = await self._collect_sheriff_decisions(
            voter_seats, decide_vote, timeout_result=SHERIFF_TECHNICAL_ABSTAIN,
        )
        if timed_out:
            self._log_sheriff_collect_timeout(step, voter_seats, timed_out)
        for seat in voter_seats:
            if not self._still_on_the_table(seat):
                continue
            choice = choices[seat]
            if isinstance(choice, BaseException):
                raise choice
            if choice == "explode":
                await self._election_explode(seat)
                return True
            technical = choice == SHERIFF_TECHNICAL_ABSTAIN
            failure_code = (
                "request_timeout" if seat in timed_out
                else SHERIFF_VOTE_FAILURE_CODE
            )
            target = choice if type(choice) is int and choice in legal else None
            if target is not None:
                counts[target] = counts.get(target, 0) + 1
            ballots[seat] = target
            self._log_sheriff_audience("SHERIFF_VOTE", {
                "round_number": self.state.round_number,
                "voter_seat": seat,
                "target_seat": target,
                "kind": step,
            })
            await self._durable_checkpoint(
                f"sheriff_vote:{self.state.round_number}:{step}:{seat}:"
                f"{target if target is not None else 'none'}"
            )
            if technical:
                # The ballot above stays empty so the timeline still shows the
                # seat did not vote; this sibling step is what tells the god
                # view the emptiness was the engine, not a voluntary pass.
                self.game_logger.log_sheriff_vote_technical_abstain(
                    self.game_id, self.state.round_number, seat,
                    failure_code=failure_code,
                )
                await self._durable_checkpoint(
                    f"sheriff_vote_technical_abstain:"
                    f"{self.state.round_number}:{step}:{seat}:{failure_code}"
                )
        self.conversation_log.add_sheriff_ballot(ballots, self.state.round_number, step)
        winner, tied = decide_tally(counts)
        if winner is not None:
            await self._finish_election(winner, "vote")
            return True
        if tied and not final_tie:
            office.pk_seats = set(tied)
            office.step = "pk"
            await self._durable_checkpoint(f"sheriff_pk_ready:{self.state.round_number}")
            return False
        await self._finish_election(None, "tie" if tied else "none")
        return True

    async def _finish_election(self, seat: Optional[int], reason: str) -> None:
        if seat is None:
            clear_office(self.state, destroyed=True)
            message = badge_loss_message(
                reason,
                frozenset(self.state.sheriff_office.candidates),
                frozenset(election_seats(self.state)),
                frozenset(self.state.sheriff_office.active),
            )
        else:
            set_sheriff(self.state, seat)
            message = f"{seat}号当选警长。"
        self.state.sheriff_election_complete = True
        self.state.sheriff_office.step = "done"
        self.conversation_log.add_system_message(message, self.state.round_number, "public")
        self._log_sheriff_audience("SHERIFF_ELECTED", {
            "round_number": self.state.round_number,
            "seat": seat,
            "reason": reason,
        })
        await self._durable_checkpoint(
            f"sheriff_elected:{self.state.round_number}:{seat or 'none'}:{reason}"
        )
        # The badge has landed: now the judge reads the night's deaths out, which
        # is the official order (the election runs before the death announcement).
        await self._announce_pending_night_deaths()
        self._pending_night_completion = None
        if not await self._check_game_over():
            self.sm.transition(SM_Event.SHERIFF_ELECTION_COMPLETE)
        await self._broadcast_phase_change()

    async def _election_explode(self, seat: int) -> None:
        player = self.state.players.get(seat)
        if player is None or not player.is_alive:
            return
        player.mark_dead("self_explode")
        death = DeathReport(seat, "self_explode", self.state.round_number)
        self.state.death_history.append(death)
        clear_office(self.state, destroyed=True)
        self.state.sheriff_election_complete = True
        self.state.sheriff_office.skip_remaining_day = True
        self.state.sheriff_office.step = "done"
        self.conversation_log.add_system_message(
            f"{seat}号在警长竞选中自爆，警徽流失。",
            self.state.round_number, "public",
        )
        self._log_sheriff_audience("SHERIFF_ELECTED", {
            "round_number": self.state.round_number,
            "seat": None,
            "reason": "explode",
        })
        await self.event_bus.publish(
            BusEvent.PLAYER_DIED, game_id=self.game_id, death=death,
        )
        self._reveal_on_death(seat)
        # The election's self-explode is a death like any other, so it belongs in
        # the same durable batch the night's settlement uses: without it the seat
        # was missing from ``summary.json``'s ``rounds.*.deaths`` and from the
        # ``/logs`` replay, and only the god-view audience stream knew it died.
        # Written before the step's checkpoint so a crash cannot lose it; the
        # batch is replayed as a whole, exactly like the night batch, and the
        # summary's ``(seat, cause, round)`` de-duplication absorbs the replay.
        self.game_logger.log_deaths(
            self.game_id, self.state.round_number, [death.to_dict()],
            phase="sheriff_election",
        )
        await self._durable_checkpoint(
            f"sheriff_explode:{self.state.round_number}:{seat}"
        )
        # A self-explode ends the election on the spot; the night's deaths are
        # still read out right here (the docs' "仍报死讯和首夜遗言").
        await self._announce_pending_night_deaths()
        self._pending_night_completion = None
        if not await self._check_game_over():
            self.sm.transition(SM_Event.SHERIFF_ELECTION_COMPLETE)
        await self._broadcast_phase_change()

    async def _maybe_reassign_badge(self) -> None:
        if not self.state.config.enable_sheriff or self.state.sheriff_office.badge_destroyed:
            return
        dead_sheriff = next(
            (
                seat for seat, player in self.state.players.items()
                if player.is_sheriff and not player.is_alive
            ),
            None,
        )
        if dead_sheriff is None:
            return
        targets = badge_targets(self.state, dead_sheriff)
        self._guard_turn(dead_sheriff)
        action, target, reasoning = await asyncio.to_thread(
            self._sheriff_director.badge_turn, self.state, dead_sheriff, targets,
        )
        apply_badge(self.state, dead_sheriff, action, target)
        if self.state.sheriff is None:
            message = f"{dead_sheriff}号撕毁警徽，本局不再有警长。"
        else:
            message = f"{dead_sheriff}号将警徽移交给{self.state.sheriff}号。"
        payload: dict[str, object] = {
            "round_number": self.state.round_number,
            "from_seat": dead_sheriff,
            "to_seat": self.state.sheriff,
        }
        if reasoning:
            payload["reasoning"] = reasoning
        self.conversation_log.add_system_message(message, self.state.round_number, "public")
        self._log_sheriff_audience("SHERIFF_BADGE", payload)
        # The reason reaches the audience through the one-shot slot, not the
        # label: the step key below must keep its exact shape or every
        # in-flight game would stop resuming.
        self._pending_badge_reasoning = reasoning or None
        await self._durable_checkpoint(
            f"sheriff_badge:{self.state.round_number}:{dead_sheriff}:"
            f"{self.state.sheriff or 'tear'}"
        )
        self._pending_badge_reasoning = None

    async def _choose_speech_side(self) -> None:
        if not sheriff_active(self.state) or self.state.sheriff_office.speech_side:
            return
        sheriff_seat = self.state.sheriff
        assert sheriff_seat is not None
        deaths = night_death_seats(self.state)
        sides = speech_sides(len(deaths))
        self._guard_turn(sheriff_seat)
        side = await asyncio.to_thread(
            self._sheriff_director.side_turn, self.state, sheriff_seat, sides,
        )
        self.state.sheriff_office.speech_side = side
        self._log_sheriff_audience("SHERIFF_SIDE", {
            "round_number": self.state.round_number,
            "seat": sheriff_seat,
            "side": side,
        })
        await self._durable_checkpoint(
            f"sheriff_side:{self.state.round_number}:{sheriff_seat}:{side}"
        )

    async def give_last_words(
        self, seat: int, cause: str, death_round: int, *, daytime: bool = False,
    ) -> Optional[str]:
        """Generate last words for a dying player. Standalone function with validation.

        Eligibility:
        - First-night eligible night deaths
        - Vote-exiled players (any round)
        - Daytime skill deaths that the rule text grants words to (``daytime``)

        Validation:
        - Player must exist and not have already given last words
        - Death cause must be eligible

        The claim is checkpointed before the LLM is asked to speak, so a resume
        after a mid-speech crash skips the words rather than repeating them.
        """
        if not is_last_words_eligible(cause, death_round, daytime=daytime):
            return None

        death_key = (seat, death_round, cause)
        if death_key in self._last_words_given:
            return None

        player = self.state.players.get(seat)
        if player is None:
            return None

        self._last_words_given.add(death_key)
        # Persist the claim *before* the LLM call. A crash mid-speech then costs
        # the line on resume instead of replaying it into the timeline twice,
        # which matters more for a game the audience reads in order.
        await self._durable_checkpoint(
            f"last_words_pending:{death_round}:{seat}:{cause}"
        )

        speech_text = await self.speak(seat, "last_words")
        if speech_text:
            await self._publish_public_speech(
                seat, player.role, speech_text, "last_words", log=True,
            )

        await self._durable_checkpoint(
            f"last_words:{death_round}:{seat}:{cause}"
        )

        return speech_text

    # =================================================================
    # Dawn Phase
    # =================================================================

    async def _execute_dawn(self) -> None:
        await asyncio.sleep(self._phase_delay * 0.5)

        # Only the night's own batch is read out as "last night": a reaction
        # death (a shot fired through the night's response window) and the
        # election's self-explode carry their own announcement, and repeating
        # them here would date them to the night.
        batch = set(night_batch_deaths(self.state))
        round_deaths = [
            death for death in self.get_night_deaths()
            if death.player_seat in batch
        ]
        self.conversation_log.add_death_announcement(round_deaths, self.state.round_number)

        self.sm.transition(SM_Event.DAWN_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Last Words Phase
    # =================================================================

    async def _execute_last_words(self) -> None:
        # Only first-night deaths get last words here; exiled players give
        # their last words immediately during vote resolution.
        for death in self.state.death_history:
            await self.give_last_words(death.player_seat, death.cause, death.round_number)

        self.sm.transition(SM_Event.LAST_WORDS_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Speech Phase
    # =================================================================

    async def _execute_speech_round(self) -> bool:
        """Run the speech round; True means a role interrupted the day."""
        if self.state.sheriff_office.skip_remaining_day:
            self.state.sheriff_office.skip_remaining_day = False
            self.state.current_speaker = None
            self.state.speaking_order = []
            if not await self._check_game_over():
                self.sm.transition(SM_Event.WEREWOLF_EXPLODED)
            await self._broadcast_phase_change()
            return True
        if not self.state.is_tiebreak:
            await self._choose_speech_side()
        alive = list(self.state.alive_players().items())
        if self.state.is_tiebreak:
            alive = [
                (seat, player)
                for seat, player in alive
                if seat not in self.state.supplemental_speakers
            ]
        else:
            spoken_this_round = {
                record.player_seat
                for record in self.state.speeches
                if record.round_number == self.state.round_number
                and record.phase != "sheriff_election"
            }
            alive = [
                (seat, player)
                for seat, player in alive
                if seat not in spoken_this_round
            ]
        alive = self._rotate_speech_order(alive)
        self.state.speaking_order = [s for s, _ in alive]
        resumed = self._journaled_day_interruption()
        if resumed is not None:
            # A daytime window already interrupted this round before the phase
            # advanced (crash in between): finish that interruption instead of
            # letting the remaining seats speak.
            self.state.current_speaker = None
            self.state.speaking_order = []
            if self.sm.get_state() is GamePhase.SPEECH:
                await self._resolve_day_interruption(*resumed)
            return True
        for seat, player in alive:
            # Every speaker's turn opens a role-agnostic DAY_ACTION window
            # first; a role may interrupt the day from it, which cancels the
            # remaining speeches and this day's vote.
            if await self._run_day_action(seat):
                self.state.current_speaker = None
                self.state.speaking_order = []
                return True
            speech_text = await self.speak(seat, "day_speech")
            if speech_text:
                await self._publish_public_speech(
                    seat, player.role, speech_text, "speech", log=True,
                )
                if self.state.is_tiebreak:
                    self.state.supplemental_speakers.add(seat)
                await self._durable_checkpoint(
                    f"speech:{self.state.round_number}:{self.state.vote_round}:{seat}"
                )
            else:
                logger.warning(
                    f"Seat {seat}: speak() returned None/empty in speech round "
                    f"(alive={player.is_alive}, phase={self.state.phase.value}). "
                    f"This should not happen for a living player in speech phase."
                )

        self.state.current_speaker = None
        self.state.speaking_order = []
        # Every living seat has now spoken: open the post-speech DAY_ACTION
        # window before the exile vote, so a daytime skill that must land after
        # the speeches (and before the ballot) still can interrupt the day.
        if await self._run_post_speech_day_action():
            return True
        # A post-speech action kills through pending damage, and that death is
        # immediate: settle and announce it before the delayed deaths and before
        # the vote, never letting it linger into the night.
        if self._pipeline_scheduler is not None:
            post_speech = await self._settle_and_publish()
            if post_speech or self._pending_point_events:
                # Deaths reach the spectator only through a step that announces
                # them; this window has no dedicated one, so it borrows the
                # daytime-reaction label shape ``<round>:<vote_round>:<actor>``.
                # The same step publishes the marks a spared seat picked up.
                await self._durable_checkpoint(
                    f"day_reaction:{self.state.round_number}"
                    f":{self.state.vote_round}:0:{self._seat_list(post_speech)}"
                )
        # Then finalise any death the settlement delayed: the rules put it right
        # after the speeches and before the exile vote. A delayed death can end
        # the game (the last god dying of a vial, the Crimson Envoy's lingering
        # exile removing the last wolf), and the day must not carry on to a vote
        # nobody needs — so the verdict is read here, exactly as it is after a
        # ballot.
        await self._resolve_delayed_deaths()
        if not await self._check_game_over():
            self.sm.transition(SM_Event.SPEECHES_COMPLETE)
        await self._broadcast_phase_change()
        return False

    # =================================================================
    # Daytime pipeline windows (role-agnostic)
    # =================================================================

    def _day_action_enabled(self) -> bool:
        """True when this game seats a role that declares a DAY_ACTION contract.

        Dead holders still count so a resumed speech round replays the
        journaled window that interrupted the day instead of skipping it.
        """
        return self._contract_point_enabled(SchedulePoint.DAY_ACTION)

    def _post_speech_action_enabled(self) -> bool:
        """True when this game seats a role with a POST_SPEECH_ACTION contract."""
        return self._contract_point_enabled(SchedulePoint.POST_SPEECH_ACTION)

    def _contract_point_enabled(
        self, point: SchedulePoint, *, alive_only: bool = False,
    ) -> bool:
        scheduler = self._pipeline_scheduler
        if scheduler is None:
            return False
        specs = getattr(getattr(scheduler, "registry", None), "specs", None)
        if not isinstance(specs, Mapping):
            return False
        seated = self.state.alive_players() if alive_only else self.state.players
        for player in seated.values():
            spec = specs.get(player.role)
            for contract in () if spec is None else spec.contracts:
                if contract.schedule_point is point:
                    return True
        return False

    @staticmethod
    def _day_slot(vote_round: int, seat: int) -> str:
        return f"r{vote_round}-s{seat}"

    def _journaled_day_interruption(
        self,
    ) -> tuple[int, tuple[Mapping[str, object], ...]] | None:
        """Find a daytime window of this speech round that already interrupted
        the day: ``(speaker_seat, interruption payloads)``.

        Two windows can interrupt a speech round: the per-speaker DAY_ACTION
        window (``r<vote_round>-s<seat>``) and the POST_SPEECH_ACTION window
        (``post_speech``) that opens once every living seat has spoken.
        """
        day_action = self._day_action_enabled()
        post_speech = self._post_speech_action_enabled()
        if not (day_action or post_speech):
            return None
        # The journal key embeds the phase the window ran in. A resumed round
        # may already have advanced to NIGHT, so match on the slot suffix rather
        # than rebuilding the key from the current phase.
        speaker_suffix = f"#r{self.state.vote_round}-s"
        post_speech_suffix = f"#{_POST_SPEECH_SLOT}"
        for key, checkpoint in point_journal(self.state).entries():
            if key.round_number != self.state.round_number:
                continue
            if day_action and key.point is SchedulePoint.DAY_ACTION and (
                speaker_suffix in key.phase
            ):
                seat = int(key.phase.rsplit(speaker_suffix, 1)[1])
            elif post_speech and key.point is SchedulePoint.POST_SPEECH_ACTION and (
                key.phase.endswith(post_speech_suffix)
            ):
                seat = 0
            else:
                continue
            interruptions = tuple(
                event["payload"] for event in checkpoint.events
                if isinstance(event, Mapping) and event.get("event_type") == "DAY_INTERRUPTED"
                and isinstance(event.get("payload"), Mapping)
            )
            if interruptions:
                return seat, interruptions
        return None

    async def _run_day_action(self, seat: int) -> bool:
        """Open the DAY_ACTION window before ``seat`` speaks.

        Returns True when a role interrupted the day; the phase has then
        already advanced to NIGHT (or GAME_OVER).
        """
        if not self._day_action_enabled():
            return False
        return await self._run_day_window(
            self._day_slot(self.state.vote_round, seat), seat,
        )

    async def _run_post_speech_day_action(self) -> bool:
        """Open the POST_SPEECH_ACTION window after the last speaker, before the vote.

        This is the role-agnostic window a daytime skill that must run once every
        living player has spoken (and before the exile vote starts) declares its
        contract on. Returns True when a role interrupted the day; the phase has
        then already advanced to NIGHT (or GAME_OVER).
        """
        if not self._post_speech_action_enabled():
            return False
        return await self._run_day_window(
            _POST_SPEECH_SLOT, 0, point=SchedulePoint.POST_SPEECH_ACTION,
        )

    async def _run_day_window(
        self, slot: str, seat: int, *, point: SchedulePoint = SchedulePoint.DAY_ACTION,
    ) -> bool:
        result = await self._run_pipeline_point(point, slot=slot)
        interruptions = tuple(
            payload for event_type, payload in self._pipeline_audience_events(result)
            if event_type == "DAY_INTERRUPTED"
        )
        if not interruptions:
            return False
        await self._resolve_day_interruption(seat, interruptions)
        return True

    async def _resolve_day_interruption(
        self, seat: int, interruptions: tuple[Mapping[str, object], ...],
    ) -> None:
        scheduler = self._pipeline_scheduler
        settlement = scheduler.settle_pending(self.state)
        if settlement is not None:
            self._announce_settlement(settlement)
            deaths = self._settlement_deaths(settlement)
            await self._publish_deaths(deaths)
            dead = "、".join(f"{death.player_seat}号" for death in deaths) or "无人"
            self.conversation_log.add_system_message(
                f"白天进程被中断：{dead}出局，本日剩余发言与投票取消，直接进入夜晚。",
                self.state.round_number, "public",
            )
        else:
            # Resumed from a checkpoint taken after the settlement (and its
            # announcements) already landed: recover the same deaths from the
            # ledger by the causes the interruption declared, without
            # publishing them a second time. The window's own events already
            # went out through their ``day_point:`` step, so the slot is cleared
            # rather than handed on.
            self._pending_point_events = ()
            causes = {
                payload.get("cause") for payload in interruptions
                if type(payload.get("cause")) is str
            }
            deaths = tuple(
                death for death in self.state.death_history
                if death.round_number == self.state.round_number
                and death.cause in causes
            )
        label = f"{self.state.round_number}:{self.state.vote_round}:{seat}"
        await self._durable_checkpoint(
            f"day_interrupted:{label}:{self._seat_list(deaths)}"
        )
        # Give reactive roles taken along a response window (a death
        # trigger), exactly like an exile does.
        if deaths:
            commit = self._synthetic_commit(
                f"day:{seat}", tuple(self._died_event(death) for death in deaths),
            )
            await self._run_response_point(
                SchedulePoint.DAWN_REACTION, commit,
                slot=self._day_slot(self.state.vote_round, seat),
            )
            reaction = await self._settle_and_publish()
            reaction += await self._run_reaction_waves(slot_prefix="day_react")
            await self._durable_checkpoint(
                f"day_reaction:{label}:{self._seat_list(reaction)}"
            )
        await self._maybe_reassign_badge()
        # The day's own deaths speak before it closes: a duel victim dies during
        # the day and every daytime death gets last words. Reactive deaths (a
        # charmed player dragged along, an explosion's passenger) are not
        # daytime skill victims and stay silent.
        for death in deaths:
            await self.give_last_words(
                death.player_seat, death.cause, death.round_number, daytime=True,
            )
        if self.memory_service:
            self.memory_service.save_memories(self.state)
        if not await self._check_game_over():
            self.sm.transition(SM_Event.WEREWOLF_EXPLODED)
        await self._broadcast_phase_change()

    async def _run_pipeline_point(
        self, point: SchedulePoint, *, slot: str = "",
    ) -> PipelineResult:
        scheduler = self._pipeline_scheduler
        if scheduler is None:
            raise ValueError("pipeline scheduler is required")
        pipeline = RolePipeline(scheduler)
        raw = await asyncio.to_thread(pipeline.execute_v2_point, self.state, point, slot=slot)
        self._log_hook_faults(point, raw.faults)
        observation = RolePipeline.observe_v2(raw)
        result = PipelineResult(
            observation.accepted_actions, observation.effects,
            observation.state_digest, observation.public_events, PipelineMode.V2,
        )
        self._log_audience_events(result, self.state.phase.value)
        # Hand the window's public events to the next durable step. Windows that
        # produced nothing write no step at all, and a stale tuple can never be
        # published because a ``day_point:`` commit only happens right after
        # this assignment.
        self._pending_point_events = self._pipeline_audience_events(result)
        self._note_card_flips(self._pending_point_events)
        if self._pending_point_events:
            await self._durable_checkpoint(f"day_point:{point.value}:{slot}")
        return result

    def _note_card_flips(
        self, events: tuple[tuple[str, object], ...],
    ) -> None:
        """Record a public identity flip that does not cancel the exile.

        The exile-cancel and vote-rewind paths announce the flip themselves.
        A flip that only publishes ``PLAYER_REVEALED`` still has to land on the
        seat, or the next prompt and the next snapshot would not know the card
        was shown.
        """
        kinds = {event_type for event_type, _ in events}
        announced = bool(kinds & {"EXILE_CANCELLED", "VOTE_REWOUND"})
        for event_type, payload in events:
            if event_type != "PLAYER_REVEALED" or not isinstance(payload, Mapping):
                continue
            seat = payload.get("seat_number")
            if type(seat) is not int:
                continue
            player = self.state.players.get(seat)
            if player is None or player.revealed_role is not None:
                continue
            player.revealed_role = player.role
            if announced:
                continue
            try:
                display_name = builtin_registry.freeze().specs[player.role].display_name
            except KeyError:
                display_name = player.role
            self.conversation_log.add_system_message(
                f"{seat}号玩家翻牌亮出身份：{display_name}。",
                self.state.round_number, "public",
            )

    def _rotate_speech_order(
        self, alive: list[tuple[int, PlayerState]],
    ) -> list[tuple[int, PlayerState]]:
        """Start speeches after the most recent death, wrapping around the table.

        Night deaths and exiles both append to ``death_history`` before the
        speech round runs, so its last entry is the anchor; with no deaths yet
        (peaceful first night) the ascending seat order is kept. A living
        sheriff instead picks sheriff-left/right or death-left/right.
        """
        side = self.state.sheriff_office.speech_side
        sheriff_seat = self.state.sheriff
        if sheriff_active(self.state) and side and sheriff_seat is not None:
            seats = [seat for seat, _ in alive]
            by_seat = {seat: player for seat, player in alive}
            return [
                (seat, by_seat[seat])
                for seat in day_speech_order(self.state, side, seats)
                if seat in by_seat
            ]
        if not alive or not self.state.death_history:
            return alive
        anchor = self.state.death_history[-1].player_seat
        seats = [seat for seat, _ in alive]
        start = next(
            (index for index, seat in enumerate(seats) if seat > anchor), 0,
        )
        return alive[start:] + alive[:start]

    def _reveal_on_death(self, seat: int) -> None:
        """Announce a dead player's identity to everyone (明牌局 option)."""
        if not self.config.reveal_on_death:
            return
        player = self.state.players.get(seat)
        if player is None or player.revealed_role is not None:
            return
        player.revealed_role = player.role
        try:
            display_name = builtin_registry.freeze().specs[player.role].display_name
        except KeyError:
            display_name = player.role
        self.conversation_log.add_system_message(
            f"{seat}号玩家出局，身份是：{display_name}。",
            self.state.round_number, "public",
        )

    # =================================================================
    # Vote Casting Phase
    # =================================================================

    async def _execute_vote_casting(self) -> None:
        if self.sm.get_state() is not GamePhase.VOTE_CASTING:
            raise RuntimeError("vote phase execution requires VOTE_CASTING")
        self.state.phase = GamePhase.VOTE_CASTING
        if not self.state.voted_seats:
            self.state.votes.clear()
        window = self._vote_service.open_window(
            timeout_seconds=float(self._vote_phase_timeout_seconds),
        )
        self._active_vote_window_id = window.window_id
        existing_receipts = {
            receipt.voter_seat
            for receipt in self._vote_service.receipts(window.window_id)
        }
        for legacy_vote in tuple(self.state.votes):
            if (
                legacy_vote.voter_seat in self.state.voted_seats
                and legacy_vote.voter_seat not in existing_receipts
            ):
                self._vote_service.submit(
                    window.window_id, legacy_vote.voter_seat,
                    CastVoteArgs(
                        action_type=(
                            "abstain"
                            if legacy_vote.target_seat is None else "vote"
                        ),
                        target_seat=legacy_vote.target_seat,
                        reasoning=legacy_vote.reasoning[:500],
                    ),
                )
        seats = sorted(self._vote_service.missing_voters(window.window_id))
        semaphore = asyncio.Semaphore(self._vote_concurrency)

        async def collect_vote(seat: int) -> None:
            queued_at = time.monotonic()
            async with semaphore:
                self.game_logger.log_vote_queue_telemetry(
                    self.game_id, self.state.round_number, seat,
                    queue_wait_ms=int((time.monotonic() - queued_at) * 1000),
                    worker_limit=self._vote_concurrency,
                    vote_round=self.state.vote_round,
                )
                vote = await self.vote(seat)
                received_at = time.monotonic()
                receipt = next((
                    item for item in self._vote_service.receipts(window.window_id)
                    if item.voter_seat == seat
                ), None)
                if receipt is None:
                    if vote is None:
                        receipt = self._vote_service.technical_abstain(
                            window.window_id, seat,
                            failure_code="missing_vote_result",
                        )
                    else:
                        receipt = self._vote_service.submit(
                            window.window_id, seat,
                            CastVoteArgs(
                                action_type=(
                                    "abstain" if vote.target_seat is None else "vote"
                                ),
                                target_seat=vote.target_seat,
                                reasoning=vote.reasoning[:500],
                            ),
                            received_at=received_at,
                        )
                if receipt.status is VoteStatus.TECHNICAL_ABSTAIN:
                    # A ballot the system cast for a seat is announced as such.
                    # The recorded vote has no target, so the plain vote step
                    # would show the god view a silent abstention with no reason
                    # while the failure code stayed in the private ledger. The
                    # two labels are mutually exclusive: a seat commits exactly
                    # one step, so no ballot is ever published twice. A technical
                    # abstain always carries its failure code.
                    await self._durable_checkpoint(
                        f"vote_technical_abstain:{self.state.round_number}"
                        f":{self.state.vote_round}:{seat}:{receipt.failure_code}"
                    )
                else:
                    await self._durable_checkpoint(
                        f"vote_received:{self.state.round_number}"
                        f":{self.state.vote_round}:{seat}"
                    )

        window = self._vote_service.arm_window(
            window.window_id,
            timeout_seconds=float(self._vote_phase_timeout_seconds),
        )
        self.game_logger.log_vote_window_opened(
            self.game_id, self.state.round_number,
            window_id=window.window_id, vote_round=window.vote_round,
            eligible_voters=sorted(window.eligible_voters),
            timeout_seconds=self._vote_phase_timeout_seconds,
        )
        warmup_seat = (
            seats[0] if self._vote_warmup and len(seats) > 1 else None
        )
        if warmup_seat is not None:
            logger.info(
                "Vote warmup: seat %s casts first for game=%s round=%s",
                warmup_seat, self.game_id, self.state.round_number,
            )

        async def collect_all() -> None:
            if warmup_seat is not None:
                warmup_task = asyncio.create_task(collect_vote(warmup_seat))
                # Release the rest once the warmup has had its head start —
                # or finished early — whichever comes first. Its own outcome
                # is awaited below with the others, never swallowed.
                await asyncio.wait(
                    {warmup_task},
                    timeout=self._vote_warmup_max_wait_seconds,
                )
                await asyncio.gather(
                    warmup_task,
                    *(collect_vote(seat) for seat in seats if seat != warmup_seat)
                )
            else:
                await asyncio.gather(*(collect_vote(seat) for seat in seats))

        try:
            await asyncio.wait_for(
                collect_all(),
                timeout=self._vote_phase_timeout_seconds,
            )
        except asyncio.TimeoutError:
            terminal_seats = {
                receipt.voter_seat
                for receipt in self._vote_service.receipts(window.window_id)
            }
            completed_seats = sorted(set(seats) & terminal_seats)
            missing_seats = sorted(set(seats) - terminal_seats)
            logger.warning(
                "Vote phase timed out after %.1fs; preserving %s/%s completed votes",
                self._vote_phase_timeout_seconds, len(completed_seats), len(seats),
            )
            self.game_logger.log_vote_phase_timeout(
                self.game_id, self.state.round_number,
                timeout_seconds=self._vote_phase_timeout_seconds,
                completed_seats=completed_seats, missing_seats=missing_seats,
                vote_round=self.state.vote_round,
            )
            for seat in missing_seats:
                receipt = self._vote_service.technical_abstain(
                    window.window_id, seat, failure_code="request_timeout",
                    timeout_type="phase_deadline",
                )
                self.game_logger.log_vote_technical_abstain(
                    self.game_id, self.state.round_number, seat,
                    failure_code="request_timeout", timeout_type="phase_deadline",
                    window_id=window.window_id, vote_round=self.state.vote_round,
                )
                # These seats never reached their own vote step, so the timeout
                # is the only place that can announce what the system cast.
                await self._durable_checkpoint(
                    f"vote_technical_abstain:{self.state.round_number}"
                    f":{self.state.vote_round}:{seat}:{receipt.failure_code}"
                )
        # Concurrent completions race for the ledger; the seat order of
        # recorded votes is a deterministic invariant, so restore it after
        # every ballot has reached a terminal receipt.
        self.state.votes.sort(key=lambda item: item.voter_seat)
        receipts = {
            receipt.voter_seat: receipt
            for receipt in self._vote_service.receipts(window.window_id)
        }
        if set(receipts) != set(window.eligible_voters):
            raise RuntimeError("vote window has missing terminal receipts")
        for seat in seats:
            receipt = receipts[seat]
            self.game_logger.log_vote_receipt(
                self.game_id, self.state.round_number, seat,
                window_id=receipt.window_id, action_key=receipt.action_key,
                vote_round=window.vote_round, status=receipt.status.value,
                target=receipt.target_seat,
                command_digest=receipt.command_digest,
                replayed=receipt.replayed,
                failure_code=receipt.failure_code,
                timeout_type=receipt.timeout_type,
            )
            self.game_logger.log_vote(
                self.game_id, self.state.round_number, seat,
                receipt.target_seat, vote_round=self.state.vote_round,
            )
            await self.event_bus.publish(
                BusEvent.VOTE_CAST, game_id=self.game_id,
                vote=VoteAction(seat, receipt.target_seat),
            )
        terminal_receipts = tuple(receipts.values())
        self.game_logger.log_vote_window_closed(
            self.game_id, self.state.round_number,
            window_id=window.window_id, vote_round=window.vote_round,
            accepted_votes=sum(
                receipt.status is VoteStatus.ACCEPTED_VOTE
                for receipt in terminal_receipts
            ),
            voluntary_abstains=sum(
                receipt.status is VoteStatus.VOLUNTARY_ABSTAIN
                for receipt in terminal_receipts
            ),
            technical_abstains=sum(
                receipt.status is VoteStatus.TECHNICAL_ABSTAIN
                for receipt in terminal_receipts
            ),
            missing_voters=len(window.eligible_voters - set(receipts)),
        )

        self.sm.transition(SM_Event.VOTES_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Vote Resolution Phase
    # =================================================================

    async def _execute_tiebreak(self, candidates: list[int]) -> None:
        """Run exactly one persisted supplemental-speech and re-vote round."""
        if not self.state.is_tiebreak:
            # Emit the first-round vote_result frame so replays show the tie.
            self.resolve_votes()
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
            )
            self.state.vote_round = 2
            self.state.is_tiebreak = True
            self.state.tiebreak_candidates = set(candidates)
            self.state.supplemental_speakers.clear()
            self.state.voted_seats.clear()

            self.conversation_log.add_system_message(
                "平票，进入补充发言轮次后重新投票。",
                self.state.round_number,
                "public",
            )
            await self._durable_checkpoint(
                f"vote_result:{self.state.round_number}:1:none"
            )

        alive_seats = set(self.state.alive_players())
        if alive_seats - self.state.supplemental_speakers:
            self.sm.set_state(GamePhase.SPEECH)
            await self._broadcast_phase_change()
            if await self._execute_speech_round():
                # The supplemental round was interrupted: the re-vote is
                # cancelled and the day is already over.
                self._clear_tiebreak_state()
                return

        if alive_seats - self.state.voted_seats:
            if self.sm.get_state() is not GamePhase.VOTE_CASTING:
                self.sm.set_state(GamePhase.VOTE_CASTING)
                await self._broadcast_phase_change()
            await self._execute_vote_casting()
        else:
            self.sm.set_state(GamePhase.VOTE_RESOLUTION)

        exiled_seat = self.resolve_votes(log=False)
        cancelled = exiled_seat is not None and await self._apply_exile(exiled_seat)
        self._log_vote_result(None if cancelled else exiled_seat)
        self.conversation_log.add_vote_result(
            self.state.votes, None if cancelled else exiled_seat, self.state.round_number,
            cancelled_seat=exiled_seat if cancelled else None,
        )
        self._vote_service.clear_tally_marks()
        await self._durable_checkpoint(
            f"vote_result:{self.state.round_number}:{self.state.vote_round}:"
            f"{'none' if cancelled or exiled_seat is None else exiled_seat}"
        )
        self._clear_tiebreak_state()
        if not await self._check_game_over():
            self.sm.transition(SM_Event.VOTE_RESOLVED)
        await self._broadcast_phase_change()

    def _journaled_vote_rewinds(self) -> tuple[int, ...]:
        """Seats whose VOTE_ACTION window already voided this day's vote.

        Read from the journal rather than the window's return value, so the first
        run and a resume that replays a finished window take the same decision.
        """
        suffix = f"#{_VOTE_VERDICT_SLOT}"
        return tuple(
            event["payload"]["seat"]
            for key, checkpoint in point_journal(self.state).entries()
            if key.round_number == self.state.round_number
            and key.point is SchedulePoint.VOTE_ACTION
            and key.phase.endswith(suffix)
            for event in checkpoint.events
            if event.get("event_type") == "VOTE_REWOUND"
        )

    async def _run_vote_verdict_window(self, tally: dict[int, int]) -> bool:
        """Open the VOTE_ACTION window over a counted-but-unexecuted first vote.

        Returns True when a role voided the vote (``VOTE_REWOUND``); the day then
        carries on as a re-vote. Role-agnostic: the engine reads only the event.
        """
        if not self._contract_point_enabled(SchedulePoint.VOTE_ACTION, alive_only=True):
            return False
        leader = max(tally, key=tally.__getitem__)
        self.conversation_log.add_system_message(
            f"放逐投票已计票：{leader}号玩家得票最多（{tally[leader]}票），尚未执行放逐。",
            self.state.round_number, "public",
        )
        await self._run_pipeline_point(
            SchedulePoint.VOTE_ACTION, slot=_VOTE_VERDICT_SLOT,
        )
        return bool(self._journaled_vote_rewinds())

    async def _rewind_vote(self) -> None:
        """Void the counted vote and start a re-vote among every living seat.

        It is the tie-break machinery with a different cast: the flipped seat is
        the only one that still owes a (supplemental) speech, and the ballot is
        open to every living seat instead of the tied ones.
        """
        flipped = self._journaled_vote_rewinds()
        for seat in flipped:
            player = self.state.players[seat]
            player.revealed_role = player.role
        alive = set(self.state.alive_players())
        self._log_vote_result(None)
        self.conversation_log.add_system_message(
            "放逐投票作废：翻牌者逆转时空，本日放逐投票回到投票前，翻牌者额外发言一次后全员重新投票。",
            self.state.round_number, "public",
        )
        self.state.vote_round = 2
        self.state.is_tiebreak = True
        self.state.tiebreak_candidates = alive
        self.state.supplemental_speakers = alive - set(flipped)
        self.state.voted_seats.clear()
        await self._durable_checkpoint(
            f"vote_result:{self.state.round_number}:1:none"
        )
        await self._execute_tiebreak(sorted(alive))

    async def _execute_vote_resolution(self) -> None:
        if self.state.is_tiebreak:
            await self._execute_tiebreak(list(self.state.tiebreak_candidates))
            return

        tally = self._tally_votes()
        tied_candidates = self._tied_top_candidates(tally)
        if tied_candidates:
            await self._execute_tiebreak(tied_candidates)
            return

        if not tally:
            # Emit the vote_result frame even when nobody voted, so replays
            # do not silently skip this day's resolution.
            self.resolve_votes()
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
            )
            self._vote_service.clear_tally_marks()
            await self._durable_checkpoint(
                f"vote_result:{self.state.round_number}:1:none"
            )
            self._clear_tiebreak_state()
            if not await self._check_game_over():
                self.sm.transition(SM_Event.VOTE_RESOLVED)
            await self._broadcast_phase_change()
            return

        if await self._run_vote_verdict_window(tally):
            await self._rewind_vote()
            return

        exiled_seat = self.resolve_votes(log=False)
        cancelled = exiled_seat is not None and await self._apply_exile(exiled_seat)
        self._log_vote_result(None if cancelled else exiled_seat)

        # Announce vote result
        if cancelled:
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
                cancelled_seat=exiled_seat,
            )
        elif exiled_seat is not None:
            self.conversation_log.add_vote_result(
                self.state.votes, exiled_seat, self.state.round_number,
            )
        else:
            tie_final = "补充投票仍为平票，无人被放逐。"
            self.conversation_log.add_system_message(tie_final, self.state.round_number, "public")
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
            )

        self._vote_service.clear_tally_marks()
        await self._durable_checkpoint(
            f"vote_result:{self.state.round_number}:1:"
            f"{'none' if cancelled or exiled_seat is None else exiled_seat}"
        )
        # Reset this round's votes and casting bookkeeping only after the
        # result is durable: the active window carries the frozen sheriff basis
        # the VOTE_RESULT event is built from. The label above is literal ``1``
        # on this path, so clearing afterwards cannot change it.
        self._clear_tiebreak_state()

        if not await self._check_game_over():
            self.sm.transition(SM_Event.VOTE_RESOLVED)

        await self._broadcast_phase_change()

    # =================================================================
    # Exile Reaction (pipeline response windows)
    # =================================================================

    def _synthetic_commit(
        self, action_key: str, events: tuple[Mapping[str, object], ...],
    ) -> CommitResult:
        """Build a synthetic committed event batch so the pipeline's response
        windows can react to an engine-side lifecycle fact without engine-side
        knowledge of any specific role."""
        return CommitResult(
            action_key, (), 0, events, self._public_state_digest(self.state),
        )

    def _died_event(self, death: DeathReport) -> dict[str, object]:
        return {
            "event_type": "PLAYER_DIED",
            "payload": {
                "target_seat": death.player_seat,
                "cause": death.cause,
                "round_number": death.round_number,
            },
            "visibility": ("PUBLIC",),
        }

    def _exile_commit(self, exiled_seat: int) -> CommitResult:
        return self._synthetic_commit(f"vote:{exiled_seat}", (self._died_event(
            DeathReport(exiled_seat, "exile", self.state.round_number),
        ),))

    def _exile_pending_commit(self, exiled_seat: int) -> CommitResult:
        """Announce that the vote selected ``exiled_seat`` before anything dies,
        so a role may still overturn the verdict."""
        event = {
            "event_type": "EXILE_PENDING",
            "payload": {
                "target_seat": exiled_seat,
                "cause": "exile",
                "round_number": self.state.round_number,
            },
            "visibility": ("PUBLIC",),
        }
        return self._synthetic_commit(f"verdict:{exiled_seat}", (event,))

    async def _run_response_point(
        self, point: SchedulePoint, commit: CommitResult, *, slot: str = "",
    ) -> PipelineResult:
        """Seed ``point`` with the synthetic commit (unless a checkpoint already
        exists) and run its response windows through the pipeline."""
        scheduler = self._pipeline_scheduler
        if scheduler is None:
            raise ValueError("pipeline scheduler is required")
        key = PointKey(
            self.state.game_id, self.state.round_number,
            Scheduler.point_phase(self.state, slot), point,
            frozen_registry_digest(self.state, scheduler.registry),
        )
        journal = point_journal(self.state)
        if journal.get(key) is None:
            journal.put(key, PointCheckpoint(
                (), (), (commit,), commit.events, (),
                tuple(PendingEvent(0, ordinal, 0) for ordinal in range(len(commit.events))),
                WorkCursor("response", 0, 0), work_count=0,
            ))
        return await self._run_pipeline_point(point, slot=slot)

    def _reaction_seeded_seats(self) -> set[int]:
        """Seats whose death already seeded a finished response window.

        The death ledger alone cannot tell a night-death window from a death a
        reaction window produced: both are ordinary ``DeathReport`` rows. The
        journal is the durable record of which seats were seeded, so a chain
        like "a death reaction killed the claw holder" can ask for exactly the
        deaths nobody has reacted to yet.

        Two points open death windows, and they spell the seat differently: a
        ``DAWN_REACTION`` commit is a synthetic ``PLAYER_DIED`` with
        ``target_seat``, while the night's own windows run inside
        ``NIGHT_COMMIT`` over the settlement batch, whose events use ``seat``.
        Reading only ``DAWN_REACTION``/``target_seat`` made every night death
        look unseeded at the next exile, so the wave below re-opened reactors
        that had already fired — the Dream Weaver's revenge then aimed at the
        dreamer it had killed hours earlier, which the write boundary rejects
        and a pure react cannot survive.
        """
        seeded: set[int] = set()
        for key, checkpoint in point_journal(self.state).entries():
            if key.round_number != self.state.round_number:
                continue
            if key.point not in (
                SchedulePoint.DAWN_REACTION, SchedulePoint.NIGHT_COMMIT,
            ):
                continue
            for event in checkpoint.events:
                if not isinstance(event, Mapping):
                    continue
                if event.get("event_type") != "PLAYER_DIED":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                seat = payload.get("target_seat")
                if type(seat) is not int:
                    seat = payload.get("seat")
                if type(seat) is int:
                    seeded.add(seat)
        return seeded

    @staticmethod
    def _reaction_wave_slot(slot_prefix: str, seats: tuple[int, ...]) -> str:
        """Deterministic slot token for one wave.

        The slot is derived from the sorted seat set, not from a counter, so a
        resumed run asks the journal for exactly the same window instead of
        opening a second one for the same deaths. ``Scheduler._slot`` accepts
        letters, digits, ``_ . : -``; the caller supplies a letter-leading
        prefix and the seats are joined with dots.
        """
        return f"{slot_prefix}-{'.'.join(str(seat) for seat in seats)}"

    async def _run_reaction_waves(
        self, *, slot_prefix: str,
    ) -> tuple[DeathReport, ...]:
        """React to deaths that only a previous reaction window produced.

        A response window settles its damage immediately
        (``_settle_and_publish``), so a death reaction can kill the holder of a
        gifted claw; that death used to be published and then forgotten. Each
        loop below seeds every current-round death no finished window has seen
        into its own ``DAWN_REACTION`` point, settles what the window deals, and
        repeats until nothing new lands. The cap turns a pathological chain into
        a recorded fault rather than a hang.
        """
        settled_total: list[DeathReport] = []
        for _ in range(_MAX_REACTION_WAVES):
            seeded = self._reaction_seeded_seats()
            deaths = tuple(sorted(
                (
                    death for death in self.state.death_history
                    if death.round_number == self.state.round_number
                    and death.player_seat not in seeded
                ),
                key=lambda death: death.player_seat,
            ))
            if not deaths:
                return tuple(settled_total)
            seats = tuple(sorted({death.player_seat for death in deaths}))
            slot = self._reaction_wave_slot(slot_prefix, seats)
            commit = self._synthetic_commit(
                f"reaction:{slot}",
                tuple(self._died_event(death) for death in deaths),
            )
            await self._run_response_point(
                SchedulePoint.DAWN_REACTION, commit, slot=slot,
            )
            settled = await self._settle_and_publish()
            settled_total.extend(settled)
            await self._durable_checkpoint(
                f"reaction_wave:{self.state.round_number}:{slot}:"
                f"{self._seat_list(settled)}"
            )
        leftover = tuple(sorted(
            (
                death for death in self.state.death_history
                if death.round_number == self.state.round_number
                and death.player_seat not in self._reaction_seeded_seats()
            ),
            key=lambda death: death.player_seat,
        ))
        if leftover:
            self._record_reaction_wave_overflow(leftover)
        return tuple(settled_total)

    def _record_reaction_wave_overflow(
        self, deaths: tuple[DeathReport, ...],
    ) -> None:
        """Announce a chain the wave cap stopped, instead of dropping it silently."""
        seats = ",".join(str(death.player_seat) for death in deaths)
        logger.error(
            "Reaction wave cap reached: game=%s round=%s wave_cap=%d pending=%s",
            self.game_id, self.state.round_number, _MAX_REACTION_WAVES, seats,
        )
        try:
            self.game_logger.log_operation(
                self.game_id, "stage_telemetry", self.state.round_number,
                self.state.phase.value,
                data={
                    "scope": "reaction",
                    "stage": SchedulePoint.DAWN_REACTION.value,
                    "code": "reaction_wave_overflow",
                    "wave_cap": _MAX_REACTION_WAVES,
                    "pending_seats": [death.player_seat for death in deaths],
                },
            )
        except Exception:
            logger.exception(
                "Failed to persist reaction wave overflow for game=%s", self.game_id,
            )

    def _pending_win_result(self, pending: "_PendingWin") -> WinResult:
        """Rebuild the published result from the pending camp/reason snapshot.

        ``_PendingWin`` is persisted with an exact field set, so the per-seat
        winners are derived here instead of being carried through the checkpoint.
        """
        return WinResult(
            pending.winning_camp, pending.reason,
            winners=winner_seats(self.state, pending.winning_camp),
        )

    @staticmethod
    def _settlement_deaths(settlement: CommitResult) -> tuple[DeathReport, ...]:
        return tuple(
            DeathReport(event["payload"]["seat"], event["payload"]["cause"],
                        event["payload"]["round_number"])
            for event in settlement.events
            if event["event_type"] == "PLAYER_DIED"
        )

    async def _resolve_delayed_deaths(self) -> tuple[DeathReport, ...]:
        """Finalise the deaths a delayable seat was spared earlier.

        A role marks itself delayable and the settlement then reports a lethal
        non-wolf hit as pending instead of killing the seat; the death lands
        here, once the day's speeches are over and before the exile vote starts.
        Role-agnostic: the engine reads only the generic statuses.
        """
        runtime = getattr(self.state, "_pipeline_runtime", None)
        if runtime is None:
            return ()
        deaths, cleared = resolve_delayed_deaths(
            runtime.statuses, self.state.round_number,
        )
        if not deaths:
            return ()
        reports = tuple(
            DeathReport(death["seat"], death["cause"], death["round_number"])
            for death in deaths
        )
        for seat, statuses in cleared.items():
            # ``set``, not ``frozenset``: the runtime declares plain sets for its
            # statuses and the next clone would reject an immutable variant.
            runtime.statuses[seat] = set(runtime.statuses[seat]) - set(statuses)
            self.state.players[seat].is_alive = False
        runtime.revision += 1
        self.state.death_history.extend(reports)
        await self._publish_deaths(reports)
        self._announce_delayed_deaths(reports)
        # A delayed death is announced by no other step, so it names its own.
        for death in reports:
            await self._durable_checkpoint(
                f"delayed_death:{death.round_number}:{death.player_seat}"
            )
        return reports

    def _announce_delayed_deaths(self, deaths: tuple[DeathReport, ...]) -> None:
        """Tell the table, in the public record, that these seats just left.

        The night batch (``_publish_pending_night_deaths``), a day interruption
        and an exile each write their own announcement; a death that lands after
        the speeches on a delayable seat wrote none, so the public history jumped
        from "everyone vote 2号" straight to a ballot that no longer listed 2号,
        and the players could only tell by spotting the seat in the authoritative
        dead list. No cause is named, deliberately: the marks that schedule such a
        death stay private, and naming one would hand the table the identity of
        whoever left it.
        """
        seats = "、".join(f"{death.player_seat}号" for death in deaths)
        self.conversation_log.add_system_message(
            f"{seats}玩家出局。", self.state.round_number, "public",
        )

    async def _publish_deaths(self, deaths: tuple[DeathReport, ...]) -> None:
        for death in deaths:
            await self.event_bus.publish(BusEvent.PLAYER_DIED, game_id=self.game_id, death=death)
            self._reveal_on_death(death.player_seat)
        self.game_logger.log_deaths(
            self.game_id, self.state.round_number,
            [death.to_dict() for death in deaths],
            phase=self.state.phase.value,
        )

    async def _settle_and_publish(self) -> tuple[DeathReport, ...]:
        """Settle reaction damage immediately (it must never linger into the
        next night) and announce the resulting deaths."""
        settlement = self._pipeline_scheduler.settle_pending(self.state)
        if settlement is None:
            # Nothing settled: the window's own events already went out through
            # their ``day_point:`` step, and the slot must not repeat them.
            self._pending_point_events = ()
            return ()
        self._announce_settlement(settlement)
        deaths = self._settlement_deaths(settlement)
        await self._publish_deaths(deaths)
        return deaths

    @staticmethod
    def _seat_list(deaths: tuple[DeathReport, ...]) -> str:
        return "-".join(str(death.player_seat) for death in deaths)

    async def _run_exile_reaction(self, exiled_seat: int) -> tuple[DeathReport, ...]:
        """Run the exile's response window; returns the deaths it settled.

        The exiled seat itself is not among them (it is marked dead directly),
        which is why the caller names it in the announcing step as well. Deaths
        the window *causes* (a death reaction) are followed by their own waves,
        so a claw holder killed by such a reaction can still answer.
        """
        await self._run_response_point(
            SchedulePoint.DAWN_REACTION, self._exile_commit(exiled_seat),
        )
        settled = await self._settle_and_publish()
        return settled + await self._run_reaction_waves(
            slot_prefix="exile_react",
        )

    def _reveal_from_verdict(self, seat: int) -> None:
        """A role publicly flipped its card to survive the vote."""
        player = self.state.players.get(seat)
        if player is None:
            return
        player.revealed_role = player.role
        try:
            display_name = builtin_registry.freeze().specs[player.role].display_name
        except KeyError:
            display_name = player.role
        self.conversation_log.add_system_message(
            f"{seat}号玩家被投票出局后翻牌亮出身份：{display_name}，免于出局，"
            "但失去投票权。",
            self.state.round_number, "public",
        )

    async def _apply_exile(self, exiled_seat: int) -> bool:
        """Carry out the vote verdict against ``exiled_seat``.

        The EXILE_VERDICT response window runs first; a role that emits
        EXILE_CANCELLED keeps the seat alive (returns True). Otherwise the
        seat is exiled, reactive roles respond, and last words follow.
        """
        player = self.state.players.get(exiled_seat)
        already_exiled = any(
            death.player_seat == exiled_seat and death.cause == "exile"
            and death.round_number == self.state.round_number
            for death in self.state.death_history
        )
        if player is None or not (player.is_alive or already_exiled):
            return False
        if not already_exiled:
            verdict = await self._run_response_point(
                SchedulePoint.EXILE_VERDICT, self._exile_pending_commit(exiled_seat),
            )
            verdict_events = self._pipeline_audience_events(verdict)
            if any(event_type == "EXILE_CANCELLED" for event_type, _ in verdict_events):
                self._reveal_from_verdict(exiled_seat)
                await self._durable_checkpoint(
                    f"exile_cancelled:{self.state.round_number}:{self.state.vote_round}:{exiled_seat}"
                )
                return True
            player.mark_dead("exile")
            self.state.death_history.append(DeathReport(
                player_seat=exiled_seat, cause="exile",
                round_number=self.state.round_number,
            ))
        self._reveal_on_death(exiled_seat)
        reaction = await self._run_exile_reaction(exiled_seat)
        # The step names every seat this window killed, so the announcement is
        # cause-agnostic: a charmed victim dragged along by a wolf beauty dies
        # with cause ``charm`` and would be filtered out by a cause whitelist.
        killed = "-".join(
            str(seat) for seat in sorted(
                {exiled_seat, *(death.player_seat for death in reaction)}
            )
        )
        await self._durable_checkpoint(
            f"exile_reaction:{self.state.round_number}:{exiled_seat}:{killed}"
        )
        await self._maybe_reassign_badge()
        await self.give_last_words(exiled_seat, "exile", self.state.round_number)
        return False

    # =================================================================
    # Day Operation Functions
    # =================================================================

    async def announce_actor(self, seat: int) -> None:
        """Tell viewers which seat is generating a night action."""
        await self.event_bus.publish(
            BusEvent.ACTING, game_id=self.game_id, seat=seat,
        )

    def announce_actor_blocking(self, seat: int) -> None:
        """Publish the acting seat from the pipeline thread before the model call.

        The night point holds the state lock across the model call, so this
        must not checkpoint. A loop-thread caller only schedules the publish;
        waiting here would deadlock the loop.
        """
        loop = self._actor_loop
        if loop is None or not loop.is_running():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(self.announce_actor(seat))
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.announce_actor(seat), loop,
            ).result(timeout=5)
        except Exception:
            logger.warning(
                "Failed to announce night actor seat=%s game=%s",
                seat, self.game_id, exc_info=True,
            )

    async def speak(self, seat: int, context: str) -> Optional[str]:
        """Generate speech for a player. Returns the speech text or None.

        Guarantees a non-empty string is returned for any living player in a
        valid speech context — if the role fails to generate speech for any
        reason, an emergency fallback is produced inline so the player is
        never silently skipped. Only returns None when the player truly
        cannot speak (no role found, dead, wrong phase).
        """
        role = self.roles.get(seat)
        if role is None:
            logger.warning(f"No role found for seat {seat} during speak() — skipping")
            return None
        self._guard_turn(seat)
        # Announce before the model call. The speech text is published only
        # after this returns, which is too late for the seat that is talking.
        self.state.current_speaker = seat
        await self._durable_checkpoint(
            f"speaking:{self.state.round_number}:{seat}:{context}"
        )
        await self.event_bus.publish(
            BusEvent.SPEAKING, game_id=self.game_id, seat=seat,
        )
        try:
            async def on_speech_chunk(text: str, kind: str = "speech") -> None:
                # Thinking is a live-only hint: it never reaches a checkpoint,
                # the conversation log, or any player's prompt.
                event = (
                    BusEvent.SPEECH_THINKING if kind == "thinking"
                    else BusEvent.SPEECH_STREAM
                )
                await self.event_bus.publish(
                    event, game_id=self.game_id, seat=seat, text=text,
                )

            try:
                try:
                    result = await role.speak(
                        self.state, self.conversation_log, context, on_chunk=on_speech_chunk,
                    )
                except TypeError as e:
                    if "on_chunk" in str(e):
                        result = await role.speak(self.state, self.conversation_log, context)
                    else:
                        raise
            except Exception as e:
                logger.error(f"Speech error (seat={seat}, context={context}): {e}", exc_info=True)
                result = self._emergency_speech(seat, context)

            if result is None:
                return None
            if not result:
                logger.warning(
                    f"Seat {seat}: role.speak() returned empty/None for context={context}. "
                    f"Generating emergency fallback speech to prevent silent skip."
                )
                result = self._emergency_speech(seat, context)
            return result
        finally:
            self.state.current_speaker = None

    def _emergency_speech(self, seat: int, context: str) -> str:
        """Last-resort speech when the role's LLM completely fails.
        Ensures the player is never silently dropped from the conversation.

        Like the role-level fallback this names the seat only: splicing in the
        role's display name leaked the identity of every player whose speech
        call failed outright, in games whose configuration keeps roles secret.
        """
        if context == "last_words":
            return (
                f"我是{seat}号，我已经出局了。"
                f"希望好人能仔细分析场上局势，找出狼人。"
            )

        # Day speech: reference another alive player as plausible content
        alive = [s for s in self.state.alive_players() if s != seat]
        if alive:
            suspect = self._rng.choice(alive)
            return (
                f"我是{seat}号，我目前比较关注{suspect}号玩家的发言。"
                f"前面几位的发言我都认真听了，"
                f"我会结合所有信息在投票时做出判断。"
            )
        return (
            f"我是{seat}号，现在场上人数很少了，"
            f"我需要仔细分析之前的发言，慎重做出今天的决定。"
        )

    async def vote(self, seat: int) -> Optional[VoteAction]:
        """Player casts a vote, using an explicit technical abstention on failure."""
        role = self.roles.get(seat)
        player = self.state.players.get(seat)
        if role is None or player is None:
            return None
        self._guard_turn(seat)
        if self._active_vote_window_id is None:
            window = self._vote_service.open_window(
                timeout_seconds=float(self._vote_phase_timeout_seconds),
            )
            self._active_vote_window_id = window.window_id
        window_id = self._active_vote_window_id
        request = ActionRequest(
            actor_seat=seat,
            role_id=player.role,
            contract=VOTE_CONTRACT,
            phase=GamePhase.VOTE_CASTING,
            round_id=self.state.round_number,
            idempotency_key=self._vote_service.window(window_id).action_key(seat),
        )
        try:
            accepted = await role.request_action(
                self.state, self.conversation_log, request
            )
            received_at = time.monotonic()
            if accepted.technical_failure_code is not None:
                receipt = self._vote_service.technical_abstain(
                    window_id, seat,
                    failure_code=accepted.technical_failure_code,
                    timeout_type=accepted.timeout_type,
                )
            else:
                receipt = self._vote_service.submit(
                    window_id, seat,
                    CastVoteArgs(
                        action_type=accepted.command.action_type,
                        target_seat=accepted.command.target_seat,
                        reasoning=accepted.command.reasoning,
                    ),
                    received_at=received_at,
                )
            return VoteAction(
                voter_seat=seat,
                target_seat=receipt.target_seat,
                reasoning=accepted.command.reasoning,
            )
        except Exception as error:
            failure_code = (
                error.code if isinstance(error, VoteError) else "invoke_error"
            )
            logger.error(
                "Vote action failed (seat=%s, failure_code=%s)",
                seat, failure_code,
            )
            self.game_logger.log_vote_technical_abstain(
                self.game_id, self.state.round_number, seat,
                failure_code=failure_code, window_id=request.idempotency_key,
            )
            existing = {
                item.voter_seat: item
                for item in self._vote_service.receipts(window_id)
            }.get(seat)
            if existing is None:
                receipt = self._vote_service.technical_abstain(
                    window_id, seat, failure_code=failure_code,
                )
            else:
                receipt = existing
            return VoteAction(
                seat, receipt.target_seat, f"technical abstain: {failure_code}",
            )

    async def _check_game_over(self) -> bool:
        """Check win conditions. If game is over, handle cleanup and broadcast. Returns True if over."""
        win_result = self.rule_engine.check_win(self.state)
        if win_result:
            self.state.win_result = win_result.to_dict()
            self.state.phase = GamePhase.GAME_OVER
            self.sm.set_state(GamePhase.GAME_OVER)
            self.game_logger.log_game_over(
                self.game_id, self.state.round_number,
                win_result.winning_camp, win_result.reason,
                winners=win_result.winners,
            )
            await self.event_bus.publish(
                BusEvent.GAME_OVER, game_id=self.game_id, win_result=win_result,
            )
            return True
        return False

    def resolve_votes(self, *, log: bool = True) -> Optional[int]:
        """Tally votes. Returns exiled seat, or None on tie/abstain.

        ``log=False`` defers the ``vote_result`` log frame so a verdict window
        may still overturn the exile before the outcome is recorded.
        """
        tally = self._tally_votes()
        exiled_seat: Optional[int] = None
        if tally:
            max_votes = max(tally.values())
            top = [s for s, c in tally.items() if c == max_votes]
            exiled_seat = top[0] if len(top) == 1 else None
        if log:
            self._log_vote_result(exiled_seat, tally)
        return exiled_seat

    def _log_vote_result(
        self, exiled_seat: Optional[int], tally: Optional[dict[int, int]] = None,
    ) -> None:
        self.game_logger.log_vote_result(
            self.game_id, self.state.round_number, exiled_seat,
            self._tally_votes() if tally is None else tally,
        )

    def _tally_votes(self) -> dict[int, int]:
        if self._active_vote_window_id is not None:
            return self._vote_service.tally(self._active_vote_window_id)
        tally: dict[int, int] = {}
        for vote in self.state.votes:
            if vote.target_seat is not None:
                tally[vote.target_seat] = (
                    tally.get(vote.target_seat, 0) + ballot_weight(self.state, vote.voter_seat)
                )
        return tally

    def vote_ballot_weight(self, voter_seat: int) -> int:
        """Half-vote weight of one ballot as this window fixed it.

        With a live window this reads the sheriff snapshot taken when the
        ballot opened, not the current office: a death reaction can kill the
        sheriff after the votes are in, and that must not rewrite the
        published ``VOTE_RESULT.weighted_counts``. Without a window (legacy
        games, a vote tallied before the window exists) it keeps the historical
        live-office behaviour.
        """
        if self._active_vote_window_id is not None:
            return self._vote_service.window_weight(
                self._active_vote_window_id, voter_seat,
            )
        return ballot_weight(self.state, voter_seat)

    @staticmethod
    def _tied_top_candidates(tally: dict[int, int]) -> list[int]:
        if not tally:
            return []
        max_votes = max(tally.values())
        top = [seat for seat, count in tally.items() if count == max_votes]
        return top if len(top) >= 2 else []

    def _clear_tiebreak_state(self) -> None:
        self.state.vote_round = 1
        self.state.is_tiebreak = False
        self.state.tiebreak_candidates.clear()
        self.state.supplemental_speakers.clear()
        self.state.voted_seats.clear()
        self._active_vote_window_id = None

    # =================================================================
    # Helpers
    # =================================================================

    async def _broadcast_phase_change(self, *, checkpoint: bool = True) -> None:
        self.state.phase = self.sm.get_state()
        if self._fault_injector is not None:
            self._fault_injector("after_phase_transition")
        self.game_logger.log_phase_change(
            self.game_id, self.state.phase.value, self.state.round_number,
        )
        await self.event_bus.publish(
            BusEvent.PHASE_CHANGED, game_id=self.game_id, phase=self.state.phase.value,
            round_number=self.state.round_number, state=self.state,
        )
        if checkpoint:
            await self._durable_checkpoint(
                f"phase:{self.state.round_number}:{self.state.phase.value}:vote:{self.state.vote_round}"
            )
