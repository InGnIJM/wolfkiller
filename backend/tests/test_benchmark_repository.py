from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.persistence.repository import (
    CommitConflict,
    GameRepository,
    InvalidExecutionTransition,
    ModelAttemptQuotaExceeded,
)


def _schedule() -> list[dict[str, object]]:
    return [
        {"scenario_id": "standard", "pair_id": "pair-1", "block_index": 0,
         "assignment": {"variant": "baseline", "seats": {"1": "model-a"}}},
        {"scenario_id": "standard", "pair_id": "pair-1", "block_index": 0,
         "assignment": {"variant": "candidate", "seats": {"1": "model-b"}}},
    ]


def test_model_config_references_only_include_unfinished_work(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_game(
            game_id="active-game", name="active", execution_status="paused",
            source="native", model_snapshot=[],
            config={"model_runtime": [{"config_id": "model-a"}]},
        )
        repository.create_game(
            game_id="finished-game", name="finished", execution_status="completed",
            source="native", model_snapshot=[],
            config={"model_runtime": [{"config_id": "model-a"}]},
        )
        repository.create_benchmark_run(
            run_id="run-1", client_request_id="client-ref", request_digest="ref",
            name="A/B", mode="paired_regression",
            config={"baseline": {"model_config_id": "model-a"}},
            schedule=_schedule(),
        )

        assert repository.model_config_references("model-a") == {
            "game_ids": ["active-game"],
            "benchmark_run_ids": ["run-1"],
        }
        assert repository.model_config_references("missing") == {
            "game_ids": [], "benchmark_run_ids": [],
        }
    finally:
        repository.close()


def test_benchmark_plan_is_fully_persisted_and_idempotent_before_claim(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        created = repository.create_benchmark_run(
            run_id="run-1", client_request_id="client-1", request_digest="request",
            name="A/B", mode="paired_regression", config={"seed": 42},
            schedule=_schedule(),
        )
        replay = repository.create_benchmark_run(
            run_id="different-ignored", client_request_id="client-1", request_digest="request",
            name="A/B", mode="paired_regression", config={"seed": 42},
            schedule=_schedule(),
        )
        assert created["run_id"] == replay["run_id"] == "run-1"
        assert replay["replayed"] is True
        run = repository.get_benchmark_run("run-1")
        assert run["config"] == {"seed": 42}
        items = repository.list_benchmark_items("run-1")
        assert [item["item_index"] for item in items] == [0, 1]
        assert items[1]["assignment"]["variant"] == "candidate"
        with pytest.raises(CommitConflict, match="client_request_id"):
            repository.create_benchmark_run(
                run_id="run-x", client_request_id="client-1", request_digest="changed",
                name="x", mode="paired_regression", config={}, schedule=[],
            )
    finally:
        repository.close()


def test_benchmark_claim_pause_resume_and_item_completion(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_benchmark_run(
            run_id="run-1", client_request_id="client-1", request_digest="request",
            name="A/B", mode="paired_regression", config={}, schedule=_schedule(),
        )
        repository.transition_benchmark("run-1", expected=("draft",), target="running")
        item = repository.claim_next_benchmark_item("run-1")
        assert item["item_index"] == 0 and item["status"] == "running"
        repository.create_game(
            game_id="game-1", name="bench game", config={},
            execution_status="running", source="benchmark", model_snapshot=[],
            benchmark_run_id="run-1",
        )
        repository.attach_benchmark_game("run-1", 0, "game-1")
        repository.finish_benchmark_item("run-1", 0, status="completed", terminal_reason=None)
        assert repository.list_benchmark_items("run-1")[0]["game_id"] == "game-1"

        repository.transition_benchmark("run-1", expected=("running",), target="paused")
        assert repository.claim_next_benchmark_item("run-1") is None
        repository.transition_benchmark("run-1", expected=("paused",), target="running")
        assert repository.claim_next_benchmark_item("run-1")["item_index"] == 1
        with pytest.raises(InvalidExecutionTransition):
            repository.transition_benchmark("run-1", expected=("draft",), target="completed")
    finally:
        repository.close()


def test_benchmark_game_creation_binds_claimed_item_atomically(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_benchmark_run(
            run_id="run-1", client_request_id="client-1", request_digest="request",
            name="A/B", mode="paired_regression", config={}, schedule=_schedule(),
        )
        repository.transition_benchmark("run-1", expected=("draft",), target="running")
        item = repository.claim_next_benchmark_item("run-1")

        repository.create_game(
            game_id="game-1", name="bench game", config={},
            execution_status="running", source="benchmark", model_snapshot=[],
            benchmark_run_id="run-1", benchmark_item_index=item["item_index"],
        )

        bound = repository.get_benchmark_item("run-1", item["item_index"])
        assert bound["game_id"] == "game-1"
        assert repository.get_game("game-1")["benchmark_run_id"] == "run-1"

        with pytest.raises(CommitConflict, match="benchmark item"):
            repository.create_game(
                game_id="orphan", name="orphan", config={},
                execution_status="running", source="benchmark", model_snapshot=[],
                benchmark_run_id="run-1", benchmark_item_index=item["item_index"],
            )
        assert repository.get_game("orphan") is None
    finally:
        repository.close()


def test_cancelling_items_and_startup_interrupt_are_durable(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_benchmark_run(
            run_id="run-1", client_request_id="client-1", request_digest="request",
            name="A/B", mode="paired_regression", config={}, schedule=_schedule(),
        )
        repository.transition_benchmark("run-1", expected=("draft",), target="running")
        repository.claim_next_benchmark_item("run-1")

        assert repository.interrupt_running_benchmarks() == ["run-1"]
        assert repository.get_benchmark_run("run-1")["status"] == "interrupted"
        assert repository.get_benchmark_item("run-1", 0)["status"] == "running"

        repository.transition_benchmark(
            "run-1", expected=("interrupted",), target="cancelled",
        )
        repository.cancel_benchmark_items("run-1")
        items = repository.list_benchmark_items("run-1")
        assert [(item["status"], item["terminal_reason"]) for item in items] == [
            ("cancelled", "cancelled"),
            ("cancelled", "cancelled_before_start"),
        ]
    finally:
        repository.close()


def test_benchmark_report_is_deduplicated_by_metric_version_and_input_digest(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_benchmark_run(
            run_id="run-1", client_request_id="client-1", request_digest="request",
            name="A/B", mode="paired_regression", config={}, schedule=[],
        )
        repository.save_benchmark_report(
            "run-1", metric_version="v1", input_digest="facts",
            report={"winner": "candidate"},
        )
        repository.save_benchmark_report(
            "run-1", metric_version="v1", input_digest="facts",
            report={"winner": "candidate"},
        )
        assert repository.get_benchmark_report(
            "run-1", metric_version="v1", input_digest="facts",
        )["report"] == {"winner": "candidate"}
        assert repository.get_latest_benchmark_report("missing") is None
        assert repository.get_latest_benchmark_report("run-1")["report"] == {
            "winner": "candidate",
        }
        repository.save_benchmark_report(
            "run-1", metric_version="v2", input_digest="new-facts",
            report={"winner": "baseline", "metric_version": "v2"},
        )
        assert repository.get_latest_benchmark_report("run-1")["report"] == {
            "winner": "baseline", "metric_version": "v2",
        }
        with pytest.raises(CommitConflict, match="report"):
            repository.save_benchmark_report(
                "run-1", metric_version="v1", input_digest="facts",
                report={"winner": "baseline"},
            )
    finally:
        repository.close()


def test_model_attempt_quota_is_atomic_across_repository_instances(tmp_path) -> None:
    first = GameRepository(tmp_path)
    second = GameRepository(tmp_path)
    try:
        first.create_benchmark_run(
            run_id="run-1", client_request_id="client-1", request_digest="request",
            name="quota", mode="mixed_arena",
            config={"max_attempts_per_game": 1}, schedule=[],
        )
        first.create_game(
            game_id="game-1", name="bench game", config={},
            execution_status="running", source="benchmark", model_snapshot=[],
            benchmark_run_id="run-1",
        )
        for request_id in ("request-1", "request-2"):
            first.prepare_model_request(
                game_id="game-1", request_id=request_id, actor_seat=1,
                action_position=request_id, request_digest=request_id,
                provider_profile="test", model_id="model-a",
            )

        def start(repository: GameRepository, request_id: str):
            return repository.start_model_attempt(
                game_id="game-1", request_id=request_id,
                execution_generation=1,
            )

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [
                workers.submit(start, first, "request-1"),
                workers.submit(start, second, "request-2"),
            ]
        results = [future.exception() for future in futures]

        assert sum(error is None for error in results) == 1
        assert sum(isinstance(error, ModelAttemptQuotaExceeded) for error in results) == 1
        assert len(first.list_model_attempts("game-1")) == 1
    finally:
        first.close()
        second.close()
