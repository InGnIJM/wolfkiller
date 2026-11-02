import asyncio

import httpx
import pytest
from openai import APIConnectionError, RateLimitError
from pydantic import BaseModel, ValidationError

from app.agents.retry import (
    KIND_REPAIR,
    KIND_TRANSPORT,
    RETRYABLE_OUTPUT_ERRORS,
    RetryPolicy,
    UnusableModelOutput,
    acall_with_retry,
    call_with_retry,
    delay_for,
    retry_after_seconds,
    retry_delay,
    retry_plan,
    retryable_provider_errors,
)


class _BadPayload(BaseModel):
    """Declared at module scope: pydantic builds the schema once."""

    n: int


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

    # ``None`` defers the lookup so constructing a policy (module-level retry
    # ladders do that at import) never imports either provider SDK.
    assert policy.retry_on is None
    assert policy.attempts == 3
    assert isinstance(connection_error(), retryable_provider_errors())
    assert isinstance(rate_limit_error(), retryable_provider_errors())


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

    result = call_with_retry(lambda kind: "ok", RetryPolicy(), sleep=waits.append)

    assert result == "ok"
    assert waits == []


def test_call_with_retry_backs_off_then_succeeds():
    attempts = {"n": 0}
    waits: list[float] = []

    def call(kind):
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

    def call(kind):
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

    def call(kind):
        attempts["n"] += 1
        raise ValueError("malformed payload")

    with pytest.raises(ValueError, match="malformed payload"):
        call_with_retry(call, RetryPolicy(attempts=5), sleep=waits.append)

    assert attempts["n"] == 1
    assert waits == []


# ── retry_plan ──────────────────────────────────────────────────


def test_retry_plan_reports_transport_for_a_provider_fault():
    kind, delay = retry_plan(
        RetryPolicy(attempts=2, base_delay_seconds=1.0, jitter_ratio=0.0),
        connection_error(), 1,
    )

    assert kind == KIND_TRANSPORT
    assert delay == 1.0


def test_retry_plan_reports_repair_for_an_unusable_answer():
    kind, delay = retry_plan(
        RetryPolicy(attempts=2, base_delay_seconds=1.0, jitter_ratio=0.0),
        UnusableModelOutput("empty"), 1,
    )

    assert kind == KIND_REPAIR
    assert delay == 1.0


def test_retry_plan_treats_a_validation_error_as_a_repair():
    """Pydantic surfaces a bad structured payload as ``ValidationError``."""
    with pytest.raises(ValidationError) as caught:
        _BadPayload.model_validate({"n": "not-an-int"})

    kind, _ = retry_plan(RetryPolicy(attempts=2), caught.value, 1)

    assert kind == KIND_REPAIR


def test_retry_plan_is_exhausted_on_the_last_attempt():
    policy = RetryPolicy(attempts=2, base_delay_seconds=0.0, jitter_ratio=0.0)

    assert retry_plan(policy, connection_error(), 2) is None
    assert retry_plan(policy, UnusableModelOutput("empty"), 2) is None


def test_retry_plan_ignores_errors_outside_both_sets():
    assert retry_plan(RetryPolicy(attempts=5), KeyError("nope"), 1) is None


def test_retry_plan_ignores_a_plain_value_error():
    """Only the canonical output errors are repaired, not every ValueError."""
    assert retry_plan(RetryPolicy(attempts=5), ValueError("bug"), 1) is None


def test_retry_plan_prefers_transport_when_both_sets_match():
    """Resending is safe; a dropped connection cannot be "repaired"."""
    policy = RetryPolicy(
        attempts=2, base_delay_seconds=0.0, jitter_ratio=0.0,
        retry_on=(UnusableModelOutput,), repair_on=(UnusableModelOutput,),
    )

    assert retry_plan(policy, UnusableModelOutput("empty"), 1)[0] == KIND_TRANSPORT


def test_retry_plan_rejects_invalid_attempts():
    with pytest.raises(ValueError, match="positive integer"):
        retry_plan(RetryPolicy(), connection_error(), 0)


def test_repair_is_off_when_a_policy_opts_out():
    assert retry_plan(
        RetryPolicy(attempts=5, repair_on=()), UnusableModelOutput("empty"), 1,
    ) is None


def test_retry_plan_resends_an_unclassified_error_when_opted_in():
    """A call site that would forfeit a turn resends even an unknown fault."""
    policy = RetryPolicy(
        attempts=3, base_delay_seconds=1.0, jitter_ratio=0.0,
        retry_unknown_errors=True,
    )

    kind, delay = retry_plan(policy, RuntimeError("gateway wrapper"), 1)

    assert kind == KIND_TRANSPORT
    assert delay == 1.0


def test_retry_plan_still_repairs_known_output_errors_when_opted_in():
    """Opting into unknown faults must not demote a repair to a plain resend."""
    policy = RetryPolicy(
        attempts=3, base_delay_seconds=0.0, jitter_ratio=0.0,
        retry_unknown_errors=True,
    )

    assert retry_plan(policy, UnusableModelOutput("empty"), 1)[0] == KIND_REPAIR


def test_retry_policy_defaults_to_the_canonical_repair_set():
    assert RetryPolicy().repair_on == RETRYABLE_OUTPUT_ERRORS
    assert RETRYABLE_OUTPUT_ERRORS == (UnusableModelOutput, ValidationError)
    # A transparent upgrade: call sites that already catch ValueError keep working.
    assert issubclass(UnusableModelOutput, ValueError)


# ── call_with_retry ─────────────────────────────────────────────


def test_call_with_retry_hands_the_kind_to_each_attempt():
    """The first attempt is a transport attempt; a repair follows a bad answer."""
    kinds: list[str] = []

    def call(kind):
        kinds.append(kind)
        if len(kinds) == 1:
            raise UnusableModelOutput("empty")
        return "recovered"

    result = call_with_retry(
        call, RetryPolicy(attempts=3, base_delay_seconds=0.0, jitter_ratio=0.0),
        sleep=lambda delay: None,
    )

    assert result == "recovered"
    assert kinds == [KIND_TRANSPORT, KIND_REPAIR]


def test_call_with_retry_keeps_resending_after_a_transport_fault():
    kinds: list[str] = []
    remaining = {"n": 2}

    def call(kind):
        kinds.append(kind)
        if remaining["n"]:
            remaining["n"] -= 1
            raise connection_error()
        return "recovered"

    result = call_with_retry(
        call, RetryPolicy(attempts=4, base_delay_seconds=0.0, jitter_ratio=0.0),
        sleep=lambda delay: None,
    )

    assert result == "recovered"
    assert kinds == [KIND_TRANSPORT, KIND_TRANSPORT, KIND_TRANSPORT]


def test_call_with_retry_reraises_an_unusable_answer_when_repair_is_off():
    attempts = {"n": 0}

    def call(kind):
        attempts["n"] += 1
        raise UnusableModelOutput("empty")

    with pytest.raises(UnusableModelOutput):
        call_with_retry(
            call, RetryPolicy(attempts=3, repair_on=()), sleep=lambda delay: None,
        )

    assert attempts["n"] == 1


# ── acall_with_retry ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acall_with_retry_backs_off_then_succeeds():
    attempts = {"n": 0}
    waits: list[float] = []

    async def call(kind):
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

    async def call(kind):
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
async def test_acall_with_retry_repairs_an_unusable_answer():
    kinds: list[str] = []

    async def call(kind):
        kinds.append(kind)
        if len(kinds) == 1:
            raise UnusableModelOutput("empty")
        return "recovered"

    async def fake_sleep(delay: float) -> None:
        return None

    result = await acall_with_retry(
        call, RetryPolicy(attempts=3, base_delay_seconds=0.0, jitter_ratio=0.0),
        sleep=fake_sleep,
    )

    assert result == "recovered"
    assert kinds == [KIND_TRANSPORT, KIND_REPAIR]


@pytest.mark.asyncio
async def test_acall_with_retry_reraises_the_final_unusable_answer():
    attempts = {"n": 0}

    async def call(kind):
        attempts["n"] += 1
        raise UnusableModelOutput("empty")

    async def fake_sleep(delay: float) -> None:
        return None

    with pytest.raises(UnusableModelOutput):
        await acall_with_retry(
            call, RetryPolicy(attempts=2, base_delay_seconds=0.0, jitter_ratio=0.0),
            sleep=fake_sleep,
        )

    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_acall_with_retry_never_swallows_cancellation():
    """A cancelled vote or an ``asyncio.wait_for`` deadline must propagate."""
    attempts = {"n": 0}

    async def call(kind):
        attempts["n"] += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await acall_with_retry(call, RetryPolicy(attempts=3))

    assert attempts["n"] == 1


# ── refusals: one long wait, then stop ──────────────────────────


class _Refusal(Exception):
    """An HTTP 503 as a gateway that is not one of the SDK classes raises it."""

    def __init__(self, status_code: int = 503, *, retry_after=None) -> None:
        super().__init__("no available account")
        self.status_code = status_code
        self.response = httpx.Response(
            status_code,
            request=httpx.Request("POST", "https://model.test/v1"),
            headers={} if retry_after is None else {"retry-after": str(retry_after)},
        )


def test_retry_plan_gives_a_refusal_one_long_wait():
    """The short transport backoff cannot span a capacity cooldown."""
    policy = RetryPolicy(
        attempts=3, base_delay_seconds=0.5, jitter_ratio=0.0,
        refused_delay_seconds=20.0,
    )

    kind, delay = retry_plan(policy, _Refusal(), 1)

    assert kind == KIND_TRANSPORT
    assert delay == 20.0


def test_retry_plan_stops_after_the_first_refusal_retry():
    """A second refusal says the same thing; another wait only spends the turn."""
    policy = RetryPolicy(attempts=3, refused_delay_seconds=20.0)

    assert retry_plan(policy, _Refusal(), 2) is None


def test_retry_plan_honours_a_refusal_retry_after_above_the_floor():
    policy = RetryPolicy(
        attempts=3, refused_delay_seconds=5.0, max_retry_after_seconds=30.0,
    )

    assert retry_plan(policy, _Refusal(retry_after=25), 1)[1] == 25.0


def test_retry_plan_caps_a_refusal_retry_after():
    policy = RetryPolicy(
        attempts=3, refused_delay_seconds=5.0, max_retry_after_seconds=30.0,
    )

    assert retry_plan(policy, _Refusal(retry_after=600), 1)[1] == 30.0


def test_retry_plan_keeps_the_floor_when_the_hint_is_shorter():
    policy = RetryPolicy(
        attempts=3, refused_delay_seconds=20.0, max_retry_after_seconds=30.0,
    )

    assert retry_plan(policy, _Refusal(retry_after=3), 1)[1] == 20.0


def test_a_refusal_setting_of_zero_retries_immediately():
    """Ops can disable the long wait; tests use it to stay fast.

    ``0`` means "no long wait", not "use the ordinary ladder": the refusal rule
    runs before the generic classification, so the floor *is* the whole delay.
    """
    policy = RetryPolicy(
        attempts=3, base_delay_seconds=0.5, jitter_ratio=0.0,
        refused_delay_seconds=0.0,
    )

    assert retry_plan(policy, _Refusal(), 1)[1] == 0.0


def test_retry_plan_checks_the_budget_before_the_refusal_rule():
    policy = RetryPolicy(attempts=1, refused_delay_seconds=20.0)

    assert retry_plan(policy, _Refusal(), 1) is None


def test_call_with_retry_resends_a_refusal_once():
    kinds = []
    remaining = {"n": 1}

    def call(kind):
        kinds.append(kind)
        if remaining["n"]:
            remaining["n"] -= 1
            raise _Refusal()
        return "recovered"

    waits: list[float] = []
    result = call_with_retry(
        call,
        RetryPolicy(attempts=3, base_delay_seconds=0.0, jitter_ratio=0.0,
                    refused_delay_seconds=20.0),
        sleep=waits.append,
    )

    assert result == "recovered"
    assert kinds == [KIND_TRANSPORT, KIND_TRANSPORT]
    assert waits == [20.0]


def test_call_with_retry_stops_after_a_second_refusal():
    attempts = {"n": 0}
    waits: list[float] = []

    def call(kind):
        attempts["n"] += 1
        raise _Refusal()

    with pytest.raises(_Refusal):
        call_with_retry(
            call,
            RetryPolicy(attempts=4, base_delay_seconds=0.0, jitter_ratio=0.0,
                        refused_delay_seconds=20.0),
            sleep=waits.append,
        )

    assert attempts["n"] == 2
    assert waits == [20.0]


def test_unusable_model_output_carries_its_own_code():
    assert UnusableModelOutput("empty").code == "unusable_model_output"
    assert UnusableModelOutput("short", code="speech_text_too_short").code == (
        "speech_text_too_short"
    )
