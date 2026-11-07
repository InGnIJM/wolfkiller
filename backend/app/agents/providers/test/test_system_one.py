"""System One (Jev) transport: request shape, answers, error mapping.

The TypeSafe ``POST /v1/systemone`` API is not a chat API: it takes a
``state`` blob plus typed ``questions`` and returns typed answers. The
transport owns the dialect end to end so the rest of the stack sees the
same provider-neutral failure classes every other gateway raises.
"""

from types import SimpleNamespace

import httpx
import pytest

from app.agents.providers.base import API_MODE_SYSTEM_ONE, ProviderProfile
from app.agents.providers.registry import ProviderRegistry
from app.agents.providers.system_one import (
    SystemOneResponse,
    SystemOneTransport,
    system_one_endpoint,
)
from app.agents.providers.transports import transport_for


def _config(**overrides):
    values = SimpleNamespace(
        base_url="https://api.typesafe.ai",
        model_id="jev-latest",
        api_key="key",
        headers={},
    )
    for key, value in overrides.items():
        setattr(values, key, value)
    return values


def _profile() -> ProviderProfile:
    return ProviderRegistry().resolve("jev", "https://api.typesafe.ai", "jev-latest")


class _StubHttp:
    """Stand-in for httpx.AsyncClient capturing posts and replaying a queue."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []
        self.closed = False

    async def post(self, url, *, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self):
        self.closed = True


class _StubSyncHttp:
    """Stand-in for httpx.Client: same capture, plus a close() marker."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []
        self.closed = False

    def post(self, url, *, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("POST", "https://x"))


def _err(status: int) -> httpx.Response:
    return httpx.Response(
        status, json={"error": "bad"}, request=httpx.Request("POST", "https://x"),
    )


def test_jev_profile_resolves_to_the_system_one_transport():
    profile = _profile()

    assert profile.api_mode == API_MODE_SYSTEM_ONE
    assert isinstance(transport_for(profile), SystemOneTransport)
    capabilities = profile.capabilities
    assert capabilities.tools is False
    assert capabilities.strict_tools is False
    assert capabilities.json_output is False
    assert capabilities.temperature is False
    assert capabilities.forced_tool_choice is False


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.typesafe.ai", "https://api.typesafe.ai/v1/systemone"),
        ("https://api.typesafe.ai/v1", "https://api.typesafe.ai/v1/systemone"),
        ("https://api.typesafe.ai/v1/", "https://api.typesafe.ai/v1/systemone"),
        (
            "https://inference.do-ai.run/v1",
            "https://inference.do-ai.run/v1/systemone",
        ),
        (
            "https://relay.example/systemone",
            "https://relay.example/systemone",
        ),
    ],
)
def test_system_one_endpoint_joining(base_url, expected):
    assert system_one_endpoint(base_url) == expected


def test_typesafe_host_auto_detects_jev():
    profile = ProviderRegistry().resolve(
        "auto", "https://api.typesafe.ai/v1", "jev-latest",
    )
    assert profile.profile_id == "jev"
    assert profile.api_mode == API_MODE_SYSTEM_ONE


@pytest.mark.asyncio
async def test_decide_posts_state_and_questions_and_reads_answers():
    http = _StubHttp([
        _ok({
            "model": "jev-1.13.0",
            "answers": {
                "ballot": {
                    "type": "choice",
                    "choice": "5",
                    "confidence": 0.87,
                    "probabilities": {"5": 0.87, "abstain": 0.13},
                },
            },
            "usage": {"input_tokens": 328, "output_tokens": 34},
        }),
    ])
    transport = SystemOneTransport(http_client=http)
    questions = {
        "ballot": {
            "type": "choice",
            "instructions": "pick",
            "criteria": {"5": "seat 5", "abstain": "pass"},
        },
    }

    response = await transport.decide(
        _config(), _profile(), "game state text", questions,
    )

    post = http.posts[0]
    assert post["url"] == "https://api.typesafe.ai/v1/systemone"
    assert post["json"]["model"] == "jev-latest"
    assert post["json"]["state"] == "game state text"
    assert post["json"]["questions"] == questions
    assert post["headers"]["Authorization"] == "Bearer key"
    assert isinstance(response, SystemOneResponse)
    assert response.answers["ballot"]["choice"] == "5"
    assert response.answers["ballot"]["confidence"] == 0.87
    assert response.usage.prompt_tokens == 328
    assert response.usage.completion_tokens == 34


@pytest.mark.asyncio
async def test_decide_applies_custom_headers():
    http = _StubHttp([
        _ok({"answers": {}, "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ])
    transport = SystemOneTransport(http_client=http)
    config = _config(headers={"X-Tenant": "wolf"})

    await transport.decide(config, _profile(), "s", {"q": {"type": "choice"}})

    assert http.posts[0]["headers"]["X-Tenant"] == "wolf"


@pytest.mark.asyncio
async def test_decide_without_key_sends_no_authorization():
    http = _StubHttp([
        _ok({"answers": {}, "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ])
    transport = SystemOneTransport(http_client=http)

    await transport.decide(
        _config(api_key=""), _profile(), "s", {"q": {}},
    )

    assert "Authorization" not in http.posts[0]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "RateLimitError"),
        (500, "InternalServerError"),
        (503, "InternalServerError"),
        (400, "BadRequestError"),
        (401, "AuthenticationError"),
        (404, "NotFoundError"),
    ],
)
async def test_decide_maps_http_errors_to_openai_classes(status, expected):
    http = _StubHttp([_err(status)])
    transport = SystemOneTransport(http_client=http)

    with pytest.raises(Exception) as exc_info:
        await transport.decide(_config(), _profile(), "s", {"q": {}})

    assert type(exc_info.value).__name__ == expected


@pytest.mark.asyncio
async def test_decide_maps_connect_failures_to_api_connection_error():
    request = httpx.Request("POST", "https://x")
    http = _StubHttp([httpx.ConnectError("down", request=request)])
    transport = SystemOneTransport(http_client=http)

    with pytest.raises(Exception) as exc_info:
        await transport.decide(_config(), _profile(), "s", {"q": {}})

    assert type(exc_info.value).__name__ == "APIConnectionError"


@pytest.mark.asyncio
async def test_decide_maps_timeouts_to_api_timeout_error():
    request = httpx.Request("POST", "https://x")
    http = _StubHttp([httpx.ReadTimeout("slow", request=request)])
    transport = SystemOneTransport(http_client=http)

    with pytest.raises(Exception) as exc_info:
        await transport.decide(_config(), _profile(), "s", {"q": {}})

    assert type(exc_info.value).__name__ == "APITimeoutError"


@pytest.mark.asyncio
async def test_decide_rejects_a_malformed_response():
    http = _StubHttp([_ok({"unexpected": True})])
    transport = SystemOneTransport(http_client=http)

    with pytest.raises(Exception) as exc_info:
        await transport.decide(_config(), _profile(), "s", {"q": {}})

    assert type(exc_info.value).__name__ == "InternalServerError"


@pytest.mark.asyncio
async def test_real_http_client_is_created_and_closed():
    transport = SystemOneTransport()
    assert transport._http is None
    await transport.aclose()

    created = transport._client()
    assert isinstance(created, httpx.AsyncClient)
    await transport.aclose()
    assert transport._http is None


@pytest.mark.asyncio
async def test_aclose_releases_both_http_clients():
    async_http, sync_http = _StubHttp([]), _StubSyncHttp([])
    transport = SystemOneTransport(
        http_client=async_http, sync_http_client=sync_http,
    )

    await transport.aclose()

    assert transport._http is None
    assert transport._sync_http is None
    assert async_http.closed is True
    assert sync_http.closed is True


def test_decide_sync_posts_and_reads_answers():
    http = _StubSyncHttp([
        _ok({
            "model": "jev-1.13.0",
            "answers": {
                "ballot": {"type": "choice", "choice": "2", "confidence": 0.5},
            },
            "usage": {"input_tokens": 10, "output_tokens": 4},
        }),
    ])
    transport = SystemOneTransport(sync_http_client=http)
    questions = {
        "ballot": {
            "type": "choice",
            "instructions": "pick",
            "criteria": {"2": "seat 2", "pass": "abstain"},
        },
    }

    response = transport.decide_sync(
        _config(), _profile(), "state text", questions, timeout=5.0,
    )

    post = http.posts[0]
    assert post["url"] == "https://api.typesafe.ai/v1/systemone"
    assert post["json"]["state"] == "state text"
    assert post["json"]["questions"] == questions
    assert post["headers"]["Authorization"] == "Bearer key"
    assert response.answers["ballot"]["choice"] == "2"
    assert response.usage.prompt_tokens == 10


def test_decide_sync_reuses_the_sync_client_and_closes_it():
    http = _StubSyncHttp([
        _ok({"answers": {}, "usage": None}),
        _ok({"answers": {}, "usage": None}),
    ])
    transport = SystemOneTransport(sync_http_client=http)

    transport.decide_sync(_config(), _profile(), "s", {"q": {}})
    transport.decide_sync(_config(), _profile(), "s", {"q": {}})

    assert len(http.posts) == 2
    assert transport._sync_http is http

    import asyncio

    asyncio.run(transport.aclose())
    assert http.closed is True
    assert transport._sync_http is None


def test_decide_sync_creates_a_real_httpx_client_lazily():
    transport = SystemOneTransport()

    created = transport._sync_client()

    assert isinstance(created, httpx.Client)
    assert transport._sync_client() is created


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "RateLimitError"),
        (500, "InternalServerError"),
        (400, "BadRequestError"),
        (401, "AuthenticationError"),
        (403, "PermissionDeniedError"),
        (404, "NotFoundError"),
        (422, "UnprocessableEntityError"),
    ],
)
def test_decide_sync_maps_http_errors_to_openai_classes(status, expected):
    http = _StubSyncHttp([_err(status)])
    transport = SystemOneTransport(sync_http_client=http)

    with pytest.raises(Exception) as exc_info:
        transport.decide_sync(_config(), _profile(), "s", {"q": {}})

    assert type(exc_info.value).__name__ == expected


def test_decide_sync_maps_connect_failures_to_api_connection_error():
    request = httpx.Request("POST", "https://x")
    http = _StubSyncHttp([httpx.ConnectError("down", request=request)])
    transport = SystemOneTransport(sync_http_client=http)

    with pytest.raises(Exception) as exc_info:
        transport.decide_sync(_config(), _profile(), "s", {"q": {}})

    assert type(exc_info.value).__name__ == "APIConnectionError"


def test_decide_sync_maps_timeouts_to_api_timeout_error():
    request = httpx.Request("POST", "https://x")
    http = _StubSyncHttp([httpx.ReadTimeout("slow", request=request)])
    transport = SystemOneTransport(sync_http_client=http)

    with pytest.raises(Exception) as exc_info:
        transport.decide_sync(_config(), _profile(), "s", {"q": {}})

    assert type(exc_info.value).__name__ == "APITimeoutError"


def test_decide_sync_rejects_a_malformed_response():
    http = _StubSyncHttp([_ok({"unexpected": True})])
    transport = SystemOneTransport(sync_http_client=http)

    with pytest.raises(Exception) as exc_info:
        transport.decide_sync(_config(), _profile(), "s", {"q": {}})

    assert type(exc_info.value).__name__ == "InternalServerError"


def test_build_is_not_a_chat_surface():
    transport = SystemOneTransport()
    with pytest.raises(NotImplementedError, match="system_one"):
        transport.build(_config(), _profile(), None)
