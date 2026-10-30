"""Cross-call provider health: the state behind the game abort policy.

A single failed call is the retry ladder's business. This module only answers
"is this provider (or this model) failing *systematically* right now?" — the
question a per-call fallback cannot answer. It is deliberately pure: no game
state, no I/O, no core imports, and an injectable clock, so the policy can be
tested without a provider or a running game.

The monitor is fed one :class:`CallSample` per **logical** call (not per retry
attempt). The guard in ``app.core`` owns the pause/abort decision; this module
only reports a :class:`HealthEvent` when a configured pattern is reached.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from app.agents.failure_taxonomy import FailureKind

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_WARN = "warn"
MODE_PAUSE = "pause"
MODE_FAIL = "fail"
_MODES = frozenset({MODE_OFF, MODE_WARN, MODE_PAUSE, MODE_FAIL})
_BENCHMARK_MODES = frozenset({MODE_OFF, MODE_FAIL})
_SINGLE_SEAT_MODES = frozenset({MODE_WARN, MODE_PAUSE})


class HealthState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"


@dataclass(frozen=True)
class CallSample:
    """One logical model call's outcome, reduced to what the breaker needs."""

    seat: int
    provider_key: str
    contract_id: str | None
    kind: FailureKind
    failure_code: str | None
    at: float
    ok: bool = False


@dataclass(frozen=True)
class HealthEvent:
    """A pattern that crossed a threshold; ``rule`` names which one."""

    provider_key: str
    rule: str
    failure_code: str | None
    seats: tuple[int, ...]
    contracts: tuple[str, ...]
    sample_count: int
    reopen_count: int

    def detail(self) -> dict[str, object]:
        return {
            "provider_key": self.provider_key,
            "rule": self.rule,
            "failure_code": self.failure_code,
            "seats": list(self.seats),
            "contracts": list(self.contracts),
            "sample_count": self.sample_count,
            "reopen_count": self.reopen_count,
        }


@dataclass(frozen=True)
class AbortPolicy:
    """Operator-facing thresholds for the game-level abort policy.

    Everything is process configuration, not game state: it must never enter
    ``GameConfig`` or a checkpoint (a resumed old game would read a field set
    the codec does not know). The policy that was effective at creation is
    written to ``game.log`` for audit only.
    """

    enabled: bool = True
    mode: str = MODE_PAUSE
    benchmark_mode: str = MODE_FAIL
    consecutive_failures: int = 5
    min_distinct_seats: int = 3
    window_seconds: int = 120
    sample_window: int = 20
    failure_ratio: float = 0.8
    ratio_min_samples: int = 10
    terminal_seats: int = 2
    quality_failures: int = 4
    quality_distinct_seats: int = 3
    single_seat_policy: str = MODE_WARN
    reopen_limit: int = 2
    no_progress_rounds: int = 0

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if self.mode not in _MODES:
            raise ValueError("mode must be off, warn, pause, or fail")
        if self.benchmark_mode not in _BENCHMARK_MODES:
            raise ValueError("benchmark_mode must be off or fail")
        if self.single_seat_policy not in _SINGLE_SEAT_MODES:
            raise ValueError("single_seat_policy must be warn or pause")
        for name in (
            "consecutive_failures", "min_distinct_seats", "window_seconds",
            "sample_window", "ratio_min_samples", "terminal_seats",
            "quality_failures", "quality_distinct_seats", "reopen_limit",
            "no_progress_rounds",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not 0 <= self.failure_ratio <= 1:
            raise ValueError("failure_ratio must be within [0, 1]")

    def for_benchmark(self) -> "AbortPolicy":
        """Benchmark games fail-fast; they are never auto-resumed by a human."""
        return _replace(self, mode=self.benchmark_mode)


def _replace(policy: AbortPolicy, **changes: object) -> AbortPolicy:
    return AbortPolicy(**{**policy.__dict__, **changes})


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    return value if value >= 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    if not math.isfinite(value):
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    return value


def _env_choice(name: str, default: str, allowed: frozenset[str]) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value not in allowed:
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    return value


def policy_from_env() -> AbortPolicy:
    """Build the effective policy from ``MODEL_ABORT_*`` environment values."""
    return AbortPolicy(
        enabled=_env_bool("MODEL_ABORT_ENABLED", True),
        mode=_env_choice("MODEL_ABORT_MODE", MODE_PAUSE, _MODES),
        benchmark_mode=_env_choice(
            "MODEL_ABORT_BENCHMARK_MODE", MODE_FAIL, _BENCHMARK_MODES,
        ),
        consecutive_failures=_env_int("MODEL_ABORT_CONSECUTIVE", 5),
        min_distinct_seats=_env_int("MODEL_ABORT_SEATS", 3),
        window_seconds=_env_int("MODEL_ABORT_WINDOW_SECONDS", 120),
        sample_window=_env_int("MODEL_ABORT_SAMPLE_WINDOW", 20),
        failure_ratio=_env_float("MODEL_ABORT_RATIO", 0.8),
        ratio_min_samples=_env_int("MODEL_ABORT_RATIO_MIN_SAMPLES", 10),
        terminal_seats=_env_int("MODEL_ABORT_TERMINAL_SEATS", 2),
        quality_failures=_env_int("MODEL_ABORT_QUALITY_FAILURES", 4),
        quality_distinct_seats=_env_int("MODEL_ABORT_QUALITY_SEATS", 3),
        single_seat_policy=_env_choice(
            "MODEL_ABORT_SINGLE_SEAT", MODE_WARN, _SINGLE_SEAT_MODES,
        ),
        reopen_limit=_env_int("MODEL_ABORT_REOPEN_LIMIT", 2),
        no_progress_rounds=_env_int("MODEL_ABORT_NO_PROGRESS_ROUNDS", 0),
    )


#: Fields the interface may override in P2. Kept as a closed set so an unknown
#: key in ``settings.json`` is ignored rather than silently applied.
_POLICY_FIELDS = frozenset(AbortPolicy.__dataclass_fields__)


def resolve_abort_policy(stored: Mapping[str, object] | None = None) -> AbortPolicy:
    """Effective policy: interface override > environment > built-in default.

    A malformed stored value is ignored field by field, mirroring the settings
    store's "a settings problem must not take a game down" contract.
    """
    policy = policy_from_env()
    if not isinstance(stored, Mapping):
        return policy
    changes: dict[str, object] = {}
    for name in _POLICY_FIELDS:
        if name not in stored:
            continue
        value = stored[name]
        default = getattr(policy, name)
        if type(default) is bool:
            if type(value) is bool:
                changes[name] = value
        elif type(default) is int:
            if type(value) is int and value >= 0:
                changes[name] = value
        elif type(default) is float:
            if type(value) in (int, float) and 0 <= float(value) <= 1:
                changes[name] = float(value)
        elif type(value) is str and value in _MODES | _BENCHMARK_MODES | _SINGLE_SEAT_MODES:
            changes[name] = value
    return _replace(policy, **changes)


@dataclass
class _KeyHealth:
    consecutive: int = 0
    consecutive_provider: int = 0
    consecutive_quality: int = 0
    consecutive_timeouts: int = 0
    recent: deque[CallSample] = field(default_factory=deque)
    seats: set[int] = field(default_factory=set)
    contracts: set[str] = field(default_factory=set)
    last_failure_at: float | None = None
    last_success_at: float | None = None
    opened: HealthEvent | None = None
    warned_single_seat: bool = False
    reopen_count: int = 0


class ProviderHealthMonitor:
    """Per-provider-key sliding window and circuit state (thread-safe).

    The pipeline provider runs on a worker thread while speech/vote run on the
    event loop, so every mutation happens under one re-entrant lock.
    """

    def __init__(self, policy: AbortPolicy) -> None:
        self._policy = policy
        self._lock = threading.RLock()
        self._keys: dict[str, _KeyHealth] = {}

    @property
    def policy(self) -> AbortPolicy:
        return self._policy

    def _state(self, key: str) -> _KeyHealth:
        state = self._keys.get(key)
        if state is None:
            state = _KeyHealth()
            self._keys[key] = state
        return state

    def record(self, sample: CallSample) -> HealthEvent | None:
        """Record one logical call; return an event only when it trips."""
        if sample.kind is FailureKind.IGNORED:
            return None
        with self._lock:
            state = self._state(sample.provider_key)
            if state.opened is not None:
                return None
            if sample.ok:
                state.consecutive = 0
                state.consecutive_provider = 0
                state.consecutive_quality = 0
                state.consecutive_timeouts = 0
                state.seats.clear()
                state.contracts.clear()
                state.last_failure_at = None
                state.last_success_at = sample.at
                state.warned_single_seat = False
                self._append(state, sample)
                return None
            self._expire_gap(state, sample.at)
            state.consecutive += 1
            state.seats.add(sample.seat)
            if sample.contract_id:
                state.contracts.add(sample.contract_id)
            state.last_failure_at = sample.at
            if sample.kind in (FailureKind.TRANSIENT, FailureKind.TERMINAL):
                state.consecutive_provider += 1
            if sample.kind is FailureKind.QUALITY:
                state.consecutive_quality += 1
            if sample.failure_code == "provider_timeout":
                state.consecutive_timeouts += 1
            else:
                state.consecutive_timeouts = 0
            self._append(state, sample)
            return self._trip(state, sample)

    def _append(self, state: _KeyHealth, sample: CallSample) -> None:
        state.recent.append(sample)
        limit = max(self._policy.sample_window, 1)
        while len(state.recent) > limit:
            state.recent.popleft()

    def _expire_gap(self, state: _KeyHealth, at: float) -> None:
        if state.last_failure_at is None:
            return
        if at - state.last_failure_at > self._policy.window_seconds:
            state.consecutive = 0
            state.consecutive_provider = 0
            state.consecutive_quality = 0
            state.consecutive_timeouts = 0
            state.seats.clear()
            state.contracts.clear()
            state.recent.clear()
            state.last_success_at = None
            state.warned_single_seat = False

    def _recent_of(
        self, state: _KeyHealth, sample: CallSample, kind: FailureKind,
    ) -> list[CallSample]:
        cutoff = sample.at - self._policy.window_seconds
        floor = state.last_success_at
        return [
            item for item in state.recent
            if item.at >= cutoff and not item.ok and item.kind is kind
            and (floor is None or item.at > floor)
        ]

    def _trip(self, state: _KeyHealth, sample: CallSample) -> HealthEvent | None:
        policy = self._policy
        kind = sample.kind
        terminal = self._recent_of(state, sample, FailureKind.TERMINAL)
        if len({item.seat for item in terminal}) >= policy.terminal_seats:
            return self._open(state, sample, "provider_terminal")
        if kind is FailureKind.REQUEST_SHAPE:
            shapes = self._recent_of(state, sample, FailureKind.REQUEST_SHAPE)
            if (
                len({item.seat for item in shapes}) >= policy.min_distinct_seats
                and len({item.contract_id for item in shapes if item.contract_id}) >= 3
            ):
                return self._open(state, sample, "request_shape")
        quality = self._recent_of(state, sample, FailureKind.QUALITY)
        if (
            state.consecutive_quality >= policy.quality_failures
            and len({item.seat for item in quality}) >= policy.quality_distinct_seats
        ):
            return self._open(state, sample, "quality_breakdown")
        timeouts = [
            item for item in self._recent_of(state, sample, FailureKind.TRANSIENT)
            if item.failure_code == "provider_timeout"
        ]
        if (
            state.consecutive_timeouts >= 3
            and len({item.seat for item in timeouts}) >= policy.min_distinct_seats
        ):
            return self._open(state, sample, "provider_timeout")
        provider = self._recent_of(state, sample, FailureKind.TRANSIENT) + terminal
        if (
            state.consecutive_provider >= policy.consecutive_failures
            and len({item.seat for item in provider}) >= policy.min_distinct_seats
        ):
            return self._open(state, sample, "provider_sustained")
        recent = [
            item for item in state.recent
            if item.at >= sample.at - policy.window_seconds
        ]
        provider_failures = [item for item in recent if not item.ok and item.kind in (
            FailureKind.TRANSIENT, FailureKind.TERMINAL,
        )]
        if (
            len(recent) >= policy.ratio_min_samples
            and len(provider_failures) / len(recent) >= policy.failure_ratio
            and len({item.seat for item in provider_failures}) >= policy.min_distinct_seats
        ):
            return self._open(state, sample, "failure_ratio")
        if (
            len({item.seat for item in recent if not item.ok}) == 1
            and state.consecutive >= 3
            and not state.warned_single_seat
        ):
            state.warned_single_seat = True
            if policy.single_seat_policy == MODE_PAUSE:
                return self._open(state, sample, "single_seat")
            logger.warning(
                "Provider %s keeps failing for one seat (%s); not pausing",
                sample.provider_key, sample.seat,
            )
        return None

    def _open(self, state: _KeyHealth, sample: CallSample, rule: str) -> HealthEvent:
        event = HealthEvent(
            provider_key=sample.provider_key,
            rule=rule,
            failure_code=sample.failure_code,
            seats=tuple(sorted(state.seats)),
            contracts=tuple(sorted(state.contracts)),
            sample_count=state.consecutive,
            reopen_count=state.reopen_count,
        )
        state.opened = event
        return event

    def open_event(self, key: str) -> HealthEvent | None:
        with self._lock:
            state = self._keys.get(key)
            return None if state is None else state.opened

    def reset(self, key: str) -> None:
        """Clear one provider after a manual resume/recover.

        The reopen counter is preserved: an operator fixing the provider and
        finding it still broken must not be able to pause forever without a
        deliberate ``force`` on a later resume.
        """
        with self._lock:
            state = self._state(key)
            was_open = state.opened is not None
            state.consecutive = 0
            state.consecutive_provider = 0
            state.consecutive_quality = 0
            state.consecutive_timeouts = 0
            state.seats.clear()
            state.contracts.clear()
            state.recent.clear()
            state.last_failure_at = None
            state.last_success_at = None
            state.warned_single_seat = False
            state.opened = None
            if was_open:
                state.reopen_count += 1

    def reset_all(self) -> None:
        with self._lock:
            for key in list(self._keys):
                self.reset(key)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Diagnostic view for the game log / model-health endpoint."""
        with self._lock:
            return {
                key: {
                    "state": HealthState.OPEN if state.opened else HealthState.CLOSED,
                    "consecutive": state.consecutive,
                    "seats": sorted(state.seats),
                    "reopen_count": state.reopen_count,
                    "rule": None if state.opened is None else state.opened.rule,
                }
                for key, state in self._keys.items()
            }
