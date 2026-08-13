from __future__ import annotations

import math
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType

from app.core.effect_applier import CommitResult
from app.core.state_transaction import state_transaction_lock
from app.models.game import GameState
from app.models.pipeline import IssuedActionRequest, SchedulePoint

_INT32 = 2_147_483_647
_GUARD = Lock()
_JOURNALS: dict[int, tuple[weakref.ReferenceType[GameState], "PointJournal"]] = {}


def _text(value: object, name: str) -> str:
    if type(value) is not str: raise TypeError(f"{name} must be a string")
    try: encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError: raise ValueError(f"{name} must be UTF-8") from None
    if not encoded or len(encoded) > 256: raise ValueError(f"invalid {name}")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int: raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= _INT32: raise ValueError(f"invalid {name}")
    return value


def _freeze(value: object, *, depth: int = 0, active: set[int] | None = None,
            nodes: list[int] | None = None) -> object:
    if depth > 64: raise ValueError("JSON exceeds maximum depth")
    nodes = [0] if nodes is None else nodes; nodes[0] += 1
    if nodes[0] > 10_000: raise ValueError("JSON is too large")
    if value is None or type(value) is bool: return value
    if type(value) is int: return _integer(value, "JSON integer")
    if type(value) is float:
        if not math.isfinite(value): raise ValueError("JSON number must be finite")
        return value
    if type(value) is str: return _text(value, "JSON string")
    if not isinstance(value, (Mapping, tuple, list)): raise TypeError("unsupported JSON value")
    active = set() if active is None else active; identity = id(value)
    if identity in active: raise ValueError("JSON contains cycle")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value): raise TypeError("JSON keys must be strings")
            return MappingProxyType({key: _freeze(item, depth=depth + 1, active=active, nodes=nodes)
                                     for key, item in value.items()})
        return tuple(_freeze(item, depth=depth + 1, active=active, nodes=nodes) for item in value)
    finally: active.remove(identity)


def _mapping_tuple(value: object, name: str, nodes: list[int]) -> tuple[Mapping[str, object], ...]:
    if type(value) is not tuple or any(not isinstance(item, Mapping) for item in value):
        raise TypeError(f"{name} must be a tuple of mappings")
    return tuple(_freeze(item, nodes=nodes) for item in value)


@dataclass(frozen=True)
class PointKey:
    game_id: str
    round_number: int
    phase: str
    point: SchedulePoint
    registry_digest: str

    def __post_init__(self) -> None:
        _text(self.game_id, "game_id"); _integer(self.round_number, "round_number")
        _text(self.phase, "phase"); _text(self.registry_digest, "registry_digest")
        if type(self.point) is not SchedulePoint: raise TypeError("point must be SchedulePoint")


@dataclass(frozen=True)
class WorkCursor:
    kind: str
    index: int
    subindex: int

    def __post_init__(self) -> None:
        if self.kind not in ("main", "response", "done"): raise ValueError("invalid cursor kind")
        _integer(self.index, "cursor index"); _integer(self.subindex, "cursor subindex")


@dataclass(frozen=True)
class PendingEvent(Mapping[str, int]):
    commit_index: int
    ordinal: int
    depth: int

    def __post_init__(self) -> None:
        _integer(self.commit_index, "commit_index"); _integer(self.ordinal, "ordinal")
        _integer(self.depth, "depth")
        if self.depth > 8: raise ValueError("pending event depth exceeds maximum")

    def __getitem__(self, key: str) -> int:
        if key not in ("commit_index", "ordinal", "depth"): raise KeyError(key)
        return getattr(self, key)

    def __iter__(self): return iter(("commit_index", "ordinal", "depth"))
    def __len__(self) -> int:
        return 3


@dataclass(frozen=True)
class PointCheckpoint:
    issued: tuple[IssuedActionRequest, ...]
    actual: tuple[IssuedActionRequest, ...]
    commits: tuple[CommitResult, ...]
    events: tuple[Mapping[str, object], ...]
    faults: tuple[Mapping[str, object], ...]
    pending: tuple[PendingEvent, ...]
    cursor: WorkCursor
    complete_result: Mapping[str, object] | None = None
    work_count: int = _INT32

    def __post_init__(self) -> None:
        for name in ("issued", "actual"):
            value = getattr(self, name)
            if type(value) is not tuple or any(type(item) is not IssuedActionRequest for item in value):
                raise TypeError(f"invalid {name}")
        if type(self.commits) is not tuple or any(type(item) is not CommitResult for item in self.commits):
            raise TypeError("invalid commits")
        if type(self.cursor) is not WorkCursor: raise TypeError("invalid cursor")
        _integer(self.work_count, "work_count")
        nodes = [0]
        for name in ("events", "faults"):
            object.__setattr__(self, name, _mapping_tuple(getattr(self, name), name, nodes))
        if type(self.pending) is not tuple: raise TypeError("pending must be a tuple")
        converted = []
        for item in self.pending:
            if type(item) is not PendingEvent and isinstance(item, Mapping) and set(item) == {"commit_index", "ordinal", "depth"}:
                item = PendingEvent(item["commit_index"], item["ordinal"], item["depth"])
            if type(item) is not PendingEvent: raise TypeError("invalid pending event")
            if item.commit_index >= len(self.commits) or item.ordinal >= len(self.commits[item.commit_index].events):
                raise ValueError("pending event is out of range")
            converted.append(item)
        object.__setattr__(self, "pending", tuple(converted))
        if self.cursor.kind == "main" and (self.cursor.index > self.work_count or self.cursor.subindex):
            raise ValueError("invalid main cursor")
        if self.cursor.kind == "response" and (self.cursor.index > len(self.pending) or
                self.cursor.index == len(self.pending) and self.cursor.subindex):
            raise ValueError("invalid response cursor")
        if self.cursor.kind == "done" and (self.cursor.index or self.cursor.subindex or self.pending):
            raise ValueError("invalid done cursor")
        if self.complete_result is not None and self.cursor.kind != "done":
            raise ValueError("complete result requires done cursor")
        if self.complete_result is not None:
            if not isinstance(self.complete_result, Mapping): raise TypeError("complete_result must be a mapping")
            object.__setattr__(self, "complete_result", _freeze(self.complete_result, nodes=nodes))


class PointJournal:
    def __init__(self, state: GameState) -> None:
        self._state = weakref.ref(state); self._values: dict[PointKey, PointCheckpoint] = {}

    def _lock(self):
        state = self._state()
        if state is None: raise RuntimeError("game state is no longer available")
        return state_transaction_lock(state)

    def get(self, key: PointKey) -> PointCheckpoint | None:
        if type(key) is not PointKey: raise TypeError("key must be PointKey")
        with self._lock(): return self._values.get(key)

    def put(self, key: PointKey, checkpoint: PointCheckpoint) -> None:
        if type(key) is not PointKey: raise TypeError("key must be PointKey")
        if type(checkpoint) is not PointCheckpoint: raise TypeError("checkpoint must be PointCheckpoint")
        with self._lock(): self._values[key] = checkpoint

    def clear(self, key: PointKey | None = None) -> bool:
        if key is not None and type(key) is not PointKey: raise TypeError("key must be PointKey")
        with self._lock():
            if key is None:
                changed = bool(self._values); self._values.clear(); return changed
            return self._values.pop(key, None) is not None


def point_journal(state: GameState) -> PointJournal:
    if type(state) is not GameState: raise TypeError("state must be GameState")
    identity = id(state)
    with _GUARD:
        entry = _JOURNALS.get(identity)
        if entry is None or entry[0]() is not state:
            store = PointJournal(state)
            reference = weakref.ref(state, lambda current, key=identity: _drop(key, current))
            _JOURNALS[identity] = (reference, store)
        return _JOURNALS[identity][1]


def _drop(identity: int, reference: weakref.ReferenceType[GameState]) -> None:
    with _GUARD:
        entry = _JOURNALS.get(identity)
        if entry is not None and entry[0] is reference: _JOURNALS.pop(identity, None)
