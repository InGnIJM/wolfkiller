from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class _PublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicPlayerResponse(_PublicResponse):
    seat_number: int
    is_alive: bool
    is_sheriff: bool


class PublicSpeechResponse(_PublicResponse):
    player_seat: int
    text: str
    round_number: int


class PublicDeathResponse(_PublicResponse):
    player_seat: int
    cause: str
    round_number: int


class PublicVoteResponse(_PublicResponse):
    voter_seat: int
    target_seat: Optional[int] = None
    round_number: int


class PublicPhaseResponse(_PublicResponse):
    phase: str
    round_number: int


class PublicWinnerResponse(_PublicResponse):
    winning_camp: str
    reason: str


class PublicSpeechReplayEvent(_PublicResponse):
    event_type: Literal["speech"]
    payload: PublicSpeechResponse


class PublicDeathReplayEvent(_PublicResponse):
    event_type: Literal["death"]
    payload: PublicDeathResponse


class PublicVoteReplayEvent(_PublicResponse):
    event_type: Literal["vote"]
    payload: PublicVoteResponse


class PublicPhaseReplayEvent(_PublicResponse):
    event_type: Literal["phase"]
    payload: PublicPhaseResponse


class PublicWinnerReplayEvent(_PublicResponse):
    event_type: Literal["winner"]
    payload: PublicWinnerResponse


PublicReplayEvent = Annotated[
    Union[
        PublicSpeechReplayEvent,
        PublicDeathReplayEvent,
        PublicVoteReplayEvent,
        PublicPhaseReplayEvent,
        PublicWinnerReplayEvent,
    ],
    Field(discriminator="event_type"),
]


class GameDetailResponse(_PublicResponse):
    game_id: str
    phase: str
    round_number: int
    players: dict[int, PublicPlayerResponse]
    sheriff: Optional[int]
    speeches: list[PublicSpeechResponse]
    votes: list[PublicVoteResponse]
    death_history: list[PublicDeathResponse]
    win_result: Optional[PublicWinnerResponse]


class SetSpeedRequest(BaseModel):
    delay_seconds: float = 2.0


class GameLogsResponse(_PublicResponse):
    game_id: str
    events: list[PublicReplayEvent]


class WSMessage(BaseModel):
    type: str
    payload: dict = {}
