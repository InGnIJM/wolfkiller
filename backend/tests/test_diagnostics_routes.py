"""旧的 `/logs` 回退路径把承载不了的记录静默丢掉，这里让它在服务端可观测。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import replay_diagnostics
from app.api.routes import diagnostics_routes


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(diagnostics_routes.router)
    return TestClient(app)


def test_replay_drops_reports_every_code_with_its_count() -> None:
    replay_diagnostics.ROWS.clear()
    replay_diagnostics.drop("night_deaths", "undeclared_cause", "love_death")
    replay_diagnostics.drop("night_deaths", "undeclared_cause", "love_death")
    replay_diagnostics.drop("game_over", "undeclared_reason", "third_party_wins")

    response = _client().get("/api/diagnostics/replay-drops")

    assert response.status_code == 200
    assert response.json() == {"drops": {
        "game_over:undeclared_reason": 1,
        "night_deaths:undeclared_cause": 2,
    }}


def test_replay_drops_is_empty_before_anything_is_dropped() -> None:
    replay_diagnostics.ROWS.clear()

    assert _client().get("/api/diagnostics/replay-drops").json() == {"drops": {}}


def test_replay_drops_response_carries_no_game_payload() -> None:
    """响应只有 code 与次数：被丢弃的取值、座位与文本都不出现。

    这是「丢弃不进公开 DTO」的另一半 —— 诊断信息也不该把对局内容带出去。
    """
    replay_diagnostics.ROWS.clear()
    replay_diagnostics.drop("night_deaths", "undeclared_cause", "love_death")
    replay_diagnostics.drop("audience_action", "unsupported_event_type", "SECRET_EVENT")

    body = _client().get("/api/diagnostics/replay-drops").text

    for leaked in ("love_death", "SECRET_EVENT", "seat", "game_id"):
        assert leaked not in body
