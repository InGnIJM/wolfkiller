import json
import os
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, HTTPException
from app.api.schemas import (
    CreateGameRequest, CreateGameResponse,
    GameListResponse, GameListItem, GameDetailResponse, GameLogsResponse,
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


def get_service() -> GameService:
    from app.main import game_service
    return game_service


@router.post("", response_model=CreateGameResponse)
async def create_game(req: CreateGameRequest = CreateGameRequest()):
    service = get_service()
    if req.role_counts is not None:
        game_id = await service.create_game(role_counts=req.role_counts)
    else:
        game_id = await service.create_game(
            num_werewolves=req.num_werewolves,
            num_villagers=req.num_villagers,
            num_seers=req.num_seers,
            num_witches=req.num_witches,
            num_hunters=req.num_hunters,
        )
    state = service.get_game_state(game_id)
    if state is None:
        raise HTTPException(404, "Game not found after creation")
    return CreateGameResponse(
        game_id=game_id,
        player_count=len(state.players),
        config={
            "role_counts": state.config.role_counts,
            **{
                field: state.config.role_counts.get(role_id, 0)
                for field, role_id in _LEGACY_ROLE_COUNT_FIELDS.items()
            },
        },
    )


@router.get("", response_model=GameListResponse)
async def list_games():
    service = get_service()
    games = service.list_games()
    items = []
    for g in games:
        state = service.get_game_state(g)
        if state is None:
            continue
        items.append(GameListItem(
            game_id=g,
            phase=state.phase.value,
            round_number=state.round_number,
            player_count=len(state.players),
            alive_count=len(state.alive_players()),
            winner=state.win_result.get("winning_camp") if state.win_result else None,
        ))
    return GameListResponse(games=items)


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
        players=public_state["players"],
        sheriff=public_state["sheriff"],
        speeches=public_state["speeches"],
        death_history=public_state["death_history"],
        win_result=public_state["win_result"],
    )


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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _public_conversation_event(record: dict[str, Any]) -> dict | None:
    if record.get("scope") != "public" or _timestamp(record) is None:
        return None
    seat = record.get("speaker_seat")
    content = record.get("content")
    round_number = record.get("round_number")
    phase = record.get("phase")
    if not (_is_int(seat) and isinstance(content, str) and _is_int(round_number) and isinstance(phase, str)):
        return None
    return {
        "event_type": "speech",
        "payload": {"player_seat": seat, "text": content, "round_number": round_number},
    }


def _public_operation_events(record: dict[str, Any]) -> list[dict]:
    timestamp = _timestamp(record)
    operation = record.get("operation")
    round_number = record.get("round")
    phase = record.get("phase")
    data = record.get("data")
    if timestamp is None or not _is_int(round_number) or not isinstance(phase, str) or not isinstance(data, dict):
        return []

    if operation == "vote":
        seat = record.get("seat")
        target = data.get("target")
        if phase != "vote_casting" or not _is_int(seat) or (target is not None and not _is_int(target)):
            return []
        return [{
            "event_type": "vote",
            "payload": {"voter_seat": seat, "target_seat": target, "round_number": round_number},
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
            if not (_is_int(death["player_seat"]) and isinstance(death["cause"], str) and _is_int(death["round_number"])):
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

    if operation == "phase_change":
        new_phase = data.get("new_phase")
        if not isinstance(new_phase, str):
            return []
        return [{
            "event_type": "phase",
            "payload": {"phase": new_phase, "round_number": round_number},
        }]

    if operation == "game_over":
        winner = data.get("winner")
        reason = data.get("reason")
        if not isinstance(winner, str) or not isinstance(reason, str):
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
    return [event for _, _, event in sorted(ordered_events, key=lambda item: (item[0], item[1]))]


@router.get("/{game_id}/logs", response_model=GameLogsResponse)
async def get_game_logs(game_id: str):
    service = get_service()
    if service.get_game_state(game_id) is None:
        raise HTTPException(404, "Game not found")
    log_dir = os.path.join("data", "games", game_id)
    conversations = _read_jsonl(os.path.join(log_dir, "conversation.log"))
    operations = _read_jsonl(os.path.join(log_dir, "game.log"))
    return GameLogsResponse(
        game_id=game_id,
        events=_public_replay_events(conversations, operations),
    )
