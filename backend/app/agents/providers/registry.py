from urllib.parse import urlparse

from .base import ModelCapabilities, ProviderProfile


_CHAT_COMPLETIONS = "chat_completions"


class ProviderRegistry:
    """Resolve explicit provider profiles or infer one from the endpoint host."""

    _PROFILES = {
        "openai": ProviderProfile(
            profile_id="openai",
            api_mode=_CHAT_COMPLETIONS,
            capabilities=ModelCapabilities(True, True, True, True, True),
            default_action_max_tokens=2048,
        ),
        "deepseek": ProviderProfile(
            profile_id="deepseek",
            api_mode=_CHAT_COMPLETIONS,
            capabilities=ModelCapabilities(True, True, True, True, True),
            default_action_max_tokens=2048,
            strict_endpoint=True,
        ),
        "openrouter": ProviderProfile(
            profile_id="openrouter",
            api_mode=_CHAT_COMPLETIONS,
            capabilities=ModelCapabilities(True, False, True, True, True),
            default_action_max_tokens=2048,
        ),
        "custom-openai": ProviderProfile(
            profile_id="custom-openai",
            api_mode=_CHAT_COMPLETIONS,
            capabilities=ModelCapabilities(
                True, False, True, False, True, forced_tool_choice=False,
            ),
            default_action_max_tokens=2048,
        ),
    }

    _HOST_PROFILES = {
        "api.openai.com": "openai",
        "api.deepseek.com": "deepseek",
        "openrouter.ai": "openrouter",
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
