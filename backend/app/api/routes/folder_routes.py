from fastapi import APIRouter, HTTPException

from app.api.schemas import FolderItem, FolderListResponse, FolderNameRequest
from app.persistence.repository import CommitConflict


router = APIRouter(prefix="/api/folders", tags=["folders"])


def get_service():
    from app.main import game_service
    return game_service


def _folder_item(record: dict) -> FolderItem:
    return FolderItem(
        folder_id=str(record["folder_id"]),
        name=str(record["name"]),
        game_count=int(record["game_count"]),
        created_at=str(record["created_at"]),
        updated_at=str(record["updated_at"]),
    )


def _map_folder_error(error: Exception) -> HTTPException:
    if isinstance(error, CommitConflict):
        return HTTPException(409, {"code": "folder_name_conflict", "message": str(error)})
    if isinstance(error, RuntimeError):
        return HTTPException(503, {"code": "folder_persistence_unavailable", "message": str(error)})
    if isinstance(error, KeyError):
        return HTTPException(404, {"code": "folder_not_found", "message": "Folder not found"})
    return HTTPException(422, {"code": "invalid_folder", "message": str(error)})


@router.get("", response_model=FolderListResponse)
async def list_folders():
    try:
        folders = get_service().list_folders()
    except RuntimeError as error:
        raise _map_folder_error(error) from None
    return FolderListResponse(folders=[_folder_item(item) for item in folders])


@router.post("", response_model=FolderItem, status_code=201)
async def create_folder(req: FolderNameRequest):
    try:
        return _folder_item(get_service().create_folder(req.name))
    except (RuntimeError, CommitConflict, ValueError) as error:
        raise _map_folder_error(error) from None


@router.patch("/{folder_id}", response_model=FolderItem)
async def rename_folder(folder_id: str, req: FolderNameRequest):
    try:
        return _folder_item(get_service().rename_folder(folder_id, req.name))
    except (RuntimeError, CommitConflict, KeyError, ValueError) as error:
        raise _map_folder_error(error) from None


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(folder_id: str):
    try:
        get_service().delete_folder(folder_id)
    except (RuntimeError, KeyError) as error:
        raise _map_folder_error(error) from None
