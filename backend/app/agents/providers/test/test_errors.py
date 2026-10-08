import anthropic
import httpx
import openai
import pytest

from app.agents.providers import errors


def _request():
    return httpx.Request("POST", "https://model.test")


def _status(status_code):
    return httpx.Response(status_code, request=_request())


@pytest.mark.parametrize(
    "error",
    [
        openai.BadRequestError("b", response=_status(400), body=None),
        openai.UnprocessableEntityError("u", response=_status(422), body=None),
        anthropic.BadRequestError("b", response=_status(400), body=None),
        anthropic.UnprocessableEntityError("u", response=_status(422), body=None),
    ],
)
def test_capability_rejections_cover_both_sdks(error):
    assert isinstance(error, errors.CAPABILITY_REJECTION_ERRORS)
    assert not isinstance(error, errors.TRANSIENT_PROVIDER_ERRORS)


@pytest.mark.parametrize(
    "error",
    [
        openai.RateLimitError("l", response=_status(429), body=None),
        anthropic.RateLimitError("l", response=_status(429), body=None),
    ],
)
def test_rate_limits_are_transient(error):
    assert isinstance(error, errors.RATE_LIMIT_ERRORS)
    assert isinstance(error, errors.TRANSIENT_PROVIDER_ERRORS)
    assert not isinstance(error, errors.SERVER_ERRORS)


@pytest.mark.parametrize(
    "error",
    [
        openai.InternalServerError("s", response=_status(500), body=None),
        anthropic.InternalServerError("s", response=_status(500), body=None),
        anthropic.OverloadedError("o", response=_status(529), body=None),
        anthropic.ServiceUnavailableError("u", response=_status(503), body=None),
    ],
)
def test_server_faults_are_transient(error):
    assert isinstance(error, errors.SERVER_ERRORS)
    assert isinstance(error, errors.TRANSIENT_PROVIDER_ERRORS)
    assert not isinstance(error, errors.RATE_LIMIT_ERRORS)


@pytest.mark.parametrize(
    "error",
    [
        openai.APITimeoutError(request=_request()),
        anthropic.APITimeoutError(request=_request()),
    ],
)
def test_timeouts_are_also_connection_errors(error):
    assert isinstance(error, errors.TIMEOUT_ERRORS)
    assert isinstance(error, errors.CONNECTION_ERRORS)
    assert isinstance(error, errors.TRANSIENT_PROVIDER_ERRORS)


@pytest.mark.parametrize(
    "error",
    [
        openai.APIConnectionError(request=_request()),
        anthropic.APIConnectionError(request=_request()),
    ],
)
def test_plain_connection_errors_are_not_timeouts(error):
    assert isinstance(error, errors.CONNECTION_ERRORS)
    assert not isinstance(error, errors.TIMEOUT_ERRORS)


@pytest.mark.parametrize(
    "error",
    [
        openai.AuthenticationError("a", response=_status(401), body=None),
        anthropic.AuthenticationError("a", response=_status(401), body=None),
        RuntimeError("unrelated"),
    ],
)
def test_auth_and_unrelated_errors_are_never_swallowed(error):
    assert not isinstance(error, errors.CAPABILITY_REJECTION_ERRORS)
    assert not isinstance(error, errors.TRANSIENT_PROVIDER_ERRORS)


class _BareStatusError(Exception):
    """A gateway error that is not one of the SDK classes but still has a status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


# ── error_chain / status_code_of ────────────────────────────────


def test_error_chain_is_outermost_first_and_cycle_guarded():
    outer = RuntimeError("outer")
    inner = ValueError("inner")
    outer.__cause__ = inner
    inner.__context__ = outer  # legal in Python: a cycle

    assert errors.error_chain(outer) == [outer, inner]


def test_status_code_of_reads_the_error_itself():
    assert errors.status_code_of(_BareStatusError(503)) == 503


def test_status_code_of_reads_the_response():
    error = openai.InternalServerError(
        "s", response=_status(502), body=None,
    )

    assert errors.status_code_of(error) == 502


def test_status_code_of_walks_the_wrapper_chain():
    """LangChain and both SDKs re-wrap, so the response is on an inner link."""
    outer = RuntimeError("wrapped")
    outer.__cause__ = _BareStatusError(503)

    assert errors.status_code_of(outer) == 503


def test_status_code_of_is_none_without_one():
    assert errors.status_code_of(RuntimeError("plain")) is None


def test_status_code_of_ignores_a_bool_status():
    """``True`` is an ``int`` in Python; a boolean is not an HTTP status."""

    class BooleanStatus(Exception):
        status_code = True

    assert errors.status_code_of(BooleanStatus()) is None


# ── is_refused_error ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "refused"),
    [
        (_BareStatusError(503), True),
        (
            openai.InternalServerError("s", response=_status(503), body=None),
            True,
        ),
        (
            anthropic.ServiceUnavailableError("u", response=_status(503), body=None),
            True,
        ),
        (_BareStatusError(500), False),
        (_BareStatusError(429), False),
        (openai.APIConnectionError(request=_request()), False),
        (RuntimeError("plain"), False),
    ],
)
def test_is_refused_error_only_matches_capacity_refusals(error, refused):
    assert errors.is_refused_error(error) is refused


def test_refused_status_codes_are_exactly_503():
    """429 is excluded on purpose: it carries a hint and its retries do work."""
    assert errors.REFUSED_STATUS_CODES == frozenset({503})


# ── classify_provider_error ─────────────────────────────────────

def _nested(outer, inner):
    """An error whose real cause sits one link down the chain."""
    outer.__cause__ = inner
    return outer


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        # Timeouts are matched by class name first, through the whole chain.
        (
            openai.APITimeoutError(request=_request()),
            "provider_timeout",
        ),
        (
            anthropic.APITimeoutError(request=_request()),
            "provider_timeout",
        ),
        (
            _nested(RuntimeError("wrapped"), TimeoutError("timed out")),
            "provider_timeout",
        ),
        # SDK buckets.
        (
            openai.RateLimitError("l", response=_status(429), body=None),
            "provider_rate_limit",
        ),
        (
            anthropic.OverloadedError("o", response=_status(529), body=None),
            "provider_server_error",
        ),
        (
            openai.InternalServerError("s", response=_status(500), body=None),
            "provider_server_error",
        ),
        (
            openai.InternalServerError("s", response=_status(503), body=None),
            "provider_unavailable",
        ),
        (
            anthropic.ServiceUnavailableError("u", response=_status(503), body=None),
            "provider_unavailable",
        ),
        (
            openai.APIConnectionError(request=_request()),
            "provider_connection_error",
        ),
        # Bare statuses: a gateway that is not one of the SDK classes.
        (_BareStatusError(429), "provider_rate_limit"),
        (_BareStatusError(503), "provider_unavailable"),
        (_BareStatusError(400), "provider_bad_request"),
        (_BareStatusError(422), "provider_bad_request"),
        (_BareStatusError(502), "model_invocation_error"),
        (RuntimeError("plain"), "model_invocation_error"),
    ],
)
def test_classify_provider_error_uses_one_vocabulary(error, expected):
    assert errors.classify_provider_error(error) == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        # The status code is ground truth: a class the SDK picked for another
        # reason must not override what the gateway actually answered.
        (
            openai.InternalServerError("s", response=_status(429), body=None),
            "provider_rate_limit",
        ),
        (
            openai.InternalServerError("s", response=_status(503), body=None),
            "provider_unavailable",
        ),
        (
            openai.RateLimitError("l", response=_status(503), body=None),
            "provider_unavailable",
        ),
    ],
)
def test_a_capacity_status_beats_the_sdk_class(error, expected):
    assert errors.classify_provider_error(error) == expected

