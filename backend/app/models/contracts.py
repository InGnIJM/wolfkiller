from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel, ConfigDict

from app.models.game import Camp, GamePhase

if TYPE_CHECKING:
    from app.roles.base import BaseRole


@dataclass(frozen=True)
class ActionContract:
    contract_id: str
    phase: GamePhase
    action_types: tuple[str, ...]
    actions_requiring_target: frozenset[str]
    resolution_priority: int
    fallback_action_type: str
    tool_name: str | None = None

    @property
    def resolved_tool_name(self) -> str:
        return self.tool_name or self.contract_id

    def json_schema(self) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action_type": {"type": "string", "enum": list(self.action_types)},
                "target_seat": {"type": ["integer", "null"]},
                "reasoning": {"type": "string", "maxLength": 500},
            },
            "required": ["action_type", "target_seat", "reasoning"],
        }


@dataclass(frozen=True)
class RoleSpec:
    role_id: str
    camp: Camp
    role_factory: Callable[..., BaseRole]
    contracts: tuple[ActionContract, ...]


class ActionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str
    target_seat: int | None
    reasoning: str


@dataclass(frozen=True)
class ActionRequest:
    actor_seat: int
    role_id: str
    contract: ActionContract
    phase: GamePhase
    round_id: int
    idempotency_key: str


@dataclass(frozen=True)
class AcceptedAction:
    request: ActionRequest
    command: ActionCommand
    technical_failure_code: str | None = None
    timeout_type: str | None = None
