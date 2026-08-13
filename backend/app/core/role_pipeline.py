from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

from app.config import PipelineMode
from app.core.effect_applier import CommitResult
from app.core.scheduler import PointResult
from app.models.game import GameState
from app.models.pipeline import SchedulePoint

_INT32 = 2_147_483_647


def _freeze(value: object, depth: int = 0, active: set[int] | None = None,
            nodes: list[int] | None = None) -> object:
    if depth > 64: raise ValueError("value exceeds maximum depth")
    nodes = [0] if nodes is None else nodes; nodes[0] += 1
    if nodes[0] > 10_000: raise ValueError("value is too large")
    if value is None or type(value) is bool: return value
    if type(value) is int:
        if not 0 <= value <= _INT32: raise ValueError("integer out of range")
        return value
    if type(value) is float:
        if not math.isfinite(value): raise ValueError("number must be finite")
        return value
    if type(value) is str:
        try: value.encode("utf-8", errors="strict")
        except UnicodeEncodeError: raise ValueError("string must be UTF-8") from None
        if len(value) > 65_536: raise ValueError("string is too long")
        return value
    if not isinstance(value, (Mapping, list, tuple)): raise TypeError("unsupported value")
    active = set() if active is None else active; identity = id(value)
    if identity in active: raise ValueError("value contains cycle")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value): raise TypeError("mapping keys must be strings")
            return MappingProxyType({key: _freeze(item, depth + 1, active, nodes) for key, item in value.items()})
        return tuple(_freeze(item, depth + 1, active, nodes) for item in value)
    finally: active.remove(identity)


def _strings(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(item) is not str for item in value):
        raise TypeError(f"{name} must be a tuple of strings")
    if len(value) > 4096: raise ValueError(f"{name} has too many items")
    size = 0
    for item in value:
        try: encoded = item.encode("utf-8", errors="strict")
        except UnicodeEncodeError: raise ValueError(f"{name} must be UTF-8") from None
        if not item or len(encoded) > 256: raise ValueError(f"invalid {name} item")
        size += len(encoded)
    if size > 65_536: raise ValueError(f"{name} is too large")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str: raise TypeError(f"{name} must be a string")
    try: encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError: raise ValueError(f"{name} must be UTF-8") from None
    if not value or len(encoded) > 256: raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class PipelineObservation:
    accepted_actions: tuple[str, ...]
    effects: tuple[str, ...]
    state_digest: str
    public_events: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        _strings(self.accepted_actions, "accepted_actions"); _strings(self.effects, "effects")
        _text(self.state_digest, "state_digest")
        if type(self.public_events) is not tuple or any(not isinstance(item, Mapping) for item in self.public_events):
            raise TypeError("public_events must contain mappings")
        object.__setattr__(self, "public_events", tuple(_freeze(item) for item in self.public_events))


@dataclass(frozen=True)
class PipelineDiff:
    matched: bool
    mismatches: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.matched) is not bool: raise TypeError("matched must be a bool")
        _strings(self.mismatches, "mismatches")
        order = ("accepted_actions", "effects", "state_digest", "public_events")
        if self.mismatches != tuple(name for name in order if name in self.mismatches):
            raise ValueError("invalid mismatches")


@dataclass(frozen=True)
class PipelineResult(PipelineObservation):
    mode: PipelineMode
    diff: PipelineDiff | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.mode) is not PipelineMode: raise TypeError("mode must be a PipelineMode")
        if self.diff is not None and type(self.diff) is not PipelineDiff: raise TypeError("diff must be a PipelineDiff")


@dataclass(frozen=True)
class RolePipeline:
    mode: PipelineMode
    v1_runner: Callable[[GameState, SchedulePoint], PipelineObservation] | None
    scheduler: object | None

    def __post_init__(self) -> None:
        if type(self.mode) is not PipelineMode: raise TypeError("mode must be a PipelineMode")
        if self.v1_runner is not None and not callable(self.v1_runner): raise TypeError("v1_runner must be callable")
        if self.mode is not PipelineMode.V2 and self.v1_runner is None: raise ValueError("v1 runner is required")
        if self.mode is not PipelineMode.V1 and (self.scheduler is None or not callable(getattr(self.scheduler, "run_point", None))):
            raise ValueError("scheduler is required")

    def run_point(self, state: GameState, point: SchedulePoint) -> PipelineResult:
        return self.run_points(state, (point,))

    def run_points(self, state: GameState, points: tuple[SchedulePoint, ...]) -> PipelineResult:
        if type(state) is not GameState: raise TypeError("state must be GameState")
        if type(points) is not tuple or not points or any(type(point) is not SchedulePoint for point in points):
            raise TypeError("points must be a nonempty tuple of SchedulePoint")
        if len(points) != len(set(points)): raise ValueError("points must be unique")
        if self.mode is PipelineMode.V2:
            return self._result(self._v2_points(state, points), None)
        shadow = deepcopy(state) if self.mode is PipelineMode.SHADOW else None
        first = self.v1_runner(state, points[0])
        if type(first) is not PipelineObservation: raise TypeError("v1 runner must return PipelineObservation")
        if shadow is None: return self._result(first, None)
        second = self._v2_points(shadow, points)
        names = tuple(name for name in ("accepted_actions", "effects", "state_digest", "public_events")
                      if getattr(first, name) != getattr(second, name))
        return self._result(first, PipelineDiff(not names, names))

    def _v2_points(self, state: GameState, points: tuple[SchedulePoint, ...]) -> PipelineObservation:
        observations = tuple(self._v2(state, point) for point in points)
        return PipelineObservation(
            tuple(item for value in observations for item in value.accepted_actions),
            tuple(item for value in observations for item in value.effects),
            observations[-1].state_digest,
            tuple(item for value in observations for item in value.public_events),
        )

    def _v2(self, state: GameState, point: SchedulePoint) -> PipelineObservation:
        result = self.scheduler.run_point(state, point)
        if type(result) is not PointResult: raise TypeError("scheduler must return exact PointResult")
        commits = result.commits
        if type(commits) is not tuple or any(type(commit) is not CommitResult for commit in commits):
            raise TypeError("PointResult must contain exact commits")
        events = tuple(event for commit in commits for event in commit.events if self._public(event))
        return PipelineObservation(
            tuple(commit.action_key for commit in commits),
            tuple(effect for commit in commits for effect in commit.effect_ids),
            result.state_digest, events,
        )

    @staticmethod
    def _public(event: object) -> bool:
        if not isinstance(event, Mapping): return False
        visibility = event.get("visibility")
        if type(visibility) is not tuple or any(type(item) is not str for item in visibility): return False
        try:
            if any(not item or len(item.encode("utf-8", errors="strict")) > 256 for item in visibility): return False
        except UnicodeEncodeError: return False
        return "PUBLIC" in visibility

    def _result(self, value: PipelineObservation, diff: PipelineDiff | None) -> PipelineResult:
        return PipelineResult(value.accepted_actions, value.effects, value.state_digest,
                              value.public_events, self.mode, diff)
