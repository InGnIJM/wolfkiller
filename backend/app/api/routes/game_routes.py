import asyncio
import os
from collections.abc import Mapping
from typing import Annotated, Any
from fastapi import APIRouter, HTTPException, Query, Response
from app.api import replay_diagnostics
from app.api.model_schemas import public_model_snapshot
from app.api.schemas import (
    AssignFolderRequest, BatchDeleteResponse, BatchGameIdsRequest, BatchMoveRequest,
    BatchMoveResponse, CreateGameRequest, CreateGameResponse,
    GameListResponse, GameListItem, GameDetailResponse, GameLogsResponse,
    GameMemoriesResponse, PlayerMemoryResponse, RenameGameRequest,
    AudienceEventPageResponse, AudienceSnapshotResponse, GameExecutionResponse,
    GenerateReviewRequest, ReviewStatusResponse,
)
from app.models.game import Camp
from app.persistence import recovery_compat
from app.persistence.repository import GameReferencedByBenchmark, InvalidExecutionTransition
from app.services.audience_event_service import (
    AudienceCursorAheadError,
    AudienceGameNotFoundError,
)
from app.services.game_service import GameService
# 旧档日志 → 封闭回放事件的投影整体迁到了 `services/legacy_replay.py`
# （导出功能也要消费同一份投影）；这里 re-export 让既有 `game_routes._*` 访问不变。
from app.services.legacy_replay import (
    _AUDIENCE_ACTION_OPTIONAL, _AUDIENCE_ACTION_SCHEMAS,
    _INTERNAL_AUDIENCE_ACTION_TYPES, _PUBLIC_DEATH_CAUSES, _PUBLIC_GAME_PHASES,
    _PUBLIC_WIN_REASONS, _PUBLIC_WINNING_CAMPS, _REASONING_EVENT_SCHEMA,
    _REASONING_EVENT_TYPES, _SHERIFF_RUN_CHOICES, _SHERIFF_SIDES,
    _SHERIFF_VOTE_KINDS, _SHERIFF_WITHDRAW_CHOICES, _is_int,
    _is_non_negative_int, _is_positive_int, _is_reasoning_text,
    _payload_matches, _public_audience_action_event, _public_conversation_event,
    _public_day_verdict_event, _public_operation_events, _public_reasoning_event,
    _public_replay_events, _public_role_skill_event, _public_timestamp,
    _read_jsonl, _timestamp,
)
from app.services.game_export import GameExportNotFoundError
from app.services.review_service import ReviewGameNotFoundError

router = APIRouter(prefix="/api/games", tags=["games"])

#: 丢弃计数与告警的实现放在 `api/replay_diagnostics.py`：诊断接口也要读它，而路由之间
#: 不该互相 import 私有名。旧名保留为别名，调用点与既有测试无需改动。
_DROPPED_REPLAY_ROWS = replay_diagnostics.ROWS

_LEGACY_ROLE_COUNT_FIELDS = {
    "num_werewolves": "wolf-killer-werewolf",
    "num_villagers": "wolf-killer-villager",
    "num_seers": "wolf-killer-seer",
    "num_witches": "wolf-killer-witch",
    "num_hunters": "wolf-killer-hunter",
}

# Resumable refusals that need the user to answer first: the game would continue
# under declarations that moved since it started.
_CONFIRMATION_CODES = frozenset({
    recovery_compat.ROLE_DECLARATION_DRIFT,
    recovery_compat.CONTRACT_DIGEST_DRIFT,
    recovery_compat.REGISTRY_IDENTITY_UNKNOWN,
})


def get_service() -> GameService:
    from app.main import game_service
    return game_service


def get_audience_service():
    from app.main import audience_event_service
    return audience_event_service


def get_review_service():
    from app.main import review_service
    return review_service


def get_export_service():
    from app.main import game_export_service
    return game_export_service


def _execution_info(
    service: GameService, game_id: str, state,
    record: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if record is not None:
        record_getter = getattr(service, "execution_info_from_record", None)
        value = record_getter(record) if callable(record_getter) else None
    else:
        getter = getattr(service, "get_execution_info", None)
        if callable(getter):
            try:
                value = getter(game_id)
            except KeyError:
                value = None
        else:
            value = None
    if isinstance(value, Mapping):
        return {
            "execution_status": str(value.get("execution_status", "running")),
            "recoverable": bool(value.get("recoverable", False)),
            "recovery_block_code": value.get("recovery_block_code"),
            "pause_code": value.get("pause_code"),
            "pause_reason": value.get("pause_reason"),
            "pause_detail": value.get("pause_detail"),
            "interruption_count": int(value.get("interruption_count", 0)),
            "benchmark_run_id": value.get("benchmark_run_id"),
        }
    phase = getattr(state, "phase", None)
    phase_str = getattr(phase, "value", None) or (phase if isinstance(phase, str) else None)
    if phase_str is None and isinstance(state, Mapping):
        phase_str = state.get("phase")
    completed = phase_str == "game_over"
    return {
        "execution_status": "completed" if completed else "running",
        "recoverable": False,
        "recovery_block_code": "legacy_archive",
        "pause_code": None,
        "pause_reason": None,
        "pause_detail": None,
        "interruption_count": 0,
        "benchmark_run_id": None,
    }


@router.post("", response_model=CreateGameResponse)
async def create_game(req: CreateGameRequest = CreateGameRequest()):
    service = get_service()
    assignments = (
        [item.model_dump() for item in req.model_assignments]
        if req.model_assignments is not None
        else None
    )
    comm_kwargs: dict[str, Any] = {}
    if (
        req.commentary_enabled
        or req.commentary_config_id is not None
        or req.commentary_model_id is not None
        or req.commentary_reasoning_effort is not None
    ):
        comm_kwargs = {
            "commentary_enabled": req.commentary_enabled,
            "commentary_config_id": req.commentary_config_id,
            "commentary_model_id": req.commentary_model_id,
            "commentary_reasoning_effort": req.commentary_reasoning_effort,
        }
    try:
        if req.role_counts is not None:
            game_id = await service.create_game(
                role_counts=req.role_counts, reveal_on_death=req.reveal_on_death,
                enable_sheriff=req.enable_sheriff,
                witch_self_save=req.witch_self_save,
                victory=req.victory,
                model_assignments=assignments,
                vote_model_config_id=req.vote_model_config_id,
                vote_model_id=req.vote_model_id,
                vote_reasoning_effort=req.vote_reasoning_effort,
                **comm_kwargs,
            )
        else:
            game_id = await service.create_game(
                num_werewolves=req.num_werewolves,
                num_villagers=req.num_villagers,
                num_seers=req.num_seers,
                num_witches=req.num_witches,
                num_hunters=req.num_hunters,
                reveal_on_death=req.reveal_on_death,
                enable_sheriff=req.enable_sheriff,
                witch_self_save=req.witch_self_save,
                victory=req.victory,
                model_assignments=assignments,
                vote_model_config_id=req.vote_model_config_id,
                vote_model_id=req.vote_model_id,
                vote_reasoning_effort=req.vote_reasoning_effort,
                **comm_kwargs,
            )
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    state = service.get_game_state(game_id)
    if state is None:
        raise HTTPException(404, "Game not found after creation")
    return CreateGameResponse(
        game_id=game_id,
        player_count=len(state.players),
        config={
            "role_counts": state.config.role_counts,
            "reveal_on_death": state.config.reveal_on_death,
            "enable_sheriff": state.config.enable_sheriff,
            **{
                field: state.config.role_counts.get(role_id, 0)
                for field, role_id in _LEGACY_ROLE_COUNT_FIELDS.items()
            },
        },
        model_snapshot=service.get_game_model_snapshot(game_id),
    )


def _list_item(
    service: GameService, game_id: str, state,
    record: Mapping[str, object] | None = None,
    folder_id: str | None = None,
) -> GameListItem:
    win = state.win_result.get("winning_camp") if state.win_result else None
    execution = _execution_info(service, game_id, state, record)
    name_getter = getattr(service, "display_name_from_record", None)
    if callable(name_getter):
        name = name_getter(game_id, record)
    else:
        name = service.get_display_name(game_id)
    return GameListItem(
        game_id=game_id,
        name=name,
        phase=state.phase.value,
        round_number=state.round_number,
        player_count=len(state.players),
        alive_count=len(state.alive_players()),
        winner=win,
        folder_id=folder_id,
        **execution,
    )


@router.get("", response_model=GameListResponse)
async def list_games(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    folder_id: Annotated[str | None, Query()] = None,
):
    service = get_service()
    # Two bulk reads replace per-game lookups: the list can hold hundreds of
    # games and each repository call opens its own connection.
    record_getter = getattr(service, "game_record_index", None)
    records: dict[str, Any] = (
        await asyncio.to_thread(record_getter) if callable(record_getter) else {}
    )
    folder_getter = getattr(service, "game_folder_index", None)
    folders: dict[str, str] = (
        await asyncio.to_thread(folder_getter) if callable(folder_getter) else {}
    )
    lobby_checker = getattr(service, "is_lobby_record", None)
    legacy_checker = getattr(service, "is_lobby_game", None)
    games = service.list_games()
    matching_games: list[tuple[str, Any, Any, str | None]] = []
    for g in games:
        record = records.get(g)
        if callable(lobby_checker):
            if not lobby_checker(record):
                continue
        elif callable(legacy_checker) and not legacy_checker(g):
            continue
        state = service.get_game_state(g)
        if state is None:
            continue
        cur_folder = folders.get(g)
        if folder_id is not None and folder_id != "all":
            if folder_id == "unfiled":
                if cur_folder is not None:
                    continue
            elif cur_folder != folder_id:
                continue
        matching_games.append((g, state, record, cur_folder))

    total = len(matching_games)
    start = (page - 1) * page_size
    end = start + page_size
    page_slice = matching_games[start:end]

    items = [
        _list_item(service, g, state, record, cur_folder)
        for g, state, record, cur_folder in page_slice
    ]
    return GameListResponse(games=items, total=total, page=page, page_size=page_size)


@router.post("/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_games(req: BatchGameIdsRequest):
    result = await get_service().batch_delete_games(req.game_ids)
    return BatchDeleteResponse.model_validate(result)


@router.post("/batch-move", response_model=BatchMoveResponse)
async def batch_move_games(req: BatchMoveRequest):
    result = get_service().batch_move_games(req.game_ids, req.folder_id)
    return BatchMoveResponse.model_validate(result)


@router.put("/{game_id}/folder", status_code=204)
async def assign_game_folder(game_id: str, req: AssignFolderRequest):
    service = get_service()
    try:
        service.assign_game_folder(game_id, req.folder_id)
    except KeyError:
        raise HTTPException(404, "Game or folder not found") from None
    except GameReferencedByBenchmark as error:
        raise HTTPException(409, {
            "code": "game_referenced_by_benchmark",
            "benchmark_run_id": str(error),
        }) from None
    except RuntimeError as error:
        raise HTTPException(503, {
            "code": "folder_persistence_unavailable",
            "message": str(error),
        }) from None


@router.patch("/{game_id}", response_model=GameListItem)
async def rename_game(game_id: str, req: RenameGameRequest):
    service = get_service()
    try:
        service.rename_game(game_id, req.name)
    except KeyError:
        raise HTTPException(404, "Game not found") from None
    state = service.get_game_state(game_id)
    if state is None:
        raise HTTPException(404, "Game not found")
    return _list_item(service, game_id, state)


@router.delete("/{game_id}", status_code=204)
async def delete_game(game_id: str):
    service = get_service()
    try:
        await service.delete_game(game_id)
    except KeyError:
        raise HTTPException(404, "Game not found") from None
    except GameReferencedByBenchmark as error:
        raise HTTPException(409, {
            "code": "game_referenced_by_benchmark",
            "benchmark_run_id": str(error),
        }) from None
    except OSError as error:
        raise HTTPException(500, "Failed to delete game archive") from error


@router.get("/{game_id}", response_model=GameDetailResponse)
async def get_game(game_id: str):
    service = get_service()
    state = service.get_game_state(game_id)
    if state is None:
        raise HTTPException(404, "Game not found")

    public_state = state.get_public_state()
    return GameDetailResponse(
        game_id=public_state["game_id"],
        phase=public_state["phase"],
        round_number=public_state["round_number"],
        reveal_on_death=public_state["reveal_on_death"],
        enable_sheriff=public_state.get("enable_sheriff", False),
        players=public_state["players"],
        sheriff=public_state["sheriff"],
        speeches=public_state["speeches"],
        death_history=public_state["death_history"],
        win_result=public_state["win_result"],
        model_snapshot=public_model_snapshot(service.get_game_model_snapshot(game_id)),
        **_execution_info(service, game_id, state),
    )


def _error(status_code: int, code: str, message: str, **details: object) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **details},
    )


async def _control_game(
    game_id: str, action: str, *, force: bool = False,
) -> GameExecutionResponse:
    service = get_service()
    method = getattr(service, f"{action}_game", None)
    if not callable(method):
        raise _error(409, "recovery_unavailable", "Game recovery is unavailable")
    try:
        if action == "pause":
            result = await method(game_id)
        else:
            result = await method(game_id, force=force)
    except KeyError:
        raise _error(404, "game_not_found", "Game not found") from None
    except InvalidExecutionTransition as error:
        raise _error(409, "invalid_execution_transition", str(error)) from None
    except ValueError as error:
        code = getattr(error, "code", None) or (str(error) if str(error) else "recovery_blocked")
        if " " in code:
            code = "recovery_blocked"
        details: dict[str, object] = {}
        if code == "game_managed_by_benchmark":
            getter = getattr(service, "get_execution_info", None)
            info = getter(game_id) if callable(getter) else None
            if isinstance(info, Mapping) and info.get("benchmark_run_id") is not None:
                details["benchmark_run_id"] = info["benchmark_run_id"]
        if code in _CONFIRMATION_CODES:
            # Resumable, but the game would continue under declarations that
            # changed since it started: the client must ask before forcing it.
            details["confirmation_required"] = True
        raise _error(409, code, str(error), **details) from None

    if not isinstance(result, Mapping):
        state = service.get_game_state(game_id)
        if state is None:
            raise _error(404, "game_not_found", "Game not found")
        result = _execution_info(service, game_id, state)
    return GameExecutionResponse(
        game_id=game_id,
        execution_status=str(result.get("execution_status", "running")),
        recoverable=bool(result.get("recoverable", False)),
        recovery_block_code=result.get("recovery_block_code"),
        pause_code=result.get("pause_code"),
        pause_reason=result.get("pause_reason"),
        pause_detail=result.get("pause_detail"),
        interruption_count=int(result.get("interruption_count", 0)),
        benchmark_run_id=result.get("benchmark_run_id"),
    )


@router.post("/{game_id}/pause", response_model=GameExecutionResponse)
async def pause_game(game_id: str):
    return await _control_game(game_id, "pause")


@router.post("/{game_id}/resume", response_model=GameExecutionResponse)
async def resume_game(game_id: str, force: Annotated[bool, Query()] = False):
    return await _control_game(game_id, "resume", force=force)


@router.post("/{game_id}/recover", response_model=GameExecutionResponse)
async def recover_game(game_id: str, force: Annotated[bool, Query()] = False):
    return await _control_game(game_id, "recover", force=force)


@router.get("/{game_id}/snapshot", response_model=AudienceSnapshotResponse)
async def get_game_snapshot(game_id: str):
    try:
        snapshot = get_audience_service().get_snapshot(game_id)
    except AudienceGameNotFoundError:
        raise _error(404, "game_not_found", "Game not found") from None
    if snapshot is None:
        raise _error(
            409, "snapshot_unavailable",
            "This game does not have an incremental audience snapshot",
        )
    service = get_service()
    state = snapshot["state"]
    exec_info = _execution_info(service, game_id, state)
    snapshot = {
        **snapshot,
        "state": {
            **state,
            "execution_status": exec_info["execution_status"],
            "recoverable": exec_info["recoverable"],
            "recovery_block_code": exec_info["recovery_block_code"],
            "pause_code": exec_info.get("pause_code"),
            "pause_reason": exec_info.get("pause_reason"),
            "pause_detail": exec_info.get("pause_detail"),
            "model_snapshot": public_model_snapshot(
                service.get_game_model_snapshot(game_id),
            ),
        },
    }
    return AudienceSnapshotResponse.model_validate(snapshot)


@router.get("/{game_id}/events", response_model=AudienceEventPageResponse)
async def get_game_events(
    game_id: str,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    through_seq: Annotated[int | None, Query(ge=0)] = None,
):
    try:
        page = get_audience_service().get_events(
            game_id, after_seq=after_seq, limit=limit,
            through_seq=through_seq,
        )
    except AudienceGameNotFoundError:
        raise _error(404, "game_not_found", "Game not found") from None
    except AudienceCursorAheadError as error:
        raise _error(
            409, "cursor_ahead", str(error),
            after_seq=error.after_seq, high_watermark=error.high_watermark,
        ) from None
    except ValueError as error:
        raise _error(422, "invalid_cursor", str(error)) from None
    return AudienceEventPageResponse.model_validate(page)


@router.get("/{game_id}/logs", response_model=GameLogsResponse)
async def get_game_logs(game_id: str):
    service = get_service()
    if service.get_game_state(game_id) is None:
        raise HTTPException(404, "Game not found")
    service_data_dir = getattr(service, "data_dir", "data")
    if not isinstance(service_data_dir, str):
        service_data_dir = "data"
    log_dir = os.path.join(service_data_dir, "games", game_id)
    conversations = _read_jsonl(os.path.join(log_dir, "conversation.log"))
    operations = _read_jsonl(os.path.join(log_dir, "game.log"))
    return GameLogsResponse(
        game_id=game_id,
        events=_public_replay_events(conversations, operations),
    )


@router.get("/{game_id}/export")
async def export_game(
    game_id: str,
    format: Annotated[str, Query()] = "html",
):
    """Download a self-contained HTML battle report + god-view replay."""
    if format != "html":
        raise _error(422, "unsupported_format", "Only format=html is supported")
    try:
        html = get_export_service().render_html(game_id)
    except GameExportNotFoundError:
        raise HTTPException(404, "Game not found") from None
    filename = f"wolfkiller-{game_id[:8]}.html"
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


def _camp_label(value: object) -> str:
    """Normalize a stored camp value (plain str or str-Enum) into its label."""
    return value.value if isinstance(value, Camp) else (value if isinstance(value, str) else "")


@router.get("/{game_id}/memories", response_model=GameMemoriesResponse)
async def get_game_memories(game_id: str):
    """Audience god-view projection of each seat's persisted night memories."""
    service = get_service()
    state = service.get_game_state(game_id)
    if state is None:
        raise HTTPException(404, "Game not found")
    memory_service = getattr(service, "memory_service", None)
    memories = []
    for seat in sorted(state.players):
        player = state.players[seat]
        raw = None
        if memory_service is not None:
            raw = memory_service.load_memory(game_id, seat)
        if raw is None:
            memories.append(PlayerMemoryResponse(
                seat_number=seat,
                role=player.role,
                camp=_camp_label(player.camp),
                is_alive=player.is_alive,
                private_knowledge={},
                action_history=[],
                witnessed_events=[],
                last_updated="",
            ))
            continue
        memories.append(PlayerMemoryResponse(
            seat_number=raw.get("seat_number", seat),
            role=raw.get("role", player.role),
            camp=_camp_label(raw.get("camp", player.camp)),
            is_alive=bool(raw.get("is_alive", player.is_alive)),
            private_knowledge=raw.get("private_knowledge") or {},
            action_history=raw.get("action_history") or [],
            witnessed_events=raw.get("witnessed_events") or [],
            last_updated=raw.get("last_updated", ""),
        ))
    return GameMemoriesResponse(game_id=game_id, memories=memories)


@router.get("/{game_id}/review", response_model=ReviewStatusResponse)
async def get_game_review(game_id: str):
    try:
        payload = get_review_service().get_review(game_id)
    except ReviewGameNotFoundError:
        raise _error(404, "game_not_found", "Game not found") from None
    return ReviewStatusResponse.model_validate(payload)


@router.post("/{game_id}/review", response_model=ReviewStatusResponse)
async def generate_game_review(
    game_id: str,
    req: GenerateReviewRequest = GenerateReviewRequest(),
):
    try:
        payload = await get_review_service().request_review(
            game_id, force=req.force,
        )
    except ReviewGameNotFoundError:
        raise _error(404, "game_not_found", "Game not found") from None
    return ReviewStatusResponse.model_validate(payload)

