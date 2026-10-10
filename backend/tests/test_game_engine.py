import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import app.core.game_engine as game_engine_module
from app.core.game_engine import GameEngine
from app.models.game import GameState, GameConfig, GamePhase, PlayerState
from app.models.actions import SpeechRecord, VoteAction, DeathReport, WinResult, is_last_words_eligible
from app.roles.registry import builtin_registry
from app.models.contracts import AcceptedAction, ActionCommand, ActionContract, ActionRequest
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.night_flow import DiscussionTurn, NightBriefing, WolfVote
from app.models.conversation import ConversationScope
from app.config import PipelineMode
from app.core.role_pipeline import PipelineResult
from app.core.effect_applier import CommitResult, _Runtime
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import PipelinePaused, PointResult
from app.models.pipeline import ActionContract, IssuedActionRequest, SchedulePoint


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


class ResourceSetupStub(ScheduleStub):
    """A stub standing in for ``Scheduler.issue``: running the night's first
    point is what establishes role resources (``initialize_role_resources`` is
    called from ``_issue_locked`` and nowhere else)."""

    def run_point(self, state, point):
        if point is SchedulePoint.NIGHT_ACTION:
            registry = builtin_registry.freeze()
            initialize_role_resources(state, registry.specs, registry.digest)
        return super().run_point(state, point)


class _FakeDirector:
    """Deterministic in-memory stand-in for NightDirector (no LLM)."""
    def __init__(self, *, speak: bool = True, vote: int | None = 4):
        self.speak, self.vote = speak, vote
        self.votes: list[WolfVote] = []
        self.vote_calls: list[int] = []
        self.discussion_calls: list[int] = []
        self.discussion_briefings: list[object] = []
        self.vote_briefings: list[object] = []
    def narration(self, kind): return ("标题", "正文")
    def wolf_open_narration(self, *, discusses): return ("标题", "正文")
    def witch_narration(self, kill_target): return ("标题", "正文")
    def dawn_narration(self, deaths): return ("天亮了", "昨晚是平安夜，没有人死亡。" if not deaths else "昨晚有人死了。")
    def wolf_discussion_turn(self, state, seat, history, briefing=NightBriefing(), random_hint=None):
        self.discussion_calls.append(seat)
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
    """The seer resolves **before** the witch.

    The official handbook gives the same-night check-and-poison outcome as a flat
    fact — 「若同一夜内预言家查验了恶灵骑士，女巫对恶灵骑士撒毒，则恶灵骑士免疫
    夜间伤害，**预言家会出局，女巫不会出局**」 — with no "if the check came
    first" qualifier, so the check is what claims the one-shot bounce. Resolving
    the seer first reproduces that outcome through the ordinary
    ``reflect_used`` one-shot rule; no board-specific branch is needed.

    Nothing else depends on the relative order: neither role reads the other's
    result, so this is observationally a no-op outside the reflect.
    """
    assert list(game_engine_module._NIGHT_POINTS) == [
        SchedulePoint.NIGHT_ACTION,
        SchedulePoint.NIGHT_WOLF_VOTE,
        SchedulePoint.NIGHT_SEER_ACTION,
        SchedulePoint.NIGHT_WITCH_ACTION,
        SchedulePoint.NIGHT_COMMIT,
    ]
    assert game_engine_module._NIGHT_POINT_STAGES == (1, 5, 7, 9, 10)


class _ScheduledPointTrace:
    """A scheduler stand-in that returns a canned result and real role specs."""

    def __init__(self, result, registry):
        self.result, self.registry, self.calls = result, registry, []

    def run_point(self, state, point):
        self.calls.append((state, point))
        return self.result

    def settle_pending(self, state):
        return None


def _issued(spec_or_id, seat: int):
    """One issued request; only ``role_id`` matters to the night script."""
    role_id = spec_or_id if isinstance(spec_or_id, str) else spec_or_id.role_id
    contracts = () if isinstance(spec_or_id, str) else spec_or_id.contracts
    contract = contracts[0] if contracts else ActionContract(
        contract_id=f"{role_id}_window",
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=1,
        action_types=("pass",),
        actions_requiring_target=frozenset(),
        fallback_action_type="pass",
    )
    return IssuedActionRequest(seat, role_id, contract, 0, 2, "night", "w", "k")


@pytest.mark.asyncio
async def test_staged_night_narrates_the_openings_of_the_roles_that_acted() -> None:
    """夜间开眼旁白按「今晚真的行动了的角色」播报，且由角色自己声明。

    旧实现按 `role_counts` 判断有没有守卫：同板里守卫已经死了也会播，而且只有守卫
    有这一句，其余夜间角色在时间轴上根本没有「请睁眼」这一拍。现在旁白来自
    `RoleSpec.opening_narration`，播报条件就是调度点的 requests —— 死了、还没解锁、
    或没有声明旁白的角色都不会被叫到。
    """
    snapshot = builtin_registry.freeze()
    guard = snapshot.require("wolf-killer-guard")
    dancer = snapshot.require("wolf-killer-dancer")

    class _BareSpec:
        contracts = ()
        opening_narration = ()

    specs = {
        guard.role_id: guard,
        dancer.role_id: dancer,
        "wolf-killer-bare": _BareSpec(),
        "wolf-killer-mock": MagicMock(),  # a malformed line must be skipped, not raised
    }
    requests = (
        _issued(dancer, 4),
        _issued(guard, 7),
        _issued(guard, 7),          # the same role twice is still one line
        _issued("wolf-killer-bare", 9),
        _issued("wolf-killer-mock", 10),
        _issued("wolf-killer-absent", 11),  # not in this registry at all
    )
    result = PointResult(requests, (), (), "d")
    scheduler = _ScheduledPointTrace(result, MagicMock(specs=specs, digest="d"))
    engine = GameEngine(
        "script", pipeline_scheduler=scheduler, director=_FakeDirector(),
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
    engine._narrate = AsyncMock()

    await engine._execute_staged_night()

    # 夜幕开场先播（stage 0），随后才是角色自己声明的台词；导演的其它节拍在最后。
    assert [call.args for call in engine._narrate.call_args_list][:3] == [
        ("标题", "正文"),
        ("舞者请睁眼", "请邀请三名玩家共舞。"),
        ("守卫请睁眼", "请选择今晚要守护的玩家。"),
    ]
    assert [point for _, point in scheduler.calls] == list(game_engine_module._NIGHT_POINTS)


class _NarrationDirector(_FakeDirector):
    """Names every beat after its own key so the emitted order is assertable."""

    def narration(self, kind): return (kind, kind)
    def wolf_open_narration(self, *, discusses):
        # Order is what this fake asserts, so the wolves' beat keeps one name
        # whether or not a discussion follows; the wording is decided by the
        # real NightDirector and is pinned in `test_night_flow.py`.
        return ("wolf_open", "wolf_open")
    def witch_narration(self, kill_target): return ("witch_open", kill_target)
    def dawn_narration(self, deaths): return ("dawn", deaths)


class _WolfBeatDirector(_FakeDirector):
    """Names the wolves' beat after the decision it was handed."""

    def wolf_open_narration(self, *, discusses):
        return ("wolf_open" if discusses else "wolf_solo", "x")


def _night_with(seats: dict[int, str]) -> GameEngine:
    """A staged night over a real registry, with the given seats and roles."""
    snapshot = builtin_registry.freeze()
    result = PointResult((), (), (), "d")
    scheduler = _ScheduledPointTrace(result, snapshot)
    engine = GameEngine(
        "beats", pipeline_scheduler=scheduler, director=_NarrationDirector(),
    )
    engine.state.players = {
        seat: PlayerState(seat, role_id, "good") for seat, role_id in seats.items()
    }
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        engine.state.round_number, 0, (), (), (),
    )
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._resume_pipeline_night = AsyncMock()
    engine._narrate = AsyncMock()
    return engine


def _beats(engine: GameEngine) -> list[str]:
    return [call.args[0] for call in engine._narrate.call_args_list]


@pytest.mark.asyncio
async def test_the_night_opens_before_any_role_is_woken() -> None:
    """每个夜晚的第一条旁白必须是夜幕开场。

    开眼旁白在 stage 1（`NIGHT_ACTION` 跑完之后，因为只有请求列表知道谁真的行动）
    播，引擎自己的节拍在 stage 2；夜幕开场从前跟着 stage 2 的狼人那句一起播，
    于是有 `NIGHT_ACTION` 角色的夜里观众先听到「守卫/猎魔人请睁眼」、再听到
    「天黑请闭眼」（近 200 局里 95 局命中，守卫板每夜如此）。
    """
    for seats in (
        {1: "wolf-killer-guard", 3: "wolf-killer-villager"},
        {1: "wolf-killer-villager", 2: "wolf-killer-werewolf"},
    ):
        engine = _night_with(seats)
        await engine._execute_staged_night()
        beats = _beats(engine)

        assert beats[0] == "night_open", (seats, beats)
        if "守卫请睁眼" in beats:
            # 角色台词仍排在狼人节拍之前（既有顺序不变）。
            assert beats.index("守卫请睁眼") < beats.index("wolf_open")


@pytest.mark.asyncio
async def test_the_wolf_beat_matches_whether_the_pack_will_discuss() -> None:
    """狼人那一拍不能承诺一场不会发生的讨论。

    stage 2 从前无条件播「开始讨论今晚的行动」，而讨论只在「存活狼窝 > 1 且未被封印」
    时进行：独狼（例如只剩石像鬼带刀）或被封印的夜里，观众听到「开始讨论」之后
    什么也没有。台词按 `len(wolves) > 1 and not _pack_sealed(wolves)` 选。
    """
    def night(seats):
        engine = _night_with(seats)
        for seat, role_id in seats.items():
            engine.state.players[seat].camp = (
                "werewolf" if role_id == "wolf-killer-werewolf" else "good"
            )
        engine._director = _WolfBeatDirector()
        return engine

    two = night({1: "wolf-killer-werewolf", 2: "wolf-killer-werewolf",
                 3: "wolf-killer-villager"})
    await two._execute_staged_night()
    assert "wolf_open" in _beats(two)

    solo = night({1: "wolf-killer-werewolf", 3: "wolf-killer-villager"})
    await solo._execute_staged_night()
    assert "wolf_solo" in _beats(solo)

    sealed = night({1: "wolf-killer-werewolf", 2: "wolf-killer-werewolf",
                    3: "wolf-killer-villager"})
    sealed._pack_sealed = MagicMock(return_value=True)
    await sealed._execute_staged_night()
    assert "wolf_solo" in _beats(sealed)


@pytest.mark.asyncio
async def test_the_seer_beat_precedes_the_seers_own_night() -> None:
    """「预言家请睁眼」现在排在预言家行动之前，女巫随后。

    The two director beats used to be swapped relative to their points: the
    witch was announced before the Seer even checked, and the Seer was announced
    *after* its own check had already been shown.
    """
    engine = _night_with({
        1: "wolf-killer-seer",
        2: "wolf-killer-witch",
        3: "wolf-killer-villager",
    })
    await engine._execute_staged_night()
    beats = _beats(engine)
    # 身份查验点的台词由角色声明（预言家声明的那句与引擎节拍逐字相同），女巫仍是
    # 引擎节拍：这条断言验的是「谁先被叫醒」，不是台词来自哪张表。
    assert beats.index("预言家请睁眼") < beats.index("witch_open")
    assert beats.index("wolf_open") < beats.index("预言家请睁眼")


@pytest.mark.asyncio
async def test_the_identity_check_line_comes_from_whoever_acts_there() -> None:
    """没有预言家的板子不该听到「预言家请睁眼」。

    查验点（`NIGHT_SEER_ACTION`）是共享的：纯白之女与狼巫都挂在这里，旧实现播的
    是引擎硬编码的预言家台词（2026-10-07 复盘 3.3，实测 113 个夜晚里播了 93 次
    「预言家请睁眼」，而那块板没有预言家）。
    """
    engine = _night_with({
        1: "wolf-killer-pure-white",
        2: "wolf-killer-wolf-witch",
        3: "wolf-killer-villager",
    })
    await engine._execute_staged_night()
    beats = _beats(engine)
    assert "seer_open" not in beats
    assert "纯白之女请睁眼" in beats and "狼巫请睁眼" in beats


@pytest.mark.asyncio
async def test_every_shared_point_speaks_through_its_own_roles() -> None:
    """共享点上的台词属于在场的角色，不是引擎写死的那个角色。

    查验点（预言家 / 纯白之女 / 狼巫 / 石像鬼）与用药点（女巫 / 狼美人）都是共享
    的：只声明了一部分角色时，另一部分会听到别人的台词。这条用例逐个板子验证
    「谁在场就播谁的台词」，且引擎节拍不插话。
    """
    cases = [
        # (seats, 期望出现的台词, 期望不出现的引擎节拍)
        ({1: "wolf-killer-gargoyle", 3: "wolf-killer-villager"},
         ["石像鬼请睁眼"], ["seer_open"]),
        ({1: "wolf-killer-wolf-beauty", 2: "wolf-killer-werewolf",
          3: "wolf-killer-villager"}, ["狼美人请睁眼"], ["witch_open", "seer_open"]),
        ({1: "wolf-killer-seer", 2: "wolf-killer-witch", 3: "wolf-killer-villager"},
         ["预言家请睁眼", "witch_open"], ["seer_open"]),
        ({1: "wolf-killer-pure-white", 2: "wolf-killer-wolf-witch",
          3: "wolf-killer-villager"}, ["纯白之女请睁眼", "狼巫请睁眼"], ["seer_open", "witch_open"]),
    ]
    for seats, present, absent in cases:
        engine = _night_with(seats)
        await engine._execute_staged_night()
        beats = _beats(engine)
        for line in present:
            assert line in beats, (seats, line, beats)
        for line in absent:
            assert line not in beats, (seats, line, beats)


@pytest.mark.asyncio
async def test_a_role_that_declares_no_line_keeps_the_engine_beat() -> None:
    """没有任何角色声明台词时，引擎节拍照旧兜底（旧档与未来角色不会静音）。"""
    from dataclasses import replace
    from types import SimpleNamespace

    engine = _night_with({1: "wolf-killer-seer", 3: "wolf-killer-villager"})
    snapshot = builtin_registry.freeze()
    engine._pipeline_scheduler.registry = SimpleNamespace(specs={
        **snapshot.specs,
        "wolf-killer-seer": replace(
            snapshot.require("wolf-killer-seer"), opening_narration=(),
        ),
    })

    await engine._execute_staged_night()

    assert "seer_open" in _beats(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize(("dead", "absent"), [
    ("wolf-killer-seer", "预言家请睁眼"),
    ("wolf-killer-witch", "witch_open"),
    ("wolf-killer-villager", "dawn"),
])
async def test_a_dead_role_is_not_woken(dead, absent) -> None:
    """死了就不播「请睁眼」——旧实现是引擎硬编码的节拍，死者照样被叫醒。"""
    others = [
        role_id for role_id in
        ("wolf-killer-seer", "wolf-killer-witch", "wolf-killer-villager")
        if role_id != dead
    ]
    seats = {1: dead}
    for index, role_id in enumerate(others, start=2):
        seats[index] = role_id
    engine = _night_with(seats)
    engine.state.players[1].is_alive = False
    await engine._execute_staged_night()
    beats = _beats(engine)
    if absent == "dawn":
        # A living seer and witch: both lines still play (the identity-check one
        # is the seer's declared line now, the witch's is the engine beat).
        assert "预言家请睁眼" in beats and "witch_open" in beats
    else:
        assert absent not in beats


@pytest.mark.asyncio
async def test_a_silenced_role_is_not_woken() -> None:
    """A seat sealed for the night never acts, so its line must not play."""
    from app.core.effect_applier import _Runtime

    engine = _night_with({
        1: "wolf-killer-seer",
        2: "wolf-killer-witch",
        3: "wolf-killer-villager",
    })
    runtime = getattr(engine.state, "_pipeline_runtime", None)
    if runtime is None:
        runtime = _Runtime()
        engine.state._pipeline_runtime = runtime
    runtime.statuses[1] = {"silenced"}
    await engine._execute_staged_night()
    beats = _beats(engine)
    assert "seer_open" not in beats
    assert "witch_open" in beats


@pytest.mark.asyncio
async def test_the_night_script_is_silent_without_a_usable_registry() -> None:
    """没有可读的注册表时只是不播旁白，绝不因为展示层的东西打断对局。"""
    bare = GameEngine("no-scheduler")
    bare._narrate = AsyncMock()
    await bare._narrate_role_openings(())
    assert bare._narrate.call_args_list == []

    stub = ScheduleStub(PointResult((), (), (), "d"))
    engine = GameEngine("mock-registry", pipeline_scheduler=stub)
    engine._narrate = AsyncMock()
    await engine._narrate_role_openings((_issued("wolf-killer-guard", 4),))
    assert engine._narrate.call_args_list == []


@pytest.mark.asyncio
async def test_execute_night_is_single_flight_for_concurrent_callers() -> None:
    entered, release = asyncio.Event(), asyncio.Event(); calls = []
    engine = GameEngine("single", pipeline_scheduler=object(), director=_FakeDirector())
    async def point(value, **_kwargs):
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
    async def point(value, **_kwargs):
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
    async def point(value, **_kwargs):
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
    # 10..13 are the deferred announcement stages a sheriff night parks in.
    game_engine_module._PendingNightCompletion(result, (death,), 0, 13)
    for call in (
        lambda: game_engine_module._PendingDeath(True, "x", 1),
        lambda: game_engine_module._PendingDeath(1, "wolf_kill", -1),
        lambda: game_engine_module._PendingDeath(1, "bad cause", 1),
        lambda: game_engine_module._PendingWin("good", "bad reason"),
        lambda: game_engine_module._PendingNightCompletion(object()),
        lambda: game_engine_module._PendingNightCompletion(result, (object(),)),
        lambda: game_engine_module._PendingNightCompletion(result, (), 1, 0),
        lambda: game_engine_module._PendingNightCompletion(result, (), 0, 15),
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
async def test_a_silenced_pack_skips_discussion_and_records_no_votes(tmp_path) -> None:
    """「恐惧到狼人 → 整个狼人阵营当夜无法袭击」的门禁。

    只要狼窝里有一人带封印标记，讨论与逐票都不再发生，``record_votes`` 收到空元组；
    stage 5 照跑（由 provider 降级为 pass），因此不会有任何狼刀伤害。
    """
    class RecordingScheduler:
        def __init__(self): self.points = []
        def run_point(self, state, point):
            self.points.append(point)
            return PointResult((), (), (), point.value)

        def settle_pending(self, state): return None

    class NarrationRecorder(_FakeDirector):
        def __init__(self):
            super().__init__()
            self.narrations: list[str] = []

        def narration(self, kind):
            self.narrations.append(kind)
            return super().narration(kind)

        def wolf_open_narration(self, *, discusses):
            # The wolves' beat has its own entry point (it is worded by whether
            # a discussion follows), so the recorder has to see it too.
            self.narrations.append("wolf_open")
            return super().wolf_open_narration(discusses=discusses)

    director = NarrationRecorder()
    scheduler = RecordingScheduler()
    engine = GameEngine(
        "sealed", pipeline_scheduler=scheduler, director=director, data_dir=str(tmp_path),
    )
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        4: PlayerState(4, "wolf-killer-villager", "good"),
        6: PlayerState(6, "wolf-killer-seer", "good"),
    }
    engine.sm.set_state(GamePhase.NIGHT); engine.state.phase = GamePhase.NIGHT
    engine._prepare_night()
    from app.core.effect_applier import _Runtime
    runtime = _Runtime()
    runtime.statuses[2] = {"silenced"}
    engine.state._pipeline_runtime = runtime
    engine._pending_night_batch = game_engine_module._PendingNightBatch(engine.state.round_number, 0, (), (), ())
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._broadcast_phase_change = AsyncMock()
    await engine._execute_staged_night()

    assert director.vote_calls == []
    assert director.discussion_briefings == []
    assert director.votes == []
    assert SchedulePoint.NIGHT_WOLF_VOTE in scheduler.points
    assert engine.state.death_history == []
    # The sealed branch announces itself instead of opening the wolf channel.
    assert "pack_sealed" in director.narrations
    assert "wolf_open" in director.narrations


@pytest.mark.asyncio
async def test_a_delayed_death_that_ends_the_game_stops_the_day(tmp_path) -> None:
    """延迟死亡落地后立刻判胜：不再为「已经没有人可投」的残局多走一轮投票。

    此前判胜要等到放逐投票之后，因此一个只差延迟死亡就结束的局面会多投一轮。
    """
    engine = GameEngine("delayed-over", pipeline_scheduler=None, director=_FakeDirector(), data_dir=str(tmp_path))
    engine.state.players = {
        4: PlayerState(4, "wolf-killer-villager", "good"),
        6: PlayerState(6, "wolf-killer-seer", "good"),
    }
    engine.state.round_number = 2
    engine.sm.set_state(GamePhase.SPEECH); engine.state.phase = GamePhase.SPEECH
    engine.memory_service = MagicMock()
    engine._broadcast_phase_change = AsyncMock()
    engine._publish_deaths = AsyncMock()
    from app.models.actions import WinResult

    engine.rule_engine.check_win = MagicMock(
        return_value=WinResult("werewolf", "all_villagers_dead"),
    )
    runtime = _Runtime()
    runtime.statuses[4] = {"delayed_death", "poisoned"}
    engine.state._pipeline_runtime = runtime

    interrupted = await engine._execute_speech_round()

    assert interrupted is False
    assert engine.state.players[4].is_alive is False
    assert engine.sm.get_state() is GamePhase.GAME_OVER
    assert engine.state.win_result is not None
    engine._publish_deaths.assert_awaited_once()


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
        # This scheduler carries no registry, so the engine cannot read declared
        # lines and keeps its own beats for every window (never silence a script
        # it cannot evaluate).
        ("narration", "标题"),   # 夜幕开场（stage 0），先于任何角色开眼
        ("narration", "标题"),
        ("audience_action", "WOLF_VOTE"),
        ("audience_action", "WEREWOLF_KILL"),
        ("narration", "标题"),
        ("narration", "标题"),
        ("narration", "天亮了"),
        ("night_deaths", None),
    ]
    assert [record["phase"] for record in records if record["operation"] == "narration"] == [
        "night", "night", "night", "night", "dawn",
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
    assert director.discussion_calls == [1, 2, 3, 1, 2, 3]
    assert len(chats) == 6
    assert [record["data"]["payload"]["seat"] for record in chats] == [1, 2, 3, 1, 2, 3]


@pytest.mark.asyncio
async def test_staged_night_skipped_wolves_count_as_consent(tmp_path) -> None:
    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        2: ("wolf-killer-werewolf", "werewolf"),
        3: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director, chats, engine = await _run_discussion_game(tmp_path, {1: 4, 2: None, 3: 4}, seats)
    assert director.discussion_calls == [1, 2, 3, 1, 2, 3]
    assert len(chats) == 4


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
async def test_staged_night_keeps_discussion_for_two_full_rounds_even_when_unanimous(tmp_path) -> None:
    seats = {
        1: ("wolf-killer-werewolf", "werewolf"),
        2: ("wolf-killer-werewolf", "werewolf"),
        3: ("wolf-killer-werewolf", "werewolf"),
        4: ("wolf-killer-villager", "good"),
    }
    director, chats, engine = await _run_discussion_game(tmp_path, {1: 4, 2: 4, 3: 4}, seats)
    assert director.discussion_calls == [1, 2, 3, 1, 2, 3]
    assert len(chats) == 6
    wolf_records = [
        record for record in engine.conversation_log.get_all()
        if record.scope is ConversationScope.WEREWOLF
    ]
    assert [(record.speaker_seat, record.round_number) for record in wolf_records] == [
        (1, 1), (2, 1), (3, 1), (1, 1), (2, 1), (3, 1),
    ]
    assert all(record.speaker_role == "wolf-killer-werewolf" for record in wolf_records)
    assert [record.content for record in wolf_records] == ["我建议刀4号"] * 6


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
    assert len(wolf_records) == 4
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
        # The fixture seats no werewolf, so the real rule engine would end the
        # game as soon as the speeches finish and the vote would never run.
        engine.rule_engine.check_win = MagicMock(return_value=None)

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
        assert engine.state.current_speaker is None

    @pytest.mark.asyncio
    async def test_speak_announces_the_seat_before_the_model_returns(self):
        role = MagicMock()

        async def finish_after_the_announcement(*_args):
            assert engine.state.current_speaker == 1
            return "我是一号，先把话说完再亮座位。"

        role.speak = finish_after_the_announcement
        bus = EventBus()
        announced: list[dict] = []

        async def capture(**kwargs):
            announced.append(kwargs)

        bus.subscribe(BusEvent.SPEAKING, capture)
        engine = GameEngine(game_id="speak-live", roles={1: role}, event_bus=bus)
        labels: list[str] = []

        async def remember(step_key: str) -> None:
            labels.append(step_key)

        engine._checkpoint_hook = remember

        assert await engine.speak(1, "day_speech") == "我是一号，先把话说完再亮座位。"
        assert labels == ["00000000:speaking:0:1:day_speech"]
        assert announced == [{"game_id": "speak-live", "seat": 1}]
        assert engine.state.current_speaker is None

    @pytest.mark.asyncio
    async def test_speak_publishes_speech_stream_chunks(self):
        role = MagicMock()

        async def stream_speech(_state, _log, _context, on_chunk=None):
            if on_chunk:
                await on_chunk("我是1号正在发言")
            return "我是1号正在发言，完整内容。"

        role.speak = stream_speech
        bus = EventBus()
        stream_events: list[dict] = []

        async def capture(**kwargs):
            stream_events.append(kwargs)

        bus.subscribe(BusEvent.SPEECH_STREAM, capture)
        engine = GameEngine(game_id="speak-stream", roles={1: role}, event_bus=bus)
        engine._checkpoint_hook = AsyncMock()

        res = await engine.speak(1, "day_speech")
        assert res == "我是1号正在发言，完整内容。"
        assert stream_events == [{"game_id": "speak-stream", "seat": 1, "text": "我是1号正在发言"}]

    @pytest.mark.asyncio
    async def test_speak_handles_mock_role_without_on_chunk(self):
        role = MagicMock()

        async def legacy_speak(_state, _log, _context):
            return "我是1号，旧接口发言。"

        role.speak = legacy_speak
        bus = EventBus()
        engine = GameEngine(game_id="speak-legacy", roles={1: role}, event_bus=bus)
        engine._checkpoint_hook = AsyncMock()

        res = await engine.speak(1, "day_speech")
        assert res == "我是1号，旧接口发言。"

    @pytest.mark.asyncio
    async def test_speak_keeps_the_seat_in_the_turn_after_a_plain_type_error(self):
        """A TypeError that is not about ``on_chunk`` must not be swallowed.

        The inner handler only retries without the streaming callback when the
        message mentions the argument; anything else is a real defect and is
        re-raised into the outer handler, which still owes the seat a turn.
        """
        role = MagicMock()

        async def broken_speak(_state, _log, _context, on_chunk=None):
            raise TypeError("role object is not callable")

        role.speak = broken_speak
        engine = GameEngine(game_id="speak-broken", roles={1: role}, event_bus=EventBus())
        engine._checkpoint_hook = AsyncMock()

        speech = await engine.speak(1, "day_speech")

        # The seat still speaks, and the fallback names only the seat: the
        # role's display name never reaches a public speech.
        assert speech
        assert speech.startswith("我是1号，")

    @pytest.mark.asyncio
    async def test_announce_actor_publishes_before_a_blocking_night_call(self, monkeypatch):
        bus = EventBus()
        announced: list[int] = []

        async def capture(**kwargs):
            announced.append(kwargs["seat"])

        bus.subscribe(BusEvent.ACTING, capture)
        engine = GameEngine(game_id="night-actor", event_bus=bus)

        engine.announce_actor_blocking(2)
        assert announced == []

        idle = asyncio.new_event_loop()
        engine._actor_loop = idle
        engine.announce_actor_blocking(2)
        idle.close()
        assert announced == []

        engine._actor_loop = asyncio.get_running_loop()
        scheduled: list[asyncio.Task] = []
        real_create = engine._actor_loop.create_task

        def tracking(coro, *args, **kwargs):
            task = real_create(coro, *args, **kwargs)
            scheduled.append(task)
            return task

        monkeypatch.setattr(engine._actor_loop, "create_task", tracking)
        engine.announce_actor_blocking(4)
        assert scheduled
        await scheduled[0]
        assert announced == [4]

        await asyncio.to_thread(engine.announce_actor_blocking, 6)
        assert announced == [4, 6]

    @pytest.mark.asyncio
    async def test_announce_actor_keeps_the_night_call_when_publish_fails(self, monkeypatch):
        engine = GameEngine(game_id="night-actor-fail")
        engine._actor_loop = asyncio.get_running_loop()

        def broken(coro, _loop):
            coro.close()
            raise RuntimeError("viewer unavailable")

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", broken)
        await asyncio.to_thread(engine.announce_actor_blocking, 7)

    @pytest.mark.asyncio
    async def test_speak_without_a_role_does_not_announce(self):
        bus = EventBus()
        announced: list[dict] = []

        async def capture(**kwargs):
            announced.append(kwargs)

        bus.subscribe(BusEvent.SPEAKING, capture)
        engine = GameEngine(game_id="speak-missing", roles={}, event_bus=bus)

        assert await engine.speak(1, "day_speech") is None
        assert announced == []

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
        engine._run_exile_reaction = AsyncMock(return_value=())
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
        engine._run_exile_reaction = AsyncMock(return_value=())
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
            return await original(seat)

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
    async def test_speech_round_skips_seats_already_recorded_this_round(self):
        bus = EventBus()
        speeches = []

        async def record_speech(**kwargs):
            speeches.append(kwargs)

        bus.subscribe(BusEvent.SPEECH_MADE, record_speech)
        roles = {
            1: make_mock_role(1, "wolf-killer-villager"),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="speech-resume", roles=roles, event_bus=bus)
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.rule_engine.check_win = MagicMock(return_value=None)
        engine.state.round_number = 1
        engine.state.speeches = [
            SpeechRecord(player_seat=1, text="already said", round_number=1),
        ]
        engine.conversation_log.add_public_speech(
            1, "wolf-killer-villager", "already said", 1, "speech",
        )
        engine.sm.set_state(GamePhase.SPEECH)
        engine.state.phase = GamePhase.SPEECH
        spoken = []

        async def capture_speak(seat, kind):
            spoken.append(seat)
            return f"speech from {seat}"

        engine.speak = capture_speak
        await engine._execute_speech_round()

        assert spoken == [2]
        assert [(record.player_seat, record.text) for record in engine.state.speeches] == [
            (1, "already said"),
            (2, "speech from 2"),
        ]
        assert [event["speech"].player_seat for event in speeches] == [2]
        public = [
            record for record in engine.conversation_log.records
            if record.speaker_seat == 1 and record.phase == "speech"
        ]
        assert len(public) == 1
        assert engine.sm.get_state() is GamePhase.VOTE_CASTING

    @pytest.mark.asyncio
    async def test_tiebreak_speech_round_still_asks_first_round_speakers(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-villager"),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="speech-tiebreak", roles=roles, event_bus=EventBus())
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.state.round_number = 1
        engine.state.is_tiebreak = True
        engine.state.vote_round = 2
        engine.state.speeches = [
            SpeechRecord(player_seat=1, text="round1 from 1", round_number=1),
            SpeechRecord(player_seat=2, text="round1 from 2", round_number=1),
        ]
        engine.state.supplemental_speakers = {1}
        engine.sm.set_state(GamePhase.SPEECH)
        engine.state.phase = GamePhase.SPEECH
        spoken = []

        async def capture_speak(seat, kind):
            spoken.append(seat)
            return f"supplemental from {seat}"

        engine.speak = capture_speak
        await engine._execute_speech_round()

        assert spoken == [2]
        assert [record.player_seat for record in engine.state.speeches] == [1, 2, 2]
        assert engine.state.supplemental_speakers == {1, 2}

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
    async def test_dawn_reads_out_only_the_nights_own_batch(self):
        """天亮播报只念夜死批次：夜死座位开的枪另有一拍，不能算作「昨夜」。"""
        roles = make_9_mock_roles()
        engine = GameEngine(game_id="test", roles=roles, event_bus=EventBus())
        engine._assign_roles()
        engine.phase_delay = 0.01
        engine.sm.set_state(GamePhase.DAWN)
        engine.state.round_number = 1
        engine.state.death_history.extend([
            DeathReport(player_seat=1, cause="wolf_kill", round_number=1),
            DeathReport(player_seat=2, cause="hunter_shot", round_number=1),
        ])
        engine.state.players[1].is_alive = False
        engine.state.players[2].is_alive = False

        await engine._execute_dawn()

        announcement = [
            record.content for record in engine.conversation_log.records
            if record.scope.value == "public" and "天亮了" in record.content
        ]
        assert announcement and "1 号" in announcement[-1]
        assert "2 号" not in announcement[-1]

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
        visible = engine.conversation_log.get_conversations_for_role(1, "good")
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
        engine.sm.set_state(GamePhase.ERROR)

        await engine._game_loop()

        assert seen == [GamePhase.ERROR, GamePhase.ERROR]
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
        # Seats no werewolf: the real rule engine would declare a good-camp win
        # as soon as the speeches end, and this test asserts the transition.
        engine.rule_engine.check_win = MagicMock(return_value=None)
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

    def test_vote_casting_runtime_defaults_to_five_workers_and_540_seconds(self):
        engine = GameEngine(game_id="vote-runtime-defaults")

        assert engine._vote_concurrency == 5
        # Two full retry ladders (90 + 120 + 60) for two batches of five workers.
        assert engine._vote_phase_timeout_seconds == 540.0

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
        assert "1号" in last_words and "出局" in last_words

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

    def test_emergency_speech_never_names_the_role(self):
        """The engine-level last resort must not reveal a hidden identity either."""
        specs = builtin_registry.freeze().specs
        names = {spec.display_name for spec in specs.values()}
        engine = GameEngine(game_id="emergency-secret")
        for role_id in specs:
            engine.state.players = {1: PlayerState(1, role_id, "good", is_alive=False)}
            text = engine._emergency_speech(1, "last_words")
            assert "1号" in text
            for name in names:
                assert name not in text, (role_id, name, text)

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


@pytest.mark.asyncio
async def test_first_night_roster_excludes_a_hidden_wolf_without_resources_yet() -> None:
    """A hidden wolf is outside the pack from the first night on.

    Role resources are established lazily, inside ``Scheduler.issue``, when the
    night's first schedule point runs. Deriving the pack list at the top of the
    staged night therefore read ``no_wolf_pack`` as absent and gave the Gargoyle
    the wolf chat, the kill vote and the roster line (game
    51f54f3e-9725-4e83-aa74-3d7241e508cb, night 1).
    """
    seats = {1: "wolf-killer-werewolf", 2: "wolf-killer-werewolf",
             3: "wolf-killer-gargoyle", 4: "wolf-killer-villager",
             5: "wolf-killer-villager", 6: "wolf-killer-seer"}
    scheduler = ResourceSetupStub(PointResult((), (), (), "d"))
    director = _FakeDirector(vote=4)
    engine = GameEngine("hidden-wolf-night-1", pipeline_scheduler=scheduler,
                        director=director)
    engine.state.players = {
        seat: PlayerState(
            seat, role,
            "werewolf" if role in ("wolf-killer-werewolf", "wolf-killer-gargoyle") else "good",
        )
        for seat, role in seats.items()
    }
    engine.state.phase = GamePhase.NIGHT
    engine.sm.set_state(GamePhase.NIGHT)
    engine._prepare_night()
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        engine.state.round_number, 0, (), (), (),
    )
    engine.memory_service = MagicMock()
    engine.rule_engine.check_win = MagicMock(return_value=None)
    engine._resume_pipeline_night = AsyncMock()

    await engine._execute_staged_night()

    assert scheduler.calls[0][1] is SchedulePoint.NIGHT_ACTION
    assert 3 not in director.discussion_calls
    assert set(director.discussion_calls) == {1, 2}
    assert director.vote_calls == [1, 2]
