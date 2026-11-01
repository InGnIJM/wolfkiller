"""One driver for every logical model call.

Before this module each call site owned its own copy of the same lifecycle:
issue a request, parse it, decide whether to retry verbatim or with a repair
reminder, merge token usage, write the durable ledger, emit telemetry, and pick
a value to use when the budget runs out. Five sites grew five answers -- the
same upstream fault ended up as a technical abstention, a template speech, a
``pass`` carrying an engine message, or an escaped exception -- and the
benchmark could not read one number back out of them.

The pieces were already correct individually (``retry_plan`` knows transport
from repair, ``classify_provider_error`` is the only failure vocabulary,
``ModelInvocationService`` is the durable ledger). What was missing was the
assembly, so this module owns exactly the shared part:

* what happens once the retry budget is spent
  (:func:`exhausted_bucket`, :class:`Exhausted`);
* one result shape (:class:`CallOutcome`) carrying attempts/transports/usage so
  a caller never reconstructs them from side channels;
* the ledger write, the telemetry records and the prompt-capture sink, so a
  call site only supplies *what to send* and *how to parse it*.

Deliberately out of scope: prompt rendering, transport selection (that stays in
:class:`~app.agents.llm_client.LLMClient`, where the provider capabilities
live), retry classification (it belongs next to ``RetryPolicy`` in
:mod:`app.agents.retry`) and audience projection.

Layering: the module imports only ``agents`` siblings and standard library, never
``core`` or ``services``. Telemetry, capture and the pause exception arrive as
callables or factories, so the component never learns about ``GameLogger``, disk,
or the scheduler.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Generic, Mapping, TypeVar

from app.agents.llm_client import LLMUsage, merge_usage
from app.agents.providers.errors import classify_provider_error
from app.agents.retry import KIND_TRANSPORT, RetryPolicy, retry_plan

T = TypeVar("T")

#: Call kinds, mirroring the ``call_kind`` values already written to
#: ``llm_calls.log`` and to the prompt-capture records.
CALL_KIND_NIGHT = "night"
CALL_KIND_ACTION = "action"
CALL_KIND_SPEECH = "speech"
CALL_KIND_PIPELINE = "pipeline"

CALL_KINDS = frozenset({
    CALL_KIND_NIGHT, CALL_KIND_ACTION, CALL_KIND_SPEECH, CALL_KIND_PIPELINE,
})

#: Outcomes for one logical call. ``degraded`` is the state the old telemetry
#: could not express: the seat still acted, but on an engine-chosen value,
#: which is why a forfeited action read as a successful one.
PARSE_OK = "ok"
PARSE_DEGRADED = "degraded"
PARSE_ERROR = "error"

#: Ledger envelope keys. ``payload`` holds whatever the call site encoded (its
#: shape is the site's business); ``call`` holds the driver's own metadata, so
#: the two can never collide over a key name.
PAYLOAD_KEY = "payload"
CALL_KEY = "call"


class _SyncSendViolation(TypeError):
    """An async ``send`` handed to :meth:`InvocationRunner.run_sync`.

    Deliberately not a provider failure: it is a wiring bug, so the ladder
    re-raises it instead of retrying it as an unclassified fault.
    """


class FailureBucket(StrEnum):
    """Where a call lands once its retry budget is spent.

    ``FailureBucket`` answers "what do we do instead", which is a different
    question from :class:`app.agents.retry.FailureClass`'s "do we try again".
    """

    #: Produce a fallback value, tagged with the failure code.
    DEGRADE = "degrade"
    #: Stop the game in a recoverable state instead of forfeiting the action.
    PAUSE = "pause"
    #: Re-raise and let the caller decide (night directors, vote ladders).
    RAISE = "raise"


def exhausted_bucket(contract: object | None, *, call_kind: str) -> FailureBucket:
    """What a spent budget means for this contract.

    Derived from the contract's own declarations rather than a new field:
    adding one to ``ActionContract`` would change ``stable_digest()`` and push
    every in-flight game across the recovery-compatibility boundary for a rule
    only the call site can act on.

    Two rules, both deliberately narrow:

    * a speech degrades to the template -- the seat still takes its turn, and
      the template no longer carries any identity;
    * a ``per_game_limit == 1`` contract pauses instead of degrading, because a
      degraded one-shot is not a lost turn but a lost ability: the ledger marks
      the contract consumed while the resource stays in the seat's hands.

    Everything else degrades. The obvious third rule -- "an action that requires
    a target must not silently become ``pass``" -- is *not* applied on purpose:
    on the audited batch it would have paused 12+ times per 30 games, and the
    cost of that pause has not been measured. Those losses are made visible
    first (one failure code in four places); the rule can follow the data.
    """
    if contract is None:
        return FailureBucket.RAISE
    if call_kind == CALL_KIND_SPEECH:
        return FailureBucket.DEGRADE
    limit = getattr(contract, "per_game_limit", None)
    if type(limit) is int and limit == 1:
        return FailureBucket.PAUSE
    return FailureBucket.DEGRADE


@dataclass(frozen=True)
class CallSpec:
    """Everything the driver needs to describe one logical call.

    ``request_id`` / ``action_position`` / ``frozen_request`` are the durable
    ledger's identity for the request. They are passed through byte for byte --
    the ledger hashes them into ``request_digest`` and compares that on resume,
    so a driver that reformatted them would invalidate in-flight games.
    """

    game_id: str
    request_id: str
    action_position: str
    frozen_request: Mapping[str, object]
    call_kind: str
    actor_seat: int
    round_number: int
    phase: str
    contract_id: str | None = None


@dataclass(frozen=True)
class RawAnswer:
    """One provider answer, before the caller's domain parse."""

    value: object
    transport: str
    usage: LLMUsage | None = None
    elapsed_ms: int = 0


@dataclass(frozen=True)
class CallOutcome(Generic[T]):
    """The result of one logical call, whatever the call site does with it."""

    value: T
    degraded: bool
    failure_code: str | None
    timeout_type: str | None
    attempts: int
    transports: tuple[str, ...]
    usage: LLMUsage | None
    elapsed_ms: int


@dataclass(frozen=True)
class CallRecord:
    """Telemetry for one logical call: successes, degradations and failures."""

    spec: CallSpec
    attempts: int
    transport: str | None
    prompt_chars: int
    elapsed_ms: int
    usage: LLMUsage | None
    parse_result: str
    failure_code: str | None
    timeout_type: str | None


@dataclass(frozen=True)
class FailureRecord:
    """Telemetry for an exhausted budget, kept apart from the call record.

    ``model_errors.log`` answers "why was this turn skipped"; ``llm_calls.log``
    answers "what did the call cost". One record serving both is part of how the
    two drifted apart.
    """

    spec: CallSpec
    failure_code: str
    attempts: int
    error: BaseException


@dataclass(frozen=True)
class Exhausted(Generic[T]):
    """The single decision point once a budget is spent.

    Built at the call site -- usually from :func:`exhausted_bucket` -- because
    only the site knows the concrete fallback value and the exception type that
    means "pause" in its layer.
    """

    bucket: FailureBucket
    make: Callable[[str], Any] | None = None

    def __post_init__(self) -> None:
        if self.bucket is FailureBucket.RAISE and self.make is not None:
            raise ValueError("a raising sink carries no factory")
        if self.bucket is not FailureBucket.RAISE and self.make is None:
            raise ValueError("a degrading or pausing sink needs a factory")

    @staticmethod
    def raise_error() -> "Exhausted[T]":
        """Re-raise the last failure; the caller decides what it means."""
        return Exhausted(FailureBucket.RAISE)

    @staticmethod
    def degrade(make: Callable[[str], T]) -> "Exhausted[T]":
        """Build the fallback value from the failure code."""
        return Exhausted(FailureBucket.DEGRADE, make=make)

    @staticmethod
    def pause(make_error: Callable[[str], BaseException]) -> "Exhausted[T]":
        """Raise a recoverable pause carrying the failure code."""
        return Exhausted(FailureBucket.PAUSE, make=make_error)


@dataclass(frozen=True)
class CallAdapter(Generic[T]):
    """How one call site issues and parses its own call.

    ``send`` receives the retry kind (``"transport"`` or ``"repair"``) and the
    capture sink to record outgoing messages into. The kind is what makes a
    repair attempt different from a resend: sharpening the request on
    ``"repair"`` is the caller's job.

    ``encode``/``decode`` translate between the domain value and the ledger
    payload. They are required whenever a ledger is wired, because a cache hit
    has to be reconstructed without another provider call. ``decode`` must also
    accept a legacy row written before the envelope existed.
    """

    send: Callable[..., RawAnswer]
    parse: Callable[[RawAnswer], T]
    encode: Callable[[T, "CallOutcome[T]"], Mapping[str, object]] | None = None
    decode: Callable[[Mapping[str, object]], T] | None = None


class InvocationRunner:
    """Run one logical call: retry ladder, ledger, telemetry, capture.

    ``sleeper`` / ``async_sleeper`` are injected because the night path runs on
    a worker thread (a blocking sleep is correct there) while the day paths run
    on the event loop (blocking would stall the game). Tests zero them out.
    """

    def __init__(
        self, *,
        ledger: Any = None,
        record_call: Callable[[CallRecord], None] | None = None,
        record_failure: Callable[[FailureRecord], None] | None = None,
        capture: Callable[[Any, CallSpec, int, str | None], None] | None = None,
        sink_factory: Callable[[], Any] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        async_sleeper: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if (capture is None) != (sink_factory is None):
            raise ValueError("capture and sink_factory must be provided together")
        self.ledger = ledger
        self._record_call = record_call
        self._record_failure = record_failure
        self._capture = capture
        self._sink_factory = sink_factory
        self._sleeper = sleeper
        self._async_sleeper = async_sleeper

    # ── public entry points ──────────────────────────────────────────────

    def run_sync(
        self, spec: CallSpec, adapter: CallAdapter[T], *,
        policy: RetryPolicy, on_exhausted: Exhausted[T],
        prompt_chars: int = 0, model_id: str | None = None,
        provider_profile: str | None = None, execution_generation: int = 1,
        recovery: bool = False,
    ) -> CallOutcome[T]:
        """Run the whole ladder on the calling (worker) thread."""
        _validate(spec, adapter, ledger=self.ledger is not None)
        if self.ledger is None:
            return self._ladder_sync(
                spec, adapter, policy=policy, on_exhausted=on_exhausted,
                prompt_chars=prompt_chars,
            )
        box: dict[str, CallOutcome[T]] = {}
        result = self.ledger.invoke_sync(
            **_ledger_args(
                spec, model_id=model_id, provider_profile=provider_profile,
                execution_generation=execution_generation, recovery=recovery,
            ),
            provider_call=lambda: self._provider_call_sync(
                spec, adapter, policy=policy, on_exhausted=on_exhausted,
                prompt_chars=prompt_chars, box=box,
            ),
        )
        return _from_ledger(adapter, result, box)

    async def run_async(
        self, spec: CallSpec, adapter: CallAdapter[T], *,
        policy: RetryPolicy, on_exhausted: Exhausted[T],
        prompt_chars: int = 0, model_id: str | None = None,
        provider_profile: str | None = None, execution_generation: int = 1,
        recovery: bool = False,
    ) -> CallOutcome[T]:
        """Run the whole ladder on the event loop."""
        _validate(spec, adapter, ledger=self.ledger is not None)
        if self.ledger is None:
            return await self._ladder_async(
                spec, adapter, policy=policy, on_exhausted=on_exhausted,
                prompt_chars=prompt_chars,
            )
        box: dict[str, CallOutcome[T]] = {}
        result = await self.ledger.invoke(
            **_ledger_args(
                spec, model_id=model_id, provider_profile=provider_profile,
                execution_generation=execution_generation, recovery=recovery,
            ),
            provider_call=lambda: self._provider_call_async(
                spec, adapter, policy=policy, on_exhausted=on_exhausted,
                prompt_chars=prompt_chars, box=box,
            ),
        )
        return _from_ledger(adapter, result, box)

    # ── ledger plumbing ──────────────────────────────────────────────────

    def _provider_call_sync(self, spec, adapter, *, policy, on_exhausted,
                            prompt_chars, box):
        outcome, sink = self._ladder_sync(
            spec, adapter, policy=policy, on_exhausted=on_exhausted,
            prompt_chars=prompt_chars, with_sink=True,
        )
        box["outcome"] = outcome
        return _encoded(adapter, outcome), _usage_row(outcome.usage)

    async def _provider_call_async(self, spec, adapter, *, policy, on_exhausted,
                                   prompt_chars, box):
        outcome, sink = await self._ladder_async(
            spec, adapter, policy=policy, on_exhausted=on_exhausted,
            prompt_chars=prompt_chars, with_sink=True,
        )
        box["outcome"] = outcome
        return _encoded(adapter, outcome), _usage_row(outcome.usage)

    # ── the ladder ───────────────────────────────────────────────────────

    def _ladder_sync(self, spec, adapter, *, policy, on_exhausted,
                     prompt_chars, with_sink=False):
        sink = self._new_sink()
        state = _Ladder(spec, prompt_chars)
        kind = KIND_TRANSPORT
        while True:
            state.attempts += 1
            try:
                # ``send`` is inside the try on purpose: the client raises
                # ``UnusableModelOutput`` from here when the provider answered
                # something it could not read, and that is exactly the case the
                # repair reminder exists for. Classifying only ``parse`` would
                # leave the real failure mode on the verbatim-resend path.
                answer = adapter.send(kind, sink)
                if inspect.isawaitable(answer):
                    raise _SyncSendViolation
                state.absorb(answer)
                value = adapter.parse(answer)
            except _SyncSendViolation:
                # A programming error, not a provider failure: never classify it.
                raise TypeError("run_sync requires a synchronous send") from None
            except BaseException as error:  # noqa: BLE001 - classified by retry_plan
                plan = retry_plan(policy, error, state.attempts)
                if plan is None:
                    return self._give_up(
                        on_exhausted, state, error, sink, with_sink,
                    )
                kind, delay = plan
                if delay:
                    self._sleeper(delay)
            else:
                outcome = state.success(value)
                self._finish(state, outcome, sink)
                return (outcome, sink) if with_sink else outcome

    async def _ladder_async(self, spec, adapter, *, policy, on_exhausted,
                            prompt_chars, with_sink=False):
        sink = self._new_sink()
        state = _Ladder(spec, prompt_chars)
        kind = KIND_TRANSPORT
        while True:
            state.attempts += 1
            try:
                answer = adapter.send(kind, sink)
                if inspect.isawaitable(answer):
                    answer = await answer
                state.absorb(answer)
                value = adapter.parse(answer)
            except BaseException as error:  # noqa: BLE001 - classified by retry_plan
                plan = retry_plan(policy, error, state.attempts)
                if plan is None:
                    return self._give_up(
                        on_exhausted, state, error, sink, with_sink,
                    )
                kind, delay = plan
                if delay:
                    await self._async_sleeper(delay)
            else:
                outcome = state.success(value)
                self._finish(state, outcome, sink)
                return (outcome, sink) if with_sink else outcome

    # ── finishing ────────────────────────────────────────────────────────

    def _give_up(self, on_exhausted, state, error, sink, with_sink):
        failure_code = _failure_code(error)
        self._emit_failure(state.spec, failure_code, state.attempts, error)
        if on_exhausted.bucket is not FailureBucket.DEGRADE:
            # A raise or pause still owes the call record: the caller's own
            # handler logs the error, but nothing else records the cost.
            state.mark_failed()
            self._emit_call(state, sink, PARSE_ERROR, failure_code)
            self._flush_capture(state.spec, sink, state.attempts, None)
            if on_exhausted.bucket is FailureBucket.PAUSE:
                raise on_exhausted.make(failure_code)  # type: ignore[misc]
            raise error
        outcome = state.degraded(on_exhausted.make(failure_code), failure_code)
        self._finish(state, outcome, sink)
        return (outcome, sink) if with_sink else outcome

    def _finish(self, state, outcome, sink) -> None:
        state.adopt(outcome)
        self._emit_call(
            state, sink,
            PARSE_DEGRADED if outcome.degraded else PARSE_OK,
            outcome.failure_code,
        )
        self._flush_capture(
            state.spec, sink, outcome.attempts, _joined(outcome.transports),
        )

    # ── sinks ────────────────────────────────────────────────────────────

    def _emit_call(self, state, sink, parse_result, failure_code) -> None:
        if self._record_call is None:
            return
        self._record_call(CallRecord(
            spec=state.spec,
            attempts=state.attempts,
            transport=_joined(state.transports),
            prompt_chars=state.prompt_chars,
            elapsed_ms=state.elapsed_ms,
            usage=state.usage,
            parse_result=parse_result,
            failure_code=failure_code,
            timeout_type=state.timeout_type,
        ))

    def _emit_failure(self, spec, failure_code, attempts, error) -> None:
        if self._record_failure is None:
            return
        self._record_failure(FailureRecord(
            spec=spec, failure_code=failure_code, attempts=attempts, error=error,
        ))

    def _flush_capture(self, spec, sink, attempts: int, transport: str | None) -> None:
        """Hand a recorded prompt to the capture hook.

        ``attempts`` is the ladder's own count, so a failed ladder still says
        how many requests were sent -- the capture record is the only place a
        resumed game can see that.
        """
        if self._capture is None or sink is None or sink.empty:
            return
        self._capture(sink, spec, attempts, transport)

    def _new_sink(self):
        return None if self._sink_factory is None else self._sink_factory()


class _Ladder:
    """Accumulators for one ladder run.

    The success and degradation paths build a :class:`CallOutcome` from these
    fields; the failure paths only have the accumulators. Keeping one mutable
    state type means ``_emit_call`` has a single shape to read.
    """

    __slots__ = ("spec", "prompt_chars", "attempts", "transports", "usage",
                 "elapsed_ms", "timeout_type", "started")

    def __init__(self, spec: CallSpec, prompt_chars: int) -> None:
        self.spec = spec
        self.prompt_chars = prompt_chars
        self.attempts = 0
        self.transports: list[str] = []
        self.usage: LLMUsage | None = None
        self.elapsed_ms = 0
        self.timeout_type: str | None = None
        # Measured from ladder construction, not from the first answer: the
        # caller's cost of interest is the whole ladder, including any send that
        # never came back.
        self.started = time.monotonic()

    def absorb(self, answer: RawAnswer) -> None:
        self.transports.append(answer.transport)
        self.usage = merge_usage(self.usage, answer.usage)

    def success(self, value) -> CallOutcome:
        return self._build(value, degraded=False, failure_code=None)

    def degraded(self, value, failure_code: str) -> CallOutcome:
        return self._build(value, degraded=True, failure_code=failure_code)

    def adopt(self, outcome: CallOutcome) -> None:
        """Fold a finished outcome back in so telemetry reads one state type."""
        self.attempts = outcome.attempts
        self.transports = list(outcome.transports)
        self.usage = outcome.usage
        self.elapsed_ms = outcome.elapsed_ms
        self.timeout_type = outcome.timeout_type

    def mark_failed(self) -> None:
        self.elapsed_ms = self._elapsed()

    def _build(self, value, *, degraded, failure_code) -> CallOutcome:
        self.elapsed_ms = self._elapsed()
        return CallOutcome(
            value=value, degraded=degraded, failure_code=failure_code,
            timeout_type=self.timeout_type, attempts=self.attempts,
            transports=tuple(self.transports), usage=self.usage,
            elapsed_ms=self.elapsed_ms,
        )

    def _elapsed(self) -> int:
        return max(0, round((time.monotonic() - self.started) * 1000))


def _validate(spec: CallSpec, adapter: CallAdapter, *, ledger: bool) -> None:
    if spec.call_kind not in CALL_KINDS:
        raise ValueError(f"unknown call kind: {spec.call_kind!r}")
    if not spec.game_id:
        raise ValueError("call spec must carry a game id")
    if (adapter.encode is None) != (adapter.decode is None):
        raise ValueError("encode and decode must be provided together")
    if ledger and (adapter.encode is None or adapter.decode is None):
        raise ValueError("a wired ledger requires encode and decode")


def _ledger_args(spec: CallSpec, *, model_id, provider_profile,
                 execution_generation, recovery) -> dict[str, object]:
    return {
        "game_id": spec.game_id,
        "request_id": spec.request_id,
        "actor_seat": spec.actor_seat,
        "action_position": spec.action_position,
        "frozen_request": spec.frozen_request,
        "provider_profile": provider_profile or "unknown",
        "model_id": model_id or "unknown",
        "execution_generation": execution_generation,
        "recovery": recovery,
    }


def _encoded(adapter: CallAdapter, outcome: CallOutcome) -> Mapping[str, object]:
    assert adapter.encode is not None  # validated before the ladder starts
    return {
        PAYLOAD_KEY: dict(adapter.encode(outcome.value, outcome)),
        CALL_KEY: {
            "status": "degraded" if outcome.degraded else "succeeded",
            "failure_code": outcome.failure_code,
            "attempts": outcome.attempts,
            "transports": list(outcome.transports),
        },
    }


def _from_ledger(adapter, result, box):
    if not result.reused and "outcome" in box:
        return box["outcome"]
    row = result.normalized_result
    meta = row.get(CALL_KEY)
    meta = meta if isinstance(meta, Mapping) else {}
    return CallOutcome(
        value=_decode(adapter, row),
        degraded=meta.get("status") == "degraded",
        failure_code=_optional_str(meta.get("failure_code")),
        timeout_type=None,
        attempts=_optional_int(meta.get("attempts")) or 1,
        transports=tuple(_transport_list(meta.get("transports"))),
        usage=None,
        elapsed_ms=0,
    )


def _decode(adapter: CallAdapter, row: Mapping[str, object]):
    """Decode a ledger row, tolerating rows written before the envelope."""
    assert adapter.decode is not None  # validated with the ledger
    inner = row.get(PAYLOAD_KEY)
    return adapter.decode(inner if isinstance(inner, Mapping) else row)


def _usage_row(usage: LLMUsage | None) -> Mapping[str, int] | None:
    if usage is None:
        return None
    row = {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }
    if usage.cached_prompt_tokens is not None:
        row["cached_prompt_tokens"] = usage.cached_prompt_tokens
    return row


def _failure_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if type(code) is str and code:
        return code
    return classify_provider_error(error)


def _transport_list(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [item for item in value if type(item) is str]
    return []


def _joined(transports) -> str | None:
    items = _transport_list(transports)
    return None if not items else "+".join(items)


def _optional_str(value) -> str | None:
    return value if type(value) is str else None


def _optional_int(value) -> int | None:
    return value if type(value) is int and value > 0 else None
