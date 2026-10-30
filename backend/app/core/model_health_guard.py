"""Game-level model-provider guard: pause or fail when an outage is systemic.

The retry ladder and the per-contract fallback bucket already keep one failed
call from stopping a game. This module adds the missing cross-call view: a
provider (or a model endpoint) that fails across seats for a sustained window,
or with a deterministic configuration fault, is not a "the model chose pass"
situation — it is an outage, and the game should stop in a recoverable state
(live) or fail the item (benchmark) instead of playing an empty game to its
round cap.

Placement is deliberate: ``check()`` is called at the engine's turn boundaries,
*above* the broad ``except Exception`` handlers in the night/speech/vote
directors. Raising from inside those handlers would be swallowed and turned
into a skip/template/abstention, which is exactly how the 2026-10-28 game ran
nine days on a dead provider.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from app.agents.failure_taxonomy import (
    FailureKind,
    failure_kind,
    normalize_failure_code,
    sample_kind,
)
from app.agents.model_health import (
    AbortPolicy,
    CallSample,
    HealthEvent,
    MODE_FAIL,
    MODE_OFF,
    MODE_WARN,
    ProviderHealthMonitor,
)
from app.core.scheduler import DecisionPaused

logger = logging.getLogger(__name__)

#: Pause/abort code persisted on the game row and exposed by the API.
CIRCUIT_OPEN_CODE = "provider_circuit_open"

#: Telemetry ``parse_result`` values for a usable answer.
_OK_PARSE_RESULTS = frozenset({"ok", "accepted", "strict_content_fallback"})


class ProviderOutagePaused(DecisionPaused):
    """A recoverable pause caused by a systemic provider outage."""

    def __init__(self, event: HealthEvent) -> None:
        super().__init__(
            f"provider circuit open: {event.provider_key} ({event.rule})",
            code=CIRCUIT_OPEN_CODE,
            detail=event.detail(),
        )
        self.event = event


class ProviderOutageFailed(DecisionPaused):
    """A benchmark/live game that must stop instead of being resumed.

    Subclasses ``DecisionPaused`` so the scheduler's pass-through and the
    ledger's ``unknown`` treatment still apply; the watcher checks this exact
    type *before* the generic pause branch and writes ``failed`` instead.
    """

    def __init__(self, event: HealthEvent) -> None:
        super().__init__(
            f"provider outage: {event.provider_key} ({event.rule})",
            code=CIRCUIT_OPEN_CODE,
            detail=event.detail(),
        )
        self.event = event


class ModelHealthGuard:
    """Per-game facade over the pure :class:`ProviderHealthMonitor`.

    ``seat_keys`` maps seat -> provider key; the game service builds it from the
    frozen per-seat clients. ``mode`` is ``pause`` or ``fail``: live games pause,
    benchmark games fail the item because the executor would auto-resume a pause
    and loop on the same outage.
    """

    def __init__(
        self, *, policy: AbortPolicy, seat_keys: Mapping[int, str],
        mode: str | None = None, clock: Callable[[], float] = time.monotonic,
        on_event: Callable[[HealthEvent], None] | None = None,
    ) -> None:
        self._policy = policy
        self._mode = mode or policy.mode
        self._seat_keys = {seat: key for seat, key in seat_keys.items()}
        self._clock = clock
        self._on_event = on_event
        self._monitor = ProviderHealthMonitor(policy)

    @property
    def policy(self) -> AbortPolicy:
        return self._policy

    @property
    def mode(self) -> str:
        return self._mode

    def set_event_sink(self, sink: Callable[[HealthEvent], None] | None) -> None:
        """Attach the game-log sink after the engine exists."""
        self._on_event = sink

    def key_for(self, seat: int) -> str:
        key = self._seat_keys.get(seat)
        if isinstance(key, str) and key:
            return key
        return f"unknown|seat:{seat}"

    def check(self, seat: int) -> None:
        """Raise when this seat's provider is open.

        Called before a model call, never inside one. ``warn``/``off`` modes
        record the pattern but never stop the game.
        """
        if not self._policy.enabled or self._mode in (MODE_OFF, MODE_WARN):
            return
        event = self._monitor.open_event(self.key_for(seat))
        if event is None:
            return
        raise self._pause_or_fail(event)

    def record_telemetry(self, record: Mapping[str, object]) -> None:
        """Feed one ``llm_calls.log`` row into the monitor.

        Never raises: this runs from ``GameLogger.log_llm_call`` on both the
        event loop and the pipeline worker thread, and an observability fault
        must not take a game down.
        """
        try:
            sample = self._sample_from_record(record)
            if sample is None:
                return
            event = self._monitor.record(sample)
            if event is not None:
                self._emit(event)
        except Exception:  # pragma: no cover - defensive boundary
            logger.exception("Provider health observation failed")

    def record_client_failure(self, seat: int, error: BaseException) -> None:
        """Count a failure that never produced an ``llm_calls`` row."""
        try:
            raw = getattr(error, "code", None)
            code = raw if isinstance(raw, str) and raw else normalize_failure_code(error)
            kind = failure_kind(code)
            if kind is FailureKind.IGNORED:
                return
            event = self._monitor.record(CallSample(
                seat=seat, provider_key=self.key_for(seat),
                contract_id=None, kind=kind, failure_code=code,
                at=self._clock(), ok=False,
            ))
            if event is not None:
                self._emit(event)
        except Exception:  # pragma: no cover - defensive boundary
            logger.exception("Provider health client-failure observation failed")

    def reset(self) -> None:
        self._monitor.reset_all()

    def snapshot(self) -> dict[str, dict[str, object]]:
        return self._monitor.snapshot()

    def _sample_from_record(self, record: Mapping[str, object]) -> CallSample | None:
        if not self._policy.enabled:
            return None
        data = record.get("data")
        if not isinstance(data, Mapping):
            return None
        seat = record.get("seat")
        if type(seat) is not int:
            return None
        parse_result = data.get("parse_result")
        failure_code = data.get("failure_code")
        timeout_type = data.get("timeout_type")
        at = self._clock()
        if failure_code is None and parse_result in _OK_PARSE_RESULTS:
            return CallSample(
                seat=seat, provider_key=self.key_for(seat),
                contract_id=_optional_str(data.get("contract_id")),
                kind=FailureKind.QUALITY, failure_code=None, at=at, ok=True,
            )
        kind = sample_kind(
            failure_code=_optional_str(failure_code),
            parse_result=_optional_str(parse_result),
            timeout_type=_optional_str(timeout_type),
        )
        if kind is None:
            return None
        return CallSample(
            seat=seat, provider_key=self.key_for(seat),
            contract_id=_optional_str(data.get("contract_id")),
            kind=kind, failure_code=_optional_str(failure_code), at=at,
            ok=False,
        )

    def _pause_or_fail(self, event: HealthEvent) -> Exception:
        if self._mode == MODE_FAIL or self._policy.mode == MODE_FAIL:
            return ProviderOutageFailed(event)
        return ProviderOutagePaused(event)

    def _emit(self, event: HealthEvent) -> None:
        if self._policy.mode == MODE_WARN or self._mode == MODE_WARN:
            logger.warning(
                "Provider health warning: %s rule=%s seats=%s",
                event.provider_key, event.rule, list(event.seats),
            )
        else:
            logger.error(
                "Provider circuit open: %s rule=%s failure=%s seats=%s",
                event.provider_key, event.rule, event.failure_code,
                list(event.seats),
            )
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # pragma: no cover - audit sink must not break play
                logger.exception("Provider health event sink failed")


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
