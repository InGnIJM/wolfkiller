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
    victory: Literal["edge", "city"] = "edge"
    model_assignments: Optional[list[ModelAssignment]] = None
    commentary_enabled: bool = False
    commentary_config_id: Optional[str] = None
    vote_model_config_id: Optional[str] = None

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
    pause_code: Optional[str] = None
    pause_reason: Optional[str] = None
    pause_detail: Optional[dict[str, object]] = None
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
    "knight_duel", "charm", "wolf_king_shot", "reflect", "dream_death",
    "verify_kill", "hunt", "dance", "awakened_hunt", "stand_in", "bond_death",
    "miracle_backlash",
    "radiance_backlash",
    "love_death",
]
PublicWinningCamp = Literal["good", "werewolf", "third_party"]
PublicWinReason = Literal[
    "all_gods_dead", "all_villagers_dead", "all_wolves_dead",
    "wolves_outnumber_good", "all_good_dead", "all_others_dead",
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


class PublicCrimsonExplodeResponse(_PublicResponse):
    """The Crimson Envoy's self-destruct: who exploded and who is sealed tonight.

    It carries no passenger (unlike the White Wolf King), so the payload names
    the sealed seats instead of a victim.
    """

    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    #: 座位数组。**必须是 list 而不是 tuple**：回放分支产出的是 JSON 数组
    #: （`game_routes` 里 `list(...)`），而 `_PublicResponse` 是 `strict=True` ——
    #: 声明成 tuple 会让任何含这条事件的对局在 `/api/games/{id}/logs` 上直接 500
    #: （实测 13/253 局），前端 `store/types.ts` 也一直是 `number[]`。
    silenced_seats: list[PositivePublicInt] = Field(default_factory=list)


class PublicDancePoolResponse(_PublicResponse):
    """The Dancer's three guests for the night (the verdict comes later)."""

    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    #: 同 `PublicCrimsonExplodeResponse.silenced_seats`：回放分支发的是 list，
    #: 这里必须是 list（strict 模式下 tuple 会拒绝它）。
    pool_seats: list[PositivePublicInt] = Field(default_factory=list)


class PublicFragranceBondResponse(_PublicResponse):
    """The Fragrance Phantom's pair for the night (the god view only).

    The two bound players are never told, so nothing reaches their prompts; the
    audience stream publishes it the same way the Wolf Beauty's charm is
    published.
    """

    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    #: 同 `PublicCrimsonExplodeResponse.silenced_seats`：回放分支发 list。
    target_seats: list[PositivePublicInt] = Field(default_factory=list)


class PublicMaskGrantedResponse(_PublicResponse):
    """The Mask's target, and whether that seat was dancing tonight."""

    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: PositivePublicInt
    in_pool: bool = False


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
    "wolf_king_shot", "guard_protect", "awakened_hunt", "fool_seer_check",
]


class PublicNightActionResponse(_PublicResponse):
    action_type: PublicNightActionType
    target_seat: PositivePublicInt
    round_number: NonNegativePublicInt
    vote_counts: Optional[dict[str, PositivePublicInt]] = None
    result: Optional[PublicWinningCamp] = None
    #: Who cast the skill. Pack wolf kills omit it (the camp acted together);
    #: a lone hidden-wolf kill and every individual skill include it. Archives
    #: written before this field existed leave it unset.
    seat: Optional[PositivePublicInt] = None
    #: The model's original target when a Time Wolf lock redirected the cast.
    #: Absent when the skill landed where it was aimed.
    intended_seat: Optional[PositivePublicInt] = None


class PublicVoteRewoundResponse(_PublicResponse):
    """The Order Prince voided this round's first exile tally."""

    seat: PositivePublicInt
    round_number: NonNegativePublicInt


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
    #: 按座位列出的胜者。只有「胜负不等于阵营」的板子（孤独少女随崇拜对象的阵营
    #: 胜负）才会带上它；没有孤独少女的对局逐字节保持历史形状。
    winners: Optional[list[PositivePublicInt]] = None


PublicThoughtActionType = Literal[
    "hunter_reasoning",
    "wolf_king_reasoning",
    "dream_weaver_reasoning",
    "gargoyle_reasoning",
    "witch_reasoning",
    "seer_reasoning",
    "guard_reasoning",
    "werewolf_king_reasoning",
    "knight_reasoning",
    "wolf_beauty_reasoning",
    "pure_white_reasoning",
    "wolf_witch_reasoning",
    "demon_hunter_reasoning",
    "crimson_envoy_reasoning",
    "nightmare_reasoning",
    "dancer_reasoning",
    "mask_reasoning",
    "awakened_hunter_reasoning",
    "awakened_beauty_reasoning",
    "awakened_seer_reasoning",
    "fragrance_phantom_reasoning",
    "miracle_merchant_reasoning",
    "lucky_reasoning",
    "mirror_girl_reasoning",
    "awakened_hidden_wolf_reasoning",
    "radiance_earl_reasoning",
    "eclipse_maid_reasoning",
    "time_wolf_reasoning",
    "order_prince_reasoning",
    "fool_seer_reasoning",
    "fool_seer_reveal_reasoning",
    "iron_villager_reasoning",
    "crow_reasoning",
    "cupid_reasoning",
    "admirer_reasoning",
]


class PublicNightThoughtResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    action_type: PublicThoughtActionType
    target_seat: Optional[PositivePublicInt] = None
    reasoning: str
    #: 引擎代行的动作（模型故障或答复连续不可用时的兜底）。可选，且**必须**可选：
    #: 本仓库后加的键，早于它的记录没有它；`extra="forbid"` 下漏掉这一行会让所有
    #: 含降级动作的对局在回放接口上 500。语义见 `models/pipeline.DEGRADED_THOUGHT`。
    degraded: Optional[bool] = None


class PublicNarrationResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    title: Annotated[str, Field(min_length=1, max_length=100)]
    text: Annotated[str, Field(min_length=1, max_length=200)]


class PublicCommentaryResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    beat: Literal["night", "day", "final"]
    title: Annotated[str, Field(min_length=1, max_length=100)]
    text: Annotated[str, Field(min_length=1)]
    model_id: Optional[str] = None
    generated_at: Optional[str] = None
    status: Optional[Literal["ok", "failed"]] = None
    failure_code: Optional[str] = None


class PublicWolfChatMessageResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    #: No length bound: wolf chat is free model text and the audience projection
    #: serves it unbounded (`AudienceEventResponse.payload` is an open dict). The
    #: old 200-char cap made the file replay silently drop every longer message
    #: (39% of them in a 40-game sample) while the live stream kept them.
    text: Annotated[str, Field(min_length=1)]
    #: 该次发言给出的次日计划；旧日志里没有这个字段，所以是可选的。
    day_plan: Optional[Annotated[str, Field(min_length=1, max_length=400)]] = None


class PublicWolfVoteResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: Optional[PositivePublicInt] = None
    reasoning: Annotated[str, Field(max_length=500)]
    #: 同 `PublicNightThoughtResponse.degraded`：引擎代投的狼票带这个键，
    #: 旧记录没有它，所以可选。
    degraded: Optional[bool] = None


class PublicWitchThoughtResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    #: Same contract as the wolf chat text: unbounded, matching the audience
    #: projection. Both share one projector branch, so they share the bound.
    text: Annotated[str, Field(min_length=1)]


class PublicSeerThoughtResponse(_PublicResponse):
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    text: Annotated[str, Field(min_length=1)]


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
    #: 警长的决策理由（观众上帝视角）。旧日志里没有这个字段，所以是可选的。
    reasoning: Optional[Annotated[str, Field(max_length=500)]] = None


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


class PublicWraithIllusionResponse(_PublicResponse):
    """The awakened wolf beauty's illusion: who would die in her place.

    Published for the same reason the Wolf Beauty's charm is — this stream is
    the god view, and no player prompt ever reads it.
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


class PublicAwakenedWitchAssistPickResponse(_PublicResponse):
    """The Awakened Witch's assistant roster for one poison brew."""
    round_number: NonNegativePublicInt
    target_seat: PositivePublicInt
    assistant_seats: list[PositivePublicInt] = Field(default_factory=list)


class PublicAwakenedWitchAssistChatResponse(_PublicResponse):
    """One private line spoken inside the Awakened Witch's assistant group."""
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: PositivePublicInt
    text: Annotated[str, Field(min_length=1)]


class PublicAwakenedWitchAssistVoteResponse(_PublicResponse):
    """One assistant's agree / veto / pass vote."""
    round_number: NonNegativePublicInt
    seat: PositivePublicInt
    target_seat: PositivePublicInt
    vote: Literal["agree", "veto", "pass"]
    reasoning: str = ""


class PublicAwakenedWitchPoisonResultResponse(_PublicResponse):
    """Whether the brew landed, was vetoed, was incomplete, or was abandoned."""
    round_number: NonNegativePublicInt
    target_seat: PositivePublicInt
    result: Literal["passed", "vetoed", "incomplete", "aborted"]


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


class PublicVoteRewoundReplayEvent(_PublicReplayEvent):
    event_type: Literal["vote_rewound"]
    payload: PublicVoteRewoundResponse


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


class PublicCommentaryReplayEvent(_PublicReplayEvent):
    event_type: Literal["commentary"]
    payload: PublicCommentaryResponse


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


class PublicDancePoolReplayEvent(_PublicReplayEvent):
    event_type: Literal["dance_pool"]
    payload: PublicDancePoolResponse


class PublicMaskGrantedReplayEvent(_PublicReplayEvent):
    event_type: Literal["mask_granted"]
    payload: PublicMaskGrantedResponse


class PublicCrimsonExplodeReplayEvent(_PublicReplayEvent):
    event_type: Literal["crimson_explode"]
    payload: PublicCrimsonExplodeResponse


class PublicKnightDuelReplayEvent(_PublicReplayEvent):
    event_type: Literal["knight_duel"]
    payload: PublicKnightDuelResponse


class PublicWolfBeautyCharmReplayEvent(_PublicReplayEvent):
    event_type: Literal["wolf_beauty_charm"]
    payload: PublicWolfBeautyCharmResponse


class PublicWraithIllusionReplayEvent(_PublicReplayEvent):
    event_type: Literal["wraith_illusion"]
    payload: PublicWraithIllusionResponse


class PublicFragranceBondReplayEvent(_PublicReplayEvent):
    event_type: Literal["fragrance_bond"]
    payload: PublicFragranceBondResponse


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


class PublicAwakenedWitchAssistPickReplayEvent(_PublicReplayEvent):
    event_type: Literal["awakened_witch_assist_pick"]
    payload: PublicAwakenedWitchAssistPickResponse


class PublicAwakenedWitchAssistChatReplayEvent(_PublicReplayEvent):
    event_type: Literal["awakened_witch_assist_chat"]
    payload: PublicAwakenedWitchAssistChatResponse


class PublicAwakenedWitchAssistVoteReplayEvent(_PublicReplayEvent):
    event_type: Literal["awakened_witch_assist_vote"]
    payload: PublicAwakenedWitchAssistVoteResponse


class PublicAwakenedWitchPoisonResultReplayEvent(_PublicReplayEvent):
    event_type: Literal["awakened_witch_poison_result"]
    payload: PublicAwakenedWitchPoisonResultResponse


PublicReplayEvent = Annotated[
    Union[
        PublicExileCancelledReplayEvent,
        PublicSelfExplodeReplayEvent,
        PublicCrimsonExplodeReplayEvent,
        PublicDancePoolReplayEvent,
        PublicMaskGrantedReplayEvent,
        PublicKnightDuelReplayEvent,
        PublicWolfBeautyCharmReplayEvent,
        PublicWraithIllusionReplayEvent,
        PublicFragranceBondReplayEvent,
        PublicWolfBeautyRevengeReplayEvent,
        PublicPlayerStatusReplayEvent,
        PublicSheriffElectedReplayEvent,
        PublicSheriffBadgeReplayEvent,
        PublicSheriffRunReplayEvent,
        PublicSheriffWithdrawReplayEvent,
        PublicSheriffVoteReplayEvent,
        PublicSheriffSideReplayEvent,
        PublicSpeakingReplayEvent,
        PublicAwakenedWitchAssistPickReplayEvent,
        PublicAwakenedWitchAssistChatReplayEvent,
        PublicAwakenedWitchAssistVoteReplayEvent,
        PublicAwakenedWitchPoisonResultReplayEvent,
        PublicPlayerRevealedReplayEvent,
        PublicSpeechReplayEvent,
        PublicDeathReplayEvent,
        PublicVoteReplayEvent,
        PublicVoteResultReplayEvent,
        PublicVoteRewoundReplayEvent,
        PublicTechnicalAbstainReplayEvent,
        PublicNightActionReplayEvent,
        PublicNightThoughtReplayEvent,
        PublicNarrationReplayEvent,
        PublicCommentaryReplayEvent,
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
    pause_code: Optional[str] = None
    pause_reason: Optional[str] = None
    pause_detail: Optional[dict[str, object]] = None
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
    pause_code: Optional[str] = None
    pause_reason: Optional[str] = None
    pause_detail: Optional[dict[str, object]] = None
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


class ReviewMvpResponse(BaseModel):
    seat: int
    role: str
    reason: str


class ReviewTurningPointResponse(BaseModel):
    round: int
    title: str
    detail: str


class ReviewMistakeResponse(BaseModel):
    seat: int
    title: str
    detail: str


class ReviewRoundDigestResponse(BaseModel):
    round: int
    digest: str


class GameReviewBodyResponse(BaseModel):
    overview: str
    mvp: Optional[ReviewMvpResponse] = None
    turning_points: list[ReviewTurningPointResponse] = Field(default_factory=list)
    mistakes: list[ReviewMistakeResponse] = Field(default_factory=list)
    camp_assessment: dict[str, str] = Field(default_factory=dict)
    closing: str = ""
    round_digests: list[ReviewRoundDigestResponse] = Field(default_factory=list)


class ReviewStatusResponse(BaseModel):
    game_id: str
    status: Literal["none", "pending", "ready", "failed"]
    review: Optional[GameReviewBodyResponse] = None
    failure: Optional[str] = None
    generated_at: Optional[str] = None
    model_id: Optional[str] = None


class GenerateReviewRequest(BaseModel):
    force: bool = False

