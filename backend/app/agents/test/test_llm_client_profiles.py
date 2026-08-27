from dataclasses import replace
from unittest.mock import patch

import pytest
from httpx import Request, Response
from langchain_core.messages import AIMessage
from openai import BadRequestError

from app.agents.llm_client import LLMClient, LLMClientConfig
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


def test_openrouter_binds_action_contract_as_non_strict_tool(openrouter_config):
    client = LLMClient(config=openrouter_config)

    with patch.object(client._transport, "build") as build:
        model = build.return_value
        result = client.get_model_with_action_tool(_contract())

    assert result is model.bind_tools.return_value
    model.bind_tools.assert_called_once_with(
        [
            {
                "type": "function",
                "function": {
                    "name": "night_check",
                    "description": "Submit the issued game action.",
                    "parameters": _contract().json_schema(),
                },
            }
        ],
        tool_choice="night_check",
    )


def test_invoke_action_returns_native_tool_payload(openrouter_config):
    class Model:
        def invoke(self, messages):
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "night_check",
                    "args": {
                        "action_type": "check",
                        "target_seat": 2,
                        "reasoning": "x",
                    },
                    "id": "call_1",
                }],
            )

    client = LLMClient(config=openrouter_config)
    with patch.object(client, "get_model_with_action_tool", return_value=Model()):
        result = client.invoke_action([], _contract())

    assert result.payload == {
        "action_type": "check",
        "target_seat": 2,
        "reasoning": "x",
    }
    assert result.transport == "tool"
    assert result.has_tool_calls is True


def test_invoke_action_accepts_json_content_when_tool_call_is_missing(
    openrouter_config,
):
    class Model:
        def invoke(self, messages):
            return AIMessage(
                content='{"action_type":"check","target_seat":2,"reasoning":"x"}',
            )

    client = LLMClient(config=openrouter_config)
    with patch.object(client, "get_model_with_action_tool", return_value=Model()):
        result = client.invoke_action([], _contract())

    assert result.payload["target_seat"] == 2
    assert result.transport == "json_fallback"
    assert result.has_tool_calls is False


def test_invoke_json_uses_generic_non_strict_tool(openrouter_config):
    schema = {
        "type": "object",
        "properties": {"speak": {"type": "boolean"}},
        "required": ["speak"],
    }

    class Model:
        def invoke(self, messages):
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "submit_night_json",
                    "args": {"speak": False},
                    "id": "call_1",
                }],
            )

    client = LLMClient(config=openrouter_config)
    with patch.object(client, "get_model_with_json_tool", return_value=Model()):
        result = client.invoke_json(
            [], tool_name="submit_night_json", schema=schema,
        )

    assert result.payload == {"speak": False}
    assert result.transport == "tool"


def test_invoke_json_retries_when_tool_payload_omits_required_fields(
    openrouter_config,
):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action_type": {"type": "string"},
            "target_seat": {"type": ["integer", "null"]},
            "reasoning": {"type": "string"},
        },
        "required": ["action_type", "target_seat", "reasoning"],
    }

    class ToolModel:
        def invoke(self, messages):
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "werewolf_kill",
                    "args": {},
                    "id": "call_1",
                }],
            )

    class JsonModel:
        def invoke(self, messages):
            return AIMessage(content=(
                '{"action_type":"kill","target_seat":2,'
                '"reasoning":"统一刀口"}'
            ))

    client = LLMClient(config=openrouter_config)
    with (
        patch.object(client, "get_model_with_json_tool", return_value=ToolModel()),
        patch.object(client, "get_action_model", return_value=JsonModel()) as fallback,
    ):
        result = client.invoke_json(
            [], tool_name="werewolf_kill", schema=schema,
        )

    assert result.payload["target_seat"] == 2
    assert result.transport == "json_fallback"
    fallback.assert_called_once()


def test_invoke_action_retries_as_json_when_provider_rejects_tools(
    openrouter_config,
):
    request = Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    error = BadRequestError(
        "tool_choice is unsupported",
        response=Response(400, request=request),
        body={"error": {"message": "tool_choice is unsupported"}},
    )

    class ToolModel:
        def invoke(self, messages):
            raise error

    class JsonModel:
        def invoke(self, messages):
            return AIMessage(
                content='{"action_type":"check","target_seat":2,"reasoning":"x"}',
            )

    client = LLMClient(config=openrouter_config)
    with (
        patch.object(client, "get_model_with_action_tool", return_value=ToolModel()),
        patch.object(client, "get_action_model", return_value=JsonModel()) as fallback,
    ):
        result = client.invoke_action([], _contract())

    assert result.payload["action_type"] == "check"
    assert result.transport == "json_fallback"
    fallback.assert_called_once()


@pytest.mark.asyncio
async def test_ainvoke_json_retries_as_json_when_provider_rejects_tools(
    openrouter_config,
):
    request = Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    error = BadRequestError(
        "provider returned error",
        response=Response(400, request=request),
        body={"error": {"message": "Provider returned error"}},
    )

    class ToolModel:
        async def ainvoke(self, messages):
            raise error

    class JsonModel:
        async def ainvoke(self, messages):
            return AIMessage(content='{"text":"这是降级后的有效公开发言内容。"}')

    client = LLMClient(config=openrouter_config)
    with (
        patch.object(client, "get_model_with_json_tool", return_value=ToolModel()),
        patch.object(client, "get_action_model", return_value=JsonModel()) as fallback,
    ):
        result = await client.ainvoke_json(
            [],
            tool_name="speak",
            schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )

    assert result.payload == {"text": "这是降级后的有效公开发言内容。"}
    assert result.transport == "json_fallback"
    fallback.assert_called_once()


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
