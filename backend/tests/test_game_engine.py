import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import app.core.game_engine as game_engine_module
from app.core.game_engine import GameEngine
from app.models.game import GameState, GameConfig, GamePhase, PlayerState
from app.models.actions import VoteAction, DeathReport, WinResult
from app.models.contracts import AcceptedAction, ActionCommand, ActionContract, ActionRequest
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.config import PipelineMode
from app.core.role_pipeline import PipelineResult
from app.core.effect_applier import CommitResult
from app.core.scheduler import PipelinePaused, PointResult
from app.models.pipeline import SchedulePoint


def test_engine_source_has_no_builtin_role_or_action_branches() -> None:
    source = Path("app/core/game_engine.py").read_text("utf-8")
    for token in ("witch", "seer", "hunter", "werewolf_kill", "poison", "shoot"):
        assert token not in source


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    """Point every engine's default GameLogger at a per-test temp dir so engine
    tests never contend on the shared data/games directory."""
    original = game_engine_module.GameLogger

    def isolated(data_dir="data"):
        return original(data_dir=data_dir if data_dir != "data" else str(tmp_path))

    monkeypatch.setattr(game_engine_module, "GameLogger", isolated)


class ScheduleStub:
    def __init__(self, result, mutate=None, digest="stub"):
        self.result, self.mutate, self.calls, self.digest = result, mutate, [], digest
        self.registry = MagicMock(digest=digest)

    def run_point(self, state, point):
        self.calls.append((state, point))
        if self.mutate is not None: self.mutate(state)
        return self.result


@pytest.mark.asyncio
async def test_schedule_point_runs_pipeline_in_thread_and_requires_scheduler() -> None:
    point_result = PointResult((), (), (), "v2-digest")
    scheduler = ScheduleStub(point_result, lambda state: setattr(state, "round_number", 7))
    engine = GameEngine("v2", pipeline_scheduler=scheduler)
    result = await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION)
    assert type(result) is PipelineResult and result.mode is PipelineMode.V2
    assert engine.state.round_number == 7

    with pytest.raises(ValueError, match="scheduler"):
        await GameEngine("missing").run_schedule_point(SchedulePoint.NIGHT_ACTION)
    with pytest.raises(TypeError):
        await engine.run_schedule_point("night")


@pytest.mark.asyncio
async def test_schedule_point_propagates_errors_and_does_not_block_loop() -> None:
    class ExplodingScheduler:
        def run_point(self, state, point): raise RuntimeError("boom")
    engine = GameEngine("error", pipeline_scheduler=ExplodingScheduler())
    with pytest.raises(RuntimeError, match="boom"):
        await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION)

    class BadPipeline:
        def __init__(self, *args): pass
        def run_points(self, *args): return object()
    monkeypatch = pytest.MonkeyPatch(); monkeypatch.setattr(game_engine_module, "RolePipeline", BadPipeline)
    try:
        with pytest.raises(TypeError, match="exact PipelineResult"):
            await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION)
    finally: monkeypatch.undo()

    scheduler = ScheduleStub(PointResult((), (), (), "d"))
    engine = GameEngine("loop", pipeline_scheduler=scheduler)
    started, release = asyncio.Event(), asyncio.Event()
    def blocking(state, point):
        return PointResult((), (), (), "d")
    scheduler.run_point = blocking
    async def waiting():
        started.set()
        await release.wait()
        return await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION)
    task = asyncio.create_task(waiting())
    await started.wait(); await asyncio.sleep(0.05)
    assert not task.done()
    release.set(); assert type(await task) is PipelineResult


@pytest.mark.asyncio
async def test_schedule_points_batches_once_and_single_point_delegates() -> None:
    scheduler = ScheduleStub(PointResult((), (), (), "d"))
    engine = GameEngine("batch", pipeline_scheduler=scheduler)
    result = await engine.run_schedule_points(
        (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT),
    )
    assert type(result) is PipelineResult
    assert [point for _, point in scheduler.calls] == [
        SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT,
    ]
    scheduler.calls.clear()
    await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION)
    assert [point for _, point in scheduler.calls] == [SchedulePoint.NIGHT_ACTION]


@pytest.mark.asyncio
async def test_execute_night_prepares_once_and_runs_v2_batch() -> None:
    scheduler = ScheduleStub(PointResult((), (), (), "d"))
    engine = GameEngine("night", pipeline_scheduler=scheduler)
    engine.state.round_number = 4; engine.state.night_actions.append(object())
    engine.state.last_wolf_kill_target = 2
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_night()
    assert engine.state.round_number == 5 and engine.state.night_actions == []
    assert engine.state.last_wolf_kill_target is None
    assert [point for _, point in scheduler.calls] == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT]
    assert engine._pending_night_batch is None and engine._pending_night_completion is not None


@pytest.mark.asyncio
async def test_execute_night_is_single_flight_for_concurrent_callers() -> None:
    entered, release = asyncio.Event(), asyncio.Event(); calls = []
    engine = GameEngine("single", pipeline_scheduler=object())
    async def point(value):
        calls.append(value)
        if value is SchedulePoint.NIGHT_ACTION: entered.set(); await release.wait()
        return PointResult((), (), (), value.value)
    engine._execute_v2_point = point; engine._resume_pipeline_night = AsyncMock()
    first = asyncio.create_task(engine._execute_night()); second = asyncio.create_task(engine._execute_night())
    await entered.wait(); release.set(); await asyncio.gather(first, second)
    assert calls == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT]
    assert engine.state.round_number == 1 and engine._night_task is None


@pytest.mark.asyncio
async def test_cancelled_night_waiter_does_not_cancel_owner() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    engine = GameEngine("cancel", pipeline_scheduler=object())
    async def point(value):
        if value is SchedulePoint.NIGHT_ACTION: entered.set(); await release.wait()
        return PointResult((), (), (), value.value)
    engine._execute_v2_point = point
    waiter = asyncio.create_task(engine._execute_night()); await entered.wait(); waiter.cancel()
    with pytest.raises(asyncio.CancelledError): await waiter
    assert engine._night_task is not None and not engine._night_task.done()
    release.set(); await engine._night_task
    assert engine._night_task is None


@pytest.mark.asyncio
async def test_start_rejects_active_night_owner_and_stop_does_not_reset_state() -> None:
    release = asyncio.Event(); engine = GameEngine("lifecycle", pipeline_scheduler=object())
    async def point(value):
        if value is SchedulePoint.NIGHT_ACTION: await release.wait()
        return PointResult((), (), (), value.value)
    engine._execute_v2_point = point
    waiter = asyncio.create_task(engine._execute_night()); await asyncio.sleep(0)
    with pytest.raises(ValueError, match="night execution is active"): await engine.start()
    original = engine.state; await engine.stop(); assert engine.state is original
    release.set(); await waiter


@pytest.mark.asyncio
async def test_v2_night_batch_resumes_only_failed_point_and_aggregates_exactly() -> None:
    first_event = {"event_type": "FIRST", "payload": {}, "visibility": ("PUBLIC",)}
    second_event = {"event_type": "SECOND", "payload": {}, "visibility": ("PUBLIC",)}
    first_commit = CommitResult("a", ("e1",), 1, (first_event,), "action")
    second_commit = CommitResult("b", ("e2",), 2, (second_event,), "commit")
    first = PointResult((), (first_commit,), first_commit.events, "action")
    second = PointResult((), (second_commit,), second_commit.events, "commit")
    class Scheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, point):
            inner.calls.append(point)
            if point is SchedulePoint.NIGHT_COMMIT and inner.calls.count(point) == 1: raise RuntimeError("commit failed")
            return first if point is SchedulePoint.NIGHT_ACTION else second
    scheduler = Scheduler(); engine = GameEngine("checkpoint", pipeline_scheduler=scheduler)
    engine.run_schedule_point = AsyncMock(side_effect=AssertionError("mixed API used")); engine._resume_pipeline_night = AsyncMock()
    with pytest.raises(RuntimeError, match="commit failed"): await engine._execute_night()
    assert engine.state.round_number == 1 and engine._pending_night_batch.next_point == 1
    await engine._execute_night()
    assert scheduler.calls == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT, SchedulePoint.NIGHT_COMMIT]
    pending = engine._pending_night_completion
    assert pending.result.accepted_actions == ("a", "b") and pending.result.effects == ("e1", "e2")
    assert tuple(event["event_type"] for event in pending.result.public_events) == ("FIRST", "SECOND")
    assert pending.result.state_digest == "commit" and pending.result.mode is PipelineMode.V2
    assert pending.result.diff is None and type(pending.result) is PipelineResult
    assert engine._pending_night_batch is None


@pytest.mark.asyncio
async def test_v2_point_that_commits_then_raises_is_retried_without_reprepare() -> None:
    class Scheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, value):
            inner.calls.append(value)
            if value is SchedulePoint.NIGHT_ACTION and inner.calls.count(value) == 1:
                state.accepted_action_keys.add("stable-action"); raise RuntimeError("after commit")
            return PointResult((), (), (), value.value)
    scheduler = Scheduler(); engine = GameEngine("commit-boundary", pipeline_scheduler=scheduler)
    engine._resume_pipeline_night = AsyncMock()
    with pytest.raises(RuntimeError, match="after commit"): await engine._execute_night()
    await engine._execute_night()
    assert engine.state.round_number == 1
    assert scheduler.calls == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT]


def test_pending_batch_is_frozen_exact_and_start_resets_checkpoints() -> None:
    result = PointResult((), (), (), "d")
    batch = game_engine_module._PendingNightBatch(1, 1, (result,))
    assert batch.next_point == 1
    for call in (
        lambda: game_engine_module._PendingNightBatch(True, 0, ()),
        lambda: game_engine_module._PendingNightBatch(1, 3, ()),
        lambda: game_engine_module._PendingNightBatch(1, 0, []),
        lambda: game_engine_module._PendingNightBatch(1, 1, (object(),)),
        lambda: game_engine_module._PendingNightBatch(1, 0, (result,)),
        lambda: game_engine_module._PendingNightBatch(1, 1, (type("SubPoint", (PointResult,), {})((), (), (), "d"),)),
    ):
        with pytest.raises((TypeError, ValueError)): call()


@pytest.mark.asyncio
async def test_v2_batch_rejects_non_v2_point_result_without_checkpoint() -> None:
    class Scheduler:
        def run_point(self, state, point): return object()
    engine = GameEngine("wrong-mode", pipeline_scheduler=Scheduler())
    with pytest.raises(TypeError, match="exact PointResult"):
        await engine._execute_night()
    assert engine._pending_night_batch == game_engine_module._PendingNightBatch(1, 0, ())


@pytest.mark.asyncio
async def test_v2_observation_failure_reuses_checkpointed_raw_results() -> None:
    bad = PointResult((), (), (), "commit")
    object.__setattr__(bad, "commits", (object(),))
    class Scheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, point):
            inner.calls.append(point)
            return PointResult((), (), (), "action") if point is SchedulePoint.NIGHT_ACTION else bad
    scheduler = Scheduler(); engine = GameEngine("observe-fail", pipeline_scheduler=scheduler)
    for _ in range(2):
        with pytest.raises(TypeError, match="commit"):
            await engine._execute_night()
    assert scheduler.calls == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT]
    assert engine._pending_night_batch.raw_results == (PointResult((), (), (), "action"), bad)


@pytest.mark.asyncio
async def test_execute_night_v2_publishes_public_deaths_and_advances() -> None:
    death_event = {
        "event_type": "PLAYER_DIED",
        "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 1},
        "visibility": ("PUBLIC",),
    }
    class NightScheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, point):
            inner.calls.append((state, point))
            if point is SchedulePoint.NIGHT_COMMIT:
                state.players[1].is_alive = False
                state.death_history.append(DeathReport(1, "wolf_kill", 1))
                commit = CommitResult("settle", ("effect",), 1, (death_event,), "final")
                return PointResult((), (commit,), commit.events, "final")
            return PointResult((), (), (), "action")
    scheduler = NightScheduler(); bus = EventBus(); published = []
    async def on_death(**kwargs): published.append(kwargs["death"])
    bus.subscribe(BusEvent.PLAYER_DIED, on_death)
    engine = GameEngine("v2-night", event_bus=bus, pipeline_scheduler=scheduler)
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-villager", "good"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
    }
    engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._broadcast_phase_change = AsyncMock()
    engine.game_logger.log_deaths = MagicMock()
    memory = MagicMock(); engine.memory_service = memory
    await engine._execute_night()
    assert engine.state.round_number == 1 and len(engine.state.death_history) == 1
    assert [(item.player_seat, item.cause, item.round_number) for item in published] == [(1, "wolf_kill", 1)]
    engine.game_logger.log_deaths.assert_called_once()
    memory.save_memories.assert_called_once_with(engine.state)
    assert engine.sm.get_state() is GamePhase.DAWN
    engine._broadcast_phase_change.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_night_resumes_delivery_without_rerunning_batch_or_stages() -> None:
    events = tuple({
        "event_type": "PLAYER_DIED",
        "payload": {"seat": seat, "cause": "wolf_kill", "round_number": 1},
        "visibility": ("PUBLIC",),
    } for seat in (1, 2))
    class RecoveringScheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, point):
            inner.calls.append(point)
            if point is SchedulePoint.NIGHT_COMMIT:
                for seat in (1, 2):
                    state.players[seat].is_alive = False
                    state.death_history.append(DeathReport(seat, "wolf_kill", 1))
                commit = CommitResult("settle", ("effect",), 1, events, "final")
                return PointResult((), (commit,), events, "final")
            return PointResult((), (), (), "action")
    class RecoveringBus:
        def __init__(self): self.attempts, self.delivered = [], []
        async def publish(self, event, **kwargs):
            if event is BusEvent.PHASE_CHANGED: return
            seat = kwargs["death"].player_seat; self.attempts.append(seat)
            if seat == 2 and self.attempts.count(2) == 1: raise RuntimeError("transport")
            self.delivered.append(seat)
    scheduler, bus = RecoveringScheduler(), RecoveringBus()
    engine = GameEngine("recover", event_bus=bus, pipeline_scheduler=scheduler)
    engine.state.players = {seat: PlayerState(seat, "r", "good") for seat in (1, 2, 3)}
    engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    engine.game_logger.log_deaths = MagicMock(side_effect=[RuntimeError("disk"), None])
    engine.memory_service = MagicMock(); engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._broadcast_phase_change = AsyncMock()
    with pytest.raises(RuntimeError, match="transport"): await engine._execute_night()
    with pytest.raises(RuntimeError, match="disk"): await engine._execute_night()
    await engine._execute_night()
    assert scheduler.calls == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT]
    assert engine.state.round_number == 1 and bus.attempts == [1, 2, 2]
    assert bus.delivered == [1, 2] and engine.game_logger.log_deaths.call_count == 2
    engine.memory_service.save_memories.assert_called_once_with(engine.state)
    engine.rule_engine.check_win.assert_called_once_with(engine.state); engine._broadcast_phase_change.assert_not_awaited()
    assert engine._pending_night_completion is None and engine.sm.get_state() is GamePhase.DAWN


@pytest.mark.asyncio
async def test_malformed_pipeline_event_is_persisted_and_never_reruns_batch() -> None:
    malformed = {
        "event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "wolf_kill"},
        "visibility": ("PUBLIC",),
    }
    class MalformedScheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, point):
            inner.calls.append(point)
            if point is SchedulePoint.NIGHT_COMMIT:
                state.players[1].is_alive = False
                state.death_history.append(DeathReport(1, "wolf_kill", 1))
                commit = CommitResult("settle", ("effect",), 1, (malformed,), "final")
                return PointResult((), (commit,), commit.events, "final")
            return PointResult((), (), (), "action")
    scheduler = MalformedScheduler(); engine = GameEngine(
        "malformed", pipeline_scheduler=scheduler,
    )
    engine.state.players = {1: PlayerState(1, "r", "good")}
    engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    engine.event_bus.publish = AsyncMock(); engine.game_logger.log_deaths = MagicMock()
    for _ in range(2):
        with pytest.raises(PipelinePaused, match="invalid pipeline night event"):
            await engine._execute_night()
    assert scheduler.calls == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT]
    assert engine.state.round_number == 1 and engine._pending_night_completion is not None
    engine.event_bus.publish.assert_not_awaited(); engine.game_logger.log_deaths.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [
    {"event_type": "NOTICE", "payload": {}, "visibility": ("PUBLIC",)},
    {"event_type": "PLAYER_DIED", "payload": {"seat": True, "cause": "wolf_kill", "round_number": 1}, "visibility": ("PUBLIC",)},
    {"event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "bad cause", "round_number": 1}, "visibility": ("PUBLIC",)},
    {"event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 2}, "visibility": ("PUBLIC",)},
])
async def test_pipeline_public_event_validation_is_closed_before_side_effects(event) -> None:
    result = PipelineResult((), (), "digest", (event,), PipelineMode.V2)
    engine = GameEngine("validate", pipeline_scheduler=object())
    engine.state.round_number = 1
    engine.state.players = {1: PlayerState(1, "r", "good", is_alive=False)}
    engine.state.death_history = [DeathReport(1, "wolf_kill", 1)]
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(result)
    if event["event_type"] == "NOTICE":
        await engine._execute_night(); assert engine._pending_night_completion is None
    else:
        with pytest.raises(PipelinePaused): await engine._execute_night()
        assert engine._pending_night_completion is not None


@pytest.mark.asyncio
async def test_start_resets_pending_night_checkpoints() -> None:
    engine = GameEngine("reset")
    engine._pending_night_completion = object()
    engine._pending_night_batch = object()
    engine._game_loop = AsyncMock(); engine._broadcast_phase_change = AsyncMock()
    engine._assign_roles = MagicMock()
    await engine.start()
    assert engine._pending_night_completion is engine._pending_night_batch is None


@pytest.mark.asyncio
async def test_pipeline_terminal_completion_resumes_log_and_publish_without_rechecking() -> None:
    class TerminalBus:
        def __init__(self): self.calls = 0
        async def publish(self, event, **kwargs):
            if event is BusEvent.PHASE_CHANGED: return
            assert event is BusEvent.GAME_OVER; self.calls += 1
            if self.calls == 1: raise RuntimeError("publish")
    engine = GameEngine("terminal", event_bus=TerminalBus(), pipeline_scheduler=object())
    engine.state.round_number = 1; engine.sm.set_state(GamePhase.NIGHT)
    result = PipelineResult((), (), "digest", (), PipelineMode.V2)
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(result, deaths=(), stage=2)
    engine.rule_engine.check_win = MagicMock(return_value=WinResult("good", "all_wolves_dead"))
    engine.game_logger.log_game_over = MagicMock(side_effect=[RuntimeError("disk"), None])
    engine._broadcast_phase_change = AsyncMock()
    with pytest.raises(RuntimeError, match="disk"): await engine._execute_night()
    assert engine.state.phase is GamePhase.GAME_OVER and engine.sm.get_state() is GamePhase.GAME_OVER
    with pytest.raises(RuntimeError, match="publish"): await engine._execute_night()
    await engine._execute_night()
    engine.rule_engine.check_win.assert_called_once_with(engine.state)
    assert engine.game_logger.log_game_over.call_count == 2 and engine.event_bus.calls == 2
    engine._broadcast_phase_change.assert_not_awaited()
    assert engine._pending_night_completion is None


@pytest.mark.asyncio
async def test_pipeline_transition_hook_failure_resumes_without_retransition() -> None:
    engine = GameEngine("transition", pipeline_scheduler=object())
    engine.state.round_number = 1; engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    result = PipelineResult((), (), "digest", (), PipelineMode.V2)
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(result, deaths=(), stage=2)
    engine.rule_engine.check_win = MagicMock(return_value=None)
    calls = []
    def explode_after_transition(): calls.append("hook"); raise RuntimeError("hook")
    engine.sm.on_enter(GamePhase.DAWN, explode_after_transition)
    engine._broadcast_phase_change = AsyncMock()
    with pytest.raises(RuntimeError, match="hook"): await engine._execute_night()
    assert engine.sm.get_state() is GamePhase.DAWN
    assert engine.state.phase is GamePhase.DAWN
    await engine._execute_night()
    assert calls == ["hook"] and engine.rule_engine.check_win.call_count == 1
    engine._broadcast_phase_change.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_phase_publish_failure_does_not_repeat_phase_log() -> None:
    class PhaseBus:
        def __init__(self): self.calls = 0
        async def publish(self, event, **kwargs):
            assert event is BusEvent.PHASE_CHANGED; self.calls += 1
            if self.calls == 1: raise RuntimeError("phase publish")
    engine = GameEngine("phase", event_bus=PhaseBus(), pipeline_scheduler=object())
    engine.state.round_number = 1; engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    result = PipelineResult((), (), "digest", (), PipelineMode.V2)
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(
        result, deaths=(), stage=7, win_checked=True,
    )
    engine.sm.set_state(GamePhase.DAWN); engine.game_logger.log_phase_change = MagicMock()
    with pytest.raises(RuntimeError, match="phase publish"): await engine._execute_night()
    await engine._execute_night()
    engine.game_logger.log_phase_change.assert_called_once_with("phase", "dawn", 1)
    assert engine.event_bus.calls == 2 and engine._pending_night_completion is None


@pytest.mark.asyncio
async def test_pending_snapshots_are_frozen_from_external_mutation() -> None:
    engine = GameEngine("snapshot", pipeline_scheduler=object())
    engine.state.round_number = 1; engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    engine.state.players = {1: PlayerState(1, "r", "good", is_alive=False)}
    engine.state.death_history = [DeathReport(1, "wolf_kill", 1)]
    event = {"event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 1}, "visibility": ("PUBLIC",)}
    result = PipelineResult((), (), "digest", (event,), PipelineMode.V2)
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(result)
    seen = []
    class MutatingBus:
        async def publish(self, kind, **kwargs):
            if kind is BusEvent.PHASE_CHANGED: return
            death = kwargs["death"]; seen.append((death.player_seat, death.cause, death.round_number))
            death.cause = "tampered"
    engine.event_bus = MutatingBus(); engine.rule_engine.check_win = MagicMock(return_value=None)
    engine.game_logger.log_deaths = MagicMock(); engine._broadcast_phase_change = AsyncMock()
    await engine._execute_night()
    assert seen == [(1, "wolf_kill", 1)]
    assert engine.game_logger.log_deaths.call_args.args[2] == [{"player_seat": 1, "cause": "wolf_kill", "round_number": 1}]


@pytest.mark.asyncio
async def test_nonexact_win_result_is_cached_as_invalid_without_rechecking() -> None:
    class WinSubclass(WinResult): pass
    engine = GameEngine("bad-win", pipeline_scheduler=object())
    engine.state.round_number = 1; engine.sm.set_state(GamePhase.NIGHT)
    result = PipelineResult((), (), "digest", (), PipelineMode.V2)
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(result, deaths=(), stage=2)
    engine.rule_engine.check_win = MagicMock(return_value=WinSubclass("good", "all_wolves_dead"))
    for _ in range(2):
        with pytest.raises(PipelinePaused, match="invalid pipeline win result"):
            await engine._execute_night()
    engine.rule_engine.check_win.assert_called_once_with(engine.state)


def test_pending_snapshots_validate_exact_types_and_ranges() -> None:
    result = PipelineResult((), (), "digest", (), PipelineMode.V2)
    death = game_engine_module._PendingDeath(1, "wolf_kill", 1)
    win = game_engine_module._PendingWin("good", "all_wolves_dead")
    game_engine_module._PendingNightCompletion(result, (death,), 1, 7, win, True, False)
    for call in (
        lambda: game_engine_module._PendingDeath(True, "x", 1),
        lambda: game_engine_module._PendingDeath(1, "wolf_kill", -1),
        lambda: game_engine_module._PendingDeath(1, "bad cause", 1),
        lambda: game_engine_module._PendingWin("good", "bad reason"),
        lambda: game_engine_module._PendingNightCompletion(object()),
        lambda: game_engine_module._PendingNightCompletion(result, (object(),)),
        lambda: game_engine_module._PendingNightCompletion(result, (), 1, 0),
        lambda: game_engine_module._PendingNightCompletion(result, (), 0, 10),
        lambda: game_engine_module._PendingNightCompletion(result, win_result=object()),
    ):
        with pytest.raises((TypeError, ValueError)): call()


@pytest.mark.asyncio
async def test_v2_night_runs_real_scheduler_end_to_end(tmp_path) -> None:
    from app.core.scheduler import Scheduler
    from app.core.context_projector import ContextProjector
    from app.core.action_validator import ActionValidator
    from app.core.action_resolver import ActionResolver
    from app.core.effect_applier import EffectApplier
    from app.roles.registry import builtin_registry
    from app.models.pipeline import ActionCommand as PipelineActionCommand
    snapshot = builtin_registry.freeze()

    def provider(request, context, attempt):
        if request.contract.contract_id == "werewolf_kill":
            return PipelineActionCommand(action_type="kill", target_seat=4, reasoning="plan")
        if request.contract.contract_id == "witch_action":
            return PipelineActionCommand(action_type="pass", target_seat=None, reasoning="wait")
        if request.contract.contract_id == "seer_check":
            return PipelineActionCommand(action_type="check", target_seat=1, reasoning="probe")
        return PipelineActionCommand(action_type="pass", target_seat=None, reasoning="")

    scheduler = Scheduler(snapshot, ContextProjector(), ActionValidator(), ActionResolver(), EffectApplier(), provider)
    roles = {seat: make_mock_role(seat, name) for seat, name in {
        1: "wolf-killer-werewolf", 2: "wolf-killer-werewolf", 3: "wolf-killer-werewolf",
        4: "wolf-killer-villager", 5: "wolf-killer-villager", 6: "wolf-killer-seer",
        7: "wolf-killer-witch", 8: "wolf-killer-hunter", 9: "wolf-killer-villager",
    }.items()}
    engine = GameEngine("e2e-night", roles=roles, pipeline_scheduler=scheduler, data_dir=str(tmp_path))
    engine._assign_roles()
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    result = await engine.run_schedule_points((
        SchedulePoint.NIGHT_WOLF_VOTE, SchedulePoint.NIGHT_WITCH_ACTION,
        SchedulePoint.NIGHT_SEER_ACTION, SchedulePoint.NIGHT_COMMIT,
    ))
    assert engine.state.round_number == 1
    assert engine.state.players[4].is_alive is False
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(4, "wolf_kill")]
    engine._log_audience_events(result, "night")

    log_lines = (tmp_path / "games" / "e2e-night" / "game.log").read_text("utf-8").splitlines()
    audience = [json.loads(line) for line in log_lines
                if json.loads(line)["operation"] == "audience_action"]
    by_type = {record["data"]["event_type"]: record["data"]["payload"] for record in audience}
    assert by_type["WEREWOLF_KILL"] == {"target_seat": 4, "vote_counts": {"4": 3}}
    assert by_type["SEER_CHECK"] == {"target_seat": 1, "result": "werewolf"}
    assert {record["round"] for record in audience} == {1}


def test_pipeline_audience_events_keep_only_valid_public_non_death_events() -> None:
    engine = GameEngine(game_id="test")
    good = {"event_type": "SOME_ACTION", "payload": {"x": 1}, "visibility": ("PUBLIC",)}
    result = PipelineResult(
        (), (), "digest", (
            {"event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 1}, "visibility": ("PUBLIC",)},
            good,
        ), PipelineMode.V2,
    )
    assert engine._pipeline_audience_events(result) == (("SOME_ACTION", {"x": 1}),)

    for bad in (
        {"event_type": "X", "payload": {"x": 1}},
        {"event_type": "X", "payload": {"x": 1}, "visibility": ("ACTOR",)},
        {"event_type": "X", "payload": {"x": 1}, "visibility": ("PUBLIC",), "extra": 1},
        {"event_type": 3, "payload": {"x": 1}, "visibility": ("PUBLIC",)},
        {"event_type": "", "payload": {"x": 1}, "visibility": ("PUBLIC",)},
        {"event_type": "bad token", "payload": {"x": 1}, "visibility": ("PUBLIC",)},
        {"event_type": "X", "payload": "not-a-mapping", "visibility": ("PUBLIC",)},
        {"event_type": "X", "payload": {"x": 1}, "visibility": "PUBLIC"},
        {"event_type": "X", "payload": {"x": 1}, "visibility": (1,)},
    ):
        with pytest.raises(PipelinePaused):
            engine._pipeline_audience_events(
                PipelineResult((), (), "digest", (bad,), PipelineMode.V2),
            )

    from types import SimpleNamespace
    with pytest.raises(PipelinePaused):
        engine._pipeline_audience_events(
            SimpleNamespace(public_events=("not-a-mapping",)),
        )
    with pytest.raises(PipelinePaused):
        engine._pipeline_audience_events(
            SimpleNamespace(public_events=(
                {"event_type": "\ud800", "payload": {"x": 1}, "visibility": ("PUBLIC",)},
            )),
        )


def test_log_audience_events_writes_operation_records(tmp_path) -> None:
    engine = GameEngine(game_id="aud", data_dir=str(tmp_path))
    result = PipelineResult(
        (), (), "digest", (
            {"event_type": "SOME_ACTION", "payload": {"target_seat": 2, "nested": (1, 2)}, "visibility": ("PUBLIC",)},
        ), PipelineMode.V2,
    )
    engine._log_audience_events(result, "night")

    records = [json.loads(line) for line in (tmp_path / "games" / "aud" / "game.log").read_text("utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["operation"] == "audience_action"
    assert records[0]["phase"] == "night"
    assert records[0]["round"] == 0
    assert records[0]["data"] == {
        "event_type": "SOME_ACTION",
        "payload": {"target_seat": 2, "nested": [1, 2]},
    }


def test_log_audience_events_writes_thought_and_werewolf_channel_sidecars(tmp_path) -> None:
    engine = GameEngine(game_id="sidecar", data_dir=str(tmp_path))
    engine.state.players = {1: PlayerState(1, "wolf-killer-witch", "good")}
    result = PipelineResult(
        (), (), "digest", (
            {"event_type": "WITCH_REASONING", "payload": {
                "seat": 1, "action_type": "save", "target_seat": 2,
                "reasoning": "r", "thought": "决定使用解药救 2 号玩家：r",
            }, "visibility": ("PUBLIC",)},
            {"event_type": "WEREWOLF_DISCUSSION", "payload": {
                "votes": [{"action_type": "kill", "target_seat": 2, "reasoning": "r"}],
                "channel": "提议刀 2 号：r",
            }, "visibility": ("PUBLIC",)},
        ), PipelineMode.V2,
    )
    engine._log_audience_events(result, "night")

    thoughts = engine.conversation_log.get_all_thoughts()
    assert [(t.speaker_seat, t.speaker_role, t.content, t.phase) for t in thoughts] == [
        (1, "wolf-killer-witch", "决定使用解药救 2 号玩家：r", "night"),
    ]
    channels = [
        record for record in engine.conversation_log.get_all()
        if record.scope.value == "werewolf"
    ]
    assert [(c.content, c.round_number, c.phase) for c in channels] == [
        ("提议刀 2 号：r", 0, "night"),
    ]


def test_log_audience_events_skips_invalid_or_unknown_sidecars(tmp_path) -> None:
    engine = GameEngine(game_id="sidecar-skip", data_dir=str(tmp_path))
    engine.state.players = {}
    result = PipelineResult(
        (), (), "digest", (
            {"event_type": "A", "payload": {"thought": 3, "seat": 1}, "visibility": ("PUBLIC",)},
            {"event_type": "B", "payload": {"thought": "x", "seat": "1"}, "visibility": ("PUBLIC",)},
            {"event_type": "C", "payload": {"thought": "x", "seat": 0}, "visibility": ("PUBLIC",)},
            {"event_type": "D", "payload": {"thought": "x", "seat": 99}, "visibility": ("PUBLIC",)},
            {"event_type": "E", "payload": {"channel": 3}, "visibility": ("PUBLIC",)},
        ), PipelineMode.V2,
    )
    engine._log_audience_events(result, "night")

    assert engine.conversation_log.get_all_thoughts() == []
    assert all(
        record.scope.value != "werewolf"
        for record in engine.conversation_log.get_all()
    )


def make_mock_role(seat: int, role_name: str,
                   speech: str = "test speech",
                   vote: VoteAction = None):
    role = MagicMock()
    role.seat = seat
    role.role_name = role_name

    async def _speak(state, conversation_log, context):
        return speech

    async def _request_action(state, conversation_log, request):
        if request.contract.contract_id == "exile_vote":
            target = vote.target_seat if vote else None
            return AcceptedAction(
                request=request,
                command=ActionCommand(
                    action_type="vote" if target is not None else "abstain",
                    target_seat=target,
                    reasoning="",
                ),
            )
        return AcceptedAction(
            request=request,
            command=ActionCommand(action_type="pass", target_seat=None, reasoning=""),
        )

    role.speak = AsyncMock(side_effect=_speak)
    role.request_action = AsyncMock(side_effect=_request_action)
    return role


def make_9_mock_roles():
    roles = {}
    role_names = [
        (1, "wolf-killer-werewolf"), (2, "wolf-killer-werewolf"), (3, "wolf-killer-werewolf"),
        (4, "wolf-killer-villager"), (5, "wolf-killer-villager"), (6, "wolf-killer-villager"),
        (7, "wolf-killer-seer"), (8, "wolf-killer-witch"), (9, "wolf-killer-hunter"),
    ]
    for seat, role_name in role_names:
        roles[seat] = make_mock_role(seat, role_name,
            vote=VoteAction(voter_seat=seat, target_seat=1),
        )
    return roles


class TestGameEngine:
    @pytest.mark.asyncio
    async def test_role_assignment(self):
        roles = make_9_mock_roles()
        engine = GameEngine(game_id="test", roles=roles)
        engine._assign_roles()

        assert len(engine.state.players) == 9
        wolves = [p for p in engine.state.players.values() if p.camp == "werewolf"]
        assert len(wolves) == 3
        assert [p.role for p in engine.state.players.values()].count("wolf-killer-seer") == 1
        assert [p.role for p in engine.state.players.values()].count("wolf-killer-witch") == 1
        assert [p.role for p in engine.state.players.values()].count("wolf-killer-hunter") == 1

    @pytest.mark.asyncio
    async def test_role_assignment_rejects_unknown_role(self):
        engine = GameEngine(game_id="test", roles={1: make_mock_role(1, "wolf-killer-unknown")})
        with pytest.raises(ValueError, match="unknown role"):
            engine._assign_roles()

    @pytest.mark.asyncio
    async def test_resolve_votes(self):
        engine = GameEngine(game_id="test")
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=5),
            VoteAction(voter_seat=2, target_seat=5),
            VoteAction(voter_seat=3, target_seat=4),
        ]
        exiled = engine.resolve_votes()
        assert exiled == 5

    @pytest.mark.asyncio
    async def test_resolve_votes_tie(self):
        engine = GameEngine(game_id="test")
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=5),
            VoteAction(voter_seat=2, target_seat=5),
            VoteAction(voter_seat=3, target_seat=4),
            VoteAction(voter_seat=4, target_seat=4),
        ]
        exiled = engine.resolve_votes()
        assert exiled is None  # tie

    @pytest.mark.asyncio
    async def test_resolve_votes_abstain(self):
        engine = GameEngine(game_id="test")
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=None),
            VoteAction(voter_seat=2, target_seat=0),
            VoteAction(voter_seat=3, target_seat=5),
        ]
        exiled = engine.resolve_votes()
        assert exiled == 5

    @pytest.mark.asyncio
    async def test_resolve_votes_all_abstain_logs_vote_result(self, tmp_path):
        engine = GameEngine(game_id="test", data_dir=str(tmp_path))
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=None),
            VoteAction(voter_seat=2, target_seat=None),
        ]
        exiled = engine.resolve_votes()

        assert exiled is None
        records = [json.loads(line) for line in (tmp_path / "games" / "test" / "game.log").read_text("utf-8").splitlines()]
        assert len(records) == 1
        assert records[0]["operation"] == "vote_result"
        assert records[0]["phase"] == "vote_resolution"
        assert records[0]["data"] == {"exiled": None, "tally": {}}

    @pytest.mark.asyncio
    async def test_phase_delay(self):
        engine = GameEngine(game_id="test")
        assert engine.phase_delay == 2.0
        engine.phase_delay = 1.0
        assert engine.phase_delay == 1.0
        engine.phase_delay = 0.01
        assert engine.phase_delay == 0.1  # clamped

    @pytest.mark.asyncio
    async def test_pause_resume(self):
        engine = GameEngine(game_id="test")
        assert engine._paused is False
        engine.pause()
        assert engine._paused is True
        engine.resume()
        assert engine._paused is False

    @pytest.mark.asyncio
    async def test_stop(self):
        engine = GameEngine(game_id="test")
        engine._running = True
        await engine.stop()
        assert engine._running is False

    @pytest.mark.asyncio
    async def test_broadcast_phase_change(self):
        bus = EventBus()
        received = []

        async def handler(**kwargs):
            received.append(kwargs)

        bus.subscribe("phase_changed", handler)
        engine = GameEngine(game_id="test", event_bus=bus)
        engine._assign_roles()
        await engine._broadcast_phase_change()

        assert len(received) == 1
        assert received[0]["game_id"] == engine.game_id
        assert received[0]["phase"] == engine.sm.get_state().value

    @pytest.mark.asyncio
    async def test_public_speech_and_vote_events_are_game_scoped(self):
        bus = EventBus()
        speeches = []
        votes = []

        async def record_speech(**kwargs):
            speeches.append(kwargs)

        async def record_vote(**kwargs):
            votes.append(kwargs)

        bus.subscribe("speech_made", record_speech)
        bus.subscribe("vote_cast", record_vote)
        roles = {
            1: make_mock_role(1, "wolf-killer-villager", vote=VoteAction(1, 2)),
            2: make_mock_role(2, "wolf-killer-villager", vote=VoteAction(2, 1)),
        }
        engine = GameEngine(game_id="public-day-events", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.SPEECH)
        engine.state.phase = GamePhase.SPEECH

        await engine._execute_speech_round()
        await engine._execute_vote_casting()

        assert speeches
        assert votes
        assert {event["game_id"] for event in speeches} == {engine.game_id}
        assert {event["game_id"] for event in votes} == {engine.game_id}

    @pytest.mark.asyncio
    async def test_last_words_speech_event_is_game_scoped(self):
        bus = EventBus()
        speeches = []

        async def record_speech(**kwargs):
            speeches.append(kwargs)

        bus.subscribe("speech_made", record_speech)
        roles = {1: make_mock_role(1, "wolf-killer-villager")}
        engine = GameEngine(game_id="public-last-words", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.state.players[1].is_alive = False

        last_words = await engine.give_last_words(1, "exile", 1)

        assert last_words
        assert len(speeches) == 1
        assert speeches[0]["game_id"] == engine.game_id

    @pytest.mark.asyncio
    async def test_last_words_eligibility_is_cause_generic(self):
        roles = {1: make_mock_role(1, "wolf-killer-villager")}
        engine = GameEngine(game_id="test", roles=roles)
        engine._assign_roles()
        engine.state.players[1].is_alive = False

        # First-night death of any night cause is eligible in round 1.
        assert await engine.give_last_words(1, "wolf_kill", 1)
        assert await engine.give_last_words(1, "wolf_kill", 1) is None  # already given
        assert await engine.give_last_words(1, "wolf_kill", 2) is None  # not first night
        assert await engine.give_last_words(1, "exile", 2) is not None  # exile any round

    @pytest.mark.asyncio
    async def test_vote_resolution_wolf_wins(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        for p in engine.state.players.values():
            if "villager" in p.role:
                p.is_alive = False

        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        await engine._execute_vote_resolution()

        assert engine.state.win_result is not None
        assert engine.state.win_result["winning_camp"] == "werewolf"
        assert engine.sm.is_terminal()

    @pytest.mark.asyncio
    async def test_vote_resolution_good_wins(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        for p in engine.state.players.values():
            if "werewolf" in p.role:
                p.is_alive = False

        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        await engine._execute_vote_resolution()

        assert engine.state.win_result is not None
        assert engine.state.win_result["winning_camp"] == "good"

    @pytest.mark.asyncio
    async def test_vote_resolution_exile_player(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus,
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine._assign_roles()

        votes = [VoteAction(voter_seat=s, target_seat=1) for s in range(2, 10)]
        engine.state.votes = votes
        engine.state.players[1].is_alive = True

        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        await engine._execute_vote_resolution()
        assert engine.state.players[1].is_alive is False

    @pytest.mark.asyncio
    async def test_exile_reaction_requires_scheduler(self):
        roles = {1: make_mock_role(1, "wolf-killer-villager")}
        engine = GameEngine(game_id="test", roles=roles)
        with pytest.raises(ValueError, match="scheduler"):
            await engine._run_exile_reaction(1)

    @pytest.mark.asyncio
    async def test_exile_reaction_injects_commit_and_runs_dawn_point(self):
        calls = []
        class Scheduler:
            def __init__(self): self.registry = MagicMock(digest="d" * 64)
            def run_point(self, state, point):
                calls.append((state, point))
                return PointResult((), (), (), "done")
        scheduler = Scheduler()
        roles = {1: make_mock_role(1, "wolf-killer-villager"), 2: make_mock_role(2, "wolf-killer-villager")}
        engine = GameEngine("exile-react", roles=roles, pipeline_scheduler=scheduler)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.state.phase = GamePhase.VOTE_RESOLUTION
        engine.state.round_number = 2
        engine.state.players[1].mark_dead("exile")
        engine.state.death_history.append(DeathReport(1, "exile", 2))
        await engine._run_exile_reaction(1)
        assert calls == [(engine.state, SchedulePoint.DAWN_REACTION)]
        from app.core.point_journal import PointKey, point_journal
        key = PointKey("exile-react", 2, "vote_resolution", SchedulePoint.DAWN_REACTION, "d" * 64)
        saved = point_journal(engine.state).get(key)
        assert saved is not None and saved.cursor.kind == "response"
        assert saved.commits[0].events[0]["event_type"] == "PLAYER_DIED"
        assert saved.commits[0].events[0]["payload"]["cause"] == "exile"

    @pytest.mark.asyncio
    async def test_speak_no_role(self):
        engine = GameEngine(game_id="test", roles={})
        result = await engine.speak(999, "day_speech")
        assert result is None

    @pytest.mark.asyncio
    async def test_vote_no_role(self):
        engine = GameEngine(game_id="test", roles={})
        result = await engine.vote(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_speak_with_role(self):
        role = make_mock_role(1, "wolf-killer-villager", speech="hello world")
        engine = GameEngine(game_id="test", roles={1: role})

        speech = await engine.speak(1, "day_speech")
        assert speech == "hello world"

    @pytest.mark.asyncio
    async def test_vote_with_role(self):
        role = make_mock_role(1, "wolf-killer-villager",
                              vote=VoteAction(voter_seat=1, target_seat=3))
        engine = GameEngine(game_id="test", roles={1: role})
        engine.state.phase = GamePhase.VOTE_CASTING
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-villager", "good"),
            3: PlayerState(3, "wolf-killer-villager", "good"),
        }

        vote = await engine.vote(1)
        assert vote is not None
        assert vote.target_seat == 3

    @pytest.mark.asyncio
    async def test_speak_error_handling(self):
        role = MagicMock()
        role.speak = AsyncMock(side_effect=Exception("LLM error"))

        engine = GameEngine(game_id="test", roles={1: role})
        result = await engine.speak(1, "day_speech")
        # Emergency fallback: engine should never return None for speech errors;
        # it generates a placeholder speech so the player is never silently skipped.
        assert result is not None
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_vote_error_handling(self):
        role = MagicMock()
        role.vote = AsyncMock(side_effect=Exception("LLM error"))

        engine = GameEngine(game_id="test", roles={1: role})
        result = await engine.vote(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_speech_round(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.SPEECH)

        await engine._execute_speech_round()

        assert len(engine.state.speeches) == len(engine.state.alive_players())
        assert engine.sm.get_state() == GamePhase.VOTE_CASTING

    @pytest.mark.asyncio
    async def test_execute_vote_casting(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.VOTE_CASTING)

        await engine._execute_vote_casting()

        assert len(engine.state.votes) == len(engine.state.alive_players())
        assert engine.sm.get_state() == GamePhase.VOTE_RESOLUTION

    @pytest.mark.asyncio
    async def test_execute_dawn(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.phase_delay = 0.01
        engine.sm.set_state(GamePhase.DAWN)

        await engine._execute_dawn()

        assert engine.sm.get_state() == GamePhase.LAST_WORDS

    @pytest.mark.asyncio
    async def test_execute_last_words(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.state.round_number = 1
        engine.state.death_history.append(
            DeathReport(player_seat=1, cause="wolf_kill", round_number=1)
        )
        engine.state.players[1].is_alive = False
        engine.sm.set_state(GamePhase.LAST_WORDS)

        await engine._execute_last_words()

        assert engine.sm.get_state() == GamePhase.SPEECH

    @pytest.mark.asyncio
    async def test_execute_last_words_dead_player_no_agent(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.state.round_number = 1
        engine.state.death_history.append(
            DeathReport(player_seat=99, cause="wolf_kill", round_number=1)
        )
        engine.sm.set_state(GamePhase.LAST_WORDS)

        await engine._execute_last_words()

        assert engine.sm.get_state() == GamePhase.SPEECH

    @pytest.mark.asyncio
    async def test_execute_last_words_after_day1(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.state.round_number = 2
        engine.state.death_history.append(
            DeathReport(player_seat=1, cause="wolf_kill", round_number=2)
        )
        engine.state.players[1].is_alive = False
        engine.sm.set_state(GamePhase.LAST_WORDS)

        await engine._execute_last_words()

        assert engine.sm.get_state() == GamePhase.SPEECH

    @pytest.mark.asyncio
    async def test_wait_if_paused(self):
        engine = GameEngine(game_id="test")
        engine._running = True
        engine._paused = True

        async def unpause():
            await asyncio.sleep(0.05)
            engine._paused = False

        task = asyncio.create_task(unpause())
        await engine._wait_if_paused()
        await task
        assert engine._paused is False

    @pytest.mark.asyncio
    async def test_vote_resolution_tie_no_exile(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus,
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine._assign_roles()

        # Initial vote: tie (3 for 1, 3 for 2)
        votes = []
        for s in [3, 4, 5]:
            votes.append(VoteAction(voter_seat=s, target_seat=1))
        for s in [6, 7, 8]:
            votes.append(VoteAction(voter_seat=s, target_seat=2))
        engine.state.votes = votes
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)

        # Make re-vote also a tie — all roles abstain so tally is empty
        for role in roles.values():
            async def abstain(state, conversation_log, request):
                return AcceptedAction(
                    request=request,
                    command=ActionCommand(
                        action_type="abstain", target_seat=None, reasoning=""
                    ),
                )

            role.request_action = AsyncMock(side_effect=abstain)

        await engine._execute_vote_resolution()

        assert engine.state.players[1].is_alive
        assert engine.state.players[2].is_alive

    @pytest.mark.asyncio
    async def test_vote_resolution_first_tie_gives_all_alive_one_supplemental_speech_then_exiles_revote_winner(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
            3: make_mock_role(3, "wolf-killer-villager"),
            4: make_mock_role(4, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="first-tie", roles=roles,
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine._assign_roles()
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=1),
            VoteAction(voter_seat=2, target_seat=1),
            VoteAction(voter_seat=3, target_seat=2),
            VoteAction(voter_seat=4, target_seat=2),
        ]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.speak = AsyncMock(return_value="supplemental speech")
        revote_states = []

        async def revote(state, conversation_log, request):
            revote_states.append((
                state.vote_round,
                state.is_tiebreak,
                set(state.tiebreak_candidates),
                set(state.supplemental_speakers),
            ))
            target = 1 if request.actor_seat != 4 else None
            return AcceptedAction(
                request=request,
                command=ActionCommand(
                    action_type="vote" if target else "abstain",
                    target_seat=target,
                    reasoning="",
                ),
            )

        for role in roles.values():
            role.request_action = AsyncMock(side_effect=revote)

        await engine._execute_vote_resolution()

        assert engine.state.players[1].is_alive is False
        supplemental_calls = [
            call for call in engine.speak.await_args_list if call.args[1] == "day_speech"
        ]
        assert len(supplemental_calls) == 4
        assert [call.args[0] for call in supplemental_calls] == [1, 2, 3, 4]
        assert all(state == (2, True, {1, 2}, {1, 2, 3, 4}) for state in revote_states)
        assert engine.state.vote_round == 1
        assert engine.state.is_tiebreak is False

    @pytest.mark.asyncio
    async def test_execute_tiebreak_resumes_only_missing_speakers_and_voters(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
            3: make_mock_role(3, "wolf-killer-villager"),
            4: make_mock_role(4, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="resume-tiebreak", roles=roles,
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine._assign_roles()
        engine.state.is_tiebreak = True
        engine.state.vote_round = 2
        engine.state.tiebreak_candidates = {1, 2}
        engine.state.supplemental_speakers = {1, 3}
        engine.state.voted_seats = {1, 4}
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=1),
            VoteAction(voter_seat=4, target_seat=2),
        ]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.speak = AsyncMock(return_value="resumed supplemental speech")
        vote_calls = []

        async def revote(state, conversation_log, request):
            vote_calls.append(request.actor_seat)
            return AcceptedAction(
                request=request,
                command=ActionCommand(
                    action_type="vote", target_seat=1, reasoning="",
                ),
            )

        for role in roles.values():
            role.request_action = AsyncMock(side_effect=revote)

        await engine._execute_vote_resolution()

        supplemental_calls = [
            call for call in engine.speak.await_args_list if call.args[1] == "day_speech"
        ]
        assert [call.args[0] for call in supplemental_calls] == [2, 4]
        assert vote_calls == [2, 3]
        assert engine.state.players[1].is_alive is False

    @pytest.mark.asyncio
    async def test_vote_resolution_second_tie_exiles_nobody_and_enters_night(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-seer"),
            3: make_mock_role(3, "wolf-killer-villager"),
            4: make_mock_role(4, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="second-tie", roles=roles)
        engine._assign_roles()
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=1),
            VoteAction(voter_seat=2, target_seat=1),
            VoteAction(voter_seat=3, target_seat=2),
            VoteAction(voter_seat=4, target_seat=2),
        ]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.speak = AsyncMock(return_value="supplemental speech")

        async def revote(state, conversation_log, request):
            target = 1 if request.actor_seat in {1, 2} else 2
            return AcceptedAction(
                request=request,
                command=ActionCommand(action_type="vote", target_seat=target, reasoning=""),
            )

        for role in roles.values():
            role.request_action = AsyncMock(side_effect=revote)

        await engine._execute_vote_resolution()

        assert all(player.is_alive for player in engine.state.players.values())
        assert engine.sm.get_state() == GamePhase.NIGHT
        assert engine.state.vote_round == 1
        assert engine.state.is_tiebreak is False

    @pytest.mark.asyncio
    async def test_vote_resolution_all_abstain_skips_tiebreak_and_supplemental_speech(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-seer"),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="all-abstain", roles=roles)
        engine._assign_roles()
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=0),
            VoteAction(voter_seat=2, target_seat=None),
        ]
        assert engine.state.votes[0].target_seat is None
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.speak = AsyncMock(return_value="should not speak")

        await engine._execute_vote_resolution()

        assert engine.speak.await_count == 0
        assert engine.sm.get_state() == GamePhase.NIGHT
        assert engine.state.is_tiebreak is False

    @pytest.mark.asyncio
    async def test_get_night_deaths(self):
        engine = GameEngine(game_id="test")
        engine.state.round_number = 1
        engine.state.death_history = [
            DeathReport(player_seat=1, cause="wolf_kill", round_number=1),
            DeathReport(player_seat=2, cause="wolf_kill", round_number=0),
        ]
        deaths = engine.get_night_deaths()
        assert len(deaths) == 1
        assert deaths[0].player_seat == 1

    @pytest.mark.asyncio
    async def test_conversation_log_integration(self):
        """Verify ConversationLog is initialized and accessible."""
        engine = GameEngine(game_id="test")
        assert engine.conversation_log is not None
        assert len(engine.conversation_log.get_all()) == 0

        engine.conversation_log.add_public_speech(1, "wolf-killer-villager", "hello", 1, "speech")
        visible = engine.conversation_log.get_conversations_for_role(1, "wolf-killer-villager")
        assert len(visible) == 1

    @pytest.mark.asyncio
    async def test_game_logger_integration(self):
        """Verify GameLogger is initialized."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = GameEngine(game_id="test", data_dir=tmpdir)
            engine.game_logger.log_operation("test", "test_op", 1, "night", {"key": "val"})
            engine.game_logger.log_conversation("test",
                {"scope": "public", "content": "test", "round_number": 1})
            log_path = os.path.join(tmpdir, "games", "test", "game.log")
            conv_path = os.path.join(tmpdir, "games", "test", "conversation.log")
            assert os.path.exists(log_path)
            assert os.path.exists(conv_path)

    @pytest.mark.asyncio
    async def test_game_loop_visits_phases_then_terminates(self):
        engine = GameEngine(game_id="loop-test")
        engine._running = True
        calls = []

        async def step(next_phase=None, terminal=False):
            calls.append(engine.sm.get_state())
            if terminal:
                engine.sm.set_state(GamePhase.GAME_OVER)
            elif next_phase is not None:
                engine.sm.set_state(next_phase)

        async def night_step():
            if len([c for c in calls if c is GamePhase.NIGHT]) == 0:
                await step(GamePhase.DAWN)
            else:
                await step(GamePhase.GAME_OVER, terminal=True)
        async def dawn_step(): await step(GamePhase.LAST_WORDS)
        async def last_words_step(): await step(GamePhase.SPEECH)
        async def speech_step(): await step(GamePhase.VOTE_CASTING)
        async def casting_step(): await step(GamePhase.VOTE_RESOLUTION)
        async def resolution_step(): await step(GamePhase.NIGHT)

        engine._execute_night = AsyncMock(side_effect=night_step)
        engine._execute_dawn = AsyncMock(side_effect=dawn_step)
        engine._execute_last_words = AsyncMock(side_effect=last_words_step)
        engine._execute_speech_round = AsyncMock(side_effect=speech_step)
        engine._execute_vote_casting = AsyncMock(side_effect=casting_step)
        engine._execute_vote_resolution = AsyncMock(side_effect=resolution_step)
        engine.sm.set_state(GamePhase.NIGHT)

        await engine._game_loop()

        assert calls == [
            GamePhase.NIGHT, GamePhase.DAWN, GamePhase.LAST_WORDS,
            GamePhase.SPEECH, GamePhase.VOTE_CASTING, GamePhase.VOTE_RESOLUTION,
            GamePhase.NIGHT,
        ]

    @pytest.mark.asyncio
    async def test_game_loop_falls_through_unhandled_phases(self):
        engine = GameEngine(game_id="loop-fallthrough")
        engine._running = True
        seen = []

        async def wait():
            seen.append(engine.sm.get_state())
            if len(seen) == 2:
                engine.sm.set_state(GamePhase.GAME_OVER)

        engine._wait_if_paused = AsyncMock(side_effect=wait)
        engine.sm.set_state(GamePhase.SHERIFF_ELECTION)

        await engine._game_loop()

        assert seen == [GamePhase.SHERIFF_ELECTION, GamePhase.SHERIFF_ELECTION]
        assert engine.sm.is_terminal()

    @pytest.mark.asyncio
    async def test_night_without_scheduler_raises(self):
        engine = GameEngine(game_id="no-scheduler")
        with pytest.raises(ValueError, match="scheduler"):
            await engine._execute_night()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event", [
        {"event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 1}, "visibility": ("PUBLIC",), "extra": 1},
        {"event_type": "", "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 1}, "visibility": ("PUBLIC",)},
        {"event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 1}, "visibility": "PUBLIC"},
        {"event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 1}, "visibility": ("PUBLIC", 1)},
        {"event_type": "PLAYER_DIED", "payload": {"seat": 99, "cause": "wolf_kill", "round_number": 1}, "visibility": ("PUBLIC",)},
        {"event_type": "PLAYER_DIED", "payload": {"seat": 1, "cause": "wolf_kill", "round_number": 1}, "visibility": ("PUBLIC",)},
    ])
    async def test_pipeline_night_deaths_rejects_more_malformed_events(self, event):
        result = PipelineResult((), (), "digest", (event,), PipelineMode.V2)
        engine = GameEngine("malformed-more", pipeline_scheduler=object())
        engine.state.round_number = 1
        engine.state.players = {1: PlayerState(1, "r", "good", is_alive=False)}
        engine.state.death_history = [DeathReport(1, "wolf_kill", 1)]
        if event.get("payload", {}).get("seat") == 1 and len(event) == 3:
            engine.state.players[1].is_alive = True  # alive player with a death record
        engine._pending_night_completion = game_engine_module._PendingNightCompletion(result)
        with pytest.raises(PipelinePaused, match="invalid pipeline night event"):
            await engine._execute_night()

    @pytest.mark.asyncio
    async def test_resume_pipeline_night_skips_recheck_when_win_checked(self):
        engine = GameEngine("win-cached", pipeline_scheduler=object())
        engine.state.round_number = 1
        engine.sm.set_state(GamePhase.NIGHT)
        result = PipelineResult((), (), "digest", (), PipelineMode.V2)
        win = game_engine_module._PendingWin("good", "all_wolves_dead")
        engine._pending_night_completion = game_engine_module._PendingNightCompletion(
            result, deaths=(), stage=2, win_checked=True, win_result=win,
        )
        engine.rule_engine.check_win = MagicMock(side_effect=AssertionError("must not recheck"))
        engine.event_bus = MagicMock()
        engine.event_bus.publish = AsyncMock()
        engine.game_logger.log_game_over = MagicMock()
        await engine._execute_night()
        assert engine.sm.get_state() is GamePhase.GAME_OVER
        engine.game_logger.log_game_over.assert_called_once()
        assert engine._pending_night_completion is None

    @pytest.mark.asyncio
    async def test_resume_pipeline_night_transition_raise_marks_phase(self):
        engine = GameEngine("transition-raise", pipeline_scheduler=object())
        engine.state.round_number = 1
        engine.sm.set_state(GamePhase.NIGHT)
        result = PipelineResult((), (), "digest", (), PipelineMode.V2)
        engine._pending_night_completion = game_engine_module._PendingNightCompletion(
            result, deaths=(), stage=2, win_checked=True,
        )
        engine.rule_engine.check_win = MagicMock(return_value=None)
        original_transition = engine.sm.transition
        def explode(event):
            engine.sm.set_state(GamePhase.DAWN)
            raise RuntimeError("transition exploded")
        engine.sm.transition = explode
        with pytest.raises(RuntimeError, match="transition exploded"):
            await engine._execute_night()
        assert engine.state.phase is GamePhase.DAWN
        assert engine._pending_night_completion.stage == 7
        engine.sm.transition = original_transition
        engine.event_bus.publish = AsyncMock()
        await engine._execute_night()
        assert engine._pending_night_completion is None

    @pytest.mark.asyncio
    async def test_resume_pipeline_night_transition_raise_without_dawn_keeps_stage(self):
        engine = GameEngine("transition-raise-no-dawn", pipeline_scheduler=object())
        engine.state.round_number = 1
        engine.sm.set_state(GamePhase.NIGHT)
        engine.state.phase = GamePhase.NIGHT
        result = PipelineResult((), (), "digest", (), PipelineMode.V2)
        engine._pending_night_completion = game_engine_module._PendingNightCompletion(
            result, deaths=(), stage=2, win_checked=True,
        )
        engine.rule_engine.check_win = MagicMock(return_value=None)
        original_transition = engine.sm.transition
        def explode(event): raise RuntimeError("transition exploded")
        engine.sm.transition = explode
        with pytest.raises(RuntimeError, match="transition exploded"):
            await engine._execute_night()
        assert engine.state.phase is GamePhase.NIGHT
        assert engine._pending_night_completion.stage == 6
        engine.sm.transition = original_transition
        engine.event_bus.publish = AsyncMock()
        await engine._execute_night()
        assert engine._pending_night_completion is None

    @pytest.mark.asyncio
    async def test_speech_round_warns_when_player_has_no_role(self):
        roles = {1: make_mock_role(1, "wolf-killer-villager")}
        engine = GameEngine(game_id="speech-missing", roles=roles, event_bus=EventBus())
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.sm.set_state(GamePhase.SPEECH)
        engine.state.phase = GamePhase.SPEECH

        await engine._execute_speech_round()

        assert len(engine.state.speeches) == 1
        assert engine.sm.get_state() is GamePhase.VOTE_CASTING

    @pytest.mark.asyncio
    async def test_vote_casting_skips_none_votes(self):
        roles = {seat: make_mock_role(seat, "wolf-killer-villager", vote=VoteAction(seat, 2))
                 for seat in (1, 2)}
        engine = GameEngine(game_id="vote-none", roles=roles, event_bus=EventBus())
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.sm.set_state(GamePhase.VOTE_CASTING)
        engine.state.phase = GamePhase.VOTE_CASTING
        engine.vote = AsyncMock(side_effect=[VoteAction(1, 2), None])

        await engine._execute_vote_casting()

        assert len(engine.state.votes) == 1
        assert engine.sm.get_state() is GamePhase.VOTE_RESOLUTION

    @pytest.mark.asyncio
    async def test_tiebreak_skips_completed_speech_and_vote_steps(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="tiebreak-done", roles=roles, event_bus=EventBus(),
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.state.is_tiebreak = True
        engine.state.vote_round = 2
        engine.state.tiebreak_candidates = {1, 2}
        engine.state.supplemental_speakers = {1, 2}
        engine.state.voted_seats = {1, 2}
        engine.state.votes = []
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.rule_engine.check_win = MagicMock(return_value=None)
        engine.speak = AsyncMock(side_effect=AssertionError("no supplemental speech expected"))

        await engine._execute_vote_resolution()

        assert engine.state.is_tiebreak is False
        assert engine.sm.get_state() is GamePhase.NIGHT

    @pytest.mark.asyncio
    async def test_tiebreak_ignores_exile_of_missing_player(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="tiebreak-missing", roles=roles, event_bus=EventBus(),
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.state.is_tiebreak = True
        engine.state.vote_round = 2
        engine.state.tiebreak_candidates = {1, 2}
        engine.state.supplemental_speakers = {1, 2}
        engine.state.voted_seats = {1, 2}
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=99),
            VoteAction(voter_seat=2, target_seat=99),
        ]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.rule_engine.check_win = MagicMock(return_value=None)

        await engine._execute_vote_resolution()

        assert engine.sm.get_state() is GamePhase.NIGHT

    @pytest.mark.asyncio
    async def test_vote_resolution_announces_tie_final_when_resolution_returns_none(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="resolve-none", roles=roles, event_bus=EventBus(),
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=1),
            VoteAction(voter_seat=2, target_seat=1),
        ]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.rule_engine.check_win = MagicMock(return_value=None)
        engine.resolve_votes = MagicMock(return_value=None)

        await engine._execute_vote_resolution()

        assert any("无人被放逐" in record.content for record in engine.conversation_log.get_all())
        assert engine.sm.get_state() is GamePhase.NIGHT

    @pytest.mark.asyncio
    async def test_vote_resolution_ignores_exile_of_missing_player(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="exile-missing", roles=roles, event_bus=EventBus(),
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=99),
            VoteAction(voter_seat=2, target_seat=99),
        ]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.rule_engine.check_win = MagicMock(return_value=None)

        await engine._execute_vote_resolution()

        assert engine.sm.get_state() is GamePhase.NIGHT

    @pytest.mark.asyncio
    async def test_vote_resolution_clears_ballot_bookkeeping_after_exile(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="exile-clean", roles=roles, event_bus=EventBus(),
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
            3: PlayerState(3, "wolf-killer-villager", "good"),
        }
        engine.state.voted_seats = {1, 2, 3}
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=1),
            VoteAction(voter_seat=2, target_seat=1),
            VoteAction(voter_seat=3, target_seat=2),
        ]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.rule_engine.check_win = MagicMock(return_value=None)

        await engine._execute_vote_resolution()

        assert engine.state.players[1].is_alive is False
        assert engine.state.voted_seats == set()
        assert engine.state.vote_round == 1
        assert engine.state.is_tiebreak is False
        assert engine.sm.get_state() is GamePhase.NIGHT

    @pytest.mark.asyncio
    async def test_vote_resolution_game_over_skips_transition(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
            3: make_mock_role(3, "wolf-killer-seer"),
        }
        engine = GameEngine(game_id="vote-win", roles=roles, event_bus=EventBus(),
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")))
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
            3: PlayerState(3, "wolf-killer-seer", "good"),
        }
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=1),
            VoteAction(voter_seat=2, target_seat=1),
            VoteAction(voter_seat=3, target_seat=1),
        ]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)

        await engine._execute_vote_resolution()

        assert engine.sm.is_terminal()
        assert engine.state.win_result is not None
        assert engine.state.win_result["winning_camp"] == "good"

    def test_emergency_speech_variants(self):
        engine = GameEngine(game_id="emergency")
        engine.state.players = {1: PlayerState(1, "unknown-role", "good")}
        last_words = engine._emergency_speech(1, "last_words")
        assert "1号" in last_words and "玩家" in last_words  # unknown role falls back

        engine.state.players = {1: PlayerState(1, "wolf-killer-villager", "good", is_alive=False)}
        villager_words = engine._emergency_speech(1, "last_words")
        assert "出局" in villager_words

        engine.state.players = {
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        with_others = engine._emergency_speech(1, "day_speech")
        assert "我目前比较关注2号玩家的发言" in with_others

        engine.state.players = {1: PlayerState(1, "wolf-killer-villager", "good")}
        alone = engine._emergency_speech(1, "day_speech")
        assert "人数很少" in alone

    @pytest.mark.asyncio
    async def test_check_game_over_returns_false_without_win(self):
        engine = GameEngine(game_id="no-win")
        engine.rule_engine.check_win = MagicMock(return_value=None)
        assert await engine._check_game_over() is False
        assert engine.state.win_result is None

    @pytest.mark.asyncio
    async def test_vote_request_exception_returns_none(self):
        role = MagicMock()
        role.request_action = AsyncMock(side_effect=RuntimeError("llm down"))
        engine = GameEngine(game_id="vote-error", roles={1: role})
        engine.state.players = {1: PlayerState(1, "wolf-killer-villager", "good")}
        engine.state.phase = GamePhase.VOTE_CASTING

        assert await engine.vote(1) is None

    @pytest.mark.asyncio
    async def test_give_last_words_without_role_returns_none(self):
        engine = GameEngine(game_id="no-role-words", roles={})
        engine.state.players = {1: PlayerState(1, "wolf-killer-villager", "good", is_alive=False)}
        assert await engine.give_last_words(1, "exile", 1) is None
        assert await engine.give_last_words(99, "exile", 1) is None

