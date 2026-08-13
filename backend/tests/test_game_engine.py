import ast
import asyncio
import inspect

import pytest
import app.core.game_engine as game_engine_module
from unittest.mock import AsyncMock, MagicMock, patch
from app.core.game_engine import GameEngine
from app.models.game import GameState, GameConfig, GamePhase, PlayerState
from app.models.actions import NightAction, VoteAction, DeathReport
from app.models.contracts import AcceptedAction, ActionCommand, ActionContract, ActionRequest
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.action_validator import ActionValidationError
from app.agents.prompt_builder import PromptBuilder
from app.services.game_service import PUBLIC_NIGHT_SUBSTEPS
from app.config import PipelineMode
from app.core.role_pipeline import PipelineObservation, PipelineResult
from app.core.effect_applier import CommitResult
from app.core.scheduler import PipelinePaused, PointResult
from app.models.pipeline import SchedulePoint


def _night_substeps_emitted_by_engine_source() -> set[str]:
    """Return every literal night-progress step emitted by GameEngine itself."""
    source = inspect.getsource(GameEngine)
    tree = ast.parse(source)
    steps: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_broadcast_night_substep"
        ):
            continue
        assert node.args, "Every night-progress broadcast must name its step"
        step = node.args[0]
        assert isinstance(step, ast.Constant) and isinstance(step.value, str), (
            "Night-progress steps must be string literals so the public contract "
            "can be checked statically"
        )
        steps.add(step.value)
    return steps


class ScheduleStub:
    def __init__(self, result, mutate=None):
        self.result, self.mutate, self.calls = result, mutate, []

    def run_point(self, state, point):
        self.calls.append((state, point))
        if self.mutate is not None: self.mutate(state)
        return self.result


@pytest.mark.asyncio
async def test_schedule_point_freezes_mode_and_bridges_v1_once(monkeypatch) -> None:
    monkeypatch.setenv("ROLE_PIPELINE_V2", "v1")
    engine = GameEngine("bridge")
    monkeypatch.setenv("ROLE_PIPELINE_V2", "v2")
    calls = []
    async def legacy():
        calls.append("legacy"); engine.state.accepted_action_keys.add("accepted")
    result = await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION, legacy)
    assert type(result) is PipelineResult and result.mode is PipelineMode.V1
    assert result.accepted_actions == ("accepted",) and result.effects == result.public_events == ()
    assert calls == ["legacy"] and engine.pipeline_mode is PipelineMode.V1


@pytest.mark.asyncio
async def test_schedule_point_v2_skips_legacy_and_shadow_isolates_state() -> None:
    point_result = PointResult((), (), (), "v2-digest")
    v2_scheduler = ScheduleStub(point_result, lambda state: setattr(state, "round_number", 7))
    v2 = GameEngine("v2", pipeline_mode=PipelineMode.V2, pipeline_scheduler=v2_scheduler)
    async def forbidden(): raise AssertionError("legacy called")
    result = await v2.run_schedule_point(SchedulePoint.NIGHT_ACTION, forbidden)
    assert result.mode is PipelineMode.V2 and v2.state.round_number == 7

    shadow_scheduler = ScheduleStub(point_result, lambda state: setattr(state, "round_number", 99))
    shadow = GameEngine("shadow", pipeline_mode=PipelineMode.SHADOW, pipeline_scheduler=shadow_scheduler)
    async def legacy(): shadow.state.round_number = 2
    result = await shadow.run_schedule_point(SchedulePoint.NIGHT_ACTION, legacy)
    assert result.mode is PipelineMode.SHADOW and shadow.state.round_number == 2
    assert shadow_scheduler.calls[0][0] is not shadow.state


@pytest.mark.asyncio
async def test_schedule_point_propagates_errors_validates_types_and_does_not_block_loop() -> None:
    with pytest.raises(TypeError): GameEngine("bad", pipeline_mode="v1")
    engine = GameEngine("missing", pipeline_mode=PipelineMode.V2)
    with pytest.raises(ValueError):
        await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION, AsyncMock())
    engine = GameEngine("error", pipeline_mode=PipelineMode.V1)
    async def explode(): raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION, explode)
    with pytest.raises(TypeError): await engine.run_schedule_point("night", AsyncMock())
    with pytest.raises(TypeError): await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION, object())

    class BadPipeline:
        def __init__(self, *args): pass
        def run_points(self, *args): return object()
    monkeypatch = pytest.MonkeyPatch(); monkeypatch.setattr(game_engine_module, "RolePipeline", BadPipeline)
    try:
        with pytest.raises(TypeError, match="exact PipelineResult"):
            await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION, AsyncMock())
    finally: monkeypatch.undo()

    started, release = asyncio.Event(), asyncio.Event()
    async def waiting(): started.set(); await release.wait()
    task = asyncio.create_task(engine.run_schedule_point(SchedulePoint.NIGHT_ACTION, waiting))
    await started.wait(); await asyncio.sleep(0); assert not task.done()
    release.set(); assert type(await task) is PipelineResult


@pytest.mark.asyncio
async def test_schedule_points_batches_once_and_single_point_delegates() -> None:
    scheduler = ScheduleStub(PointResult((), (), (), "d"))
    engine = GameEngine("batch", pipeline_mode=PipelineMode.V2, pipeline_scheduler=scheduler)
    legacy = AsyncMock(side_effect=AssertionError("legacy called"))
    result = await engine.run_schedule_points(
        (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT), legacy,
    )
    assert type(result) is PipelineResult
    assert [point for _, point in scheduler.calls] == [
        SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT,
    ]
    scheduler.calls.clear()
    await engine.run_schedule_point(SchedulePoint.NIGHT_ACTION, legacy)
    assert [point for _, point in scheduler.calls] == [SchedulePoint.NIGHT_ACTION]
    legacy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [PipelineMode.V1, PipelineMode.SHADOW])
async def test_execute_night_prepares_once_and_legacy_runs_once(mode) -> None:
    scheduler = None if mode is PipelineMode.V1 else ScheduleStub(PointResult((), (), (), "d"))
    engine = GameEngine("night", pipeline_mode=mode, pipeline_scheduler=scheduler)
    engine.state.round_number = 4; engine.state.night_actions.append(object())
    engine.state.last_wolf_kill_target = 2
    seen = []
    async def legacy():
        seen.append((engine.state.round_number, tuple(engine.state.night_actions), engine.state.last_wolf_kill_target))
    engine._execute_night_legacy = legacy
    await engine._execute_night()
    assert seen == [(5, (), None)] and engine.state.round_number == 5
    if mode is PipelineMode.SHADOW:
        assert [point for _, point in scheduler.calls] == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT]
        assert all(state is not engine.state and state.round_number == 5 for state, _ in scheduler.calls)


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
    engine = GameEngine("v2-night", event_bus=bus, pipeline_mode=PipelineMode.V2, pipeline_scheduler=scheduler)
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-villager", "good"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
    }
    engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    engine._check_game_over = AsyncMock(return_value=False)
    engine._broadcast_phase_change = AsyncMock()
    engine.game_logger.log_deaths = MagicMock()
    memory = MagicMock(); engine.memory_service = memory
    await engine._execute_night()
    assert engine.state.round_number == 1 and len(engine.state.death_history) == 1
    assert [(item.player_seat, item.cause, item.round_number) for item in published] == [(1, "wolf_kill", 1)]
    engine.game_logger.log_deaths.assert_called_once()
    memory.save_memories.assert_called_once_with(engine.state)
    assert engine.sm.get_state() is GamePhase.DAWN
    engine._broadcast_phase_change.assert_awaited_once()


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
            seat = kwargs["death"].player_seat; self.attempts.append(seat)
            if seat == 2 and self.attempts.count(2) == 1: raise RuntimeError("transport")
            self.delivered.append(seat)
    scheduler, bus = RecoveringScheduler(), RecoveringBus()
    engine = GameEngine("recover", event_bus=bus, pipeline_mode=PipelineMode.V2, pipeline_scheduler=scheduler)
    engine.state.players = {seat: PlayerState(seat, "r", "good") for seat in (1, 2, 3)}
    engine.state.phase = GamePhase.NIGHT; engine.sm.set_state(GamePhase.NIGHT)
    engine.game_logger.log_deaths = MagicMock(side_effect=[RuntimeError("disk"), None])
    engine.memory_service = MagicMock(); engine._check_game_over = AsyncMock(return_value=False)
    engine._broadcast_phase_change = AsyncMock()
    with pytest.raises(RuntimeError, match="transport"): await engine._execute_night()
    with pytest.raises(RuntimeError, match="disk"): await engine._execute_night()
    await engine._execute_night()
    assert scheduler.calls == [SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT]
    assert engine.state.round_number == 1 and bus.attempts == [1, 2, 2]
    assert bus.delivered == [1, 2] and engine.game_logger.log_deaths.call_count == 2
    engine.memory_service.save_memories.assert_called_once_with(engine.state)
    engine._check_game_over.assert_awaited_once(); engine._broadcast_phase_change.assert_awaited_once()
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
        "malformed", pipeline_mode=PipelineMode.V2, pipeline_scheduler=scheduler,
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
    engine = GameEngine("validate", pipeline_mode=PipelineMode.V2, pipeline_scheduler=object())
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
async def test_start_resets_pending_night_completion() -> None:
    engine = GameEngine("reset"); engine._pending_night_completion = object()
    engine._game_loop = AsyncMock(); engine._broadcast_phase_change = AsyncMock()
    engine._assign_roles = MagicMock()
    await engine.start()
    assert engine._pending_night_completion is None


def make_mock_role(seat: int, role_name: str,
                   night_action: NightAction = None,
                   speech: str = "test speech",
                   vote: VoteAction = None,
                   chat_msg: str = "let's kill someone"):
    role = MagicMock()
    role.seat = seat
    role.role_name = role_name

    async def _speak(state, conversation_log, context):
        return speech

    async def _vote(state, conversation_log, context):
        if vote:
            return vote
        return VoteAction(voter_seat=seat, target_seat=None)

    async def _kill(state, conversation_log):
        if night_action and night_action.action_type == "kill":
            return night_action
        return NightAction(player_seat=seat, action_type="kill", target_seat=9)

    async def _save(state, conversation_log, wolf_target):
        return False  # Default: don't save

    async def _poison(state, conversation_log, wolf_target):
        if night_action and night_action.action_type == "poison":
            return night_action
        return NightAction(player_seat=seat, action_type="pass")

    async def _check(state, conversation_log):
        if night_action and night_action.action_type == "check":
            return night_action
        return NightAction(player_seat=seat, action_type="check", target_seat=1)

    async def _shoot(state, conversation_log):
        if night_action and night_action.action_type == "shoot":
            return night_action
        return NightAction(player_seat=seat, action_type="pass")

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
        action = night_action or NightAction(
            player_seat=seat, action_type="pass", target_seat=None
        )
        return AcceptedAction(
            request=request,
            command=ActionCommand(
                action_type=action.action_type,
                target_seat=action.target_seat,
                reasoning=action.reasoning,
            ),
        )

    async def _chat(state, conversation_log):
        return chat_msg

    role.speak = AsyncMock(side_effect=_speak)
    role.vote = AsyncMock(side_effect=_vote)
    role.request_action = AsyncMock(side_effect=_request_action)

    if "werewolf" in role_name:
        role.kill = AsyncMock(side_effect=_kill)
        role.chat = AsyncMock(side_effect=_chat)
    if "witch" in role_name:
        role.save = AsyncMock(side_effect=_save)
        role.poison = AsyncMock(side_effect=_poison)
    if "seer" in role_name:
        role.check = AsyncMock(side_effect=_check)
    if "hunter" in role_name:
        role.shoot = AsyncMock(side_effect=_shoot)

    return role


def make_9_mock_roles():
    roles = {}
    role_names = [
        (1, "wolf-killer-werewolf"), (2, "wolf-killer-werewolf"), (3, "wolf-killer-werewolf"),
        (4, "wolf-killer-villager"), (5, "wolf-killer-villager"), (6, "wolf-killer-villager"),
        (7, "wolf-killer-seer"), (8, "wolf-killer-witch"), (9, "wolf-killer-hunter"),
    ]
    for seat, role_name in role_names:
        target = 9 if "werewolf" in role_name else (2 if "seer" in role_name else None)
        action_type = "kill" if "werewolf" in role_name else ("check" if "seer" in role_name else "pass")
        roles[seat] = make_mock_role(seat, role_name,
            night_action=NightAction(player_seat=seat, action_type=action_type, target_seat=target),
            vote=VoteAction(voter_seat=seat, target_seat=1),
        )
    return roles


class TestGameEngine:
    @pytest.mark.asyncio
    async def test_engine_night_progress_steps_match_public_service_contract(self):
        """Every engine NIGHT_SUBSTEP reaches EventBus and is public-safe."""
        emitted_steps = _night_substeps_emitted_by_engine_source()
        captured_events = []
        bus = EventBus()

        async def capture(**kwargs):
            captured_events.append(kwargs)

        bus.subscribe(BusEvent.NIGHT_SUBSTEP, capture)
        engine = GameEngine(game_id="night-progress-contract", event_bus=bus)
        engine.state.round_number = 1

        for step in emitted_steps:
            await engine._broadcast_night_substep(step)

        captured_steps = {event["step"] for event in captured_events}
        assert captured_steps == emitted_steps
        assert captured_steps == PUBLIC_NIGHT_SUBSTEPS
        assert all(
            event["game_id"] == engine.game_id and event["round_number"] == 1
            for event in captured_events
        )

    @pytest.mark.asyncio
    async def test_witch_pass_then_poison_uses_one_request_per_night(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=3),
            ),
            2: make_mock_role(2, "wolf-killer-witch"),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="witch-pass-poison", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()
        responses = iter([
            {"action_type": "pass", "target_seat": None, "reasoning": "wait"},
            {"action_type": "poison", "target_seat": 1, "reasoning": "poison wolf"},
        ])

        async def witch_action(state, conversation_log, request):
            return engine.action_validator.validate_and_accept(
                state, request, next(responses),
            )

        roles[2].request_action = AsyncMock(side_effect=witch_action)

        await engine._execute_night()
        engine.sm.set_state(GamePhase.NIGHT)
        engine.state.phase = GamePhase.NIGHT
        await engine._execute_night()

        requests = roles[2].request_action.await_args_list
        assert len(requests) == 2
        assert all(
            request.args[2].contract.action_types == ("save", "poison", "pass")
            for request in requests
        )
        assert engine.state.players[2].has_antidote is True
        assert engine.state.players[2].has_poison is False
        witch_actions = [
            action for action in engine.state.night_actions if action.player_seat == 2
        ]
        assert [(action.action_type, action.target_seat) for action in witch_actions] == [
            ("poison", 1),
        ]

    @pytest.mark.asyncio
    async def test_witch_save_prevents_poison_and_resolver_gets_one_action(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=3),
            ),
            2: make_mock_role(2, "wolf-killer-witch"),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="witch-save", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()

        async def witch_action(state, conversation_log, request):
            assert request.contract.action_types == ("save", "poison", "pass")
            return engine.action_validator.validate_and_accept(
                state,
                request,
                {"action_type": "save", "target_seat": 3, "reasoning": "save target"},
            )

        roles[2].request_action = AsyncMock(side_effect=witch_action)
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        await engine._execute_night()

        witch_actions = [
            action for action in resolve.call_args.args[1]
            if action.request.actor_seat == 2
        ]
        assert roles[2].request_action.await_count == 1
        assert [action.command.action_type for action in witch_actions] == ["save"]
        assert engine.state.players[2].has_antidote is False
        assert engine.state.players[2].has_poison is True

    @pytest.mark.asyncio
    async def test_witch_poison_prevents_save_and_hides_wolf_target_without_antidote(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=3),
            ),
            2: make_mock_role(2, "wolf-killer-witch"),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="witch-poison", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()

        async def witch_action(state, conversation_log, request):
            assert request.contract.action_types == ("save", "poison", "pass")
            return engine.action_validator.validate_and_accept(
                state,
                request,
                {"action_type": "poison", "target_seat": 1, "reasoning": "poison wolf"},
            )

        roles[2].request_action = AsyncMock(side_effect=witch_action)

        engine.state.players[2].has_antidote = False
        prompt = PromptBuilder().build_action_prompt(
            engine.state,
            2,
            "wolf-killer-witch",
            engine.conversation_log,
            "witch_save",
            wolf_target=3,
        )
        engine.state.players[2].has_antidote = True

        await engine._execute_night()
        assert roles[2].request_action.await_count == 1
        assert engine.state.players[2].has_antidote is True
        assert engine.state.players[2].has_poison is False
        assert '"action_type":"save"' not in prompt
        assert "3号玩家" not in prompt

    @pytest.mark.asyncio
    async def test_witch_with_no_potions_is_not_requested(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=3),
            ),
            2: make_mock_role(2, "wolf-killer-witch"),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="witch-no-potions", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.players[2].has_antidote = False
        engine.state.players[2].has_poison = False
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()

        await engine._execute_night()

        roles[2].request_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_witch_invalid_save_target_becomes_pass_without_spending_antidote(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=3),
            ),
            2: make_mock_role(2, "wolf-killer-witch"),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="witch-invalid-save", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()

        async def invalid_save(state, conversation_log, request):
            with pytest.raises(ActionValidationError, match="save target"):
                engine.action_validator.validate_and_accept(
                    state,
                    request,
                    {"action_type": "save", "target_seat": 1, "reasoning": "wrong target"},
                )
            return engine.action_validator.safe_fallback(state, request)

        roles[2].request_action = AsyncMock(side_effect=invalid_save)

        await engine._execute_night()

        assert roles[2].request_action.await_count == 1
        assert engine.state.players[2].has_antidote is True
        assert engine.state.players[2].has_poison is True
        assert [
            action.action_type for action in engine.state.night_actions
            if action.player_seat == 2
        ] == ["pass"]

    @pytest.mark.asyncio
    async def test_role_assignment(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        assert len(engine.state.players) == 9
        wolves = [p for p in engine.state.players.values() if p.camp == "werewolf"]
        assert len(wolves) == 3

        witch = [p for p in engine.state.players.values() if "witch" in p.role]
        assert len(witch) == 1
        assert witch[0].has_antidote is True
        assert witch[0].has_poison is True

        hunter = [p for p in engine.state.players.values() if "hunter" in p.role]
        assert len(hunter) == 1
        assert hunter[0].has_gun is True

    @pytest.mark.asyncio
    async def test_camp_from_role(self):
        roles = make_9_mock_roles()
        engine = GameEngine(game_id="test", roles=roles)
        assert engine._camp_from_role("wolf-killer-werewolf") == "werewolf"
        assert engine._camp_from_role("wolf-killer-villager") == "good"
        assert engine._camp_from_role("wolf-killer-seer") == "good"

    @pytest.mark.asyncio
    async def test_find_player_by_role(self):
        roles = make_9_mock_roles()
        engine = GameEngine(game_id="test", roles=roles)
        engine._assign_roles()

        seer = engine._find_player_by_role("seer")
        assert seer is not None
        assert "seer" in seer.role

        nonexistent = engine._find_player_by_role("nonexistent")
        assert nonexistent is None

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
    async def test_night_death_and_game_over_events_are_game_scoped(self):
        bus = EventBus()
        deaths = []
        game_over = []

        async def record_death(**kwargs):
            deaths.append(kwargs)

        async def record_game_over(**kwargs):
            game_over.append(kwargs)

        bus.subscribe("player_died", record_death)
        bus.subscribe("game_over", record_game_over)
        roles = {
            1: make_mock_role(
                1,
                "wolf-killer-werewolf",
                night_action=NightAction(1, "kill", 3),
            ),
            2: make_mock_role(
                2,
                "wolf-killer-seer",
                night_action=NightAction(2, "check", 1),
            ),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="public-night-events", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.NIGHT)
        engine.state.phase = GamePhase.NIGHT
        engine._sleep_night_step = AsyncMock()

        await engine._execute_night()

        assert len(deaths) == 1
        assert len(game_over) == 1
        assert deaths[0]["game_id"] == engine.game_id
        assert game_over[0]["game_id"] == engine.game_id

    @pytest.mark.asyncio
    async def test_night_hunter_shot_emits_one_public_death_event(self):
        bus = EventBus()
        deaths = []

        async def record_death(**kwargs):
            deaths.append(kwargs)

        bus.subscribe("player_died", record_death)
        roles = {
            1: make_mock_role(
                1,
                "wolf-killer-werewolf",
                night_action=NightAction(1, "kill", 2),
            ),
            2: make_mock_role(
                2,
                "wolf-killer-hunter",
                night_action=NightAction(2, "shoot", 3),
            ),
            3: make_mock_role(3, "wolf-killer-villager"),
            4: make_mock_role(
                4,
                "wolf-killer-seer",
                night_action=NightAction(4, "check", 1),
            ),
        }
        engine = GameEngine(game_id="night-hunter-once", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.NIGHT)
        engine.state.phase = GamePhase.NIGHT
        engine._sleep_night_step = AsyncMock()

        await engine._execute_night()

        shot_events = [
            event for event in deaths if event["death"].player_seat == 3
        ]
        assert len(shot_events) == 1
        assert shot_events[0]["game_id"] == engine.game_id
        assert [event["death"].player_seat for event in deaths].count(2) == 1

    @pytest.mark.asyncio
    async def test_hunter_death_event_is_game_scoped(self):
        bus = EventBus()
        deaths = []

        async def record_death(**kwargs):
            deaths.append(kwargs)

        bus.subscribe("player_died", record_death)
        roles = {
            1: make_mock_role(
                1,
                "wolf-killer-hunter",
                night_action=NightAction(1, "shoot", 2),
            ),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="public-hunter-event", roles=roles, event_bus=bus)
        engine._assign_roles()

        death = await engine.hunter_shoot(1)

        assert death is not None
        assert len(deaths) == 1
        assert deaths[0]["game_id"] == engine.game_id

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
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        votes = [VoteAction(voter_seat=s, target_seat=1) for s in range(2, 10)]
        engine.state.votes = votes
        engine.state.players[1].is_alive = True

        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        await engine._execute_vote_resolution()
        assert engine.state.players[1].is_alive is False

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
    async def test_execute_night_round(self):
        roles = {}
        role_list = [
            (1, "wolf-killer-werewolf"), (2, "wolf-killer-werewolf"), (3, "wolf-killer-werewolf"),
            (4, "wolf-killer-villager"), (5, "wolf-killer-villager"), (6, "wolf-killer-villager"),
            (7, "wolf-killer-seer"), (8, "wolf-killer-witch"), (9, "wolf-killer-hunter"),
        ]
        for seat, role_name in role_list:
            target = 4 if "werewolf" in role_name else (2 if "seer" in role_name else None)
            action_type = "kill" if "werewolf" in role_name else ("check" if "seer" in role_name else "pass")
            roles[seat] = make_mock_role(seat, role_name,
                night_action=NightAction(player_seat=seat, action_type=action_type, target_seat=target),
            )

        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        # Manually set up players without random shuffle to match mock roles
        for seat, role_name in role_list:
            camp = "werewolf" if "werewolf" in role_name else "good"
            player = PlayerState(seat_number=seat, role=role_name, camp=camp)
            if "witch" in role_name:
                player.has_antidote = True
                player.has_poison = True
            if "hunter" in role_name:
                player.has_gun = True
            engine.state.players[seat] = player
        engine.sm.set_state(GamePhase.NIGHT)
        engine.state.phase = GamePhase.NIGHT
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        await engine._execute_night()

        assert engine.state.round_number == 1
        assert len(engine.state.night_actions) > 0
        assert all(
            isinstance(action, AcceptedAction)
            for action in resolve.call_args.args[1]
        )
        assert engine.sm.get_state() == GamePhase.DAWN

    @pytest.mark.asyncio
    async def test_accept_night_actions_converts_invalid_actions_before_resolver(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            7: make_mock_role(7, "wolf-killer-seer"),
        }
        engine = GameEngine(game_id="test", roles=roles)
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            4: PlayerState(4, "wolf-killer-villager", "good"),
            7: PlayerState(7, "wolf-killer-seer", "good"),
        }
        engine.state.phase = GamePhase.NIGHT

        accepted_actions = engine._accept_night_actions([
            NightAction(player_seat=1, action_type="poison", target_seat=4),
            NightAction(player_seat=7, action_type="check", target_seat=99),
        ])
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        deaths = engine.action_resolver.resolve(engine.state, accepted_actions)

        resolver_actions = resolve.call_args.args[1]
        assert deaths == []
        assert all(isinstance(action, AcceptedAction) for action in resolver_actions)
        assert all(not isinstance(action, NightAction) for action in resolver_actions)
        assert [action.command.action_type for action in resolver_actions] == ["pass", "pass"]
        assert all(action.command.target_seat is None for action in resolver_actions)

    @pytest.mark.asyncio
    async def test_execute_night_does_not_send_second_witch_potion_to_resolver(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=5),
            ),
            2: make_mock_role(2, "wolf-killer-witch"),
            3: make_mock_role(3, "wolf-killer-villager"),
            4: make_mock_role(
                4, "wolf-killer-seer",
                night_action=NightAction(player_seat=4, action_type="check", target_seat=1),
            ),
            5: make_mock_role(5, "wolf-killer-villager"),
        }
        async def save_action(state, conversation_log, request):
            state.players[2].has_antidote = False
            return AcceptedAction(
                request=request,
                command=ActionCommand(
                    action_type="save", target_seat=5, reasoning="x"
                ),
            )

        roles[2].request_action = AsyncMock(side_effect=save_action)
        engine = GameEngine(game_id="test", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        await engine._execute_night()

        resolver_actions = resolve.call_args.args[1]
        witch_actions = [
            action for action in resolver_actions
            if action.request.actor_seat == 2
        ]
        assert all(isinstance(action, AcceptedAction) for action in resolver_actions)
        assert [action.command.action_type for action in witch_actions] == ["save"]
        assert [action.action_type for action in engine.state.night_actions if action.player_seat == 2] == ["save"]
        assert engine.state.players[2].has_antidote is False
        assert engine.state.players[2].has_poison is True

    @pytest.mark.asyncio
    async def test_execute_night_records_safe_fallback_instead_of_invalid_raw_action(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
            ),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        roles[1].kill = AsyncMock(return_value=NightAction(
            player_seat=1, action_type="poison", target_seat=2,
        ))
        engine = GameEngine(game_id="test", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()

        await engine._execute_night()

        assert [(action.player_seat, action.action_type, action.target_seat) for action in engine.state.night_actions] == [
            (1, "pass", None),
        ]

    @pytest.mark.asyncio
    async def test_execute_night_records_each_wolf_kill_without_overwriting(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=3),
            ),
            2: make_mock_role(
                2, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=2, action_type="kill", target_seat=3),
            ),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="test", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()

        await engine._execute_night()

        assert [(action.player_seat, action.action_type, action.target_seat) for action in engine.state.night_actions] == [
            (1, "kill", 3),
            (2, "kill", 3),
        ]

    def test_accept_night_actions_rejects_duplicate_without_second_resolution(self):
        roles = {1: make_mock_role(1, "wolf-killer-werewolf")}
        engine = GameEngine(game_id="test", roles=roles)
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.state.phase = GamePhase.NIGHT
        action = NightAction(player_seat=1, action_type="kill", target_seat=2)
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        accepted_actions = engine._accept_night_actions([action])
        engine.action_resolver.resolve(engine.state, accepted_actions)
        duplicate_actions = engine._accept_night_actions([action])
        if duplicate_actions:
            engine.action_resolver.resolve(engine.state, duplicate_actions)

        assert [accepted.command.action_type for accepted in accepted_actions] == ["kill"]
        assert duplicate_actions == []
        assert resolve.call_count == 1

    def test_accept_night_actions_discards_missing_player_without_fallback(self, caplog):
        engine = GameEngine(game_id="test")
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-villager", "good"),
        }
        engine.state.phase = GamePhase.NIGHT
        missing_player_request = ActionRequest(
            actor_seat=99,
            role_id="wolf-killer-werewolf",
            contract=ActionContract(
                contract_id="werewolf_kill", phase=GamePhase.NIGHT,
                action_types=("kill", "pass"), actions_requiring_target=frozenset({"kill"}),
                resolution_priority=10, fallback_action_type="pass",
            ),
            phase=GamePhase.NIGHT, round_id=0,
            idempotency_key="0:night:99:werewolf_kill",
        )
        engine.action_validator.safe_fallback = MagicMock()
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        with patch(
            "app.core.game_engine.builtin_registry.build_requests",
            return_value=[missing_player_request],
        ):
            accepted_actions = engine._accept_night_actions([
                NightAction(player_seat=99, action_type="kill", target_seat=1),
            ])
        if accepted_actions:
            engine.action_resolver.resolve(engine.state, accepted_actions)

        assert accepted_actions == []
        assert engine.state.accepted_action_keys == set()
        assert engine.state.night_actions == []
        engine.action_validator.safe_fallback.assert_not_called()
        resolve.assert_not_called()
        assert "Discarding night action from missing player (seat=99)" in caplog.text

    def test_accept_night_actions_keeps_duplicate_bound_to_its_original_contract(self):
        roles = {1: make_mock_role(1, "wolf-killer-werewolf")}
        engine = GameEngine(game_id="test", roles=roles)
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.state.phase = GamePhase.NIGHT
        kill_request = ActionRequest(
            actor_seat=1,
            role_id="wolf-killer-werewolf",
            contract=ActionContract(
                contract_id="kill", phase=GamePhase.NIGHT,
                action_types=("kill",), actions_requiring_target=frozenset({"kill"}),
                resolution_priority=10, fallback_action_type="pass",
            ),
            phase=GamePhase.NIGHT, round_id=0, idempotency_key="0:night:1:kill",
        )
        check_request = ActionRequest(
            actor_seat=1,
            role_id="wolf-killer-werewolf",
            contract=ActionContract(
                contract_id="check", phase=GamePhase.NIGHT,
                action_types=("check", "pass"), actions_requiring_target=frozenset({"check"}),
                resolution_priority=20, fallback_action_type="pass",
            ),
            phase=GamePhase.NIGHT, round_id=0, idempotency_key="0:night:1:check",
        )
        action = NightAction(player_seat=1, action_type="kill", target_seat=2)
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        with patch(
            "app.core.game_engine.builtin_registry.build_requests",
            side_effect=([kill_request, check_request], [check_request], [check_request]),
        ), patch(
            "app.core.game_engine.builtin_registry.require",
            return_value=MagicMock(
                contracts=(kill_request.contract, check_request.contract),
            ),
        ):
            accepted_actions = engine._accept_night_actions([action])
            engine.state.night_actions = [action]
            engine.action_resolver.resolve(engine.state, accepted_actions)
            night_actions_before = list(engine.state.night_actions)
            accepted_keys_before = set(engine.state.accepted_action_keys)
            resolver_inputs_before = list(resolve.call_args_list)

            duplicate_actions = engine._accept_night_actions([action])
            if duplicate_actions:
                engine.action_resolver.resolve(engine.state, duplicate_actions)

            unknown_actions = engine._accept_night_actions([
                NightAction(player_seat=1, action_type="unknown", target_seat=None),
            ])
            if unknown_actions:
                engine.action_resolver.resolve(engine.state, unknown_actions)

        assert [accepted.command.action_type for accepted in accepted_actions] == ["kill"]
        assert duplicate_actions == []
        assert unknown_actions == []
        assert engine.state.night_actions == night_actions_before
        assert engine.state.accepted_action_keys == accepted_keys_before == {"0:night:1:kill"}
        assert resolve.call_args_list == resolver_inputs_before

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
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
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
        engine = GameEngine(game_id="first-tie", roles=roles)
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
        engine = GameEngine(game_id="resume-tiebreak", roles=roles)
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
    async def test_seer_check_result(self):
        engine = GameEngine(game_id="test")
        engine.state.players[1] = PlayerState(seat_number=1, role="wolf-killer-werewolf", camp="werewolf")
        engine.state.players[2] = PlayerState(seat_number=2, role="wolf-killer-villager", camp="good")

        assert engine.resolve_seer_check(NightAction(player_seat=7, action_type="check", target_seat=1)) == "werewolf"
        assert engine.resolve_seer_check(NightAction(player_seat=7, action_type="check", target_seat=2)) == "good"

    @pytest.mark.asyncio
    async def test_werewolf_kill(self):
        roles = {}
        for seat in [1, 2, 3]:
            roles[seat] = make_mock_role(seat, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=seat, action_type="kill", target_seat=4))

        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        for seat, role_name in [(1, "wolf-killer-werewolf"), (2, "wolf-killer-werewolf"),
                                 (3, "wolf-killer-werewolf"), (4, "wolf-killer-villager")]:
            camp = "werewolf" if "werewolf" in role_name else "good"
            engine.state.players[seat] = PlayerState(seat_number=seat, role=role_name, camp=camp)

        actions, target = await engine.werewolf_kill([1, 2, 3])
        assert len(actions) == 3
        assert target == 4

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
