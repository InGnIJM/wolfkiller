from __future__ import annotations

import asyncio
import json
import sqlite3
import threading

import pytest

from app.persistence.repository import (
    CommitConflict,
    GameRepository,
    RepositoryVersionConflict,
)


def _game(repository: GameRepository, game_id: str = "game-1") -> None:
    repository.create_game(
        game_id=game_id,
        name="测试对局",
        config={"role_counts": {"wolf-killer-werewolf": 1, "wolf-killer-villager": 3}},
        execution_status="running",
        source="native",
        model_snapshot=[{"config_id": None, "count": 4, "seats": [1, 2, 3, 4]}],
    )


def test_repository_initializes_durable_schema_and_pragmas(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        assert repository.database_path == tmp_path / "wolfkiller.sqlite3"
        assert repository.schema_version() == 5
        pragmas = repository.pragmas()
        assert pragmas == {
            "journal_mode": "wal",
            "synchronous": 2,
            "foreign_keys": 1,
            "busy_timeout": 5000,
        }
        tables = repository.table_names()
        assert {
            "games", "game_checkpoints", "game_runtime_clocks", "game_commits",
            "domain_events", "audience_events", "audience_snapshots",
            "model_requests", "model_attempts", "benchmark_runs",
            "benchmark_items", "benchmark_reports", "derived_jobs",
            "game_folders", "game_folder_items",
        } <= tables
    finally:
        repository.close()


def test_create_game_is_readable_and_uses_full_metadata(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        record = repository.get_game("game-1")
        assert record is not None
        assert record["game_id"] == "game-1"
        assert record["execution_status"] == "running"
        assert record["deleted_at"] is None
        assert record["config"]["role_counts"]["wolf-killer-werewolf"] == 1
        assert record["model_snapshot"][0]["seats"] == [1, 2, 3, 4]
        assert repository.list_games()[0]["game_id"] == "game-1"
    finally:
        repository.close()


def test_commit_step_atomically_writes_checkpoint_receipt_and_events(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        result = repository.commit_step(
            game_id="game-1",
            expected_storage_revision=0,
            execution_generation=1,
            step_key="round:1:night:witch:seat:4",
            input_digest="input-a",
            result_digest="result-a",
            checkpoint={"checkpoint_version": 1, "state": {"phase": "night"}},
            domain_events=[{
                "event_id": "domain-1", "event_type": "WITCH_SAVE",
                "payload": {"target_seat": 2}, "schema_version": 1,
            }],
            audience_events=[{
                "event_id": "public-1", "event_type": "night_action",
                "payload": {"action_type": "witch_save", "target_seat": 2},
                "schema_version": 1,
            }],
            audience_state={"game_id": "game-1", "phase": "night"},
        )
        assert result == {"storage_revision": 1, "first_seq": 1, "last_seq": 1, "replayed": False}
        checkpoint = repository.load_checkpoint("game-1")
        assert checkpoint is not None
        assert checkpoint["storage_revision"] == 1
        assert checkpoint["checkpoint"]["state"]["phase"] == "night"
        assert repository.get_audience_snapshot("game-1") == {
            "game_id": "game-1",
            "schema_version": 1,
            "projection_version": 1,
            "last_seq": 1,
            "state": {"game_id": "game-1", "phase": "night"},
        }
        page = repository.get_audience_events("game-1", after_seq=0, limit=10)
        assert page["next_seq"] == 1
        assert page["high_watermark"] == 1
        assert page["has_more"] is False
        assert page["events"][0]["event_id"] == "public-1"
    finally:
        repository.close()


def test_commit_step_is_idempotent_and_rejects_conflicting_replay(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        values = dict(
            game_id="game-1",
            expected_storage_revision=0,
            execution_generation=1,
            step_key="step-1",
            input_digest="same-input",
            result_digest="same-result",
            checkpoint={"checkpoint_version": 1, "state": {}},
            domain_events=[],
            audience_events=[],
            audience_state={"game_id": "game-1"},
        )
        first = repository.commit_step(**values)
        second = repository.commit_step(**values)
        assert first["storage_revision"] == second["storage_revision"] == 1
        assert second["replayed"] is True
        assert len(repository.list_commits("game-1")) == 1

        with pytest.raises(CommitConflict, match="step_key"):
            repository.commit_step(**{**values, "input_digest": "different"})
        with pytest.raises(RepositoryVersionConflict, match="storage revision"):
            repository.commit_step(**{**values, "step_key": "step-2", "expected_storage_revision": 0})
    finally:
        repository.close()


def test_commit_step_rolls_back_every_write_when_any_event_is_invalid(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        with pytest.raises(sqlite3.IntegrityError):
            repository.commit_step(
                game_id="game-1",
                expected_storage_revision=0,
                execution_generation=1,
                step_key="step-1",
                input_digest="input",
                result_digest="result",
                checkpoint={"checkpoint_version": 1, "state": {}},
                domain_events=[],
                audience_events=[
                    {"event_id": "same", "event_type": "phase", "payload": {}, "schema_version": 1},
                    {"event_id": "same", "event_type": "phase", "payload": {}, "schema_version": 1},
                ],
                audience_state={"game_id": "game-1"},
            )
        assert repository.load_checkpoint("game-1") is None
        assert repository.list_commits("game-1") == []
        assert repository.get_audience_events("game-1", after_seq=0, limit=10)["events"] == []
        assert repository.get_game("game-1")["storage_revision"] == 0
    finally:
        repository.close()


def test_event_pagination_has_a_stable_through_seq(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        for index in range(1, 4):
            repository.commit_step(
                game_id="game-1", expected_storage_revision=index - 1,
                execution_generation=1, step_key=f"step-{index}",
                input_digest=f"i-{index}", result_digest=f"r-{index}",
                checkpoint={"checkpoint_version": 1, "state": {"index": index}},
                domain_events=[],
                audience_events=[{
                    "event_id": f"event-{index}", "event_type": "phase",
                    "payload": {"index": index}, "schema_version": 1,
                }],
                audience_state={"index": index},
            )
        page = repository.get_audience_events(
            "game-1", after_seq=0, limit=1, through_seq=2,
        )
        assert [event["seq"] for event in page["events"]] == [1]
        assert page["high_watermark"] == 2
        assert page["has_more"] is True
        final_page = repository.get_audience_events(
            "game-1", after_seq=1, limit=10, through_seq=2,
        )
        assert [event["seq"] for event in final_page["events"]] == [2]
        assert final_page["has_more"] is False
    finally:
        repository.close()


def test_model_request_attempt_lifecycle_preserves_unknown_usage(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        repository.prepare_model_request(
            game_id="game-1", request_id="request-1", actor_seat=2,
            action_position="round:1:speech:seat:2", request_digest="digest",
            provider_profile="openai", model_id="model-a",
        )
        attempt = repository.start_model_attempt(
            game_id="game-1", request_id="request-1", execution_generation=1,
        )
        assert attempt["attempt_index"] == 1
        repository.finish_model_attempt(
            attempt["attempt_id"], status="unknown", failure_code="process_interrupted",
            elapsed_ms=None, usage=None,
        )
        repository.resolve_model_request(
            "game-1", "request-1", status="resolved",
            normalized_result={"action_type": "pass", "target_seat": None},
        )
        request = repository.get_model_request("game-1", "request-1")
        assert request["status"] == "resolved"
        assert request["normalized_result"]["action_type"] == "pass"
        attempts = repository.list_model_attempts("game-1")
        assert attempts[0]["usage_known"] is False
        assert attempts[0]["total_tokens"] is None
    finally:
        repository.close()


def test_repository_rejects_unsupported_database_version(tmp_path) -> None:
    path = tmp_path / "wolfkiller.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    connection.execute("INSERT INTO schema_migrations VALUES (999, 'now')")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="newer schema"):
        GameRepository(tmp_path)


def test_json_columns_are_canonical_and_do_not_store_nan(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        with pytest.raises(ValueError, match="JSON"):
            repository.create_game(
                game_id="bad", name="bad", config={"value": float("nan")},
                execution_status="running", source="native", model_snapshot=[],
            )
        assert repository.get_game("bad") is None
        _game(repository)
        with sqlite3.connect(repository.database_path) as connection:
            raw = connection.execute("SELECT config_json FROM games WHERE game_id='game-1'").fetchone()[0]
        assert raw == json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    finally:
        repository.close()


def test_commit_atomically_consumes_model_requests_and_enqueues_derived_jobs(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        repository.prepare_model_request(
            game_id="game-1", request_id="request-1", actor_seat=2,
            action_position="round:1:speech:seat:2", request_digest="request-digest",
            provider_profile="test", model_id="deterministic",
        )
        repository.resolve_model_request(
            "game-1", "request-1", status="resolved",
            normalized_result={"text": "hello"},
        )
        repository.commit_step(
            game_id="game-1", expected_storage_revision=0,
            execution_generation=1, step_key="speech:1:2",
            input_digest="input", result_digest="result",
            checkpoint={"checkpoint_version": 1}, domain_events=[],
            audience_events=[], audience_state={},
            consumed_model_request_ids=("request-1",),
            derived_jobs=({"job_key": "summary:game-1", "job_type": "summary"},),
        )
        assert repository.get_model_request("game-1", "request-1")["status"] == "consumed"
        assert repository.list_derived_jobs(game_id="game-1") == [{
            "job_key": "summary:game-1", "job_type": "summary",
            "game_id": "game-1", "run_id": None, "status": "pending",
            "error": None,
            "created_at": repository.list_derived_jobs(game_id="game-1")[0]["created_at"],
            "updated_at": repository.list_derived_jobs(game_id="game-1")[0]["updated_at"],
        }]
    finally:
        repository.close()


def _table_count(repository: GameRepository, table: str, game_id: str) -> int:
    with sqlite3.connect(repository.database_path) as connection:
        return int(connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE game_id=?", (game_id,),
        ).fetchone()[0])


def test_commit_step_keeps_only_the_latest_audience_snapshot(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        for index in range(1, 3):
            repository.commit_step(
                game_id="game-1", expected_storage_revision=index - 1,
                execution_generation=1, step_key=f"step-{index}",
                input_digest=f"i-{index}", result_digest=f"r-{index}",
                checkpoint={"checkpoint_version": 1, "state": {"index": index}},
                domain_events=[{
                    "event_id": f"domain-{index}", "event_type": "PHASE_CHANGED",
                    "payload": {"index": index}, "schema_version": 1,
                }],
                audience_events=[{
                    "event_id": f"event-{index}", "event_type": "phase",
                    "payload": {"index": index}, "schema_version": 1,
                }],
                audience_state={"index": index},
            )
        assert _table_count(repository, "audience_snapshots", "game-1") == 1
        assert _table_count(repository, "audience_events", "game-1") == 2
        snapshot = repository.get_audience_snapshot("game-1")
        assert snapshot is not None
        assert snapshot["last_seq"] == 2
        assert snapshot["state"] == {"index": 2}
    finally:
        repository.close()


def test_mark_game_deleted_purges_durable_payload_tables(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        repository.save_runtime_clock(
            "game-1", active_elapsed_ms=10, remaining_window_ms=None,
            execution_generation=1,
        )
        repository.prepare_model_request(
            game_id="game-1", request_id="request-1", actor_seat=2,
            action_position="round:1:speech:seat:2", request_digest="digest",
            provider_profile="test", model_id="model",
        )
        attempt = repository.start_model_attempt(
            game_id="game-1", request_id="request-1", execution_generation=1,
        )
        repository.finish_model_attempt(
            attempt["attempt_id"], status="succeeded", failure_code=None,
            elapsed_ms=1, usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )
        repository.resolve_model_request(
            "game-1", "request-1", status="resolved",
            normalized_result={"text": "hello"},
        )
        repository.commit_step(
            game_id="game-1", expected_storage_revision=0,
            execution_generation=1, step_key="step-1",
            input_digest="input", result_digest="result",
            checkpoint={"checkpoint_version": 1},
            domain_events=[{
                "event_id": "domain-1", "event_type": "SPEECH_MADE",
                "payload": {}, "schema_version": 1,
            }],
            audience_events=[{
                "event_id": "event-1", "event_type": "speech",
                "payload": {}, "schema_version": 1,
            }],
            audience_state={"phase": "speech"},
            consumed_model_request_ids=("request-1",),
            derived_jobs=({"job_key": "summary:game-1", "job_type": "summary"},),
        )
        repository.mark_game_deleted("game-1")
        for table in (
            "audience_snapshots", "audience_events", "domain_events",
            "model_attempts", "model_requests", "game_commits",
            "game_checkpoints", "game_runtime_clocks", "derived_jobs",
        ):
            assert _table_count(repository, table, "game-1") == 0, table
        assert repository.get_game("game-1") is None
        assert repository.get_game("game-1", include_deleted=True) is not None
    finally:
        repository.close()


def _commit_kwargs(**updates) -> dict[str, object]:
    values: dict[str, object] = {
        "game_id": "game-1",
        "expected_storage_revision": 0,
        "execution_generation": 1,
        "step_key": "step-async",
        "input_digest": "input-async",
        "result_digest": "result-async",
        "checkpoint": {"checkpoint_version": 1, "state": {"phase": "speech"}},
        "domain_events": [],
        "audience_events": [],
        "audience_state": {"game_id": "game-1"},
    }
    values.update(updates)
    return values


@pytest.mark.asyncio
async def test_awrite_rejects_closed_repository(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    repository.close()
    with pytest.raises(RuntimeError, match="closed"):
        await repository.awrite(lambda: None)


@pytest.mark.asyncio
async def test_commit_step_async_matches_sync_and_yields_to_event_loop(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        gate = threading.Event()
        original = repository._commit_step

        def blocked(*args):
            assert gate.wait(timeout=2)
            return original(*args)

        repository._commit_step = blocked
        progressed = False

        async def marker() -> None:
            nonlocal progressed
            await asyncio.sleep(0)
            progressed = True
            gate.set()

        result, _ = await asyncio.wait_for(
            asyncio.gather(
                repository.commit_step_async(**_commit_kwargs()),
                marker(),
            ),
            timeout=2,
        )
        assert progressed is True
        assert result["storage_revision"] == 1
        assert result["replayed"] is False
        assert repository.load_checkpoint("game-1")["storage_revision"] == 1
    finally:
        repository.close()


def test_mark_game_deleted_cancels_an_active_game(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        repository.mark_game_deleted("game-1")
        row = repository.get_game("game-1", include_deleted=True)
        assert row is not None
        assert row["execution_status"] == "cancelled"
    finally:
        repository.close()


def test_mark_game_deleted_keeps_a_completed_outcome(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        repository.transition_execution(
            "game-1", expected=("running",), target="completed",
        )
        repository.mark_game_deleted("game-1")
        row = repository.get_game("game-1", include_deleted=True)
        assert row is not None
        assert row["execution_status"] == "completed"
    finally:
        repository.close()


def test_transition_to_paused_persists_and_clears_the_reason(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        paused = repository.transition_execution(
            "game-1", expected=("running",), target="paused",
            pause_code="provider_circuit_open",
            pause_detail={"rule": "provider_terminal", "seats": [1, 2]},
        )
        assert paused["execution_status"] == "paused"
        assert paused["pause_code"] == "provider_circuit_open"
        assert paused["pause_detail"]["rule"] == "provider_terminal"
        assert paused["paused_at"] is not None

        resumed = repository.transition_execution(
            "game-1", expected=("paused",), target="running",
            increment_generation=True,
        )
        assert resumed["pause_code"] is None
        assert resumed["pause_detail"] == {}
        assert resumed["paused_at"] is None
    finally:
        repository.close()


def test_a_manual_pause_gets_a_reason_by_default(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        paused = repository.transition_execution(
            "game-1", expected=("running",), target="paused",
        )
        assert paused["pause_code"] == "manual"
        assert paused["pause_detail"] == {}
    finally:
        repository.close()


def test_v3_database_gains_the_pause_columns(tmp_path) -> None:
    """Opening an old fact database must migrate, not refuse to start."""
    path = tmp_path / "wolfkiller.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'x');
        INSERT INTO schema_migrations(version, applied_at) VALUES (2, 'x');
        INSERT INTO schema_migrations(version, applied_at) VALUES (3, 'x');
        CREATE TABLE games (
            game_id TEXT PRIMARY KEY, name TEXT NOT NULL, config_json TEXT NOT NULL,
            model_snapshot_json TEXT NOT NULL, execution_status TEXT NOT NULL,
            source TEXT NOT NULL, benchmark_run_id TEXT,
            storage_revision INTEGER NOT NULL DEFAULT 0,
            execution_generation INTEGER NOT NULL DEFAULT 1,
            recovery_block_code TEXT,
            interruption_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
        );
        INSERT INTO games(
            game_id, name, config_json, model_snapshot_json, execution_status,
            source, created_at, updated_at
        ) VALUES ('old', 'old', '{}', '[]', 'running', 'native', 'x', 'x');
    """)
    connection.commit()
    connection.close()

    repository = GameRepository(tmp_path)
    try:
        assert repository.schema_version() == 5
        row = repository.get_game("old")
        assert row["pause_code"] is None
        assert row["pause_detail"] == {}
        assert row["paused_at"] is None
    finally:
        repository.close()


def test_v4_database_gains_the_attempts_started_index(tmp_path) -> None:
    """A pre-v5 database upgrades and receives the latency-stats index."""
    path = tmp_path / "wolfkiller.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'x');
        INSERT INTO schema_migrations(version, applied_at) VALUES (2, 'x');
        INSERT INTO schema_migrations(version, applied_at) VALUES (3, 'x');
        INSERT INTO schema_migrations(version, applied_at) VALUES (4, 'x');
        CREATE TABLE model_attempts (attempt_id TEXT PRIMARY KEY, status TEXT, started_at TEXT);
    """)
    connection.commit()
    connection.close()

    repository = GameRepository(tmp_path)
    try:
        assert repository.schema_version() == 5
        with repository._connect() as connection:
            names = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        assert "idx_model_attempts_started_at" in names
    finally:
        repository.close()


def test_list_folder_items_returns_full_mapping(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        assert repository.list_folder_items() == {}
        folder = repository.create_folder("归档")
        _game(repository)
        repository.set_game_folder("game-1", folder["folder_id"])
        assert repository.list_folder_items() == {"game-1": folder["folder_id"]}
    finally:
        repository.close()


def test_append_audience_events_interleaves_with_commit_step_and_validates(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        assert repository.append_audience_events("game-1", []) == []
        repository.commit_step(
            game_id="game-1", expected_storage_revision=0,
            execution_generation=1, step_key="step-1",
            input_digest="i-1", result_digest="r-1",
            checkpoint={"checkpoint_version": 1},
            domain_events=[],
            audience_events=[{
                "event_id": "event-1", "event_type": "phase",
                "payload": {"phase": "dawn", "round_number": 1}, "schema_version": 1,
            }],
            audience_state={"phase": "dawn"},
        )
        seqs = repository.append_audience_events(
            "game-1",
            [{
                "event_id": "commentary:1:night",
                "event_type": "commentary",
                "payload": {
                    "round_number": 1, "beat": "night",
                    "title": "第1夜解说", "text": "平安夜。",
                },
                "schema_version": 1,
            }],
        )
        assert seqs == [2]
        # Duplicate event_id is idempotent and returns existing seq without raising
        dup_seqs = repository.append_audience_events(
            "game-1",
            [{
                "event_id": "commentary:1:night",
                "event_type": "commentary",
                "payload": {
                    "round_number": 1, "beat": "night",
                    "title": "第1夜解说", "text": "平安夜。",
                },
                "schema_version": 1,
            }],
        )
        assert dup_seqs == [2]
        # Next commit_step continues from MAX(seq)=2
        receipt = repository.commit_step(
            game_id="game-1", expected_storage_revision=1,
            execution_generation=1, step_key="step-2",
            input_digest="i-2", result_digest="r-2",
            checkpoint={"checkpoint_version": 1},
            domain_events=[],
            audience_events=[{
                "event_id": "event-2", "event_type": "speech",
                "payload": {"player_seat": 1, "text": "hi", "round_number": 1, "phase": "speech"},
                "schema_version": 1,
            }],
            audience_state={"phase": "speech"},
        )
        assert receipt["first_seq"] == 3
        assert receipt["last_seq"] == 3
        page = repository.get_audience_events("game-1", after_seq=0, limit=10)
        assert [row["seq"] for row in page["events"]] == [1, 2, 3]
        assert page["events"][1]["event_type"] == "commentary"

        with pytest.raises(KeyError):
            repository.append_audience_events("missing-game", [{
                "event_id": "c-1", "event_type": "commentary", "payload": {},
            }])
        with pytest.raises(ValueError, match="step_key"):
            repository.append_audience_events(
                "game-1",
                [{"event_id": "c-2", "event_type": "commentary", "payload": {}}],
                step_key="",
            )
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_append_audience_events_async_persists_rows(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _game(repository)
        seqs = await repository.append_audience_events_async(
            "game-1",
            [{
                "event_id": "commentary:1:day",
                "event_type": "commentary",
                "payload": {"round_number": 1, "beat": "day", "title": "t", "text": "x"},
            }],
        )
        assert seqs == [1]
        with pytest.raises(ValueError, match="step_key"):
            await repository.append_audience_events_async(
                "game-1",
                [{"event_id": "c-x", "event_type": "commentary", "payload": {}}],
                step_key="",
            )
    finally:
        repository.close()


def test_model_config_references_includes_commentary_config_id(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_game(
            game_id="game-comm",
            name="comm",
            config={"commentary": {"enabled": True, "commentary_config_id": "cfg-comm"}},
            execution_status="running",
            source="native",
            model_snapshot=[],
        )
        refs = repository.model_config_references("cfg-comm")
        assert refs["game_ids"] == ["game-comm"]
    finally:
        repository.close()

