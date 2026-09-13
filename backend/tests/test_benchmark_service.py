from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.persistence.repository import GameRepository
from app.services.benchmark_service import BenchmarkService


def _paired_spec() -> dict[str, object]:
    return {
        "name": "regression", "mode": "paired_regression", "seed": 42,
        "repetitions": 2, "scenario": {"scenario_id": "standard", "role_counts": {"villager": 2}},
        "baseline": {"model_config_id": "base", "temperature": 0.2},
        "candidate": {"model_config_id": "candidate", "temperature": 0.2},
    }


def test_paired_schedule_is_deterministic_and_frozen_before_start(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        service = BenchmarkService(repository)
        first = service.create_run(_paired_spec(), client_request_id="client")
        replay = service.create_run(_paired_spec(), client_request_id="client")
        assert replay["run_id"] == first["run_id"]
        assert replay["replayed"] is True
        items = repository.list_benchmark_items(first["run_id"])
        assert len(items) == 4
        assert [item["pair_id"] for item in items] == [
            "pair-0000", "pair-0000", "pair-0001", "pair-0001",
        ]
        assert [item["assignment"] for item in items] == [
            row["assignment"] for row in service._paired_schedule(_paired_spec())
        ]
        for pair_id in ("pair-0000", "pair-0001"):
            assert {
                item["assignment"]["variant"] for item in items if item["pair_id"] == pair_id
            } == {"baseline", "candidate"}
            baseline, candidate = [
                item["assignment"] for item in items if item["pair_id"] == pair_id
            ]
            if baseline["variant"] != "baseline":
                baseline, candidate = candidate, baseline
            baseline_common = {
                key: value for key, value in baseline.items()
                if key not in {"variant", "model"}
            }
            candidate_common = {
                key: value for key, value in candidate.items()
                if key not in {"variant", "model"}
            }
            assert baseline_common == candidate_common
            assert baseline["runtime_seed"] == candidate["runtime_seed"]
            assert Counter(baseline["role_by_seat"].values()) == {"villager": 2}
        assert repository.get_benchmark_run(first["run_id"])["status"] == "draft"
    finally:
        repository.close()


def test_mixed_complete_block_balances_every_model_across_seats(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        service = BenchmarkService(repository)
        run = service.create_run({
            "name": "arena", "mode": "mixed_arena", "seed": 9, "games": 4,
            "scenario": {"scenario_id": "tiny", "role_counts": {"villager": 2}},
            "models": [{"model_config_id": "a"}, {"model_config_id": "b"}],
        })
        counts = {}
        for item in repository.list_benchmark_items(run["run_id"]):
            assert item["assignment"]["balanced"] is True
            for seat, model in item["assignment"]["seat_models"].items():
                key = (seat, model["model_config_id"])
                counts[key] = counts.get(key, 0) + 1
        assert counts == {("1", "a"): 2, ("1", "b"): 2,
                          ("2", "a"): 2, ("2", "b"): 2}

        with pytest.raises(ValueError, match="player count"):
            service.create_run({
                "name": "bad", "mode": "mixed_arena", "seed": 9, "games": 4,
                "scenario": {"scenario_id": "tiny", "role_counts": {"villager": 2}},
                "models": [{"model_config_id": "a"}],
            })
    finally:
        repository.close()


def test_mixed_n_squared_block_balances_models_across_roles_and_seats() -> None:
    specification = {
        "name": "arena", "mode": "mixed_arena", "seed": 917, "games": 9,
        "concurrency": 1,
        "scenario": {
            "scenario_id": "three-player",
            "role_counts": {"werewolf": 1, "villager": 2},
        },
        "models": [
            {"model_config_id": "a"},
            {"model_config_id": "b"},
            {"model_config_id": "c"},
        ],
    }

    schedule = BenchmarkService._mixed_schedule(specification)
    concurrent = deepcopy(specification)
    concurrent["concurrency"] = 4

    assert schedule == BenchmarkService._mixed_schedule(concurrent)
    assert len(schedule) == 9
    assert {item["block_index"] for item in schedule} == {0}
    assert all(item["assignment"]["balanced"] is True for item in schedule)
    assert len({item["assignment"]["runtime_seed"] for item in schedule}) == 9

    model_by_seat: Counter[tuple[str, str]] = Counter()
    model_by_role: Counter[tuple[str, str]] = Counter()
    role_by_seat: Counter[tuple[str, str]] = Counter()
    for item in schedule:
        assignment = item["assignment"]
        roles = assignment["role_by_seat"]
        models = assignment["seat_models"]
        assert set(roles) == set(models) == {"1", "2", "3"}
        assert Counter(roles.values()) == {"werewolf": 1, "villager": 2}
        for seat in ("1", "2", "3"):
            model = models[seat]["model_config_id"]
            role = roles[seat]
            model_by_seat[seat, model] += 1
            model_by_role[model, role] += 1
            role_by_seat[seat, role] += 1

    assert set(model_by_seat.values()) == {3}
    assert {
        model: {role: model_by_role[model, role] for role in ("werewolf", "villager")}
        for model in ("a", "b", "c")
    } == {
        "a": {"werewolf": 3, "villager": 6},
        "b": {"werewolf": 3, "villager": 6},
        "c": {"werewolf": 3, "villager": 6},
    }
    assert set(role_by_seat.values()) == {3, 6}


def test_mixed_truncation_marks_every_row_unbalanced_and_keeps_block_clusters() -> None:
    schedule = BenchmarkService._mixed_schedule({
        "seed": 7, "games": 5,
        "scenario": {"scenario_id": "tiny", "role_counts": {"wolf": 1, "villager": 1}},
        "models": [{"model_config_id": "a"}, {"model_config_id": "b"}],
    })

    assert [item["block_index"] for item in schedule] == [0, 0, 0, 0, 1]
    assert all(item["assignment"]["balanced"] is False for item in schedule)
    assert all("role_by_seat" in item["assignment"] for item in schedule)
    assert all(type(item["assignment"]["runtime_seed"]) is int for item in schedule)


def test_executor_claims_persisted_items_and_resume_continues_remaining(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    calls: list[int] = []
    release = asyncio.Event()
    started = asyncio.Event()

    async def execute(item):
        calls.append(item["item_index"])
        started.set()
        if item["item_index"] == 0:
            await release.wait()
        game_id = f"game-{item['item_index']}"
        repository.create_game(
            game_id=game_id, name=game_id, config={}, execution_status="completed",
            source="benchmark", benchmark_run_id=item["run_id"], model_snapshot=[],
        )
        return game_id, "completed", None

    async def scenario() -> None:
        service = BenchmarkService(repository, item_executor=execute)
        run = service.create_run(_paired_spec(), client_request_id="client")
        await service.start(run["run_id"])
        await started.wait()
        await service.pause(run["run_id"])
        release.set()
        await service.wait(run["run_id"])
        assert repository.get_benchmark_run(run["run_id"])["status"] == "paused"
        assert calls == [0]
        await service.resume(run["run_id"])
        await service.wait(run["run_id"])
        # Item 0 was paused before it created a game, so resuming safely
        # re-enters that item. The durable game binding prevents duplicates
        # once creation has committed.
        assert calls == [0, 0, 1, 2, 3]
        assert repository.get_benchmark_run(run["run_id"])["status"] == "completed"
        assert all(item["status"] == "completed" for item in repository.list_benchmark_items(run["run_id"]))

    try:
        asyncio.run(scenario())
    finally:
        repository.close()


def test_report_uses_frozen_database_facts_and_is_reproducible(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        service = BenchmarkService(repository)
        run = service.create_run({
            "name": "arena", "mode": "mixed_arena", "seed": 7, "games": 1,
            "scenario": {"scenario_id": "tiny", "role_counts": {"villager": 1}},
            "models": [{"model_config_id": "model-a"}],
        }, client_request_id="arena")
        repository.create_game(
            game_id="game", name="game", config={}, execution_status="completed",
            source="benchmark", benchmark_run_id=run["run_id"], model_snapshot=[],
        )
        # Simulate the durable item lifecycle so the game is traceable to the run.
        repository.transition_benchmark(run["run_id"], expected=("draft",), target="running")
        item = repository.claim_next_benchmark_item(run["run_id"])
        repository.attach_benchmark_game(run["run_id"], item["item_index"], "game")
        repository.finish_benchmark_item(run["run_id"], item["item_index"], status="completed", terminal_reason=None)
        report1 = service.generate_report(run["run_id"])
        report2 = service.generate_report(run["run_id"])
        assert report2 == report1
        assert report1["input_digest"] == report2["input_digest"]
        assert report1["metric_version"] == "v2"
        assert report1["planned_count"] == 1
        assert report1["terminal_count"] == 1
        assert report1["completed_count"] == 1
        assert report1["provisional"] is True
        assert report1["generated_at"]
        assert report1["data_quality"]["unknown_token_attempts"] == 0
        assert report1["metrics"]["interruption_rate"] == 0.0
        assert report1["uncertainty"]["mixed_arena"] == []
        assert report1["uncertainty"]["paired_latency_ms"]["pair_count"] == 0
    finally:
        repository.close()


def test_report_digest_tracks_late_usage_and_recovery_quality(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        service = BenchmarkService(repository)
        run = service.create_run({
            "name": "arena", "mode": "mixed_arena", "seed": 7, "games": 1,
            "scenario": {"scenario_id": "tiny", "role_counts": {"villager": 1}},
            "models": [{"model_config_id": "model-a"}],
        }, client_request_id="late-usage")
        repository.transition_benchmark(
            run["run_id"], expected=("draft",), target="running",
        )
        item = repository.claim_next_benchmark_item(run["run_id"])
        repository.create_game(
            game_id="game", name="game", config={}, execution_status="running",
            source="benchmark", benchmark_run_id=run["run_id"], model_snapshot=[],
            execution_generation=1,
        )
        repository.attach_benchmark_game(run["run_id"], item["item_index"], "game")
        repository.transition_execution(
            "game", expected=("running",), target="interrupted",
        )
        repository.transition_execution(
            "game", expected=("interrupted",), target="running",
            increment_generation=True,
        )
        repository.prepare_model_request(
            game_id="game", request_id="request", actor_seat=1,
            action_position="vote:1:1", request_digest="digest",
            provider_profile="test", model_id="model-a",
        )
        repository.resolve_model_request(
            "game", "request", status="unknown", normalized_result=None,
        )
        assert repository.reserve_recovery_retry("game", "request") is True
        attempt = repository.start_model_attempt(
            game_id="game", request_id="request", execution_generation=2,
        )

        before = service.generate_report(run["run_id"])
        assert before["summary"]["attempts"]["token_usage"] == {
            "known_attempts": 0, "unknown_attempts": 1,
            "known_prompt_tokens": 0, "known_completion_tokens": 0,
            "known_total_tokens": 0, "completeness_rate": 0.0,
        }

        repository.finish_model_attempt(
            attempt["attempt_id"], status="resolved", failure_code=None,
            elapsed_ms=12,
            usage={"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
        )
        after = service.generate_report(run["run_id"])

        assert after["input_digest"] != before["input_digest"]
        assert after["summary"]["attempts"]["token_usage"]["known_total_tokens"] == 12
        assert after["data_quality"] == {
            "unknown_token_attempts": 0,
            "known_token_attempts": 1,
            "interruptions": 1,
            "recovery_retries": 1,
            "recovery_attempts": 1,
            "excluded_pairs": 0,
            "latency_pairs_excluded_for_interruptions": 0,
            "latency_pairs_excluded_for_missing_data": 0,
        }
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("specification", "message"),
    [
        ({"name": "x", "mode": "mixed_arena", "seed": 1,
          "scenario": {}, "models": [], "games": 1,
          "extra": float("nan")}, "canonical"),
        ({"name": None, "mode": "mixed_arena", "seed": 1}, "name"),
        ({"name": " ", "mode": "mixed_arena", "seed": 1}, "name"),
        ({"name": "x", "mode": "unknown", "seed": 1}, "mode"),
        ({"name": "x", "mode": "mixed_arena", "seed": "1"}, "seed"),
        ({"name": "x", "mode": "mixed_arena", "seed": -1}, "seed"),
    ],
)
def test_create_run_rejects_invalid_identity_and_noncanonical_specs(
    specification, message,
) -> None:
    with pytest.raises(ValueError, match=message):
        BenchmarkService(MagicMock()).create_run(specification)


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        (None, "scenario is required"),
        ({"scenario_id": 1, "role_counts": {"villager": 1}}, "scenario_id"),
        ({"scenario_id": "", "role_counts": {"villager": 1}}, "scenario_id"),
        ({"scenario_id": "x", "role_counts": []}, "role_counts are required"),
        ({"scenario_id": "x", "role_counts": {}}, "role_counts are required"),
        ({"scenario_id": "x", "role_counts": {1: 1}}, "invalid scenario"),
        ({"scenario_id": "x", "role_counts": {"villager": "1"}}, "invalid scenario"),
        ({"scenario_id": "x", "role_counts": {"villager": -1}}, "invalid scenario"),
        ({"scenario_id": "x", "role_counts": {"villager": 0}}, "contain players"),
    ],
)
def test_scenario_validation_rejects_malformed_role_counts(scenario, message) -> None:
    with pytest.raises(ValueError, match=message):
        BenchmarkService._scenario({"scenario": scenario})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"repetitions": "1"}, "repetitions"),
        ({"repetitions": 0}, "repetitions"),
        ({"baseline": None}, "requires baseline"),
        ({"candidate": None}, "requires baseline"),
        ({"candidate": {"model_config_id": "base", "temperature": 0.2}},
         "must differ"),
    ],
)
def test_paired_schedule_rejects_invalid_comparison_plan(changes, message) -> None:
    spec = _paired_spec()
    spec.update(changes)
    with pytest.raises(ValueError, match=message):
        BenchmarkService._paired_schedule(spec)


@pytest.mark.parametrize(
    ("games", "models", "message"),
    [
        ("1", [{"model_config_id": "a"}], "games"),
        (0, [{"model_config_id": "a"}], "games"),
        (1, None, "model configurations"),
        (1, [], "model configurations"),
        (1, ["model"], "model configurations"),
    ],
)
def test_mixed_schedule_rejects_invalid_game_and_model_plan(games, models, message) -> None:
    spec = {
        "seed": 1, "games": games, "models": models,
        "scenario": {"scenario_id": "tiny", "role_counts": {"villager": 1}},
    }
    with pytest.raises(ValueError, match=message):
        BenchmarkService._mixed_schedule(spec)


@pytest.mark.asyncio
async def test_start_and_resume_require_an_executor() -> None:
    service = BenchmarkService(MagicMock())
    with pytest.raises(RuntimeError, match="not configured"):
        await service.start("run")
    with pytest.raises(RuntimeError, match="not configured"):
        await service.resume("run")


@pytest.mark.asyncio
async def test_pause_and_cancel_delegate_active_games_and_cancel_workers() -> None:
    repository = MagicMock()
    repository.transition_benchmark.return_value = {"run_id": "run", "status": "paused"}
    repository.list_benchmark_items.return_value = [
        {"status": "running", "game_id": "game"},
        {"status": "running", "game_id": None},
        {"status": "completed", "game_id": "done"},
    ]
    pauser = AsyncMock()
    canceller = AsyncMock()
    service = BenchmarkService(
        repository, item_executor=AsyncMock(),
        game_pauser=pauser, game_canceller=canceller,
    )
    worker = asyncio.create_task(asyncio.Event().wait())
    service._tasks["run"] = worker

    paused = await service.pause("run")
    await asyncio.sleep(0)
    assert paused["status"] == "paused"
    pauser.assert_awaited_once_with("game")
    assert worker.cancelled()

    worker = asyncio.create_task(asyncio.Event().wait())
    service._tasks["run"] = worker
    repository.transition_benchmark.return_value = {"run_id": "run", "status": "cancelled"}
    cancelled = await service.cancel("run")
    await asyncio.sleep(0)
    assert cancelled["status"] == "cancelled"
    canceller.assert_awaited_once_with("game")
    repository.cancel_benchmark_items.assert_called_once_with("run")
    assert worker.cancelled()


@pytest.mark.asyncio
async def test_idle_controls_and_wait_are_noops() -> None:
    repository = MagicMock()
    repository.transition_benchmark.return_value = {"run_id": "run"}
    repository.list_benchmark_items.return_value = []
    service = BenchmarkService(
        repository, item_executor=AsyncMock(),
        game_pauser=AsyncMock(), game_canceller=AsyncMock(),
    )
    await service.pause("run")
    await service.cancel("run")
    await service.wait("missing")
    service._game_pauser.assert_not_awaited()
    service._game_canceller.assert_not_awaited()

    cancelled = asyncio.create_task(asyncio.sleep(10))
    cancelled.cancel()
    service._tasks["cancelled"] = cancelled
    await service.wait("cancelled")


@pytest.mark.asyncio
async def test_ensure_task_does_not_duplicate_live_worker() -> None:
    service = BenchmarkService(MagicMock(), item_executor=AsyncMock())
    worker = asyncio.create_task(asyncio.Event().wait())
    service._tasks["run"] = worker
    service._ensure_task("run")
    assert service._tasks["run"] is worker
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_run_handles_missing_run_and_nonterminal_work() -> None:
    missing_repository = MagicMock()
    missing_repository.get_benchmark_run.return_value = None
    missing = BenchmarkService(missing_repository, item_executor=AsyncMock())
    with pytest.raises(KeyError, match="run"):
        await missing._run("run")
    assert "run" not in missing._tasks

    repository = MagicMock()
    repository.get_benchmark_run.side_effect = [
        {"run_id": "run", "status": "running", "config": {"concurrency": 9}},
        {"run_id": "run", "status": "running"},
    ]
    repository.list_benchmark_items.side_effect = [[], [{"status": "pending"}]]
    repository.claim_next_benchmark_item.return_value = None
    service = BenchmarkService(repository, item_executor=AsyncMock())
    await service._run("run")
    repository.transition_benchmark.assert_not_called()

    repository = MagicMock()
    repository.get_benchmark_run.side_effect = [
        {"run_id": "run", "status": "running", "config": None}, None,
    ]
    repository.list_benchmark_items.return_value = []
    repository.claim_next_benchmark_item.return_value = None
    await BenchmarkService(repository, item_executor=AsyncMock())._run("run")


@pytest.mark.asyncio
async def test_execute_item_attaches_results_and_handles_failures() -> None:
    repository = MagicMock()
    repository.get_benchmark_item.side_effect = [
        {"game_id": None, "status": "running"},
        {"game_id": "game", "status": "running"},
    ]
    service = BenchmarkService(
        repository,
        item_executor=AsyncMock(return_value=("game", "cancelled", "timeout")),
    )
    await service._execute_item("run", {"item_index": 0})
    repository.attach_benchmark_game.assert_called_once_with("run", 0, "game")
    repository.finish_benchmark_item.assert_called_once_with(
        "run", 0, status="failed", terminal_reason="timeout",
    )

    repository = MagicMock()
    repository.get_benchmark_item.side_effect = [
        {"game_id": "other", "status": "running"},
        {"game_id": "other", "status": "running"},
    ]
    service = BenchmarkService(
        repository, item_executor=AsyncMock(return_value=("game", "completed", None)),
    )
    await service._execute_item("run", {"item_index": 1})
    repository.finish_benchmark_item.assert_called_once_with(
        "run", 1, status="failed", terminal_reason="RuntimeError",
    )

    repository = MagicMock()
    repository.get_benchmark_item.side_effect = [None, None]
    service = BenchmarkService(
        repository, item_executor=AsyncMock(return_value=("game", "completed", None)),
    )
    await service._execute_item("run", {"item_index": 2})
    repository.finish_benchmark_item.assert_not_called()


@pytest.mark.asyncio
async def test_execute_item_propagates_cancellation() -> None:
    async def cancelled(_item):
        raise asyncio.CancelledError

    service = BenchmarkService(MagicMock(), item_executor=cancelled)
    with pytest.raises(asyncio.CancelledError):
        await service._execute_item("run", {"item_index": 0})


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_item", [None, {"game_id": "game", "status": "completed"}])
async def test_execute_item_accepts_existing_binding_and_terminal_races(
    terminal_item,
) -> None:
    repository = MagicMock()
    repository.get_benchmark_item.side_effect = [
        {"game_id": "game", "status": "running"}, terminal_item,
    ]
    service = BenchmarkService(
        repository, item_executor=AsyncMock(return_value=("game", "completed", None)),
    )
    await service._execute_item("run", {"item_index": 0})
    repository.attach_benchmark_game.assert_not_called()
    repository.finish_benchmark_item.assert_not_called()


def test_report_rejects_missing_run_and_skips_unbound_or_missing_games() -> None:
    missing = MagicMock()
    missing.get_benchmark_run.return_value = None
    with pytest.raises(KeyError, match="run"):
        BenchmarkService(missing).generate_report("run")

    repository = MagicMock()
    repository.get_benchmark_run.return_value = {
        "run_id": "run", "status": "completed",
    }
    repository.list_benchmark_items.return_value = [
        {"game_id": None, "status": "pending", "pair_id": None},
        {"game_id": "missing", "status": "failed", "pair_id": None},
    ]
    repository.get_game.return_value = None
    repository.get_benchmark_report.return_value = None
    report = BenchmarkService(repository).generate_report("run")
    assert report["provisional"] is False
    assert report["summary"]["games"]["total"] == 0
    assert report["metrics"]["completion_rate"] == 0.0
    repository.save_benchmark_report.assert_called_once()

    repository.list_benchmark_items.return_value = []
    empty = BenchmarkService(repository).generate_report("run")
    assert empty["metrics"]["model_performance"] == []
    assert empty["metrics"]["completion_rate"] is None
    assert empty["metrics"]["progress"] == 0.0
    assert empty["metrics"]["request_quality"]["eligible_terminal"] == 0
    assert empty["metrics"]["attempt_usage_and_latency"]["total"] == 0
    assert empty["metrics"]["interruption_rate"] is None


@pytest.mark.asyncio
async def test_request_report_returns_pending_deduplicates_and_serves_cached_refresh() -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {"run_id": "run", "status": "running"}
    repository.get_latest_benchmark_report.return_value = None
    service = BenchmarkService(repository)
    generated = {"metric_version": "v2", "input_digest": "digest"}
    service.generate_report = MagicMock(return_value=generated)

    first = await service.request_report("run")
    second = await service.request_report("run")
    assert first == second == {"status": "pending", "run_id": "run"}
    assert len(service._report_tasks) == 1
    assert await service.wait_for_report("run") == generated
    assert service.generate_report.call_count == 1
    await asyncio.sleep(0)

    repository.get_latest_benchmark_report.return_value = {"report": generated}
    cached = await service.request_report("run")
    assert cached == generated
    await service.aclose()


@pytest.mark.asyncio
async def test_request_report_surfaces_background_failure_once_and_validates_run() -> None:
    repository = MagicMock()
    repository.get_benchmark_run.return_value = None
    service = BenchmarkService(repository)
    with pytest.raises(KeyError):
        await service.request_report("missing")

    repository.get_benchmark_run.return_value = {"run_id": "run"}
    repository.get_latest_benchmark_report.return_value = None
    service.generate_report = MagicMock(side_effect=ValueError("bad facts"))
    assert await service.request_report("run") == {
        "status": "pending", "run_id": "run",
    }
    with pytest.raises(ValueError, match="bad facts"):
        await service.wait_for_report("run")
    await asyncio.sleep(0)
    assert await service.request_report("run") == {
        "status": "failed", "run_id": "run", "error_code": "ValueError",
    }
    with pytest.raises(KeyError):
        await service.wait_for_report("run")


@pytest.mark.asyncio
async def test_aclose_interrupts_running_runs_and_cancels_all_workers() -> None:
    repository = MagicMock()
    repository.get_benchmark_run.side_effect = [
        {"status": "running"}, {"status": "paused"}, None,
    ]
    service = BenchmarkService(repository, item_executor=AsyncMock())
    for run_id in ("running", "paused", "missing"):
        service._tasks[run_id] = asyncio.create_task(asyncio.Event().wait())

    await service.aclose()

    repository.transition_benchmark.assert_called_once_with(
        "running", expected=("running",), target="interrupted",
    )
    assert all(task.cancelled() for task in service._tasks.values())

    empty = BenchmarkService(repository, item_executor=AsyncMock())
    await empty.aclose()
