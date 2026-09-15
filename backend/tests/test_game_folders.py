from __future__ import annotations

import sqlite3

import pytest

from app.persistence.repository import CommitConflict, GameRepository, _normalize_folder_name


def test_v1_database_migrates_to_folder_schema(tmp_path) -> None:
    path = tmp_path / "wolfkiller.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations VALUES (1, 'now');
        CREATE TABLE games(game_id TEXT PRIMARY KEY);
        """
    )
    connection.commit()
    connection.close()

    repository = GameRepository(tmp_path)
    try:
        assert repository.schema_version() == 2
        assert {"game_folders", "game_folder_items"} <= repository.table_names()
    finally:
        repository.close()


def test_folder_crud_counts_and_unique_names(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        created = repository.create_folder("  九月对局  ")
        assert created["name"] == "九月对局"
        assert created["folder_id"]
        listed = repository.list_folders()
        assert listed == [{
            "folder_id": created["folder_id"],
            "name": "九月对局",
            "game_count": 0,
            "created_at": created["created_at"],
            "updated_at": created["updated_at"],
        }]
        with pytest.raises(ValueError, match="blank"):
            repository.create_folder("   ")
        with pytest.raises(ValueError, match="blank"):
            _normalize_folder_name(None)
        with pytest.raises(ValueError, match="too long"):
            repository.create_folder("x" * 51)
        with pytest.raises(CommitConflict, match="already exists"):
            repository.create_folder("九月对局")
        other = repository.create_folder("另一夹")
        with pytest.raises(CommitConflict, match="already exists"):
            repository.rename_folder(other["folder_id"], "九月对局")
        repository.delete_folder(other["folder_id"])
        with pytest.raises(ValueError, match="game_id"):
            repository.set_game_folder("", folder_id=created["folder_id"])
        with pytest.raises(ValueError, match="folder_id"):
            repository.set_game_folder("game", "")
        repository.rename_folder(created["folder_id"], "归档")
        renamed = repository.get_folder(created["folder_id"])
        assert renamed is not None
        assert renamed["name"] == "归档"
        with pytest.raises(KeyError, match="missing"):
            repository.rename_folder("missing", "x")
        with pytest.raises(KeyError, match="missing"):
            repository.delete_folder("missing")
        repository.delete_folder(created["folder_id"])
        assert repository.list_folders() == []
        assert repository.get_folder(created["folder_id"]) is None
    finally:
        repository.close()


def test_legacy_game_id_can_be_filed_and_folder_delete_unfiles(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        folder = repository.create_folder("旧档")
        repository.set_game_folder("legacy-jsonl", folder["folder_id"])
        assert repository.get_game_folder("legacy-jsonl") == folder["folder_id"]
        assert repository.list_folders()[0]["game_count"] == 1
        with pytest.raises(KeyError, match="missing"):
            repository.set_game_folder("legacy-jsonl", "missing")
        repository.set_game_folder("legacy-jsonl", None)
        assert repository.get_game_folder("legacy-jsonl") is None
        repository.set_game_folder("legacy-jsonl", folder["folder_id"])
        repository.delete_folder(folder["folder_id"])
        assert repository.get_game_folder("legacy-jsonl") is None
    finally:
        repository.close()


def test_mark_game_deleted_drops_folder_membership(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        repository.create_game(
            game_id="game", name="game", config={}, execution_status="completed",
            source="native", model_snapshot=[],
        )
        folder = repository.create_folder("收纳")
        repository.set_game_folder("game", folder["folder_id"])
        repository.mark_game_deleted("game")
        assert repository.get_game_folder("game") is None
        assert repository.list_folders()[0]["game_count"] == 0
    finally:
        repository.close()
