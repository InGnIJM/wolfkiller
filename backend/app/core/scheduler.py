from __future__ import annotations

import hashlib
import math
import re
from collections import deque
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from queue import Empty, Queue
from threading import RLock, Semaphore, Thread
from time import monotonic
from types import MappingProxyType

from app.core.action_resolver import ActionResolver, RuleExecutionError
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import (
    CommitResult, EffectApplier, EffectPermission, initialize_role_resources,
)
from app.core.state_transaction import state_transaction_lock
from app.core.point_journal import PendingEvent, PointCheckpoint, PointKey, WorkCursor, point_journal
from app.models.game import GameState
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, IssuedActionRequest,
    RoleSpec, SchedulePoint,
)
from app.roles.registry import RegistrySnapshot

_EVENT_ID = re.compile(r"^event:[0-9a-f]{16,64}$")
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_INT32 = 2_147_483_647
_RULE_SLOTS = Semaphore(32)


class ResponseLimitExceeded(RuntimeError): pass
class PipelinePaused(RuntimeError): pass


def _text(value: object, name: str, token: bool = False) -> str:
    if type(value) is not str: raise TypeError(f"{name} must be a string")
    try: value.encode("utf-8")
    except UnicodeEncodeError as error: raise ValueError(f"{name} must be UTF-8") from error
    if not value or len(value) > 256 or token and _TOKEN.fullmatch(value) is None: raise ValueError(f"invalid {name}")
    return value


def _freeze(value: object, depth: int = 0, active: set[int] | None = None,
            nodes: list[int] | None = None) -> object:
    if depth > 64: raise ValueError("frozen value exceeds maximum depth")
    nodes = [0] if nodes is None else nodes; nodes[0] += 1
    if nodes[0] > 10_000: raise ValueError("frozen value is too large")
    if value is None or type(value) is bool: return value
    if type(value) is int:
        if not 0 <= value <= _INT32: raise ValueError("integer out of range")
        return value
    if type(value) is float:
        if not math.isfinite(value): raise ValueError("number must be finite")
        return value
    if type(value) is str: return _text(value, "value")
    if not isinstance(value, (Mapping, list, tuple)): raise TypeError("unsupported frozen value")
    active = set() if active is None else active; identity = id(value)
    if identity in active: raise ValueError("frozen value contains a cycle")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value): raise TypeError("mapping keys must be strings")
            return MappingProxyType({key: _freeze(item, depth + 1, active, nodes) for key, item in value.items()})
        return tuple(_freeze(item, depth + 1, active, nodes) for item in value)
    finally: active.remove(identity)


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
        if any(type(item) is not IssuedActionRequest for item in self.requests): raise TypeError("invalid request")
        if any(type(item) is not CommitResult for item in self.commits): raise TypeError("invalid commit")
        if any(not isinstance(item, Mapping) for item in self.events + self.faults): raise TypeError("invalid result mapping")
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
        self._events: deque[tuple[DomainEvent, int]] = deque(); self._seen: set[str] = set()
        self._windows: dict[str, tuple[ResponseWindow, ...]] = {}; self._lock = RLock()

    def enqueue(self, event: DomainEvent, *, depth: int) -> bool:
        if type(event) is not DomainEvent: raise TypeError("event must be DomainEvent")
        with self._lock:
            self._depth(depth)
            if event.event_id in self._seen: result = False
            else:
                if len(self._seen) >= self.max_events:
                    raise ResponseLimitExceeded("event limit exceeded")
                self._seen.add(event.event_id); self._events.append((event, depth)); result = True
        return result

    def pop(self, state: GameState) -> tuple[DomainEvent, int, tuple[ResponseWindow, ...]] | None:
        if type(state) is not GameState: raise TypeError("state must be GameState")
        with self._lock:
            if not self._events: result = None
            else:
                event, depth = self._events.popleft(); result = (event, depth, self._open_new(state, event, depth))
        return result

    def open(self, state: GameState, event: DomainEvent, *, depth: int) -> tuple[ResponseWindow, ...]:
        if type(state) is not GameState or type(event) is not DomainEvent: raise TypeError("invalid response input")
        with self._lock:
            self._depth(depth)
            if event.event_id in self._windows: result = self._windows[event.event_id]
            else:
                if event.event_id not in self._seen:
                    if len(self._seen) >= self.max_events:
                        raise ResponseLimitExceeded("event limit exceeded")
                    self._seen.add(event.event_id)
                result = self._open_new(state, event, depth)
        return result

    def _depth(self, depth: int) -> None:
        if type(depth) is not int or depth < 0: raise ValueError("invalid depth")
        if depth > self.max_depth: raise ResponseLimitExceeded("response depth exceeded")

    def _open_new(self, state: GameState, event: DomainEvent, depth: int) -> tuple[ResponseWindow, ...]:
        if event.event_id in self._windows: return self._windows[event.event_id]
        target = event.payload.get("target_seat")
        player = state.players.get(target) if type(target) is int and target > 0 else None
        spec = None if player is None else self.registry.specs.get(player.role)
        windows = []
        for contract in () if spec is None else sorted(spec.contracts, key=lambda c: (c.order, spec.role_id, c.contract_id, target)):
            if event.event_type not in contract.response_event_types: continue
            if contract.response_reasons and event.reason not in contract.response_reasons: continue
            if not contract.response_reasons and event.reason is not None: continue
            windows.append(ResponseWindow(stable_window_id(state.game_id, event.event_id, contract.contract_id, target), event.event_id, contract.contract_id, target, depth))
        self._windows[event.event_id] = tuple(windows); return self._windows[event.event_id]


class Scheduler:
    def __init__(self, registry: RegistrySnapshot, projector: ContextProjector,
                 validator: ActionValidator, resolver: ActionResolver,
                 applier: EffectApplier, command_provider: Callable[..., ActionCommand],
                 *, hook_soft_ms: int = 50, hook_hard_ms: int = 200):
        for value, name in ((hook_soft_ms, "hook_soft_ms"), (hook_hard_ms, "hook_hard_ms")):
            if type(value) is not int: raise TypeError(f"{name} must be an integer")
            if value <= 0: raise ValueError(f"{name} must be positive")
        if hook_soft_ms > hook_hard_ms: raise ValueError("soft budget must not exceed hard budget")
        self.registry, self.projector, self.validator = registry, projector, validator
        self.resolver, self.applier, self.command_provider = resolver, applier, command_provider
        self.hook_soft_ms, self.hook_hard_ms = hook_soft_ms, hook_hard_ms
        self._faults: ContextVar[list[Mapping[str, object]] | None] = ContextVar("scheduler_faults", default=None)

    def _rule_call(self, label: str, call: Callable[[], object]) -> object:
        """Bound trusted hooks; a timed-out daemon may finish but cannot access GameState."""
        if not _RULE_SLOTS.acquire(blocking=False): raise PipelinePaused("rule execution capacity exceeded")
        output: Queue[tuple[bool, object]] = Queue(maxsize=1)
        def invoke() -> None:
            try: output.put((True, call()))
            except BaseException as error: output.put((False, error))
            finally: _RULE_SLOTS.release()
        started = monotonic(); Thread(target=invoke, daemon=True).start()
        try: successful, value = output.get(timeout=self.hook_hard_ms / 1000)
        except Empty: raise PipelinePaused("rule execution timed out") from None
        elapsed = (monotonic() - started) * 1000
        if elapsed > self.hook_soft_ms and (faults := self._faults.get()) is not None:
            faults.append({"code": "slow_rule", "label": label, "elapsed_bucket": "soft_exceeded"})
        if successful: return value
        if not isinstance(value, Exception): raise value
        if isinstance(value, RuleExecutionError): raise value
        raise PipelinePaused(f"{label} rule failed") from None

    @staticmethod
    def _revision(state: GameState) -> int:
        runtime = getattr(state, "_pipeline_runtime", None)
        return 0 if runtime is None else runtime.revision

    def issue(self, state: GameState, point: SchedulePoint, registry: RegistrySnapshot) -> tuple[IssuedActionRequest, ...]:
        if type(state) is not GameState or type(point) is not SchedulePoint or type(registry) is not RegistrySnapshot: raise TypeError("invalid issue input")
        with state_transaction_lock(state): return self._issue_locked(state, point, registry)

    def _issue_locked(self, state: GameState, point: SchedulePoint, registry: RegistrySnapshot) -> tuple[IssuedActionRequest, ...]:
        for player in state.players.values(): registry.require(player.role)
        initialize_role_resources(state, registry.specs, registry.digest)
        revision = self._revision(state); phase = state.phase.value if hasattr(state.phase, "value") else state.phase
        requests = []
        for role_id, role in registry.specs.items():
            for contract in role.contracts:
                if contract.schedule_point is not point: continue
                candidates = tuple(sorted(
                    (seat, player) for seat, player in state.players.items()
                    if player.role == role_id and player.is_alive
                ))
                prepared = []
                for seat, player in candidates:
                    token = _digest(state.game_id, state.round_number, point.value, seat, contract.contract_id, contract.schema_version, registry.digest)
                    request = IssuedActionRequest(seat, role_id, contract, revision, state.round_number, phase, token, token)
                    context = self.projector.project(state, request, registry)
                    prepared.append((request, context))
                if contract.aggregate is not None and prepared and self._limit_reached(prepared[0][1], contract): continue
                for request, context in prepared:
                    if contract.aggregate is None and self._limit_reached(context, contract): continue
                    applies = contract.is_applicable is None or self._rule_call("applicability", lambda: contract.is_applicable(context))
                    if type(applies) is not bool: raise PipelinePaused("applicability rule failed")
                    if applies: requests.append(request)
        return tuple(sorted(requests, key=lambda r: (r.contract.order, r.role_id, r.contract.contract_id, r.actor_seat)))

    @staticmethod
    def _limit_reached(context: ActionContext, contract: ActionContract) -> bool:
        limits = (("window", contract.per_window_limit), ("round", contract.per_round_limit),
                  ("game", contract.per_game_limit))
        return any(limit is not None and context.counters.get(scope, 0) >= limit
                   for scope, limit in limits)

    def _bind(self, request: IssuedActionRequest, state: GameState, *, action_key: str | None = None) -> IssuedActionRequest:
        return IssuedActionRequest(request.actor_seat, request.role_id, request.contract, self._revision(state),
                                   request.round_number, request.phase, request.window_id,
                                   request.action_key if action_key is None else action_key)

    def _command(self, state: GameState, request: IssuedActionRequest,
                 context: ActionContext) -> tuple[ActionCommand, ActionContext]:
        for attempt in (0, 1):
            try: command = self.command_provider(request, context, attempt)
            except Exception: raise PipelinePaused("command provider failed") from None
            if type(command) is not ActionCommand: raise TypeError("provider must return ActionCommand")
            rule_context = self.projector.project_selected_target(
                state, request, context, command, self.registry
            )
            violations = self._rule_call("validation", lambda: self.validator.validate(rule_context, request.contract, command))
            if not violations: return command, rule_context
        return self._fallback(state, request, context)

    def _fallback(self, state: GameState, request: IssuedActionRequest,
                  context: ActionContext) -> tuple[ActionCommand, ActionContext]:
        contract = request.contract
        command = ActionCommand(action_type=contract.fallback_action_type, target_seat=None, reasoning="safe fallback")
        rule_context = self.projector.project_selected_target(
            state, request, context, command, self.registry
        )
        violations = self._rule_call("validation", lambda: self.validator.validate(rule_context, contract, command))
        if violations: raise PipelinePaused("fallback command invalid")
        return command, rule_context

    def _resolve_with_fallback(self, state: GameState, request: IssuedActionRequest,
                               context: ActionContext, role: RoleSpec,
                               command: ActionCommand):
        contract = request.contract
        try: return self._rule_call("resolve", lambda: self.resolver.resolve_effects(context, role, contract, command))
        except RuleExecutionError:
            if command.action_type == contract.fallback_action_type: raise PipelinePaused("rule execution failed") from None
            base_context = self.projector.project(state, request, self.registry)
            fallback, fallback_context = self._fallback(state, request, base_context)
            try: return self._rule_call("resolve", lambda: self.resolver.resolve_effects(fallback_context, role, contract, fallback))
            except RuleExecutionError: raise PipelinePaused("rule execution failed") from None

    @staticmethod
    def _permission(context: ActionContext, role: RoleSpec, contract: ActionContract) -> EffectPermission:
        return EffectPermission(context.actor_seat, role.allowed_effects, contract.allowed_effects,
            frozenset(context.facts.get("alive_seats", ())) | {context.actor_seat},
            role.visibility_namespaces & contract.visibility_namespaces)

    def _apply(self, state: GameState, context: ActionContext, role: RoleSpec,
               contract: ActionContract, effects, commits: list, events: list) -> CommitResult:
        commit = self.applier.apply(state, effects, self._permission(context, role, contract))
        commits.append(commit); events.extend(commit.events); return commit

    @staticmethod
    def _domain(commit: CommitResult, ordinal: int, raw: Mapping[str, object]) -> DomainEvent:
        try:
            if not isinstance(raw, Mapping) or type(raw.get("event_type")) is not str or not isinstance(raw.get("payload"), Mapping): raise TypeError
            payload = dict(raw["payload"])
            if "seat" in payload and "target_seat" not in payload: payload["target_seat"] = payload.pop("seat")
            reason = payload.get("cause") if type(payload.get("cause")) is str else None
            return DomainEvent("event:" + _digest(commit.action_key, ordinal), raw["event_type"], payload, reason)
        except Exception: raise PipelinePaused("invalid internal event") from None

    def run_point(self, state: GameState, point: SchedulePoint) -> PointResult:
        if type(state) is not GameState: raise TypeError("state must be GameState")
        with state_transaction_lock(state):
            phase = state.phase.value if hasattr(state.phase, "value") else state.phase
            key = PointKey(state.game_id, state.round_number, phase, point, self.registry.digest)
            journal = point_journal(state); saved = journal.get(key)
            faults = list(saved.faults) if saved is not None else []
            token = self._faults.set(faults)
            try:
                result = self._point_result(saved) if saved is not None and saved.complete_result is not None else \
                    self._run_point_locked(state, point, faults, key, saved, journal)
                return result
            finally: self._faults.reset(token)

    def _run_point_locked(self, state: GameState, point: SchedulePoint,
                          faults: list[Mapping[str, object]], key: PointKey,
                          saved: PointCheckpoint | None, journal) -> PointResult:
        if saved is None:
            issued = () if point is SchedulePoint.NIGHT_COMMIT else self.issue(state, point, self.registry)
        else: issued = saved.issued
        groups: dict[tuple[str, str], list[IssuedActionRequest]] = {}
        for request in issued: groups.setdefault((request.role_id, request.contract.contract_id), []).append(request)
        work = [("settlement", ())] if point is SchedulePoint.NIGHT_COMMIT else []
        for members in groups.values():
            contract = members[0].contract; role = self.registry.require(members[0].role_id)
            if contract.aggregate is None:
                work.extend(("normal", (member,)) for member in members)
            else: work.append(("aggregate", tuple(members)))
        work_count = len(work)
        if saved is None:
            saved = PointCheckpoint(issued, (), (), (), tuple(faults), (), WorkCursor("main", 0, 0), work_count=work_count)
            journal.put(key, saved)
        actual = list(saved.actual); commits = list(saved.commits); events = list(saved.events)
        pending = list(saved.pending)
        for index in range(saved.cursor.index if saved.cursor.kind == "main" else len(work), len(work)):
            kind, members = work[index]; before = len(commits)
            if kind == "settlement":
                revision = self._revision(state); settlement = self.applier.settle_pending(state, round_number=state.round_number)
                if settlement is not None and settlement.revision > revision:
                    commits.append(settlement); events.extend(settlement.events)
            elif kind == "normal":
                member = members[0]; contract = member.contract; role = self.registry.require(member.role_id)
                request = self._bind(member, state); context = self.projector.project(state, request, self.registry)
                command, rule_context = self._command(state, request, context)
                effects = self._resolve_with_fallback(state, request, rule_context, role, command)
                actual.append(request); self._apply(state, rule_context, role, contract, effects, commits, events)
            else:
                contract = members[0].contract; role = self.registry.require(members[0].role_id)
                bound = tuple(self._bind(member, state) for member in members)
                contexts = tuple(self.projector.project(state, request, self.registry) for request in bound)
                commands = tuple(self._command(state, request, context)[0] for request, context in zip(bound, contexts))
                group_key = _digest(*(sorted(request.action_key for request in bound)))
                group_request = self._bind(bound[0], state, action_key=group_key)
                group_context = self.projector.project(state, group_request, self.registry)
                try: effects = self._rule_call("aggregate", lambda: self.resolver.aggregate_effects(group_context, role, contract, commands))
                except RuleExecutionError:
                    if all(command.action_type == contract.fallback_action_type for command in commands): raise PipelinePaused("rule execution failed") from None
                    fallbacks = tuple(self._fallback(state, request, context)[0] for request, context in zip(bound, contexts))
                    try: effects = self._rule_call("aggregate", lambda: self.resolver.aggregate_effects(group_context, role, contract, fallbacks))
                    except RuleExecutionError: raise PipelinePaused("rule execution failed") from None
                actual.extend(bound); self._apply(state, group_context, role, contract, effects, commits, events)
            for commit_index in range(before, len(commits)):
                pending.extend(PendingEvent(commit_index, ordinal, 0)
                               for ordinal, _ in enumerate(commits[commit_index].events))
            saved = PointCheckpoint(issued, tuple(actual), tuple(commits), tuple(events), tuple(faults),
                                    tuple(pending), WorkCursor("main", index + 1, 0), work_count=work_count)
            journal.put(key, saved)
        if saved.cursor.kind == "main":
            saved = PointCheckpoint(issued, tuple(actual), tuple(commits), tuple(events), tuple(faults),
                                    tuple(pending), WorkCursor("response", 0, 0), work_count=work_count)
            journal.put(key, saved)
        event_index, subindex = saved.cursor.index, saved.cursor.subindex
        queue = ResponseQueue(self.registry)
        while event_index < len(pending):
            item = pending[event_index]; commit = commits[item.commit_index]
            event = self._domain(commit, item.ordinal, commit.events[item.ordinal])
            windows = queue.open(state, event, depth=item.depth)
            while subindex < len(windows):
                window = windows[subindex]
                player = state.players.get(window.actor_seat); role = None if player is None else self.registry.specs.get(player.role)
                contract = None if role is None else next((item for item in role.contracts if item.contract_id == window.contract_id), None)
                if contract is None: raise PipelinePaused("invalid response window")
                base = IssuedActionRequest(window.actor_seat, role.role_id, contract, self._revision(state), state.round_number,
                    state.phase.value if hasattr(state.phase, "value") else state.phase, window.window_id, window.window_id)
                trigger = {"event_id": event.event_id, "type": event.event_type, **{name: event.payload[name] for name in ("source_seat", "target_seat", "cause", "round_number", "phase") if name in event.payload}}
                try: context = self.projector.project(state, base, self.registry, source_event_id=event.event_id, trigger_event=trigger, trigger_reason=event.reason)
                except Exception: raise PipelinePaused("invalid response context") from None
                if self._limit_reached(context, contract):
                    subindex += 1
                    saved = PointCheckpoint(issued, tuple(actual), tuple(commits), tuple(events), tuple(faults),
                        tuple(pending), WorkCursor("response", event_index, subindex), work_count=work_count)
                    journal.put(key, saved); continue
                if contract.react is not None:
                    try: effects = self._rule_call("react", lambda: self.resolver.react_effects(context, role, contract))
                    except RuleExecutionError: raise PipelinePaused("rule execution failed") from None
                else:
                    command, rule_context = self._command(state, base, context)
                    effects = self._resolve_with_fallback(state, base, rule_context, role, command); context = rule_context
                actual.append(base); new_commit = self._apply(state, context, role, contract, effects, commits, events)
                commit_index = len(commits) - 1
                pending.extend(PendingEvent(commit_index, ordinal, item.depth + 1)
                               for ordinal, _ in enumerate(new_commit.events))
                subindex += 1
                saved = PointCheckpoint(issued, tuple(actual), tuple(commits), tuple(events), tuple(faults),
                    tuple(pending), WorkCursor("response", event_index, subindex), work_count=work_count)
                journal.put(key, saved)
            event_index += 1; subindex = 0
            saved = PointCheckpoint(issued, tuple(actual), tuple(commits), tuple(events), tuple(faults),
                tuple(pending), WorkCursor("response", event_index, 0), work_count=work_count)
            journal.put(key, saved)
        digest = commits[-1].state_digest if commits else _digest(state.game_id, self._revision(state))
        done = PointCheckpoint(issued, tuple(actual), tuple(commits), tuple(events), tuple(faults), (),
                               WorkCursor("done", 0, 0), work_count=work_count)
        journal.put(key, done); result = self._point_result(done, digest)
        journal.put(key, PointCheckpoint(done.issued, done.actual, done.commits, done.events, done.faults,
                                         (), done.cursor, {"state_digest": digest}, work_count=work_count))
        return result

    @staticmethod
    def _point_result(saved: PointCheckpoint, digest: str | None = None) -> PointResult:
        if digest is None: digest = saved.complete_result["state_digest"]
        return PointResult(saved.actual, saved.commits, saved.events, digest, saved.faults)

    @staticmethod
    def can_advance(*, pending_requests: int, pending_effects: int, queued_events: int) -> bool:
        values = (pending_requests, pending_effects, queued_events)
        if any(type(value) is not int for value in values): raise TypeError("pending counts must be integers")
        if any(value < 0 for value in values): raise ValueError("pending counts must be nonnegative")
        return values == (0, 0, 0)
