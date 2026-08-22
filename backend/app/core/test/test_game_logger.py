import json

from app.core.game_logger import GameLogger


def read_last(path):
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


def test_vote_telemetry_persists_failure_classification_without_private_text(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_vote_telemetry(
        "g", 3, 2, transport="json", attempt=2, prompt_chars=4282,
        elapsed_ms=90000, retried=True, parse_result="timeout_fallback",
        timeout_type="provider_timeout", failure_code="request_timeout",
        window_id="3:vote_casting:2:2:exile_vote",
    )

    record = read_last(tmp_path / "games" / "g" / "game.log")
    assert record["data"]["timeout_type"] == "provider_timeout"
    assert record["data"]["failure_code"] == "request_timeout"
    assert record["data"]["window_id"] == "3:vote_casting:2:2:exile_vote"
    assert "response" not in record["data"]
    assert "reasoning" not in record["data"]


def test_vote_queue_telemetry_persists_scheduler_wait(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_vote_queue_telemetry(
        "g", 3, 9, queue_wait_ms=55547, worker_limit=5, vote_round=2,
    )

    record = read_last(tmp_path / "games" / "g" / "game.log")
    assert record["operation"] == "vote_queue_telemetry"
    assert record["data"] == {
        "queue_wait_ms": 55547, "worker_limit": 5, "vote_round": 2,
    }


def test_vote_phase_timeout_persists_completed_and_missing_seats(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_vote_phase_timeout(
        "g", 3, timeout_seconds=500.0,
        completed_seats=[1, 3, 6], missing_seats=[2, 7], vote_round=2,
    )

    record = read_last(tmp_path / "games" / "g" / "game.log")
    assert record["operation"] == "vote_phase_timeout"
    assert record["data"] == {
        "timeout_seconds": 500.0,
        "completed_seats": [1, 3, 6],
        "missing_seats": [2, 7],
        "vote_round": 2,
    }


def test_technical_abstain_persists_stable_failure_code(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_vote_technical_abstain(
        "g", 3, 7, failure_code="request_timeout",
        timeout_type="provider_timeout", window_id="3:vote_casting:2:7:exile_vote",
    )

    record = read_last(tmp_path / "games" / "g" / "game.log")
    assert record["operation"] == "vote_technical_abstain"
    assert record["seat"] == 7
    assert record["data"] == {
        "failure_code": "request_timeout", "timeout_type": "provider_timeout",
        "window_id": "3:vote_casting:2:7:exile_vote",
    }
