"""Bounded retry with exponential backoff for transient provider failures.

Every model call site shares this policy so a flaky endpoint degrades the same
way everywhere. Provider SDKs already retry a few times internally, but their
backoff is short and entirely consumed inside a single call, so an application
level retry that re-fires immediately lands in the very same outage window.
Waiting instead is what turns a burst of connection resets or 429s into a
recovered call.

Capability rejections (HTTP 400/422) are deliberately *not* retryable: they say
the request shape is wrong, which another attempt cannot fix. Those want a
different transport, which is a caller-level decision, not a retry.

The helpers take an injectable ``sleep`` and ``rng`` so tests stay deterministic
and never actually wait.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import ValidationError

from app.agents.providers.errors import (
    is_refused_error,
    timeout_errors,
    transient_provider_errors,
)

T = TypeVar("T")


def retryable_provider_errors() -> tuple[type[Exception], ...]:
    """Everything worth another attempt: rate limits, server faults, transport
    failures and provider-side timeouts. Mirrors the classification the durable
    action gateway already applied to its own single retry.

    Built on demand: a ``RetryPolicy`` is created at module import time (for
    example the speech ladder in ``roles/base.py``), while the SDK exception
    groups are lazy so importing the module never loads either provider SDK.
    """
    return (*transient_provider_errors(), *timeout_errors())


class UnusableModelOutput(ValueError):
    """The provider answered, but the payload could not be used.

    A transport fault and an unusable answer both deserve another attempt, but
    they need *different* follow-ups: a resend for the first, a sharper request
    for the second. Raising this instead of a bare ``ValueError`` is what lets
    the driver tell them apart. It subclasses ``ValueError`` so call sites that
    already treat a bad payload as a value error keep working unchanged.

    ``code`` is the stable token recorded as the telemetry ``failure_code``, so
    a game log can answer "why was this turn skipped" without decoding a
    sentence. It defaults to the generic token; call sites with something more
    specific to say pass their own.
    """

    def __init__(self, message: str, *, code: str = "unusable_model_output") -> None:
        super().__init__(message)
        self.code = code


#: "The provider answered, but we cannot use it." Pydantic surfaces a bad
#: structured payload as ``ValidationError`` (a ``ValueError`` subclass), so the
#: canonical repair set has to list it explicitly: matching a bare ``ValueError``
#: would also swallow unrelated bugs.
RETRYABLE_OUTPUT_ERRORS: tuple[type[Exception], ...] = (
    UnusableModelOutput,
    ValidationError,
)

#: Retry kinds handed to the callable by :func:`call_with_retry`.
KIND_TRANSPORT = "transport"
KIND_REPAIR = "repair"

_UNREACHABLE = "retry loop always returns or re-raises"


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to re-ask a provider, and how long to wait in between."""

    attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter_ratio: float = 0.25
    #: A provider answering ``429 Retry-After: 60`` is asking for a minute. Honour
    #: it, but cap it so one slow ballot cannot hold the vote window hostage.
    max_retry_after_seconds: float = 30.0
    #: ``None`` means "use :func:`retryable_provider_errors`", resolved when a
    #: decision is actually made so constructing a policy never imports the SDKs.
    retry_on: tuple[type[Exception], ...] | None = None
    #: Errors meaning "the answer was unusable". Worth another attempt, but the
    #: request is re-asked with a repair instruction rather than resent verbatim.
    #: Empty for callers that only ever retry transport faults.
    repair_on: tuple[type[Exception], ...] = RETRYABLE_OUTPUT_ERRORS
    #: Whether an error outside both sets is resent anyway. Off by default, so a
    #: deterministic bug is reported the first time instead of four times. The
    #: call sites that would otherwise forfeit a player's turn (speech, night
    #: turns, night actions) turn it on: an exception we merely failed to
    #: classify - a gateway-specific wrapper, say - should cost a resend, not the
    #: seat's whole action.
    retry_unknown_errors: bool = False
    #: How long to wait before re-asking after a *refusal* (HTTP 503: capacity,
    #: quota, or an emptied upstream account pool). Such a refusal is a
    #: minutes-scale capacity decision, so the half-second-to-two-second
    #: transport backoff is noise: it neither gives the pool time to recover nor
    #: leaves the turn usable. The refusal gets one wait of at least this long,
    #: and no second retry at all -- see :func:`retry_plan`.
    refused_delay_seconds: float = 20.0

    def __post_init__(self) -> None:
        if type(self.attempts) is not int or self.attempts < 1:
            raise ValueError("attempts must be a positive integer")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must not be below base_delay_seconds")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be within [0, 1]")
        if self.max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds must not be negative")


def retry_delay(
    policy: RetryPolicy, failed_attempt: int, *, rng: Any = None,
) -> float:
    """Seconds to wait before the attempt following ``failed_attempt``.

    ``failed_attempt`` is 1-based and counts the attempt that just failed, so
    ``retry_delay(policy, 1)`` is the wait before the second attempt.
    """
    if type(failed_attempt) is not int or failed_attempt < 1:
        raise ValueError("failed_attempt must be a positive integer")
    source = random if rng is None else rng
    delay = min(
        policy.max_delay_seconds,
        policy.base_delay_seconds * (2 ** (failed_attempt - 1)),
    )
    if policy.jitter_ratio > 0:
        spread = delay * policy.jitter_ratio
        delay += source.uniform(-spread, spread)
    return max(0.0, delay)


def _header_value(error: BaseException) -> Any:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(error, "headers", None)
    if headers is None:
        return None
    try:
        return headers.get("retry-after")
    except Exception:  # pragma: no cover - exotic mapping without .get
        return None


def _body_value(error: BaseException) -> Any:
    body = getattr(error, "body", None)
    if not isinstance(body, Mapping):
        return None
    nested = body.get("error")
    if isinstance(nested, Mapping):
        return nested.get("retry_after", nested.get("retryAfter"))
    return body.get("retry_after", body.get("retryAfter"))


def retry_after_seconds(error: BaseException) -> float | None:
    """Read a provider ``Retry-After`` hint from a wrapped SDK error.

    Only the numeric-seconds form is understood; an HTTP-date is ignored, which
    just means the caller falls back to the computed exponential delay. The
    chain is walked because LangChain and the SDKs both re-wrap the original
    error, and the hint lives on whichever link kept the response object.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        raw = _header_value(current)
        if raw is None:
            raw = _body_value(current)
        if raw is not None:
            try:
                seconds = float(raw)
            except (TypeError, ValueError):
                return None
            # ``nan`` and negative hints are meaningless; ignore them.
            if seconds != seconds or seconds < 0:
                return None
            return seconds
        current = current.__cause__ or current.__context__
    return None


def delay_for(
    policy: RetryPolicy, error: BaseException, failed_attempt: int, *,
    rng: Any = None,
) -> float:
    """Backoff for a failed attempt, honouring a provider ``Retry-After`` hint."""
    delay = retry_delay(policy, failed_attempt, rng=rng)
    hint = retry_after_seconds(error)
    if hint is None:
        return delay
    return max(delay, min(hint, policy.max_retry_after_seconds))


class FailureClass(StrEnum):
    """What to do about a failed attempt.

    Four decisions, and that is deliberate: naming the *cause* is
    ``classify_provider_error``'s job (provider faults) or
    ``UnusableModelOutput.code``'s (unusable payloads). A second cause
    vocabulary here would recreate the two-names-for-one-fault problem this
    enum exists to remove.
    """

    #: The gateway refused for capacity reasons (HTTP 503). Waiting is the only
    #: useful follow-up, and only once -- it is a minutes-scale decision.
    REFUSED = "refused"
    #: No usable answer arrived: resend the very same request.
    TRANSPORT = "transport"
    #: The provider answered, but the payload could not be used: re-ask with a
    #: sharpened request.
    REPAIR = "repair"
    #: Another attempt cannot help, or the policy forbids one.
    STOP = "stop"


def classify_failure(policy: RetryPolicy, error: BaseException) -> FailureClass:
    """The single retry decision for ``error`` under ``policy``.

    The order is the contract, and callers depend on it:

    1. a refusal outranks everything -- it is a capacity decision, not a request
       shape, so no amount of rephrasing helps;
    2. a transport fault beats a repair one: when an error belongs to both sets
       the safe reading is "we never got an answer", because resending cannot
       make a rejected payload worse, whereas repairing a dropped connection is
       pointless;
    3. an unclassified error is resent only when the policy opts in -- the call
       sites that would otherwise forfeit a player's turn do, because an
       exception we merely failed to classify should cost a resend rather than
       the seat's whole action.
    """
    if is_refused_error(error):
        return FailureClass.REFUSED
    retry_on = policy.retry_on
    if retry_on is None:
        retry_on = retryable_provider_errors()
    if isinstance(error, retry_on):
        return FailureClass.TRANSPORT
    if policy.repair_on and isinstance(error, policy.repair_on):
        return FailureClass.REPAIR
    if policy.retry_unknown_errors:
        return FailureClass.TRANSPORT
    return FailureClass.STOP


def retry_plan(
    policy: RetryPolicy, error: BaseException, failed_attempt: int, *,
    rng: Any = None,
) -> tuple[str, float] | None:
    """How to follow up a failed attempt: ``(kind, delay)``, or ``None`` if final.

    ``kind`` is :data:`KIND_TRANSPORT` when the request never got a usable
    answer and should be resent, or :data:`KIND_REPAIR` when the provider did
    answer but the payload was unusable, which wants a stricter request.

    The decision itself lives in :func:`classify_failure`; this function only
    turns it into a kind plus a delay, so a caller that needs the decision
    without the arithmetic (a driver deciding whether to sharpen the request)
    has nowhere else to look.
    """
    if type(failed_attempt) is not int or failed_attempt < 1:
        raise ValueError("failed_attempt must be a positive integer")
    if failed_attempt >= policy.attempts:
        return None
    failure = classify_failure(policy, error)
    if failure is FailureClass.REFUSED:
        # One long wait, then stop. A gateway that answers "no capacity" is
        # telling us its cooldown, not our request shape, so a short second
        # attempt would only spend the turn re-learning the same thing.
        if failed_attempt > 1:
            return None
        hint = retry_after_seconds(error)
        if hint is not None:
            hint = min(hint, policy.max_retry_after_seconds)
        return KIND_TRANSPORT, max(policy.refused_delay_seconds, hint or 0.0)
    if failure is FailureClass.STOP:
        return None
    kind = KIND_REPAIR if failure is FailureClass.REPAIR else KIND_TRANSPORT
    return kind, delay_for(policy, error, failed_attempt, rng=rng)


def call_with_retry(
    call: Callable[[str], T], policy: RetryPolicy, *,
    sleep: Callable[[float], Any] = time.sleep, rng: Any = None,
) -> T:
    """Run ``call`` with bounded backoff, re-raising the final error.

    ``call`` receives the retry kind for the attempt it is about to make, so a
    caller can tighten the request on ``"repair"`` instead of repeating it
    verbatim. The first attempt is always ``"transport"``: nothing has failed yet.

    Safe to call from a worker thread. Never use this on the event loop: the
    default ``sleep`` blocks. Use :func:`acall_with_retry` there instead.
    """
    kind = KIND_TRANSPORT
    for attempt in range(1, policy.attempts + 1):
        try:
            return call(kind)
        except Exception as error:
            plan = retry_plan(policy, error, attempt, rng=rng)
            if plan is None:
                raise
            kind, delay = plan
            sleep(delay)
    raise AssertionError(_UNREACHABLE)  # pragma: no cover - loop always exits


async def acall_with_retry(
    call: Callable[[str], Any], policy: RetryPolicy, *,
    sleep: Callable[[float], Any] = asyncio.sleep, rng: Any = None,
) -> Any:
    """Await ``call()`` with bounded backoff, re-raising the final error.

    ``asyncio.CancelledError`` derives from ``BaseException`` and is therefore
    never retried: a cancelled vote or a deadline expiry must propagate.
    """
    kind = KIND_TRANSPORT
    for attempt in range(1, policy.attempts + 1):
        try:
            return await call(kind)
        except Exception as error:
            plan = retry_plan(policy, error, attempt, rng=rng)
            if plan is None:
                raise
            kind, delay = plan
            await sleep(delay)
    raise AssertionError(_UNREACHABLE)  # pragma: no cover - loop always exits
