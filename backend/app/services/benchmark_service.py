"""Durable benchmark planning, execution control, and report generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.persistence.repository import (
    GameReferencedByBenchmark, GameRepository, InvalidExecutionTransition,
)
from app.services.benchmark_metrics import BenchmarkMetrics
from app.services.game_summary import read_game_summary
from app.stores.model_config_store import get_model_config_store

logger = logging.getLogger(__name__)


def _model_labels() -> dict[str, str]:
    """Config id → display label, best-effort.

    ``model_performance`` and ``mixed_uncertainty`` rows key on the config id;
    the label adds the human-readable ``name (model_id)`` the store knows at
    report-generation time. A missing store or deleted config just yields no
    label and the raw id stays as the fallback.
    """
    labels: dict[str, str] = {}
    try:
        configs = get_model_config_store().list_all()
    except Exception:
        return labels
    for config in configs:
        label = config.name or config.model_id
        if config.model_id and config.model_id not in label:
            label = f"{label} ({config.model_id})"
        labels[config.id] = label
    return labels


def _apply_model_labels(report: Mapping[str, Any]) -> None:
    labels = _model_labels()
    if not labels:
        return
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        return
    for key in ("model_performance", "mixed_uncertainty"):
        rows = summary.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = labels.get(row.get("model"))
            if label:
                row["model_label"] = label


ItemExecutor = Callable[[Mapping[str, object]], Awaitable[tuple[str, str, str | None]]]
GameControl = Callable[[str], Awaitable[None]]
OwnedGameDeleter = Callable[[str, str], Awaitable[None]]


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
        owned_game_deleter: OwnedGameDeleter | None = None,
        data_dir: str | None = None,
        claim_retry_seconds: float = 1.0,
    ) -> None:
        self._repository = repository
        # Where the per-game ``summary.json`` files live. Explicit in production;
        # the environment default keeps test constructions working.
        self._data_dir = data_dir or os.getenv("WOLFKILLER_DATA_DIR", "data")
        self._item_executor = item_executor
        self._game_pauser = game_pauser
        self._game_canceller = game_canceller
        self._owned_game_deleter = owned_game_deleter
        self._claim_retry_seconds = claim_retry_seconds
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
        # Identical seat configs make a model rotation a copy of the same
        # seating. Drop that axis so a block is one role rotation per seat,
        # not player_count copies of each pin.
        same_model = len({_canonical(dict(model)) for model in models}) == 1
        model_span = 1 if same_model else total
        block_size = total * model_span
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
                for model_rotation in range(model_span):
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
        # Stop the workers first: while they live, an in-flight ``create_game``
        # can still commit a game that a pre-scan of live games would miss.
        # After they are joined, the games table lists every game that exists.
        await self._stop_run_task(run_id)
        if self._game_pauser is not None:
            await asyncio.gather(
                *(
                    self._game_pauser(str(game["game_id"]))
                    for game in self._repository.list_benchmark_games(
                        run_id, ("running",),
                    )
                ),
                return_exceptions=True,
            )
        return run

    async def cancel(self, run_id: str) -> dict[str, object]:
        async with self._control_locks.setdefault(run_id, asyncio.Lock()):
            return await self._cancel(run_id)

    async def _cancel(self, run_id: str) -> dict[str, object]:
        run = self._repository.transition_benchmark(
            run_id, expected=("draft", "running", "paused", "interrupted"),
            target="cancelled",
        )
        # Freeze the items first: ``create_game`` binds its game inside a
        # transaction guarded by ``item.status='running' AND game_id IS NULL``,
        # so once the items are cancelled no new game can ever commit for this
        # run — the games-table scan below is then complete by construction.
        self._repository.cancel_benchmark_items(run_id)
        await self._stop_run_task(run_id)
        if self._game_canceller is not None:
            await asyncio.gather(
                *(
                    self._game_canceller(str(game["game_id"]))
                    for game in self._repository.list_benchmark_games(
                        run_id,
                        ("running", "paused", "interrupted", "recovery_blocked"),
                    )
                ),
                return_exceptions=True,
            )
        return run

    async def _stop_run_task(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def delete_owned_game(self, run_id: str, game_id: str) -> None:
        if self._repository.get_benchmark_run(run_id) is None:
            raise KeyError(run_id)
        if self._owned_game_deleter is None:
            raise RuntimeError("benchmark game deletion is unavailable")
        await self._owned_game_deleter(run_id, game_id)
        self._repository.delete_benchmark_reports(run_id)

    async def delete_owned_games(
        self, run_id: str, game_ids: list[str],
    ) -> dict[str, list]:
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        seen: set[str] = set()
        for game_id in game_ids:
            if game_id in seen:
                continue
            seen.add(game_id)
            try:
                await self.delete_owned_game(run_id, game_id)
                deleted.append(game_id)
            except GameReferencedByBenchmark as error:
                failed.append({
                    "game_id": game_id, "code": "game_referenced_by_benchmark",
                    "message": str(error),
                })
            except KeyError:
                failed.append({
                    "game_id": game_id, "code": "not_found",
                    "message": "Game or benchmark not found",
                })
            except OSError as error:
                failed.append({
                    "game_id": game_id, "code": "archive_busy",
                    "message": str(error),
                })
            except RuntimeError as error:
                failed.append({
                    "game_id": game_id, "code": "unavailable",
                    "message": str(error),
                })
        return {"deleted": deleted, "failed": failed}

    async def delete_run(self, run_id: str) -> None:
        run = self._repository.get_benchmark_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] in {"draft", "running", "paused", "interrupted"}:
            await self._cancel(run_id)
        for item in self._repository.list_benchmark_items(run_id):
            game_id = item.get("game_id")
            if isinstance(game_id, str) and game_id:
                try:
                    await self.delete_owned_game(run_id, game_id)
                except KeyError:
                    continue
        self._repository.delete_benchmark_run(run_id)

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
                try:
                    first = self._repository.claim_next_benchmark_item(run_id)
                except Exception:
                    # Kickoff convenience claim — the workers claim (and retry)
                    # themselves, so a transient miss here must not kill the
                    # supervisor before it even spawns them.
                    logger.exception(
                        "Benchmark kickoff claim failed; workers will retry run=%s",
                        run_id,
                    )
                    first = None
                if first is not None:
                    recovered.put_nowait(first)

            async def worker() -> None:
                while True:
                    try:
                        item = recovered.get_nowait()
                    except asyncio.QueueEmpty:
                        try:
                            item = self._repository.claim_next_benchmark_item(
                                run_id,
                            )
                        except Exception:
                            # The claim is an atomic UPDATE, so retrying is
                            # safe — and staying alive beats dying: an escaped
                            # error here used to propagate through the gather
                            # and kill every sibling worker, freezing the run.
                            logger.exception(
                                "Benchmark item claim failed; retrying run=%s",
                                run_id,
                            )
                            await asyncio.sleep(self._claim_retry_seconds)
                            continue
                    if item is None:
                        return
                    try:
                        await self._execute_item(run_id, item)
                    except Exception:
                        # ``_execute_item`` already contains its own failures;
                        # this guard catches an exception raised inside its
                        # handler (e.g. a repository error mid-bookkeeping) so
                        # one bad item can never kill the worker.
                        logger.exception(
                            "Benchmark item execution escaped containment: "
                            "run=%s item=%s",
                            run_id, item.get("item_index"),
                        )

            await asyncio.gather(
                *(worker() for _ in range(concurrency)), return_exceptions=True,
            )
            run = self._repository.get_benchmark_run(run_id)
            if run is not None and run["status"] == "running":
                items = self._repository.list_benchmark_items(run_id)
                if all(item["status"] in {"completed", "failed", "cancelled"} for item in items):
                    self._repository.transition_benchmark(
                        run_id, expected=("running",), target="completed",
                    )
                else:
                    # Workers drained while items are still pending/running:
                    # nothing is left to drive them, so make the run honestly
                    # interrupted (restartable via ``start``) instead of a
                    # silent ``running`` zombie.
                    try:
                        self._repository.transition_benchmark(
                            run_id, expected=("running",), target="interrupted",
                        )
                        logger.error(
                            "Benchmark run %s drained its workers with "
                            "non-terminal items; marked interrupted", run_id,
                        )
                    except InvalidExecutionTransition:
                        pass
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
        # Decision quality lives in each game's ``summary.json`` (the durable log
        # digest), not in the fact tables: read them best-effort so a game without
        # one degrades to "no samples" instead of failing the whole report.
        summaries = [
            summary for summary in (
                read_game_summary(self._data_dir, str(game.get("game_id")))
                for game in games
            ) if summary is not None
        ]
        report = BenchmarkMetrics.compute(
            games=games, items=items, model_requests=requests,
            model_attempts=attempts, summaries=summaries,
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
                "summaries_available": len(summaries),
                "summaries_missing": len(games) - len(summaries),
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
        _apply_model_labels(report)
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
