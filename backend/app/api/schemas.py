from pydantic import BaseModel
from typing import Optional


class CreateGameRequest(BaseModel):
    num_werewolves: int = 3
    num_villagers: int = 3
    num_seers: int = 1
    num_witches: int = 1
    num_hunters: int = 1


class CreateGameResponse(BaseModel):
    game_id: str
    player_count: int
    config: dict


class GameListItem(BaseModel):
    game_id: str
    phase: str
    round_number: int
    player_count: int
    alive_count: int
    winner: Optional[str] = None


class GameListResponse(BaseModel):
    games: list[GameListItem]


class GameDetailResponse(BaseModel):
    game_id: str
    phase: str
    round_number: int
    players: dict
    sheriff: Optional[int]
    speeches: list
    votes: list
    death_history: list
    win_result: Optional[dict]


class SetSpeedRequest(BaseModel):
    delay_seconds: float = 2.0


class GameLogsResponse(BaseModel):
    game_id: str
    conversations: list[dict]
    operations: list[dict]


class WSMessage(BaseModel):
    type: str
    payload: dict = {}
