import asyncio

import httpx
import pytest
from openai import APIConnectionError, RateLimitError

from app.agents.retry import (
    RETRYABLE_PROVIDER_ERRORS,
    RetryPolicy,
    acall_with_retry,
    call_with_retry,
    delay_for,
    retry_after_seconds,
    retry_delay,
)


class Recorder:
    """Stand-in for ``random`` so delays are exact and tests never wait."""

    def __init__(self, value: float = 0.0):
        self.value = value
        self.calls: list[tuple[float, float]] = []

    def uniform(self, low: float, high: float) -> float:
        self.calls.append((low, high))
        return self.value


def connection_error() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("POST", "https://model.test/v1"))


def rate_limit_error(retry_after: str | None = None) -> RateLimitError:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    return RateLimitError(
        "slow down",
        response=httpx.Response(
            429, request=httpx.Request("POST", "https://model.test/v1"),
            headers=headers,
        ),
        body=None,
    )


# ── RetryPolicy validation ──────────────────────────────────────


@pytest.mark.parametrize(("overrides", "message"), [
    ({"attempts": 0}, "attempts"),
    ({"attempts": 1.5}, "attempts"),
    ({"base_delay_seconds": -1.0}, "base_delay_seconds"),
    ({"base_delay_seconds": 5.0, "max_delay_seconds": 1.0}, "max_delay_seconds"),
    ({"jitter_ratio": 1.5}, "jitter_ratio"),
    ({"jitter_ratio": -0.1}, "jitter_ratio"),
    ({"max_retry_after_seconds": -1.0}, "max_retry_after_seconds"),
])
def test_policy_rejects_impossible_values(overrides, message):
    with pytest.raises(ValueError, match=message):
        RetryPolicy(**overrides)


def test_policy_defaults_are_retryable_provider_failures():
    policy = RetryPolicy()

    assert policy.retry_on == RETRYABLE_PROVIDER_ERRORS
    assert policy.attempts == 3


# ── retry_delay ─────────────────────────────────────────────────


def test_retry_delay_grows_exponentially_and_caps():
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=2.5, jitter_ratio=0.0)

    assert retry_delay(policy, 1) == 1.0
    assert retry_delay(policy, 2) == 2.0
    # 4.0 would be next; the cap holds it.
    assert retry_delay(policy, 3) == 2.5


def test_retry_delay_applies_centred_jitter():
    policy = RetryPolicy(base_delay_seconds=1.0, jitter_ratio=0.5)
    rng = Recorder(value=0.5)

    assert retry_delay(policy, 1, rng=rng) == 1.5
    assert rng.calls == [(-0.5, 0.5)]


def test_retry_delay_without_jitter_never_consults_the_rng():
    rng = Recorder()

    assert retry_delay(RetryPolicy(jitter_ratio=0.0), 1, rng=rng) == 0.5
    assert rng.calls == []


def test_retry_delay_clamps_negative_jitter_to_zero():
    policy = RetryPolicy(base_delay_seconds=1.0, jitter_ratio=1.0)

    assert retry_delay(policy, 1, rng=Recorder(value=-1.0)) == 0.0


@pytest.mark.parametrize("failed_attempt", [0, -1, 1.5])
def test_retry_delay_rejects_invalid_attempts(failed_attempt):
    with pytest.raises(ValueError, match="failed_attempt"):
        retry_delay(RetryPolicy(), failed_attempt)


# ── retry_after_seconds ─────────────────────────────────────────


def test_retry_after_reads_the_response_header():
    assert retry_after_seconds(rate_limit_error("7")) == 7.0


def test_retry_after_reads_a_bare_header_on_the_error():
    class WithHeaders(Exception):
        headers = {"retry-after": "3"}

    assert retry_after_seconds(WithHeaders()) == 3.0


def test_retry_after_reads_a_nested_body_field():
    class WithBody(Exception):
        body = {"error": {"retry_after": 4}}

    assert retry_after_seconds(WithBody()) == 4.0


def test_retry_after_reads_a_top_level_body_field():
    class WithBody(Exception):
        body = {"retryAfter": "5"}

    assert retry_after_seconds(WithBody()) == 5.0


def test_retry_after_walks_the_cause_chain():
    outer = RuntimeError("wrapped")
    outer.__cause__ = rate_limit_error("9")

    assert retry_after_seconds(outer) == 9.0


def test_retry_after_survives_a_cause_cycle():
    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert retry_after_seconds(first) is None


@pytest.mark.parametrize("error", [
    connection_error(),
    RuntimeError("no hint"),
])
def test_retry_after_returns_none_without_a_hint(error):
    assert retry_after_seconds(error) is None


@pytest.mark.parametrize("raw", ["soon", "Wed, 21 Oct 2026 07:28:00 GMT", "nan", "-2", ""])
def test_retry_after_ignores_unusable_hints(raw):
    assert retry_after_seconds(rate_limit_error(raw)) is None


def test_retry_after_ignores_a_non_mapping_body():
    class WithBody(Exception):
        body = ["not", "a", "mapping"]

    assert retry_after_seconds(WithBody()) is None


# ── delay_for ───────────────────────────────────────────────────


def test_delay_for_prefers_a_generous_provider_hint():
    policy = RetryPolicy(base_delay_seconds=0.5, jitter_ratio=0.0)

    assert delay_for(policy, rate_limit_error("6"), 1) == 6.0


def test_delay_for_caps_an_absurd_provider_hint():
    policy = RetryPolicy(
        base_delay_seconds=0.5, jitter_ratio=0.0, max_retry_after_seconds=10.0,
    )

    assert delay_for(policy, rate_limit_error("600"), 1) == 10.0


def test_delay_for_keeps_the_computed_delay_when_the_hint_is_smaller():
    policy = RetryPolicy(base_delay_seconds=5.0, jitter_ratio=0.0)

    assert delay_for(policy, rate_limit_error("1"), 1) == 5.0


# ── call_with_retry ─────────────────────────────────────────────


def test_call_with_retry_returns_the_first_success_without_sleeping():
    waits: list[float] = []

    result = call_with_retry(lambda: "ok", RetryPolicy(), sleep=waits.append)

    assert result == "ok"
    assert waits == []


def test_call_with_retry_backs_off_then_succeeds():
    attempts = {"n": 0}
    waits: list[float] = []

    def call():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise connection_error()
        return "recovered"

    result = call_with_retry(
        call, RetryPolicy(attempts=3, base_delay_seconds=1.0, jitter_ratio=0.0),
        sleep=waits.append,
    )

    assert result == "recovered"
    assert waits == [1.0, 2.0]


def test_call_with_retry_reraises_the_final_error():
    attempts = {"n": 0}
    waits: list[float] = []

    def call():
        attempts["n"] += 1
        raise connection_error()

    with pytest.raises(APIConnectionError):
        call_with_retry(
            call, RetryPolicy(attempts=3, base_delay_seconds=1.0, jitter_ratio=0.0),
            sleep=waits.append,
        )

    assert attempts["n"] == 3
    assert waits == [1.0, 2.0]


def test_call_with_retry_does_not_retry_other_errors():
    attempts = {"n": 0}
    waits: list[float] = []

    def call():
        attempts["n"] += 1
        raise ValueError("malformed payload")

    with pytest.raises(ValueError, match="malformed payload"):
        call_with_retry(call, RetryPolicy(attempts=5), sleep=waits.append)

    assert attempts["n"] == 1
    assert waits == []


# ── acall_with_retry ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acall_with_retry_backs_off_then_succeeds():
    attempts = {"n": 0}
    waits: list[float] = []

    async def call():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise connection_error()
        return "recovered"

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    result = await acall_with_retry(
        call, RetryPolicy(attempts=3, base_delay_seconds=1.0, jitter_ratio=0.0),
        sleep=fake_sleep,
    )

    assert result == "recovered"
    assert waits == [1.0]


@pytest.mark.asyncio
async def test_acall_with_retry_reraises_the_final_error():
    attempts = {"n": 0}
    waits: list[float] = []

    async def call():
        attempts["n"] += 1
        raise connection_error()

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    with pytest.raises(APIConnectionError):
        await acall_with_retry(
            call, RetryPolicy(attempts=2, base_delay_seconds=1.0, jitter_ratio=0.0),
            sleep=fake_sleep,
        )

    assert attempts["n"] == 2
    assert waits == [1.0]


@pytest.mark.asyncio
async def test_acall_with_retry_never_swallows_cancellation():
    """A cancelled vote or an ``asyncio.wait_for`` deadline must propagate."""
    attempts = {"n": 0}

    async def call():
        attempts["n"] += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await acall_with_retry(call, RetryPolicy(attempts=3))

    assert attempts["n"] == 1
