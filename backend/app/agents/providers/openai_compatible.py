from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from .base import CallPurpose, ProviderProfile


class OpenAICompatibleTransport:
    """Build a ChatOpenAI model from a provider profile and call purpose."""

    def build(self, config, profile: ProviderProfile, purpose: CallPurpose) -> BaseChatModel:
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
        }
        if purpose is CallPurpose.ACTION_STRICT and profile.strict_endpoint:
            kwargs["base_url"] = config.strict_base_url
        if profile.capabilities.temperature:
            kwargs["temperature"] = config.temperature
        return ChatOpenAI(**kwargs)
