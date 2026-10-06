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
from typing import Any, TypeVar

from app.agents.providers.errors import TIMEOUT_ERRORS, TRANSIENT_PROVIDER_ERRORS

T = TypeVar("T")

#: Everything worth another attempt: rate limits, server faults, transport
#: failures and provider-side timeouts. Mirrors the classification the durable
#: action gateway already applied to its own single retry.
RETRYABLE_PROVIDER_ERRORS: tuple[type[Exception], ...] = (
    *TRANSIENT_PROVIDER_ERRORS,
    *TIMEOUT_ERRORS,
)

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
    retry_on: tuple[type[Exception], ...] = RETRYABLE_PROVIDER_ERRORS

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


def _pending_delay(
    policy: RetryPolicy, error: Exception, failed_attempt: int, rng: Any,
) -> float | None:
    """Delay to wait before retrying, or ``None`` when the error is final."""
    if failed_attempt >= policy.attempts:
        return None
    if not isinstance(error, policy.retry_on):
        return None
    return delay_for(policy, error, failed_attempt, rng=rng)


def call_with_retry(
    call: Callable[[], T], policy: RetryPolicy, *,
    sleep: Callable[[float], Any] = time.sleep, rng: Any = None,
) -> T:
    """Run ``call`` with bounded backoff, re-raising the final error.

    Safe to call from a worker thread. Never use this on the event loop: the
    default ``sleep`` blocks. Use :func:`acall_with_retry` there instead.
    """
    for attempt in range(1, policy.attempts + 1):
        try:
            return call()
        except Exception as error:
            delay = _pending_delay(policy, error, attempt, rng)
            if delay is None:
                raise
            sleep(delay)
    raise AssertionError(_UNREACHABLE)  # pragma: no cover - loop always exits


async def acall_with_retry(
    call: Callable[[], Any], policy: RetryPolicy, *,
    sleep: Callable[[float], Any] = asyncio.sleep, rng: Any = None,
) -> Any:
    """Await ``call()`` with bounded backoff, re-raising the final error.

    ``asyncio.CancelledError`` derives from ``BaseException`` and is therefore
    never retried: a cancelled vote or a deadline expiry must propagate.
    """
    for attempt in range(1, policy.attempts + 1):
        try:
            return await call()
        except Exception as error:
            delay = _pending_delay(policy, error, attempt, rng)
            if delay is None:
                raise
            await sleep(delay)
    raise AssertionError(_UNREACHABLE)  # pragma: no cover - loop always exits
