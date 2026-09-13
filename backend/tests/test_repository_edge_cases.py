from __future__ import annotations

import sqlite3

import pytest

from app.persistence.repository import (
    CommitConflict, GameRepository, RepositoryVersionConflict,
)


def _create_game(repository: GameRepository, game_id: str = "game") -> None:
    repository.create_game(
        game_id=game_id, name="game", config={}, execution_status="running",
        source="native", model_snapshot=[], execution_generation=1,
    )


def _commit_args(**updates) -> dict[str, object]:
    values: dict[str, object] = {
        "game_id": "game", "expected_storage_revision": 0,
        "execution_generation": 1, "step_key": "step",
        "input_digest": "input", "result_digest": "result",
        "checkpoint": {"checkpoint_version": 1},
        "domain_events": [], "audience_events": [], "audience_state": {},
    }
    values.update(updates)
    return values


def test_closed_repository_rejects_writes_and_close_is_idempotent(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    repository.close()
    repository.close()
    with pytest.raises(RuntimeError, match="closed"):
        _create_game(repository)


def test_create_game_and_model_reference_inputs_are_validated(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        with pytest.raises(ValueError, match="benchmark_item_index"):
            repository.create_game(
                game_id="bad", name="bad", config={}, execution_status="running",
                source="native", model_snapshot=[], benchmark_item_index=0,
            )
        for value in (None, ""):
            with pytest.raises(ValueError, match="config_id"):
                repository.model_config_references(value)
    finally:
        repository.close()


def test_rename_delete_and_include_deleted_lifecycle(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _create_game(repository)
        with pytest.raises(ValueError, match="blank"):
            repository.rename_game("game", "  ")
        with pytest.raises(KeyError, match="missing"):
            repository.rename_game("missing", "name")
        repository.rename_game("game", " renamed ")
        assert repository.get_game("game")["name"] == "renamed"

        repository.mark_game_deleted("game")
        assert repository.get_game("game") is None
        assert repository.get_game("game", include_deleted=True)["deleted_at"] is not None
        assert repository.list_games() == []
        assert [row["game_id"] for row in repository.list_games(include_deleted=True)] == ["game"]
        with pytest.raises(KeyError, match="game"):
            repository.mark_game_deleted("game")
        with pytest.raises(KeyError, match="game"):
            repository.rename_game("game", "again")
        with pytest.raises(KeyError, match="missing"):
            repository.mark_game_deleted("missing")
    finally:
        repository.close()


@pytest.mark.parametrize("expected", [(), [], ("",), (1,)])
def test_transition_rejects_invalid_expected_states(tmp_path, expected) -> None:
    repository = GameRepository(tmp_path)
    try:
        with pytest.raises(ValueError, match="expected states"):
            repository.transition_execution("game", expected=expected, target="paused")
    finally:
        repository.close()


def test_transition_rejects_blank_target_and_missing_game(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        with pytest.raises(ValueError, match="target"):
            repository.transition_execution("game", expected=("running",), target="")
        with pytest.raises(KeyError, match="missing"):
            repository.transition_execution("missing", expected=("running",), target="paused")
    finally:
        repository.close()


@pytest.mark.parametrize(
    "updates",
    [
        {"active_elapsed_ms": -1, "remaining_window_ms": None, "execution_generation": 1},
        {"active_elapsed_ms": 0, "remaining_window_ms": None, "execution_generation": -1},
        {"active_elapsed_ms": 0, "remaining_window_ms": -1, "execution_generation": 1},
        {"active_elapsed_ms": 0, "remaining_window_ms": True, "execution_generation": 1},
    ],
)
def test_runtime_clock_rejects_invalid_values(tmp_path, updates) -> None:
    repository = GameRepository(tmp_path)
    try:
        with pytest.raises(ValueError):
            repository.save_runtime_clock("game", **updates)
    finally:
        repository.close()


def test_runtime_clock_missing_game_and_empty_startup_interrupt(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        assert repository.get_runtime_clock("missing") is None
        with pytest.raises(KeyError, match="missing"):
            repository.save_runtime_clock(
                "missing", active_elapsed_ms=0, remaining_window_ms=None,
                execution_generation=1,
            )
        assert repository.interrupt_running_games() == []
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"checkpoint": {}}, "checkpoint_version"),
        ({"checkpoint": {"checkpoint_version": 0}}, "checkpoint_version"),
        ({"consumed_model_request_ids": ("",)}, "non-empty strings"),
        ({"consumed_model_request_ids": ("request", "request")}, "unique"),
        ({"derived_jobs": ({"job_key": "job", "job_type": "type", "extra": 1},)}, "unknown"),
        ({"derived_jobs": ({"job_key": "", "job_type": "type"},)}, "key and type"),
        ({"derived_jobs": ({"job_key": "job", "job_type": ""},)}, "key and type"),
        ({"derived_jobs": ({"job_key": "job", "job_type": "type", "run_id": ""},)}, "run_id"),
        ({"audience_events": ({"event_type": "phase"},)}, "event_id"),
        ({"audience_events": ({"event_id": "id"},)}, "event_type"),
        ({"audience_events": ({"event_id": "id", "event_type": "phase", "schema_version": 0},)}, "schema_version"),
        ({"audience_events": ({"event_id": "id", "event_type": "phase", "timestamp": 1},)}, "timestamp"),
    ],
)
def test_commit_validates_checkpoint_events_requests_and_jobs(
    tmp_path, updates, message,
) -> None:
    repository = GameRepository(tmp_path)
    try:
        _create_game(repository)
        with pytest.raises(ValueError, match=message):
            repository.commit_step(**_commit_args(**updates))
        assert repository.list_commits("game") == []
    finally:
        repository.close()


def test_commit_rejects_missing_game_stale_generation_and_unresolved_request(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        with pytest.raises(KeyError, match="missing"):
            repository.commit_step(**_commit_args(game_id="missing"))
        _create_game(repository)
        with pytest.raises(RepositoryVersionConflict, match="generation"):
            repository.commit_step(**_commit_args(execution_generation=2))

        repository.prepare_model_request(
            game_id="game", request_id="request", actor_seat=1,
            action_position="speech", request_digest="digest",
            provider_profile="test", model_id="fake",
        )
        with pytest.raises(CommitConflict, match="not resolved"):
            repository.commit_step(**_commit_args(
                consumed_model_request_ids=("request",),
            ))
        assert repository.get_game("game")["storage_revision"] == 0
    finally:
        repository.close()


def test_commit_fault_boundary_filters_and_empty_reads(tmp_path) -> None:
    faults: list[str] = []
    repository = GameRepository(tmp_path, fault_injector=faults.append)
    try:
        _create_game(repository)
        assert repository.load_checkpoint("game") is None
        assert repository.get_audience_snapshot("game") is None
        assert repository.list_derived_jobs() == []
        assert repository.list_derived_jobs(game_id="game") == []
        assert repository.list_derived_jobs(run_id="missing") == []
        assert repository.list_derived_jobs(game_id="game", run_id="missing") == []
        result = repository.commit_step(**_commit_args())
        assert result["first_seq"] is None
        assert faults == ["inside_transaction_before_commit"]
    finally:
        repository.close()


def test_load_checkpoint_detects_digest_corruption(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _create_game(repository)
        repository.commit_step(**_commit_args())
        with sqlite3.connect(repository.database_path) as connection:
            connection.execute(
                "UPDATE game_checkpoints SET checkpoint_digest='wrong' WHERE game_id='game'"
            )
        with pytest.raises(ValueError, match="digest mismatch"):
            repository.load_checkpoint("game")
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("after_seq", "limit", "through_seq", "message"),
    [
        (-1, 1, None, "after_seq"),
        (True, 1, None, "after_seq"),
        (0, 0, None, "limit"),
        (0, 1001, None, "limit"),
        (0, 1, -1, "through_seq"),
        (0, 1, True, "through_seq"),
    ],
)
def test_audience_event_page_validates_bounds(
    tmp_path, after_seq, limit, through_seq, message,
) -> None:
    repository = GameRepository(tmp_path)
    try:
        with pytest.raises(ValueError, match=message):
            repository.get_audience_events(
                "game", after_seq=after_seq, limit=limit, through_seq=through_seq,
            )
    finally:
        repository.close()


def test_model_request_storage_reports_conflicts_and_missing_rows(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _create_game(repository)
        assert repository.get_model_request("game", "missing") is None
        assert repository.list_model_requests("game") == []
        with pytest.raises(KeyError, match="missing"):
            repository.start_model_attempt(
                game_id="game", request_id="missing", execution_generation=1,
            )
        with pytest.raises(KeyError, match="missing"):
            repository.finish_model_attempt(
                "missing", status="failed", failure_code="missing",
                elapsed_ms=None, usage=None,
            )
        with pytest.raises(KeyError, match="missing"):
            repository.resolve_model_request(
                "game", "missing", status="unknown", normalized_result=None,
            )
        with pytest.raises(KeyError, match="missing"):
            repository.reserve_recovery_retry("game", "missing")

        request = dict(
            game_id="game", request_id="request", actor_seat=1,
            action_position="speech", request_digest="digest",
            provider_profile="test", model_id="fake",
        )
        repository.prepare_model_request(**request)
        repository.prepare_model_request(**request)
        with pytest.raises(CommitConflict, match="conflicting"):
            repository.prepare_model_request(**{**request, "request_digest": "other"})
        rows = repository.list_model_requests("game")
        assert len(rows) == 1
        assert rows[0]["normalized_result"] is None
    finally:
        repository.close()


def test_interrupt_rolls_back_and_propagates_database_failure(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        with sqlite3.connect(repository.database_path) as connection:
            connection.execute("ALTER TABLE games RENAME TO unavailable_games")
        with pytest.raises(sqlite3.OperationalError, match="games"):
            repository.interrupt_running_games()
    finally:
        repository.close()
