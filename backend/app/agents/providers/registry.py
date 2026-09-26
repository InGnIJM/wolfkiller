from dataclasses import replace
from urllib.parse import urlparse

from .base import (
    API_MODE_ANTHROPIC_MESSAGES, API_MODE_CHAT_COMPLETIONS,
    API_MODE_OPENAI_RESPONSES, ModelCapabilities, ProviderProfile,
    is_responses_endpoint,
)


#: Host shared by the two OpenCode gateways.
_OPENCODE_HOST = "opencode.ai"

#: Header the OpenCode gateways expect on every request.
_OPENCODE_SESSION_HEADER = "x-opencode-session"

#: Zen and Go live on the same host and differ only by path prefix, so the
#: longer prefix has to be tested first.
_OPENCODE_PATH_PROFILES = (
    ("/zen/go", "opencode-go"),
    ("/zen", "opencode"),
)


def _opencode_profile(profile_id: str) -> ProviderProfile:
    """Build a Zen/Go profile; the two differ only by endpoint.

    Capabilities deliberately mirror ``custom-openai``: the gateways are
    OpenAI-compatible relays whose strict-schema and forced-tool-choice
    behaviour has not been verified on a real endpoint, and guessing wrong
    turns every action call into a hard 400. Relax these once measured.
    """
    return ProviderProfile(
        profile_id=profile_id,
        api_mode=API_MODE_CHAT_COMPLETIONS,
        capabilities=ModelCapabilities(
            True, False, True, False, True, forced_tool_choice=False,
        ),
        default_action_max_tokens=2048,
        session_header=_OPENCODE_SESSION_HEADER,
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
        # OpenCode Zen and OpenCode Go: OpenAI-compatible gateways that also
        # require a session-affinity header on every request.
        "opencode": _opencode_profile("opencode"),
        "opencode-go": _opencode_profile("opencode-go"),
        # OpenAI's Responses dialect, for hosts whose Base URL does not carry
        # the ``/responses`` suffix and therefore cannot be auto-detected.
        # Strict tools are off: the Responses schema dialect differs from
        # chat-completions strict mode, and the reference client clears
        # ``strict`` on every tool for this dialect.
        "openai-responses": ProviderProfile(
            profile_id="openai-responses",
            api_mode=API_MODE_OPENAI_RESPONSES,
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

    @staticmethod
    def _opencode_profile_id(path: str) -> str:
        """Pick Zen or Go from the endpoint path; Zen is the host default."""
        for prefix, profile_id in _OPENCODE_PATH_PROFILES:
            if path.startswith(prefix):
                return profile_id
        return "opencode"

    @staticmethod
    def _apply_endpoint_dialect(
        profile: ProviderProfile, path: str,
    ) -> ProviderProfile:
        """Let a ``/responses`` endpoint override the profile's dialect.

        Providers publish the dialect in the endpoint column of their model
        table, so a Base URL pasted verbatim has to win over the profile
        default. Only the dialect and strict-tool support change: the
        provider's own headers and capabilities are carried over, which is
        what keeps the OpenCode session header on a Zen Responses URL.
        """
        if not is_responses_endpoint(path):
            return profile
        return replace(
            profile,
            api_mode=API_MODE_OPENAI_RESPONSES,
            capabilities=replace(profile.capabilities, strict_tools=False),
        )

    def resolve(self, profile_id: str, base_url: str, model_id: str) -> ProviderProfile:
        del model_id
        parsed = urlparse(base_url)
        if profile_id != "auto":
            try:
                profile = self._PROFILES[profile_id]
            except KeyError as exc:
                raise ValueError(f"unknown provider profile: {profile_id}") from exc
            return self._apply_endpoint_dialect(profile, parsed.path)

        hostname = parsed.hostname
        if hostname == _OPENCODE_HOST:
            profile = self._PROFILES[self._opencode_profile_id(parsed.path)]
        else:
            profile = self._PROFILES[self._HOST_PROFILES.get(hostname, "custom-openai")]
        return self._apply_endpoint_dialect(profile, parsed.path)
