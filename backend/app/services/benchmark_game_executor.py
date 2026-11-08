"""Bridge one durable benchmark item to the public game lifecycle service."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping

from app.core.model_health_guard import ProviderOutageFailed
from app.core.scheduler import DecisionPaused
from app.models.game import (
    DEFAULT_VICTORY, DEFAULT_WITCH_SELF_SAVE, VICTORY_POLICIES, WITCH_SELF_SAVE_POLICIES,
)
from app.persistence.repository import GameRepository
from app.services.game_service import GameService


def _seat_model(model: object) -> dict[str, object]:
    """Return one frozen seat entry for ``create_game``.

    The whole dict — not just the config id — crosses the boundary so
    per-selection ``model_id`` / ``reasoning_effort`` / ``temperature``
    overrides survive into the game; ``create_game`` validates the fields.
    """
    if not isinstance(model, Mapping):
        raise ValueError("benchmark model assignment must be an object")
    value = model.get("config_id", model.get("model_config_id"))
    if value is not None and not isinstance(value, str):
        raise ValueError("benchmark model config_id must be a string or null")
    return dict(model)


def _seat_assignments(
    assignment: Mapping[str, object], player_count: int,
) -> dict[int, dict[str, object]]:
    seat_models = assignment.get("seat_models")
    if isinstance(seat_models, Mapping):
        result: dict[int, dict[str, object]] = {}
        for raw_seat, model in seat_models.items():
            try:
                seat = int(raw_seat)
            except (TypeError, ValueError):
                raise ValueError("benchmark seat must be an integer") from None
            result[seat] = _seat_model(model)
        if set(result) != set(range(1, player_count + 1)):
            raise ValueError("benchmark assignment must cover every seat")
        return result

    model = assignment.get("model")
    seats = assignment.get("seats")
    if not isinstance(seats, list) or set(seats) != set(range(1, player_count + 1)):
        raise ValueError("paired benchmark assignment must cover every seat")
    seat_model = _seat_model(model)
    return {seat: dict(seat_model) for seat in range(1, player_count + 1)}


def _optional_text(config: Mapping[str, object], key: str) -> str | None:
    value = config.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"benchmark {key} must be a string or null")
    return value


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


_SYSTEMIC_PAUSE_CODES = frozenset({
    "provider_circuit_open", "no_progress",
})


def _is_systemic_pause(code: object) -> bool:
    """A paused benchmark game that must not be auto-resumed."""
    if not isinstance(code, str) or not code:
        return False
    if code in _SYSTEMIC_PAUSE_CODES:
        return True
    return code.startswith(("provider_", "quality_"))


class BenchmarkGameExecutor:
    """Create or continue the game already owned by one schedule item."""

    def __init__(
        self, repository: GameRepository, game_service: GameService, *,
        timeout_poll_seconds: float = 1.0,
        pause_settle_polls: int = 40,
        pause_settle_seconds: float = 0.05,
    ) -> None:
        self._repository = repository
        self._game_service = game_service
        self._timeout_poll_seconds = timeout_poll_seconds
        self._pause_settle_polls = pause_settle_polls
        self._pause_settle_seconds = pause_settle_seconds

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
            else 7200
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
                # A provider/quality pause is not a human break: resuming it
                # would immediately re-trip and burn the game timeout. Benchmark
                # games normally fail instead of pausing, so this is the safety
                # net for a pause written by an older row or a manual pause path.
                if _is_systemic_pause(record.get("pause_code")):
                    return game_id, "failed", str(record.get("pause_code"))
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
            enable_sheriff_raw = scenario.get("enable_sheriff")
            enable_sheriff = enable_sheriff_raw if isinstance(enable_sheriff_raw, bool) else False
            # Read from the frozen scenario, never from the live catalog: a run
            # must keep replaying the rule set it was created under.
            self_save_raw = scenario.get("witch_self_save")
            witch_self_save = (
                self_save_raw if self_save_raw in WITCH_SELF_SAVE_POLICIES
                else DEFAULT_WITCH_SELF_SAVE
            )
            victory_raw = scenario.get("victory")
            victory = victory_raw if victory_raw in VICTORY_POLICIES else DEFAULT_VICTORY
            game_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"wolfkiller:benchmark:{run_id}:{item_index}",
            ))
            commentary_enabled_raw = config.get("commentary_enabled")
            commentary_enabled = (
                commentary_enabled_raw
                if isinstance(commentary_enabled_raw, bool)
                else False
            )
            game_id = await self._game_service.create_game(
                role_counts=normalized_counts,
                model_seat_assignments=seats,
                role_by_seat=roles,
                runtime_seed=runtime_seed,
                game_id_override=game_id,
                source="benchmark",
                benchmark_run_id=run_id,
                benchmark_item_index=item_index,
                enable_sheriff=enable_sheriff,
                witch_self_save=witch_self_save,
                victory=victory,
                commentary_enabled=commentary_enabled,
                commentary_config_id=_optional_text(
                    config, "commentary_config_id",
                ),
                commentary_model_id=_optional_text(
                    config, "commentary_model_id",
                ),
                commentary_reasoning_effort=_optional_text(
                    config, "commentary_reasoning_effort",
                ),
                vote_model_config_id=_optional_text(
                    config, "vote_model_config_id",
                ),
                vote_model_id=_optional_text(config, "vote_model_id"),
                vote_reasoning_effort=_optional_text(
                    config, "vote_reasoning_effort",
                ),
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
                    try:
                        return await wait_task
                    except DecisionPaused as error:
                        # ``wait_game`` re-raises the engine's terminal
                        # exception, so a recoverable pause arrives here
                        # mid-wait. Resuming it is the whole point of the
                        # pause: the point re-runs and only degrades if it
                        # exhausts a second time, so the card survives one
                        # bad model streak instead of costing the game.
                        resumed = await self._resume_decision_pause(
                            game_id, error,
                        )
                        if resumed:
                            wait_task = asyncio.create_task(
                                self._game_service.wait_game(game_id),
                            )
                            continue
                        return self._terminal_pause_info(game_id, error)
                    except asyncio.CancelledError:
                        return self._runner_cancelled_info(game_id)
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

    async def _resume_decision_pause(
        self, game_id: str, error: DecisionPaused,
    ) -> bool:
        """Resume a recoverable in-flight pause; False when it is terminal."""
        if type(error) is ProviderOutageFailed or _is_systemic_pause(
            getattr(error, "code", None),
        ):
            return False
        # The watcher flips the row to ``paused`` inside the engine task's
        # done callback, which is already scheduled on this loop but is not
        # guaranteed to have run yet — settle briefly instead of racing it.
        # ``resume`` on a still-``running`` row is a no-op that would leave
        # the wait loop parked on a dead engine, so only resume once the
        # pause is durably visible.
        record = None
        for _ in range(self._pause_settle_polls):
            record = self._repository.get_game(game_id)
            if record is None or str(
                record.get("execution_status"),
            ) != "running":
                break
            await asyncio.sleep(self._pause_settle_seconds)
        if record is None or str(record.get("execution_status")) != "paused":
            return False
        try:
            await self._game_service.resume_benchmark_game(game_id)
        except Exception:
            return False
        return True

    def _runner_cancelled_info(
        self, game_id: str,
    ) -> dict[str, object]:
        """Shape the outcome when the runner was cancelled underneath the wait.

        ``cancel_benchmark_game`` commits the durable ``cancelled`` row before
        the task dies — e.g. the runtime-clock heartbeat enforcing the armed
        timeout — so the row, not the runner's ``CancelledError``, is the
        authoritative outcome. Re-raising here would kill the worker and leave
        the item ``running``, stalling the whole run.
        """
        record = self._repository.get_game(game_id) or {}
        status = str(record.get("execution_status") or "")
        if status in {"", "running", "paused"}:
            status = "cancelled"
        return {
            "execution_status": status,
            "recovery_block_code": (
                record.get("recovery_block_code")
                or "benchmark_runner_cancelled"
            ),
        }

    def _terminal_pause_info(
        self, game_id: str, error: DecisionPaused,
    ) -> dict[str, object]:
        """Shape the failure info for a pause that must not be resumed.

        The item outcome is terminal regardless of the row's own status: a
        systemic pause refuses to resume like the dispatch branch does, and a
        one-shot pause whose resume already failed has no next step either.
        """
        record = self._repository.get_game(game_id) or {}
        status = str(record.get("execution_status") or "failed")
        if status in ("running", "paused"):
            status = "failed"
        return {
            "execution_status": status,
            "recovery_block_code": (
                record.get("recovery_block_code")
                or record.get("pause_code")
                or getattr(error, "code", None)
                or type(error).__name__
            ),
        }

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
