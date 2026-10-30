"""Awakened Witch engine / night-flow plumbing that has no role-hook home."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.context_projector as context_projector
import app.core.game_engine as game_engine_module
from app.agents.prompt_renderer import PromptRenderer
from app.core.conversation_log import ConversationLog
from app.core.effect_applier import EffectApplier
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.core.night_flow import NightDirector
from app.core.role_pipeline import PipelineResult
from app.core.scheduler import PointResult
from app.models.conversation import ConversationScope
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand, IssuedActionRequest, SchedulePoint
from app.roles.awakened_witch import AWAKENED_WITCH_SPEC, BREW_PLAN_KEY
from app.roles.registry import RegistrySnapshot, builtin_registry
from app.services.game_service import GameService
from app.api.websocket.ws_handler import WSManager


def _game(round_number=1) -> GameState:
    return GameState(
        "assist", round_number=round_number, phase=GamePhase.NIGHT,
        config=GameConfig(),
        players={
            1: PlayerState(1, "wolf-killer-awakened-witch", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
            3: PlayerState(3, "wolf-killer-villager", "good"),
            4: PlayerState(4, "wolf-killer-villager", "good"),
        },
    )


# ── NightDirector assistant speech ─────────────────────────────────────────


def _director(invoke) -> NightDirector:
    return NightDirector(
        RegistrySnapshot(specs={}, digest="test"),
        lambda messages, _tool_name, _schema, _seat: invoke(messages),
    )


def test_assistant_prompt_names_the_target_group_and_own_seat() -> None:
    state = _game()
    director = _director(lambda messages: "{}")
    messages = director.assist_discussion_prompt(
        state, 1, 3, (1, 2), ("2号：先听3号",),
    )
    system, human = messages[0]["content"], messages[1]["content"]
    assert "协助者" in system and "毒药就不生效" in system
    assert "你是 1 号" in human
    assert "毒药目标是 3 号" in human
    assert "1号、2号" in human
    assert "2号：先听3号" in human
    assert '"speak": true' in human


def test_assistant_speech_turn_returns_clean_text() -> None:
    state = _game()
    director = _director(
        lambda messages: '{"speak": true, "text": "  先听3号再决定  "}'
    )
    assert director.assist_discussion_turn(state, 1, 3, (1, 2), ()).strip() == "先听3号再决定"


def test_assistant_speech_turn_respects_speak_false() -> None:
    state = _game()
    director = _director(
        lambda messages: '{"speak": false, "text": "这局不想说"}'
    )
    assert director.assist_discussion_turn(state, 1, 3, (1, 2), ()) == ""


def test_assistant_speech_turn_degrades_on_invalid_json() -> None:
    state = _game()
    director = _director(lambda messages: "not-json")
    assert director.assist_discussion_turn(state, 1, 3, (1, 2), ()) == ""


def test_assistant_speech_turn_degrades_on_invoke_failure() -> None:
    state = _game()

    def boom(_messages):
        raise RuntimeError("provider down")

    director = _director(boom)
    assert director.assist_discussion_turn(state, 1, 3, (1, 2), ()) == ""


# ── ConversationLog.add_night_intel ────────────────────────────────────────


def test_add_night_intel_is_visible_only_to_the_named_group() -> None:
    log = ConversationLog()
    record = log.add_night_intel(
        "3号：我同意", 2, [3, 2, 2], speaker_seat=3,
    )
    assert record.scope is ConversationScope.NIGHT_INTEL
    assert record.visible_to == [2, 3]
    assert record.speaker_seat == 3
    assert record.speaker_role is None
    assert [item.content for item in log.get_conversations_for_role(2, "good")] == [
        "3号：我同意"
    ]
    assert log.get_conversations_for_role(1, "good") == []
    assert log.get_conversations_for_role(3, "werewolf") == [record]


# ── The hook-only assist_votes derived fact ────────────────────────────────


def _patch_private(monkeypatch, plan, resources):
    monkeypatch.setattr(
        context_projector, "role_private_data_view",
        lambda state, seat: {"brew_plan": plan} if seat == 1 else {},
    )
    monkeypatch.setattr(
        context_projector, "role_resource_view",
        lambda state, seat: resources.get(seat, {}),
    )


def test_assist_votes_builder_handles_missing_and_malformed_plans(monkeypatch) -> None:
    state = _game()
    actor = state.players[1]
    _patch_private(monkeypatch, None, {})
    assert context_projector._assist_votes(state, actor) == ()
    _patch_private(monkeypatch, "not-a-mapping", {})
    assert context_projector._assist_votes(state, actor) == ()
    _patch_private(monkeypatch, {"assist_seats": "bad"}, {})
    assert context_projector._assist_votes(state, actor) == ()
    _patch_private(monkeypatch, {"assist_seats": (peer for peer in ())}, {})
    assert context_projector._assist_votes(state, actor) == ()


def test_assist_votes_builder_only_includes_integer_votes(monkeypatch) -> None:
    state = _game()
    actor = state.players[1]
    _patch_private(
        monkeypatch, {"assist_seats": ("x", 2, 3)},
        {2: {"assist_vote": 1}, 3: {"assist_vote": "bad"}},
    )
    assert context_projector._assist_votes(state, actor) == ({"seat": 2, "vote": 1},)


# ── Engine sub-steps ───────────────────────────────────────────────────────


def _engine(monkeypatch, private: dict):
    engine = GameEngine("assist-engine", pipeline_scheduler=MagicMock())
    engine._pipeline_scheduler.point_completed.return_value = False
    engine.state = _game()
    engine.conversation_log = ConversationLog()
    engine._pending_point_events = ()
    engine._log_audience_pairs = MagicMock()
    engine.announce_actor = AsyncMock()
    labels: list[str] = []
    executed: list[SchedulePoint] = []

    async def checkpoint(label):
        labels.append(label)

    async def execute(point, **_kwargs):
        executed.append(point)
        return PointResult((), (), (), "digest")

    monkeypatch.setattr(engine, "_durable_checkpoint", checkpoint)
    monkeypatch.setattr(engine, "_execute_v2_point", execute)
    monkeypatch.setattr(
        game_engine_module, "role_private_data_view",
        lambda state, seat: private.get(seat, {}),
    )
    director = MagicMock()
    director.assist_discussion_turn.return_value = ""
    engine._director = director
    return engine, labels, executed, private, director


@pytest.mark.asyncio
async def test_engine_assist_steps_no_plan_does_nothing(monkeypatch) -> None:
    engine, labels, executed, _private, _director = _engine(monkeypatch, {})
    await engine._run_assist_steps()
    assert labels == [] and executed == []


@pytest.mark.asyncio
async def test_engine_assist_steps_pick_then_none_returns(monkeypatch) -> None:
    private = {1: {"brew_plan": {"round": 1, "assist_seats": (), "target": 3}}}
    engine, labels, executed, private, _director = _engine(monkeypatch, private)
    original_execute = engine._execute_v2_point

    async def execute(point, **_kwargs):
        executed.append(point)
        private[1] = {}
        return PointResult((), (), (), "digest")

    engine._execute_v2_point = execute
    await engine._run_assist_steps()
    assert executed == [SchedulePoint.NIGHT_WITCH_ASSIST_PICK]
    assert labels == ["night_assist:night_witch_assist_pick"]


@pytest.mark.asyncio
async def test_engine_assist_steps_pick_then_empty_seats_returns(monkeypatch) -> None:
    private = {1: {"brew_plan": {"round": 1, "assist_seats": (), "target": 3}}}
    engine, labels, executed, private, _director = _engine(monkeypatch, private)

    async def execute(point, **_kwargs):
        executed.append(point)
        private[1] = {"brew_plan": {"round": 1, "assist_seats": (), "target": 3}}
        return PointResult((), (), (), "digest")

    engine._execute_v2_point = execute
    await engine._run_assist_steps()
    assert executed == [SchedulePoint.NIGHT_WITCH_ASSIST_PICK]
    assert labels == ["night_assist:night_witch_assist_pick"]


@pytest.mark.asyncio
async def test_engine_assist_steps_lone_assistant_votes_without_chat(monkeypatch) -> None:
    private = {1: {"brew_plan": {"round": 1, "assist_seats": (1,), "target": 3}}}
    engine, labels, executed, _private, director = _engine(monkeypatch, private)
    await engine._run_assist_steps()
    assert executed == [
        SchedulePoint.NIGHT_WITCH_ASSIST_VOTE,
        SchedulePoint.NIGHT_WITCH_ASSIST_TALLY,
    ]
    assert labels == [
        "night_assist:night_witch_assist_vote",
        "night_assist:night_witch_assist_tally",
    ]
    director.assist_discussion_turn.assert_not_called()


@pytest.mark.asyncio
async def test_engine_assistant_chat_writes_private_records_and_events(monkeypatch) -> None:
    private = {1: {"brew_plan": {"round": 1, "assist_seats": (2, 3), "target": 4}}}
    engine, labels, _executed, _private, director = _engine(monkeypatch, private)
    director.assist_discussion_turn.side_effect = ["2号的发言", "3号的发言"]
    await engine._run_assist_steps()
    records = [
        record for record in engine.conversation_log.records
        if record.scope is ConversationScope.NIGHT_INTEL
    ]
    assert [record.speaker_seat for record in records] == [2, 3]
    assert [record.content for record in records] == ["2号的发言", "3号的发言"]
    assert all(record.visible_to == [2, 3] for record in records)
    assert labels == [
        "night_assist_chat:1:2:0",
        "night_assist_chat:1:3:1",
        "night_assist:night_witch_assist_vote",
        "night_assist:night_witch_assist_tally",
    ]
    chat_calls = [
        call for call in engine._log_audience_pairs.call_args_list
        if call.args[0] and call.args[0][0][0] == "AWAKENED_WITCH_ASSIST_CHAT"
    ]
    assert len(chat_calls) == 2


@pytest.mark.asyncio
async def test_engine_assistant_chat_skips_already_recorded_speaker(monkeypatch) -> None:
    private = {1: {"brew_plan": {"round": 1, "assist_seats": (2, 3), "target": 4}}}
    engine, labels, _executed, _private, director = _engine(monkeypatch, private)
    engine.conversation_log.add_night_intel(
        "2号已经说过", 1, [2, 3], speaker_seat=2,
    )
    director.assist_discussion_turn.return_value = ""
    await engine._run_assist_steps()
    assert labels == [
        "night_assist:night_witch_assist_vote",
        "night_assist:night_witch_assist_tally",
    ]
    assert director.assist_discussion_turn.call_count == 1
    assert director.assist_discussion_turn.call_args.args[1] == 3


@pytest.mark.asyncio
async def test_engine_assistant_chat_skips_empty_speech(monkeypatch) -> None:
    private = {1: {"brew_plan": {"round": 1, "assist_seats": (2, 3), "target": 4}}}
    engine, labels, _executed, _private, director = _engine(monkeypatch, private)
    director.assist_discussion_turn.return_value = ""
    await engine._run_assist_steps()
    assert labels == [
        "night_assist:night_witch_assist_vote",
        "night_assist:night_witch_assist_tally",
    ]
    assert not engine.conversation_log.records


@pytest.mark.asyncio
async def test_engine_assist_point_skips_a_completed_point(monkeypatch) -> None:
    engine, labels, _executed, _private, _director = _engine(monkeypatch, {})
    engine._pipeline_scheduler.point_completed.return_value = True
    await engine._run_assist_point(SchedulePoint.NIGHT_WITCH_ASSIST_TALLY)
    assert labels == []


@pytest.mark.asyncio
async def test_engine_assistant_chat_rejects_an_invalid_target(monkeypatch) -> None:
    from app.core.scheduler import PipelinePaused

    private = {1: {"brew_plan": {"round": 1, "assist_seats": (2, 3), "target": 0}}}
    engine, _labels, _executed, _private, _director = _engine(monkeypatch, private)
    with pytest.raises(PipelinePaused):
        await engine._run_assist_chat(private[1]["brew_plan"])


@pytest.mark.asyncio
async def test_engine_brew_plan_ignores_another_round(monkeypatch) -> None:
    private = {1: {"brew_plan": {"round": 2, "assist_seats": (), "target": 3}}}
    engine, labels, executed, _private, _director = _engine(monkeypatch, private)
    await engine._run_assist_steps()
    assert labels == [] and executed == []


@pytest.mark.asyncio
async def test_engine_assist_point_without_a_scheduler_still_runs(monkeypatch) -> None:
    engine, labels, _executed, _private, _director = _engine(monkeypatch, {})
    engine._pipeline_scheduler = None
    await engine._run_assist_point(SchedulePoint.NIGHT_WITCH_ASSIST_TALLY)
    assert labels == ["night_assist:night_witch_assist_tally"]


def test_engine_assist_seats_rejects_malformed_plans() -> None:
    from app.core.scheduler import PipelinePaused

    engine = GameEngine("assist-engine")
    with pytest.raises(PipelinePaused):
        engine._assist_seats({"assist_seats": "bad"})
    with pytest.raises(PipelinePaused):
        engine._assist_seats({"assist_seats": (1, "x")})


# ── GameService checkpoint projection and the synthetic tally ─────────────


def test_checkpoint_domain_events_projects_night_assist_rows() -> None:
    engine = SimpleNamespace(
        game_id="g",
        state=SimpleNamespace(round_number=3),
        _pending_point_events=(
            ("AWAKENED_WITCH_ASSIST_PICK", {
                "round_number": 3, "target_seat": 4, "assistant_seats": [2, 3],
            }),
        ),
    )
    rows = GameService._checkpoint_domain_events(
        engine, "00000007:night_assist:night_witch_assist_pick",
    )
    assert rows == [{
        "event_id": rows[0]["event_id"],
        "event_type": "AWAKENED_WITCH_ASSIST_PICK",
        "payload": {
            "round_number": 3, "target_seat": 4, "assistant_seats": [2, 3],
        },
        "visibility": ["PUBLIC"],
        "schema_version": 1,
    }]
    assert rows[0]["event_id"].startswith("domain:")


def test_checkpoint_domain_events_night_assist_without_events_falls_back() -> None:
    engine = SimpleNamespace(
        game_id="g", state=SimpleNamespace(round_number=1), _pending_point_events=(),
    )
    rows = GameService._checkpoint_domain_events(
        engine, "00000008:night_assist:night_witch_assist_tally",
    )
    # The label branch is exercised but has nothing to publish; the generic
    # STEP_COMMITTED fallback still marks the step.
    assert [row["event_type"] for row in rows] == ["STEP_COMMITTED"]


def test_provider_short_circuits_the_assist_tally() -> None:
    service = GameService(WSManager(), EventBus())
    snapshot = builtin_registry.freeze()
    director = NightDirector(snapshot, lambda messages: None)
    provider = service._command_provider(
        snapshot, PromptRenderer(), lambda seat: MagicMock(), director,
    )
    contract = next(
        contract for contract in snapshot.require(AWAKENED_WITCH_SPEC.role_id).contracts
        if contract.contract_id == "awakened_witch_assist_tally"
    )
    request = IssuedActionRequest(
        1, AWAKENED_WITCH_SPEC.role_id, contract, 0, 1, "night", "w", "k",
    )
    command = provider(request, MagicMock(game_id="g"), 0)
    assert command == ActionCommand(
        action_type="resolve", target_seat=None, reasoning="",
    )


def test_durable_provider_never_writes_a_request_for_the_assist_tally() -> None:
    """The deterministic tally must bypass the durable ledger too.

    The non-durable provider already synthesised the command, but with a
    ``ModelInvocationService`` attached the tally still created a 0ms request
    and attempt with unknown usage. ``invoke_sync`` must not be reached.
    """
    service = GameService(WSManager(), EventBus())
    service._model_invocations = MagicMock()
    snapshot = builtin_registry.freeze()
    director = NightDirector(snapshot, lambda messages: None)
    provider = service._command_provider(
        snapshot, PromptRenderer(), lambda seat: MagicMock(), director,
    )
    contract = next(
        contract for contract in snapshot.require(AWAKENED_WITCH_SPEC.role_id).contracts
        if contract.contract_id == "awakened_witch_assist_tally"
    )
    request = IssuedActionRequest(
        1, AWAKENED_WITCH_SPEC.role_id, contract, 0, 1, "night", "w", "k",
    )

    command = provider(request, SimpleNamespace(game_id="g"), 0)

    assert command == ActionCommand(
        action_type="resolve", target_seat=None, reasoning="",
    )
    service._model_invocations.invoke_sync.assert_not_called()


def test_assistant_prompt_uses_the_registered_display_name() -> None:
    state = _game()
    snapshot = builtin_registry.freeze()
    director = NightDirector(snapshot, lambda messages: "{}")
    messages = director.assist_discussion_prompt(
        state, 2, 3, (1, 2), (),
    )
    assert "你自己的身份：Villager" in messages[1]["content"]


@pytest.mark.asyncio
async def test_engine_brew_plan_ignores_a_malformed_slot(monkeypatch) -> None:
    private = {1: {"brew_plan": "not-a-plan"}}
    engine, labels, executed, _private, _director = _engine(monkeypatch, private)
    await engine._run_assist_steps()
    assert labels == [] and executed == []


def test_scheduler_point_completed_reads_a_completed_journal() -> None:
    from app.core.action_resolver import ActionResolver
    from app.core.action_validator import ActionValidator
    from app.core.context_projector import ContextProjector
    from app.core.effect_applier import EffectApplier
    from app.core.scheduler import Scheduler
    from app.models.game import GameState, PlayerState

    snapshot = builtin_registry.freeze()
    game = GameState(
        "completed-point", phase=GamePhase.NIGHT, round_number=1,
        players={1: PlayerState(1, "wolf-killer-villager", "good")},
    )
    scheduler = Scheduler(
        snapshot, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), lambda *args: ActionCommand(
            action_type="pass", target_seat=None, reasoning="",
        ),
    )
    scheduler.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert scheduler.point_completed(game, SchedulePoint.NIGHT_ACTION) is True
