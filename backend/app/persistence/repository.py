"""SQLite repository used as the durable source of truth for native games.

Writes are serialized on one dedicated thread.  Every public read opens a
short-lived connection so no transaction is kept open across application
``await`` points.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TypeVar


SCHEMA_VERSION = 2
T = TypeVar("T")
_FOLDER_NAME_MAX = 50


class CommitConflict(RuntimeError):
    """A stable step key was replayed with different inputs or results."""


class RepositoryVersionConflict(RuntimeError):
    """The caller attempted to commit from a stale durable revision."""


class InvalidExecutionTransition(RuntimeError):
    """The durable execution state does not allow the requested transition."""


class GameReferencedByBenchmark(RuntimeError):
    """A benchmark-owned game cannot be deleted independently."""


class ModelAttemptQuotaExceeded(RuntimeError):
    """A benchmark game has exhausted its frozen provider-attempt budget."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"value is not canonical JSON: {error}") from error


def _decode(value: str | None, default: T) -> Any | T:
    if value is None:
        return default
    return json.loads(value)


def _references_model_config(value: object, config_id: str) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {
                "config_id", "model_config_id", "baseline_config_id",
                "candidate_config_id",
            } and item == config_id:
                return True
            if _references_model_config(item, config_id):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_references_model_config(item, config_id) for item in value)
    return False


def _digest_json(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    model_snapshot_json TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    source TEXT NOT NULL,
    benchmark_run_id TEXT,
    storage_revision INTEGER NOT NULL DEFAULT 0 CHECK(storage_revision >= 0),
    execution_generation INTEGER NOT NULL DEFAULT 1 CHECK(execution_generation >= 0),
    recovery_block_code TEXT,
    interruption_count INTEGER NOT NULL DEFAULT 0 CHECK(interruption_count >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS game_checkpoints (
    game_id TEXT PRIMARY KEY REFERENCES games(game_id) ON DELETE CASCADE,
    storage_revision INTEGER NOT NULL CHECK(storage_revision >= 0),
    checkpoint_version INTEGER NOT NULL CHECK(checkpoint_version >= 1),
    checkpoint_json TEXT NOT NULL,
    checkpoint_digest TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_runtime_clocks (
    game_id TEXT PRIMARY KEY REFERENCES games(game_id) ON DELETE CASCADE,
    active_elapsed_ms INTEGER NOT NULL DEFAULT 0 CHECK(active_elapsed_ms >= 0),
    remaining_window_ms INTEGER CHECK(remaining_window_ms IS NULL OR remaining_window_ms >= 0),
    execution_generation INTEGER NOT NULL CHECK(execution_generation >= 0),
    saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_commits (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    result_digest TEXT NOT NULL,
    storage_revision INTEGER NOT NULL CHECK(storage_revision >= 1),
    first_audience_seq INTEGER,
    last_audience_seq INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (game_id, step_key),
    UNIQUE (game_id, storage_revision)
);

CREATE TABLE IF NOT EXISTS domain_events (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    domain_seq INTEGER NOT NULL CHECK(domain_seq >= 1),
    event_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (game_id, domain_seq),
    UNIQUE (game_id, event_id)
);

CREATE TABLE IF NOT EXISTS audience_events (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL CHECK(seq >= 1),
    event_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (game_id, seq),
    UNIQUE (game_id, event_id)
);

CREATE TABLE IF NOT EXISTS audience_snapshots (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL CHECK(seq >= 0),
    projection_version INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    state_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (game_id, seq)
);

CREATE TABLE IF NOT EXISTS model_requests (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    actor_seat INTEGER NOT NULL CHECK(actor_seat >= 1),
    action_position TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    provider_profile TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL,
    normalized_result_json TEXT,
    recovery_retry_count INTEGER NOT NULL DEFAULT 0 CHECK(recovery_retry_count >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (game_id, request_id)
);

CREATE TABLE IF NOT EXISTS model_attempts (
    attempt_id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    attempt_index INTEGER NOT NULL CHECK(attempt_index >= 1),
    execution_generation INTEGER NOT NULL CHECK(execution_generation >= 0),
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    elapsed_ms INTEGER CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0),
    prompt_tokens INTEGER CHECK(prompt_tokens IS NULL OR prompt_tokens >= 0),
    completion_tokens INTEGER CHECK(completion_tokens IS NULL OR completion_tokens >= 0),
    total_tokens INTEGER CHECK(total_tokens IS NULL OR total_tokens >= 0),
    usage_known INTEGER NOT NULL DEFAULT 0 CHECK(usage_known IN (0, 1)),
    failure_code TEXT,
    FOREIGN KEY (game_id, request_id) REFERENCES model_requests(game_id, request_id) ON DELETE CASCADE,
    UNIQUE (game_id, request_id, attempt_index)
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    run_id TEXT PRIMARY KEY,
    client_request_id TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    schedule_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_items (
    run_id TEXT NOT NULL REFERENCES benchmark_runs(run_id) ON DELETE RESTRICT,
    item_index INTEGER NOT NULL CHECK(item_index >= 0),
    scenario_id TEXT NOT NULL,
    pair_id TEXT,
    block_index INTEGER NOT NULL CHECK(block_index >= 0),
    assignment_json TEXT NOT NULL,
    game_id TEXT UNIQUE REFERENCES games(game_id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    terminal_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, item_index)
);

CREATE TABLE IF NOT EXISTS benchmark_reports (
    run_id TEXT NOT NULL REFERENCES benchmark_runs(run_id) ON DELETE RESTRICT,
    metric_version TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    report_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, metric_version, input_digest)
);

CREATE TABLE IF NOT EXISTS derived_jobs (
    job_key TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    game_id TEXT REFERENCES games(game_id) ON DELETE CASCADE,
    run_id TEXT REFERENCES benchmark_runs(run_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_games_status ON games(execution_status, updated_at);
CREATE INDEX IF NOT EXISTS idx_audience_events_game_seq ON audience_events(game_id, seq);
CREATE INDEX IF NOT EXISTS idx_model_attempts_request ON model_attempts(game_id, request_id, attempt_index);
CREATE INDEX IF NOT EXISTS idx_benchmark_items_status ON benchmark_items(run_id, status, item_index);
"""

_FOLDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_folders (
    folder_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_folder_items (
    game_id TEXT PRIMARY KEY,
    folder_id TEXT NOT NULL REFERENCES game_folders(folder_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_game_folder_items_folder ON game_folder_items(folder_id);
"""

_SCHEMA += _FOLDER_SCHEMA


def _normalize_folder_name(name: object) -> str:
    if type(name) is not str:
        raise ValueError("folder name must not be blank")
    stripped = name.strip()
    if not stripped:
        raise ValueError("folder name must not be blank")
    if len(stripped) > _FOLDER_NAME_MAX:
        raise ValueError("folder name is too long")
    return stripped


class GameRepository:
    """Durable repository with one serialized writer and transactional reads."""

    def __init__(
        self, data_dir: str | Path = "data",
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "wolfkiller.sqlite3"
        self._fault_injector = fault_injector
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wolfkiller-db")
        self._closed = False
        try:
            self._write(self._initialize)
        except BaseException:
            self._writer.shutdown(wait=True, cancel_futures=True)
            self._closed = True
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()
            current = int(row["version"] or 0)
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database has newer schema version {current}; supported is {SCHEMA_VERSION}"
                )
            if current < 1:
                connection.executescript(_SCHEMA)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, _now()),
                )
                current = 1
            if current < 2:
                connection.executescript(_FOLDER_SCHEMA)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, _now()),
                )

    def _write(self, function: Callable[..., T], *args: object) -> T:
        if self._closed:
            raise RuntimeError("repository is closed")
        return self._writer.submit(function, *args).result()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.shutdown(wait=True, cancel_futures=False)

    def schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
            return int(row["version"] or 0)

    def pragmas(self) -> dict[str, object]:
        with self._connect() as connection:
            return {
                "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
                "synchronous": connection.execute("PRAGMA synchronous").fetchone()[0],
                "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
                "busy_timeout": connection.execute("PRAGMA busy_timeout").fetchone()[0],
            }

    def table_names(self) -> set[str]:
        with self._connect() as connection:
            return {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

    def create_game(
        self, *, game_id: str, name: str, config: Mapping[str, object],
        execution_status: str, source: str,
        model_snapshot: Iterable[Mapping[str, object]],
        benchmark_run_id: str | None = None,
        benchmark_item_index: int | None = None,
        execution_generation: int = 1,
    ) -> None:
        if benchmark_item_index is not None and benchmark_run_id is None:
            raise ValueError("benchmark_item_index requires benchmark_run_id")
        config_json = _json(dict(config))
        snapshot_json = _json([dict(item) for item in model_snapshot])
        self._write(
            self._create_game, game_id, name, config_json, execution_status,
            source, snapshot_json, benchmark_run_id, benchmark_item_index,
            execution_generation,
        )

    def _create_game(
        self, game_id: str, name: str, config_json: str,
        execution_status: str, source: str, snapshot_json: str,
        benchmark_run_id: str | None, benchmark_item_index: int | None,
        execution_generation: int,
    ) -> None:
        now = _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if benchmark_item_index is not None:
                item = connection.execute(
                    "SELECT status,game_id FROM benchmark_items "
                    "WHERE run_id=? AND item_index=?",
                    (benchmark_run_id, benchmark_item_index),
                ).fetchone()
                if item is None or item["status"] != "running" or item["game_id"] is not None:
                    raise CommitConflict("benchmark item cannot bind game")
            connection.execute(
                "INSERT INTO games(game_id,name,config_json,model_snapshot_json,"
                "execution_status,source,benchmark_run_id,execution_generation,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (game_id, name, config_json, snapshot_json, execution_status,
                 source, benchmark_run_id, execution_generation, now, now),
            )
            if benchmark_item_index is not None:
                cursor = connection.execute(
                    "UPDATE benchmark_items SET game_id=?,updated_at=? "
                    "WHERE run_id=? AND item_index=? AND status='running' "
                    "AND game_id IS NULL",
                    (game_id, now, benchmark_run_id, benchmark_item_index),
                )
                if cursor.rowcount != 1:
                    raise CommitConflict("benchmark item cannot bind game")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _game_row(row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        result["config"] = _decode(result.pop("config_json"), {})
        result["model_snapshot"] = _decode(result.pop("model_snapshot_json"), [])
        return result

    def get_game(self, game_id: str, *, include_deleted: bool = False) -> dict[str, object] | None:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM games WHERE game_id=?{clause}", (game_id,),
            ).fetchone()
            return None if row is None else self._game_row(row)

    def list_games(self, *, include_deleted: bool = False) -> list[dict[str, object]]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM games {where} ORDER BY created_at, game_id"
            ).fetchall()
            return [self._game_row(row) for row in rows]

    def model_config_references(self, config_id: str) -> dict[str, list[str]]:
        """Return unfinished games and benchmark plans that still need a config."""
        if type(config_id) is not str or not config_id:
            raise ValueError("config_id must be a non-empty string")
        game_ids = [
            str(row["game_id"])
            for row in self.list_games()
            if row["execution_status"] not in {"completed", "failed", "cancelled"}
            and _references_model_config(row["config"], config_id)
        ]
        benchmark_ids = [
            str(row["run_id"])
            for row in self.list_benchmark_runs()
            if row["status"] not in {"completed", "failed", "cancelled"}
            and _references_model_config(row["config"], config_id)
        ]
        return {
            "game_ids": sorted(game_ids),
            "benchmark_run_ids": sorted(benchmark_ids),
        }

    def rename_game(self, game_id: str, name: str) -> None:
        if type(name) is not str or not name.strip():
            raise ValueError("game name must not be blank")
        self._write(self._rename_game, game_id, name.strip())

    def _rename_game(self, game_id: str, name: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE games SET name=?,updated_at=? "
                "WHERE game_id=? AND deleted_at IS NULL",
                (name, _now(), game_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(game_id)

    def mark_game_deleted(self, game_id: str) -> None:
        self._write(self._mark_game_deleted, game_id)

    def _mark_game_deleted(self, game_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT benchmark_run_id,deleted_at FROM games WHERE game_id=?",
                (game_id,),
            ).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise KeyError(game_id)
            if row["benchmark_run_id"] is not None:
                raise GameReferencedByBenchmark(str(row["benchmark_run_id"]))
            now = _now()
            connection.execute(
                "UPDATE games SET deleted_at=?,updated_at=? WHERE game_id=?",
                (now, now, game_id),
            )
            self._purge_game_payload(connection, game_id)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _purge_game_payload(connection: sqlite3.Connection, game_id: str) -> None:
        connection.execute("DELETE FROM model_requests WHERE game_id=?", (game_id,))
        for table in (
            "audience_snapshots", "audience_events", "domain_events",
            "game_commits", "game_checkpoints", "game_runtime_clocks",
            "derived_jobs", "game_folder_items",
        ):
            connection.execute(f"DELETE FROM {table} WHERE game_id=?", (game_id,))

    def create_folder(self, name: str) -> dict[str, object]:
        return self._write(self._create_folder, _normalize_folder_name(name))

    def _create_folder(self, name: str) -> dict[str, object]:
        folder_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO game_folders(folder_id,name,created_at,updated_at) "
                    "VALUES (?,?,?,?)",
                    (folder_id, name, now, now),
                )
            except sqlite3.IntegrityError as error:
                raise CommitConflict("folder name already exists") from error
        record = self.get_folder(folder_id)
        assert record is not None
        return record

    def get_folder(self, folder_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT f.folder_id,f.name,f.created_at,f.updated_at,"
                "COUNT(i.game_id) AS game_count "
                "FROM game_folders AS f "
                "LEFT JOIN game_folder_items AS i ON i.folder_id=f.folder_id "
                "WHERE f.folder_id=? GROUP BY f.folder_id",
                (folder_id,),
            ).fetchone()
            return None if row is None else dict(row)

    def list_folders(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT f.folder_id,f.name,f.created_at,f.updated_at,"
                "COUNT(i.game_id) AS game_count "
                "FROM game_folders AS f "
                "LEFT JOIN game_folder_items AS i ON i.folder_id=f.folder_id "
                "GROUP BY f.folder_id "
                "ORDER BY f.created_at, f.folder_id"
            ).fetchall()
            return [dict(row) for row in rows]

    def rename_folder(self, folder_id: str, name: str) -> dict[str, object]:
        return self._write(self._rename_folder, folder_id, _normalize_folder_name(name))

    def _rename_folder(self, folder_id: str, name: str) -> dict[str, object]:
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    "UPDATE game_folders SET name=?,updated_at=? WHERE folder_id=?",
                    (name, _now(), folder_id),
                )
            except sqlite3.IntegrityError as error:
                raise CommitConflict("folder name already exists") from error
            if cursor.rowcount != 1:
                raise KeyError(folder_id)
        record = self.get_folder(folder_id)
        assert record is not None
        return record

    def delete_folder(self, folder_id: str) -> None:
        self._write(self._delete_folder, folder_id)

    def _delete_folder(self, folder_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM game_folders WHERE folder_id=?", (folder_id,),
            )
            if cursor.rowcount != 1:
                raise KeyError(folder_id)

    def get_game_folder(self, game_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT folder_id FROM game_folder_items WHERE game_id=?",
                (game_id,),
            ).fetchone()
            return None if row is None else str(row["folder_id"])

    def set_game_folder(self, game_id: str, folder_id: str | None) -> None:
        if type(game_id) is not str or not game_id:
            raise ValueError("game_id must be a non-empty string")
        if folder_id is not None and (type(folder_id) is not str or not folder_id):
            raise ValueError("folder_id must be a non-empty string")
        self._write(self._set_game_folder, game_id, folder_id)

    def _set_game_folder(self, game_id: str, folder_id: str | None) -> None:
        with self._connect() as connection:
            if folder_id is None:
                connection.execute(
                    "DELETE FROM game_folder_items WHERE game_id=?", (game_id,),
                )
                return
            exists = connection.execute(
                "SELECT 1 FROM game_folders WHERE folder_id=?", (folder_id,),
            ).fetchone()
            if exists is None:
                raise KeyError(folder_id)
            connection.execute(
                "INSERT INTO game_folder_items(game_id,folder_id) VALUES (?,?) "
                "ON CONFLICT(game_id) DO UPDATE SET folder_id=excluded.folder_id",
                (game_id, folder_id),
            )

    def transition_execution(
        self, game_id: str, *, expected: tuple[str, ...], target: str,
        increment_generation: bool = False,
        recovery_block_code: str | None = None,
    ) -> dict[str, object]:
        if type(expected) is not tuple or not expected or any(
            type(item) is not str or not item for item in expected
        ):
            raise ValueError("expected states must be a non-empty tuple")
        if type(target) is not str or not target:
            raise ValueError("target must be a non-empty string")
        return self._write(
            self._transition_execution, game_id, expected, target,
            increment_generation, recovery_block_code,
        )

    def _transition_execution(
        self, game_id: str, expected: tuple[str, ...], target: str,
        increment_generation: bool, recovery_block_code: str | None,
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM games WHERE game_id=? AND deleted_at IS NULL",
                (game_id,),
            ).fetchone()
            if row is None:
                raise KeyError(game_id)
            current = str(row["execution_status"])
            if current == target:
                connection.rollback()
                return self._game_row(row)
            if current not in expected:
                raise InvalidExecutionTransition(
                    f"cannot transition game from {current} to {target}"
                )
            generation = int(row["execution_generation"]) + int(increment_generation)
            interruptions = int(row["interruption_count"]) + int(target == "interrupted")
            connection.execute(
                "UPDATE games SET execution_status=?,execution_generation=?,"
                "recovery_block_code=?,interruption_count=?,updated_at=? WHERE game_id=?",
                (target, generation, recovery_block_code, interruptions, _now(), game_id),
            )
            updated = connection.execute(
                "SELECT * FROM games WHERE game_id=?", (game_id,),
            ).fetchone()
            connection.commit()
            return self._game_row(updated)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_runtime_clock(
        self, game_id: str, *, active_elapsed_ms: int,
        remaining_window_ms: int | None, execution_generation: int,
    ) -> None:
        for value, label in (
            (active_elapsed_ms, "active_elapsed_ms"),
            (execution_generation, "execution_generation"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if remaining_window_ms is not None and (
            type(remaining_window_ms) is not int or remaining_window_ms < 0
        ):
            raise ValueError("remaining_window_ms must be a non-negative integer or null")
        self._write(
            self._save_runtime_clock, game_id, active_elapsed_ms,
            remaining_window_ms, execution_generation,
        )

    def _save_runtime_clock(
        self, game_id: str, active_elapsed_ms: int,
        remaining_window_ms: int | None, generation: int,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT execution_generation FROM games WHERE game_id=? AND deleted_at IS NULL",
                (game_id,),
            ).fetchone()
            if row is None:
                raise KeyError(game_id)
            if int(row["execution_generation"]) != generation:
                raise ValueError("stale execution generation")
            connection.execute(
                "INSERT INTO game_runtime_clocks VALUES (?,?,?,?,?) "
                "ON CONFLICT(game_id) DO UPDATE SET "
                "active_elapsed_ms=excluded.active_elapsed_ms,"
                "remaining_window_ms=excluded.remaining_window_ms,"
                "execution_generation=excluded.execution_generation,"
                "saved_at=excluded.saved_at",
                (game_id, active_elapsed_ms, remaining_window_ms, generation, _now()),
            )

    def get_runtime_clock(self, game_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM game_runtime_clocks WHERE game_id=?", (game_id,),
            ).fetchone()
            return None if row is None else dict(row)

    def interrupt_running_games(self) -> list[str]:
        """Apply startup/shutdown recovery semantics in one short transaction."""
        return self._write(self._interrupt_running_games)

    def _interrupt_running_games(self) -> list[str]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            game_ids = [row[0] for row in connection.execute(
                "SELECT game_id FROM games WHERE execution_status='running' "
                "AND deleted_at IS NULL ORDER BY game_id"
            )]
            if not game_ids:
                connection.rollback()
                return []
            placeholders = ",".join("?" for _ in game_ids)
            now = _now()
            connection.execute(
                f"UPDATE games SET execution_status='interrupted',"
                f"interruption_count=interruption_count+1,updated_at=? "
                f"WHERE game_id IN ({placeholders})", (now, *game_ids),
            )
            connection.execute(
                f"UPDATE model_attempts SET status='unknown',finished_at=?,"
                f"failure_code='process_interrupted',usage_known=0 "
                f"WHERE status='in_flight' AND game_id IN ({placeholders})",
                (now, *game_ids),
            )
            connection.execute(
                f"UPDATE model_requests SET status='unknown',updated_at=? "
                f"WHERE status='in_flight' AND game_id IN ({placeholders})",
                (now, *game_ids),
            )
            connection.commit()
            return game_ids
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def commit_step(
        self, *, game_id: str, expected_storage_revision: int,
        execution_generation: int, step_key: str, input_digest: str,
        result_digest: str, checkpoint: Mapping[str, object],
        domain_events: Iterable[Mapping[str, object]],
        audience_events: Iterable[Mapping[str, object]],
        audience_state: Mapping[str, object], projection_version: int = 1,
        consumed_model_request_ids: Iterable[str] = (),
        derived_jobs: Iterable[Mapping[str, object]] = (),
    ) -> dict[str, object]:
        checkpoint_value = dict(checkpoint)
        checkpoint_json = _json(checkpoint_value)
        checkpoint_version = checkpoint_value.get("checkpoint_version")
        if type(checkpoint_version) is not int or checkpoint_version < 1:
            raise ValueError("checkpoint_version must be a positive integer")
        domains = [self._normalize_event(item) for item in domain_events]
        audience = [self._normalize_event(item) for item in audience_events]
        state_json = _json(dict(audience_state))
        request_ids = tuple(consumed_model_request_ids)
        if any(type(item) is not str or not item for item in request_ids):
            raise ValueError("consumed model request IDs must be non-empty strings")
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("consumed model request IDs must be unique")
        jobs: list[tuple[str, str, str | None]] = []
        for value in derived_jobs:
            row = dict(value)
            if set(row) - {"job_key", "job_type", "run_id"}:
                raise ValueError("unknown derived job field")
            job_key, job_type, run_id = row.get("job_key"), row.get("job_type"), row.get("run_id")
            if type(job_key) is not str or not job_key or type(job_type) is not str or not job_type:
                raise ValueError("derived job key and type are required")
            if run_id is not None and (type(run_id) is not str or not run_id):
                raise ValueError("derived job run_id must be a string or null")
            jobs.append((job_key, job_type, run_id))
        return self._write(
            self._commit_step, game_id, expected_storage_revision,
            execution_generation, step_key, input_digest, result_digest,
            checkpoint_version, checkpoint_json, domains, audience,
            state_json, projection_version, request_ids, jobs,
        )

    @staticmethod
    def _normalize_event(event: Mapping[str, object]) -> tuple[str, str, str, int, str]:
        event_id = event.get("event_id")
        event_type = event.get("event_type")
        schema_version = event.get("schema_version", 1)
        timestamp = event.get("timestamp") or _now()
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id is required")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type is required")
        if type(schema_version) is not int or schema_version < 1:
            raise ValueError("event schema_version must be positive")
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError("event timestamp is required")
        return event_id, event_type, _json(dict(event.get("payload") or {})), schema_version, timestamp

    def _commit_step(
        self, game_id: str, expected_revision: int, generation: int,
        step_key: str, input_digest: str, result_digest: str,
        checkpoint_version: int, checkpoint_json: str,
        domains: list[tuple[str, str, str, int, str]],
        audience: list[tuple[str, str, str, int, str]],
        state_json: str, projection_version: int,
        request_ids: tuple[str, ...], jobs: list[tuple[str, str, str | None]],
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            game = connection.execute(
                "SELECT storage_revision,execution_generation FROM games "
                "WHERE game_id=? AND deleted_at IS NULL", (game_id,),
            ).fetchone()
            if game is None:
                raise KeyError(game_id)
            existing = connection.execute(
                "SELECT * FROM game_commits WHERE game_id=? AND step_key=?",
                (game_id, step_key),
            ).fetchone()
            if existing is not None:
                if (existing["input_digest"], existing["result_digest"]) != (input_digest, result_digest):
                    raise CommitConflict(f"step_key {step_key!r} has conflicting content")
                connection.rollback()
                return {
                    "storage_revision": existing["storage_revision"],
                    "first_seq": existing["first_audience_seq"],
                    "last_seq": existing["last_audience_seq"],
                    "replayed": True,
                }
            current_revision = int(game["storage_revision"])
            if current_revision != expected_revision:
                raise RepositoryVersionConflict(
                    f"storage revision is {current_revision}, expected {expected_revision}"
                )
            if int(game["execution_generation"]) != generation:
                raise RepositoryVersionConflict("execution generation is stale")

            next_revision = current_revision + 1
            domain_base = connection.execute(
                "SELECT COALESCE(MAX(domain_seq),0) FROM domain_events WHERE game_id=?",
                (game_id,),
            ).fetchone()[0]
            audience_base = connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM audience_events WHERE game_id=?",
                (game_id,),
            ).fetchone()[0]
            now = _now()
            connection.execute(
                "INSERT INTO game_commits VALUES (?,?,?,?,?,?,?,?)",
                (game_id, step_key, input_digest, result_digest, next_revision,
                 audience_base + 1 if audience else None,
                 audience_base + len(audience), now),
            )
            for offset, (event_id, event_type, payload, version, timestamp) in enumerate(domains, 1):
                connection.execute(
                    "INSERT INTO domain_events VALUES (?,?,?,?,?,?,?,?)",
                    (game_id, domain_base + offset, event_id, step_key,
                     event_type, payload, version, timestamp),
                )
            for offset, (event_id, event_type, payload, version, timestamp) in enumerate(audience, 1):
                connection.execute(
                    "INSERT INTO audience_events VALUES (?,?,?,?,?,?,?,?)",
                    (game_id, audience_base + offset, event_id, step_key,
                     event_type, payload, version, timestamp),
                )
            last_seq = audience_base + len(audience)
            connection.execute(
                "INSERT INTO game_checkpoints VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(game_id) DO UPDATE SET "
                "storage_revision=excluded.storage_revision,checkpoint_version=excluded.checkpoint_version,"
                "checkpoint_json=excluded.checkpoint_json,checkpoint_digest=excluded.checkpoint_digest,"
                "updated_at=excluded.updated_at",
                (game_id, next_revision, checkpoint_version, checkpoint_json,
                 hashlib.sha256(checkpoint_json.encode("utf-8")).hexdigest(), now),
            )
            connection.execute(
                "INSERT OR REPLACE INTO audience_snapshots VALUES (?,?,?,?,?,?)",
                (game_id, last_seq, projection_version, state_json,
                 hashlib.sha256(state_json.encode("utf-8")).hexdigest(), now),
            )
            connection.execute(
                "DELETE FROM audience_snapshots WHERE game_id=? AND seq < ?",
                (game_id, last_seq),
            )
            connection.execute(
                "UPDATE games SET storage_revision=?,updated_at=? WHERE game_id=?",
                (next_revision, now, game_id),
            )
            for request_id in request_ids:
                cursor = connection.execute(
                    "UPDATE model_requests SET status='consumed',updated_at=? "
                    "WHERE game_id=? AND request_id=? AND status='resolved'",
                    (now, game_id, request_id),
                )
                if cursor.rowcount != 1:
                    raise CommitConflict(
                        f"model request {request_id!r} is absent or not resolved"
                    )
            for job_key, job_type, run_id in jobs:
                connection.execute(
                    "INSERT INTO derived_jobs(job_key,job_type,game_id,run_id,status,error,created_at,updated_at) "
                    "VALUES (?,?,?,?, 'pending',NULL,?,?)",
                    (job_key, job_type, game_id, run_id, now, now),
                )
            if self._fault_injector is not None:
                self._fault_injector("inside_transaction_before_commit")
            connection.commit()
            return {
                "storage_revision": next_revision,
                "first_seq": audience_base + 1 if audience else None,
                "last_seq": last_seq,
                "replayed": False,
            }
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_checkpoint(self, game_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM game_checkpoints WHERE game_id=?", (game_id,),
            ).fetchone()
            if row is None:
                return None
            checkpoint_json = row["checkpoint_json"]
            digest = hashlib.sha256(checkpoint_json.encode("utf-8")).hexdigest()
            if digest != row["checkpoint_digest"]:
                raise ValueError("checkpoint digest mismatch")
            return {
                "game_id": game_id,
                "storage_revision": row["storage_revision"],
                "checkpoint_version": row["checkpoint_version"],
                "checkpoint": json.loads(checkpoint_json),
                "updated_at": row["updated_at"],
            }

    def list_commits(self, game_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM game_commits WHERE game_id=? ORDER BY storage_revision",
                (game_id,),
            )]

    def list_derived_jobs(
        self, *, game_id: str | None = None, run_id: str | None = None,
    ) -> list[dict[str, object]]:
        clauses: list[str] = []
        parameters: list[str] = []
        if game_id is not None:
            clauses.append("game_id=?"); parameters.append(game_id)
        if run_id is not None:
            clauses.append("run_id=?"); parameters.append(run_id)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM derived_jobs" + where + " ORDER BY created_at,job_key",
                parameters,
            )]

    def get_audience_snapshot(self, game_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM audience_snapshots WHERE game_id=? ORDER BY seq DESC LIMIT 1",
                (game_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "game_id": game_id, "schema_version": 1,
                "projection_version": row["projection_version"],
                "last_seq": row["seq"], "state": json.loads(row["state_json"]),
            }

    def get_audience_events(
        self, game_id: str, *, after_seq: int, limit: int,
        through_seq: int | None = None,
    ) -> dict[str, object]:
        if type(after_seq) is not int or after_seq < 0:
            raise ValueError("after_seq must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if through_seq is not None and (type(through_seq) is not int or through_seq < 0):
            raise ValueError("through_seq must be a non-negative integer")
        with self._connect() as connection:
            connection.execute("BEGIN")
            actual_high = int(connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM audience_events WHERE game_id=?",
                (game_id,),
            ).fetchone()[0])
            high = actual_high if through_seq is None else min(through_seq, actual_high)
            rows = connection.execute(
                "SELECT * FROM audience_events WHERE game_id=? AND seq>? AND seq<=? "
                "ORDER BY seq LIMIT ?", (game_id, after_seq, high, limit),
            ).fetchall()
            connection.commit()
        events = [{
            "game_id": game_id,
            "seq": row["seq"],
            "event_id": row["event_id"],
            "schema_version": row["schema_version"],
            "event_type": row["event_type"],
            "timestamp": row["created_at"],
            "payload": json.loads(row["payload_json"]),
        } for row in rows]
        next_seq = int(rows[-1]["seq"]) if rows else after_seq
        return {
            "game_id": game_id,
            "events": events,
            "next_seq": next_seq,
            "high_watermark": high,
            "has_more": next_seq < high,
        }

    def prepare_model_request(
        self, *, game_id: str, request_id: str, actor_seat: int,
        action_position: str, request_digest: str,
        provider_profile: str, model_id: str,
    ) -> None:
        self._write(
            self._prepare_model_request, game_id, request_id, actor_seat,
            action_position, request_digest, provider_profile, model_id,
        )

    def _prepare_model_request(
        self, game_id: str, request_id: str, actor_seat: int,
        action_position: str, request_digest: str,
        provider_profile: str, model_id: str,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT request_digest FROM model_requests WHERE game_id=? AND request_id=?",
                (game_id, request_id),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise CommitConflict(f"request_id {request_id!r} has conflicting content")
                return
            connection.execute(
                "INSERT INTO model_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (game_id, request_id, actor_seat, action_position, request_digest,
                 provider_profile, model_id, "prepared", None, 0, now, now),
            )

    def start_model_attempt(
        self, *, game_id: str, request_id: str, execution_generation: int,
    ) -> dict[str, object]:
        return self._write(
            self._start_model_attempt, game_id, request_id, execution_generation,
        )

    def _start_model_attempt(
        self, game_id: str, request_id: str, generation: int,
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT mr.status,g.benchmark_run_id,br.config_json "
                "FROM model_requests AS mr "
                "JOIN games AS g ON g.game_id=mr.game_id "
                "LEFT JOIN benchmark_runs AS br ON br.run_id=g.benchmark_run_id "
                "WHERE mr.game_id=? AND mr.request_id=?",
                (game_id, request_id),
            ).fetchone()
            if request is None:
                raise KeyError(request_id)
            config = _decode(request["config_json"], {})
            limit = config.get("max_attempts_per_game") if isinstance(config, Mapping) else None
            if type(limit) is int and limit > 0:
                used = int(connection.execute(
                    "SELECT COUNT(*) FROM model_attempts WHERE game_id=?",
                    (game_id,),
                ).fetchone()[0])
                if used >= limit:
                    raise ModelAttemptQuotaExceeded(
                        f"benchmark game {game_id!r} exhausted its attempt quota ({limit})"
                    )
            attempt_index = int(connection.execute(
                "SELECT COUNT(*) FROM model_attempts WHERE game_id=? AND request_id=?",
                (game_id, request_id),
            ).fetchone()[0]) + 1
            attempt_id = str(uuid.uuid4())
            started = _now()
            connection.execute(
                "INSERT INTO model_attempts(attempt_id,game_id,request_id,attempt_index,"
                "execution_generation,status,started_at) VALUES (?,?,?,?,?,?,?)",
                (attempt_id, game_id, request_id, attempt_index, generation,
                 "in_flight", started),
            )
            connection.execute(
                "UPDATE model_requests SET status='in_flight',updated_at=? "
                "WHERE game_id=? AND request_id=?", (started, game_id, request_id),
            )
            connection.commit()
            return {"attempt_id": attempt_id, "attempt_index": attempt_index, "started_at": started}
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_model_attempt(
        self, attempt_id: str, *, status: str, failure_code: str | None,
        elapsed_ms: int | None, usage: Mapping[str, int] | None,
    ) -> None:
        self._write(
            self._finish_model_attempt, attempt_id, status, failure_code,
            elapsed_ms, None if usage is None else dict(usage),
        )

    def _finish_model_attempt(
        self, attempt_id: str, status: str, failure_code: str | None,
        elapsed_ms: int | None, usage: dict[str, int] | None,
    ) -> None:
        values = usage or {}
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE model_attempts SET status=?,finished_at=?,elapsed_ms=?,"
                "prompt_tokens=?,completion_tokens=?,total_tokens=?,usage_known=?,failure_code=? "
                "WHERE attempt_id=?",
                (status, _now(), elapsed_ms, values.get("prompt_tokens"),
                 values.get("completion_tokens"), values.get("total_tokens"),
                 int(usage is not None), failure_code, attempt_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(attempt_id)

    def resolve_model_request(
        self, game_id: str, request_id: str, *, status: str,
        normalized_result: Mapping[str, object] | None,
    ) -> None:
        result_json = None if normalized_result is None else _json(dict(normalized_result))
        self._write(
            self._resolve_model_request, game_id, request_id, status, result_json,
        )

    def _resolve_model_request(
        self, game_id: str, request_id: str, status: str,
        result_json: str | None,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE model_requests SET status=?,normalized_result_json=?,updated_at=? "
                "WHERE game_id=? AND request_id=?",
                (status, result_json, _now(), game_id, request_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(request_id)

    def get_model_request(self, game_id: str, request_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_requests WHERE game_id=? AND request_id=?",
                (game_id, request_id),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["normalized_result"] = _decode(result.pop("normalized_result_json"), None)
            return result

    def reserve_recovery_retry(self, game_id: str, request_id: str) -> bool:
        """Persistently grant at most one retry for an interrupted logical request."""
        return self._write(self._reserve_recovery_retry, game_id, request_id)

    def _reserve_recovery_retry(self, game_id: str, request_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE model_requests SET recovery_retry_count=recovery_retry_count+1,"
                "updated_at=? WHERE game_id=? AND request_id=? AND status='unknown' "
                "AND recovery_retry_count=0",
                (_now(), game_id, request_id),
            )
            if cursor.rowcount:
                return True
            row = connection.execute(
                "SELECT 1 FROM model_requests WHERE game_id=? AND request_id=?",
                (game_id, request_id),
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            return False

    def list_model_attempts(self, game_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            return [
                {**dict(row), "usage_known": bool(row["usage_known"])}
                for row in connection.execute(
                    "SELECT * FROM model_attempts WHERE game_id=? ORDER BY request_id,attempt_index",
                    (game_id,),
                )
            ]

    def list_model_requests(self, game_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM model_requests WHERE game_id=? ORDER BY request_id",
                (game_id,),
            )
            result = []
            for row in rows:
                item = dict(row)
                item["normalized_result"] = _decode(
                    item.pop("normalized_result_json"), None,
                )
                result.append(item)
            return result

    def create_benchmark_run(
        self, *, run_id: str, client_request_id: str, request_digest: str,
        name: str, mode: str, config: Mapping[str, object],
        schedule: Iterable[Mapping[str, object]],
    ) -> dict[str, object]:
        if mode not in {"mixed_arena", "paired_regression"}:
            raise ValueError("unsupported benchmark mode")
        config_json = _json(dict(config))
        items: list[tuple[int, str, str | None, int, str]] = []
        for index, value in enumerate(schedule):
            row = dict(value)
            if set(row) != {"scenario_id", "pair_id", "block_index", "assignment"}:
                raise ValueError("benchmark schedule item fields do not match schema")
            scenario_id, pair_id = row["scenario_id"], row["pair_id"]
            block_index, assignment = row["block_index"], row["assignment"]
            if type(scenario_id) is not str or not scenario_id:
                raise ValueError("scenario_id is required")
            if pair_id is not None and (type(pair_id) is not str or not pair_id):
                raise ValueError("pair_id must be a string or null")
            if type(block_index) is not int or block_index < 0:
                raise ValueError("block_index must be a non-negative integer")
            if not isinstance(assignment, Mapping):
                raise ValueError("assignment must be an object")
            items.append((index, scenario_id, pair_id, block_index, _json(dict(assignment))))
        schedule_value = [
            {"item_index": index, "scenario_id": scenario, "pair_id": pair,
             "block_index": block, "assignment": json.loads(assignment)}
            for index, scenario, pair, block, assignment in items
        ]
        return self._write(
            self._create_benchmark_run, run_id, client_request_id,
            request_digest, name, mode, config_json,
            _digest_json(schedule_value), items,
        )

    def _create_benchmark_run(
        self, run_id: str, client_request_id: str, request_digest: str,
        name: str, mode: str, config_json: str, schedule_digest: str,
        items: list[tuple[int, str, str | None, int, str]],
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM benchmark_runs WHERE client_request_id=?",
                (client_request_id,),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise CommitConflict("client_request_id has conflicting benchmark content")
                connection.rollback()
                return {**self._benchmark_run_row(existing), "replayed": True}
            now = _now()
            connection.execute(
                "INSERT INTO benchmark_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, client_request_id, request_digest, name, mode, "draft",
                 config_json, schedule_digest, now, now),
            )
            for index, scenario, pair, block, assignment_json in items:
                connection.execute(
                    "INSERT INTO benchmark_items VALUES (?,?,?,?,?,?,NULL,'pending',NULL,?,?)",
                    (run_id, index, scenario, pair, block, assignment_json, now, now),
                )
            row = connection.execute(
                "SELECT * FROM benchmark_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            connection.commit()
            return {**self._benchmark_run_row(row), "replayed": False}
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _benchmark_run_row(row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        return result

    @staticmethod
    def _benchmark_item_row(row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        result["assignment"] = json.loads(result.pop("assignment_json"))
        return result

    def get_benchmark_run(self, run_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            return None if row is None else self._benchmark_run_row(row)

    def list_benchmark_runs(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            return [self._benchmark_run_row(row) for row in connection.execute(
                "SELECT * FROM benchmark_runs ORDER BY created_at,run_id"
            )]

    def list_benchmark_items(self, run_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            return [self._benchmark_item_row(row) for row in connection.execute(
                "SELECT * FROM benchmark_items WHERE run_id=? ORDER BY item_index",
                (run_id,),
            )]

    def get_benchmark_item(
        self, run_id: str, item_index: int,
    ) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_items WHERE run_id=? AND item_index=?",
                (run_id, item_index),
            ).fetchone()
            return None if row is None else self._benchmark_item_row(row)

    def interrupt_running_benchmarks(self) -> list[str]:
        return self._write(self._interrupt_running_benchmarks)

    def _interrupt_running_benchmarks(self) -> list[str]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            run_ids = [str(row[0]) for row in connection.execute(
                "SELECT run_id FROM benchmark_runs "
                "WHERE status IN ('running','pausing') ORDER BY run_id"
            )]
            if not run_ids:
                connection.rollback()
                return []
            placeholders = ",".join("?" for _ in run_ids)
            connection.execute(
                f"UPDATE benchmark_runs SET status='interrupted',updated_at=? "
                f"WHERE run_id IN ({placeholders})",
                (_now(), *run_ids),
            )
            connection.commit()
            return run_ids
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def cancel_benchmark_items(self, run_id: str) -> None:
        self._write(self._cancel_benchmark_items, run_id)

    def _cancel_benchmark_items(self, run_id: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE benchmark_items SET status='cancelled',"
                "terminal_reason='cancelled',updated_at=? "
                "WHERE run_id=? AND status='running'",
                (now, run_id),
            )
            connection.execute(
                "UPDATE benchmark_items SET status='cancelled',"
                "terminal_reason='cancelled_before_start',updated_at=? "
                "WHERE run_id=? AND status='pending'",
                (now, run_id),
            )

    def transition_benchmark(
        self, run_id: str, *, expected: tuple[str, ...], target: str,
    ) -> dict[str, object]:
        return self._write(
            self._transition_benchmark, run_id, expected, target,
        )

    def _transition_benchmark(
        self, run_id: str, expected: tuple[str, ...], target: str,
    ) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = str(row["status"])
            if current != target:
                if current not in expected:
                    raise InvalidExecutionTransition(
                        f"cannot transition benchmark from {current} to {target}"
                    )
                connection.execute(
                    "UPDATE benchmark_runs SET status=?,updated_at=? WHERE run_id=?",
                    (target, _now(), run_id),
                )
                row = connection.execute(
                    "SELECT * FROM benchmark_runs WHERE run_id=?", (run_id,),
                ).fetchone()
            return self._benchmark_run_row(row)

    def claim_next_benchmark_item(self, run_id: str) -> dict[str, object] | None:
        return self._write(self._claim_next_benchmark_item, run_id)

    def _claim_next_benchmark_item(self, run_id: str) -> dict[str, object] | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM benchmark_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            if run["status"] != "running":
                connection.rollback()
                return None
            row = connection.execute(
                "SELECT * FROM benchmark_items WHERE run_id=? AND status='pending' "
                "ORDER BY item_index LIMIT 1", (run_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            now = _now()
            connection.execute(
                "UPDATE benchmark_items SET status='running',updated_at=? "
                "WHERE run_id=? AND item_index=?",
                (now, run_id, row["item_index"]),
            )
            updated = connection.execute(
                "SELECT * FROM benchmark_items WHERE run_id=? AND item_index=?",
                (run_id, row["item_index"]),
            ).fetchone()
            connection.commit()
            return self._benchmark_item_row(updated)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def attach_benchmark_game(self, run_id: str, item_index: int, game_id: str) -> None:
        self._write(self._attach_benchmark_game, run_id, item_index, game_id)

    def _attach_benchmark_game(self, run_id: str, item_index: int, game_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE benchmark_items SET game_id=?,updated_at=? WHERE run_id=? "
                "AND item_index=? AND status='running' AND game_id IS NULL",
                (game_id, _now(), run_id, item_index),
            )
            if cursor.rowcount != 1:
                raise CommitConflict("benchmark item cannot attach game")

    def finish_benchmark_item(
        self, run_id: str, item_index: int, *, status: str,
        terminal_reason: str | None,
    ) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid benchmark item terminal status")
        self._write(
            self._finish_benchmark_item, run_id, item_index, status,
            terminal_reason,
        )

    def _finish_benchmark_item(
        self, run_id: str, item_index: int, status: str,
        terminal_reason: str | None,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE benchmark_items SET status=?,terminal_reason=?,updated_at=? "
                "WHERE run_id=? AND item_index=? AND status='running'",
                (status, terminal_reason, _now(), run_id, item_index),
            )
            if cursor.rowcount != 1:
                raise CommitConflict("benchmark item is not running")

    def save_benchmark_report(
        self, run_id: str, *, metric_version: str, input_digest: str,
        report: Mapping[str, object],
    ) -> None:
        self._write(
            self._save_benchmark_report, run_id, metric_version, input_digest,
            _json(dict(report)),
        )

    def _save_benchmark_report(
        self, run_id: str, metric_version: str, input_digest: str,
        report_json: str,
    ) -> None:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT report_json FROM benchmark_reports WHERE run_id=? "
                "AND metric_version=? AND input_digest=?",
                (run_id, metric_version, input_digest),
            ).fetchone()
            if existing is not None:
                if existing["report_json"] != report_json:
                    raise CommitConflict("benchmark report has conflicting content")
                return
            connection.execute(
                "INSERT INTO benchmark_reports VALUES (?,?,?,?,?)",
                (run_id, metric_version, input_digest, report_json, _now()),
            )

    def get_benchmark_report(
        self, run_id: str, *, metric_version: str, input_digest: str,
    ) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_reports WHERE run_id=? AND metric_version=? "
                "AND input_digest=?", (run_id, metric_version, input_digest),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["report"] = json.loads(result.pop("report_json"))
            return result

    def get_latest_benchmark_report(self, run_id: str) -> dict[str, object] | None:
        """Return the newest cached report without recomputing benchmark facts."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_reports WHERE run_id=? "
                "ORDER BY generated_at DESC, rowid DESC LIMIT 1", (run_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["report"] = json.loads(result.pop("report_json"))
            return result

    def release_benchmark_game(self, run_id: str, game_id: str) -> None:
        self._write(self._release_benchmark_game, run_id, game_id)

    def _release_benchmark_game(self, run_id: str, game_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT benchmark_run_id,deleted_at FROM games WHERE game_id=?",
                (game_id,),
            ).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise KeyError(game_id)
            if row["benchmark_run_id"] != run_id:
                raise GameReferencedByBenchmark(str(row["benchmark_run_id"] or "benchmark"))
            now = _now()
            connection.execute(
                "UPDATE benchmark_items SET game_id=NULL,status='cancelled',"
                "terminal_reason='game_deleted',updated_at=? "
                "WHERE run_id=? AND game_id=?",
                (now, run_id, game_id),
            )
            connection.execute(
                "UPDATE games SET deleted_at=?,updated_at=?,benchmark_run_id=NULL "
                "WHERE game_id=?",
                (now, now, game_id),
            )
            self._purge_game_payload(connection, game_id)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_benchmark_reports(self, run_id: str) -> None:
        self._write(self._delete_benchmark_reports, run_id)

    def _delete_benchmark_reports(self, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM benchmark_reports WHERE run_id=?", (run_id,),
            )

    def delete_benchmark_run(self, run_id: str) -> None:
        self._write(self._delete_benchmark_run, run_id)

    def _delete_benchmark_run(self, run_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM benchmark_reports WHERE run_id=?", (run_id,),
            )
            connection.execute(
                "DELETE FROM benchmark_items WHERE run_id=?", (run_id,),
            )
            cursor = connection.execute(
                "DELETE FROM benchmark_runs WHERE run_id=?", (run_id,),
            )
            if cursor.rowcount != 1:
                raise KeyError(run_id)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
