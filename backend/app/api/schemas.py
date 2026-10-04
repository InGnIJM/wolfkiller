from datetime import datetime
from typing import Annotated, Literal, Optional, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.model_schemas import ModelAssignment, ModelSnapshotEntry


class CreateGameRequest(BaseModel):
    role_counts: Optional[dict[str, int]] = None
    num_werewolves: int = 3
    num_villagers: int = 3
    num_seers: int = 1
    num_witches: int = 1
    num_hunters: int = 1
    reveal_on_death: bool = False
    enable_sheriff: bool = False
    witch_self_save: Literal["first_night_only", "never"] = "first_night_only"
    model_assignments: Optional[list[ModelAssignment]] = None

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
    model_snapshot: list[ModelSnapshotEntry] = Field(default_factory=list)


class GameListItem(BaseModel):
    game_id: str
    name: str
    phase: str
    round_number: int
    player_count: int
    alive_count: int
    winner: Optional[str] = None
    execution_status: str = "running"
    recoverable: bool = False
    recovery_block_code: Optional[str] = None
    interruption_count: int = 0
    benchmark_run_id: Optional[str] = None
    folder_id: Optional[str] = None


class RenameGameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class GameListResponse(BaseModel):
    games: list[GameListItem]
    total: int = 0
    page: int = 1
    page_size: int = 10


class FolderItem(BaseModel):
    folder_id: str
    name: str
    game_count: int
    created_at: str
    updated_at: str


class FolderListResponse(BaseModel):
    folders: list[FolderItem]


class FolderNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class AssignFolderRequest(BaseModel):
    folder_id: Optional[str] = None


class BatchGameIdsRequest(BaseModel):
    game_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("game_ids")
    @classmethod
    def _non_empty_ids(cls, value: list[str]) -> list[str]:
        if any(type(item) is not str or not item.strip() for item in value):
            raise ValueError("game_ids must contain non-empty strings")
        return value


class BatchMoveRequest(BatchGameIdsRequest):
    folder_id: Optional[str] = None


class BatchFailure(BaseModel):
    game_id: str
    code: str
    message: str


class BatchDeleteResponse(BaseModel):
    deleted: list[str]
    failed: list[BatchFailure]


class BatchMoveResponse(BaseModel):
    moved: list[str]
    failed: list[BatchFailure]


class _PublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


PositivePublicInt = Annotated[int, Field(gt=0)]
NonNegativePublicInt = Annotated[int, Field(ge=0)]
# These three enumerations mirror what the engine actually produces: the rule
# engine's win reasons, every death cause a role can apply, and the phase a
# crashed game is parked in. A value missing here is not a cosmetic gap — the
# detail route builds GameDetailResponse straight out of the live state, so an
# unlisted value raises ValidationError and turns the response into HTTP 500.
# Keep them in step with `core/rule_engine.py`, `models/actions.py` and the role
# specs; `tests/test_schemas.py` fails if one drops out.
PublicGamePhase = Literal[
    "waiting", "role_deal", "night", "dawn", "last_words",
    "sheriff_election", "speech", "vote_casting", "vote_resolution",
    "game_over", "error",
]
PublicDeathCause = Literal[
    "wolf_kill", "poison", "hunter_shot", "exile", "self_explode",
    "knight_duel", "charm", "wolf_king_shot",
]
PublicWinningCamp = Literal["good", "werewolf"]
PublicWinReason = Literal[
    "all_gods_dead", "all_villagers_dead", "all_wolves_dead",
    "wolves_outnumber_good",
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


class PublicTechnicalAbstainResponse(_PublicResponse):
    """The engine voted on a seat's behalf after that seat's model call failed.

    ``failure_code`` deliberately carries no upper bound: the replay projection
    accepts any non-empty code, so a stricter bound here would reject a record
    the mapper had just produced and fail the entire replay response.
    """
    voter_seat: PositivePublicInt
    round_number: NonNegativePublicInt
    failure_code: Annotated[str, Field(min_length=1)]


PublicNightActionType = Literal[
    "werewolf_kill", "witch_save", "witch_poison", "seer_check", "hunter_shot",
    "wolf_king_shot", "guard_protect",
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
    """How the game ended.

    ``reason`` is optional on purpose: an archive reconstructed from the manifest
    knows the winning camp but not the reason (that only lives in the log's
    ``game_over`` record), and "unknown" is expressed by absence rather than by a
    magic value the vocabulary would have to carry — and that a response model
    would otherwise reject.
    """

    winning_camp: PublicWinningCamp
    reason: Optional[PublicWinReason] = None


PublicThoughtActionType = Literal[
    "hunter_reasoning",
    "wolf_king_reasoning",
    "witch_reasoning",
    "seer_reasoning",
    "guard_reasoning",
    "werewolf_king_reasoning",
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
    #: 该次发言给出的次日计划；旧日志里没有这个字段，所以是可选的。
    day_plan: Optional[Annotated[str, Field(min_length=1, max_length=400)]] = None


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


class PublicExileCancelledResponse(_PublicResponse):
    """The vote selected a seat that flipped its card and survived."""
    round_number: NonNegativePublicInt
    target_seat: PositivePublicInt


class PublicSheriffElectedResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: Optional[PositivePublicInt] = None
    reason: Annotated[str, Field(min_length=1, max_length=32)]


class PublicSheriffBadgeResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    from_seat: PositivePublicInt
    to_seat: Optional[PositivePublicInt] = None


class PublicSheriffRunResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    choice: Literal["run", "pass"]


class PublicSheriffWithdrawResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    choice: Literal["stay", "withdraw"]


class PublicSheriffVoteResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    voter_seat: PositivePublicInt
    target_seat: Optional[PositivePublicInt] = None
    kind: Literal["vote", "pk"]


class PublicSheriffSideResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    side: Literal["sheriff_left", "sheriff_right", "death_left", "death_right"]


class PublicSpeakingResponse(_PublicResponse):
    """The seat whose model is generating a speech. Not the finished line."""
    round_number: NonNegativePublicInt
    seat: PositivePublicInt


class PublicSelfExplodeResponse(_PublicResponse):
    """A daytime self-destruct that took another seat along."""
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: PositivePublicInt


class PublicPlayerRevealedResponse(_PublicResponse):
    """A publicly flipped identity (the role is intentionally public)."""
    seat_number: PositivePublicInt
    role: str
    camp: PublicWinningCamp


class PublicKnightDuelResponse(_PublicResponse):
    """The knight's public duel verdict, including the challenged camp.

    The judge announces the verdict, so the audience sees which camp was
    challenged — the players' own view never receives this event.
    """
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: PositivePublicInt
    camp: PublicWinningCamp


class PublicWolfBeautyCharmResponse(_PublicResponse):
    """The wolf beauty's charm, published because this stream is the god view.

    ``target_seat`` is absent when the charm passed instead of naming a seat.
    """
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: Optional[PositivePublicInt] = None


class PublicWolfBeautyRevengeResponse(_PublicResponse):
    """The charmed victim dying with the wolf beauty."""
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: PositivePublicInt
    cause: PublicDeathCause


class PublicPlayerStatusResponse(_PublicResponse):
    """A status marker a settlement left on a seat (poisoned / wounded / ...).

    Unknown statuses are carried through verbatim: the contract stays open here
    because the marker vocabulary belongs to the settlement, and a new marker
    must not turn the whole replay into HTTP 500.
    """
    round_number: NonNegativePublicInt
    player_seat: PositivePublicInt
    status: Annotated[str, Field(min_length=1)]


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


class PublicTechnicalAbstainReplayEvent(_PublicReplayEvent):
    event_type: Literal["technical_abstain"]
    payload: PublicTechnicalAbstainResponse


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


class PublicExileCancelledReplayEvent(_PublicReplayEvent):
    event_type: Literal["exile_cancelled"]
    payload: PublicExileCancelledResponse


class PublicSelfExplodeReplayEvent(_PublicReplayEvent):
    event_type: Literal["self_explode"]
    payload: PublicSelfExplodeResponse


class PublicKnightDuelReplayEvent(_PublicReplayEvent):
    event_type: Literal["knight_duel"]
    payload: PublicKnightDuelResponse


class PublicWolfBeautyCharmReplayEvent(_PublicReplayEvent):
    event_type: Literal["wolf_beauty_charm"]
    payload: PublicWolfBeautyCharmResponse


class PublicWolfBeautyRevengeReplayEvent(_PublicReplayEvent):
    event_type: Literal["wolf_beauty_revenge"]
    payload: PublicWolfBeautyRevengeResponse


class PublicPlayerStatusReplayEvent(_PublicReplayEvent):
    event_type: Literal["player_status"]
    payload: PublicPlayerStatusResponse


class PublicPlayerRevealedReplayEvent(_PublicReplayEvent):
    event_type: Literal["player_revealed"]
    payload: PublicPlayerRevealedResponse


class PublicSheriffElectedReplayEvent(_PublicReplayEvent):
    event_type: Literal["sheriff_elected"]
    payload: PublicSheriffElectedResponse


class PublicSheriffBadgeReplayEvent(_PublicReplayEvent):
    event_type: Literal["sheriff_badge"]
    payload: PublicSheriffBadgeResponse


class PublicSheriffRunReplayEvent(_PublicReplayEvent):
    event_type: Literal["sheriff_run"]
    payload: PublicSheriffRunResponse


class PublicSheriffWithdrawReplayEvent(_PublicReplayEvent):
    event_type: Literal["sheriff_withdraw"]
    payload: PublicSheriffWithdrawResponse


class PublicSheriffVoteReplayEvent(_PublicReplayEvent):
    event_type: Literal["sheriff_vote"]
    payload: PublicSheriffVoteResponse


class PublicSheriffSideReplayEvent(_PublicReplayEvent):
    event_type: Literal["sheriff_side"]
    payload: PublicSheriffSideResponse


class PublicSpeakingReplayEvent(_PublicReplayEvent):
    event_type: Literal["speaking"]
    payload: PublicSpeakingResponse


PublicReplayEvent = Annotated[
    Union[
        PublicExileCancelledReplayEvent,
        PublicSelfExplodeReplayEvent,
        PublicKnightDuelReplayEvent,
        PublicWolfBeautyCharmReplayEvent,
        PublicWolfBeautyRevengeReplayEvent,
        PublicPlayerStatusReplayEvent,
        PublicSheriffElectedReplayEvent,
        PublicSheriffBadgeReplayEvent,
        PublicSheriffRunReplayEvent,
        PublicSheriffWithdrawReplayEvent,
        PublicSheriffVoteReplayEvent,
        PublicSheriffSideReplayEvent,
        PublicSpeakingReplayEvent,
        PublicPlayerRevealedReplayEvent,
        PublicSpeechReplayEvent,
        PublicDeathReplayEvent,
        PublicVoteReplayEvent,
        PublicVoteResultReplayEvent,
        PublicTechnicalAbstainReplayEvent,
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
    reveal_on_death: bool
    enable_sheriff: bool = False
    players: dict[PositivePublicInt, PublicPlayerResponse]
    sheriff: Optional[PositivePublicInt]
    speeches: list[PublicSpeechResponse]
    death_history: list[PublicDeathResponse]
    win_result: Optional[PublicWinnerResponse]
    execution_status: str = "running"
    recoverable: bool = False
    recovery_block_code: Optional[str] = None
    interruption_count: NonNegativePublicInt = 0
    benchmark_run_id: Optional[str] = None
    model_snapshot: list[ModelSnapshotEntry] = Field(default_factory=list)

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


class GameExecutionResponse(_PublicResponse):
    game_id: str
    execution_status: str
    recoverable: bool
    recovery_block_code: Optional[str]
    interruption_count: NonNegativePublicInt
    benchmark_run_id: Optional[str]


class AudienceSnapshotResponse(_PublicResponse):
    game_id: str
    schema_version: PositivePublicInt
    projection_version: PositivePublicInt
    last_seq: NonNegativePublicInt
    state: dict[str, object]


class AudienceEventResponse(_PublicResponse):
    game_id: str
    seq: PositivePublicInt
    event_id: str
    schema_version: PositivePublicInt
    event_type: str
    timestamp: str
    payload: dict[str, object]


class AudienceEventPageResponse(_PublicResponse):
    game_id: str
    events: list[AudienceEventResponse]
    next_seq: NonNegativePublicInt
    high_watermark: NonNegativePublicInt
    has_more: bool


class WSMessage(BaseModel):
    type: str
    payload: dict = {}
