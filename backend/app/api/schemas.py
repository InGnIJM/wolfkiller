from pydantic import BaseModel, model_validator
from typing import Optional


class CreateGameRequest(BaseModel):
    role_counts: Optional[dict[str, int]] = None
    num_werewolves: int = 3
    num_villagers: int = 3
    num_seers: int = 1
    num_witches: int = 1
    num_hunters: int = 1

    @model_validator(mode="after")
    def reject_mixed_role_count_formats(self):
        legacy_fields = {
            "num_werewolves",
            "num_villagers",
            "num_seers",
            "num_witches",
            "num_hunters",
        }
        if self.role_counts is not None and self.model_fields_set & legacy_fields:
            raise ValueError("role_counts cannot be combined with legacy role counts")
        return self


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
