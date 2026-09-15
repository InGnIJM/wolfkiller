from dataclasses import replace

import pytest

from app.agents.providers.anthropic_messages import AnthropicMessagesTransport
from app.agents.providers.openai_compatible import OpenAICompatibleTransport
from app.agents.providers.registry import ProviderRegistry
from app.agents.providers.transports import transport_for


@pytest.mark.parametrize(
    ("profile_id", "transport_cls"),
    [
        ("openai", OpenAICompatibleTransport),
        ("deepseek", OpenAICompatibleTransport),
        ("openrouter", OpenAICompatibleTransport),
        ("custom-openai", OpenAICompatibleTransport),
        ("anthropic", AnthropicMessagesTransport),
        ("custom-anthropic", AnthropicMessagesTransport),
    ],
)
def test_transport_matches_profile_api_mode(profile_id, transport_cls):
    profile = ProviderRegistry().resolve(profile_id, "https://example.test", "m")

    assert isinstance(transport_for(profile), transport_cls)


def test_each_call_returns_a_fresh_transport_instance():
    profile = ProviderRegistry().resolve("anthropic", "https://example.test", "m")

    assert transport_for(profile) is not transport_for(profile)


def test_unknown_api_mode_is_rejected():
    profile = replace(
        ProviderRegistry().resolve("openai", "https://example.test", "m"),
        api_mode="responses",
    )

    with pytest.raises(ValueError, match="unknown provider api_mode: responses"):
        transport_for(profile)
