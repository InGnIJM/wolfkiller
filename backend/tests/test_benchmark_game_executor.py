from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.benchmark_game_executor import (
    BenchmarkGameExecutor,
    _config_id,
    _seat_assignments,
)


def test_seat_assignments_preserve_frozen_schedule() -> None:
    assert _seat_assignments({
        "seat_models": {
            "1": {"model_config_id": "a"},
            "2": {"config_id": "b"},
        },
    }, 2) == {1: "a", 2: "b"}
    with pytest.raises(ValueError, match="every seat"):
        _seat_assignments({"seat_models": {"1": {"config_id": "a"}}}, 2)


def test_seat_assignments_reject_malformed_models_seats_and_paired_plans() -> None:
    assert _config_id({"config_id": None}) is None
    with pytest.raises(ValueError, match="must be an object"):
        _config_id("model")
    with pytest.raises(ValueError, match="string or null"):
        _config_id({"config_id": 7})
    with pytest.raises(ValueError, match="seat must be an integer"):
        _seat_assignments({"seat_models": {"first": {"config_id": "a"}}}, 1)
    with pytest.raises(ValueError, match="paired benchmark"):
        _seat_assignments({"model": {"config_id": "a"}, "seats": "1"}, 1)


@pytest.mark.asyncio
async def test_executor_creates_and_waits_through_formal_game_service_api() -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {
        "config": {"scenario": {"role_counts": {"villager": 2}}},
    }
    game_service = MagicMock()
    game_service.create_game = AsyncMock(return_value="created")
    game_service.wait_game = AsyncMock(return_value={"execution_status": "completed"})
    executor = BenchmarkGameExecutor(repository, game_service)

    result = await executor({
        "run_id": "run", "item_index": 3, "game_id": None,
        "assignment": {
            "model": {"model_config_id": "model"},
            "seats": [1, 2],
            "role_by_seat": {"1": "werewolf", "2": "villager"},
            "runtime_seed": 987654321,
        },
    })

    assert result == ("created", "completed", None)
    call = game_service.create_game.await_args.kwargs
    assert call["role_counts"] == {"villager": 2}
    assert call["model_seat_assignments"] == {1: "model", 2: "model"}
    assert call["role_by_seat"] == {1: "werewolf", 2: "villager"}
    assert call["runtime_seed"] == 987654321
    assert call["benchmark_run_id"] == "run"
    assert call["benchmark_item_index"] == 3
    game_service.wait_game.assert_awaited_once_with("created")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        ({"model": {"config_id": None}, "seats": [1],
          "role_by_seat": {"1": "villager"}, "runtime_seed": -1}, "runtime seed"),
        ({"model": {"config_id": None}, "seats": [1],
          "role_by_seat": {"2": "villager"}, "runtime_seed": 1}, "every seat"),
        ({"model": {"config_id": None}, "seats": [1],
          "role_by_seat": {"first": "villager"}, "runtime_seed": 1}, "role seat"),
        ({"model": {"config_id": None}, "seats": [1],
          "role_by_seat": {"1": 7}, "runtime_seed": 1}, "role id"),
    ],
)
async def test_executor_rejects_malformed_frozen_random_inputs(
    assignment, message,
) -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {
        "config": {"scenario": {"role_counts": {"villager": 1}}},
    }

    with pytest.raises(ValueError, match=message):
        await BenchmarkGameExecutor(repository, MagicMock())({
            "run_id": "run", "item_index": 0,
            "assignment": assignment,
        })


@pytest.mark.asyncio
async def test_executor_recovers_existing_interrupted_game_without_creating() -> None:
    repository = MagicMock()
    repository.get_game.return_value = {
        "execution_status": "interrupted", "recovery_block_code": None,
    }
    game_service = MagicMock()
    game_service.recover_benchmark_game = AsyncMock()
    game_service.wait_game = AsyncMock(return_value={"execution_status": "completed"})
    executor = BenchmarkGameExecutor(repository, game_service)

    assert await executor({
        "run_id": "run", "item_index": 0, "game_id": "existing",
    }) == ("existing", "completed", None)
    game_service.recover_benchmark_game.assert_awaited_once_with("existing")
    game_service.create_game.assert_not_called()


@pytest.mark.asyncio
async def test_executor_cancels_game_at_persisted_active_time_limit() -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {
        "config": {"game_timeout_seconds": 1},
    }
    repository.get_game.return_value = {
        "execution_status": "running", "recovery_block_code": None,
    }
    repository.get_runtime_clock.side_effect = [
        {"active_elapsed_ms": 999},
        {"active_elapsed_ms": 1000},
    ]
    waiting = asyncio.Event()

    async def wait_forever(_game_id: str) -> dict[str, object]:
        await waiting.wait()
        return {"execution_status": "completed"}

    class ClocklessService:
        def __init__(self) -> None:
            self.wait_game = AsyncMock(side_effect=wait_forever)
            self.cancel_benchmark_game = AsyncMock()

    game_service = ClocklessService()
    executor = BenchmarkGameExecutor(
        repository, game_service, timeout_poll_seconds=0,
    )

    assert await executor({
        "run_id": "run", "item_index": 0, "game_id": "existing",
    }) == ("existing", "cancelled", "benchmark_game_timeout")
    assert repository.get_runtime_clock.call_count == 2
    game_service.cancel_benchmark_game.assert_awaited_once_with(
        "existing", recovery_block_code="benchmark_game_timeout",
    )


@pytest.mark.asyncio
async def test_executor_times_out_from_live_clock_when_persisted_clock_is_stale() -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {
        "config": {"game_timeout_seconds": 1},
    }
    repository.get_game.return_value = {
        "execution_status": "running", "recovery_block_code": None,
    }
    repository.get_runtime_clock.return_value = {"active_elapsed_ms": 0}
    waiting = asyncio.Event()
    game_service = MagicMock()

    async def wait_forever(_game_id: str) -> dict[str, object]:
        await waiting.wait()
        return {"execution_status": "completed"}

    game_service.wait_game = AsyncMock(side_effect=wait_forever)
    game_service.cancel_benchmark_game = AsyncMock()
    game_service.live_active_elapsed_ms = MagicMock(return_value=1500)
    executor = BenchmarkGameExecutor(
        repository, game_service, timeout_poll_seconds=0,
    )

    assert await asyncio.wait_for(executor({
        "run_id": "run", "item_index": 0, "game_id": "existing",
    }), timeout=1.0) == ("existing", "cancelled", "benchmark_game_timeout")
    game_service.arm_benchmark_timeout.assert_called_once_with("existing", 1)
    game_service.cancel_benchmark_game.assert_awaited_once_with(
        "existing", recovery_block_code="benchmark_game_timeout",
    )
    repository.get_runtime_clock.assert_not_called()


@pytest.mark.asyncio
async def test_executor_returns_existing_completed_game_without_waiting() -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {
        "config": {"game_timeout_seconds": 1},
    }
    repository.get_game.return_value = {
        "execution_status": "completed", "recovery_block_code": None,
    }
    game_service = MagicMock()
    game_service.wait_game = AsyncMock()
    executor = BenchmarkGameExecutor(repository, game_service)

    assert await executor({
        "run_id": "run", "item_index": 0, "game_id": "existing",
    }) == ("existing", "completed", None)
    game_service.wait_game.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item", "run", "game", "message"),
    [
        ({"run_id": None, "item_index": 0}, {}, None, "identity"),
        ({"run_id": "run", "item_index": "0"}, {}, None, "identity"),
        ({"run_id": "run", "item_index": 0}, None, None, "run"),
        ({"run_id": "run", "item_index": 0, "game_id": "missing"}, {}, None,
         "missing game"),
    ],
)
async def test_executor_rejects_invalid_durable_item_references(
    item, run, game, message,
) -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = run
    repository.get_game.return_value = game
    executor = BenchmarkGameExecutor(repository, MagicMock())

    with pytest.raises((ValueError, KeyError), match=message):
        await executor(item)


@pytest.mark.asyncio
async def test_executor_resumes_paused_and_surfaces_terminal_failure_reason() -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {
        "config": {"game_timeout_seconds": 0},
    }
    repository.get_game.side_effect = [
        {"execution_status": "paused", "recovery_block_code": None},
        {"execution_status": "recovery_blocked", "recovery_block_code": "registry_mismatch"},
    ]
    game_service = MagicMock()
    game_service.resume_benchmark_game = AsyncMock()
    game_service.wait_game = AsyncMock(return_value={})
    executor = BenchmarkGameExecutor(repository, game_service)

    resumed = await executor({
        "run_id": "run", "item_index": 0, "game_id": "paused",
    })
    blocked = await executor({
        "run_id": "run", "item_index": 1, "game_id": "blocked",
    })

    assert resumed == ("paused", "failed", None)
    assert blocked == ("blocked", "recovery_blocked", "registry_mismatch")
    game_service.resume_benchmark_game.assert_awaited_once_with("paused")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        {"run_id": "run", "item_index": 0, "assignment": {}},
        {"run_id": "run", "item_index": 0, "assignment": "invalid"},
    ],
)
async def test_executor_rejects_incomplete_creation_scenario(item) -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {
        "config": {"scenario": {"role_counts": None}},
    }
    with pytest.raises(ValueError, match="scenario is incomplete"):
        await BenchmarkGameExecutor(repository, MagicMock())(item)


@pytest.mark.asyncio
async def test_executor_clock_ignores_unknown_samples_and_control_delegates() -> None:
    repository = MagicMock()
    repository.get_runtime_clock.side_effect = [
        {"active_elapsed_ms": "unknown"},
        {"active_elapsed_ms": 1000},
    ]
    waiting = asyncio.Event()
    game_service = MagicMock()

    async def wait_forever(_game_id: str) -> dict[str, object]:
        await waiting.wait()
        return {}

    game_service.wait_game = AsyncMock(side_effect=wait_forever)
    game_service.cancel_benchmark_game = AsyncMock()
    game_service.pause_benchmark_game = AsyncMock()
    executor = BenchmarkGameExecutor(
        repository, game_service, timeout_poll_seconds=0,
    )

    result = await executor._wait_with_active_timeout("game", 1)
    await executor.pause("game")
    await executor.cancel("game")

    assert result["recovery_block_code"] == "benchmark_game_timeout"
    game_service.pause_benchmark_game.assert_awaited_once_with("game")
    assert game_service.cancel_benchmark_game.await_count == 2
    timeout_call, manual_call = game_service.cancel_benchmark_game.await_args_list
    assert timeout_call.args == ("game",)
    assert timeout_call.kwargs == {"recovery_block_code": "benchmark_game_timeout"}
    assert manual_call.args == ("game",)
    assert manual_call.kwargs == {}
