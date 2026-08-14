import json
import os
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, HTTPException
from app.api.schemas import (
    CreateGameRequest, CreateGameResponse,
    GameListResponse, GameListItem, GameDetailResponse, GameLogsResponse,
    GameMemoriesResponse, PlayerMemoryResponse,
)
from app.models.game import Camp
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
    "game_over",
})
_PUBLIC_DEATH_CAUSES = frozenset({"wolf_kill", "poison", "hunter_shot", "exile"})
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
    "WOLF_CHAT_MESSAGE": frozenset({"seat", "text"}),
    "WOLF_VOTE": frozenset({"seat", "target_seat", "reasoning"}),
    "WITCH_THOUGHT": frozenset({"seat", "text"}),
    "SEER_THOUGHT": frozenset({"seat", "text"}),
}

_REASONING_EVENT_SCHEMA = frozenset(
    {"seat", "action_type", "target_seat", "reasoning", "thought"}
)
_REASONING_EVENT_TYPES = {
    "HUNTER_REASONING": ("hunter_reasoning", frozenset({"shoot", "pass"})),
    "WITCH_REASONING": ("witch_reasoning", frozenset({"save", "poison", "pass"})),
    "SEER_REASONING": ("seer_reasoning", frozenset({"check", "pass"})),
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
    log_dir = os.path.join("data", "games", game_id)
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
