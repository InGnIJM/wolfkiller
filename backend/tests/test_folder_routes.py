from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes import folder_routes, game_routes
from app.api.schemas import AssignFolderRequest, BatchGameIdsRequest, BatchMoveRequest, FolderNameRequest
from app.persistence.repository import CommitConflict, GameReferencedByBenchmark


def test_batch_game_ids_reject_blank_entries():
    with pytest.raises(ValueError, match="non-empty"):
        BatchGameIdsRequest(game_ids=["g1", "  "])


def test_get_folder_service_reads_main(monkeypatch):
    service = object()
    monkeypatch.setitem(__import__("sys").modules, "app.main", SimpleNamespace(game_service=service))
    assert folder_routes.get_service() is service


@pytest.mark.asyncio
async def test_folder_crud_routes(monkeypatch):
    folder = {
        "folder_id": "f1", "name": "九月", "game_count": 0,
        "created_at": "now", "updated_at": "now",
    }
    service = MagicMock()
    service.list_folders.return_value = [folder]
    service.create_folder.return_value = folder
    service.rename_folder.return_value = {**folder, "name": "归档"}
    monkeypatch.setattr(folder_routes, "get_service", lambda: service)

    listed = await folder_routes.list_folders()
    assert listed.folders[0].name == "九月"
    created = await folder_routes.create_folder(FolderNameRequest(name="九月"))
    assert created.folder_id == "f1"
    renamed = await folder_routes.rename_folder("f1", FolderNameRequest(name="归档"))
    assert renamed.name == "归档"
    assert await folder_routes.delete_folder("f1") is None
    service.delete_folder.assert_called_once_with("f1")


@pytest.mark.asyncio
@pytest.mark.parametrize("method,error,status,code", [
    ("list_folders", RuntimeError("unavailable"), 503, "folder_persistence_unavailable"),
    ("create_folder", RuntimeError("unavailable"), 503, "folder_persistence_unavailable"),
    ("create_folder", CommitConflict("folder name already exists"), 409, "folder_name_conflict"),
    ("create_folder", ValueError("too long"), 422, "invalid_folder"),
    ("rename_folder", CommitConflict("folder name already exists"), 409, "folder_name_conflict"),
    ("rename_folder", RuntimeError("unavailable"), 503, "folder_persistence_unavailable"),
    ("delete_folder", RuntimeError("unavailable"), 503, "folder_persistence_unavailable"),
    ("delete_folder", KeyError("missing"), 404, "folder_not_found"),
])
async def test_folder_routes_map_errors(monkeypatch, method, error, status, code):
    service = MagicMock()
    getattr(service, method).side_effect = error
    monkeypatch.setattr(folder_routes, "get_service", lambda: service)
    if method == "list_folders":
        call = folder_routes.list_folders()
    elif method == "create_folder":
        call = folder_routes.create_folder(FolderNameRequest(name="九月"))
    elif method == "rename_folder":
        call = folder_routes.rename_folder("f1", FolderNameRequest(name="归档"))
    else:
        call = folder_routes.delete_folder("f1")
    with pytest.raises(HTTPException) as caught:
        await call
    assert caught.value.status_code == status
    assert caught.value.detail["code"] == code


@pytest.mark.asyncio
async def test_assign_and_batch_game_folder_routes(monkeypatch):
    service = MagicMock()
    service.batch_delete_games = AsyncMock(return_value={
        "deleted": ["g1"],
        "failed": [{"game_id": "g2", "code": "not_found", "message": "Game not found"}],
    })
    service.batch_move_games.return_value = {
        "moved": ["g1"],
        "failed": [{"game_id": "g2", "code": "not_found", "message": "Game or folder not found"}],
    }
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    deleted = await game_routes.batch_delete_games(BatchGameIdsRequest(game_ids=["g1", "g2"]))
    assert deleted.deleted == ["g1"]
    moved = await game_routes.batch_move_games(
        BatchMoveRequest(game_ids=["g1", "g2"], folder_id="f1"),
    )
    assert moved.moved == ["g1"]
    assert await game_routes.assign_game_folder("g1", AssignFolderRequest(folder_id=None)) is None
    service.assign_game_folder.assert_called_once_with("g1", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [
    (KeyError("g1"), 404),
    (GameReferencedByBenchmark("run-1"), 409),
    (RuntimeError("unavailable"), 503),
])
async def test_assign_game_folder_maps_errors(monkeypatch, error, status):
    service = MagicMock()
    service.assign_game_folder.side_effect = error
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.assign_game_folder("g1", AssignFolderRequest(folder_id="f1"))
    assert caught.value.status_code == status
