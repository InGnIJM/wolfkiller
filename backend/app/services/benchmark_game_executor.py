"""Bridge one durable benchmark item to the public game lifecycle service."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping

from app.persistence.repository import GameRepository
from app.services.game_service import GameService


def _config_id(model: object) -> str | None:
    if not isinstance(model, Mapping):
        raise ValueError("benchmark model assignment must be an object")
    value = model.get("config_id", model.get("model_config_id"))
    if value is not None and not isinstance(value, str):
        raise ValueError("benchmark model config_id must be a string or null")
    return value


def _seat_assignments(
    assignment: Mapping[str, object], player_count: int,
) -> dict[int, str | None]:
    seat_models = assignment.get("seat_models")
    if isinstance(seat_models, Mapping):
        result: dict[int, str | None] = {}
        for raw_seat, model in seat_models.items():
            try:
                seat = int(raw_seat)
            except (TypeError, ValueError):
                raise ValueError("benchmark seat must be an integer") from None
            result[seat] = _config_id(model)
        if set(result) != set(range(1, player_count + 1)):
            raise ValueError("benchmark assignment must cover every seat")
        return result

    model = assignment.get("model")
    seats = assignment.get("seats")
    if not isinstance(seats, list) or set(seats) != set(range(1, player_count + 1)):
        raise ValueError("paired benchmark assignment must cover every seat")
    config_id = _config_id(model)
    return {seat: config_id for seat in range(1, player_count + 1)}


def _role_assignments(
    assignment: Mapping[str, object], player_count: int,
) -> dict[int, str]:
    role_by_seat = assignment.get("role_by_seat")
    if not isinstance(role_by_seat, Mapping):
        raise ValueError("benchmark role_by_seat must be an object")
    result: dict[int, str] = {}
    for raw_seat, role_id in role_by_seat.items():
        try:
            seat = int(raw_seat)
        except (TypeError, ValueError):
            raise ValueError("benchmark role seat must be an integer") from None
        if type(role_id) is not str or not role_id:
            raise ValueError("benchmark role id must be a non-empty string")
        result[seat] = role_id
    if set(result) != set(range(1, player_count + 1)):
        raise ValueError("benchmark role assignment must cover every seat")
    return result


def _runtime_seed(assignment: Mapping[str, object]) -> int:
    value = assignment.get("runtime_seed")
    if type(value) is not int or value < 0:
        raise ValueError("benchmark runtime seed must be a non-negative integer")
    return value


class BenchmarkGameExecutor:
    """Create or continue the game already owned by one schedule item."""

    def __init__(
        self, repository: GameRepository, game_service: GameService, *,
        timeout_poll_seconds: float = 1.0,
    ) -> None:
        self._repository = repository
        self._game_service = game_service
        self._timeout_poll_seconds = timeout_poll_seconds

    async def __call__(
        self, item: Mapping[str, object],
    ) -> tuple[str, str, str | None]:
        run_id = item.get("run_id")
        item_index = item.get("item_index")
        if not isinstance(run_id, str) or type(item_index) is not int:
            raise ValueError("invalid benchmark item identity")
        run = self._repository.get_benchmark_run(run_id)
        if run is None:
            raise KeyError(run_id)
        config = run.get("config")
        configured_timeout = (
            config.get("game_timeout_seconds")
            if isinstance(config, Mapping) else None
        )
        timeout_seconds = (
            configured_timeout
            if type(configured_timeout) is int and configured_timeout > 0
            else 3600
        )
        game_id = item.get("game_id")
        if isinstance(game_id, str):
            record = self._repository.get_game(game_id)
            if record is None:
                raise ValueError("benchmark item references a missing game")
            status = str(record["execution_status"])
            if status == "interrupted":
                await self._game_service.recover_benchmark_game(game_id)
            elif status == "paused":
                await self._game_service.resume_benchmark_game(game_id)
            elif status == "completed":
                return game_id, status, None
            elif status != "running":
                return game_id, status, str(record.get("recovery_block_code") or status)
        else:
            scenario = config.get("scenario") if isinstance(config, Mapping) else None
            role_counts = scenario.get("role_counts") if isinstance(scenario, Mapping) else None
            assignment = item.get("assignment")
            if not isinstance(role_counts, Mapping) or not isinstance(assignment, Mapping):
                raise ValueError("benchmark scenario is incomplete")
            normalized_counts = {str(key): int(value) for key, value in role_counts.items()}
            player_count = sum(normalized_counts.values())
            seats = _seat_assignments(assignment, player_count)
            roles = _role_assignments(assignment, player_count)
            runtime_seed = _runtime_seed(assignment)
            game_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"wolfkiller:benchmark:{run_id}:{item_index}",
            ))
            game_id = await self._game_service.create_game(
                role_counts=normalized_counts,
                model_seat_assignments=seats,
                role_by_seat=roles,
                runtime_seed=runtime_seed,
                game_id_override=game_id,
                source="benchmark",
                benchmark_run_id=run_id,
                benchmark_item_index=item_index,
            )

        arm = getattr(self._game_service, "arm_benchmark_timeout", None)
        if callable(arm):
            arm(game_id, timeout_seconds)
        info = await self._wait_with_active_timeout(game_id, timeout_seconds)
        status = str(info.get("execution_status", "failed"))
        reason = info.get("recovery_block_code")
        return game_id, status, str(reason) if reason is not None else None

    async def _wait_with_active_timeout(
        self, game_id: str, timeout_seconds: int,
    ) -> Mapping[str, object]:
        wait_task = asyncio.create_task(self._game_service.wait_game(game_id))
        try:
            while True:
                done, _ = await asyncio.wait(
                    (wait_task,), timeout=self._timeout_poll_seconds,
                )
                if done:
                    return await wait_task
                active_elapsed_ms = self._active_elapsed_ms(game_id)
                if (
                    type(active_elapsed_ms) is int
                    and active_elapsed_ms >= timeout_seconds * 1000
                ):
                    await self._game_service.cancel_benchmark_game(
                        game_id, recovery_block_code="benchmark_game_timeout",
                    )
                    return {
                        "execution_status": "cancelled",
                        "recovery_block_code": "benchmark_game_timeout",
                    }
        finally:
            if not wait_task.done():
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)

    def _active_elapsed_ms(self, game_id: str) -> int | None:
        live = getattr(self._game_service, "live_active_elapsed_ms", None)
        if callable(live):
            value = live(game_id)
            if type(value) is int:
                return value
        clock = self._repository.get_runtime_clock(game_id) or {}
        elapsed = clock.get("active_elapsed_ms")
        return elapsed if type(elapsed) is int else None

    async def pause(self, game_id: str) -> None:
        await self._game_service.pause_benchmark_game(game_id)

    async def cancel(self, game_id: str) -> None:
        await self._game_service.cancel_benchmark_game(game_id)
