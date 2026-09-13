from __future__ import annotations

import pytest

from app.persistence.repository import GameRepository, InvalidExecutionTransition


def _game(repository: GameRepository, game_id: str = "game-1", status: str = "running") -> None:
    repository.create_game(
        game_id=game_id, name=game_id, config={}, execution_status=status,
        source="native", model_snapshot=[], execution_generation=1,
    )


def test_pause_resume_and_recover_increment_generation_only_when_execution_restarts(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        paused = repository.transition_execution(
            "game-1", expected=("running",), target="paused",
        )
        assert paused["execution_status"] == "paused"
        assert paused["execution_generation"] == 1
        assert repository.transition_execution(
            "game-1", expected=("paused",), target="paused",
        )["execution_generation"] == 1

        resumed = repository.transition_execution(
            "game-1", expected=("paused",), target="running", increment_generation=True,
        )
        assert resumed["execution_generation"] == 2
        repository.transition_execution("game-1", expected=("running",), target="interrupted")
        recovered = repository.transition_execution(
            "game-1", expected=("interrupted",), target="running", increment_generation=True,
        )
        assert recovered["execution_generation"] == 3
        assert recovered["interruption_count"] == 1

        with pytest.raises(InvalidExecutionTransition, match="running"):
            repository.transition_execution(
                "game-1", expected=("paused",), target="completed",
            )
    finally:
        repository.close()


def test_runtime_clock_round_trip_is_independent_of_business_revision(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        repository.save_runtime_clock(
            "game-1", active_elapsed_ms=1250, remaining_window_ms=8900,
            execution_generation=1,
        )
        assert repository.get_runtime_clock("game-1") == {
            "game_id": "game-1", "active_elapsed_ms": 1250,
            "remaining_window_ms": 8900, "execution_generation": 1,
            "saved_at": repository.get_runtime_clock("game-1")["saved_at"],
        }
        assert repository.get_game("game-1")["storage_revision"] == 0
        with pytest.raises(ValueError, match="stale"):
            repository.save_runtime_clock(
                "game-1", active_elapsed_ms=1300, remaining_window_ms=8800,
                execution_generation=2,
            )
    finally:
        repository.close()


def test_startup_interrupt_marks_in_flight_attempt_unknown_and_preserves_paused(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository, "running")
        _game(repository, "paused", status="paused")
        repository.prepare_model_request(
            game_id="running", request_id="req", actor_seat=1,
            action_position="round:1:speech:1", request_digest="d",
            provider_profile="test", model_id="fake",
        )
        attempt = repository.start_model_attempt(
            game_id="running", request_id="req", execution_generation=1,
        )
        changed = repository.interrupt_running_games()
        assert changed == ["running"]
        assert repository.get_game("running")["execution_status"] == "interrupted"
        assert repository.get_game("paused")["execution_status"] == "paused"
        assert repository.get_model_request("running", "req")["status"] == "unknown"
        row = repository.list_model_attempts("running")[0]
        assert row["attempt_id"] == attempt["attempt_id"]
        assert row["status"] == "unknown"
        assert row["failure_code"] == "process_interrupted"
        assert row["usage_known"] is False
    finally:
        repository.close()


def test_unknown_request_allows_one_persisted_recovery_retry(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        repository.prepare_model_request(
            game_id="game-1", request_id="req", actor_seat=1,
            action_position="position", request_digest="digest",
            provider_profile="test", model_id="fake",
        )
        attempt = repository.start_model_attempt(
            game_id="game-1", request_id="req", execution_generation=1,
        )
        repository.finish_model_attempt(
            attempt["attempt_id"], status="unknown", failure_code="process_interrupted",
            elapsed_ms=None, usage=None,
        )
        repository.resolve_model_request(
            "game-1", "req", status="unknown", normalized_result=None,
        )
        assert repository.reserve_recovery_retry("game-1", "req") is True
        assert repository.reserve_recovery_retry("game-1", "req") is False
        assert repository.get_model_request("game-1", "req")["recovery_retry_count"] == 1
    finally:
        repository.close()
