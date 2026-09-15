"""LLMClient behaviour when the resolved profile speaks the Anthropic Messages API."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import anthropic
import httpx
import pytest
from langchain_core.messages import AIMessage

from app.agents.llm_client import LLMClient, LLMClientConfig, _content_text
from app.agents.output_parser import StrictCapabilityError
from app.agents.providers.anthropic_messages import AnthropicMessagesTransport
from app.agents.providers.base import CallPurpose
from app.models.contracts import ActionContract
from app.models.game import GamePhase


@pytest.fixture
def anthropic_config():
    return LLMClientConfig(
        base_url="https://api.anthropic.com",
        api_key="test-key",
        model_id="claude-sonnet-4-5",
        temperature=1.2,
        max_tokens=512,
        strict_base_url="https://api.anthropic.com",
        action_max_tokens=2048,
    )


@pytest.fixture
def relay_config(anthropic_config):
    return replace(
        anthropic_config,
        base_url="https://relay.example/v1",
        provider_profile="custom-anthropic",
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


def _bad_request(status=400):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        "tool_choice rejected",
        response=httpx.Response(status, request=request),
        body={"error": {"message": "tool_choice rejected"}},
    )


def test_official_hostname_selects_anthropic_transport(anthropic_config):
    client = LLMClient(config=anthropic_config)

    assert client.provider_profile.profile_id == "anthropic"
    assert isinstance(client._transport, AnthropicMessagesTransport)
    assert client.supports_action_tools is True
    assert client.supports_strict_actions is False


def test_explicit_relay_profile_selects_anthropic_transport(relay_config):
    client = LLMClient(config=relay_config)

    assert client.provider_profile.profile_id == "custom-anthropic"
    assert isinstance(client._transport, AnthropicMessagesTransport)


def test_get_model_builds_chat_anthropic_with_clamped_temperature(anthropic_config):
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        LLMClient(config=anthropic_config).get_model()

    kwargs = chat.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-5"
    assert kwargs["base_url"] == "https://api.anthropic.com"
    assert kwargs["temperature"] == 1.0
    assert kwargs["max_tokens"] == 512


def test_action_model_uses_action_budget_and_action_temperature(anthropic_config):
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        LLMClient(config=anthropic_config).get_action_model()

    kwargs = chat.call_args.kwargs
    assert kwargs["max_tokens"] == 2048
    assert kwargs["temperature"] == 0.1
    assert kwargs["timeout"] == 125.0


def test_patched_chat_openai_is_not_used_for_anthropic_transport(anthropic_config):
    """The ChatOpenAI test seam must not leak into the Messages transport."""
    with (
        patch("app.agents.llm_client.ChatOpenAI") as chat_openai,
        patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat_anthropic,
    ):
        LLMClient(config=anthropic_config).get_model()

    chat_openai.assert_not_called()
    chat_anthropic.assert_called_once()


def test_action_contract_binds_openai_format_tool_with_forced_choice(anthropic_config):
    client = LLMClient(config=anthropic_config)

    with patch.object(client._transport, "build") as build:
        model = build.return_value
        result = client.get_model_with_action_tool(_contract())

    assert build.call_args.args[2] is CallPurpose.TOOLS
    assert result is model.bind_tools.return_value
    tool = model.bind_tools.call_args.args[0][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "night_check"
    assert "strict" not in tool["function"]
    # Thinking mode rejects a named tool; Anthropic still forces a tool via "any".
    assert model.bind_tools.call_args.kwargs == {"tool_choice": "any"}


def test_real_chat_anthropic_converts_openai_tool_format(anthropic_config):
    bound = LLMClient(config=anthropic_config).get_model_with_action_tool(_contract())

    assert bound.kwargs["tools"][0]["name"] == "night_check"
    assert bound.kwargs["tools"][0]["input_schema"] == _contract().json_schema()
    assert bound.kwargs["tool_choice"] == {"type": "any"}


def test_json_tool_also_forces_any_instead_of_a_named_tool(anthropic_config):
    client = LLMClient(config=anthropic_config)

    with patch.object(client._transport, "build") as build:
        model = build.return_value
        client.get_model_with_json_tool(
            "speak_json", {"type": "object", "properties": {"text": {"type": "string"}}},
        )

    assert model.bind_tools.call_args.kwargs == {"tool_choice": "any"}


def test_invoke_action_reads_tool_use_payload_from_block_content(anthropic_config):
    class Model:
        def invoke(self, messages):
            return AIMessage(
                content=[
                    {"type": "text", "text": "I will check seat 2."},
                    {"type": "tool_use", "id": "toolu_1", "name": "night_check",
                     "input": {"action_type": "check", "target_seat": 2, "reasoning": "x"}},
                ],
                tool_calls=[{
                    "name": "night_check",
                    "args": {"action_type": "check", "target_seat": 2, "reasoning": "x"},
                    "id": "toolu_1",
                }],
                response_metadata={"stop_reason": "tool_use"},
            )

    client = LLMClient(config=anthropic_config)
    with patch.object(client, "get_model_with_action_tool", return_value=Model()):
        result = client.invoke_action([], _contract())

    assert result.payload["target_seat"] == 2
    assert result.transport == "tool"
    assert result.finish_reason == "tool_use"
    assert result.content_length == len("I will check seat 2.")


def test_invoke_action_parses_json_from_text_blocks_without_tool_call(anthropic_config):
    class Model:
        def invoke(self, messages):
            return AIMessage(
                content=[
                    {"type": "text", "text": '{"action_type":"check",'},
                    {"type": "text", "text": '"target_seat":3,"reasoning":"x"}'},
                ],
                response_metadata={"stop_reason": "end_turn"},
            )

    client = LLMClient(config=anthropic_config)
    with patch.object(client, "get_model_with_action_tool", return_value=Model()):
        result = client.invoke_action([], _contract())

    assert result.payload["target_seat"] == 3
    assert result.transport == "json_fallback"
    assert result.finish_reason == "end_turn"


def test_invoke_action_falls_back_to_json_when_anthropic_rejects_tools(anthropic_config):
    class ToolModel:
        def invoke(self, messages):
            raise _bad_request()

    class JsonModel:
        def invoke(self, messages):
            return AIMessage(
                content='{"action_type":"check","target_seat":2,"reasoning":"x"}',
            )

    client = LLMClient(config=anthropic_config)
    with (
        patch.object(client, "get_model_with_action_tool", return_value=ToolModel()),
        patch.object(client, "get_action_model", return_value=JsonModel()) as fallback,
    ):
        result = client.invoke_action([], _contract())

    assert result.transport == "json_fallback"
    assert result.llm_attempts == 2
    fallback.assert_called_once()


def test_invoke_json_falls_back_when_anthropic_rejects_tools(anthropic_config):
    class ToolModel:
        def invoke(self, messages):
            raise _bad_request(422)

    class JsonModel:
        def invoke(self, messages):
            return AIMessage(content='{"speak": true}')

    client = LLMClient(config=anthropic_config)
    with (
        patch.object(client, "get_model_with_json_tool", return_value=ToolModel()),
        patch.object(client, "get_action_model", return_value=JsonModel()),
    ):
        result = client.invoke_json(
            [], tool_name="speak_json",
            schema={"type": "object", "properties": {"speak": {"type": "boolean"}}},
        )

    assert result.payload == {"speak": True}
    assert result.transport == "json_fallback"


@pytest.mark.asyncio
async def test_ainvoke_json_falls_back_when_anthropic_rejects_tools(anthropic_config):
    class ToolModel:
        async def ainvoke(self, messages):
            raise _bad_request()

    class JsonModel:
        async def ainvoke(self, messages):
            return AIMessage(content='{"text":"这是降级后的有效公开发言内容。"}')

    client = LLMClient(config=anthropic_config)
    with (
        patch.object(client, "get_model_with_json_tool", return_value=ToolModel()),
        patch.object(client, "get_action_model", return_value=JsonModel()),
    ):
        result = await client.ainvoke_json(
            [], tool_name="speak",
            schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )

    assert result.transport == "json_fallback"


def test_anthropic_bad_request_maps_to_strict_capability_error():
    mapped = LLMClient.map_strict_capability_error(_bad_request())

    assert isinstance(mapped, StrictCapabilityError)


def test_wrapped_anthropic_bad_request_still_maps_to_capability_error():
    wrapped = ValueError("model invocation failed")
    wrapped.__cause__ = _bad_request()

    mapped = LLMClient.map_strict_capability_error(wrapped)

    assert isinstance(mapped, StrictCapabilityError)


def test_wrapped_anthropic_422_still_maps_to_capability_error():
    wrapped = ValueError("model invocation failed")
    wrapped.__context__ = _bad_request(422)

    mapped = LLMClient.map_strict_capability_error(wrapped)

    assert isinstance(mapped, StrictCapabilityError)


def test_anthropic_rate_limit_is_not_a_capability_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None,
    )

    assert LLMClient.map_strict_capability_error(error) is error


def test_anthropic_unauthorized_bad_request_is_not_a_capability_error():
    error = _bad_request(401)

    assert LLMClient.map_strict_capability_error(error) is error


def test_maps_bad_request_when_status_code_lives_only_on_response():
    error = _bad_request()
    error.status_code = None

    mapped = LLMClient.map_strict_capability_error(error)

    assert isinstance(mapped, StrictCapabilityError)


def test_capability_mapping_stops_on_a_cause_cycle():
    error = ValueError("loop")
    error.__cause__ = error

    assert LLMClient.map_strict_capability_error(error) is error
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    inner = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None,
    )
    wrapped = ValueError("model invocation failed")
    wrapped.__cause__ = inner

    assert LLMClient.map_strict_capability_error(wrapped) is wrapped


def test_probe_returns_anthropic_capabilities(anthropic_config):
    with patch("app.agents.providers.anthropic_messages.ChatAnthropic") as chat:
        result = LLMClient(config=anthropic_config).probe()

    chat.return_value.invoke.assert_called_once()
    assert result == {
        "tools": True,
        "strict_tools": False,
        "json_output": True,
        "reasoning_effort": False,
        "temperature": True,
        "forced_tool_choice": True,
    }


@pytest.mark.asyncio
async def test_aclose_closes_anthropic_sdk_clients(anthropic_config):
    client = LLMClient(config=anthropic_config)
    async_close = AsyncMock()
    sync_close = Mock()
    client._built_models[(CallPurpose.TEXT, client._config)] = SimpleNamespace(
        _async_client=SimpleNamespace(close=async_close),
        _client=SimpleNamespace(close=sync_close),
    )

    await client.aclose()

    async_close.assert_awaited_once()
    sync_close.assert_called_once()
    assert client._built_models == {}


@pytest.mark.asyncio
async def test_aclose_closes_real_chat_anthropic_clients(anthropic_config):
    client = LLMClient(config=anthropic_config)
    model = client.get_model()
    with (
        patch.object(model._async_client, "close", new=AsyncMock()) as async_close,
        patch.object(model._client, "close") as sync_close,
    ):
        await client.aclose()

    async_close.assert_awaited_once()
    sync_close.assert_called_once()


@pytest.mark.asyncio
async def test_aclose_does_not_poison_the_next_anthropic_http_client(relay_config):
    """LangChain caches one httpx client per (base_url, timeout). Closing it
    during aclose() must not hand the next LLMClient a dead connection pool —
    that surfaces as AnthropicConnectionError on every subsequent DeepSeek
    Anthropic call until the process restarts.
    """
    from langchain_anthropic._client_utils import (
        _get_default_async_httpx_client, _get_default_httpx_client,
    )

    _get_default_httpx_client.cache_clear()
    _get_default_async_httpx_client.cache_clear()
    first = LLMClient(config=relay_config)
    first.get_model()
    first.get_action_model()
    await first.aclose()

    second = LLMClient(config=relay_config)
    text_http = second.get_model()._client._client
    action_http = second.get_action_model()._client._client
    try:
        assert text_http.is_closed is False
        assert action_http.is_closed is False
    finally:
        await second.aclose()
        _get_default_httpx_client.cache_clear()
        _get_default_async_httpx_client.cache_clear()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("plain", "plain"),
        ([], ""),
        (["a", "b"], "ab"),
        ([{"type": "text", "text": "a"}, {"type": "tool_use", "name": "t"}], "a"),
        ([{"type": "text", "text": "a"}, "b", {"type": "text"}], "ab"),
        ([{"type": "text", "text": 1}], ""),
        ([{"type": "thinking", "thinking": "chain"}, {"type": "text", "text": '{"ok":true}'}],
         'chain{"ok":true}'),
        ([{"type": "reasoning", "reasoning": '{"ok":1}'}], '{"ok":1}'),
        ([{"type": "reasoning", "text": '{"ok":2}'}], '{"ok":2}'),
        ([{"type": "thinking", "thinking": 1}], ""),
        ([None, {"type": "unknown"}], ""),
        (None, ""),
        ({"type": "text", "text": "not-a-list"}, ""),
    ],
)
def test_content_text_flattens_text_blocks(content, expected):
    assert _content_text(content) == expected


def test_invoke_action_parses_json_hidden_in_thinking_blocks(anthropic_config):
    class Model:
        def invoke(self, messages):
            return AIMessage(
                content=[
                    {"type": "thinking", "thinking": "I will check seat 4."},
                    {"type": "text", "text": '{"action_type":"check","target_seat":4,"reasoning":"x"}'},
                ],
                response_metadata={"stop_reason": "end_turn"},
            )

    client = LLMClient(config=anthropic_config)
    with patch.object(client, "get_model_with_action_tool", return_value=Model()):
        result = client.invoke_action([], _contract())

    assert result.payload["target_seat"] == 4
    assert result.transport == "json_fallback"
