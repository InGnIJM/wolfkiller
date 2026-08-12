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
    model_config = ConfigDict(extra="forbid", strict=True)


PositivePublicInt = Annotated[int, Field(gt=0)]
NonNegativePublicInt = Annotated[int, Field(ge=0)]
PublicGamePhase = Literal[
    "waiting", "role_deal", "night", "dawn", "last_words",
    "sheriff_election", "speech", "vote_casting", "vote_resolution",
    "game_over",
]
PublicDeathCause = Literal["wolf_kill", "poison", "hunter_shot", "exile"]
PublicWinningCamp = Literal["good", "werewolf"]
PublicWinReason = Literal[
    "all_gods_dead", "all_villagers_dead", "all_wolves_dead",
]


class PublicPlayerResponse(_PublicResponse):
    seat_number: PositivePublicInt
    is_alive: bool
    is_sheriff: bool


class PublicSpeechResponse(_PublicResponse):
    player_seat: PositivePublicInt
    text: str
    round_number: NonNegativePublicInt


class PublicDeathResponse(_PublicResponse):
    player_seat: PositivePublicInt
    cause: PublicDeathCause
    round_number: NonNegativePublicInt


class PublicVoteResponse(_PublicResponse):
    voter_seat: PositivePublicInt
    target_seat: Optional[PositivePublicInt] = None
    round_number: NonNegativePublicInt


class PublicVoteResultResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    exiled_seat: Optional[PositivePublicInt] = None


class PublicPhaseResponse(_PublicResponse):
    phase: PublicGamePhase
    round_number: NonNegativePublicInt


class PublicWinnerResponse(_PublicResponse):
    winning_camp: PublicWinningCamp
    reason: PublicWinReason


class PublicSpeechReplayEvent(_PublicResponse):
    event_type: Literal["speech"]
    payload: PublicSpeechResponse


class PublicDeathReplayEvent(_PublicResponse):
    event_type: Literal["death"]
    payload: PublicDeathResponse


class PublicVoteReplayEvent(_PublicResponse):
    event_type: Literal["vote"]
    payload: PublicVoteResponse


class PublicVoteResultReplayEvent(_PublicResponse):
    event_type: Literal["vote_result"]
    payload: PublicVoteResultResponse


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
        PublicVoteResultReplayEvent,
        PublicPhaseReplayEvent,
        PublicWinnerReplayEvent,
    ],
    Field(discriminator="event_type"),
]


class GameDetailResponse(_PublicResponse):
    game_id: str
    phase: PublicGamePhase
    round_number: NonNegativePublicInt
    players: dict[PositivePublicInt, PublicPlayerResponse]
    sheriff: Optional[PositivePublicInt]
    speeches: list[PublicSpeechResponse]
    death_history: list[PublicDeathResponse]
    win_result: Optional[PublicWinnerResponse]

    @model_validator(mode="after")
    def require_player_map_keys_to_match_public_seats(self):
        for seat_number, player in self.players.items():
            if seat_number != player.seat_number:
                raise ValueError("player map key must match the embedded seat_number")
        return self


class SetSpeedRequest(BaseModel):
    delay_seconds: float = 2.0


class GameLogsResponse(_PublicResponse):
    game_id: str
    events: list[PublicReplayEvent]


class WSMessage(BaseModel):
    type: str
    payload: dict = {}
