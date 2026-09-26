from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models._client_utils import (
    _cached_async_httpx_client, _cached_sync_httpx_client,
)

from .base import (
    CallPurpose, ProviderProfile, call_budget, merge_request_headers,
    new_session_id, provider_max_retries, strip_responses_suffix,
)


class OpenAICompatibleTransport:
    """Build a ChatOpenAI model from a provider profile and call purpose."""

    #: Set by the Responses subclass, which posts to ``/responses`` instead of
    #: ``/chat/completions`` while sharing every other request parameter.
    use_responses_api = False

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
    ) -> BaseChatModel:
        budget = call_budget(config, purpose)
        kwargs = {
            "model": config.model_id,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "max_tokens": budget.max_tokens,
            "timeout": budget.timeout,
            "max_retries": provider_max_retries(),
        }
        if purpose is CallPurpose.ACTION_STRICT and profile.strict_endpoint:
            kwargs["base_url"] = config.strict_base_url
        if self.use_responses_api:
            # The SDK appends the resource path itself, so a Base URL copied
            # from a provider's model table must lose its ``/responses``.
            kwargs["base_url"] = strip_responses_suffix(kwargs["base_url"])
            kwargs["use_responses_api"] = True
        if profile.capabilities.temperature:
            kwargs["temperature"] = config.temperature
        headers = merge_request_headers(
            profile, getattr(config, "headers", ()), self.session_id,
        )
        if headers:
            kwargs["default_headers"] = headers
        factory = chat_model_factory or ChatOpenAI
        return factory(**kwargs)

    @staticmethod
    def discard_shared_http_clients() -> None:
        """Drop LangChain's process-wide httpx cache after an SDK close.

        ChatOpenAI reuses one httpx client per (base_url, timeout). Closing
        that wrapper would otherwise poison the next LLMClient with a dead pool.
        """
        _cached_sync_httpx_client.cache_clear()
        _cached_async_httpx_client.cache_clear()
