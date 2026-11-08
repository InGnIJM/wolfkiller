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


def test_model_config_references_include_run_level_vote_model(tmp_path) -> None:
    """评测 run 级冻结的投票模型也要进删除保护，不能只查每座位条目。"""
    repository = GameRepository(tmp_path)
    try:
        repository.create_benchmark_run(
            run_id="run-vote", client_request_id="client-vote",
            request_digest="ref", name="vote", mode="mixed_arena",
            config={"vote_model_config_id": "vote-cfg"},
            schedule=_schedule(),
        )
        assert repository.model_config_references("vote-cfg")["benchmark_run_ids"] == [
            "run-vote",
        ]
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


def test_older_benchmark_reports_are_pruned_per_metric_version(tmp_path) -> None:
    """报告缓存必须有界：每个 (run_id, metric_version) 只留最新两份。

    每次事实变化（迟到的 usage、新的 attempt、run 状态翻转）都会算出一个新的
    `input_digest` 并插入一整份报告，而此前没有任何清理 —— 单个 run 曾累积 3404 行。
    读者只用最新一行，旧行是死重量；留两份是为了还能对照上一次计算。
    """
    repository = GameRepository(tmp_path)
    try:
        repository.create_benchmark_run(
            run_id="run-keep", client_request_id="client-keep", request_digest="request",
            name="A/B", mode="paired_regression", config={}, schedule=[],
        )
        for index in range(4):
            repository.save_benchmark_report(
                "run-keep", metric_version="v1", input_digest=f"facts-{index}",
                report={"winner": "candidate", "index": index},
            )
        repository.save_benchmark_report(
            "run-keep", metric_version="v2", input_digest="other",
            report={"winner": "baseline", "metric_version": "v2"},
        )

        # The two oldest v1 rows are gone; the newest two survive.
        for stale in ("facts-0", "facts-1"):
            assert repository.get_benchmark_report(
                "run-keep", metric_version="v1", input_digest=stale,
            ) is None
        assert repository.get_benchmark_report(
            "run-keep", metric_version="v1", input_digest="facts-2",
        )["report"]["index"] == 2
        assert repository.get_latest_benchmark_report("run-keep")["report"] == {
            "winner": "baseline", "metric_version": "v2",
        }
        # A different metric version is not collateral damage.
        assert repository.get_benchmark_report(
            "run-keep", metric_version="v2", input_digest="other",
        )["report"]["metric_version"] == "v2"
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


def _record_attempt(
    repository: GameRepository, *, request_id: str, model_id: str,
    provider_profile: str, elapsed_ms: int | None, status: str = "resolved",
) -> None:
    repository.prepare_model_request(
        game_id="game-1", request_id=request_id, actor_seat=1,
        action_position=request_id, request_digest=request_id,
        provider_profile=provider_profile, model_id=model_id,
    )
    attempt = repository.start_model_attempt(
        game_id="game-1", request_id=request_id, execution_generation=1,
    )
    repository.finish_model_attempt(
        attempt["attempt_id"], status=status, failure_code=None,
        elapsed_ms=elapsed_ms, usage=None,
    )


def test_model_latency_stats_summarize_resolved_attempts(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_game(
            game_id="game-1", name="latency", config={},
            execution_status="running", source="native", model_snapshot=[],
        )
        for index, elapsed in enumerate([4000, 1000, 3000, 2000]):
            _record_attempt(
                repository, request_id=f"fast-{index}", model_id="model-fast",
                provider_profile="profile-a", elapsed_ms=elapsed,
            )
        # A lone call is not a measurement, and a failed or untimed attempt
        # never carried a latency worth averaging.
        _record_attempt(
            repository, request_id="lonely", model_id="model-slow",
            provider_profile="profile-a", elapsed_ms=9000,
        )
        _record_attempt(
            repository, request_id="failed", model_id="model-fast",
            provider_profile="profile-a", elapsed_ms=60000, status="failed",
        )
        _record_attempt(
            repository, request_id="untimed", model_id="model-fast",
            provider_profile="profile-a", elapsed_ms=None,
        )

        stats = repository.model_latency_stats(min_samples=2)

        assert stats == {
            ("model-fast", "profile-a"): {
                "samples": 4, "avg_ms": 2500, "p50_ms": 3000, "p90_ms": 4000,
            },
        }
    finally:
        repository.close()


def test_model_latency_stats_window_and_sample_floor(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_game(
            game_id="game-1", name="latency", config={},
            execution_status="running", source="native", model_snapshot=[],
        )
        for index in range(3):
            _record_attempt(
                repository, request_id=f"call-{index}", model_id="model-a",
                provider_profile="profile-a", elapsed_ms=1000 * (index + 1),
            )

        assert ("model-a", "profile-a") in repository.model_latency_stats(min_samples=3)
        assert repository.model_latency_stats(min_samples=4) == {}
        assert repository.model_latency_stats(
            since="2000-01-01T00:00:00+00:00", min_samples=1,
        ) == {("model-a", "profile-a"): {
            "samples": 3, "avg_ms": 2000, "p50_ms": 2000, "p90_ms": 3000,
        }}
        assert repository.model_latency_stats(
            since="2999-01-01T00:00:00+00:00", min_samples=1,
        ) == {}
    finally:
        repository.close()


def test_list_benchmark_games_filters_status_and_skips_deleted(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_benchmark_run(
            run_id="run-1", client_request_id="client-1", request_digest="request",
            name="A/B", mode="paired_regression", config={}, schedule=[],
        )
        for game_id, status in (
            ("running-game", "running"),
            ("paused-game", "paused"),
            ("done-game", "completed"),
            ("deleted-game", "running"),
        ):
            repository.create_game(
                game_id=game_id, name=game_id, config={},
                execution_status=status, source="benchmark", model_snapshot=[],
                benchmark_run_id="run-1",
            )
        repository.create_game(
            game_id="foreign-game", name="foreign", config={},
            execution_status="running", source="benchmark", model_snapshot=[],
            benchmark_run_id="run-2",
        )
        with repository._connect() as connection:
            connection.execute(
                "UPDATE games SET deleted_at=? WHERE game_id=?",
                ("2026-01-01T00:00:00+00:00", "deleted-game"),
            )

        assert [
            game["game_id"]
            for game in repository.list_benchmark_games("run-1", ("running",))
        ] == ["running-game"]
        assert {
            game["game_id"]
            for game in repository.list_benchmark_games(
                "run-1", ("running", "paused"),
            )
        } == {"running-game", "paused-game"}
        assert repository.list_benchmark_games("run-1", ()) == []
        assert repository.list_benchmark_games("run-missing", ("running",)) == []
    finally:
        repository.close()
