import json
import os
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Annotated, Any
from fastapi import APIRouter, HTTPException, Query
from app.api.model_schemas import public_model_snapshot
from app.api.schemas import (
    AssignFolderRequest, BatchDeleteResponse, BatchGameIdsRequest, BatchMoveRequest,
    BatchMoveResponse, CreateGameRequest, CreateGameResponse,
    GameListResponse, GameListItem, GameDetailResponse, GameLogsResponse,
    GameMemoriesResponse, PlayerMemoryResponse, RenameGameRequest,
    AudienceEventPageResponse, AudienceSnapshotResponse, GameExecutionResponse,
)
from app.models.game import Camp
from app.persistence.repository import GameReferencedByBenchmark, InvalidExecutionTransition
from app.services.audience_event_service import (
    AudienceCursorAheadError,
    AudienceGameNotFoundError,
)
from app.services.game_service import GameService

router = APIRouter(prefix="/api/games", tags=["games"])

_LEGACY_ROLE_COUNT_FIELDS = {
    "num_werewolves": "wolf-killer-werewolf",
    "num_villagers": "wolf-killer-villager",
    "num_seers": "wolf-killer-seer",
    "num_witches": "wolf-killer-witch",
    "num_hunters": "wolf-killer-hunter",
}

_PUBLIC_GAME_PHASES = frozenset({
    "waiting", "role_deal", "night", "dawn", "last_words",
    "sheriff_election", "speech", "vote_casting", "vote_resolution",
    "game_over", "error",
})
_PUBLIC_DEATH_CAUSES = frozenset({"wolf_kill", "poison", "hunter_shot", "exile", "self_explode"})
_PUBLIC_WINNING_CAMPS = frozenset({"good", "werewolf"})
_PUBLIC_WIN_REASONS = frozenset({
    "all_gods_dead", "all_villagers_dead", "all_wolves_dead",
})

_AUDIENCE_ACTION_SCHEMAS = {
    "WEREWOLF_KILL": frozenset({"target_seat", "vote_counts"}),
    "WITCH_SAVE": frozenset({"target_seat"}),
    "WITCH_POISON": frozenset({"target_seat"}),
    "SEER_CHECK": frozenset({"target_seat", "result"}),
    "HUNTER_SHOT": frozenset({"target_seat"}),
    "GUARD_PROTECT": frozenset({"target_seat"}),
    "WOLF_CHAT_MESSAGE": frozenset({"seat", "text"}),
    "WOLF_VOTE": frozenset({"seat", "target_seat", "reasoning"}),
    "WITCH_THOUGHT": frozenset({"seat", "text"}),
    "SEER_THOUGHT": frozenset({"seat", "text"}),
    "EXILE_CANCELLED": frozenset({"target_seat", "round_number"}),
    "SELF_EXPLODE": frozenset({"seat", "target_seat", "round_number"}),
    "PLAYER_REVEALED": frozenset({"seat_number", "role", "camp"}),
}

_REASONING_EVENT_SCHEMA = frozenset(
    {"seat", "action_type", "target_seat", "reasoning", "thought"}
)
_REASONING_EVENT_TYPES = {
    "HUNTER_REASONING": ("hunter_reasoning", frozenset({"shoot", "pass"})),
    "WITCH_REASONING": ("witch_reasoning", frozenset({"save", "poison", "pass"})),
    "SEER_REASONING": ("seer_reasoning", frozenset({"check", "pass"})),
    "GUARD_REASONING": ("guard_reasoning", frozenset({"guard", "pass"})),
    "WEREWOLF_KING_REASONING": ("werewolf_king_reasoning", frozenset({"explode", "pass"})),
}


def get_service() -> GameService:
    from app.main import game_service
    return game_service


def get_audience_service():
    from app.main import audience_event_service
    return audience_event_service


def _execution_info(service: GameService, game_id: str, state) -> dict[str, object]:
    getter = getattr(service, "get_execution_info", None)
    if callable(getter):
        try:
            value = getter(game_id)
        except KeyError:
            value = None
        if isinstance(value, Mapping):
            return {
                "execution_status": str(value.get("execution_status", "running")),
                "recoverable": bool(value.get("recoverable", False)),
                "recovery_block_code": value.get("recovery_block_code"),
                "interruption_count": int(value.get("interruption_count", 0)),
                "benchmark_run_id": value.get("benchmark_run_id"),
            }
    completed = getattr(getattr(state, "phase", None), "value", None) == "game_over"
    return {
        "execution_status": "completed" if completed else "running",
        "recoverable": False,
        "recovery_block_code": "legacy_archive",
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
    try:
        if req.role_counts is not None:
            game_id = await service.create_game(
                role_counts=req.role_counts, reveal_on_death=req.reveal_on_death,
                model_assignments=assignments,
            )
        else:
            game_id = await service.create_game(
                num_werewolves=req.num_werewolves,
                num_villagers=req.num_villagers,
                num_seers=req.num_seers,
                num_witches=req.num_witches,
                num_hunters=req.num_hunters,
                reveal_on_death=req.reveal_on_death,
                model_assignments=assignments,
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
            **{
                field: state.config.role_counts.get(role_id, 0)
                for field, role_id in _LEGACY_ROLE_COUNT_FIELDS.items()
            },
        },
        model_snapshot=service.get_game_model_snapshot(game_id),
    )


def _list_item(service: GameService, game_id: str, state) -> GameListItem:
    win = state.win_result.get("winning_camp") if state.win_result else None
    execution = _execution_info(service, game_id, state)
    folder_id = None
    getter = getattr(service, "get_game_folder_id", None)
    if callable(getter):
        value = getter(game_id)
        if isinstance(value, str) and value:
            folder_id = value
    return GameListItem(
        game_id=game_id,
        name=service.get_display_name(game_id),
        phase=state.phase.value,
        round_number=state.round_number,
        player_count=len(state.players),
        alive_count=len(state.alive_players()),
        winner=win,
        folder_id=folder_id,
        **execution,
    )


@router.get("", response_model=GameListResponse)
async def list_games():
    service = get_service()
    games = service.list_games()
    items = []
    for g in games:
        checker = getattr(service, "is_lobby_game", None)
        if callable(checker) and not checker(g):
            continue
        state = service.get_game_state(g)
        if state is None:
            continue
        items.append(_list_item(service, g, state))
    return GameListResponse(games=items)


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


async def _control_game(game_id: str, action: str) -> GameExecutionResponse:
    service = get_service()
    method = getattr(service, f"{action}_game", None)
    if not callable(method):
        raise _error(409, "recovery_unavailable", "Game recovery is unavailable")
    try:
        result = await method(game_id)
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
        interruption_count=int(result.get("interruption_count", 0)),
        benchmark_run_id=result.get("benchmark_run_id"),
    )


@router.post("/{game_id}/pause", response_model=GameExecutionResponse)
async def pause_game(game_id: str):
    return await _control_game(game_id, "pause")


@router.post("/{game_id}/resume", response_model=GameExecutionResponse)
async def resume_game(game_id: str):
    return await _control_game(game_id, "resume")


@router.post("/{game_id}/recover", response_model=GameExecutionResponse)
async def recover_game(game_id: str):
    return await _control_game(game_id, "recover")


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
    state = snapshot["state"]
    snapshot = {
        **snapshot,
        "state": {
            **state,
            "model_snapshot": public_model_snapshot(
                get_service().get_game_model_snapshot(game_id),
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


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _timestamp(record: dict[str, Any]) -> datetime | None:
    value = record.get("timestamp")
    if not isinstance(value, str) or ("T" not in value and " " not in value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public_timestamp(value: datetime) -> str:
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return _is_int(value) and value > 0


def _is_non_negative_int(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _is_reasoning_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 500


def _public_conversation_event(record: dict[str, Any]) -> dict | None:
    if record.get("scope") != "public" or _timestamp(record) is None:
        return None
    seat = record.get("speaker_seat")
    content = record.get("content")
    round_number = record.get("round_number")
    phase = record.get("phase")
    if not (
        _is_positive_int(seat)
        and isinstance(content, str)
        and _is_non_negative_int(round_number)
        and isinstance(phase, str)
        and phase in _PUBLIC_GAME_PHASES
    ):
        return None
    return {
        "event_type": "speech",
        "payload": {
            "player_seat": seat, "text": content, "round_number": round_number,
            "phase": phase,
        },
    }


def _public_reasoning_event(
    event_type: str, payload: dict[str, Any], round_number: int,
) -> list[dict]:
    """Project a role reasoning audience record into a closed night_thought event."""
    public_type, allowed_actions = _REASONING_EVENT_TYPES[event_type]
    seat = payload.get("seat")
    action = payload.get("action_type")
    target = payload.get("target_seat")
    reasoning = payload.get("reasoning")
    thought = payload.get("thought")
    if not _is_positive_int(seat):
        return []
    if not isinstance(action, str) or action not in allowed_actions:
        return []
    if target is not None and not _is_positive_int(target):
        return []
    if not _is_reasoning_text(reasoning) or not _is_reasoning_text(thought):
        return []
    return [{
        "event_type": "night_thought",
        "payload": {
            "round_number": round_number, "seat": seat,
            "action_type": public_type, "target_seat": target,
            "reasoning": reasoning,
        },
    }]


def _public_day_verdict_event(
    event_type: str, payload: dict[str, Any], round_number: int,
) -> list[dict]:
    """Project the daytime verdict events (a card flip that cancels an exile,
    a self-destruct, a public identity reveal) into closed audience events."""
    if event_type == "EXILE_CANCELLED":
        target = payload.get("target_seat")
        if not _is_positive_int(target):
            return []
        return [{"event_type": "exile_cancelled", "payload": {
            "round_number": round_number, "target_seat": target}}]
    if event_type == "SELF_EXPLODE":
        seat = payload.get("seat"); target = payload.get("target_seat")
        if not _is_positive_int(seat) or not _is_positive_int(target):
            return []
        return [{"event_type": "self_explode", "payload": {
            "round_number": round_number, "seat": seat, "target_seat": target}}]
    seat = payload.get("seat_number"); role = payload.get("role"); camp = payload.get("camp")
    if (not _is_positive_int(seat) or not isinstance(role, str) or not role
            or camp not in _PUBLIC_WINNING_CAMPS):
        return []
    return [{"event_type": "player_revealed", "payload": {
        "seat_number": seat, "role": role, "camp": camp}}]


def _public_audience_action_event(record: dict[str, Any], round_number: int) -> list[dict]:
    """Project one audience_action log record into a closed audience event."""
    data = record.get("data")
    if not isinstance(data, dict):
        return []
    event_type = data.get("event_type")
    payload = data.get("payload")
    if not isinstance(event_type, str):
        return []
    if event_type in ("WOLF_CHAT_MESSAGE", "WITCH_THOUGHT", "SEER_THOUGHT"):
        if not isinstance(payload, dict) or set(payload) != _AUDIENCE_ACTION_SCHEMAS[event_type]:
            return []
        seat = payload.get("seat"); text = payload.get("text")
        if not _is_positive_int(seat) or not isinstance(text, str) or not text or len(text) > 200:
            return []
        public_type = {"WOLF_CHAT_MESSAGE": "wolf_chat_message",
                       "WITCH_THOUGHT": "witch_thought",
                       "SEER_THOUGHT": "seer_thought"}[event_type]
        return [{"event_type": public_type, "payload": {
            "round_number": round_number, "seat": seat, "text": text}}]
    if event_type == "WOLF_VOTE":
        if not isinstance(payload, dict) or set(payload) != _AUDIENCE_ACTION_SCHEMAS[event_type]:
            return []
        seat = payload.get("seat"); target = payload.get("target_seat")
        reasoning = payload.get("reasoning")
        if (not _is_positive_int(seat)
                or (target is not None and not _is_positive_int(target))
                or not _is_reasoning_text(reasoning)):
            return []
        return [{"event_type": "wolf_vote", "payload": {
            "round_number": round_number, "seat": seat,
            "target_seat": target, "reasoning": reasoning}}]
    if event_type in _REASONING_EVENT_TYPES:
        if not isinstance(payload, dict) or set(payload) != _REASONING_EVENT_SCHEMA:
            return []
        return _public_reasoning_event(event_type, payload, round_number)
    if event_type in ("EXILE_CANCELLED", "SELF_EXPLODE", "PLAYER_REVEALED"):
        if not isinstance(payload, dict) or set(payload) != _AUDIENCE_ACTION_SCHEMAS[event_type]:
            return []
        return _public_day_verdict_event(event_type, payload, round_number)
    if event_type not in _AUDIENCE_ACTION_SCHEMAS:
        return []
    if not isinstance(payload, dict) or set(payload) != _AUDIENCE_ACTION_SCHEMAS[event_type]:
        return []
    target = payload.get("target_seat")
    if not _is_positive_int(target):
        return []
    public_payload: dict[str, Any] = {
        "action_type": event_type.lower(),
        "target_seat": target,
        "round_number": round_number,
    }
    if event_type == "WEREWOLF_KILL":
        votes = payload.get("vote_counts")
        if not isinstance(votes, dict):
            return []
        for seat_key, count in votes.items():
            if not isinstance(seat_key, str) or not seat_key.isdigit() or not _is_positive_int(count):
                return []
        public_payload["vote_counts"] = votes
    if event_type == "SEER_CHECK":
        result = payload.get("result")
        if not isinstance(result, str) or result not in _PUBLIC_WINNING_CAMPS:
            return []
        public_payload["result"] = result
    return [{"event_type": "night_action", "payload": public_payload}]


def _public_operation_events(record: dict[str, Any]) -> list[dict]:
    timestamp = _timestamp(record)
    operation = record.get("operation")
    round_number = record.get("round")
    phase = record.get("phase")
    data = record.get("data")
    if (
        timestamp is None
        or not _is_non_negative_int(round_number)
        or not isinstance(phase, str)
        or phase not in _PUBLIC_GAME_PHASES
        or not isinstance(data, dict)
    ):
        return []

    if operation == "vote":
        seat = record.get("seat")
        target = data.get("target")
        if (
            phase != "vote_casting"
            or not _is_positive_int(seat)
            or (target is not None and not _is_positive_int(target))
        ):
            return []
        return [{
            "event_type": "vote",
            "payload": {"voter_seat": seat, "target_seat": target, "round_number": round_number},
        }]

    if operation == "vote_technical_abstain":
        seat = record.get("seat")
        failure_code = data.get("failure_code")
        if (
            phase != "vote_casting"
            or not _is_positive_int(seat)
            or not isinstance(failure_code, str)
            or not failure_code
        ):
            return []
        return [{
            "event_type": "technical_abstain",
            "payload": {
                "voter_seat": seat,
                "round_number": round_number,
                "failure_code": failure_code,
            },
        }]

    if operation == "vote_result":
        if "exiled" not in data:
            return []
        exiled = data.get("exiled")
        if phase != "vote_resolution" or (exiled is not None and (not _is_int(exiled) or exiled <= 0)):
            return []
        return [{
            "event_type": "vote_result",
            "payload": {"round_number": round_number, "exiled_seat": exiled},
        }]

    if operation == "night_deaths":
        if phase != "dawn" or not isinstance(data.get("deaths"), list):
            return []
        events = []
        for death in data["deaths"]:
            if not isinstance(death, dict) or set(death) != {"player_seat", "cause", "round_number"}:
                return []
            if not (
                _is_positive_int(death["player_seat"])
                and isinstance(death["cause"], str)
                and death["cause"] in _PUBLIC_DEATH_CAUSES
                and _is_non_negative_int(death["round_number"])
            ):
                return []
            events.append({
                "event_type": "death",
                "payload": {
                    "player_seat": death["player_seat"],
                    "cause": death["cause"],
                    "round_number": death["round_number"],
                },
            })
        return events

    if operation == "audience_action":
        return _public_audience_action_event(record, round_number)

    if operation == "phase_change":
        new_phase = data.get("new_phase")
        if not isinstance(new_phase, str) or new_phase not in _PUBLIC_GAME_PHASES:
            return []
        return [{
            "event_type": "phase",
            "payload": {"phase": new_phase, "round_number": round_number},
        }]

    if operation == "narration":
        title, text = data.get("title"), data.get("text")
        if (not isinstance(title, str) or not isinstance(text, str)
                or not title or not text or len(title) > 100 or len(text) > 200):
            return []
        return [{"event_type": "narration", "payload": {
            "round_number": round_number, "title": title, "text": text}}]

    if operation == "game_over":
        winner = data.get("winner")
        reason = data.get("reason")
        if (
            not isinstance(winner, str)
            or winner not in _PUBLIC_WINNING_CAMPS
            or not isinstance(reason, str)
            or reason not in _PUBLIC_WIN_REASONS
        ):
            return []
        return [{
            "event_type": "winner",
            "payload": {"winning_camp": winner, "reason": reason},
        }]

    return []


def _public_replay_events(conversations: list[dict], operations: list[dict]) -> list[dict]:
    ordered_events: list[tuple[datetime, int, dict]] = []
    order = 0
    for record in conversations:
        if not isinstance(record, dict):
            continue
        event = _public_conversation_event(record)
        timestamp = _timestamp(record)
        if event is not None and timestamp is not None:
            ordered_events.append((timestamp, order, event))
            order += 1
    for record in operations:
        if not isinstance(record, dict):
            continue
        timestamp = _timestamp(record)
        if timestamp is None:
            continue
        for event in _public_operation_events(record):
            ordered_events.append((timestamp, order, event))
            order += 1
    return [
        {**event, "timestamp": _public_timestamp(timestamp)}
        for timestamp, _, event in sorted(ordered_events, key=lambda item: (item[0], item[1]))
    ]


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
