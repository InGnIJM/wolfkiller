from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


class SchedulePoint(str, Enum):
    GAME_SETUP = "game_setup"
    NIGHT_ACTION = "night_action"
    NIGHT_COMMIT = "night_commit"
    DAWN_REACTION = "dawn_reaction"
    DAY_ACTION = "day_action"
    VOTE_ACTION = "vote_action"
    ROUND_END = "round_end"
    GAME_END = "game_end"


class EffectKind(str, Enum):
    ACCEPT_ACTION = "accept_action"
    CONSUME_RESOURCE = "consume_resource"
    SET_RESOURCE = "set_resource"
    SET_PRIVATE_DATA = "set_private_data"
    ADD_STATUS = "add_status"
    REMOVE_STATUS = "remove_status"
    ADD_RELATION = "add_relation"
    REMOVE_RELATION = "remove_relation"
    RECORD_PRIVATE_FACT = "record_private_fact"
    SUBMIT_DAMAGE = "submit_damage"
    SUBMIT_PROTECTION = "submit_protection"
    MARK_DEATH = "mark_death"
    EMIT_EVENT = "emit_event"


def _freeze_json(value: object, *, path: str = "value") -> JsonValue:
    if isinstance(value, Enum):
        raise TypeError(f"{path} must contain only JSON values")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} JSON object keys must be strings")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(f"{path} must contain only JSON values")


def _freeze_int_mapping(value: Mapping[str, int], *, path: str) -> Mapping[str, int]:
    frozen: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{path} keys must be strings")
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError(f"{path} values must be integers")
        frozen[key] = item
    return MappingProxyType(frozen)


def _callable_name(value: Callable[..., object]) -> str:
    return f"{value.__module__}.{value.__qualname__}"


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    if callable(value):
        return _callable_name(value)
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
    return value


def _stable_json(value: object) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class _FrozenValue:
    SCHEMA_VERSION: ClassVar[int] = 1

    def to_json(self) -> str:
        return _stable_json(self)

    def stable_digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, raw: str) -> Any:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid JSON document") from error
        if not isinstance(value, dict):
            raise ValueError("JSON document must be an object")
        return cls.from_mapping(value)

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, object] | None = None, **values: object
    ) -> Any:
        if raw is not None and values:
            raise TypeError("provide either a mapping or keyword fields")
        if raw is None:
            raw = values
        if not isinstance(raw, Mapping):
            raise TypeError("value must be a mapping")
        field_names = {field.name for field in fields(cls)}
        unknown = set(raw) - field_names
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
        return cls(**dict(raw))

    def _validate_schema_version(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")


@dataclass(frozen=True)
class ActionContext(_FrozenValue):
    game_id: str
    revision: int
    facts: Mapping[str, JsonValue]
    schema_version: int = 1
    config_version: str = ""
    round_number: int = 0
    phase: str = ""
    window_id: str = ""
    schedule_point: SchedulePoint = SchedulePoint.GAME_SETUP
    actor_seat: int = 0
    actor_role_id: str = ""
    actor_alive: bool = False
    resources: Mapping[str, JsonValue] = field(
        default_factory=lambda: MappingProxyType({})
    )
    action_key: str = ""
    counters: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    source_event_id: str | None = None

    def __post_init__(self) -> None:
        self._validate_schema_version()
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be an integer")
        if isinstance(self.round_number, bool) or not isinstance(self.round_number, int):
            raise TypeError("round_number must be an integer")
        if isinstance(self.actor_seat, bool) or not isinstance(self.actor_seat, int):
            raise TypeError("actor_seat must be an integer")
        if not isinstance(self.actor_alive, bool):
            raise TypeError("actor_alive must be a boolean")
        try:
            point = SchedulePoint(self.schedule_point)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown schedule_point: {self.schedule_point}") from error
        object.__setattr__(self, "schedule_point", point)
        object.__setattr__(self, "resources", _freeze_json(self.resources, path="resources"))
        object.__setattr__(self, "facts", _freeze_json(self.facts, path="facts"))
        object.__setattr__(self, "counters", _freeze_int_mapping(self.counters, path="counters"))


class ActionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    action_type: StrictStr
    target_seat: StrictInt | None
    reasoning: StrictStr = Field(max_length=500)

    def to_json(self) -> str:
        return _stable_json(self.model_dump(mode="json"))

    @classmethod
    def from_json(cls, raw: str) -> "ActionCommand":
        return cls.model_validate_json(raw, strict=True)

    def stable_digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActionContract(_FrozenValue):
    contract_id: str
    schedule_point: SchedulePoint
    order: int
    action_types: tuple[str, ...]
    actions_requiring_target: frozenset[str]
    fallback_action_type: str
    schema_version: int = 1
    allowed_effects: frozenset[EffectKind] = frozenset()
    required_resources: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    visibility_namespaces: frozenset[str] = frozenset()
    response_event_types: frozenset[str] = frozenset()
    response_reasons: frozenset[str] = frozenset()
    per_window_limit: int = 1
    per_round_limit: int | None = None
    per_game_limit: int | None = None
    is_applicable: Callable[..., object] | None = None
    validate: Callable[..., object] | None = None
    resolve: Callable[..., object] | None = None
    react: Callable[..., object] | None = None
    aggregate: Callable[..., object] | None = None

    def __post_init__(self) -> None:
        self._validate_schema_version()
        try:
            point = SchedulePoint(self.schedule_point)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown schedule_point: {self.schedule_point}") from error
        object.__setattr__(self, "schedule_point", point)
        object.__setattr__(self, "action_types", tuple(self.action_types))
        object.__setattr__(
            self, "actions_requiring_target", frozenset(self.actions_requiring_target)
        )
        try:
            effects = frozenset(EffectKind(kind) for kind in self.allowed_effects)
        except (TypeError, ValueError) as error:
            raise ValueError("unknown allowed effect kind") from error
        object.__setattr__(self, "allowed_effects", effects)
        object.__setattr__(
            self, "required_resources", _freeze_int_mapping(self.required_resources, path="required_resources")
        )
        object.__setattr__(self, "visibility_namespaces", frozenset(self.visibility_namespaces))
        object.__setattr__(self, "response_event_types", frozenset(self.response_event_types))
        object.__setattr__(self, "response_reasons", frozenset(self.response_reasons))


@dataclass(frozen=True)
class RoleSpec(_FrozenValue):
    role_id: str
    display_name: str = ""
    camp_id: str = ""
    schema_version: int = 1
    contracts: tuple[ActionContract, ...] = ()
    initial_resources: Mapping[str, JsonValue] = field(
        default_factory=lambda: MappingProxyType({})
    )
    initial_private_data: Mapping[str, JsonValue] = field(
        default_factory=lambda: MappingProxyType({})
    )
    visibility_namespaces: frozenset[str] = frozenset()
    allowed_effects: frozenset[EffectKind] = frozenset()
    tags: frozenset[str] = frozenset()
    dependencies: frozenset[str] = frozenset()
    exclusions: frozenset[str] = frozenset()
    min_count: int = 0
    max_count: int | None = None
    instructions: str = ""

    def __post_init__(self) -> None:
        self._validate_schema_version()
        contracts = tuple(
            contract
            if isinstance(contract, ActionContract)
            else ActionContract.from_mapping(contract)
            for contract in self.contracts
        )
        object.__setattr__(self, "contracts", contracts)
        object.__setattr__(
            self, "initial_resources", _freeze_json(self.initial_resources, path="initial_resources")
        )
        object.__setattr__(
            self,
            "initial_private_data",
            _freeze_json(self.initial_private_data, path="initial_private_data"),
        )
        object.__setattr__(self, "visibility_namespaces", frozenset(self.visibility_namespaces))
        object.__setattr__(self, "tags", frozenset(self.tags))
        object.__setattr__(self, "dependencies", frozenset(self.dependencies))
        object.__setattr__(self, "exclusions", frozenset(self.exclusions))
        try:
            effects = frozenset(EffectKind(kind) for kind in self.allowed_effects)
        except (TypeError, ValueError) as error:
            raise ValueError("unknown allowed effect kind") from error
        object.__setattr__(self, "allowed_effects", effects)


@dataclass(frozen=True)
class IssuedActionRequest(_FrozenValue):
    actor_seat: int
    role_id: str
    contract: ActionContract
    context_revision: int
    round_number: int
    phase: str
    window_id: str
    action_key: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        self._validate_schema_version()
        if not isinstance(self.contract, ActionContract):
            object.__setattr__(self, "contract", ActionContract.from_mapping(self.contract))


@dataclass(frozen=True)
class RuleViolation(_FrozenValue):
    code: str
    message: str
    schema_version: int = 1
    details: Mapping[str, JsonValue] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        self._validate_schema_version()
        object.__setattr__(self, "details", _freeze_json(self.details, path="details"))


@dataclass(frozen=True)
class GameEffect(_FrozenValue):
    effect_id: str
    kind: EffectKind
    source_action_key: str
    schema_version: int = 1
    payload: Mapping[str, JsonValue] = field(
        default_factory=lambda: MappingProxyType({})
    )
    visibility: tuple[str, ...] = ()
    expected_revision: int = 0
    target_seat: int | None = None
    preconditions: Mapping[str, JsonValue] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_event_id: str | None = None
    sort_key: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        self._validate_schema_version()
        try:
            kind = EffectKind(self.kind)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown effect kind: {self.kind}") from error
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", _freeze_json(self.payload, path="payload"))
        object.__setattr__(
            self, "preconditions", _freeze_json(self.preconditions, path="preconditions")
        )
        object.__setattr__(self, "visibility", tuple(self.visibility))
        object.__setattr__(self, "sort_key", tuple(self.sort_key))
