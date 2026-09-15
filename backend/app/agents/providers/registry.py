from urllib.parse import urlparse

from .base import (
    API_MODE_ANTHROPIC_MESSAGES, API_MODE_CHAT_COMPLETIONS,
    ModelCapabilities, ProviderProfile,
)


class ProviderRegistry:
    """Resolve explicit provider profiles or infer one from the endpoint host."""

    _PROFILES = {
        "openai": ProviderProfile(
            profile_id="openai",
            api_mode=API_MODE_CHAT_COMPLETIONS,
            capabilities=ModelCapabilities(True, True, True, True, True),
            default_action_max_tokens=2048,
        ),
        "deepseek": ProviderProfile(
            profile_id="deepseek",
            api_mode=API_MODE_CHAT_COMPLETIONS,
            capabilities=ModelCapabilities(True, True, True, True, True),
            default_action_max_tokens=2048,
            strict_endpoint=True,
        ),
        "openrouter": ProviderProfile(
            profile_id="openrouter",
            api_mode=API_MODE_CHAT_COMPLETIONS,
            capabilities=ModelCapabilities(True, False, True, True, True),
            default_action_max_tokens=2048,
        ),
        "custom-openai": ProviderProfile(
            profile_id="custom-openai",
            api_mode=API_MODE_CHAT_COMPLETIONS,
            capabilities=ModelCapabilities(
                True, False, True, False, True, forced_tool_choice=False,
            ),
            default_action_max_tokens=2048,
        ),
        # Anthropic Messages API: native tool use with a forced tool_choice is
        # reliable, but OpenAI-style strict JSON schema mode is not a Messages
        # concept, so actions use the non-strict tool path.
        "anthropic": ProviderProfile(
            profile_id="anthropic",
            api_mode=API_MODE_ANTHROPIC_MESSAGES,
            capabilities=ModelCapabilities(True, False, True, False, True),
            default_action_max_tokens=2048,
        ),
        # Third-party relays speaking the Messages protocol; chosen explicitly
        # because their hostnames cannot be recognised.
        "custom-anthropic": ProviderProfile(
            profile_id="custom-anthropic",
            api_mode=API_MODE_ANTHROPIC_MESSAGES,
            capabilities=ModelCapabilities(True, False, True, False, True),
            default_action_max_tokens=2048,
        ),
    }

    _HOST_PROFILES = {
        "api.openai.com": "openai",
        "api.deepseek.com": "deepseek",
        "openrouter.ai": "openrouter",
        "api.anthropic.com": "anthropic",
    }

    def resolve(self, profile_id: str, base_url: str, model_id: str) -> ProviderProfile:
        del model_id
        if profile_id != "auto":
            try:
                return self._PROFILES[profile_id]
            except KeyError as exc:
                raise ValueError(f"unknown provider profile: {profile_id}") from exc

        hostname = urlparse(base_url).hostname
        resolved_id = self._HOST_PROFILES.get(hostname, "custom-openai")
        return self._PROFILES[resolved_id]
