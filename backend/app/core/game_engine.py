from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Optional

from app.models.game import GameState, GamePhase, GameConfig, PlayerState
from app.models.actions import VoteAction, SpeechRecord, DeathReport, WinResult
from app.models.contracts import ActionContract, ActionRequest
from app.core.state_machine import GameStateMachine, GameEvent as SM_Event
from app.core.rule_engine import RuleEngine
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.night_flow import WolfVote, build_briefing
from app.core.conversation_log import ConversationLog
from app.core.game_logger import GameLogger
from app.roles.registry import builtin_registry
from app.config import PipelineMode
from app.core.effect_applier import CommitResult
from app.core.point_journal import PendingEvent, PointCheckpoint, PointKey, WorkCursor, point_journal
from app.core.role_pipeline import PipelineResult, RolePipeline
from app.core.scheduler import PipelinePaused, PointResult
from app.models.pipeline import SchedulePoint

logger = logging.getLogger(__name__)

VOTE_CONTRACT = ActionContract(
    contract_id="exile_vote",
    phase=GamePhase.VOTE_CASTING,
    action_types=("vote", "abstain"),
    actions_requiring_target=frozenset({"vote"}),
    resolution_priority=0,
    fallback_action_type="abstain",
)

_EVENT_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")

_NIGHT_POINTS = (
    SchedulePoint.NIGHT_WOLF_VOTE,
    SchedulePoint.NIGHT_WITCH_ACTION,
    SchedulePoint.NIGHT_SEER_ACTION,
    SchedulePoint.NIGHT_COMMIT,
)

# Staged night batch stages at which each pipeline point executes; a batch
# with `stage > point_stage` must carry that point's result in raw_results.
_NIGHT_POINT_STAGES = (3, 5, 7, 8)


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
        if type(self.stage) is not int or not 0 <= self.stage <= 9: raise ValueError("invalid completion stage")
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
    ):
        self.game_id = game_id or str(uuid.uuid4())[:8]
        self.config = config or GameConfig()
        self.sm = GameStateMachine()
        self.rule_engine = RuleEngine()
        self.event_bus = event_bus or EventBus()
        self.roles = roles or {}
        self.memory_service = memory_service
        self.game_logger = GameLogger(data_dir=data_dir)
        self.conversation_log = ConversationLog(logger=self.game_logger, game_id=self.game_id)
        self.state = GameState(game_id=self.game_id, config=self.config)
        self._phase_delay: float = 2.0
        self._running = False
        self._paused = False
        self._last_words_given: set[tuple[int, int]] = set()
        self._pipeline_scheduler = pipeline_scheduler
        self._director = director
        self._pending_night_completion: _PendingNightCompletion | None = None
        self._pending_night_batch: _PendingNightBatch | None = None
        self._night_task: asyncio.Task | None = None

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
        pipeline = RolePipeline(PipelineMode.V2, None, self._pipeline_scheduler)
        result = await asyncio.to_thread(pipeline.run_points, self.state, points)
        if type(result) is not PipelineResult: raise TypeError("pipeline must return exact PipelineResult")
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
        if self._night_task is not None and not self._night_task.done():
            raise ValueError("night execution is active")
        self._night_task = None
        self._running = True
        self.sm.reset()
        self.state = GameState(game_id=self.game_id, config=self.config)
        self.conversation_log = ConversationLog(logger=self.game_logger, game_id=self.game_id)
        self._pending_night_completion = None
        self._pending_night_batch = None

        self.sm.transition(SM_Event.START)
        await self._broadcast_phase_change()

        self._assign_roles()
        self.sm.transition(SM_Event.ROLES_ASSIGNED)
        await self._broadcast_phase_change()

        await self._game_loop()

    async def stop(self) -> None:
        self._running = False

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

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
        self.game_logger.log_role_init(self.game_id, players_dict)

    # =================================================================
    # Main Loop
    # =================================================================

    async def _game_loop(self) -> None:
        while self._running and not self.sm.is_terminal():
            await self._wait_if_paused()
            phase = self.sm.get_state()

            if phase == GamePhase.NIGHT:
                await self._execute_night()
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

    async def _log_stage_audience(self, result: PointResult) -> None:
        observation = RolePipeline.observe_v2(result)
        self._log_audience_events(
            PipelineResult((), (), observation.state_digest, observation.public_events, PipelineMode.V2),
            "night",
        )

    async def _execute_staged_night(self) -> None:
        pending = self._pending_night_batch
        director = self._director
        state = self.state
        wolves = [
            seat for seat in sorted(state.players)
            if state.players[seat].role == "wolf-killer-werewolf" and state.players[seat].is_alive
        ]

        if pending.stage == 0:
            title, text = director.narration("wolf_open")
            await self._narrate(title, text)
            pending = replace(pending, stage=1); self._pending_night_batch = pending

        if pending.stage == 1:
            if wolves:
                history = list(pending.discussion_history)
                leads = dict(pending.discussion_leads)
                max_turns = 3 * len(wolves)
                while len(history) < max_turns:
                    seat = wolves[len(history) % len(wolves)]
                    briefing = build_briefing(self.conversation_log, seat, self.state.round_number)
                    result = await asyncio.to_thread(director.wolf_discussion_turn, state, seat, tuple(history), briefing)
                    if result.spoke:
                        history.append(f"{seat}号：{result.text}")
                        if result.preferred_target is not None:
                            leads[seat] = result.preferred_target
                        self.game_logger.log_audience_action(
                            self.game_id, state.round_number, "night",
                            "WOLF_CHAT_MESSAGE", {"seat": seat, "text": result.text},
                        )
                    else:
                        history.append(f"{seat}号：（跳过）")
                    pending = replace(pending, discussion_history=tuple(history),
                                      discussion_leads=tuple(sorted(leads.items())))
                    self._pending_night_batch = pending
                    if (len(history) >= len(wolves)
                            and all(line.endswith("（跳过）") for line in history[-len(wolves):])):
                        break
                    if _discussion_consensus(wolves, history, leads):
                        break
            pending = replace(pending, stage=2); self._pending_night_batch = pending

        if pending.stage == 2:
            if wolves:
                votes = list(pending.wolf_votes)
                discussion = tuple(line for line in pending.discussion_history if not line.endswith("（跳过）"))
                for seat in wolves[len(votes):]:
                    briefing = build_briefing(self.conversation_log, seat, self.state.round_number)
                    result = await asyncio.to_thread(director.wolf_vote_turn, state, seat, discussion, tuple(votes), briefing)
                    votes.append(result)
                    self.game_logger.log_audience_action(
                        self.game_id, state.round_number, "night", "WOLF_VOTE",
                        {"seat": seat, "target_seat": result.target_seat, "reasoning": result.reasoning},
                    )
                    pending = replace(pending, wolf_votes=tuple(votes)); self._pending_night_batch = pending
                director.record_votes(tuple(votes))
            else:
                director.record_votes(())
            pending = replace(pending, stage=3); self._pending_night_batch = pending

        if pending.stage == 3:
            raw = await self._execute_v2_point(_NIGHT_POINTS[0])
            await self._log_stage_audience(raw)
            runtime = getattr(state, "_pipeline_runtime", None)
            if runtime is not None:
                state.last_wolf_kill_target = _wolf_kill_target(tuple(runtime.pending_damage))
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=4)
            self._pending_night_batch = pending

        if pending.stage == 4:
            title, text = director.narration("witch_open")
            await self._narrate(title, text)
            pending = replace(pending, stage=5); self._pending_night_batch = pending

        if pending.stage == 5:
            raw = await self._execute_v2_point(_NIGHT_POINTS[1])
            await self._log_stage_audience(raw)
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=6)
            self._pending_night_batch = pending

        if pending.stage == 6:
            title, text = director.narration("seer_open")
            await self._narrate(title, text)
            pending = replace(pending, stage=7); self._pending_night_batch = pending

        if pending.stage == 7:
            raw = await self._execute_v2_point(_NIGHT_POINTS[2])
            await self._log_stage_audience(raw)
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=8)
            self._pending_night_batch = pending

        if pending.stage == 8:
            raw = await self._execute_v2_point(_NIGHT_POINTS[3])
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=9)
            self._pending_night_batch = pending
            await self._log_stage_audience(raw)

        if pending.stage == 9:
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

    async def _execute_v2_point(self, point: SchedulePoint) -> PointResult:
        pipeline = RolePipeline(PipelineMode.V2, None, self._pipeline_scheduler)
        return await asyncio.to_thread(pipeline.execute_v2_point, self.state, point)

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
        return tuple(deaths)

    def _pipeline_audience_events(self, result: PipelineResult) -> tuple[tuple[str, object], ...]:
        """Collect validated non-death PUBLIC pipeline events for the audience log."""
        events = []
        try:
            for event in result.public_events:
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

    def _log_audience_events(self, result: PipelineResult, phase: str) -> None:
        for event_type, payload in self._pipeline_audience_events(result):
            self.game_logger.log_audience_action(
                self.game_id, self.state.round_number, phase, event_type, payload,
            )
            thought = payload.get("thought")
            seat = payload.get("seat")
            if type(thought) is str and type(seat) is int and seat > 0:
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

    def get_night_deaths(self) -> list[DeathReport]:
        """Get all deaths from the current round."""
        return [d for d in self.state.death_history if d.round_number == self.state.round_number]

    async def _resume_pipeline_night(self) -> None:
        pending = self._pending_night_completion
        if pending.deaths is None:
            pending = replace(pending, deaths=self._pipeline_night_deaths(pending.result))
            self._pending_night_completion = pending
        while pending.event_cursor < len(pending.deaths):
            snapshot = pending.deaths[pending.event_cursor]
            await self.event_bus.publish(BusEvent.PLAYER_DIED, game_id=self.game_id,
                death=DeathReport(snapshot.seat, snapshot.cause, snapshot.round_number))
            pending = replace(pending, event_cursor=pending.event_cursor + 1); self._pending_night_completion = pending
        if pending.stage == 0:
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
            pending = replace(pending, stage=3); self._pending_night_completion = pending
        if pending.stage == 3:
            if pending.win_result is not None:
                self.state.win_result = WinResult(pending.win_result.winning_camp, pending.win_result.reason).to_dict(); self.state.phase = GamePhase.GAME_OVER
                self.sm.set_state(GamePhase.GAME_OVER)
            pending = replace(pending, stage=4); self._pending_night_completion = pending
        if pending.stage == 4:
            if pending.win_result is not None:
                self.game_logger.log_game_over(self.game_id, self.state.round_number,
                    pending.win_result.winning_camp, pending.win_result.reason)
            pending = replace(pending, stage=5); self._pending_night_completion = pending
        if pending.stage == 5:
            if pending.win_result is not None:
                await self.event_bus.publish(BusEvent.GAME_OVER, game_id=self.game_id,
                    win_result=WinResult(pending.win_result.winning_camp, pending.win_result.reason))
            pending = replace(pending, stage=6); self._pending_night_completion = pending
        if pending.stage == 6:
            if pending.win_result is None and self.sm.get_state() is not GamePhase.DAWN:
                try: self.sm.transition(SM_Event.NIGHT_ACTIONS_COMPLETE)
                except BaseException:
                    if self.sm.get_state() is GamePhase.DAWN:
                        self.state.phase = GamePhase.DAWN
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
            self._pending_night_completion = None

    async def give_last_words(self, seat: int, cause: str, death_round: int) -> Optional[str]:
        """Generate last words for a dying player. Standalone function with validation.

        Eligibility:
        - First-night deaths (any night cause) in round 1
        - Vote-exiled players (any round)

        Validation:
        - Player must exist and not have already given last words
        - Death cause must be eligible
        """
        is_first_night_death = cause != "exile" and death_round == 1
        is_exile = cause == "exile"
        if not (is_first_night_death or is_exile):
            return None

        death_key = (seat, death_round, cause)
        if death_key in self._last_words_given:
            return None

        player = self.state.players.get(seat)
        if player is None:
            return None

        self._last_words_given.add(death_key)

        speech_text = await self.speak(seat, "last_words")
        if speech_text:
            self.state.speeches.append(SpeechRecord(
                player_seat=seat, text=speech_text,
                round_number=self.state.round_number,
            ))
            self.conversation_log.add_public_speech(
                seat, player.role, speech_text,
                self.state.round_number, "last_words",
            )
            await self.event_bus.publish(
                BusEvent.SPEECH_MADE,
                game_id=self.game_id,
                speech=SpeechRecord(
                    player_seat=seat, text=speech_text,
                    round_number=self.state.round_number,
                ),
            )
            self.game_logger.log_speech(
                self.game_id, self.state.round_number, "last_words",
                seat, speech_text,
            )

        return speech_text

    # =================================================================
    # Dawn Phase
    # =================================================================

    async def _execute_dawn(self) -> None:
        await asyncio.sleep(self._phase_delay * 0.5)

        round_deaths = self.get_night_deaths()
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

    async def _execute_speech_round(self) -> None:
        alive = list(self.state.alive_players().items())
        if self.state.is_tiebreak:
            alive = [
                (seat, player)
                for seat, player in alive
                if seat not in self.state.supplemental_speakers
            ]
        self.state.speaking_order = [s for s, _ in alive]
        for seat, player in alive:
            self.state.current_speaker = seat
            speech_text = await self.speak(seat, "day_speech")
            if speech_text:
                self.state.speeches.append(SpeechRecord(
                    player_seat=seat, text=speech_text,
                    round_number=self.state.round_number,
                ))
                self.conversation_log.add_public_speech(
                    seat, player.role, speech_text,
                    self.state.round_number, "speech",
                )
                await self.event_bus.publish(
                    BusEvent.SPEECH_MADE,
                    game_id=self.game_id,
                    speech=SpeechRecord(
                        player_seat=seat, text=speech_text,
                        round_number=self.state.round_number,
                    ),
                )
                self.game_logger.log_speech(
                    self.game_id, self.state.round_number, "speech", seat, speech_text,
                )
                if self.state.is_tiebreak:
                    self.state.supplemental_speakers.add(seat)
            else:
                logger.warning(
                    f"Seat {seat}: speak() returned None/empty in speech round "
                    f"(alive={player.is_alive}, phase={self.state.phase.value}). "
                    f"This should not happen for a living player in speech phase."
                )

        self.state.current_speaker = None
        self.state.speaking_order = []
        self.sm.transition(SM_Event.SPEECHES_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Vote Casting Phase
    # =================================================================

    async def _execute_vote_casting(self) -> None:
        if not self.state.voted_seats:
            self.state.votes.clear()
        for seat in self.state.alive_players():
            if seat in self.state.voted_seats:
                continue
            vote = await self.vote(seat)
            if vote:
                self.state.votes.append(vote)
                self.state.voted_seats.add(seat)
                self.game_logger.log_vote(
                    self.game_id, self.state.round_number, seat, vote.target_seat,
                )
                await self.event_bus.publish(
                    BusEvent.VOTE_CAST, game_id=self.game_id, vote=vote,
                )

        self.sm.transition(SM_Event.VOTES_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Vote Resolution Phase
    # =================================================================

    async def _execute_tiebreak(self, candidates: list[int]) -> None:
        """Run exactly one persisted supplemental-speech and re-vote round."""
        if not self.state.is_tiebreak:
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

        alive_seats = set(self.state.alive_players())
        if alive_seats - self.state.supplemental_speakers:
            self.sm.set_state(GamePhase.SPEECH)
            await self._broadcast_phase_change()
            await self._execute_speech_round()

        if alive_seats - self.state.voted_seats:
            self.sm.set_state(GamePhase.VOTE_CASTING)
            await self._broadcast_phase_change()
            await self._execute_vote_casting()
        else:
            self.sm.set_state(GamePhase.VOTE_RESOLUTION)

        exiled_seat = self.resolve_votes()
        if exiled_seat is not None:
            player = self.state.players.get(exiled_seat)
            if player:
                player.mark_dead("exile")
                self.state.death_history.append(DeathReport(
                    player_seat=exiled_seat, cause="exile",
                    round_number=self.state.round_number,
                ))
                await self._run_exile_reaction(exiled_seat)
                await self.give_last_words(
                    exiled_seat, "exile", self.state.round_number,
                )

        self.conversation_log.add_vote_result(
            self.state.votes, exiled_seat, self.state.round_number,
        )
        self._clear_tiebreak_state()
        if not await self._check_game_over():
            self.sm.transition(SM_Event.VOTE_RESOLVED)
        await self._broadcast_phase_change()

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
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
            )
            self._clear_tiebreak_state()
            if not await self._check_game_over():
                self.sm.transition(SM_Event.VOTE_RESOLVED)
            await self._broadcast_phase_change()
            return

        exiled_seat = self.resolve_votes()

        if exiled_seat is not None:
            player = self.state.players.get(exiled_seat)
            if player:
                player.mark_dead("exile")
                self.state.death_history.append(DeathReport(
                    player_seat=exiled_seat, cause="exile",
                    round_number=self.state.round_number,
                ))
                await self._run_exile_reaction(exiled_seat)
                await self.give_last_words(exiled_seat, "exile", self.state.round_number)

        # Reset this round's votes and casting bookkeeping so the next round
        # starts with an empty ballot instead of re-exiling the same seat.
        self._clear_tiebreak_state()

        # Announce vote result
        if exiled_seat is not None:
            self.conversation_log.add_vote_result(
                self.state.votes, exiled_seat, self.state.round_number,
            )
        else:
            tie_final = "补充投票仍为平票，无人被放逐。"
            self.conversation_log.add_system_message(tie_final, self.state.round_number, "public")
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
            )

        if not await self._check_game_over():
            self.sm.transition(SM_Event.VOTE_RESOLVED)

        await self._broadcast_phase_change()

    # =================================================================
    # Exile Reaction (pipeline response windows)
    # =================================================================

    def _exile_commit(self, exiled_seat: int) -> CommitResult:
        """Build a synthetic committed event announcing the exile so the
        pipeline's response windows can react to it without engine-side
        knowledge of any specific role."""
        event = {
            "event_type": "PLAYER_DIED",
            "payload": {
                "target_seat": exiled_seat,
                "cause": "exile",
                "round_number": self.state.round_number,
            },
            "visibility": ("PUBLIC",),
        }
        return CommitResult(
            f"vote:{exiled_seat}", (), 0, (event,),
            self._public_state_digest(self.state),
        )

    async def _run_exile_reaction(self, exiled_seat: int) -> None:
        scheduler = self._pipeline_scheduler
        if scheduler is None: raise ValueError("pipeline scheduler is required")
        commit = self._exile_commit(exiled_seat)
        key = PointKey(
            self.state.game_id, self.state.round_number, self.state.phase.value,
            SchedulePoint.DAWN_REACTION, scheduler.registry.digest,
        )
        point_journal(self.state).put(key, PointCheckpoint(
            (), (), (commit,), commit.events, (), (PendingEvent(0, 0, 0),),
            WorkCursor("response", 0, 0), work_count=0,
        ))
        pipeline = RolePipeline(PipelineMode.V2, None, scheduler)
        result = await asyncio.to_thread(pipeline.run_point, self.state, SchedulePoint.DAWN_REACTION)
        self._log_audience_events(result, self.state.phase.value)

    # =================================================================
    # Day Operation Functions
    # =================================================================

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
        try:
            result = await role.speak(self.state, self.conversation_log, context)
        except Exception as e:
            logger.error(f"Speech error (seat={seat}, context={context}): {e}", exc_info=True)
            result = None

        if not result:
            logger.warning(
                f"Seat {seat}: role.speak() returned empty/None for context={context}. "
                f"Generating emergency fallback speech to prevent silent skip."
            )
            result = self._emergency_speech(seat, context)
        return result

    def _emergency_speech(self, seat: int, context: str) -> str:
        """Last-resort speech when the role's LLM completely fails.
        Ensures the player is never silently dropped from the conversation.
        """
        player = self.state.players.get(seat)
        role_cn = "玩家"
        if player:
            try:
                role_cn = builtin_registry.freeze().specs[player.role].display_name
            except KeyError:
                role_cn = "玩家"

        if context == "last_words":
            return (
                f"我是{seat}号{role_cn}，我已经出局了。"
                f"希望好人能仔细分析场上局势，找出狼人。"
            )

        # Day speech: reference another alive player as plausible content
        alive = [s for s in self.state.alive_players() if s != seat]
        if alive:
            import random
            suspect = random.choice(alive)
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
        """Player casts a vote. Returns VoteAction or None."""
        role = self.roles.get(seat)
        player = self.state.players.get(seat)
        if role is None or player is None:
            return None
        request = ActionRequest(
            actor_seat=seat,
            role_id=player.role,
            contract=VOTE_CONTRACT,
            phase=GamePhase.VOTE_CASTING,
            round_id=self.state.round_number,
            idempotency_key=(
                f"{self.state.round_number}:{GamePhase.VOTE_CASTING.value}:"
                f"{self.state.vote_round}:{seat}:{VOTE_CONTRACT.contract_id}"
            ),
        )
        try:
            accepted = await role.request_action(
                self.state, self.conversation_log, request
            )
            return VoteAction(
                voter_seat=seat,
                target_seat=accepted.command.target_seat,
                reasoning=accepted.command.reasoning,
            )
        except Exception as e:
            logger.error(f"Vote error (seat={seat}): {e}", exc_info=True)
            return None

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
            )
            await self.event_bus.publish(
                BusEvent.GAME_OVER, game_id=self.game_id, win_result=win_result,
            )
            return True
        return False

    def resolve_votes(self) -> Optional[int]:
        """Tally votes. Returns exiled seat, or None on tie/abstain."""
        tally = self._tally_votes()

        if not tally:
            self.game_logger.log_vote_result(
                self.game_id, self.state.round_number, None, tally,
            )
            return None

        max_votes = max(tally.values())
        top = [s for s, c in tally.items() if c == max_votes]

        self.game_logger.log_vote_result(
            self.game_id, self.state.round_number,
            top[0] if len(top) == 1 else None,
            tally,
        )

        return top[0] if len(top) == 1 else None

    def _tally_votes(self) -> dict[int, int]:
        tally: dict[int, int] = {}
        for vote in self.state.votes:
            if vote.target_seat is not None:
                tally[vote.target_seat] = tally.get(vote.target_seat, 0) + 1
        return tally

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

    # =================================================================
    # Helpers
    # =================================================================

    async def _broadcast_phase_change(self) -> None:
        self.state.phase = self.sm.get_state()
        self.game_logger.log_phase_change(
            self.game_id, self.state.phase.value, self.state.round_number,
        )
        await self.event_bus.publish(
            BusEvent.PHASE_CHANGED, game_id=self.game_id, phase=self.state.phase.value,
            round_number=self.state.round_number, state=self.state,
        )
