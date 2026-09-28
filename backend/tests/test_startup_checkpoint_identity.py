"""A checkpoint frozen under an older role registry must stay resumable.

``registry.digest`` is a *frozen* identity: journal keys, request tokens and the
role-resource setup marker are all stamped with the digest the game was created
under, so a later role declaration cannot re-key work the game already
committed. Startup must therefore register such a game and leave its status
alone instead of quarantining it as corrupt. Genuinely corrupt checkpoints —
and documents whose identity disagrees with their own journal — still log an
exception and get a precise block code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3

import pytest

from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.persistence.checkpoint_codec import CheckpointError
from app.persistence.repository import GameRepository
from app.services.game_service import GameService

OLDER_REGISTRY = "b" * 64


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


def _rewrite_identity(repository: GameRepository, game_id: str, identity: str) -> None:
    """Re-stamp a stored checkpoint as if it had been frozen earlier."""
    row = repository.load_checkpoint(game_id)
    assert row is not None
    document = row["checkpoint"]
    document["registry_digest"] = identity
    document["state"]["registry_digest"] = identity
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE game_checkpoints SET checkpoint_json=?,checkpoint_digest=? WHERE game_id=?",
            (raw, hashlib.sha256(raw.encode()).hexdigest(), game_id),
        )


@pytest.mark.asyncio
async def test_a_checkpoint_from_an_older_registry_is_registered_not_quarantined(
    tmp_path, monkeypatch, caplog,
) -> None:
    import app.services.game_service as service_module
    from app.core.game_engine import GameEngine

    class FakeClient:
        def __init__(self, *, config) -> None:
            self.config = config

        async def aclose(self) -> None:
            pass

    async def stop_after_initial_checkpoint(self) -> None:
        self._running = False

    monkeypatch.setattr(service_module, "LLMClient", FakeClient)
    monkeypatch.setattr(GameEngine, "_game_loop", stop_after_initial_checkpoint)
    repository = GameRepository(tmp_path)
    first = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    game_id = await first.create_game(role_counts={"wolf-killer-villager": 2})
    await first._tasks[game_id]
    _rewrite_identity(repository, game_id, OLDER_REGISTRY)
    repository.transition_execution(
        game_id, expected=("running",), target="interrupted",
    )

    with caplog.at_level(logging.WARNING, logger="app.services.game_service"):
        second = GameService(
            WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
        )
    try:
        assert not [
            record for record in caplog.records if record.levelno >= logging.ERROR
        ], "an older registry is not corruption"
        assert second.get_game_state(game_id) is not None
        record = repository.get_game(game_id)
        assert record["execution_status"] == "interrupted"
        assert record["recovery_block_code"] is None
        assert second.get_execution_info(game_id)["recoverable"] is True
    finally:
        await first.aclose()
        await second.aclose()
        repository.close()


@pytest.mark.parametrize(
    "message,code",
    [
        ("boom", "checkpoint_corrupt"),
        ("unsupported checkpoint version 99", "checkpoint_version_unsupported"),
        ("journal registry mismatch", "checkpoint_corrupt"),
    ],
)
def test_genuine_decode_error_still_logs_exception(
    tmp_path, monkeypatch, caplog, message, code,
) -> None:
    repository = _make_repo_with_checkpoints(tmp_path, [("broken-1", "paused")])

    def _raise_boom(self, document):
        raise CheckpointError(message)

    monkeypatch.setattr(
        "app.services.game_service.CheckpointCodec.decode", _raise_boom
    )
    with caplog.at_level(logging.DEBUG, logger="app.services.game_service"):
        service = GameService(
            WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository
        )
    try:
        assert [
            record for record in caplog.records if record.levelno >= logging.ERROR
        ], "genuine corruption must still log an exception"
        assert repository.get_game("broken-1")["recovery_block_code"] == code
    finally:
        asyncio.run(service.aclose())
        repository.close()


@pytest.mark.parametrize("status,expected_status,expected_code", [
    ("completed", "completed", None),
    ("paused", "recovery_blocked", "checkpoint_corrupt"),
])
def test_unexpected_decode_error_still_logs_exception(
    tmp_path, monkeypatch, caplog, status, expected_status, expected_code,
) -> None:
    repository = _make_repo_with_checkpoints(tmp_path, [("weird-1", status)])

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
        assert [record for record in caplog.records if record.levelno >= logging.ERROR]
        record = repository.get_game("weird-1")
        assert record["execution_status"] == expected_status
        assert record["recovery_block_code"] == expected_code
    finally:
        asyncio.run(service.aclose())
        repository.close()
