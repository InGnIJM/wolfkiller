"""Startup must not spew a traceback per stale-registry checkpoint.

Old games whose checkpoint was frozen under a previous role registry digest
can never resume (registry.digest changes with every new role). They should
be quarantined quietly with one summary warning, not one ERROR+traceback
per game. Genuinely corrupt checkpoints must still log an exception.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3

from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.persistence.checkpoint_codec import CheckpointError
from app.persistence.repository import GameRepository
from app.services.game_service import GameService


def _insert_checkpoint(repository: GameRepository, game_id: str, payload: dict) -> None:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO game_checkpoints VALUES (?,?,?,?,?,?)",
            (game_id, 0, 1, raw, hashlib.sha256(raw.encode()).hexdigest(), "now"),
        )


def _make_repo_with_checkpoints(tmp_path, games: list[tuple[str, str]]) -> GameRepository:
    repository = GameRepository(tmp_path)
    for game_id, status in games:
        repository.create_game(
            game_id=game_id, name=game_id, config={},
            execution_status=status, source="native", model_snapshot=[],
        )
        _insert_checkpoint(repository, game_id, {"checkpoint_version": 1})
    return repository


def test_registry_mismatch_logs_one_warning_without_traceback(
    tmp_path, monkeypatch, caplog
) -> None:
    game_ids = ["stale-1", "stale-2"]
    repository = _make_repo_with_checkpoints(
        tmp_path, [(game_ids[0], "interrupted"), (game_ids[1], "failed"),
                   ("stale-completed", "completed")],
    )

    def _raise_mismatch(self, document):
        raise CheckpointError("registry mismatch")

    monkeypatch.setattr(
        "app.services.game_service.CheckpointCodec.decode", _raise_mismatch
    )
    with caplog.at_level(logging.WARNING, logger="app.services.game_service"):
        service = GameService(
            WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository
        )
    try:
        assert not [
            r for r in caplog.records if r.levelno >= logging.ERROR
        ], "stale-registry games must not log ERROR with traceback"
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "registry" in r.getMessage().lower()
        ]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "3" in message
        for game_id in game_ids:
            record = repository.get_game(game_id)
            assert record["execution_status"] == "recovery_blocked"
            assert record["recovery_block_code"] == "checkpoint_corrupt"
        # terminal but non-recoverable statuses are left untouched
        assert repository.get_game("stale-completed")["execution_status"] == (
            "completed"
        )
    finally:
        asyncio.run(service.aclose())
        repository.close()


def test_genuine_decode_error_still_logs_exception(
    tmp_path, monkeypatch, caplog
) -> None:
    repository = _make_repo_with_checkpoints(tmp_path, [("broken-1", "paused")])

    def _raise_boom(self, document):
        raise CheckpointError("boom")

    monkeypatch.setattr(
        "app.services.game_service.CheckpointCodec.decode", _raise_boom
    )
    with caplog.at_level(logging.DEBUG, logger="app.services.game_service"):
        service = GameService(
            WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository
        )
    try:
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "genuine corruption must still log an exception"
        assert repository.get_game("broken-1")["recovery_block_code"] == (
            "checkpoint_corrupt"
        )
    finally:
        asyncio.run(service.aclose())
        repository.close()


def test_unexpected_decode_error_still_logs_exception(
    tmp_path, monkeypatch, caplog
) -> None:
    repository = _make_repo_with_checkpoints(tmp_path, [("weird-1", "completed")])

    def _raise_weird(self, document):
        raise RuntimeError("weird")

    monkeypatch.setattr(
        "app.services.game_service.CheckpointCodec.decode", _raise_weird
    )
    with caplog.at_level(logging.DEBUG, logger="app.services.game_service"):
        service = GameService(
            WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository
        )
    try:
        assert [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert repository.get_game("weird-1")["execution_status"] == "completed"
    finally:
        asyncio.run(service.aclose())
        repository.close()
