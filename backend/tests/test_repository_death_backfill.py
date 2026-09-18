from __future__ import annotations

import json
import sqlite3

from app.persistence.repository import (
    GameRepository,
    _backfill_audience_death_player_seat,
)


def _memory_audience_events() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE audience_events ("
        "game_id TEXT NOT NULL, seq INTEGER NOT NULL, event_type TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, PRIMARY KEY (game_id, seq))"
    )
    return connection


def test_backfill_promotes_seat_and_leaves_other_rows_untouched() -> None:
    connection = _memory_audience_events()
    connection.executemany(
        "INSERT INTO audience_events(game_id, seq, event_type, payload_json) "
        "VALUES (?, ?, ?, ?)",
        [
            ("g", 1, "death", json.dumps({"seat": 5, "cause": "wolf_kill", "round_number": 3})),
            ("g", 2, "death", json.dumps({"player_seat": 7, "cause": "exile", "round_number": 1})),
            ("g", 3, "death", "{not json"),
            ("g", 4, "death", json.dumps({"cause": "poison", "round_number": 2})),
            ("g", 5, "speech", json.dumps({"player_seat": 1, "text": "hi"})),
        ],
    )

    _backfill_audience_death_player_seat(connection)

    rows = {
        row["seq"]: row["payload_json"]
        for row in connection.execute("SELECT seq, payload_json FROM audience_events")
    }
    assert json.loads(rows[1]) == {"player_seat": 5, "cause": "wolf_kill", "round_number": 3}
    assert json.loads(rows[2]) == {"player_seat": 7, "cause": "exile", "round_number": 1}
    assert rows[3] == "{not json"
    assert json.loads(rows[4]) == {"cause": "poison", "round_number": 2}
    assert json.loads(rows[5]) == {"player_seat": 1, "text": "hi"}


def test_backfill_is_a_noop_without_the_audience_events_table() -> None:
    connection = sqlite3.connect(":memory:")

    _backfill_audience_death_player_seat(connection)

    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall() == []


def test_repository_startup_runs_v3_death_backfill(tmp_path) -> None:
    db_path = tmp_path / "wolfkiller.sqlite3"
    GameRepository(tmp_path).close()

    connection = sqlite3.connect(db_path)
    connection.execute(
        "INSERT INTO audience_events("
        "game_id, seq, event_id, step_key, event_type, payload_json, "
        "schema_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "game-1", 1, "evt", "step", "death",
            json.dumps({"seat": 9, "cause": "wolf_kill", "round_number": 2}), 1, "now",
        ),
    )
    connection.execute("DELETE FROM schema_migrations WHERE version = 3")
    connection.commit()
    connection.close()

    repository = GameRepository(tmp_path)
    try:
        assert repository.schema_version() == 3
    finally:
        repository.close()

    connection = sqlite3.connect(db_path)
    try:
        raw = connection.execute(
            "SELECT payload_json FROM audience_events WHERE game_id = 'game-1' AND seq = 1"
        ).fetchone()[0]
    finally:
        connection.close()
    assert json.loads(raw) == {"player_seat": 9, "cause": "wolf_kill", "round_number": 2}
