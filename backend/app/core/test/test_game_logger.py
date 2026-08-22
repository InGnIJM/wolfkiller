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


def test_vote_window_logs_open_and_complete_terminal_summary(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_vote_window_opened(
        "g", 3, window_id="g:3:vote_casting:2:cast_vote:v2",
        vote_round=2, eligible_voters=[1, 2], timeout_seconds=500.0,
    )
    opened = read_last(tmp_path / "games" / "g" / "game.log")
    logger.log_vote_window_closed(
        "g", 3, window_id="g:3:vote_casting:2:cast_vote:v2",
        vote_round=2, accepted_votes=1, voluntary_abstains=0,
        technical_abstains=1, missing_voters=0,
    )
    closed = read_last(tmp_path / "games" / "g" / "game.log")

    assert opened["operation"] == "vote_window_opened"
    assert opened["data"]["eligible_voters"] == [1, 2]
    assert closed["operation"] == "vote_window_closed"
    assert closed["data"]["missing_voters"] == 0
    assert closed["data"]["technical_abstains"] == 1


def test_vote_receipt_log_is_correlatable_without_private_reasoning(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_vote_receipt(
        "g", 3, 2, window_id="g:3:vote_casting:2:cast_vote:v2",
        action_key="g:3:vote_casting:2:cast_vote:v2:2",
        vote_round=2, status="technical_abstain", target=None,
        command_digest="a" * 64, replayed=False,
        failure_code="request_timeout", timeout_type="provider_timeout",
    )

    record = read_last(tmp_path / "games" / "g" / "game.log")
    assert record["operation"] == "vote_receipt"
    assert record["data"]["action_key"].endswith(":2")
    assert record["data"]["failure_code"] == "request_timeout"
    assert "reasoning" not in record["data"]
    assert "response" not in record["data"]


def test_remaining_game_logger_operations_are_json_serializable(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))
    game_log = tmp_path / "games" / "g" / "game.log"

    logger.log_phase_change("g", "night", 1)
    logger.log_deaths("g", 1, [{"player_seat": 2}])
    logger.log_audience_action(
        "g", 1, "night", "CHECK", {"nested": (1, {"ok": True})},
    )
    logger.log_speech("g", 1, "speech", 1, "hello")
    logger.log_vote("g", 1, 1, 2, vote_round=1)
    logger.log_vote_receipt(
        "g", 1, 1, window_id="w", action_key="w:1", vote_round=1,
        status="accepted_vote", target=2, command_digest="a" * 64,
        replayed=False,
    )
    logger.log_vote_technical_abstain(
        "g", 1, 2, failure_code="request_timeout", vote_round=1,
    )
    logger.log_vote_result("g", 1, 2, {2: 1})
    logger.log_role_init("g", {1: "villager"})
    logger.log_game_over("g", 1, "good", "all_wolves_dead")

    records = [json.loads(line) for line in game_log.read_text(encoding="utf-8").splitlines()]
    assert [record["operation"] for record in records] == [
        "phase_change", "night_deaths", "audience_action", "speak", "vote",
        "vote_receipt", "vote_technical_abstain", "vote_result", "role_init",
        "game_over",
    ]
    assert records[2]["data"]["payload"] == {"nested": [1, {"ok": True}]}


def test_conversation_logger_preserves_or_generates_timestamp(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))
    first = {"content": "one", "timestamp": "fixed"}
    second = {"content": "two"}

    logger.log_conversation("g", first)
    logger.log_conversation("g", second)

    records = [
        json.loads(line)
        for line in (tmp_path / "games" / "g" / "conversation.log")
        .read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["timestamp"] == "fixed"
    assert records[1]["timestamp"]
