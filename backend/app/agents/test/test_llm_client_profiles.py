from dataclasses import replace
from unittest.mock import patch

import pytest

from app.agents.llm_client import LLMClient, LLMClientConfig
from app.agents.output_parser import StrictCapabilityError
from app.models.contracts import ActionContract
from app.models.game import GamePhase


@pytest.fixture
def openrouter_config():
    return LLMClientConfig(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model_id="xiaomi/mimo-v2-omni",
        temperature=0.7,
        max_tokens=512,
        strict_base_url="https://unused.test/beta",
        action_max_tokens=2048,
    )


@pytest.fixture
def deepseek_config():
    return LLMClientConfig(
        base_url="https://api.deepseek.com/v1",
        api_key="test-key",
        model_id="deepseek-chat",
        temperature=0.7,
        max_tokens=512,
        strict_base_url="https://api.deepseek.com/beta",
        action_max_tokens=2048,
    )


def _contract() -> ActionContract:
    return ActionContract(
        contract_id="night_check",
        phase=GamePhase.NIGHT,
        action_types=("check", "pass"),
        actions_requiring_target=frozenset({"check"}),
        resolution_priority=1,
        fallback_action_type="pass",
    )


def test_openrouter_skips_strict_action_attempt(openrouter_config):
    client = LLMClient(config=openrouter_config)

    assert client.supports_strict_actions is False


def test_deepseek_keeps_strict_action_attempt(deepseek_config):
    client = LLMClient(config=deepseek_config)

    assert client.supports_strict_actions is True


def test_explicit_profile_overrides_hostname(openrouter_config):
    client = LLMClient(
        config=replace(openrouter_config, provider_profile="custom-openai"),
    )

    assert client.provider_profile.profile_id == "custom-openai"


def test_action_model_uses_full_configured_budget(openrouter_config):
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        LLMClient(config=openrouter_config).get_action_model()

    assert chat.call_args.kwargs["max_tokens"] == 2048


def test_unsupported_profile_rejects_strict_action_before_building_model(
    openrouter_config,
):
    client = LLMClient(config=openrouter_config)

    with patch.object(client._transport, "build") as build:
        with pytest.raises(StrictCapabilityError):
            client.get_model_with_action_tool(_contract())

    build.assert_not_called()


def test_probe_invokes_plain_model_and_returns_capabilities(deepseek_config):
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        result = LLMClient(config=deepseek_config).probe()

    chat.return_value.invoke.assert_called_once()
    assert result == {
        "tools": True,
        "strict_tools": True,
        "json_output": True,
        "reasoning_effort": True,
        "temperature": True,
    }
