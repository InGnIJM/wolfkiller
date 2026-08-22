"""Immutable voting-domain values shared by the engine and vote service."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


class VoteStatus(str, Enum):
    ACCEPTED_VOTE = "accepted_vote"
    VOLUNTARY_ABSTAIN = "voluntary_abstain"
    TECHNICAL_ABSTAIN = "technical_abstain"


class CastVoteArgs(BaseModel):
    """The only untrusted fields accepted from a model vote decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    action_type: Literal["vote", "abstain"]
    target_seat: StrictInt | None
    reasoning: StrictStr = Field(max_length=500)

    @model_validator(mode="after")
    def validate_target_relationship(self) -> "CastVoteArgs":
        if self.action_type == "vote" and self.target_seat is None:
            raise ValueError("vote requires target_seat")
        if self.action_type == "abstain" and self.target_seat is not None:
            raise ValueError("abstain forbids target_seat")
        if self.target_seat is not None and self.target_seat <= 0:
            raise ValueError("target_seat must be positive")
        return self

    def command_digest(self) -> str:
        semantic = {
            "schema_version": self.schema_version,
            "action_type": self.action_type,
            "target_seat": self.target_seat,
        }
        encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class VoteWindow(BaseModel):
    """Server-owned capability describing one normal or tiebreak vote window."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    game_id: StrictStr = Field(min_length=1, max_length=128)
    round_number: StrictInt = Field(ge=0)
    vote_round: StrictInt = Field(ge=1)
    eligible_voters: frozenset[StrictInt]
    eligible_targets: frozenset[StrictInt]
    deadline: StrictFloat = Field(gt=0)

    @property
    def window_id(self) -> str:
        return (
            f"{self.game_id}:{self.round_number}:vote_casting:"
            f"{self.vote_round}:cast_vote:v2"
        )

    def action_key(self, voter_seat: int) -> str:
        return f"{self.window_id}:{voter_seat}"


class VoteReceipt(BaseModel):
    """Persisted terminal result for one voter in one vote window."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    window_id: StrictStr = Field(min_length=1, max_length=512)
    action_key: StrictStr = Field(min_length=1, max_length=640)
    voter_seat: StrictInt = Field(gt=0)
    status: VoteStatus
    target_seat: StrictInt | None
    command_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_at: StrictFloat = Field(ge=0)
    failure_code: StrictStr | None = None
    timeout_type: StrictStr | None = None
    replayed: bool = False

    def as_replay(self) -> "VoteReceipt":
        return self.model_copy(update={"replayed": True})


class VoteError(ValueError):
    """Stable public failure classification for rejected vote submissions."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
