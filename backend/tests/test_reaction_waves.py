"""Reaction-chain deaths must open their own response windows.

A response window settles the damage it deals immediately, so a hunter's shot
can kill the holder of a gifted Awakened Wolf King claw (or a Wolf King). Before
``GameEngine._run_reaction_waves`` those second-order deaths were published and
then forgotten: no ``DAWN_REACTION`` window was ever seeded for them, so the
claw that the rules say fires on ``hunter_shot`` could never fire.

These tests pin the wave helper and the exile site that calls it. The template
is ``tests/test_game_engine.py::test_exile_reaction_settles_reaction_damage_immediately``
(real Scheduler + fake command provider + a hand-built ``_Runtime``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.core.game_engine as game_engine_module
from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import CommitResult, EffectApplier, _Runtime
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.core.point_journal import (
    PointCheckpoint,
    PointKey,
    WorkCursor,
    point_journal,
)
from app.core.scheduler import Scheduler
from app.models.actions import DeathReport
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand as PipelineActionCommand, SchedulePoint
from app.roles.registry import builtin_registry


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    """Keep the default GameLogger out of the live ``data/games`` directory."""
    original = game_engine_module.GameLogger

    def isolated(data_dir="data"):
        return original(data_dir=data_dir if data_dir != "data" else str(tmp_path))

    monkeypatch.setattr(game_engine_module, "GameLogger", isolated)


def _scheduler(provider):
    registry = builtin_registry.freeze()
    return Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )


def _engine(game_id, provider, *, players, round_number=2):
    engine = GameEngine(
        game_id, event_bus=EventBus(),
        pipeline_scheduler=_scheduler(provider),
    )
    engine.state = GameState(
        game_id, phase=GamePhase.VOTE_RESOLUTION, round_number=round_number,
        players=players,
    )
    return engine


def _shoot_provider(calls, *, hunter_target=None, claw_target=None):
    def provider(request, context, attempt):
        contract_id = request.contract.contract_id
        calls.append(contract_id)
        if contract_id == "hunter_shoot":
            return PipelineActionCommand(
                action_type="shoot", target_seat=hunter_target, reasoning="test",
            )
        if contract_id == "awakened_wolf_claw_gift":
            return PipelineActionCommand(
                action_type="shoot", target_seat=claw_target, reasoning="test",
            )
        raise AssertionError(f"unexpected contract {contract_id}")

    return provider


@pytest.mark.asyncio
async def test_a_death_caused_by_a_reaction_window_gets_its_own_window():
    """Hunter (1, exiled) shoots the gifted claw holder (2), who then shoots 3.

    Without the wave the gift contract is never requested and the second death
    is published with no answer; the third death never exists at all.
    """
    calls = []
    engine = _engine(
        "reaction-wave",
        _shoot_provider(calls, hunter_target=2, claw_target=3),
        players={
            1: PlayerState(1, "wolf-killer-hunter", "good", is_alive=False),
            2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
            3: PlayerState(3, "wolf-killer-villager", "good"),
            4: PlayerState(4, "wolf-killer-werewolf", "werewolf"),
        },
    )
    engine.state._pipeline_runtime = _Runtime(
        role_resources={1: {"gun": 1}},
        private_data={2: {"awakened_claw_gift": "note"}},
    )
    engine.state.death_history.append(DeathReport(1, "exile", 2))

    settled = await engine._run_exile_reaction(1)

    assert calls == ["hunter_shoot", "awakened_wolf_claw_gift"]
    assert [(d.player_seat, d.cause) for d in settled] == [
        (2, "hunter_shot"), (3, "wolf_king_shot"),
    ]
    assert engine.state._pipeline_runtime.pending_damage == ()
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [
        (1, "exile"), (2, "hunter_shot"), (3, "wolf_king_shot"),
    ]
    phases = {key.phase for key, _ in point_journal(engine.state).entries()}
    assert "vote_resolution#exile_react-2" in phases
    assert "vote_resolution#exile_react-3" in phases

    # A second call must not re-ask anything: every current-round death is now
    # seeded by a finished window (the durable shape a resume sees).
    assert await engine._run_reaction_waves(slot_prefix="exile_react") == ()
    assert calls == ["hunter_shoot", "awakened_wolf_claw_gift"]


@pytest.mark.asyncio
async def test_a_death_without_a_response_contract_opens_no_request_and_stops():
    """The queue decides applicability; a plain villager death asks nobody."""
    calls = []

    def provider(request, context, attempt):
        calls.append(request.contract.contract_id)
        raise AssertionError("no response contract should be asked")

    engine = _engine(
        "reaction-noop", provider,
        players={1: PlayerState(1, "wolf-killer-villager", "good")},
    )
    engine.state.death_history.append(DeathReport(1, "wolf_kill", 2))

    settled = await engine._run_reaction_waves(slot_prefix="noop")

    assert settled == ()
    assert calls == []
    assert [key.phase for key, _ in point_journal(engine.state).entries()] == [
        "vote_resolution#noop-1",
    ]
    assert engine.state.death_history == [DeathReport(1, "wolf_kill", 2)]


@pytest.mark.asyncio
async def test_the_wave_cap_stops_the_chain_and_records_a_telemetry_fault(
    monkeypatch,
):
    """An endless chain must end in a fault row, not a hang."""
    calls = []

    def provider(request, context, attempt):
        calls.append(request.contract.contract_id)
        raise AssertionError("villagers never respond")

    engine = _engine(
        "reaction-cap", provider,
        players={
            seat: PlayerState(seat, "wolf-killer-villager", "good")
            for seat in range(1, 12)
        },
    )
    engine.state.death_history.append(DeathReport(1, "wolf_kill", 2))
    settled_waves = []

    async def endless_settle():
        # Every wave deals one more death so the loop can never converge.
        seat = len(settled_waves) + 2
        death = DeathReport(seat, "wolf_king_shot", 2)
        engine.state.death_history.append(death)
        settled_waves.append(death)
        return (death,)

    monkeypatch.setattr(engine, "_settle_and_publish", endless_settle)
    engine.game_logger.log_operation = MagicMock()

    settled = await engine._run_reaction_waves(slot_prefix="cap")

    cap = game_engine_module._MAX_REACTION_WAVES
    assert len(settled_waves) == cap
    assert [(d.player_seat, d.cause) for d in settled] == [
        (death.player_seat, death.cause) for death in settled_waves
    ]
    assert calls == []
    assert engine.state.death_history[-1].player_seat == cap + 1
    telemetry = [
        call.kwargs["data"] for call in engine.game_logger.log_operation.call_args_list
        if call.args[1] == "stage_telemetry"
    ]
    assert telemetry == [{
        "scope": "reaction",
        "stage": "dawn_reaction",
        "code": "reaction_wave_overflow",
        "wave_cap": cap,
        "pending_seats": [cap + 1],
    }]


@pytest.mark.asyncio
async def test_a_journaled_window_does_not_hide_an_unseeded_death():
    """Resume shape: the first window is already journaled; its victim's death
    still has to get a wave of its own."""
    calls = []
    engine = _engine(
        "reaction-resume",
        _shoot_provider(calls, claw_target=3),
        players={
            1: PlayerState(1, "wolf-killer-hunter", "good", is_alive=False),
            2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
            3: PlayerState(3, "wolf-killer-villager", "good"),
            4: PlayerState(4, "wolf-killer-werewolf", "werewolf"),
        },
    )
    engine.state._pipeline_runtime = _Runtime(
        private_data={2: {"awakened_claw_gift": "note"}},
    )
    engine.state.death_history.extend([
        DeathReport(1, "exile", 2),
        DeathReport(2, "hunter_shot", 2),
    ])
    seeded_event = {
        "event_type": "PLAYER_DIED",
        "payload": {"target_seat": 1, "cause": "exile", "round_number": 2},
        "visibility": ("PUBLIC",),
    }
    key = PointKey(
        "reaction-resume", 2, "vote_resolution",
        SchedulePoint.DAWN_REACTION, "d" * 64,
    )
    point_journal(engine.state).put(key, PointCheckpoint(
        (), (), (CommitResult("response", (), 1, (), "saved"),),
        (seeded_event,), (), (), WorkCursor("done", 0, 0),
        {"state_digest": "saved"}, work_count=0,
    ))

    settled = await engine._run_reaction_waves(slot_prefix="resume")

    # 1 号已被 journal 认领，不再重开；2 号的爪仍然拿到窗口。
    assert calls == ["awakened_wolf_claw_gift"]
    assert [(d.player_seat, d.cause) for d in settled] == [(3, "wolf_king_shot")]
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [
        (1, "exile"), (2, "hunter_shot"), (3, "wolf_king_shot"),
    ]


def test_the_seeded_scan_ignores_foreign_rounds_points_and_malformed_rows(
    monkeypatch,
):
    """The journal is durable data: the scan must not trust its shape blindly."""
    engine = GameEngine("reaction-scan", pipeline_scheduler=object())
    engine.state.round_number = 2
    entries = (
        (SimpleNamespace(round_number=3, point=SchedulePoint.DAWN_REACTION),
         SimpleNamespace(events=())),
        (SimpleNamespace(round_number=2, point=SchedulePoint.NIGHT_ACTION),
         SimpleNamespace(events=())),
        (SimpleNamespace(round_number=2, point=SchedulePoint.DAWN_REACTION),
         SimpleNamespace(events=(
             "not-a-mapping",
             {"event_type": "ANNOUNCEMENT", "payload": {"target_seat": 4}},
             {"event_type": "PLAYER_DIED", "payload": ("not-a-mapping",)},
             {"event_type": "PLAYER_DIED", "payload": {"target_seat": "five"}},
             {"event_type": "PLAYER_DIED", "payload": {"target_seat": 5}},
         ))),
    )
    monkeypatch.setattr(
        game_engine_module, "point_journal",
        lambda state: SimpleNamespace(entries=lambda: entries),
    )

    assert engine._reaction_seeded_seats() == {5}


@pytest.mark.asyncio
async def test_a_capped_chain_without_leftovers_logs_no_fault(monkeypatch):
    """Exactly ``_MAX_REACTION_WAVES`` waves that converge need no fault row."""
    cap = game_engine_module._MAX_REACTION_WAVES

    def provider(request, context, attempt):
        raise AssertionError("villagers never respond")

    engine = _engine(
        "reaction-bounded", provider,
        players={
            seat: PlayerState(seat, "wolf-killer-villager", "good")
            for seat in range(1, cap + 2)
        },
    )
    engine.state.death_history.append(DeathReport(1, "wolf_kill", 2))
    settle_calls = 0

    async def bounded_settle():
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == cap:
            return ()
        death = DeathReport(settle_calls + 1, "wolf_king_shot", 2)
        engine.state.death_history.append(death)
        return (death,)

    monkeypatch.setattr(engine, "_settle_and_publish", bounded_settle)
    engine.game_logger.log_operation = MagicMock()

    settled = await engine._run_reaction_waves(slot_prefix="bounded")

    assert settle_calls == cap
    assert [(death.player_seat, death.cause) for death in settled] == [
        (seat, "wolf_king_shot") for seat in range(2, cap + 1)
    ]
    engine.game_logger.log_operation.assert_not_called()


def test_overflow_telemetry_failure_is_swallowed(caplog):
    """A broken log sink must never turn the overflow signal into a crash."""
    engine = GameEngine("reaction-telemetry", pipeline_scheduler=object())
    engine.state.round_number = 2
    engine.state.phase = GamePhase.VOTE_RESOLUTION
    engine.game_logger.log_operation = MagicMock(
        side_effect=RuntimeError("disk unavailable"),
    )

    engine._record_reaction_wave_overflow((DeathReport(4, "wolf_kill", 2),))

    assert any(
        "reaction wave overflow" in message for message in caplog.messages
    )
