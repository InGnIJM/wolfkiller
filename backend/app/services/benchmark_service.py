"""Durable benchmark planning, execution control, and report generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.persistence.repository import GameRepository, InvalidExecutionTransition
from app.services.benchmark_metrics import BenchmarkMetrics


ItemExecutor = Callable[[Mapping[str, object]], Awaitable[tuple[str, str, str | None]]]
GameControl = Callable[[str], Awaitable[None]]


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("benchmark configuration must be canonical JSON") from error


def _derived_seed(seed: int, *parts: object) -> int:
    """Derive stable, independent PRNG streams from a benchmark seed."""
    payload = _canonical([seed, *parts]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], "big")


def _expanded_roles(scenario: Mapping[str, object]) -> list[str]:
    counts = scenario["role_counts"]
    assert isinstance(counts, Mapping)
    return [
        str(role_id)
        for role_id, count in sorted(counts.items())
        for _ in range(int(count))
    ]


class BenchmarkService:
    def __init__(
        self, repository: GameRepository,
        item_executor: ItemExecutor | None = None,
        *,
        game_pauser: GameControl | None = None,
        game_canceller: GameControl | None = None,
    ) -> None:
        self._repository = repository
        self._item_executor = item_executor
        self._game_pauser = game_pauser
        self._game_canceller = game_canceller
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._control_locks: dict[str, asyncio.Lock] = {}
        self._report_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._report_failures: dict[str, str] = {}

    def create_run(
        self, specification: Mapping[str, object], *,
        client_request_id: str | None = None,
    ) -> dict[str, object]:
        spec = json.loads(_canonical(dict(specification)))
        name = spec.get("name")
        mode = spec.get("mode")
        seed = spec.get("seed")
        if type(name) is not str or not name.strip():
            raise ValueError("benchmark name is required")
        if mode not in {"mixed_arena", "paired_regression"}:
            raise ValueError("unsupported benchmark mode")
        if type(seed) is not int or seed < 0:
            raise ValueError("benchmark seed must be a non-negative integer")
        schedule = (
            self._paired_schedule(spec)
            if mode == "paired_regression"
            else self._mixed_schedule(spec)
        )
        digest = hashlib.sha256(_canonical(spec).encode("utf-8")).hexdigest()
        return self._repository.create_benchmark_run(
            run_id=str(uuid.uuid4()),
            client_request_id=client_request_id or str(uuid.uuid4()),
            request_digest=digest, name=name.strip(), mode=mode,
            config=spec, schedule=schedule,
        )

    @staticmethod
    def _scenario(spec: Mapping[str, object]) -> tuple[str, dict[str, object], int]:
        scenario = spec.get("scenario")
        if not isinstance(scenario, Mapping):
            raise ValueError("benchmark scenario is required")
        scenario_id = scenario.get("scenario_id")
        counts = scenario.get("role_counts")
        if type(scenario_id) is not str or not scenario_id:
            raise ValueError("scenario_id is required")
        if not isinstance(counts, Mapping) or not counts:
            raise ValueError("scenario role_counts are required")
        if any(type(key) is not str or type(value) is not int or value < 0 for key, value in counts.items()):
            raise ValueError("invalid scenario role_counts")
        total = sum(counts.values())
        if total < 1:
            raise ValueError("scenario must contain players")
        return scenario_id, dict(scenario), total

    @classmethod
    def _paired_schedule(cls, spec: Mapping[str, object]) -> list[dict[str, object]]:
        scenario_id, scenario, total = cls._scenario(spec)
        repetitions = spec.get("repetitions")
        if type(repetitions) is not int or not 1 <= repetitions <= 10_000:
            raise ValueError("repetitions must be between 1 and 10000")
        baseline, candidate = spec.get("baseline"), spec.get("candidate")
        if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
            raise ValueError("paired regression requires baseline and candidate")
        if _canonical(dict(baseline)) == _canonical(dict(candidate)):
            raise ValueError("baseline and candidate configurations must differ")
        seed = int(spec["seed"])
        roles = _expanded_roles(scenario)
        rows: list[dict[str, object]] = []
        for pair_index in range(repetitions):
            role_order = list(roles)
            random.Random(
                _derived_seed(seed, "paired", "roles", pair_index)
            ).shuffle(role_order)
            common = {
                "seats": list(range(1, total + 1)),
                "role_by_seat": {
                    str(seat): role_id
                    for seat, role_id in enumerate(role_order, start=1)
                },
                "runtime_seed": _derived_seed(
                    seed, "paired", "runtime", pair_index,
                ),
            }
            variants = [
                ("baseline", baseline), ("candidate", candidate),
            ]
            random.Random(
                _derived_seed(seed, "paired", "execution_order", pair_index)
            ).shuffle(variants)
            for variant, model in variants:
                rows.append({
                    "scenario_id": scenario_id,
                    "pair_id": f"pair-{pair_index:04d}",
                    "block_index": pair_index,
                    "assignment": {
                        "variant": variant, "model": dict(model), **common,
                    },
                })
        return rows

    @classmethod
    def _mixed_schedule(cls, spec: Mapping[str, object]) -> list[dict[str, object]]:
        scenario_id, scenario, total = cls._scenario(spec)
        games, models = spec.get("games"), spec.get("models")
        if type(games) is not int or not 1 <= games <= 100_000:
            raise ValueError("games must be between 1 and 100000")
        if type(models) is not list or not models or any(not isinstance(item, Mapping) for item in models):
            raise ValueError("mixed arena requires model configurations")
        if len(models) != total:
            raise ValueError("mixed arena model count must equal player count")
        seed = int(spec["seed"])
        roles = _expanded_roles(scenario)
        block_size = total * total
        balanced = games % block_size == 0
        rows: list[dict[str, object]] = []
        block_count = (games + block_size - 1) // block_size
        for block_index in range(block_count):
            role_order = list(roles)
            model_order = [dict(model) for model in models]
            random.Random(
                _derived_seed(seed, "mixed", "roles", block_index)
            ).shuffle(role_order)
            random.Random(
                _derived_seed(seed, "mixed", "models", block_index)
            ).shuffle(model_order)
            block: list[dict[str, object]] = []
            for role_rotation in range(total):
                for model_rotation in range(total):
                    block.append({
                        "scenario_id": scenario_id,
                        "pair_id": None,
                        "block_index": block_index,
                        "assignment": {
                            "variant": "mixed",
                            "seat_models": {
                                str(seat + 1): dict(
                                    model_order[(seat + model_rotation) % total]
                                )
                                for seat in range(total)
                            },
                            "role_by_seat": {
                                str(seat + 1): role_order[
                                    (seat + role_rotation) % total
                                ]
                                for seat in range(total)
                            },
                            "runtime_seed": _derived_seed(
                                seed, "mixed", "runtime", block_index,
                                role_rotation, model_rotation,
                            ),
                            "balanced": balanced,
                        },
                    })
            random.Random(
                _derived_seed(seed, "mixed", "execution_order", block_index)
            ).shuffle(block)
            remaining = games - len(rows)
            rows.extend(block[:remaining])
        return rows

    async def start(self, run_id: str) -> dict[str, object]:
        if self._item_executor is None:
            raise RuntimeError("benchmark item executor is not configured")
        async with self._control_locks.setdefault(run_id, asyncio.Lock()):
            run = self._repository.transition_benchmark(
                run_id, expected=("draft", "interrupted"), target="running",
            )
            self._ensure_task(run_id)
            return run

    async def resume(self, run_id: str) -> dict[str, object]:
        if self._item_executor is None:
            raise RuntimeError("benchmark item executor is not configured")
        async with self._control_locks.setdefault(run_id, asyncio.Lock()):
            run = self._repository.transition_benchmark(
                run_id, expected=("paused",), target="running",
            )
            self._ensure_task(run_id)
            return run

    async def pause(self, run_id: str) -> dict[str, object]:
        async with self._control_locks.setdefault(run_id, asyncio.Lock()):
            return await self._pause(run_id)

    async def _pause(self, run_id: str) -> dict[str, object]:
        run = self._repository.transition_benchmark(
            run_id, expected=("running",), target="paused",
        )
        active_games = [
            str(item["game_id"])
            for item in self._repository.list_benchmark_items(run_id)
            if item["status"] == "running" and isinstance(item.get("game_id"), str)
        ]
        if self._game_pauser is not None and active_games:
            await asyncio.gather(
                *(self._game_pauser(game_id) for game_id in active_games),
                return_exceptions=True,
            )
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return run

    async def cancel(self, run_id: str) -> dict[str, object]:
        async with self._control_locks.setdefault(run_id, asyncio.Lock()):
            return await self._cancel(run_id)

    async def _cancel(self, run_id: str) -> dict[str, object]:
        run = self._repository.transition_benchmark(
            run_id, expected=("draft", "running", "paused", "interrupted"),
            target="cancelled",
        )
        active_games = [
            str(item["game_id"])
            for item in self._repository.list_benchmark_items(run_id)
            if item["status"] == "running" and isinstance(item.get("game_id"), str)
        ]
        if self._game_canceller is not None and active_games:
            await asyncio.gather(
                *(self._game_canceller(game_id) for game_id in active_games),
                return_exceptions=True,
            )
        self._repository.cancel_benchmark_items(run_id)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return run

    async def wait(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _ensure_task(self, run_id: str) -> None:
        current = self._tasks.get(run_id)
        if current is None or current.done():
            self._tasks[run_id] = asyncio.create_task(self._run(run_id))

    async def _run(self, run_id: str) -> None:
        assert self._item_executor is not None
        try:
            run = self._repository.get_benchmark_run(run_id)
            if run is None:
                raise KeyError(run_id)
            config = run.get("config")
            concurrency = config.get("concurrency", 1) if isinstance(config, Mapping) else 1
            concurrency = concurrency if type(concurrency) is int and 1 <= concurrency <= 4 else 1
            recovered = asyncio.Queue()
            for item in self._repository.list_benchmark_items(run_id):
                if item["status"] == "running":
                    recovered.put_nowait(item)
            if recovered.empty():
                first = self._repository.claim_next_benchmark_item(run_id)
                if first is not None:
                    recovered.put_nowait(first)

            async def worker() -> None:
                while True:
                    try:
                        item = recovered.get_nowait()
                    except asyncio.QueueEmpty:
                        item = self._repository.claim_next_benchmark_item(run_id)
                    if item is None:
                        return
                    await self._execute_item(run_id, item)

            await asyncio.gather(*(worker() for _ in range(concurrency)))
            run = self._repository.get_benchmark_run(run_id)
            if run is not None and run["status"] == "running":
                items = self._repository.list_benchmark_items(run_id)
                if all(item["status"] in {"completed", "failed", "cancelled"} for item in items):
                    self._repository.transition_benchmark(
                        run_id, expected=("running",), target="completed",
                    )
        finally:
            if self._tasks.get(run_id) is asyncio.current_task():
                self._tasks.pop(run_id, None)

    async def _execute_item(
        self, run_id: str, item: Mapping[str, object],
    ) -> None:
        assert self._item_executor is not None
        item_index = int(item["item_index"])
        try:
            game_id, status, reason = await self._item_executor(item)
            current = self._repository.get_benchmark_item(run_id, item_index)
            if current is None:
                raise KeyError((run_id, item_index))
            attached = current.get("game_id")
            if attached is None:
                self._repository.attach_benchmark_game(run_id, item_index, game_id)
            elif attached != game_id:
                raise RuntimeError("benchmark executor returned a different game")
            current = self._repository.get_benchmark_item(run_id, item_index)
            if current is not None and current["status"] == "running":
                terminal = "completed" if status == "completed" else "failed"
                self._repository.finish_benchmark_item(
                    run_id, item_index, status=terminal, terminal_reason=reason,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            current = self._repository.get_benchmark_item(run_id, item_index)
            if current is not None and current["status"] == "running":
                self._repository.finish_benchmark_item(
                    run_id, item_index, status="failed",
                    terminal_reason=type(error).__name__,
                )

    def generate_report(self, run_id: str) -> dict[str, Any]:
        run = self._repository.get_benchmark_run(run_id)
        if run is None:
            raise KeyError(run_id)
        items = self._repository.list_benchmark_items(run_id)
        games: list[dict[str, object]] = []
        requests: list[dict[str, object]] = []
        attempts: list[dict[str, object]] = []
        for item in items:
            game_id = item.get("game_id")
            if not isinstance(game_id, str):
                continue
            game = self._repository.get_game(game_id, include_deleted=True)
            if game is None:
                continue
            snapshot = self._repository.get_audience_snapshot(game_id)
            state = {} if snapshot is None else snapshot.get("state", {})
            state = state if isinstance(state, Mapping) else {}
            win = state.get("win_result")
            winner = win.get("winning_camp") if isinstance(win, Mapping) else None
            clock = self._repository.get_runtime_clock(game_id) or {}
            games.append({
                "game_id": game_id, "winner": winner,
                "rounds": state.get("round_number"),
                "active_elapsed_ms": clock.get("active_elapsed_ms"),
                "players": state.get("players", {}),
                "interruption_count": game.get("interruption_count", 0),
            })
            requests.extend(self._repository.list_model_requests(game_id))
            attempts.extend(self._repository.list_model_attempts(game_id))
        report = BenchmarkMetrics.compute(
            games=games, items=items, model_requests=requests,
            model_attempts=attempts,
        )
        report["input_digest"] = hashlib.sha256(_canonical({
            "metrics_digest": report["input_digest"], "run_status": run["status"],
        }).encode("utf-8")).hexdigest()
        cached = self._repository.get_benchmark_report(
            run_id, metric_version=report["metric_version"],
            input_digest=report["input_digest"],
        )
        if cached is not None:
            return dict(cached["report"])
        item_counts = {
            status: sum(item.get("status") == status for item in items)
            for status in ("pending", "running", "completed", "failed", "cancelled")
        }
        terminal_count = sum(
            item_counts[status] for status in ("completed", "failed", "cancelled")
        )
        unknown_tokens = sum(not bool(row.get("usage_known")) for row in attempts)
        incomplete_games = sum(game.get("winner") is None for game in games)
        report = {
            **report,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provisional": run["status"] not in {"completed", "cancelled", "failed"},
            "planned_count": len(items),
            "started_count": len(items) - item_counts["pending"],
            "terminal_count": terminal_count,
            "completed_count": item_counts["completed"],
            "inclusion": {
                "games_with_results": len(games) - incomplete_games,
                "games_without_results": incomplete_games,
            },
            "exclusion": {
                "unstarted_items": item_counts["pending"],
                "failed_items": item_counts["failed"],
                "cancelled_items": item_counts["cancelled"],
            },
            "metrics": {
                "model_performance": report["summary"]["model_performance"],
                "request_quality": report["summary"]["requests"],
                "attempt_usage_and_latency": report["summary"]["attempts"],
                "interruption_rate": report["summary"]["games"]["interruption_rate"],
                "completion_rate": (
                    item_counts["completed"] / terminal_count
                    if terminal_count else None
                ),
                "progress": terminal_count / len(items) if items else 0.0,
            },
            "uncertainty": {
                "mixed_arena": report["summary"]["mixed_uncertainty"],
                "paired_regression": report["summary"]["paired_regression"]["bootstrap"],
                "paired_latency_ms": report["summary"]["paired_latency_ms"],
            },
            "data_quality": {
                "unknown_token_attempts": unknown_tokens,
                "known_token_attempts": len(attempts) - unknown_tokens,
                "interruptions": sum(
                    int(game.get("interruption_count", 0) or 0) for game in games
                ),
                "recovery_retries": sum(
                    int(request.get("recovery_retry_count", 0) or 0)
                    for request in requests
                ),
                "recovery_attempts": sum(
                    type(attempt.get("execution_generation")) is int
                    and int(attempt["execution_generation"]) > 1
                    for attempt in attempts
                ),
                "excluded_pairs": max(
                    0,
                    len({item.get("pair_id") for item in items if item.get("pair_id")})
                    - int(report["summary"]["paired_regression"]["pair_count"]),
                ),
                "latency_pairs_excluded_for_interruptions": report["summary"]
                ["paired_latency_ms"]["excluded_interrupted_pairs"],
                "latency_pairs_excluded_for_missing_data": report["summary"]
                ["paired_latency_ms"]["excluded_incomplete_pairs"],
            },
        }
        self._repository.save_benchmark_report(
            run_id, metric_version=report["metric_version"],
            input_digest=report["input_digest"], report=report,
        )
        return report

    async def request_report(self, run_id: str) -> dict[str, Any]:
        """Read a cached report and deduplicate an asynchronous refresh."""
        if self._repository.get_benchmark_run(run_id) is None:
            raise KeyError(run_id)
        cached = self._repository.get_latest_benchmark_report(run_id)
        task = self._report_tasks.get(run_id)
        failure = self._report_failures.pop(run_id, None)
        if cached is None and failure is not None and task is None:
            return {"status": "failed", "run_id": run_id, "error_code": failure}
        if task is None or task.done():
            task = asyncio.create_task(asyncio.to_thread(self.generate_report, run_id))
            self._report_tasks[run_id] = task

            def complete(done: asyncio.Task[dict[str, Any]]) -> None:
                try:
                    done.result()
                except asyncio.CancelledError:
                    self._report_failures[run_id] = "report_generation_cancelled"
                except Exception as error:
                    self._report_failures[run_id] = type(error).__name__
                finally:
                    if self._report_tasks.get(run_id) is done:
                        self._report_tasks.pop(run_id, None)

            task.add_done_callback(complete)
        if cached is not None:
            return dict(cached["report"])
        return {"status": "pending", "run_id": run_id}

    async def wait_for_report(self, run_id: str) -> dict[str, Any]:
        """Wait for the in-process report refresh, primarily for shutdown/tests."""
        task = self._report_tasks.get(run_id)
        if task is not None:
            return await asyncio.shield(task)
        cached = self._repository.get_latest_benchmark_report(run_id)
        if cached is None:
            raise KeyError(run_id)
        return dict(cached["report"])

    async def aclose(self) -> None:
        for run_id in list(self._tasks):
            run = self._repository.get_benchmark_run(run_id)
            if run is not None and run["status"] == "running":
                self._repository.transition_benchmark(
                    run_id, expected=("running",), target="interrupted",
                )
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        report_tasks = list(self._report_tasks.values())
        if report_tasks:
            await asyncio.gather(*report_tasks, return_exceptions=True)
