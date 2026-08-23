import pytest

from app.agents.providers.registry import ProviderRegistry


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.openai.com/v1", "openai"),
        ("https://api.deepseek.com", "deepseek"),
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("http://127.0.0.1:8000/v1", "custom-openai"),
    ],
)
def test_auto_profile_uses_exact_hostname(url, expected):
    assert ProviderRegistry().resolve("auto", url, "model").profile_id == expected


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
