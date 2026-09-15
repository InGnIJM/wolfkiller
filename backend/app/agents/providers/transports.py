from .anthropic_messages import AnthropicMessagesTransport
from .base import (
    API_MODE_ANTHROPIC_MESSAGES, API_MODE_CHAT_COMPLETIONS, ProviderProfile,
)
from .openai_compatible import OpenAICompatibleTransport


_TRANSPORTS = {
    API_MODE_CHAT_COMPLETIONS: OpenAICompatibleTransport,
    API_MODE_ANTHROPIC_MESSAGES: AnthropicMessagesTransport,
}


def transport_for(profile: ProviderProfile):
    """Instantiate the transport that speaks the profile's API dialect."""
    try:
        transport_cls = _TRANSPORTS[profile.api_mode]
    except KeyError as exc:
        raise ValueError(f"unknown provider api_mode: {profile.api_mode}") from exc
    return transport_cls()
