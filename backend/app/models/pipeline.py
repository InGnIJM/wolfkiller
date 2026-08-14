"""Frozen pipeline values for trusted in-process rule code.

Freezing prevents accidental mutation; it is not a sandbox against malicious
trusted code using ``object.__setattr__`` or Pydantic copying APIs. All external
data must enter through a constructor or ``from_json`` validation boundary, and
registry hooks are trusted code reviewed and shipped with the server.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
MAX_JSON_DEPTH = 64


class SchedulePoint(str, Enum):
    GAME_SETUP = "game_setup"
    NIGHT_ACTION = "night_action"
    NIGHT_WOLF_VOTE = "night_wolf_vote"
    NIGHT_WITCH_ACTION = "night_witch_action"
    NIGHT_SEER_ACTION = "night_seer_action"
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


def _require_utf8(value: str, *, path: str) -> None:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{path} must be valid UTF-8 Unicode") from error


def _freeze_json(
    value: object,
    *,
    path: str = "value",
    _depth: int = 0,
    _active: set[int] | None = None,
) -> JsonValue:
    if _depth > MAX_JSON_DEPTH:
        raise ValueError(f"{path} exceeds maximum JSON depth {MAX_JSON_DEPTH}")
    if _active is None:
        _active = set()
    if isinstance(value, Enum):
        raise TypeError(f"{path} must contain only JSON values")
    if isinstance(value, str):
        _require_utf8(value, path=path)
        return value
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in _active:
            raise ValueError(f"{path} contains a JSON container cycle")
        _active.add(identity)
        try:
            frozen: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{path} JSON object keys must be strings")
                _require_utf8(key, path=f"{path} key")
                frozen[key] = _freeze_json(
                    item,
                    path=f"{path}.{key}",
                    _depth=_depth + 1,
                    _active=_active,
                )
            return MappingProxyType(frozen)
        finally:
            _active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in _active:
            raise ValueError(f"{path} contains a JSON container cycle")
        _active.add(identity)
        try:
            return tuple(
                _freeze_json(
                    item,
                    path=f"{path}[{index}]",
                    _depth=_depth + 1,
                    _active=_active,
                )
                for index, item in enumerate(value)
            )
        finally:
            _active.remove(identity)
    raise TypeError(f"{path} must contain only JSON values")


def _freeze_int_mapping(value: Mapping[str, int], *, path: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    frozen: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{path} keys must be strings")
        _require_utf8(key, path=f"{path} key")
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError(f"{path} values must be integers")
        if not 0 <= item <= 2_147_483_647:
            raise ValueError(f"{path} values must be between 0 and 2147483647")
        frozen[key] = item
    return MappingProxyType(frozen)


def _callable_name(value: Callable[..., object]) -> str:
    return f"{value.__module__}.{value.__qualname__}"


def _require_str(name: str, value: object, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    _require_utf8(value, path=name)


def _require_int(name: str, value: object, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")


def _require_bool(name: str, value: object) -> None:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a boolean")


def _require_tuple_of(name: str, value: object, item_type: type) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if any(type(item) is not item_type for item in value):
        raise TypeError(f"{name} elements must be {item_type.__name__}")
    if item_type is str:
        for index, item in enumerate(value):
            _require_utf8(item, path=f"{name}[{index}]")


def _require_str_frozenset(name: str, value: object) -> None:
    if type(value) is not frozenset:
        raise TypeError(f"{name} must be a frozenset")
    if any(type(item) is not str for item in value):
        raise TypeError(f"{name} elements must be str")
    for item in value:
        _require_utf8(item, path=f"{name} item")


def _require_hook(name: str, value: object) -> None:
    if value is None:
        return
    if not inspect.isfunction(value):
        raise TypeError(f"Hook {name} must be a module top-level Python function")
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if (
        type(module) is not str
        or type(qualname) is not str
        or not module
        or not qualname
        or value.__name__ == "<lambda>"
        or "<locals>" in qualname
        or value.__closure__ is not None
    ):
        raise ValueError(f"Hook {name} must be a stable module top-level Python function")
    _require_utf8(module, path=f"Hook {name} module")
    _require_utf8(qualname, path=f"Hook {name} qualname")


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
        return hashlib.sha256(_stable_json(self).encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, raw: str) -> Any:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid JSON document") from error
        if not isinstance(value, dict):
            raise ValueError("JSON document must be an object")
        return cls._from_serialized_mapping(value)

    @classmethod
    def _from_serialized_mapping(cls, raw: Mapping[str, object]) -> Any:
        return cls.from_mapping(raw)

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
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an integer")
        if not 1 <= self.schema_version <= self.SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")


@dataclass(frozen=True)
class ActionContext(_FrozenValue):
    game_id: str
    revision: int
    facts: Mapping[str, JsonValue]
    schema_version: int = 1
    config_version: str = ""
    contract_id: str = ""
    contract_version: int = 1
    contract_digest: str = ""
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
    trigger_event: Mapping[str, JsonValue] | None = None
    trigger_reason: str | None = None
    accepted_command_summaries: tuple[JsonValue, ...] = ()
    aggregate_result: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        self._validate_schema_version()
        for name in (
            "game_id",
            "config_version",
            "contract_id",
            "contract_digest",
            "phase",
            "window_id",
            "actor_role_id",
            "action_key",
        ):
            _require_str(name, getattr(self, name))
        _require_str("source_event_id", self.source_event_id, optional=True)
        _require_str("trigger_reason", self.trigger_reason, optional=True)
        _require_int("revision", self.revision)
        _require_int("contract_version", self.contract_version)
        _require_int("round_number", self.round_number)
        _require_int("actor_seat", self.actor_seat)
        _require_bool("actor_alive", self.actor_alive)
        try:
            point = SchedulePoint(self.schedule_point)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown schedule_point: {self.schedule_point}") from error
        object.__setattr__(self, "schedule_point", point)
        if not isinstance(self.resources, Mapping):
            raise TypeError("resources must be a mapping")
        if not isinstance(self.facts, Mapping):
            raise TypeError("facts must be a mapping")
        object.__setattr__(self, "resources", _freeze_json(self.resources, path="resources"))
        object.__setattr__(self, "facts", _freeze_json(self.facts, path="facts"))
        object.__setattr__(self, "counters", _freeze_int_mapping(self.counters, path="counters"))
        if self.trigger_event is not None:
            if not isinstance(self.trigger_event, Mapping):
                raise TypeError("trigger_event must be a mapping")
            object.__setattr__(
                self, "trigger_event", _freeze_json(self.trigger_event, path="trigger_event")
            )
        summaries = _freeze_json(
            self.accepted_command_summaries, path="accepted_command_summaries"
        )
        if not isinstance(summaries, tuple):
            raise TypeError("accepted_command_summaries must be a sequence")
        object.__setattr__(self, "accepted_command_summaries", summaries)
        if self.aggregate_result is not None:
            if not isinstance(self.aggregate_result, Mapping):
                raise TypeError("aggregate_result must be a mapping")
            object.__setattr__(
                self,
                "aggregate_result",
                _freeze_json(self.aggregate_result, path="aggregate_result"),
            )


class ActionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    action_type: StrictStr
    target_seat: StrictInt | None
    reasoning: StrictStr = Field(max_length=500)

    @field_validator("action_type", "reasoning")
    @classmethod
    def _validate_utf8_string(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "string")
        _require_utf8(value, path=field_name)
        return value

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
    selected_target_fact_namespaces: frozenset[str] = frozenset()
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
        _require_str("contract_id", self.contract_id)
        _require_int("order", self.order)
        _require_tuple_of("action_types", self.action_types, str)
        _require_str_frozenset("actions_requiring_target", self.actions_requiring_target)
        _require_str("fallback_action_type", self.fallback_action_type)
        _require_str_frozenset("visibility_namespaces", self.visibility_namespaces)
        _require_str_frozenset("selected_target_fact_namespaces", self.selected_target_fact_namespaces)
        if any(not item or len(item) > 128 or not all(char.isalnum() or char in "_.:-" for char in item)
               for item in self.selected_target_fact_namespaces):
            raise ValueError("invalid selected target fact namespace")
        _require_str_frozenset("response_event_types", self.response_event_types)
        _require_str_frozenset("response_reasons", self.response_reasons)
        _require_int("per_window_limit", self.per_window_limit)
        _require_int("per_round_limit", self.per_round_limit, optional=True)
        _require_int("per_game_limit", self.per_game_limit, optional=True)
        for name in ("is_applicable", "validate", "resolve", "react", "aggregate"):
            _require_hook(name, getattr(self, name))
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
        object.__setattr__(self, "selected_target_fact_namespaces", frozenset(self.selected_target_fact_namespaces))
        object.__setattr__(self, "response_event_types", frozenset(self.response_event_types))
        object.__setattr__(self, "response_reasons", frozenset(self.response_reasons))

    @classmethod
    def _from_serialized_mapping(cls, raw: Mapping[str, object]) -> "ActionContract":
        converted = dict(raw)
        for hook_name in ("is_applicable", "validate", "resolve", "react", "aggregate"):
            if converted.get(hook_name) is not None:
                raise ValueError(f"Hook {hook_name} must be rebound by the trusted registry")
        for name in (
            "action_types",
            "visibility_namespaces",
            "selected_target_fact_namespaces",
            "response_event_types",
            "response_reasons",
        ):
            if isinstance(converted.get(name), list):
                converted[name] = tuple(converted[name]) if name == "action_types" else frozenset(converted[name])
        if isinstance(converted.get("actions_requiring_target"), list):
            converted["actions_requiring_target"] = frozenset(
                converted["actions_requiring_target"]
            )
        if isinstance(converted.get("allowed_effects"), list):
            converted["allowed_effects"] = frozenset(converted["allowed_effects"])
        return cls.from_mapping(converted)


@dataclass(frozen=True)
class RoleSpec(_FrozenValue):
    SCHEMA_VERSION: ClassVar[int] = 2

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
        for name in ("role_id", "display_name", "camp_id", "instructions"):
            _require_str(name, getattr(self, name))
        _require_tuple_of("contracts", self.contracts, ActionContract)
        for name in (
            "visibility_namespaces",
            "tags",
            "dependencies",
            "exclusions",
        ):
            _require_str_frozenset(name, getattr(self, name))
        _require_int("min_count", self.min_count)
        _require_int("max_count", self.max_count, optional=True)
        contracts = tuple(
            contract
            if isinstance(contract, ActionContract)
            else ActionContract.from_mapping(contract)
            for contract in self.contracts
        )
        object.__setattr__(self, "contracts", contracts)
        if not isinstance(self.initial_resources, Mapping):
            raise TypeError("initial_resources must be a mapping")
        if not isinstance(self.initial_private_data, Mapping):
            raise TypeError("initial_private_data must be a mapping")
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

    @classmethod
    def _from_serialized_mapping(cls, raw: Mapping[str, object]) -> "RoleSpec":
        converted = dict(raw)
        contracts = converted.get("contracts", [])
        if not isinstance(contracts, list):
            raise TypeError("contracts must be a JSON array")
        converted["contracts"] = tuple(
            ActionContract._from_serialized_mapping(contract)
            if isinstance(contract, Mapping)
            else contract
            for contract in contracts
        )
        for name in (
            "visibility_namespaces",
            "allowed_effects",
            "tags",
            "dependencies",
            "exclusions",
        ):
            if isinstance(converted.get(name), list):
                converted[name] = frozenset(converted[name])
        return cls.from_mapping(converted)


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
        _require_int("actor_seat", self.actor_seat)
        _require_str("role_id", self.role_id)
        _require_int("context_revision", self.context_revision)
        _require_int("round_number", self.round_number)
        _require_str("phase", self.phase)
        _require_str("window_id", self.window_id)
        _require_str("action_key", self.action_key)
        if not isinstance(self.contract, ActionContract):
            if not isinstance(self.contract, Mapping):
                raise TypeError("contract must be an ActionContract")
            object.__setattr__(
                self, "contract", ActionContract._from_serialized_mapping(self.contract)
            )

    @classmethod
    def _from_serialized_mapping(
        cls, raw: Mapping[str, object]
    ) -> "IssuedActionRequest":
        converted = dict(raw)
        contract = converted.get("contract")
        if isinstance(contract, Mapping):
            converted["contract"] = ActionContract._from_serialized_mapping(contract)
        return cls.from_mapping(converted)


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
        _require_str("code", self.code)
        _require_str("message", self.message)
        if not isinstance(self.details, Mapping):
            raise TypeError("details must be a mapping")
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
        _require_str("effect_id", self.effect_id)
        _require_str("source_action_key", self.source_action_key)
        _require_tuple_of("visibility", self.visibility, str)
        _require_int("expected_revision", self.expected_revision)
        _require_int("target_seat", self.target_seat, optional=True)
        _require_str("source_event_id", self.source_event_id, optional=True)
        _require_tuple_of("sort_key", self.sort_key, int)
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not isinstance(self.preconditions, Mapping):
            raise TypeError("preconditions must be a mapping")
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

    @classmethod
    def _from_serialized_mapping(cls, raw: Mapping[str, object]) -> "GameEffect":
        converted = dict(raw)
        for name in ("visibility", "sort_key"):
            if isinstance(converted.get(name), list):
                converted[name] = tuple(converted[name])
        return cls.from_mapping(converted)
