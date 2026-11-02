"""Startup restores terminal archives as summaries and only decodes live work.

A completed game is browse-only: its pipeline runtime and point journal are
never touched again. Decoding them kept startup proportional to every finished
archive (tens of seconds once a few hundred existed). Resumable games, and only
those, keep the full decode so corruption is still caught at boot.
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
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.persistence.checkpoint_codec import (
    CHECKPOINT_VERSION, CheckpointCodec,
)
from app.persistence.repository import GameRepository
from app.roles.registry import builtin_registry
from app.services.game_service import GameService


def _state(game_id: str, *, phase: GamePhase = GamePhase.GAME_OVER) -> GameState:
    return GameState(
        game_id=game_id,
        phase=phase,
        round_number=2,
        config=GameConfig(role_counts={"wolf-killer-villager": 2}),
        players={
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-werewolf", "werewolf", is_alive=False),
        },
        win_result={"winning_camp": "good", "reason": "all_wolves_dead"},
    )


def _document(game_id: str) -> dict:
    codec = CheckpointCodec(builtin_registry.freeze())
    return codec.encode(_state(game_id), orchestration={})


def _insert_checkpoint(repository: GameRepository, game_id: str, document: dict) -> None:
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO game_checkpoints VALUES (?,?,?,?,?,?)",
            (
                game_id, 0, CHECKPOINT_VERSION, raw,
                hashlib.sha256(raw.encode("utf-8")).hexdigest(), "now",
            ),
        )


def _repository(tmp_path, game_id: str, status: str, *, document: dict | None = None) -> GameRepository:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id=game_id, name=game_id,
        config={"role_counts": {"wolf-killer-villager": 2}},
        execution_status=status, source="native", model_snapshot=[],
    )
    if document is not None:
        _insert_checkpoint(repository, game_id, document)
    return repository


def _write_log(tmp_path, game_id: str) -> None:
    game_dir = tmp_path / "games" / game_id
    game_dir.mkdir(parents=True)
    lines = [
        {
            "operation": "role_init",
            "data": {"players": {
                "1": {"role": "wolf-killer-villager", "camp": "good", "is_alive": True},
                "2": {"role": "wolf-killer-villager", "camp": "good", "is_alive": True},
            }},
        },
        {"operation": "game_over", "data": {"winner": "good", "reason": "all_wolves_dead"}},
    ]
    (game_dir / "game.log").write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )


def _service(tmp_path, repository: GameRepository) -> GameService:
    return GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )


def _close(service: GameService, repository: GameRepository) -> None:
    asyncio.run(service.aclose())
    repository.close()


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_terminal_archives_are_restored_as_summaries(tmp_path, monkeypatch, status):
    repository = _repository(tmp_path, "terminal", status, document=_document("terminal"))

    def _fail(self, document):
        raise AssertionError("terminal archives must not use the full decode")

    monkeypatch.setattr(CheckpointCodec, "decode", _fail)
    service = _service(tmp_path, repository)
    try:
        state = service.get_game_state("terminal")
        assert state is not None
        assert state.game_id == "terminal"
        assert not hasattr(state, "_pipeline_runtime")
        public = state.get_public_state()
        assert public["phase"] == "game_over"
        assert public["win_result"] == {"winning_camp": "good", "reason": "all_wolves_dead"}
        assert len(public["players"]) == 2
    finally:
        _close(service, repository)


@pytest.mark.parametrize(
    "status",
    ["running", "paused", "interrupted", "failed", "recovery_blocked", "unexpected"],
)
def test_resumable_and_unknown_statuses_keep_the_full_decode(tmp_path, monkeypatch, status):
    repository = _repository(tmp_path, "live", status, document=_document("live"))

    def _fail(self, document):
        raise AssertionError("only terminal archives may use the summary decode")

    monkeypatch.setattr(CheckpointCodec, "decode_summary", _fail)
    service = _service(tmp_path, repository)
    try:
        state = service.get_game_state("live")
        assert state is not None
        assert hasattr(state, "_pipeline_runtime")
        assert state._pipeline_runtime.revision == 0
    finally:
        _close(service, repository)


def test_a_corrupt_terminal_summary_is_skipped_without_quarantine(
    tmp_path, caplog,
):
    repository = _repository(tmp_path, "broken", "completed", document={})
    with caplog.at_level(logging.ERROR, logger="app.services.game_service"):
        service = _service(tmp_path, repository)
    try:
        assert service.get_game_state("broken") is None
        record = repository.get_game("broken")
        assert record["execution_status"] == "completed"
        assert record["recovery_block_code"] is None
        assert [r for r in caplog.records if r.levelno >= logging.ERROR]
    finally:
        _close(service, repository)


def test_a_missing_checkpoint_falls_back_to_the_jsonl_manifest(tmp_path):
    repository = _repository(tmp_path, "legacy", "completed")
    _write_log(tmp_path, "legacy")

    service = _service(tmp_path, repository)
    try:
        state = service.get_game_state("legacy")
        assert state is not None
        assert state.phase is GamePhase.GAME_OVER
        assert state.win_result["winning_camp"] == "good"
        assert len(state.players) == 2
    finally:
        _close(service, repository)


def test_manifest_entries_for_native_games_are_not_rebuilt(tmp_path, monkeypatch):
    repository = _repository(tmp_path, "native", "completed", document=_document("native"))
    # The manifest lists the same archive; without the skip this path would
    # re-read its game.log and rebuild a state that is immediately overwritten.
    _write_log(tmp_path, "native")

    def _fail(self, game_id, meta):
        raise AssertionError("a native state must not be rebuilt from game.log")

    monkeypatch.setattr(GameService, "_reconstruct_state", _fail)
    service = _service(tmp_path, repository)
    try:
        state = service.get_game_state("native")
        assert state is not None
        assert not hasattr(state, "_pipeline_runtime")
    finally:
        _close(service, repository)


def test_a_legacy_only_archive_is_still_reconstructed(tmp_path):
    repository = GameRepository(tmp_path)
    _write_log(tmp_path, "jsonl-only")

    service = _service(tmp_path, repository)
    try:
        state = service.get_game_state("jsonl-only")
        assert state is not None
        assert state.phase is GamePhase.GAME_OVER
    finally:
        _close(service, repository)


def test_a_manifest_entry_that_is_not_finished_is_not_reconstructed(tmp_path):
    """Only completed archives are rebuilt from game.log; an in-progress JSONL
    directory must not appear in the lobby as a restored game."""
    repository = GameRepository(tmp_path)
    game_dir = tmp_path / "games" / "unfinished"
    game_dir.mkdir(parents=True)
    (game_dir / "game.log").write_text(
        json.dumps({
            "operation": "role_init",
            "data": {"players": {
                "1": {"role": "wolf-killer-villager", "camp": "good", "is_alive": True},
            }},
        }) + "\n",
        encoding="utf-8",
    )

    service = _service(tmp_path, repository)
    try:
        assert service.get_game_state("unfinished") is None
    finally:
        _close(service, repository)
