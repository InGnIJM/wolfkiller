"""Tests for engine-side stage and hook telemetry persisted to game.log."""

import json
import time

import pytest
from unittest.mock import MagicMock

from app.core.game_engine import GameEngine
from app.core.scheduler import PointResult
from app.models.game import GamePhase
from app.models.pipeline import SchedulePoint


def _read_game_log(tmp_path, game_id):
    path = tmp_path / "games" / game_id / "game.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_v2_point_persists_hook_faults(tmp_path):
    faults = ({
        "code": "slow_rule", "label": "validation",
        "elapsed_ms": 80, "elapsed_bucket": "soft_exceeded",
    },)
    scheduler = MagicMock()
    scheduler.run_point.return_value = PointResult((), (), (), "digest", faults)
    engine = GameEngine("bench", data_dir=str(tmp_path), pipeline_scheduler=scheduler)
    engine.state.round_number = 2
    engine.state.phase = GamePhase.NIGHT

    result = await engine._execute_v2_point(SchedulePoint.NIGHT_ACTION)

    assert result.faults == faults
    hook_records = [
        record for record in _read_game_log(tmp_path, "bench")
        if record["operation"] == "stage_telemetry"
    ]
    assert len(hook_records) == 1
    data = hook_records[0]["data"]
    assert data["scope"] == "hook"
    assert data["stage"] == SchedulePoint.NIGHT_ACTION.value
    assert data["elapsed_ms"] == 80
    assert data["label"] == "validation"
    assert hook_records[0]["round"] == 2
    assert hook_records[0]["phase"] == "night"


def test_hook_fault_persist_failure_is_swallowed(tmp_path, caplog):
    engine = GameEngine("bench-x", data_dir=str(tmp_path), pipeline_scheduler=object())
    engine.game_logger.log_operation = MagicMock(
        side_effect=RuntimeError("disk unavailable"),
    )

    engine._log_hook_faults(SchedulePoint.VOTE_ACTION, ({"code": "slow_rule"},))

    assert any("hook fault" in message for message in caplog.messages)


def test_stage_telemetry_failure_is_swallowed(tmp_path, caplog):
    engine = GameEngine("bench-y", data_dir=str(tmp_path), pipeline_scheduler=object())
    engine.game_logger.log_operation = MagicMock(
        side_effect=RuntimeError("disk unavailable"),
    )

    engine._log_stage_telemetry("phase", "speech", time.monotonic())

    assert any("stage telemetry" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_game_loop_records_phase_stage_telemetry(tmp_path):
    """One full loop pass logs a phase-scope stage_telemetry record."""
    class OneShotStateMachine:
        def __init__(self):
            self.calls = 0

        def get_state(self):
            return GamePhase.DAWN

        def is_terminal(self):
            self.calls += 1
            return self.calls > 1

    async def _completed():
        return None

    engine = GameEngine("bench-loop", data_dir=str(tmp_path), pipeline_scheduler=object())
    engine.sm = OneShotStateMachine()
    engine._running = True
    engine._wait_if_paused = _completed
    engine._execute_dawn = _completed
    engine.state.round_number = 3
    engine.state.phase = GamePhase.DAWN

    await engine._game_loop()

    phase_records = [
        record for record in _read_game_log(tmp_path, "bench-loop")
        if record["operation"] == "stage_telemetry"
        and record["data"]["scope"] == "phase"
    ]
    assert len(phase_records) == 1
    data = phase_records[0]["data"]
    assert data["stage"] == "dawn"
    assert data["elapsed_ms"] >= 0
    assert phase_records[0]["phase"] == "dawn"
