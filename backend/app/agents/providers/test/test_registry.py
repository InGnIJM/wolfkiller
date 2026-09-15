import pytest

from app.agents.providers.base import (
    API_MODE_ANTHROPIC_MESSAGES, API_MODE_CHAT_COMPLETIONS,
)
from app.agents.providers.registry import ProviderRegistry


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.openai.com/v1", "openai"),
        ("https://api.deepseek.com", "deepseek"),
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://api.anthropic.com", "anthropic"),
        ("https://api.anthropic.com/v1", "anthropic"),
        ("http://127.0.0.1:8000/v1", "custom-openai"),
    ],
)
def test_auto_profile_uses_exact_hostname(url, expected):
    assert ProviderRegistry().resolve("auto", url, "model").profile_id == expected


@pytest.mark.parametrize("profile_id", ["anthropic", "custom-anthropic"])
def test_anthropic_profiles_speak_messages_api_with_non_strict_tools(profile_id):
    profile = ProviderRegistry().resolve(profile_id, "https://relay.example", "claude")

    assert profile.api_mode == API_MODE_ANTHROPIC_MESSAGES
    assert profile.capabilities.tools is True
    assert profile.capabilities.strict_tools is False
    assert profile.capabilities.forced_tool_choice is True
    assert profile.capabilities.temperature is True
    assert profile.strict_endpoint is False


def test_openai_style_profiles_speak_chat_completions():
    for profile_id in ("openai", "deepseek", "openrouter", "custom-openai"):
        profile = ProviderRegistry().resolve(profile_id, "https://x.test/v1", "m")
        assert profile.api_mode == API_MODE_CHAT_COMPLETIONS


def test_anthropic_relay_hostname_is_not_auto_detected():
    profile = ProviderRegistry().resolve("auto", "https://anthropic.relay.example", "m")

    assert profile.profile_id == "custom-openai"


def test_openrouter_is_conservative_about_strict_tools():
    profile = ProviderRegistry().resolve("auto", "https://openrouter.ai/api/v1", "xiaomi/mimo-v2-omni")
    assert profile.capabilities.tools is True
    assert profile.capabilities.strict_tools is False


def test_deepseek_enables_strict_endpoint():
    profile = ProviderRegistry().resolve("auto", "https://api.deepseek.com", "deepseek-chat")
    assert profile.capabilities.strict_tools is True
    assert profile.strict_endpoint is True


def test_deepseek_like_hostname_does_not_match():
    profile = ProviderRegistry().resolve("auto", "https://deepseek.example.com/v1", "model")
    assert profile.profile_id == "custom-openai"


def test_unknown_explicit_profile_is_rejected():
    with pytest.raises(ValueError, match="unknown provider profile"):
        ProviderRegistry().resolve("missing", "https://example.test/v1", "model")


@pytest.mark.parametrize(
    "profile_id",
    ["openai", "deepseek", "openrouter", "custom-openai", "anthropic", "custom-anthropic"],
)
def test_profiles_use_config_default_action_token_budget(profile_id):
    profile = ProviderRegistry().resolve(
        profile_id, "https://example.test/v1", "model",
    )

    assert profile.default_action_max_tokens == 2048
