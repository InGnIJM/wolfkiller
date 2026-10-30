import json

from app.core.game_logger import GameLogger


def _read_last_line(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()[-1]


def test_log_vote_telemetry_keeps_only_operational_metadata(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_vote_telemetry(
        "g1", 2, 7, transport="json", attempt=2, prompt_chars=1234,
        elapsed_ms=456, retried=True, parse_result="accepted",
    )

    record = json.loads(_read_last_line(tmp_path / "games" / "g1" / "game.log"))
    assert record["operation"] == "vote_telemetry"
    assert record["data"] == {
        "transport": "json", "attempt": 2, "prompt_chars": 1234,
        "elapsed_ms": 456, "retried": True, "parse_result": "accepted",
    }


def test_log_sheriff_vote_technical_abstain_names_the_engine_failure(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_sheriff_vote_technical_abstain(
        "g1", 1, 7, failure_code="sheriff_vote_unusable",
    )

    record = json.loads(_read_last_line(tmp_path / "games" / "g1" / "game.log"))
    assert record["operation"] == "sheriff_vote_technical_abstain"
    assert record["phase"] == "sheriff_election"
    assert record["seat"] == 7
    assert record["data"] == {"failure_code": "sheriff_vote_unusable"}


def test_log_narration_writes_structured_record(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))
    logger.log_narration("g1", 1, "night", "天亮了", "昨晚是平安夜，没有人死亡。")
    record = json.loads(_read_last_line(tmp_path / "games" / "g1" / "game.log"))
    assert record["operation"] == "narration" and record["phase"] == "night"
    assert record["data"] == {"title": "天亮了", "text": "昨晚是平安夜，没有人死亡。"}



def test_model_error_keeps_structured_sanitized_cause_chain(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))
    causes = [{"exception_type": "TimeoutError", "message": "request timed out"}]
    logger.log_model_error(
        "g", 1, "night", 1, contract_id="vote", schedule_point="night_action",
        attempt=1, provider_profile="custom-openai", model_id="fake",
        failure_code="request_timeout", exception_type="RuntimeError",
        message="provider failed", cause_chain=causes,
    )
    record = json.loads(_read_last_line(tmp_path / "games" / "g" / "game.log"))
    assert record["data"]["cause_chain"] == causes


def test_log_deaths_can_record_a_non_dawn_phase(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))

    logger.log_deaths(
        "g1", 2, [{"player_seat": 3, "cause": "hunter_shot"}],
        phase="vote_resolution",
    )

    record = json.loads(
        (tmp_path / "games" / "g1" / "game.log").read_text(encoding="utf-8").strip()
    )
    assert record["operation"] == "night_deaths"
    assert record["phase"] == "vote_resolution"


def test_close_is_idempotent_and_stops_writes(tmp_path):
    import shutil

    logger = GameLogger(data_dir=str(tmp_path))
    logger.log_phase_change("g1", "night", 1)
    path = tmp_path / "games" / "g1" / "game.log"
    before = path.read_text(encoding="utf-8")

    logger.close()
    logger.close()
    logger.log_phase_change("g1", "dawn", 1)
    assert path.read_text(encoding="utf-8") == before

    shutil.rmtree(tmp_path / "games" / "g1")
    logger.log_phase_change("g1", "speech", 1)
    assert not (tmp_path / "games" / "g1").exists()


def test_an_llm_call_observer_failure_never_breaks_the_log(tmp_path) -> None:
    from app.core.game_logger import GameLogger

    def boom(_record) -> None:
        raise RuntimeError("observer down")

    logger = GameLogger(data_dir=str(tmp_path), on_llm_call=boom)
    logger.log_llm_call(
        "game", 1, "night", 1, call_kind="night", parse_result="ok",
    )
    assert (tmp_path / "games" / "game" / "llm_calls.log").exists()
