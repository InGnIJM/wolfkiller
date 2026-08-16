from datetime import datetime
from typing import Annotated, Literal, Optional, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


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


def _validate_public_utc_timestamp(value: str) -> str:
    try:
        datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be a valid UTC instant") from exc
    return value


PublicUTCTimestamp = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"),
    AfterValidator(_validate_public_utc_timestamp),
]


class PublicPlayerResponse(_PublicResponse):
    seat_number: PositivePublicInt
    is_alive: bool
    is_sheriff: bool
    role: str
    camp: str


class PublicSpeechResponse(_PublicResponse):
    player_seat: PositivePublicInt
    text: str
    round_number: NonNegativePublicInt
    phase: Optional[PublicGamePhase] = None


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


PublicNightActionType = Literal[
    "werewolf_kill", "witch_save", "witch_poison", "seer_check", "hunter_shot",
]


class PublicNightActionResponse(_PublicResponse):
    action_type: PublicNightActionType
    target_seat: PositivePublicInt
    round_number: NonNegativePublicInt
    vote_counts: Optional[dict[str, PositivePublicInt]] = None
    result: Optional[PublicWinningCamp] = None


class PublicPhaseResponse(_PublicResponse):
    phase: PublicGamePhase
    round_number: NonNegativePublicInt


class PublicWinnerResponse(_PublicResponse):
    winning_camp: PublicWinningCamp
    reason: PublicWinReason


PublicThoughtActionType = Literal[
    "hunter_reasoning",
    "witch_reasoning",
    "seer_reasoning",
]


class PublicNightThoughtResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    action_type: PublicThoughtActionType
    target_seat: Optional[PositivePublicInt] = None
    reasoning: str


class PublicNarrationResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    title: Annotated[str, Field(min_length=1, max_length=100)]
    text: Annotated[str, Field(min_length=1, max_length=200)]


class PublicWolfChatMessageResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    text: Annotated[str, Field(min_length=1, max_length=200)]


class PublicWolfVoteResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: Optional[PositivePublicInt] = None
    reasoning: Annotated[str, Field(max_length=500)]


class PublicWitchThoughtResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    text: Annotated[str, Field(min_length=1, max_length=200)]


class PublicSeerThoughtResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    text: Annotated[str, Field(min_length=1, max_length=200)]


class _PublicReplayEvent(_PublicResponse):
    timestamp: PublicUTCTimestamp


class PublicSpeechReplayEvent(_PublicReplayEvent):
    event_type: Literal["speech"]
    payload: PublicSpeechResponse


class PublicDeathReplayEvent(_PublicReplayEvent):
    event_type: Literal["death"]
    payload: PublicDeathResponse


class PublicVoteReplayEvent(_PublicReplayEvent):
    event_type: Literal["vote"]
    payload: PublicVoteResponse


class PublicVoteResultReplayEvent(_PublicReplayEvent):
    event_type: Literal["vote_result"]
    payload: PublicVoteResultResponse


class PublicNightActionReplayEvent(_PublicReplayEvent):
    event_type: Literal["night_action"]
    payload: PublicNightActionResponse


class PublicNightThoughtReplayEvent(_PublicReplayEvent):
    event_type: Literal["night_thought"]
    payload: PublicNightThoughtResponse


class PublicNarrationReplayEvent(_PublicReplayEvent):
    event_type: Literal["narration"]
    payload: PublicNarrationResponse


class PublicWolfChatMessageReplayEvent(_PublicReplayEvent):
    event_type: Literal["wolf_chat_message"]
    payload: PublicWolfChatMessageResponse


class PublicWolfVoteReplayEvent(_PublicReplayEvent):
    event_type: Literal["wolf_vote"]
    payload: PublicWolfVoteResponse


class PublicWitchThoughtReplayEvent(_PublicReplayEvent):
    event_type: Literal["witch_thought"]
    payload: PublicWitchThoughtResponse


class PublicSeerThoughtReplayEvent(_PublicReplayEvent):
    event_type: Literal["seer_thought"]
    payload: PublicSeerThoughtResponse


class PublicPhaseReplayEvent(_PublicReplayEvent):
    event_type: Literal["phase"]
    payload: PublicPhaseResponse


class PublicWinnerReplayEvent(_PublicReplayEvent):
    event_type: Literal["winner"]
    payload: PublicWinnerResponse


PublicReplayEvent = Annotated[
    Union[
        PublicSpeechReplayEvent,
        PublicDeathReplayEvent,
        PublicVoteReplayEvent,
        PublicVoteResultReplayEvent,
        PublicNightActionReplayEvent,
        PublicNightThoughtReplayEvent,
        PublicNarrationReplayEvent,
        PublicWolfChatMessageReplayEvent,
        PublicWolfVoteReplayEvent,
        PublicWitchThoughtReplayEvent,
        PublicSeerThoughtReplayEvent,
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


class PlayerMemoryResponse(_PublicResponse):
    seat_number: PositivePublicInt
    role: str
    camp: str
    is_alive: bool
    private_knowledge: dict
    action_history: list
    witnessed_events: list
    last_updated: str


class GameMemoriesResponse(_PublicResponse):
    game_id: str
    memories: list[PlayerMemoryResponse]


class WSMessage(BaseModel):
    type: str
    payload: dict = {}
