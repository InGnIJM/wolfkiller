"""TypeSafe System One transport (Jev and compatible relays).

``POST /v1/systemone`` is a *decision* API, not a chat API: the request is a
``state`` blob plus a map of typed ``questions`` (``choice``/``score``/…), and
the answer picks one of the declared options with a calibrated probability.
This transport therefore has no ``build()``-into-ChatModel path — every other
transport exists to produce a LangChain surface, while this one exposes
:meth:`decide` directly.

Failures are re-raised as the *openai* exception classes so the shared
failure taxonomy (``transient_provider_errors``/``timeout_errors``/
``classify_provider_error``) classifies Jev outages exactly like a chat
gateway's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import openai

from .base import ProviderProfile, merge_request_headers


@dataclass(frozen=True)
class SystemOneResponse:
    """One ``/systemone`` answer set plus its token usage."""

    answers: dict[str, Any]
    usage: Any  #: ``LLMUsage``; typed loosely to keep the import deferred.


def system_one_endpoint(base_url: str) -> str:
    """Resolve the decide endpoint for any reasonable Base URL shape.

    Accepts a bare host (``https://api.typesafe.ai``), a versioned root
    (``.../v1``), or the fully spelled endpoint; appending a second
    ``/systemone`` to the last would double-post the path.
    """
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/systemone"):
        return trimmed
    if trimmed.endswith("/v1"):
        return f"{trimmed}/systemone"
    return f"{trimmed}/v1/systemone"


_STATUS_TO_ERROR: dict[int, type[openai.APIStatusError]] = {
    400: openai.BadRequestError,
    401: openai.AuthenticationError,
    403: openai.PermissionDeniedError,
    404: openai.NotFoundError,
    422: openai.UnprocessableEntityError,
    429: openai.RateLimitError,
}


class SystemOneTransport:
    """HTTP caller for the System One decide endpoint."""

    def __init__(self, http_client=None, sync_http_client=None):
        #: Injectable for tests; a real ``httpx.AsyncClient`` is created lazily
        #: on first call and closed by :meth:`aclose`.
        self._http = http_client
        #: The sync sibling for callers already running on a worker thread
        #: (the sheriff ballot goes through the synchronous ``_night_invoke``
        #: funnel; ``asyncio.run`` per call would reuse ``_http`` across event
        #: loops). ``httpx.Client`` has no loop to belong to.
        self._sync_http = sync_http_client

    def build(self, *_args, **_kwargs):
        """System One has no chat surface; callers must use :meth:`decide`."""
        raise NotImplementedError(
            "system_one models cannot serve chat completions; "
            "route them through LLMClient.ainvoke_system_one",
        )

    def discard_shared_http_clients(self) -> None:
        """Parity with other transports; this transport owns no shared pool."""

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._sync_http is not None:
            self._sync_http.close()
            self._sync_http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    def _sync_client(self) -> httpx.Client:
        if self._sync_http is None:
            self._sync_http = httpx.Client(timeout=30.0)
        return self._sync_http

    @staticmethod
    def _request(
        config, profile: ProviderProfile, state: Any, questions: dict[str, Any],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers = merge_request_headers(profile, (config.headers or {}).items())
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        return (
            system_one_endpoint(config.base_url),
            headers,
            {
                "model": config.model_id,
                "state": state,
                "questions": questions,
            },
        )

    @staticmethod
    def _reraise_transport_error(
        error: httpx.TransportError, config,
    ) -> None:
        request = (
            getattr(error, "request", None)
            or httpx.Request("POST", config.base_url)
        )
        if isinstance(error, httpx.TimeoutException):
            raise openai.APITimeoutError(request=request) from error
        raise openai.APIConnectionError(
            message=str(error), request=request,
        ) from error

    @staticmethod
    def _parse(response: httpx.Response) -> SystemOneResponse:
        if response.status_code >= 400:
            error_cls = _STATUS_TO_ERROR.get(
                response.status_code, openai.InternalServerError,
            )
            raise error_cls(
                f"systemone request failed with HTTP {response.status_code}",
                response=response,
                body=response.text,
            )
        try:
            payload = response.json()
            answers = payload["answers"]
        except (ValueError, KeyError, TypeError) as error:
            raise openai.InternalServerError(
                "systemone returned a malformed response",
                response=response,
                body=response.text,
            ) from error
        # Deferred: ``llm_client`` imports this module through ``transports``,
        # so a top-level import would make the cycle concrete.
        from app.agents.llm_client import _usage_from_map

        return SystemOneResponse(
            answers=answers, usage=_usage_from_map(payload.get("usage")),
        )

    async def decide(
        self,
        config,
        profile: ProviderProfile,
        state: Any,
        questions: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> SystemOneResponse:
        """Evaluate ``questions`` against ``state`` in one parallel pass."""
        url, headers, body = self._request(config, profile, state, questions)
        try:
            response = await self._client().post(
                url, json=body, headers=headers, timeout=timeout or 30.0,
            )
        except httpx.TransportError as error:
            self._reraise_transport_error(error, config)
        return self._parse(response)

    def decide_sync(
        self,
        config,
        profile: ProviderProfile,
        state: Any,
        questions: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> SystemOneResponse:
        """Worker-thread sibling of :meth:`decide` over a plain ``httpx.Client``."""
        url, headers, body = self._request(config, profile, state, questions)
        try:
            response = self._sync_client().post(
                url, json=body, headers=headers, timeout=timeout or 30.0,
            )
        except httpx.TransportError as error:
            self._reraise_transport_error(error, config)
        return self._parse(response)
