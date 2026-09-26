import pytest

from app.agents.providers.base import (
    API_MODE_ANTHROPIC_MESSAGES, API_MODE_CHAT_COMPLETIONS,
    API_MODE_OPENAI_RESPONSES,
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
    for profile_id in (
        "openai", "deepseek", "openrouter", "custom-openai",
        "opencode", "opencode-go",
    ):
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
    [
        "openai", "deepseek", "openrouter", "custom-openai",
        "anthropic", "custom-anthropic", "opencode", "opencode-go",
    ],
)
def test_profiles_use_config_default_action_token_budget(profile_id):
    profile = ProviderRegistry().resolve(
        profile_id, "https://example.test/v1", "model",
    )

    assert profile.default_action_max_tokens == 2048


# ── OpenCode Zen / Go ─────────────────────────────────────────

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://opencode.ai/zen/v1", "opencode"),
        ("https://opencode.ai/zen/v1/", "opencode"),
        ("https://opencode.ai/zen/go/v1", "opencode-go"),
        ("https://opencode.ai/zen/go", "opencode-go"),
    ],
)
def test_auto_profile_separates_zen_from_go_by_path(url, expected):
    assert ProviderRegistry().resolve("auto", url, "model").profile_id == expected


def test_opencode_host_without_a_known_path_defaults_to_zen():
    profile = ProviderRegistry().resolve("auto", "https://opencode.ai/v1", "model")

    assert profile.profile_id == "opencode"


def test_opencode_lookalike_hostname_is_not_auto_detected():
    profile = ProviderRegistry().resolve("auto", "https://opencode.ai.evil.test/zen/v1", "m")

    assert profile.profile_id == "custom-openai"


@pytest.mark.parametrize("profile_id", ["opencode", "opencode-go"])
def test_opencode_profiles_request_the_session_header(profile_id):
    profile = ProviderRegistry().resolve(profile_id, "https://x.test/v1", "m")

    assert profile.session_header == "x-opencode-session"


@pytest.mark.parametrize("profile_id", ["opencode", "opencode-go"])
def test_opencode_profiles_stay_conservative_about_forced_tool_choice(profile_id):
    """Unverified gateway behaviour must not become a hard 400 on every call."""
    profile = ProviderRegistry().resolve(profile_id, "https://x.test/v1", "m")

    assert profile.capabilities.tools is True
    assert profile.capabilities.strict_tools is False
    assert profile.capabilities.forced_tool_choice is False


def test_only_opencode_profiles_carry_a_session_header():
    for profile_id in ("openai", "deepseek", "openrouter", "custom-openai",
                       "anthropic", "custom-anthropic"):
        profile = ProviderRegistry().resolve(profile_id, "https://x.test/v1", "m")
        assert profile.session_header is None
        assert profile.default_headers == ()


# ── OpenAI Responses dialect ──────────────────────────────────

@pytest.mark.parametrize(
    ("url", "expected_id"),
    [
        ("https://opencode.ai/zen/v1/responses", "opencode"),
        ("https://opencode.ai/zen/go/v1/responses", "opencode-go"),
        ("https://api.openai.com/v1/responses", "openai"),
        ("https://relay.example/v1/responses", "custom-openai"),
    ],
)
def test_a_responses_endpoint_switches_the_dialect(url, expected_id):
    profile = ProviderRegistry().resolve("auto", url, "muse-spark-1.3-contributor-free")

    assert profile.profile_id == expected_id
    assert profile.api_mode == API_MODE_OPENAI_RESPONSES


@pytest.mark.parametrize(
    "url",
    [
        "https://opencode.ai/zen/v1",
        "https://opencode.ai/zen/go/v1",
        "https://api.openai.com/v1",
        "https://relay.example/v1/chat/completions",
    ],
)
def test_a_plain_endpoint_keeps_chat_completions(url):
    profile = ProviderRegistry().resolve("auto", url, "m")

    assert profile.api_mode == API_MODE_CHAT_COMPLETIONS


def test_the_responses_dialect_keeps_the_provider_session_header():
    """Pasting the published Zen URL must not lose the OpenCode headers."""
    profile = ProviderRegistry().resolve(
        "auto", "https://opencode.ai/zen/v1/responses", "m",
    )

    assert profile.session_header == "x-opencode-session"


@pytest.mark.parametrize(
    "url",
    [
        "https://opencode.ai/zen/v1/responses",
        "https://api.openai.com/v1/responses",
        "https://relay.example/v1/responses",
    ],
)
def test_the_responses_dialect_drops_strict_tools(url):
    """The reference client clears ``strict`` on every tool for this dialect."""
    profile = ProviderRegistry().resolve("auto", url, "m")

    assert profile.capabilities.strict_tools is False
    assert profile.capabilities.tools is True


def test_the_dialect_override_does_not_leak_into_the_shared_profile():
    """``replace()`` must not mutate the frozen profile registry entry."""
    ProviderRegistry().resolve("auto", "https://api.openai.com/v1/responses", "m")
    plain = ProviderRegistry().resolve("auto", "https://api.openai.com/v1", "m")

    assert plain.api_mode == API_MODE_CHAT_COMPLETIONS
    assert plain.capabilities.strict_tools is True


def test_explicit_responses_profile_works_without_the_url_suffix():
    profile = ProviderRegistry().resolve(
        "openai-responses", "https://api.openai.com/v1", "m",
    )

    assert profile.api_mode == API_MODE_OPENAI_RESPONSES
    assert profile.capabilities.tools is True
    assert profile.capabilities.strict_tools is False
    assert profile.strict_endpoint is False


def test_explicit_responses_profile_applies_to_an_explicit_zen_profile():
    profile = ProviderRegistry().resolve(
        "opencode", "https://opencode.ai/zen/v1/responses", "m",
    )

    assert profile.api_mode == API_MODE_OPENAI_RESPONSES
    assert profile.session_header == "x-opencode-session"
