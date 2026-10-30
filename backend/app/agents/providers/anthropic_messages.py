import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_anthropic._client_utils import (
    _AsyncHttpxClientWrapper, _SyncHttpxClientWrapper,
    _get_default_async_httpx_client, _get_default_httpx_client,
)
from langchain_core.language_models import BaseChatModel

from .base import (
    CallPurpose, ProviderProfile, call_budget, merge_request_headers,
    new_session_id, provider_max_retries,
)


# The Messages API only accepts temperatures in [0, 1]; the project default
# (1.2) targets OpenAI-style ranges and must be clamped, not rejected.
_MIN_TEMPERATURE = 0.0
_MAX_TEMPERATURE = 1.0


def clamp_temperature(temperature: float) -> float:
    """Clamp a configured temperature into the Anthropic-accepted range."""
    return min(max(temperature, _MIN_TEMPERATURE), _MAX_TEMPERATURE)


def normalize_base_url(base_url: str) -> str:
    """Strip a trailing ``/v1`` so the SDK does not post to ``/v1/v1/messages``.

    OpenAI-style configurations habitually end in ``/v1``; the Anthropic SDK
    appends ``/v1/messages`` itself, so the suffix must go.
    """
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        trimmed = trimmed[: -len("/v1")]
    return trimmed


class AnthropicMessagesTransport:
    """Build a ChatAnthropic model from a provider profile and call purpose.

    Works for the official endpoint and for any relay that speaks the
    Anthropic Messages protocol; tool definitions arrive in OpenAI function
    format and are converted by ``ChatAnthropic.bind_tools``.
    """

    def __init__(self) -> None:
        # One transport per LLMClient, i.e. one per seat: the session id is
        # minted here so it stays stable for that seat's whole lifetime while
        # two seats never share one.
        self.session_id = new_session_id()

    def build(
        self,
        config,
        profile: ProviderProfile,
        purpose: CallPurpose,
        chat_model_factory=None,
        *,
        dedicated_http_clients: bool = False,
    ) -> BaseChatModel:
        budget = call_budget(config, purpose)
        kwargs = {
            "model": config.model_id,
            "api_key": config.api_key,
            "base_url": normalize_base_url(config.base_url),
            "max_tokens": budget.max_tokens,
            "timeout": budget.timeout,
            "max_retries": provider_max_retries(),
        }
        if profile.capabilities.temperature:
            kwargs["temperature"] = clamp_temperature(config.temperature)
        headers = merge_request_headers(
            profile, getattr(config, "headers", ()), self.session_id,
        )
        if headers:
            kwargs["default_headers"] = headers
        factory = chat_model_factory or ChatAnthropic
        model = factory(**kwargs)
        if dedicated_http_clients and isinstance(model, ChatAnthropic):
            self._inject_dedicated_http_clients(model)
        return model

    @staticmethod
    def _inject_dedicated_http_clients(model: ChatAnthropic) -> None:
        """Swap the shared-pool SDK clients for private ones.

        ``ChatAnthropic`` resolves ``_client``/``_async_client`` lazily through
        LangChain's process-wide per-(base_url, timeout) cache. Pre-seeding
        the cached properties keeps the close path identical while the pool
        belongs to this model alone, so LLMClient.aclose() cannot kill
        sibling clients still using the shared pool.
        """
        params = model._client_params
        http_params = {
            "base_url": params["base_url"],
            "timeout": params["timeout"],
            "proxy": model.anthropic_proxy,
        }
        model.__dict__["_client"] = anthropic.Client(
            **params, http_client=_SyncHttpxClientWrapper(**http_params),
        )
        model.__dict__["_async_client"] = anthropic.AsyncClient(
            **params, http_client=_AsyncHttpxClientWrapper(**http_params),
        )

    @staticmethod
    def discard_shared_http_clients() -> None:
        """Drop LangChain's process-wide httpx cache after an SDK close.

        ChatAnthropic reuses one httpx client per (base_url, timeout). Closing
        that wrapper (game end, model probe) would otherwise leave the next
        LLMClient with a dead pool and every call fails as a connection error.
        """
        _get_default_httpx_client.cache_clear()
        _get_default_async_httpx_client.cache_clear()
