import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI as _LangChainChatOpenAI
from langchain_openai.chat_models._client_utils import (
    _cached_async_httpx_client, _cached_sync_httpx_client,
)

from .base import (
    CallPurpose, ProviderProfile, call_budget, merge_request_headers,
    new_session_id, provider_max_retries, strip_responses_suffix,
)

#: Non-standard delta keys that carry a thinking model's reasoning stream.
REASONING_DELTA_KEYS = ("reasoning_content", "reasoning")


class ChatOpenAI(_LangChainChatOpenAI):
    """``ChatOpenAI`` that keeps the reasoning stream of OpenAI-compatible gateways.

    LangChain drops ``delta.reasoning_content``: a thinking model then streams
    for tens of seconds while every chunk looks empty, so the audience sees
    nothing until the tool arguments arrive at the very end.
    """

    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info,
    ):
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info,
        )
        if generation is None or not isinstance(generation.message, AIMessageChunk):
            return generation
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        delta = choices[0].get("delta") if choices else None
        if not isinstance(delta, dict):
            return generation
        for key in REASONING_DELTA_KEYS:
            value = delta.get(key)
            if isinstance(value, str) and value:
                generation.message.additional_kwargs["reasoning_content"] = value
                break
        return generation


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
        *,
        dedicated_http_clients: bool = False,
    ) -> BaseChatModel:
        budget = call_budget(config, purpose)
        kwargs = {
            "model": config.model_id,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "max_tokens": budget.max_tokens,
            # An unhashable timeout makes the SDK skip its process-wide
            # per-(base_url, timeout) httpx cache: the model then owns a
            # private pool that LLMClient.aclose() may close without killing
            # sibling clients still holding the shared one.
            "timeout": (
                httpx.Timeout(budget.timeout)
                if dedicated_http_clients else budget.timeout
            ),
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
        # The Responses dialect wants a nested ``reasoning`` object instead;
        # a flat ``reasoning_effort`` is only the chat-completions spelling.
        if getattr(config, "reasoning_effort", None) and not self.use_responses_api:
            kwargs["model_kwargs"] = {
                "reasoning_effort": config.reasoning_effort,
            }
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
