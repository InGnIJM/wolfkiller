from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.providers.base import CallPurpose
from app.agents.providers.openai_compatible import OpenAICompatibleTransport
from app.agents.providers.registry import ProviderRegistry


def config(**overrides):
    values = dict(
        base_url="https://openrouter.ai/api/v1",
        strict_base_url="https://unused.test/beta",
        api_key="secret",
        model_id="xiaomi/mimo-v2-omni",
        temperature=0.7,
        max_tokens=4096,
        action_max_tokens=2048,
        action_timeout_seconds=90.0,
        action_retry_timeout_seconds=120.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_action_budget_is_not_capped_at_768():
    cfg = config()
    profile = ProviderRegistry().resolve("openrouter", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.ACTION_JSON)
    assert chat.call_args.kwargs["max_tokens"] == 2048


def test_strict_action_uses_deepseek_strict_endpoint():
    cfg = config(
        base_url="https://api.deepseek.com",
        strict_base_url="https://api.deepseek.com/beta",
        model_id="deepseek-chat",
    )
    profile = ProviderRegistry().resolve("deepseek", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.ACTION_STRICT)
    assert chat.call_args.kwargs["base_url"] == "https://api.deepseek.com/beta"


def test_temperature_is_omitted_when_profile_forbids_it():
    cfg = config()
    profile = ProviderRegistry().resolve("openai", "https://api.openai.com/v1", "model")
    profile = replace(profile, capabilities=replace(profile.capabilities, temperature=False))
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    assert "temperature" not in chat.call_args.kwargs


def test_text_uses_general_budget_and_timeout():
    cfg = config()
    profile = ProviderRegistry().resolve("openrouter", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    kwargs = chat.call_args.kwargs
    assert kwargs["max_tokens"] == 4096
    assert kwargs["timeout"] == 90.0


def test_action_uses_action_timeout_with_retry_headroom():
    cfg = config()
    profile = ProviderRegistry().resolve("openrouter", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.ACTION_JSON)
    kwargs = chat.call_args.kwargs
    assert kwargs["timeout"] == 125.0


def test_tools_uses_action_budget_and_timeout():
    cfg = config()
    profile = ProviderRegistry().resolve("openrouter", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TOOLS)
    kwargs = chat.call_args.kwargs
    assert kwargs["max_tokens"] == 2048
    assert kwargs["timeout"] == 125.0


def test_strict_action_keeps_base_url_without_strict_endpoint_capability():
    cfg = config(
        base_url="https://openrouter.ai/api/v1",
        strict_base_url="https://unused.test/beta",
    )
    profile = ProviderRegistry().resolve("openrouter", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.ACTION_STRICT)
    assert chat.call_args.kwargs["base_url"] == cfg.base_url


def test_discard_shared_http_clients_drops_langchain_httpx_cache():
    from langchain_openai.chat_models._client_utils import (
        _cached_async_httpx_client, _cached_sync_httpx_client,
    )

    sync = _cached_sync_httpx_client("https://api.deepseek.com", 90.0)
    async_client = _cached_async_httpx_client("https://api.deepseek.com", 90.0)
    OpenAICompatibleTransport.discard_shared_http_clients()
    assert _cached_sync_httpx_client("https://api.deepseek.com", 90.0) is not sync
    assert _cached_async_httpx_client("https://api.deepseek.com", 90.0) is not async_client
    OpenAICompatibleTransport.discard_shared_http_clients()


# ── request headers ───────────────────────────────────────────

def test_plain_profiles_send_no_default_headers():
    cfg = config()
    profile = ProviderRegistry().resolve("openrouter", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    assert "default_headers" not in chat.call_args.kwargs


def test_opencode_profile_sends_a_generated_session_header():
    cfg = config(base_url="https://opencode.ai/zen/v1", model_id="claude-sonnet-4-6")
    profile = ProviderRegistry().resolve("auto", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    headers = chat.call_args.kwargs["default_headers"]
    assert list(headers) == ["x-opencode-session"]
    assert headers["x-opencode-session"].startswith("ses_")


def test_the_session_id_is_stable_across_builds_on_one_transport():
    cfg = config(base_url="https://opencode.ai/zen/v1", model_id="m")
    profile = ProviderRegistry().resolve("auto", cfg.base_url, cfg.model_id)
    transport = OpenAICompatibleTransport()
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        transport.build(cfg, profile, CallPurpose.TEXT)
        transport.build(cfg, profile, CallPurpose.ACTION_JSON)
    first, second = (call.kwargs["default_headers"] for call in chat.call_args_list)
    assert first == second


def test_each_transport_mints_its_own_session_id():
    cfg = config(base_url="https://opencode.ai/zen/v1", model_id="m")
    profile = ProviderRegistry().resolve("auto", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    first, second = (call.kwargs["default_headers"] for call in chat.call_args_list)
    assert first != second


def test_configured_headers_are_merged_with_the_profile_session_header():
    cfg = config(
        base_url="https://opencode.ai/zen/v1", model_id="m",
        headers=(("x-opencode-request", "user-1"),),
    )
    profile = ProviderRegistry().resolve("auto", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    headers = chat.call_args.kwargs["default_headers"]
    assert headers["x-opencode-request"] == "user-1"
    assert headers["x-opencode-session"].startswith("ses_")


def test_configured_headers_override_the_generated_session_header():
    cfg = config(
        base_url="https://opencode.ai/zen/v1", model_id="m",
        headers=(("x-opencode-session", "ses_pinned"),),
    )
    profile = ProviderRegistry().resolve("auto", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    assert chat.call_args.kwargs["default_headers"] == {
        "x-opencode-session": "ses_pinned",
    }


def test_configured_headers_reach_a_plain_profile_without_a_session_header():
    cfg = config(headers=(("http-referer", "https://example.test"),))
    profile = ProviderRegistry().resolve("openrouter", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    assert chat.call_args.kwargs["default_headers"] == {
        "http-referer": "https://example.test",
    }


def test_unusable_configured_headers_are_dropped():
    cfg = config(headers=(("Authorization", "Bearer leaked"), ("bad name", "v")))
    profile = ProviderRegistry().resolve("openrouter", cfg.base_url, cfg.model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.TEXT)
    assert "default_headers" not in chat.call_args.kwargs
