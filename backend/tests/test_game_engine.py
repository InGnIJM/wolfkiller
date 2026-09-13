import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import app.core.game_engine as game_engine_module
from app.core.game_engine import GameEngine
from app.models.game import GameState, GameConfig, GamePhase, PlayerState
from app.models.actions import VoteAction, DeathReport, WinResult, is_last_words_eligible
from app.models.contracts import AcceptedAction, ActionCommand, ActionContract, ActionRequest
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.night_flow import DiscussionTurn, NightBriefing, WolfVote
from app.models.conversation import ConversationScope
from app.config import PipelineMode
from app.core.role_pipeline import PipelineResult
from app.core.effect_applier import CommitResult
from app.core.scheduler import PipelinePaused, PointResult
from app.models.pipeline import SchedulePoint


def test_engine_source_has_no_builtin_role_or_action_branches() -> None:
    source = Path("app/core/game_engine.py").read_text("utf-8")
    # The staged night driver legitimately references the werewolf/witch/seer
    # role ids to drive narration, discussion, votes and thinks, so only
    # action-specific branches remain forbidden here.
    for token in ("hunter", "werewolf_kill", "poison", "shoot"):
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
        self.settles = []
        self.registry = MagicMock(digest=digest)

    def run_point(self, state, point):
        self.calls.append((state, point))
        if self.mutate is not None: self.mutate(state)
        return self.result

    def settle_pending(self, state):
        self.settles.append(state)
        return None


class _FakeDirector:
    """Deterministic in-memory stand-in for NightDirector (no LLM)."""
    def __init__(self, *, speak: bool = True, vote: int | None = 4):
        self.speak, self.vote = speak, vote
        self.votes: list[WolfVote] = []
        self.vote_calls: list[int] = []
        self.discussion_briefings: list[object] = []
        self.vote_briefings: list[object] = []
    def narration(self, kind): return ("标题", "正文")
    def witch_narration(self, kill_target): return ("标题", "正文")
    def dawn_narration(self, deaths): return ("天亮了", "昨晚是平安夜，没有人死亡。" if not deaths else "昨晚有人死了。")
    def wolf_discussion_turn(self, state, seat, history, briefing=NightBriefing(), random_hint=None):
        self.discussion_briefings.append(briefing)
        return DiscussionTurn(seat, self.speak, "我怀疑2号" if self.speak else "")
    def wolf_vote_turn(self, state, seat, discussion, prior, briefing=NightBriefing(), random_hint=None):
        self.vote_calls.append(seat)
        self.vote_briefings.append(briefing)
        if self.vote is None:
            return WolfVote(seat, "pass", None, "观望")
        return WolfVote(seat, "kill", self.vote, "像神")
    def record_votes(self, votes): self.votes = list(votes)
    def collected_vote(self, seat):
        for vote in self.votes:
            if vote.seat == seat:
                return vote.to_command()
        return None


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
async def test_execute_night_prepares_once_and_runs_staged_night() -> None:
    scheduler = ScheduleStub(PointResult((), (), (), "d"))
    engine = GameEngine("night", pipeline_scheduler=scheduler, director=_FakeDirector())
    engine.state.round_number = 4; engine.state.night_actions.append(object())
    engine.state.last_wolf_kill_target = 2
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_night()
    assert engine.state.round_number == 5 and engine.state.night_actions == []
    assert engine.state.last_wolf_kill_target is None
    assert [point for _, point in scheduler.calls] == list(game_engine_module._NIGHT_POINTS)
    assert engine._pending_night_batch is None and engine._pending_night_completion is not None


def test_night_points_run_guard_action_before_wolf_vote() -> None:
    assert list(game_engine_module._NIGHT_POINTS) == [
        SchedulePoint.NIGHT_ACTION,
        SchedulePoint.NIGHT_WOLF_VOTE,
        SchedulePoint.NIGHT_WITCH_ACTION,
        SchedulePoint.NIGHT_SEER_ACTION,
        SchedulePoint.NIGHT_COMMIT,
    ]
    assert game_engine_module._NIGHT_POINT_STAGES == (1, 5, 7, 9, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_guard", [True, False])
async def test_staged_night_narrates_guard_open_only_on_guard_boards(with_guard) -> None:
    class RecordingDirector(_FakeDirector):
        def __init__(self):
            super().__init__()
            self.narration_kinds = []
        def narration(self, kind):
            self.narration_kinds.append(kind)
            return ("标题", "正文")
        def witch_narration(self, kill_target):
            self.narration_kinds.append("witch_open")
            return ("标题", "正文")

    role_counts = {
        "wolf-killer-werewolf": 3, "wolf-killer-villager": 3,
        "wolf-killer-seer": 1, "wolf-killer-witch": 1, "wolf-killer-hunter": 1,
    }
    if with_guard:
        role_counts["wolf-killer-guard"] = 1
    scheduler = ScheduleStub(PointResult((), (), (), "d"))
    director = RecordingDirector()
    engine = GameEngine(
        "guard-board" if with_guard else "no-guard-board",
        config=GameConfig(role_counts=role_counts),
        pipeline_scheduler=scheduler, director=director,
    )
    engine.state.players = {4: PlayerState(4, "wolf-killer-villager", "good")}
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        engine.state.round_number, 0, (), (), (),
    )
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_staged_night()
    expected = (["guard_open", "wolf_open", "witch_open", "seer_open"]
                if with_guard else ["wolf_open", "witch_open", "seer_open"])
    assert director.narration_kinds == expected
    assert [point for _, point in scheduler.calls] == list(game_engine_module._NIGHT_POINTS)


@pytest.mark.asyncio
async def test_execute_night_is_single_flight_for_concurrent_callers() -> None:
    entered, release = asyncio.Event(), asyncio.Event(); calls = []
    engine = GameEngine("single", pipeline_scheduler=object(), director=_FakeDirector())
    async def point(value):
        calls.append(value)
        if value is SchedulePoint.NIGHT_WOLF_VOTE: entered.set(); await release.wait()
        return PointResult((), (), (), value.value)
    engine._execute_v2_point = point; engine._resume_pipeline_night = AsyncMock()
    first = asyncio.create_task(engine._execute_night()); second = asyncio.create_task(engine._execute_night())
    await entered.wait(); release.set(); await asyncio.gather(first, second)
    assert calls == list(game_engine_module._NIGHT_POINTS)
    assert engine.state.round_number == 1 and engine._night_task is None


@pytest.mark.asyncio
async def test_cancelled_night_waiter_does_not_cancel_owner() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    engine = GameEngine("cancel", pipeline_scheduler=object(), director=_FakeDirector())
    async def point(value):
        if value is SchedulePoint.NIGHT_WOLF_VOTE: entered.set(); await release.wait()
        return PointResult((), (), (), value.value)
    engine._execute_v2_point = point
    waiter = asyncio.create_task(engine._execute_night()); await entered.wait(); waiter.cancel()
    with pytest.raises(asyncio.CancelledError): await waiter
    assert engine._night_task is not None and not engine._night_task.done()
    release.set(); await engine._night_task
    assert engine._night_task is None


@pytest.mark.asyncio
async def test_start_rejects_active_night_owner_and_stop_does_not_reset_state() -> None:
    release = asyncio.Event(); engine = GameEngine("lifecycle", pipeline_scheduler=object(), director=_FakeDirector())
    async def point(value):
        if value is SchedulePoint.NIGHT_WOLF_VOTE: await release.wait()
        return PointResult((), (), (), value.value)
    engine._execute_v2_point = point
    waiter = asyncio.create_task(engine._execute_night()); await asyncio.sleep(0)
    with pytest.raises(ValueError, match="night execution is active"): await engine.start()
    original = engine.state; await engine.stop(); assert engine.state is original
    release.set(); await waiter


@pytest.mark.asyncio
async def test_staged_night_resumes_only_failed_point_and_aggregates_exactly() -> None:
    points = game_engine_module._NIGHT_POINTS

    def commit(key, effects, event_type, digest):
        event = {"event_type": event_type, "payload": {}, "visibility": ("PUBLIC",)}
        return CommitResult(key, effects, 1, (event,), digest)

    commits = {
        points[0]: commit("a", ("e1",), "GUARD", "guard"),
        points[1]: commit("b", ("e2",), "FIRST", "wolf"),
        points[2]: commit("c", ("e3",), "SECOND", "witch"),
        points[3]: commit("d", ("e4",), "THIRD", "seer"),
        points[4]: commit("e", ("e5",), "FOURTH", "commit"),
    }
    class Scheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, point):
            inner.calls.append(point)
            if point is points[4] and inner.calls.count(point) == 1: raise RuntimeError("commit failed")
            c = commits[point]
            return PointResult((), (c,), c.events, c.state_digest)
    scheduler = Scheduler(); engine = GameEngine("checkpoint", pipeline_scheduler=scheduler, director=_FakeDirector())
    engine.run_schedule_point = AsyncMock(side_effect=AssertionError("mixed API used")); engine._resume_pipeline_night = AsyncMock()
    with pytest.raises(RuntimeError, match="commit failed"): await engine._execute_night()
    assert engine.state.round_number == 1 and engine._pending_night_batch.stage == 10
    await engine._execute_night()
    assert scheduler.calls == [*points, points[4]]
    pending = engine._pending_night_completion
    assert pending.result.accepted_actions == ("a", "b", "c", "d", "e") and pending.result.effects == ("e1", "e2", "e3", "e4", "e5")
    assert tuple(event["event_type"] for event in pending.result.public_events) == ("GUARD", "FIRST", "SECOND", "THIRD", "FOURTH")
    assert pending.result.state_digest == "commit" and pending.result.mode is PipelineMode.V2
    assert pending.result.diff is None and type(pending.result) is PipelineResult
    assert engine._pending_night_batch is None


@pytest.mark.asyncio
async def test_staged_night_resumes_wolf_voting_from_checkpointed_cursor() -> None:
    scheduler = ScheduleStub(PointResult((), (), (), "d"))
    director = _FakeDirector()
    engine = GameEngine("wolf-vote-resume", pipeline_scheduler=scheduler, director=director)
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        3: PlayerState(3, "wolf-killer-werewolf", "werewolf"),
        4: PlayerState(4, "wolf-killer-villager", "good"),
    }
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        engine.state.round_number, 4, (), (WolfVote(1, "pass", None, "观望"),),
        (PointResult((), (), (), "d"),),
    )
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_staged_night()
    assert director.vote_calls == [2, 3]


@pytest.mark.asyncio
async def test_staged_night_resume_with_all_wolves_voted_keeps_checkpointed_votes() -> None:
    scheduler = ScheduleStub(PointResult((), (), (), "d"))
    director = _FakeDirector()
    engine = GameEngine("wolf-vote-resume-all", pipeline_scheduler=scheduler, director=director)
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        3: PlayerState(3, "wolf-killer-werewolf", "werewolf"),
        4: PlayerState(4, "wolf-killer-villager", "good"),
    }
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    prior = (
        WolfVote(1, "kill", 4, "a"),
        WolfVote(2, "kill", 4, "b"),
        WolfVote(3, "kill", 4, "c"),
    )
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        engine.state.round_number, 4, (), prior,
        (PointResult((), (), (), "d"),),
    )
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_staged_night()
    assert director.vote_calls == []
    assert director.votes == list(prior)


@pytest.mark.asyncio
async def test_staged_night_point_that_commits_then_raises_is_retried_without_reprepare() -> None:
    points = game_engine_module._NIGHT_POINTS
    class Scheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, value):
            inner.calls.append(value)
            if value is points[0] and inner.calls.count(value) == 1:
                state.accepted_action_keys.add("stable-action"); raise RuntimeError("after commit")
            return PointResult((), (), (), value.value)
    scheduler = Scheduler(); engine = GameEngine("commit-boundary", pipeline_scheduler=scheduler, director=_FakeDirector())
    engine._resume_pipeline_night = AsyncMock()
    with pytest.raises(RuntimeError, match="after commit"): await engine._execute_night()
    await engine._execute_night()
    assert engine.state.round_number == 1
    assert scheduler.calls == [points[0], points[0], points[1], points[2], points[3], points[4]]


def test_pending_batch_is_frozen_exact_and_start_resets_checkpoints() -> None:
    result = PointResult((), (), (), "d")
    vote = WolfVote(1, "pass", None, "观望")
    batch = game_engine_module._PendingNightBatch(1, 2, ("1号：我怀疑2号",), (vote,), (result,))
    assert batch.stage == 2 and batch.raw_results == (result,)
    # Valid checkpoints at every raw_results boundary
    game_engine_module._PendingNightBatch(1, 0, (), (), ())
    game_engine_module._PendingNightBatch(1, 1, (), (), ())
    game_engine_module._PendingNightBatch(1, 2, (), (), (result,))
    game_engine_module._PendingNightBatch(1, 6, (), (), (result, result))
    game_engine_module._PendingNightBatch(1, 8, (), (), (result, result, result))
    game_engine_module._PendingNightBatch(1, 10, (), (), (result, result, result, result))
    game_engine_module._PendingNightBatch(1, 12, (), (), (result, result, result, result, result))
    sub = type("SubPoint", (PointResult,), {})((), (), (), "d")
    for call in (
        lambda: game_engine_module._PendingNightBatch(True, 0, (), (), ()),
        lambda: game_engine_module._PendingNightBatch(0, 0, (), (), ()),
        lambda: game_engine_module._PendingNightBatch(2_147_483_648, 0, (), (), ()),
        lambda: game_engine_module._PendingNightBatch(1, True, (), (), ()),
        lambda: game_engine_module._PendingNightBatch(1, -1, (), (), ()),
        lambda: game_engine_module._PendingNightBatch(1, 13, (), (), ()),
        lambda: game_engine_module._PendingNightBatch(1, 0, [], (), ()),
        lambda: game_engine_module._PendingNightBatch(1, 0, (1,), (), ()),
        lambda: game_engine_module._PendingNightBatch(1, 0, (), [vote], ()),
        lambda: game_engine_module._PendingNightBatch(1, 0, (), (object(),), ()),
        lambda: game_engine_module._PendingNightBatch(1, 0, (), (), []),
        lambda: game_engine_module._PendingNightBatch(1, 0, (), (), (object(),)),
        lambda: game_engine_module._PendingNightBatch(1, 0, (), (), (result,)),
        lambda: game_engine_module._PendingNightBatch(1, 2, (), (), ()),
        lambda: game_engine_module._PendingNightBatch(1, 7, (), (), (result,)),
        lambda: game_engine_module._PendingNightBatch(1, 10, (), (), (result, result)),
        lambda: game_engine_module._PendingNightBatch(1, 2, (), (), (sub,)),
        lambda: game_engine_module._PendingNightBatch(1, 0, (), (), (), (), 0),
    ):
        with pytest.raises((TypeError, ValueError)): call()


@pytest.mark.asyncio
async def test_staged_night_rejects_non_point_result_without_checkpoint() -> None:
    class Scheduler:
        def run_point(self, state, point): return object()
    engine = GameEngine("wrong-mode", pipeline_scheduler=Scheduler(), director=_FakeDirector())
    with pytest.raises(TypeError, match="exact PointResult"):
        await engine._execute_night()
    assert engine._pending_night_batch == game_engine_module._PendingNightBatch(1, 1, (), (), ())


@pytest.mark.asyncio
async def test_staged_night_observation_failure_reuses_checkpointed_raw_results() -> None:
    points = game_engine_module._NIGHT_POINTS
    bad = PointResult((), (), (), "commit")
    object.__setattr__(bad, "commits", (object(),))
    good = PointResult((), (), (), "action")
    class Scheduler:
        def __init__(self): self.calls = []
        def run_point(inner, state, point):
            inner.calls.append(point)
            return bad if point is points[4] else good
    scheduler = Scheduler(); engine = GameEngine("observe-fail", pipeline_scheduler=scheduler, director=_FakeDirector())
    for _ in range(2):
        with pytest.raises(TypeError, match="commit"):
            await engine._execute_night()
    assert scheduler.calls == list(points)
    assert engine._pending_night_batch.raw_results == (good, good, good, good, bad)


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
    engine = GameEngine("v2-night", event_bus=bus, pipeline_scheduler=scheduler, director=_FakeDirector())
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
    engine = GameEngine("recover", event_bus=bus, pipeline_scheduler=scheduler, director=_FakeDirector())
    engine.state.players = {seat: PlayerState(seat, "r", "good") for seat in (1, 2, 3)}
    engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    engine.game_logger.log_deaths = MagicMock(side_effect=[RuntimeError("disk"), None])
    engine.memory_service = MagicMock(); engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._broadcast_phase_change = AsyncMock()
    with pytest.raises(RuntimeError, match="transport"): await engine._execute_night()
    with pytest.raises(RuntimeError, match="disk"): await engine._execute_night()
    await engine._execute_night()
    assert scheduler.calls == list(game_engine_module._NIGHT_POINTS)
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
        "malformed", pipeline_scheduler=scheduler, director=_FakeDirector(),
    )
    engine.state.players = {1: PlayerState(1, "r", "good")}
    engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    engine.event_bus.publish = AsyncMock(); engine.game_logger.log_deaths = MagicMock()
    for _ in range(2):
        with pytest.raises(PipelinePaused, match="invalid pipeline night event"):
            await engine._execute_night()
    assert scheduler.calls == list(game_engine_module._NIGHT_POINTS)
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
    from app.core.night_flow import NightDirector
    from app.roles.registry import builtin_registry
    from app.models.pipeline import ActionCommand as PipelineActionCommand
    snapshot = builtin_registry.freeze()

    def invoke(messages, _tool_name, _schema, _seat):
        human = messages[1]["content"]
        if "投票" in human:
            return '{"schema_version": 1, "action_type": "kill", "target_seat": 4, "reasoning": "像神"}'
        if "讨论" in human:
            return '{"speak": true, "text": "刀4号"}'
        return '{"text": "考虑救人"}'

    director = NightDirector(snapshot, invoke)

    def provider(request, context, attempt):
        if request.contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE:
            command = director.collected_vote(request.actor_seat)
            return command if command is not None else PipelineActionCommand(
                action_type="pass", target_seat=None, reasoning="系统异常，本轮未行动",
            )
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
    engine = GameEngine("e2e-night", roles=roles, pipeline_scheduler=scheduler, director=director, data_dir=str(tmp_path))
    engine._assign_roles()
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(engine.state.round_number, 0, (), (), ())
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._broadcast_phase_change = AsyncMock()
    await engine._execute_staged_night()
    assert engine.state.round_number == 1
    assert engine.state.players[4].is_alive is False
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(4, "wolf_kill")]

    log_lines = (tmp_path / "games" / "e2e-night" / "game.log").read_text("utf-8").splitlines()
    audience = [json.loads(line) for line in log_lines
                if json.loads(line)["operation"] == "audience_action"]
    by_type = {record["data"]["event_type"]: record["data"]["payload"] for record in audience}
    assert by_type["WEREWOLF_KILL"] == {"target_seat": 4, "vote_counts": {"4": 3}}
    assert by_type["SEER_CHECK"] == {"target_seat": 1, "result": "werewolf"}
    assert by_type["WITCH_REASONING"]["action_type"] == "pass"
    assert by_type["WITCH_REASONING"]["target_seat"] is None
    assert by_type["SEER_REASONING"]["action_type"] == "check"
    assert by_type["SEER_REASONING"]["target_seat"] == 1
    assert {record["round"] for record in audience} == {1}


@pytest.mark.asyncio
async def test_staged_night_logs_full_operation_order(tmp_path) -> None:
    class OrderScheduler:
        def __init__(self): self.calls = []
        def run_point(self, state, point):
            self.calls.append(point)
            if point is SchedulePoint.NIGHT_WOLF_VOTE:
                kill_event = {
                    "event_type": "WEREWOLF_KILL",
                    "payload": {"target_seat": 4, "vote_counts": {"1": 1}},
                    "visibility": ("PUBLIC",),
                }
                commit = CommitResult("wolf", ("effect",), 1, (kill_event,), "wolf")
                return PointResult((), (commit,), commit.events, "wolf")
            if point is SchedulePoint.NIGHT_COMMIT:
                state.players[4].is_alive = False
                state.death_history.append(DeathReport(4, "wolf_kill", 1))
                death_event = {
                    "event_type": "PLAYER_DIED",
                    "payload": {"seat": 4, "cause": "wolf_kill", "round_number": 1},
                    "visibility": ("PUBLIC",),
                }
                commit = CommitResult("settle", ("effect",), 1, (death_event,), "final")
                return PointResult((), (commit,), commit.events, "final")
            return PointResult((), (), (), "action")

    engine = GameEngine("order", pipeline_scheduler=OrderScheduler(), director=_FakeDirector(), data_dir=str(tmp_path))
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
        4: PlayerState(4, "wolf-killer-villager", "good"),
        6: PlayerState(6, "wolf-killer-seer", "good"),
        7: PlayerState(7, "wolf-killer-witch", "good"),
    }
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(engine.state.round_number, 0, (), (), ())
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    await engine._execute_staged_night()

    records = [json.loads(line) for line in (tmp_path / "games" / "order" / "game.log").read_text("utf-8").splitlines()]
    night_ops = []
    for record in records:
        op = record["operation"]
        if op == "narration":
            night_ops.append(("narration", record["data"]["title"]))
        elif op == "audience_action":
            night_ops.append(("audience_action", record["data"]["event_type"]))
        elif op == "night_deaths":
            night_ops.append(("night_deaths", None))

    assert night_ops == [
        ("narration", "标题"),
        ("audience_action", "WOLF_VOTE"),
        ("audience_action", "WEREWOLF_KILL"),
        ("narration", "标题"),
        ("narration", "标题"),
        ("narration", "天亮了"),
        ("night_deaths", None),
    ]
    assert [record["phase"] for record in records if record["operation"] == "narration"] == [
        "night", "night", "night", "dawn",
    ]
    assert records[-1]["operation"] == "phase_change"


@pytest.mark.asyncio
async def test_staged_night_skip_discussion_and_no_thought_logs() -> None:
    director = _FakeDirector(speak=False)
    engine = GameEngine("skip-none", pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")), director=director)
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        6: PlayerState(6, "wolf-killer-seer", "good"),
        7: PlayerState(7, "wolf-killer-witch", "good"),
    }
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(engine.state.round_number, 0, (), (), ())
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_staged_night()
    assert len(director.votes) == 2
    assert engine._pending_night_completion is not None


class _TargetDirector(_FakeDirector):
    """Deterministic director whose discussion turns carry a kill target."""

    def __init__(self, targets: dict[int, int | None]):
        super().__init__()
        self.targets = targets
        self.discussion_calls: list[int] = []

    def wolf_discussion_turn(self, state, seat, history, briefing=NightBriefing(), random_hint=None):
        self.discussion_calls.append(seat)
        target = self.targets.get(seat)
        if target is None:
            return DiscussionTurn(seat, False)
        return DiscussionTurn(seat, True, f"我建议刀{target}号", target)


async def _run_discussion_game(tmp_path, targets: dict[int, int | None], seats: dict[int, tuple[str, str]], director=None):
    director = _TargetDirector(targets) if director is None else director
    engine = GameEngine("discuss", pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")), director=director, data_dir=str(tmp_path))
    engine.state.players = {
        seat: PlayerState(seat, role, camp) for seat, (role, camp) in seats.items()
    }
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(engine.state.round_number, 0, (), (), ())
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_staged_night()
    records = [json.loads(line) for line in (tmp_path / "games" / "discuss" / "game.log").read_text("utf-8").splitlines()]
    chats = [record for record in records
             if record["operation"] == "audience_action" and record["data"]["event_type"] == "WOLF_CHAT_MESSAGE"]
    return director, chats, engine


@pytest.mark.asyncio
async def test_staged_night_reuses_persisted_wolf_random_hint(tmp_path) -> None:
    class HintDirector(_TargetDirector):
        def __init__(self):
            super().__init__({1: None, 2: None})
            self.random_hints: list[int | None] = []

        def wolf_discussion_turn(
            self, state, seat, history, briefing=NightBriefing(), random_hint=None,
        ):
            self.random_hints.append(random_hint)
            return super().wolf_discussion_turn(
                state, seat, history, briefing, random_hint,
            )

    point = PointResult((), (), (), "d")
    director = HintDirector()
    engine = GameEngine(
        "persisted-hint", pipeline_scheduler=ScheduleStub(point),
        director=director, data_dir=str(tmp_path),
    )
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        4: PlayerState(4, "wolf-killer-villager", "good"),
    }
    engine.sm.set_state(GamePhase.NIGHT)
    engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        engine.state.round_number, 3, (), (), (point,), wolf_random_hint=4,
    )
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._resume_pipeline_night = AsyncMock()

    await engine._execute_staged_night()

    assert director.random_hints == [4, 4]


@pytest.mark.asyncio
async def test_staged_night_ends_discussion_when_wolves_unanimous(tmp_path) -> None:
    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        2: ("wolf-killer-werewolf", "werewolf"),
        3: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director, chats, engine = await _run_discussion_game(tmp_path, {1: 4, 2: 4, 3: 4}, seats)
    assert director.discussion_calls == [1, 2, 3]
    assert len(chats) == 3
    assert [record["data"]["payload"]["seat"] for record in chats] == [1, 2, 3]


@pytest.mark.asyncio
async def test_staged_night_skipped_wolves_count_as_consent(tmp_path) -> None:
    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        2: ("wolf-killer-werewolf", "werewolf"),
        3: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director, chats, engine = await _run_discussion_game(tmp_path, {1: 4, 2: None, 3: 4}, seats)
    assert director.discussion_calls == [1, 2, 3]
    assert len(chats) == 2


@pytest.mark.asyncio
async def test_staged_night_single_wolf_skips_discussion_and_votes_directly(tmp_path) -> None:
    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director, chats, engine = await _run_discussion_game(tmp_path, {1: 4}, seats)
    assert director.discussion_calls == []
    assert len(chats) == 0
    assert director.vote_calls == [1]


@pytest.mark.asyncio
async def test_staged_night_continues_discussion_without_unanimous_target(tmp_path) -> None:
    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        2: ("wolf-killer-werewolf", "werewolf"),
        3: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director, chats, engine = await _run_discussion_game(tmp_path, {1: 2, 2: 3, 3: 4}, seats)
    assert director.discussion_calls == [1, 2, 3, 1, 2, 3, 1, 2, 3]
    assert len(chats) == 9


@pytest.mark.asyncio
async def test_staged_night_writes_wolf_chat_into_conversation_log(tmp_path) -> None:
    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        2: ("wolf-killer-werewolf", "werewolf"),
        3: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director, chats, engine = await _run_discussion_game(tmp_path, {1: 4, 2: 4, 3: 4}, seats)
    assert len(chats) == 3
    wolf_records = [
        record for record in engine.conversation_log.get_all()
        if record.scope is ConversationScope.WEREWOLF
    ]
    assert [(record.speaker_seat, record.round_number) for record in wolf_records] == [
        (1, 1), (2, 1), (3, 1),
    ]
    assert all(record.speaker_role == "wolf-killer-werewolf" for record in wolf_records)
    assert [record.content for record in wolf_records] == [
        "我建议刀4号", "我建议刀4号", "我建议刀4号",
    ]


@pytest.mark.asyncio
async def test_staged_night_stores_day_plan_in_channel_and_discussion(tmp_path) -> None:
    class PlanDirector(_TargetDirector):
        def __init__(self, targets):
            super().__init__(targets)
            self.received_discussion = None

        def wolf_discussion_turn(self, state, seat, history, briefing=NightBriefing(), random_hint=None):
            self.discussion_calls.append(seat)
            target = self.targets.get(seat)
            if target is None:
                return DiscussionTurn(seat, False)
            return DiscussionTurn(
                seat, True, f"我建议刀{target}号", target, "明天白天带节奏踩9号",
            )

        def wolf_vote_turn(self, state, seat, discussion, prior, briefing=NightBriefing(), random_hint=None):
            self.received_discussion = discussion
            return super().wolf_vote_turn(state, seat, discussion, prior, briefing)

    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        2: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director, chats, engine = await _run_discussion_game(
        tmp_path, {1: 4, 2: 4}, seats, director=PlanDirector({1: 4, 2: 4}),
    )
    wolf_records = [
        record for record in engine.conversation_log.get_all()
        if record.scope is ConversationScope.WEREWOLF
    ]
    assert len(wolf_records) == 2
    assert all("明天白天带节奏踩9号" in record.content for record in wolf_records)
    assert any("明天白天带节奏踩9号" in line for line in director.received_discussion)


@pytest.mark.asyncio
async def test_staged_night_passes_day_and_channel_briefing_into_director(tmp_path) -> None:
    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        2: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director = _FakeDirector()
    engine = GameEngine(
        "briefing", pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")),
        director=director, data_dir=str(tmp_path),
    )
    engine.state.players = {
        seat: PlayerState(seat, role, camp) for seat, (role, camp) in seats.items()
    }
    engine.conversation_log.add_public_speech(4, "wolf-killer-villager", "我觉得2号可疑", 1, "speech")
    engine.conversation_log.add_werewolf_channel("1号：昨晚刀4号", 1)
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine.state.round_number = 1
    engine._prepare_night()  # night 2
    engine._pending_night_batch = game_engine_module._PendingNightBatch(engine.state.round_number, 0, (), (), ())
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_staged_night()

    assert director.discussion_briefings
    assert any("我觉得2号可疑" in line for line in director.discussion_briefings[0].public_lines)
    assert any("昨晚刀4号" in line for line in director.discussion_briefings[0].wolf_lines)
    assert director.vote_briefings
    assert any("我觉得2号可疑" in line for line in director.vote_briefings[0].public_lines)


@pytest.mark.asyncio
async def test_staged_night_logs_night_commit_audience_events(tmp_path) -> None:
    hunter_event = {
        "event_type": "HUNTER_REASONING",
        "payload": {
            "seat": 3, "action_type": "shoot", "target_seat": 9,
            "reasoning": "怀疑9号", "thought": "决定开枪带走 9 号玩家：怀疑9号",
        },
        "visibility": ("PUBLIC",),
    }

    class CommitScheduler:
        def run_point(self, state, point):
            if point is SchedulePoint.NIGHT_COMMIT:
                commit = CommitResult("hunter", ("effect",), 1, (hunter_event,), "final")
                return PointResult((), (commit,), commit.events, "final")
            return PointResult((), (), (), "other")

    engine = GameEngine("commit-aud", pipeline_scheduler=CommitScheduler(), director=_FakeDirector(), data_dir=str(tmp_path))
    engine.state.players = {
        3: PlayerState(3, "wolf-killer-hunter", "good"),
        4: PlayerState(4, "wolf-killer-villager", "good"),
    }
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(engine.state.round_number, 0, (), (), ())
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_staged_night()

    records = [json.loads(line) for line in (tmp_path / "games" / "commit-aud" / "game.log").read_text("utf-8").splitlines()]
    audience = [record for record in records if record["operation"] == "audience_action"]
    assert [record["data"]["event_type"] for record in audience] == ["HUNTER_REASONING"]
    assert audience[0]["phase"] == "night"


@pytest.mark.parametrize("leads,error", [
    (("a", 1), TypeError),
    ((1,), TypeError),
    ((1, 2, 3), TypeError),
    ((1, "4"), TypeError),
    ("not-a-tuple", TypeError),
    (((0, 1),), ValueError),
    (((1, 0),), ValueError),
])
def test_pending_night_batch_rejects_invalid_discussion_leads(leads, error) -> None:
    with pytest.raises(error):
        game_engine_module._PendingNightBatch(1, 0, (), (), (), leads)


def test_pending_night_batch_accepts_valid_discussion_leads() -> None:
    batch = game_engine_module._PendingNightBatch(1, 0, (), (), (), ((1, 4), (8, 4)))
    assert batch.discussion_leads == ((1, 4), (8, 4))


@pytest.mark.asyncio
async def test_staged_night_resumes_final_stage_without_rerunning_points() -> None:
    raw = PointResult((), (), (), "d")
    engine = GameEngine("stage-12", pipeline_scheduler=ScheduleStub(raw), director=_FakeDirector())
    engine.state.players = {4: PlayerState(4, "wolf-killer-villager", "good")}
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        engine.state.round_number, 12, (), (), (raw, raw, raw, raw, raw),
    )
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._resume_pipeline_night = AsyncMock()
    await engine._execute_staged_night()
    assert engine._pending_night_batch is None
    assert engine._pending_night_completion is not None
    assert engine._pending_night_completion.result.accepted_actions == ()
    assert engine._pending_night_completion.result.state_digest == "d"


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


@pytest.mark.asyncio
async def test_log_stage_audience_excludes_non_public_events(tmp_path) -> None:
    event = {"event_type": "WOLF_INTERNAL", "payload": {"x": 1}, "visibility": ("ACTOR",)}
    commit = CommitResult("wolf", (), 1, (event,), "digest")
    result = PointResult((), (commit,), (event,), "digest")
    engine = GameEngine(game_id="stage-aud", data_dir=str(tmp_path))
    await engine._log_stage_audience(result)
    assert not (tmp_path / "games" / "stage-aud" / "game.log").exists()


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
    async def test_give_last_words_uses_cause_specific_eligibility(self):
        roles = {1: make_mock_role(1, "wolf-killer-villager")}
        engine = GameEngine(game_id="test", roles=roles)
        engine._assign_roles()
        engine.state.players[1].is_alive = False

        assert await engine.give_last_words(1, "wolf_kill", 1)
        assert await engine.give_last_words(1, "wolf_kill", 1) is None  # already given
        assert await engine.give_last_words(1, "wolf_kill", 2) is None  # not first night
        assert await engine.give_last_words(1, "exile", 2) is not None  # exile any round
        assert is_last_words_eligible("hunter_shot", 1) is False
        assert await engine.give_last_words(1, "hunter_shot", 1) is None
        roles[1].speak.assert_awaited()

    @pytest.mark.asyncio
    async def test_last_words_scan_skips_hunter_shot_without_side_effects(self, tmp_path):
        bus = EventBus()
        speech_events = []

        async def record_speech(**kwargs):
            speech_events.append(kwargs)

        bus.subscribe(BusEvent.SPEECH_MADE, record_speech)
        role = make_mock_role(1, "wolf-killer-villager")
        engine = GameEngine(
            game_id="hunter-last-words", roles={1: role}, event_bus=bus,
            data_dir=str(tmp_path),
        )
        engine._assign_roles()
        engine.state.round_number = 2
        engine.state.players[1].is_alive = False
        engine.state.death_history = [DeathReport(1, "hunter_shot", 1)]
        engine.sm.set_state(GamePhase.LAST_WORDS)

        await engine._execute_last_words()

        role.speak.assert_not_awaited()
        assert engine.state.speeches == []
        assert engine.conversation_log.get_all() == []
        assert speech_events == []
        records = [
            json.loads(line)
            for line in (tmp_path / "games" / "hunter-last-words" / "game.log")
            .read_text("utf-8").splitlines()
        ]
        assert not any(
            record["operation"] == "speak"
            and record["seat"] == 1
            and record["phase"] == "last_words"
            for record in records
        )

    @pytest.mark.asyncio
    async def test_speak_preserves_none_as_eligibility_rejection(self):
        role = MagicMock()
        role.speak = AsyncMock(return_value=None)
        engine = GameEngine(game_id="speak-rejected", roles={1: role})

        assert await engine.speak(1, "last_words") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["", RuntimeError("provider unavailable")])
    async def test_speak_uses_emergency_speech_for_empty_or_exception(self, outcome):
        role = MagicMock()
        role.speak = AsyncMock(
            side_effect=outcome if isinstance(outcome, Exception) else None,
            return_value=None if isinstance(outcome, Exception) else outcome,
        )
        engine = GameEngine(game_id="speak-fallback", roles={1: role})
        engine.state.players = {1: PlayerState(1, "wolf-killer-villager", "good")}

        assert await engine.speak(1, "day_speech") == engine._emergency_speech(1, "day_speech")

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
    async def test_vote_resolution_resume_does_not_duplicate_existing_exile(self):
        engine = GameEngine(
            game_id="resume-exile", roles=make_9_mock_roles(),
            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "d")),
        )
        engine._assign_roles()
        engine.state.players[1].mark_dead("exile")
        engine.state.death_history.append(DeathReport(1, "exile", engine.state.round_number))
        engine.state.votes = [VoteAction(voter_seat=seat, target_seat=1) for seat in range(2, 10)]
        engine._run_exile_reaction = AsyncMock()
        engine.give_last_words = AsyncMock(return_value=None)
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)

        await engine._execute_vote_resolution()

        assert [
            death for death in engine.state.death_history
            if death.player_seat == 1 and death.cause == "exile"
        ] == [DeathReport(1, "exile", engine.state.round_number)]
        engine._run_exile_reaction.assert_awaited_once_with(1)
        engine.give_last_words.assert_awaited_once_with(1, "exile", engine.state.round_number)

    @pytest.mark.asyncio
    async def test_tiebreak_resume_does_not_duplicate_existing_exile(self):
        engine = GameEngine(game_id="resume-tiebreak", roles=make_9_mock_roles())
        engine._assign_roles()
        engine.state.players[1].mark_dead("exile")
        engine.state.death_history.append(DeathReport(1, "exile", engine.state.round_number))
        alive = set(engine.state.alive_players())
        engine.state.is_tiebreak = True
        engine.state.supplemental_speakers = set(alive)
        engine.state.voted_seats = set(alive)
        engine.resolve_votes = MagicMock(return_value=1)
        engine._run_exile_reaction = AsyncMock()
        engine.give_last_words = AsyncMock(return_value=None)
        engine._check_game_over = AsyncMock(return_value=True)

        await engine._execute_tiebreak([1, 2])

        assert [
            death for death in engine.state.death_history
            if death.player_seat == 1 and death.cause == "exile"
        ] == [DeathReport(1, "exile", engine.state.round_number)]
        engine._run_exile_reaction.assert_awaited_once_with(1)
        engine.give_last_words.assert_awaited_once_with(1, "exile", engine.state.round_number)

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
            def __init__(self):
                self.registry = MagicMock(digest="d" * 64)
                self.settles = []
            def run_point(self, state, point):
                calls.append((state, point))
                return PointResult((), (), (), "done")
            def settle_pending(self, state):
                self.settles.append(state)
                return None
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
        assert scheduler.settles == [engine.state]
        from app.core.point_journal import PointKey, point_journal
        key = PointKey("exile-react", 2, "vote_resolution", SchedulePoint.DAWN_REACTION, "d" * 64)
        saved = point_journal(engine.state).get(key)
        assert saved is not None and saved.cursor.kind == "response"
        assert saved.commits[0].events[0]["event_type"] == "PLAYER_DIED"
        assert saved.commits[0].events[0]["payload"]["cause"] == "exile"

    @pytest.mark.asyncio
    async def test_exile_reaction_keeps_an_existing_response_checkpoint(self):
        from app.core.point_journal import PointCheckpoint, PointKey, WorkCursor, point_journal

        scheduler = ScheduleStub(PointResult((), (), (), "done"), digest="d" * 64)
        engine = GameEngine("exile-resume", pipeline_scheduler=scheduler)
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        engine.state.phase = GamePhase.VOTE_RESOLUTION
        engine.state.round_number = 2
        key = PointKey("exile-resume", 2, "vote_resolution", SchedulePoint.DAWN_REACTION, "d" * 64)
        previous = PointCheckpoint(
            (), (), (CommitResult("response", (), 1, (), "saved"),), (), (), (),
            WorkCursor("done", 0, 0), {"state_digest": "saved"}, work_count=0,
        )
        point_journal(engine.state).put(key, previous)

        await engine._run_exile_reaction(1)

        assert point_journal(engine.state).get(key) is previous

    @pytest.mark.asyncio
    async def test_vote_resolution_retry_does_not_duplicate_exile_death(self, monkeypatch):
        roles = make_9_mock_roles()
        engine = GameEngine("exile-retry", roles=roles,
                            pipeline_scheduler=ScheduleStub(PointResult((), (), (), "done")))
        engine._assign_roles()
        engine.state.votes = [VoteAction(voter_seat=seat, target_seat=1) for seat in range(2, 10)]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        original = engine._run_exile_reaction
        failed = [False]

        async def interrupted(seat):
            if not failed[0]:
                failed[0] = True
                raise PipelinePaused("retry after exile")
            await original(seat)

        monkeypatch.setattr(engine, "_run_exile_reaction", interrupted)
        with pytest.raises(PipelinePaused, match="retry after exile"):
            await engine._execute_vote_resolution()
        await engine._execute_vote_resolution()

        assert [(item.player_seat, item.cause) for item in engine.state.death_history] == [(1, "exile")]

    @pytest.mark.asyncio
    async def test_exile_reaction_settles_reaction_damage_immediately(self):
        from app.core.action_resolver import ActionResolver
        from app.core.action_validator import ActionValidator
        from app.core.context_projector import ContextProjector
        from app.core.effect_applier import EffectApplier, _Runtime
        from app.core.scheduler import Scheduler
        from app.models.pipeline import ActionCommand as PipelineActionCommand
        from app.roles.registry import builtin_registry

        registry = builtin_registry.freeze()
        provider_calls = []
        def provider(request, context, attempt):
            provider_calls.append(request.contract.contract_id)
            return PipelineActionCommand(action_type="shoot", target_seat=2, reasoning="怀疑2号")
        scheduler = Scheduler(registry, ContextProjector(), ActionValidator(),
                              ActionResolver(), EffectApplier(), provider)
        bus = EventBus()
        published = []
        async def on_player_died(**kwargs):
            published.append(kwargs["death"])
        bus.subscribe(BusEvent.PLAYER_DIED, on_player_died)

        engine = GameEngine("exile-shot", event_bus=bus, pipeline_scheduler=scheduler)
        engine.state = GameState(
            "exile-shot", phase=GamePhase.VOTE_RESOLUTION, round_number=2,
            players={
                1: PlayerState(1, "wolf-killer-hunter", "good", is_alive=False),
                2: PlayerState(2, "wolf-killer-villager", "good"),
            },
        )
        engine.state._pipeline_runtime = _Runtime(role_resources={1: {"gun": 1}})
        engine.state.death_history.append(DeathReport(1, "exile", 2))

        await engine._run_exile_reaction(1)

        assert provider_calls == ["hunter_shoot"]
        assert engine.state.players[2].is_alive is False
        assert engine.state._pipeline_runtime.pending_damage == ()
        assert engine.state._pipeline_runtime.role_resources[1]["gun"] == 0
        assert [(d.player_seat, d.cause, d.round_number) for d in engine.state.death_history] == [
            (1, "exile", 2), (2, "hunter_shot", 2),
        ]
        assert [(d.player_seat, d.cause) for d in published] == [(2, "hunter_shot")]

    @pytest.mark.asyncio
    async def test_exile_reaction_pass_leaves_no_pending_damage(self):
        from app.core.action_resolver import ActionResolver
        from app.core.action_validator import ActionValidator
        from app.core.context_projector import ContextProjector
        from app.core.effect_applier import EffectApplier, _Runtime
        from app.core.scheduler import Scheduler
        from app.models.pipeline import ActionCommand as PipelineActionCommand
        from app.roles.registry import builtin_registry

        registry = builtin_registry.freeze()
        def provider(request, context, attempt):
            return PipelineActionCommand(action_type="pass", target_seat=None, reasoning="没有把握")
        scheduler = Scheduler(registry, ContextProjector(), ActionValidator(),
                              ActionResolver(), EffectApplier(), provider)
        engine = GameEngine("exile-pass", event_bus=EventBus(), pipeline_scheduler=scheduler)
        engine.state = GameState(
            "exile-pass", phase=GamePhase.VOTE_RESOLUTION, round_number=2,
            players={
                1: PlayerState(1, "wolf-killer-hunter", "good", is_alive=False),
                2: PlayerState(2, "wolf-killer-villager", "good"),
            },
        )
        engine.state._pipeline_runtime = _Runtime(role_resources={1: {"gun": 1}})
        engine.state.death_history.append(DeathReport(1, "exile", 2))

        await engine._run_exile_reaction(1)

        assert engine.state.players[2].is_alive is True
        assert engine.state._pipeline_runtime.pending_damage == ()
        assert engine.state._pipeline_runtime.role_resources[1]["gun"] == 1
        assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(1, "exile")]

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
    async def test_speech_round_starts_after_most_recent_death(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        for seat in (2, 7):
            engine.state.players[seat].is_alive = False
        engine.state.death_history = [
            DeathReport(player_seat=2, cause="exile", round_number=1),
            DeathReport(player_seat=7, cause="wolf_kill", round_number=2),
        ]
        engine.sm.set_state(GamePhase.SPEECH)

        orders, seats = [], []

        async def capture_speak(seat, kind):
            orders.append(list(engine.state.speaking_order))
            seats.append(seat)
            return "speech"

        engine.speak = capture_speak
        await engine._execute_speech_round()

        # Seat 7 died last: speeches wrap from the first alive seat after 7.
        assert orders[0] == [8, 9, 1, 3, 4, 5, 6]
        assert seats == [8, 9, 1, 3, 4, 5, 6]

    @pytest.mark.asyncio
    async def test_speech_order_wraps_when_anchor_is_last_seat(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.state.players[9].is_alive = False
        engine.state.death_history = [
            DeathReport(player_seat=9, cause="exile", round_number=1),
        ]
        engine.sm.set_state(GamePhase.SPEECH)

        orders = []

        async def capture_speak(seat, kind):
            orders.append(list(engine.state.speaking_order))
            return "speech"

        engine.speak = capture_speak
        await engine._execute_speech_round()

        assert orders[0] == [1, 2, 3, 4, 5, 6, 7, 8]

    @pytest.mark.asyncio
    async def test_speech_order_keeps_ascending_without_deaths(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.SPEECH)

        orders = []

        async def capture_speak(seat, kind):
            orders.append(list(engine.state.speaking_order))
            return "speech"

        engine.speak = capture_speak
        await engine._execute_speech_round()

        assert orders[0] == list(range(1, 10))

    @pytest.mark.asyncio
    async def test_reveal_on_death_announces_identity_publicly(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.config.reveal_on_death = True
        engine.state.players[2].mark_dead("exile")

        engine._reveal_on_death(2)
        engine._reveal_on_death(2)  # idempotent

        assert engine.state.players[2].revealed_role == "wolf-killer-werewolf"
        announcements = [
            record.content for record in engine.conversation_log.get_all()
            if record.scope.value == "public"
        ]
        assert announcements.count("2号玩家出局，身份是：Werewolf。") == 1

    @pytest.mark.asyncio
    async def test_reveal_on_death_falls_back_to_raw_role_for_unknown_id(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.config.reveal_on_death = True
        engine.state.players[2].role = "unknown-role-id"
        engine.state.players[2].mark_dead("exile")

        engine._reveal_on_death(2)

        assert engine.state.players[2].revealed_role == "unknown-role-id"
        announcements = [
            record.content for record in engine.conversation_log.get_all()
            if record.scope.value == "public"
        ]
        assert announcements == ["2号玩家出局，身份是：unknown-role-id。"]

    @pytest.mark.asyncio
    async def test_reveal_on_death_is_silent_by_default(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        assert engine.config.reveal_on_death is False
        engine.state.players[2].mark_dead("exile")

        engine._reveal_on_death(2)

        assert engine.state.players[2].revealed_role is None
        assert engine.conversation_log.get_all() == []

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
        phases = []

        async def record_phase(**kwargs):
            phases.append(kwargs["phase"])

        bus.subscribe(BusEvent.PHASE_CHANGED, record_phase)
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
    async def test_vote_resolution_first_tie_gives_all_alive_one_supplemental_speech_then_exiles_revote_winner(self, tmp_path):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
            3: make_mock_role(3, "wolf-killer-villager"),
            4: make_mock_role(4, "wolf-killer-villager"),
        }
        bus = EventBus()
        phases = []

        async def record_phase(**kwargs):
            phases.append(kwargs["phase"])

        bus.subscribe(BusEvent.PHASE_CHANGED, record_phase)
        engine = GameEngine(game_id="first-tie", roles=roles, event_bus=bus,
                            data_dir=str(tmp_path),
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
        records = [json.loads(line) for line in (tmp_path / "games" / "first-tie" / "game.log").read_text("utf-8").splitlines()]
        vote_results = [r for r in records if r["operation"] == "vote_result"]
        assert len(vote_results) == 2
        assert vote_results[0]["data"] == {"exiled": None, "tally": {"1": 2, "2": 2}}
        assert vote_results[1]["data"] == {"exiled": 1, "tally": {"1": 3}}
        assert phases.count(GamePhase.VOTE_CASTING) == 1
        phase_changes = [r for r in records if r["operation"] == "phase_change"]
        assert sum(r["data"]["new_phase"] == "vote_casting" for r in phase_changes) == 1

    @pytest.mark.asyncio
    async def test_execute_tiebreak_resumes_only_missing_speakers_and_voters(self, tmp_path):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-villager"),
            3: make_mock_role(3, "wolf-killer-villager"),
            4: make_mock_role(4, "wolf-killer-villager"),
        }
        bus = EventBus()
        phases = []

        async def record_phase(**kwargs):
            phases.append(kwargs["phase"])

        bus.subscribe(BusEvent.PHASE_CHANGED, record_phase)
        engine = GameEngine(game_id="resume-tiebreak", roles=roles, event_bus=bus,
                            data_dir=str(tmp_path),
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
        assert phases.count(GamePhase.VOTE_CASTING) == 1
        records = [json.loads(line) for line in (tmp_path / "games" / "resume-tiebreak" / "game.log").read_text("utf-8").splitlines()]
        assert sum(
            record["operation"] == "phase_change"
            and record["data"]["new_phase"] == "vote_casting"
            for record in records
        ) == 1

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
    async def test_vote_resolution_all_abstain_skips_tiebreak_and_supplemental_speech(self, tmp_path):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            2: make_mock_role(2, "wolf-killer-seer"),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="all-abstain", roles=roles, data_dir=str(tmp_path))
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
        records = [json.loads(line) for line in (tmp_path / "games" / "all-abstain" / "game.log").read_text("utf-8").splitlines()]
        vote_results = [r for r in records if r["operation"] == "vote_result"]
        assert len(vote_results) == 1
        assert vote_results[0]["data"] == {"exiled": None, "tally": {}}

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
    async def test_night_without_director_raises(self):
        engine = GameEngine(game_id="no-director", pipeline_scheduler=object())
        with pytest.raises(ValueError, match="director"):
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
    async def test_vote_casting_starts_all_votes_concurrently_and_keeps_seat_order(self):
        engine = GameEngine(game_id="parallel-votes", event_bus=EventBus())
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.sm.set_state(GamePhase.VOTE_CASTING)
        engine.state.phase = GamePhase.VOTE_CASTING
        started: set[int] = set()
        release = asyncio.Event()

        async def vote(seat: int):
            started.add(seat)
            if len(started) == 2:
                release.set()
            await asyncio.wait_for(release.wait(), timeout=0.1)
            return VoteAction(seat, 2)

        engine.vote = vote
        await engine._execute_vote_casting()

        assert started == {1, 2}
        assert [item.voter_seat for item in engine.state.votes] == [1, 2]

    def test_vote_casting_runtime_defaults_to_five_workers_and_500_seconds(self):
        engine = GameEngine(game_id="vote-runtime-defaults")

        assert engine._vote_concurrency == 5
        assert engine._vote_phase_timeout_seconds == 500.0

    @pytest.mark.parametrize(
        ("concurrency", "phase_timeout"),
        [(0, 0), (-1, -1.0), (True, False), ("5", "500")],
    )
    def test_vote_casting_invalid_runtime_limits_fall_back_to_safe_defaults(
        self, monkeypatch, concurrency, phase_timeout,
    ):
        monkeypatch.setattr(
            game_engine_module.app_config.game, "vote_concurrency", concurrency,
        )
        monkeypatch.setattr(
            game_engine_module.app_config.game,
            "vote_phase_timeout_seconds", phase_timeout,
        )

        engine = GameEngine(game_id="invalid-vote-runtime")

        assert engine._vote_concurrency == 5
        assert engine._vote_phase_timeout_seconds == 500.0

    @pytest.mark.asyncio
    async def test_vote_casting_limits_concurrency_to_five_and_keeps_seat_order(self):
        engine = GameEngine(game_id="five-vote-workers", event_bus=EventBus())
        engine.state.players = {
            seat: PlayerState(seat, "wolf-killer-villager", "good")
            for seat in range(1, 8)
        }
        engine.sm.set_state(GamePhase.VOTE_CASTING)
        engine.state.phase = GamePhase.VOTE_CASTING
        active = 0
        peak = 0
        started: list[int] = []
        first_batch_started = asyncio.Event()
        release = asyncio.Event()

        async def vote(seat: int):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.append(seat)
            if len(started) == 5:
                first_batch_started.set()
            await release.wait()
            active -= 1
            return VoteAction(seat, 1)

        engine.vote = vote
        run = asyncio.create_task(engine._execute_vote_casting())
        await asyncio.wait_for(first_batch_started.wait(), timeout=0.1)

        assert started == [1, 2, 3, 4, 5]
        assert peak == 5

        release.set()
        await run
        assert [item.voter_seat for item in engine.state.votes] == list(range(1, 8))

    @pytest.mark.asyncio
    async def test_vote_phase_timeout_marks_pending_votes_as_technical_abstentions(self):
        engine = GameEngine(game_id="vote-phase-timeout", event_bus=EventBus())
        engine._vote_phase_timeout_seconds = 0.1
        engine.state.players = {
            seat: PlayerState(seat, "wolf-killer-villager", "good")
            for seat in (1, 2, 3)
        }
        engine.sm.set_state(GamePhase.VOTE_CASTING)
        engine.state.phase = GamePhase.VOTE_CASTING
        cancelled: set[int] = set()
        never = asyncio.Event()

        async def vote(seat: int):
            if seat == 1:
                return VoteAction(1, 2)
            try:
                await never.wait()
            except asyncio.CancelledError:
                cancelled.add(seat)
                raise

        engine.vote = vote

        await asyncio.wait_for(engine._execute_vote_casting(), timeout=1.0)

        assert [(item.voter_seat, item.target_seat) for item in engine.state.votes] == [
            (1, 2), (2, None), (3, None),
        ]
        assert engine.state.voted_seats == {1, 2, 3}
        assert cancelled == {2, 3}
        assert engine.sm.get_state() is GamePhase.VOTE_RESOLUTION

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

        assert len(engine.state.votes) == 2
        assert engine.state.votes[1].target_seat is None
        assert engine.state.voted_seats == {1, 2}
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
    async def test_vote_request_exception_returns_technical_abstention(self):
        role = MagicMock()
        role.request_action = AsyncMock(side_effect=RuntimeError("llm down"))
        engine = GameEngine(game_id="vote-error", roles={1: role})
        engine.state.players = {1: PlayerState(1, "wolf-killer-villager", "good")}
        engine.state.phase = GamePhase.VOTE_CASTING

        vote = await engine.vote(1)
        assert vote == VoteAction(1, None, "technical abstain: invoke_error")

    @pytest.mark.asyncio
    async def test_give_last_words_without_role_returns_none(self):
        engine = GameEngine(game_id="no-role-words", roles={})
        engine.state.players = {1: PlayerState(1, "wolf-killer-villager", "good", is_alive=False)}
        assert await engine.give_last_words(1, "exile", 1) is None
        assert await engine.give_last_words(99, "exile", 1) is None


# ── wolf kill target recording (witch antidote fix) ──────────


def test_wolf_kill_target_extracts_only_wolf_damage() -> None:
    assert game_engine_module._wolf_kill_target(()) is None
    assert game_engine_module._wolf_kill_target(
        ({"target": 4, "amount": 1, "cause": "wolf_kill"},)
    ) == 4
    assert game_engine_module._wolf_kill_target(
        ({"target": 2, "amount": 1, "cause": "poison"},)
    ) is None
    assert game_engine_module._wolf_kill_target(
        (
            {"target": 2, "amount": 1, "cause": "poison"},
            {"target": 4, "amount": 1, "cause": "wolf_kill"},
        )
    ) == 4
    assert game_engine_module._wolf_kill_target((object(),)) is None
    assert game_engine_module._wolf_kill_target(
        ({"target": "x", "amount": 1, "cause": "wolf_kill"},)
    ) is None


@pytest.mark.asyncio
async def test_v2_night_records_wolf_kill_target_and_witch_save_rescues(tmp_path) -> None:
    from app.core.scheduler import Scheduler
    from app.core.context_projector import ContextProjector
    from app.core.action_validator import ActionValidator
    from app.core.action_resolver import ActionResolver
    from app.core.effect_applier import EffectApplier, role_resource_view
    from app.core.night_flow import NightDirector
    from app.roles.registry import builtin_registry
    from app.models.pipeline import ActionCommand as PipelineActionCommand
    snapshot = builtin_registry.freeze()

    def invoke(messages, _tool_name, _schema, _seat):
        human = messages[1]["content"]
        if "投票" in human:
            return '{"schema_version": 1, "action_type": "kill", "target_seat": 4, "reasoning": "像神"}'
        if "讨论" in human:
            return '{"speak": true, "text": "刀4号"}'
        return '{"text": "考虑救人"}'

    director = NightDirector(snapshot, invoke)

    def provider(request, context, attempt):
        if request.contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE:
            command = director.collected_vote(request.actor_seat)
            return command if command is not None else PipelineActionCommand(
                action_type="pass", target_seat=None, reasoning="系统异常，本轮未行动",
            )
        if request.contract.contract_id == "witch_action":
            target = context.facts.get("wolf_kill_target")
            return PipelineActionCommand(
                action_type="save" if target is not None else "pass",
                target_seat=target,
                reasoning="use antidote on the wolf target",
            )
        if request.contract.contract_id == "seer_check":
            return PipelineActionCommand(action_type="check", target_seat=1, reasoning="probe")
        return PipelineActionCommand(action_type="pass", target_seat=None, reasoning="")

    scheduler = Scheduler(snapshot, ContextProjector(), ActionValidator(), ActionResolver(), EffectApplier(), provider)
    roles = {seat: make_mock_role(seat, name) for seat, name in {
        1: "wolf-killer-werewolf", 2: "wolf-killer-werewolf", 3: "wolf-killer-werewolf",
        4: "wolf-killer-villager", 5: "wolf-killer-villager", 6: "wolf-killer-seer",
        7: "wolf-killer-witch", 8: "wolf-killer-hunter", 9: "wolf-killer-villager",
    }.items()}
    engine = GameEngine("e2e-night-save", roles=roles, pipeline_scheduler=scheduler, director=director, data_dir=str(tmp_path))
    engine._assign_roles()
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(engine.state.round_number, 0, (), (), ())
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._broadcast_phase_change = AsyncMock()
    await engine._execute_staged_night()

    assert engine.state.last_wolf_kill_target == 4
    assert engine.state.players[4].is_alive is True
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == []
    witch_seat = 7
    assert role_resource_view(engine.state, witch_seat) == {"antidote": 0, "poison": 1}

    log_lines = (tmp_path / "games" / "e2e-night-save" / "game.log").read_text("utf-8").splitlines()
    audience = [json.loads(line) for line in log_lines
                if json.loads(line)["operation"] == "audience_action"]
    by_type = {record["data"]["event_type"]: record["data"]["payload"] for record in audience}
    assert by_type["WEREWOLF_KILL"] == {"target_seat": 4, "vote_counts": {"4": 3}}
    assert by_type["WITCH_SAVE"] == {"target_seat": 4}
    assert by_type["WITCH_REASONING"]["action_type"] == "save"
    assert by_type["WITCH_REASONING"]["target_seat"] == 4
    assert by_type["SEER_REASONING"]["action_type"] == "check"
    assert by_type["SEER_REASONING"]["target_seat"] == 1



@pytest.mark.asyncio
async def test_tiebreak_restore_enters_missing_vote_phase_without_repeating_speech(tmp_path):
    roles = {
        1: make_mock_role(1, "wolf-killer-werewolf"),
        2: make_mock_role(2, "wolf-killer-villager"),
    }
    engine = GameEngine("restore-voting", roles=roles, data_dir=str(tmp_path))
    engine._assign_roles()
    engine.state.round_number = 1
    engine.state.is_tiebreak = True
    engine.state.vote_round = 2
    engine.state.tiebreak_candidates = {1, 2}
    engine.state.supplemental_speakers = {1, 2}
    engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine.speak = AsyncMock(side_effect=AssertionError("speech already checkpointed"))
    await engine._execute_tiebreak([1, 2])
    engine.speak.assert_not_awaited()
    for role in roles.values(): role.request_action.assert_awaited_once()
    assert engine.sm.get_state() is GamePhase.NIGHT
