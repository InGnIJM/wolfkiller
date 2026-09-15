from langchain_anthropic import ChatAnthropic
from langchain_anthropic._client_utils import (
    _get_default_async_httpx_client, _get_default_httpx_client,
)
from langchain_core.language_models import BaseChatModel

from .base import CallPurpose, ProviderProfile, call_budget, provider_max_retries


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

    def build(
        self,
        config,
        profile: ProviderProfile,
        purpose: CallPurpose,
        chat_model_factory=None,
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
        factory = chat_model_factory or ChatAnthropic
        return factory(**kwargs)

    @staticmethod
    def discard_shared_http_clients() -> None:
        """Drop LangChain's process-wide httpx cache after an SDK close.

        ChatAnthropic reuses one httpx client per (base_url, timeout). Closing
        that wrapper (game end, model probe) would otherwise leave the next
        LLMClient with a dead pool and every call fails as a connection error.
        """
        _get_default_httpx_client.cache_clear()
        _get_default_async_httpx_client.cache_clear()
