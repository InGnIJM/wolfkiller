from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.providers.anthropic_messages import (
    AnthropicMessagesTransport, clamp_temperature, normalize_base_url,
)
from app.agents.providers.base import CallPurpose
from app.agents.providers.registry import ProviderRegistry


def config(**overrides):
    values = dict(
        base_url="https://api.anthropic.com",
        strict_base_url="https://unused.test/beta",
        api_key="secret",
        model_id="claude-sonnet-4-5",
        temperature=0.7,
        max_tokens=4096,
        action_max_tokens=2048,
        action_timeout_seconds=90.0,
        action_retry_timeout_seconds=120.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def profile(profile_id="anthropic"):
    return ProviderRegistry().resolve(profile_id, "https://api.anthropic.com", "m")


def test_text_call_passes_core_kwargs_to_chat_anthropic():
    cfg = config()
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        AnthropicMessagesTransport().build(cfg, profile(), CallPurpose.TEXT)
    kwargs = chat.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-5"
    assert kwargs["api_key"] == "secret"
    assert kwargs["base_url"] == "https://api.anthropic.com"
    assert kwargs["max_tokens"] == 4096
    assert kwargs["timeout"] == 90.0
    assert kwargs["temperature"] == 0.7
    assert kwargs["max_retries"] == 4


def test_max_retries_honours_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_MAX_RETRIES", "1")
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        AnthropicMessagesTransport().build(config(), profile(), CallPurpose.TEXT)
    assert chat.call_args.kwargs["max_retries"] == 1


@pytest.mark.parametrize(
    "purpose", [CallPurpose.ACTION_JSON, CallPurpose.TOOLS, CallPurpose.ACTION_STRICT],
)
def test_action_calls_use_action_budget_and_retry_headroom(purpose):
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        AnthropicMessagesTransport().build(config(), profile(), purpose)
    kwargs = chat.call_args.kwargs
    assert kwargs["max_tokens"] == 2048
    assert kwargs["timeout"] == 125.0


def test_strict_purpose_never_switches_endpoint():
    cfg = config(strict_base_url="https://strict.example")
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        AnthropicMessagesTransport().build(cfg, profile(), CallPurpose.ACTION_STRICT)
    assert chat.call_args.kwargs["base_url"] == "https://api.anthropic.com"


def test_temperature_above_one_is_clamped_for_messages_api():
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        AnthropicMessagesTransport().build(
            config(temperature=1.2), profile(), CallPurpose.TEXT,
        )
    assert chat.call_args.kwargs["temperature"] == 1.0


def test_temperature_is_omitted_when_profile_forbids_it():
    forbidding = replace(
        profile(), capabilities=replace(profile().capabilities, temperature=False),
    )
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        AnthropicMessagesTransport().build(config(), forbidding, CallPurpose.TEXT)
    assert "temperature" not in chat.call_args.kwargs


def test_custom_factory_replaces_chat_anthropic():
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return "model"

    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        result = AnthropicMessagesTransport().build(
            config(), profile("custom-anthropic"), CallPurpose.TEXT,
            chat_model_factory=factory,
        )
    assert result == "model"
    assert created[0]["model"] == "claude-sonnet-4-5"
    chat.assert_not_called()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-0.5, 0.0), (0.0, 0.0), (0.3, 0.3), (1.0, 1.0), (1.2, 1.0), (2.0, 1.0)],
)
def test_clamp_temperature(value, expected):
    assert clamp_temperature(value) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.anthropic.com", "https://api.anthropic.com"),
        ("https://api.anthropic.com/", "https://api.anthropic.com"),
        ("https://api.anthropic.com/v1", "https://api.anthropic.com"),
        ("https://api.anthropic.com/v1/", "https://api.anthropic.com"),
        ("https://relay.example/anthropic", "https://relay.example/anthropic"),
        ("https://relay.example/anthropic/v1", "https://relay.example/anthropic"),
        ("https://relay.example/v1beta", "https://relay.example/v1beta"),
    ],
)
def test_normalize_base_url_strips_trailing_v1(url, expected):
    assert normalize_base_url(url) == expected


def test_build_creates_a_real_chat_anthropic_with_normalized_url():
    model = AnthropicMessagesTransport().build(
        config(base_url="https://relay.example/v1/", temperature=1.5),
        profile("custom-anthropic"),
        CallPurpose.TEXT,
    )
    assert model.anthropic_api_url == "https://relay.example"
    assert model.temperature == 1.0
    assert model.max_tokens == 4096


def test_discard_shared_http_clients_drops_langchain_httpx_cache():
    from langchain_anthropic._client_utils import (
        _get_default_async_httpx_client, _get_default_httpx_client,
    )

    sync = _get_default_httpx_client(base_url="https://relay.example", timeout=90.0)
    async_client = _get_default_async_httpx_client(
        base_url="https://relay.example", timeout=90.0,
    )
    AnthropicMessagesTransport.discard_shared_http_clients()
    assert _get_default_httpx_client(
        base_url="https://relay.example", timeout=90.0,
    ) is not sync
    assert _get_default_async_httpx_client(
        base_url="https://relay.example", timeout=90.0,
    ) is not async_client
    AnthropicMessagesTransport.discard_shared_http_clients()
