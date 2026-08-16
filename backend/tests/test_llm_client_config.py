from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.llm_client import (
    LLMClient, LLMClientConfig, derive_strict_base_url, env_default_client_config,
)
from app.models.contracts import ActionContract
from app.models.game import GamePhase


def _config(**overrides):
    base = dict(
        base_url="https://example.test/v1",
        api_key="sk-test-key",
        model_id="test-model",
        temperature=0.7,
        max_tokens=512,
        strict_base_url="https://example.test/beta",
    )
    base.update(overrides)
    return LLMClientConfig(**base)


def test_env_default_client_config_materializes_app_config(monkeypatch):
    import app.agents.llm_client as mod

    monkeypatch.setattr(
        mod.app_config,
        "llm",
        SimpleNamespace(
            base_url="http://env.test/v1",
            api_key="env-key",
            models=["env-model"],
            temperature=1.1,
            max_tokens=2048,
            strict_base_url="http://env.test/beta",
        ),
    )

    cfg = env_default_client_config()

    assert cfg == LLMClientConfig(
        base_url="http://env.test/v1", api_key="env-key", model_id="env-model",
        temperature=1.1, max_tokens=2048, strict_base_url="http://env.test/beta",
    )


def _patch_empty_models(mod, monkeypatch):
    monkeypatch.setattr(
        mod.app_config, "llm",
        SimpleNamespace(
            base_url="http://env.test/v1", api_key="env-key",
            models=[], temperature=1.0, max_tokens=512,
            strict_base_url="http://env.test/beta",
        ),
    )


def test_env_default_client_config_falls_back_when_models_empty(monkeypatch):
    import app.agents.llm_client as mod

    _patch_empty_models(mod, monkeypatch)
    assert env_default_client_config().model_id == "deepseek-v4-pro"


def test_llm_client_with_explicit_model_tolerates_empty_models(monkeypatch):
    import app.agents.llm_client as mod

    _patch_empty_models(mod, monkeypatch)
    client = LLMClient(model="custom-model")
    assert client.model_name == "custom-model"


def test_get_model_uses_explicit_config_kwargs():
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        mock_chat.return_value = SimpleNamespace(__class__=mock_chat)
        client = LLMClient(config=_config())
        client.get_model()
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["api_key"] == "sk-test-key"
    assert kwargs["base_url"] == "https://example.test/v1"
    assert kwargs["temperature"] == 0.7
    assert kwargs["max_tokens"] == 512


def test_model_and_temperature_params_override_config():
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        client = LLMClient(model="custom", temperature=0.1, config=_config())
        client.get_model()
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["model"] == "custom"
    assert kwargs["temperature"] == 0.1
    assert kwargs["max_tokens"] == 512


def test_get_model_with_temperature_overrides_temperature():
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        client = LLMClient(config=_config())
        client.get_model_with_temperature(0.3)
    assert mock_chat.call_args.kwargs["temperature"] == 0.3
    assert mock_chat.call_args.kwargs["base_url"] == "https://example.test/v1"


def test_get_model_with_tools_binds_tools():
    tools = [{"type": "function", "function": {"name": "f"}}]
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        bound = mock_chat.return_value.bind_tools.return_value = SimpleNamespace()
        client = LLMClient(config=_config())
        assert client.get_model_with_tools(tools) is bound
    mock_chat.return_value.bind_tools.assert_called_once_with(tools)


def test_get_model_with_action_tool_uses_strict_base_url():
    contract = ActionContract(
        contract_id="night_check",
        phase=GamePhase.NIGHT,
        action_types=("check", "pass"),
        actions_requiring_target=frozenset({"check"}),
        resolution_priority=1,
        fallback_action_type="pass",
    )
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        client = LLMClient(config=_config())
        client.get_model_with_action_tool(contract)
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["base_url"] == "https://example.test/beta"
    tool = mock_chat.return_value.bind_tools.call_args.args[0][0]
    assert tool["function"]["name"] == "night_check"
    assert mock_chat.return_value.bind_tools.call_args.kwargs["strict"] is True


def test_llm_client_config_is_frozen():
    with pytest.raises(FrozenInstanceError):
        _config().base_url = "x"


def test_derive_strict_base_url_prefers_explicit_value():
    assert derive_strict_base_url(
        "https://api.xiaomimimo.com/v1", "https://custom.test/strict",
    ) == "https://custom.test/strict"


def test_derive_strict_base_url_uses_beta_for_official_deepseek():
    assert derive_strict_base_url("https://api.deepseek.com/v1") == (
        "https://api.deepseek.com/beta"
    )


def test_derive_strict_base_url_reuses_base_url_for_other_providers():
    assert derive_strict_base_url("https://api.xiaomimimo.com/v1") == (
        "https://api.xiaomimimo.com/v1"
    )


def test_derive_strict_base_url_ignores_port_when_matching_deepseek_host():
    assert derive_strict_base_url("https://api.deepseek.com:443/v1") == (
        "https://api.deepseek.com/beta"
    )


def test_derive_strict_base_url_keeps_other_deepseek_like_hosts_on_base_url():
    assert derive_strict_base_url("https://proxy.deepseek.example/v1") == (
        "https://proxy.deepseek.example/v1"
    )
