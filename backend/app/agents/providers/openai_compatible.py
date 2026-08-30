import os

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from .base import CallPurpose, ProviderProfile


def _provider_max_retries() -> int:
    """SDK-level retries for transient 429/5xx; honors Retry-After headers."""
    return int(os.getenv("LLM_PROVIDER_MAX_RETRIES", "4"))


class OpenAICompatibleTransport:
    """Build a ChatOpenAI model from a provider profile and call purpose."""

    def build(
        self,
        config,
        profile: ProviderProfile,
        purpose: CallPurpose,
        chat_model_factory=None,
    ) -> BaseChatModel:
        is_action = purpose in {
            CallPurpose.ACTION_JSON,
            CallPurpose.ACTION_STRICT,
            CallPurpose.TOOLS,
        }
        kwargs = {
            "model": config.model_id,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "max_tokens": config.action_max_tokens if is_action else config.max_tokens,
            "timeout": (
                max(config.action_timeout_seconds, config.action_retry_timeout_seconds) + 5.0
                if is_action
                else config.action_timeout_seconds
            ),
            "max_retries": _provider_max_retries(),
        }
        if purpose is CallPurpose.ACTION_STRICT and profile.strict_endpoint:
            kwargs["base_url"] = config.strict_base_url
        if profile.capabilities.temperature:
            kwargs["temperature"] = config.temperature
        factory = chat_model_factory or ChatOpenAI
        return factory(**kwargs)
