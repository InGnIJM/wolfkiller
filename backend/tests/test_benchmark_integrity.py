"""Repository failures must not leave partial benchmark ownership or progress."""

import sqlite3

import pytest

from app.persistence.repository import (
    CommitConflict, GameReferencedByBenchmark, GameRepository,
)


@pytest.fixture
def repository(tmp_path):
    value = GameRepository(tmp_path)
    try:
        yield value
    finally:
        value.close()


def _create_run(repository, run_id="run"):
    return repository.create_benchmark_run(
        run_id=run_id, client_request_id=run_id, request_digest=run_id,
        name=run_id, mode="mixed_arena", config={},
        schedule=[{
            "scenario_id": "tiny", "pair_id": None, "block_index": 0,
            "assignment": {"variant": "mixed"},
        }],
    )


def _install_trigger(repository, sql):
    connection = sqlite3.connect(repository.database_path)
    try:
        connection.execute(sql)
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"extra": True}, "fields do not match"),
        ({"scenario_id": ""}, "scenario_id"),
        ({"pair_id": 1}, "pair_id"),
        ({"block_index": -1}, "block_index"),
        ({"assignment": []}, "assignment"),
    ],
)
def test_invalid_schedule_cannot_persist_a_partial_plan(repository, changes, match):
    row = {"scenario_id": "tiny", "pair_id": None, "block_index": 0, "assignment": {}}
    with pytest.raises(ValueError, match=match):
        repository.create_benchmark_run(
            run_id="run", client_request_id="run", request_digest="run", name="run",
            mode="mixed_arena", config={}, schedule=[row, {**row, **changes}],
        )
    assert repository.list_benchmark_runs() == []
    assert repository.list_benchmark_items("run") == []


def test_unknown_benchmark_mode_is_rejected_before_persistence(repository):
    with pytest.raises(ValueError, match="unsupported benchmark mode"):
        repository.create_benchmark_run(
            run_id="run", client_request_id="run", request_digest="run", name="run",
            mode="unknown", config={}, schedule=[],
        )
    assert repository.list_benchmark_runs() == []


def test_benchmark_game_cannot_be_deleted_independently(repository):
    _create_run(repository)
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="running",
        source="benchmark", model_snapshot=[], benchmark_run_id="run",
    )
    with pytest.raises(GameReferencedByBenchmark, match="run"):
        repository.mark_game_deleted("game")
    assert repository.get_game("game")["deleted_at"] is None


def test_release_benchmark_game_unbinds_item_and_soft_deletes(repository):
    _create_run(repository)
    repository.transition_benchmark("run", expected=("draft",), target="running")
    repository.claim_next_benchmark_item("run")
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="failed",
        source="benchmark", model_snapshot=[], benchmark_run_id="run",
        benchmark_item_index=0,
    )
    repository.create_folder("箱")
    folder = repository.list_folders()[0]
    repository.set_game_folder("game", folder["folder_id"])
    repository.release_benchmark_game("run", "game")
    assert repository.get_game("game") is None
    deleted = repository.get_game("game", include_deleted=True)
    assert deleted["deleted_at"] is not None
    assert deleted["benchmark_run_id"] is None
    assert repository.get_benchmark_item("run", 0)["game_id"] is None
    assert repository.get_benchmark_item("run", 0)["terminal_reason"] == "game_deleted"
    assert repository.get_game_folder("game") is None
    with pytest.raises(KeyError):
        repository.release_benchmark_game("run", "game")
    repository.create_game(
        game_id="other", name="other", config={}, execution_status="failed",
        source="native", model_snapshot=[],
    )
    with pytest.raises(GameReferencedByBenchmark):
        repository.release_benchmark_game("run", "other")
    with pytest.raises(KeyError):
        repository.delete_benchmark_run("missing")
    repository.delete_benchmark_reports("run")
    repository.delete_benchmark_run("run")
    assert repository.get_benchmark_run("run") is None


def test_failed_item_binding_rolls_back_the_new_game(repository):
    _create_run(repository)
    repository.transition_benchmark("run", expected=("draft",), target="running")
    repository.claim_next_benchmark_item("run")
    # Simulate a storage guard rejecting the ownership update after game INSERT.
    _install_trigger(repository, """
        CREATE TRIGGER reject_binding BEFORE UPDATE OF game_id ON benchmark_items
        BEGIN SELECT RAISE(IGNORE); END
    """)
    with pytest.raises(CommitConflict, match="cannot bind game"):
        repository.create_game(
            game_id="game", name="game", config={}, execution_status="running",
            source="benchmark", model_snapshot=[], benchmark_run_id="run",
            benchmark_item_index=0,
        )
    assert repository.get_game("game") is None
    assert repository.get_benchmark_item("run", 0)["game_id"] is None


def test_startup_interrupt_failure_rolls_back_all_runs(repository):
    for run_id in ("first", "last"):
        _create_run(repository, run_id)
        repository.transition_benchmark(run_id, expected=("draft",), target="running")
    _install_trigger(repository, """
        CREATE TRIGGER reject_last_interrupt BEFORE UPDATE OF status ON benchmark_runs
        WHEN NEW.run_id = 'last' AND NEW.status = 'interrupted'
        BEGIN SELECT RAISE(ABORT, 'interruption storage failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="storage failure"):
        repository.interrupt_running_benchmarks()
    assert [row["status"] for row in repository.list_benchmark_runs()] == ["running", "running"]


def test_missing_run_commands_fail_and_replayed_transition_is_idempotent(repository):
    with pytest.raises(KeyError, match="missing"):
        repository.transition_benchmark("missing", expected=("draft",), target="running")
    with pytest.raises(KeyError, match="missing"):
        repository.claim_next_benchmark_item("missing")
    _create_run(repository)
    started = repository.transition_benchmark("run", expected=("draft",), target="running")
    assert repository.transition_benchmark("run", expected=("draft",), target="running") == started
    assert repository.get_benchmark_item("run", 0)["status"] == "pending"


def test_failed_claim_keeps_item_available_for_retry(repository):
    _create_run(repository)
    repository.transition_benchmark("run", expected=("draft",), target="running")
    _install_trigger(repository, """
        CREATE TRIGGER reject_claim AFTER UPDATE OF status ON benchmark_items
        WHEN NEW.status = 'running'
        BEGIN SELECT RAISE(ABORT, 'claim storage failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="storage failure"):
        repository.claim_next_benchmark_item("run")
    assert repository.get_benchmark_item("run", 0)["status"] == "pending"
    _install_trigger(repository, "DROP TRIGGER reject_claim")
    assert repository.claim_next_benchmark_item("run")["item_index"] == 0


def test_terminal_item_cannot_be_rebound_or_overwritten(repository):
    _create_run(repository)
    repository.transition_benchmark("run", expected=("draft",), target="running")
    repository.claim_next_benchmark_item("run")
    with pytest.raises(ValueError, match="terminal status"):
        repository.finish_benchmark_item("run", 0, status="running", terminal_reason=None)
    repository.finish_benchmark_item("run", 0, status="failed", terminal_reason="original")
    finished = repository.get_benchmark_item("run", 0)
    with pytest.raises(CommitConflict, match="cannot attach game"):
        repository.attach_benchmark_game("run", 0, "replacement")
    with pytest.raises(CommitConflict, match="not running"):
        repository.finish_benchmark_item("run", 0, status="completed", terminal_reason=None)
    assert repository.get_benchmark_item("run", 0) == finished
