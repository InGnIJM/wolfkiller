"""Select the transport implementation for a provider profile.

The transport modules import ``langchain_openai`` / ``langchain_anthropic`` (and
through them the ``openai`` / ``anthropic`` SDKs). Those imports cost seconds on
machines where every opened file is scanned by security software, so the
implementation modules are imported on first use instead of at startup.
``prewarm()`` lets the startup thread load them off the request path.
"""

from __future__ import annotations

import importlib

from .base import (
    API_MODE_ANTHROPIC_MESSAGES, API_MODE_CHAT_COMPLETIONS,
    API_MODE_OPENAI_RESPONSES, ProviderProfile,
)


_TRANSPORTS = {
    API_MODE_CHAT_COMPLETIONS: (
        "app.agents.providers.openai_compatible",
        "OpenAICompatibleTransport",
    ),
    API_MODE_ANTHROPIC_MESSAGES: (
        "app.agents.providers.anthropic_messages",
        "AnthropicMessagesTransport",
    ),
    API_MODE_OPENAI_RESPONSES: (
        "app.agents.providers.openai_responses",
        "OpenAIResponsesTransport",
    ),
}


def transport_for(profile: ProviderProfile):
    """Instantiate the transport that speaks the profile's API dialect."""
    try:
        module_name, class_name = _TRANSPORTS[profile.api_mode]
    except KeyError as exc:
        raise ValueError(f"unknown provider api_mode: {profile.api_mode}") from exc
    module = importlib.import_module(module_name)
    return getattr(module, class_name)()


def prewarm() -> None:
    """Import every transport implementation (used by the startup prewarm)."""
    for module_name, _ in _TRANSPORTS.values():
        importlib.import_module(module_name)
