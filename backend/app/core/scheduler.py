from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.core.action_resolver import ActionResolver, RuleExecutionError
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import CommitResult, EffectApplier, EffectPermission
from app.models.game import GameState
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, IssuedActionRequest,
    RoleSpec, SchedulePoint,
)
from app.roles.registry import RegistrySnapshot

_EVENT_ID = re.compile(r"^event:[0-9a-f]{16,64}$")
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class ResponseLimitExceeded(RuntimeError): pass
class PipelinePaused(RuntimeError): pass


def _text(value: object, name: str, token: bool = False) -> str:
    if type(value) is not str: raise TypeError(f"{name} must be a string")
    try: value.encode("utf-8")
    except UnicodeEncodeError as error: raise ValueError(f"{name} must be UTF-8") from error
    if not value or len(value) > 256 or token and _TOKEN.fullmatch(value) is None: raise ValueError(f"invalid {name}")
    return value


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value): raise TypeError("mapping keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)): return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, int, float, bool): return value
    raise TypeError("unsupported frozen value")


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    payload: Mapping[str, object]
    reason: str | None = None

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        if _EVENT_ID.fullmatch(self.event_id) is None: raise ValueError("invalid event_id")
        _text(self.event_type, "event_type", token=True)
        if self.reason is not None: _text(self.reason, "reason", token=True)
        if not isinstance(self.payload, Mapping): raise TypeError("payload must be a mapping")
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True)
class ResponseWindow:
    window_id: str
    event_id: str
    contract_id: str
    actor_seat: int
    depth: int

    def __post_init__(self) -> None:
        for name in ("window_id", "event_id", "contract_id"): _text(getattr(self, name), name)
        for name in ("actor_seat", "depth"):
            value = getattr(self, name)
            if type(value) is not int or value < (1 if name == "actor_seat" else 0): raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class PointResult:
    requests: tuple[IssuedActionRequest, ...]
    commits: tuple[CommitResult, ...]
    events: tuple[Mapping[str, object], ...]
    state_digest: str
    faults: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        for name in ("requests", "commits", "events", "faults"):
            if type(getattr(self, name)) is not tuple: raise TypeError(f"{name} must be a tuple")
        _text(self.state_digest, "state_digest")
        object.__setattr__(self, "events", tuple(_freeze(event) for event in self.events))
        object.__setattr__(self, "faults", tuple(_freeze(fault) for fault in self.faults))


def _digest(*parts: object) -> str:
    return hashlib.sha256("\0".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def stable_window_id(game_id: str, source_event_id: str, contract_id: str, actor_seat: int) -> str:
    _text(game_id, "game_id"); _text(source_event_id, "source_event_id"); _text(contract_id, "contract_id")
    if type(actor_seat) is not int or actor_seat <= 0: raise ValueError("invalid actor_seat")
    return _digest(game_id, source_event_id, contract_id, actor_seat)


class ResponseQueue:
    def __init__(self, registry: RegistrySnapshot, *, max_depth: int = 8, max_events: int = 64):
        if type(registry) is not RegistrySnapshot: raise TypeError("registry must be RegistrySnapshot")
        for value, name in ((max_depth, "max_depth"), (max_events, "max_events")):
            if type(value) is not int or value < 0: raise ValueError(f"invalid {name}")
        self.registry, self.max_depth, self.max_events = registry, max_depth, max_events
        self._seen_events: set[str] = set(); self._windows: dict[str, ResponseWindow] = {}

    def open(self, state: GameState, event: DomainEvent, *, depth: int) -> tuple[ResponseWindow, ...]:
        if type(state) is not GameState or type(event) is not DomainEvent: raise TypeError("invalid response input")
        if type(depth) is not int or depth < 0: raise ValueError("invalid depth")
        if depth > self.max_depth: raise ResponseLimitExceeded("response depth exceeded")
        if event.event_id in self._seen_events:
            return tuple(window for window in self._windows.values() if window.event_id == event.event_id)
        if len(self._seen_events) >= self.max_events: raise ResponseLimitExceeded("event limit exceeded")
        self._seen_events.add(event.event_id)
        target = event.payload.get("target_seat")
        if type(target) is not int or target <= 0: return ()
        player = state.players.get(target)
        if player is None: return ()
        windows = []
        spec = self.registry.specs.get(player.role)
        if spec is None: return ()
        contracts = sorted(spec.contracts, key=lambda c: (c.order, spec.role_id, c.contract_id, target))
        for contract in contracts:
            if event.event_type not in contract.response_event_types: continue
            if event.reason is not None and event.reason not in contract.response_reasons: continue
            window = ResponseWindow(stable_window_id(state.game_id, event.event_id, contract.contract_id, target), event.event_id, contract.contract_id, target, depth)
            self._windows.setdefault(window.window_id, window); windows.append(self._windows[window.window_id])
        return tuple(windows)


class Scheduler:
    def __init__(self, registry: RegistrySnapshot, projector: ContextProjector,
                 validator: ActionValidator, resolver: ActionResolver,
                 applier: EffectApplier, command_provider: Callable[..., ActionCommand]):
        self.registry, self.projector, self.validator = registry, projector, validator
        self.resolver, self.applier, self.command_provider = resolver, applier, command_provider

    @staticmethod
    def _revision(state: GameState) -> int:
        runtime = getattr(state, "_pipeline_runtime", None)
        return 0 if runtime is None else runtime.revision

    def issue(self, state: GameState, point: SchedulePoint, registry: RegistrySnapshot) -> tuple[IssuedActionRequest, ...]:
        if type(state) is not GameState or type(point) is not SchedulePoint or type(registry) is not RegistrySnapshot: raise TypeError("invalid issue input")
        for player in state.players.values(): registry.require(player.role)
        revision = self._revision(state); phase = state.phase.value if hasattr(state.phase, "value") else state.phase
        requests = []
        for role_id, role in registry.specs.items():
            for contract in role.contracts:
                if contract.schedule_point is not point: continue
                for seat, player in state.players.items():
                    if player.role != role_id or not player.is_alive: continue
                    token = _digest(state.game_id, state.round_number, point.value, seat, contract.contract_id, contract.schema_version, registry.digest)
                    request = IssuedActionRequest(seat, role_id, contract, revision, state.round_number, phase, token, token)
                    context = self.projector.project(state, request, registry)
                    try: applies = contract.is_applicable is None or contract.is_applicable(context)
                    except Exception: raise PipelinePaused("applicability rule failed") from None
                    if type(applies) is not bool: raise PipelinePaused("applicability rule failed")
                    if applies: requests.append(request)
        return tuple(sorted(requests, key=lambda r: (r.contract.order, r.role_id, r.contract.contract_id, r.actor_seat)))

    def run_point(self, state: GameState, point: SchedulePoint) -> PointResult:
        requests = self.issue(state, point, self.registry); accepted = []
        for request in requests:
            context = self.projector.project(state, request, self.registry)
            command = None
            for attempt in (0, 1):
                command = self.command_provider(request, context, attempt)
                if type(command) is not ActionCommand: raise TypeError("provider must return ActionCommand")
                if not self.validator.validate(context, request.contract, command): break
            if command is None or self.validator.validate(context, request.contract, command):
                command = ActionCommand(action_type=request.contract.fallback_action_type, target_seat=None, reasoning="safe fallback")
                if self.validator.validate(context, request.contract, command): raise PipelinePaused("fallback command invalid")
            accepted.append((request, context, command))
        commits, events = [], []
        groups: dict[tuple[str, str], list] = {}
        for item in accepted: groups.setdefault((item[0].role_id, item[0].contract.contract_id), []).append(item)
        for items in groups.values():
            request, context, command = items[0]; role = self.registry.require(request.role_id)
            try:
                if request.contract.aggregate is None:
                    batches = [(context, self.resolver.resolve_effects(context, role, request.contract, item[2])) for item in items]
                else:
                    key = _digest(*(item[0].action_key for item in items))
                    group_request = IssuedActionRequest(request.actor_seat, request.role_id, request.contract, self._revision(state), request.round_number, request.phase, key, key)
                    group_context = self.projector.project(state, group_request, self.registry)
                    batches = [(group_context, self.resolver.aggregate_effects(group_context, role, request.contract, tuple(item[2] for item in items)))]
            except RuleExecutionError: raise PipelinePaused("rule execution failed") from None
            for bound_context, effects in batches:
                permission = EffectPermission(bound_context.actor_seat, role.allowed_effects, request.contract.allowed_effects,
                    frozenset(bound_context.facts.get("alive_seats", ())) | {bound_context.actor_seat},
                    role.visibility_namespaces & request.contract.visibility_namespaces)
                commit = self.applier.apply(state, effects, permission); commits.append(commit); events.extend(commit.events)
        digest = commits[-1].state_digest if commits else _digest(state.game_id, self._revision(state))
        return PointResult(requests, tuple(commits), tuple(events), digest)

    @staticmethod
    def can_advance(*, pending_requests: int, pending_effects: int, queued_events: int) -> bool:
        values = (pending_requests, pending_effects, queued_events)
        if any(type(value) is not int for value in values): raise TypeError("pending counts must be integers")
        if any(value < 0 for value in values): raise ValueError("pending counts must be nonnegative")
        return values == (0, 0, 0)
