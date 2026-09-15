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
