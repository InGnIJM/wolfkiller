import json
import os
from fastapi import APIRouter, HTTPException
from app.api.schemas import (
    CreateGameRequest, CreateGameResponse,
    GameListResponse, GameListItem, GameDetailResponse, GameLogsResponse,
)
from app.services.game_service import GameService

router = APIRouter(prefix="/api/games", tags=["games"])


def get_service() -> GameService:
    from app.main import game_service
    return game_service


@router.post("", response_model=CreateGameResponse)
async def create_game(req: CreateGameRequest = CreateGameRequest()):
    service = get_service()
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
            "num_werewolves": req.num_werewolves,
            "num_villagers": req.num_villagers,
            "num_seers": req.num_seers,
            "num_witches": req.num_witches,
            "num_hunters": req.num_hunters,
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

    return GameDetailResponse(
        game_id=state.game_id,
        phase=state.phase.value,
        round_number=state.round_number,
        players={
            s: {
                "seat_number": p.seat_number,
                "role": p.role,
                "camp": p.camp,
                "is_alive": p.is_alive,
                "has_antidote": p.has_antidote,
                "has_poison": p.has_poison,
                "has_gun": p.has_gun,
                "revealed_role": p.revealed_role,
                "is_sheriff": p.is_sheriff,
            }
            for s, p in state.players.items()
        },
        sheriff=state.sheriff,
        speeches=[s.to_dict() if hasattr(s, "to_dict") else s for s in state.speeches],
        votes=[v.to_dict() if hasattr(v, "to_dict") else v for v in state.votes],
        death_history=[d.to_dict() if hasattr(d, "to_dict") else d for d in state.death_history],
        win_result=state.win_result,
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


@router.get("/{game_id}/logs", response_model=GameLogsResponse)
async def get_game_logs(game_id: str):
    log_dir = os.path.join("data", "games", game_id)
    conversations = _read_jsonl(os.path.join(log_dir, "conversation.log"))
    operations = _read_jsonl(os.path.join(log_dir, "game.log"))
    return GameLogsResponse(
        game_id=game_id,
        conversations=conversations,
        operations=operations,
    )
