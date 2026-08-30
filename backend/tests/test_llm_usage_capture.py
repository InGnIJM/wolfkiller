"""Tests for LLM token-usage and elapsed-time capture on the model gateway."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from httpx import Request, Response
from openai import BadRequestError

from app.agents.llm_client import (
    LLMClient,
    LLMClientConfig,
    LLMUsage,
    extract_usage,
    merge_usage,
)
from app.agents.output_parser import StrictCapabilityError
from app.roles.registry import builtin_registry


def _usage_metadata(prompt: int, completion: int, total: int | None = None) -> dict:
    metadata = {"input_tokens": prompt, "output_tokens": completion}
    if total is not None:
        metadata["total_tokens"] = total
    return metadata


def _token_usage(prompt: int, completion: int, total: int | None = None) -> dict:
    usage = {"prompt_tokens": prompt, "completion_tokens": completion}
    if total is not None:
        usage["total_tokens"] = total
    return usage


class TestUsageHelpers:
    def test_extract_usage_reads_langchain_metadata(self):
        response = SimpleNamespace(
            usage_metadata=_usage_metadata(10, 4, 14),
        )

        assert extract_usage(response) == LLMUsage(10, 4, 14)

    def test_extract_usage_sums_when_total_missing(self):
        response = SimpleNamespace(usage_metadata=_usage_metadata(10, 4))

        assert extract_usage(response) == LLMUsage(10, 4, 14)

    def test_extract_usage_reads_openai_token_usage(self):
        response = SimpleNamespace(
            usage_metadata=None,
            response_metadata={"token_usage": _token_usage(7, 3, 10)},
        )

        assert extract_usage(response) == LLMUsage(7, 3, 10)

    def test_extract_usage_returns_none_when_absent(self):
        assert extract_usage(SimpleNamespace()) is None

    def test_extract_usage_skips_malformed_langchain_metadata(self):
        response = SimpleNamespace(
            usage_metadata={"input_tokens": "many", "output_tokens": 4},
            response_metadata={"token_usage": _token_usage(7, 3, 10)},
        )

        assert extract_usage(response) == LLMUsage(7, 3, 10)

    def test_extract_usage_returns_none_for_malformed_token_usage(self):
        response = SimpleNamespace(
            usage_metadata=None,
            response_metadata={"token_usage": {"prompt_tokens": 1.5, "completion_tokens": 2}},
        )

        assert extract_usage(response) is None

    def test_extract_usage_ignores_non_mapping_metadata(self):
        response = SimpleNamespace(
            usage_metadata="junk", response_metadata="junk",
        )

        assert extract_usage(response) is None

    def test_usage_map_rejects_bool_tokens(self):
        assert extract_usage(
            SimpleNamespace(usage_metadata={"input_tokens": True, "output_tokens": 2}),
        ) is None

    def test_merge_usage_accumulates_both_sides(self):
        first = LLMUsage(10, 4, 14)
        second = LLMUsage(7, 3, 10)

        assert merge_usage(first, second) == LLMUsage(17, 7, 24)
        assert merge_usage(None, second) is second
        assert merge_usage(first, None) is first
        assert merge_usage(None, None) is None


class _ScriptedModel:
    """Fake chat model returning queued responses or raising queued errors."""

    def __init__(self, script: list):
        self._script = list(script)
        self.calls = 0

    def _next(self):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def invoke(self, messages):
        return self._next()

    async def ainvoke(self, messages):
        return self._next()


class _BindableModel:
    """Fake chat model capturing bind_tools calls."""

    def __init__(self):
        self.bound = None

    def bind_tools(self, tools, **kwargs):
        self.bound = (tools, kwargs)
        return "bound-model"


class _ScriptedClient(LLMClient):
    """LLMClient with scripted capability flags; models are injected per test."""

    def __init__(self, *, tools: bool, strict: bool):
        self._config = LLMClientConfig(
            base_url="https://provider.example", api_key="key",
            model_id="scripted-model", temperature=0.1, max_tokens=64,
            strict_base_url="https://provider.example",
        )
        self.provider_profile = SimpleNamespace(
            profile_id="scripted",
            capabilities=SimpleNamespace(strict_tools=strict, tools=tools),
        )
        self.model_name = "scripted-model"


def _tool_response(name: str, args, usage: dict | None = None):
    return SimpleNamespace(
        tool_calls=[{"name": name, "args": args}],
        content="",
        response_metadata={"finish_reason": "tool_calls"},
        usage_metadata=usage,
    )


def _json_response(content: str, usage: dict | None = None):
    return SimpleNamespace(
        tool_calls=[],
        content=content,
        response_metadata={"finish_reason": "stop"},
        usage_metadata=usage,
    )


def _provider_error(status_code: int) -> BadRequestError:
    request = Request("POST", "https://provider.example/chat/completions")
    body = {"error": {"message": "rejected", "code": "invalid_request"}}
    return BadRequestError("rejected", response=Response(status_code, request=request, json=body), body=body)


def _contract():
    return builtin_registry.require("wolf-killer-werewolf").contracts[0]


class TestInvokeActionCapture:
    def test_captures_usage_and_reports_strict_transport(self):
        client = _ScriptedClient(tools=True, strict=True)
        tool_model = _ScriptedModel([
            _tool_response(
                _contract().resolved_tool_name, {"action_type": "pass"},
                usage=_usage_metadata(12, 5, 17),
            ),
        ])
        client.get_model_with_action_tool = lambda contract: tool_model

        result = client.invoke_action([{"role": "user"}], _contract())

        assert result.transport == "strict_tool"
        assert result.usage == LLMUsage(12, 5, 17)
        assert result.llm_attempts == 1
        assert result.elapsed_ms >= 0

    def test_falls_back_to_json_and_merges_usage_across_attempts(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([_provider_error(400)])
        json_model = _ScriptedModel([
            _json_response('{"action_type": "pass"}', usage=_usage_metadata(30, 8, 38)),
        ])
        client.get_model_with_action_tool = lambda contract: tool_model
        client.get_action_model = lambda: json_model

        result = client.invoke_action([{"role": "user"}], _contract())

        assert result.transport == "json_fallback"
        assert result.usage == LLMUsage(30, 8, 38)
        assert result.llm_attempts == 2

    def test_without_tools_uses_single_json_attempt(self):
        client = _ScriptedClient(tools=False, strict=False)
        json_model = _ScriptedModel([
            _json_response(
                '{"action_type": "pass"}',
                usage=_token_usage(9, 2, 11),
            ),
        ])
        client.get_action_model = lambda: json_model

        result = client.invoke_action([{"role": "user"}], _contract())

        assert result.usage == LLMUsage(9, 2, 11)
        assert result.llm_attempts == 1

    def test_retries_when_response_has_multiple_tool_calls(self):
        client = _ScriptedClient(tools=True, strict=False)
        double = SimpleNamespace(
            tool_calls=[
                {"name": "one", "args": {}},
                {"name": "two", "args": {}},
            ],
            content="", response_metadata={}, usage_metadata=None,
        )
        tool_model = _ScriptedModel([double])
        json_model = _ScriptedModel([_json_response('{"action_type": "pass"}')])
        client.get_model_with_action_tool = lambda contract: tool_model
        client.get_action_model = lambda: json_model

        result = client.invoke_action([{"role": "user"}], _contract())

        assert json_model.calls == 1
        assert result.llm_attempts == 2

    def test_retries_when_response_uses_unexpected_tool(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([_tool_response("other_tool", {})])
        json_model = _ScriptedModel([_json_response('{"action_type": "pass"}')])
        client.get_model_with_action_tool = lambda contract: tool_model
        client.get_action_model = lambda: json_model

        result = client.invoke_action([{"role": "user"}], _contract())

        assert result.llm_attempts == 2

    def test_parses_string_encoded_tool_args(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([
            _tool_response(
                _contract().resolved_tool_name,
                '{"action_type": "pass"}',
                usage=_usage_metadata(5, 5, 10),
            ),
        ])
        client.get_model_with_action_tool = lambda contract: tool_model

        result = client.invoke_action([{"role": "user"}], _contract())

        assert result.payload == {"action_type": "pass"}
        assert result.llm_attempts == 1

    def test_retries_when_tool_args_are_not_a_mapping(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([_tool_response(_contract().resolved_tool_name, 42)])
        json_model = _ScriptedModel([_json_response('{"action_type": "pass"}')])
        client.get_model_with_action_tool = lambda contract: tool_model
        client.get_action_model = lambda: json_model

        assert client.invoke_action([{"role": "user"}], _contract()).llm_attempts == 2

    def test_raises_when_final_response_has_neither_tool_nor_json(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([_provider_error(400)])
        json_model = _ScriptedModel([_json_response("plain prose, no json")])
        client.get_model_with_action_tool = lambda contract: tool_model
        client.get_action_model = lambda: json_model

        with pytest.raises(ValueError, match="neither a tool call nor JSON"):
            client.invoke_action([{"role": "user"}], _contract())

    def test_requires_tools_for_action_contract(self):
        client = _ScriptedClient(tools=False, strict=False)

        with pytest.raises(StrictCapabilityError):
            client.get_model_with_action_tool(_contract())

    def test_binds_action_tool_without_strict_mode(self):
        client = _ScriptedClient(tools=True, strict=False)
        bindable = _BindableModel()
        client._build = lambda purpose, config=None: bindable

        bound = client.get_model_with_action_tool(_contract())

        assert bound == "bound-model"
        tools, kwargs = bindable.bound
        assert tools[0]["function"]["name"] == _contract().resolved_tool_name
        assert kwargs == {"tool_choice": _contract().resolved_tool_name}


class TestInvokeJsonCapture:
    def test_tool_success_returns_usage(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([
            _tool_response("submit", {"text": "今晚三号很可疑"}, _usage_metadata(21, 6, 27)),
        ])
        client.get_model_with_json_tool = lambda tool_name, schema: tool_model

        result = client.invoke_json(
            [{"role": "user"}], tool_name="submit",
            schema={"required": ["text"]},
        )

        assert result.payload == {"text": "今晚三号很可疑"}
        assert result.usage == LLMUsage(21, 6, 27)
        assert result.llm_attempts == 1

    def test_schema_violation_falls_back_and_merges_usage(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([_tool_response("submit", {})])
        json_model = _ScriptedModel([
            _json_response('{"text": "白天我们需要集中票型"}', _usage_metadata(40, 9, 49)),
        ])
        client.get_model_with_json_tool = lambda tool_name, schema: tool_model
        client.get_action_model = lambda: json_model

        result = client.invoke_json(
            [{"role": "user"}], tool_name="submit",
            schema={"required": ["text"]},
        )

        assert result.payload["text"].startswith("白天")
        assert result.usage == LLMUsage(40, 9, 49)
        assert result.llm_attempts == 2

    def test_without_tools_uses_json_model(self):
        client = _ScriptedClient(tools=False, strict=False)
        json_model = _ScriptedModel([
            _json_response('{"text": "我同意出四号"}', usage=_token_usage(15, 4)),
        ])
        client.get_action_model = lambda: json_model

        result = client.invoke_json(
            [{"role": "user"}], tool_name="submit",
            schema={"required": ["text"]},
        )

        assert result.usage == LLMUsage(15, 4, 19)
        assert result.llm_attempts == 1

    def test_rejects_schema_with_non_string_required_fields(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([_provider_error(400)])
        json_model = _ScriptedModel([_json_response('{"text": "ok"}')])
        client.get_model_with_json_tool = lambda tool_name, schema: tool_model
        client.get_action_model = lambda: json_model

        with pytest.raises(ValueError, match="required fields must be strings"):
            client.invoke_json(
                [{"role": "user"}], tool_name="submit", schema={"required": "text"},
            )

    def test_requires_tools_for_json_tool(self):
        client = _ScriptedClient(tools=False, strict=False)

        with pytest.raises(StrictCapabilityError):
            client.get_model_with_json_tool("submit", {})

    def test_binds_json_tool_with_choice(self):
        client = _ScriptedClient(tools=True, strict=False)
        bindable = _BindableModel()
        client._build = lambda purpose, config=None: bindable

        bound = client.get_model_with_json_tool(
            "submit", {"type": "object"},
        )

        assert bound == "bound-model"
        tools, kwargs = bindable.bound
        assert tools[0]["function"]["name"] == "submit"
        assert kwargs == {"tool_choice": "submit"}


class TestAsyncInvokeJsonCapture:
    @pytest.mark.asyncio
    async def test_tool_success_returns_usage(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([
            _tool_response("submit", {"text": "夜里守卫守自己"}, _usage_metadata(18, 6, 24)),
        ])
        client.get_model_with_json_tool = lambda tool_name, schema: tool_model

        result = await client.ainvoke_json(
            [{"role": "user"}], tool_name="submit",
            schema={"required": ["text"]},
        )

        assert result.usage == LLMUsage(18, 6, 24)
        assert result.llm_attempts == 1

    @pytest.mark.asyncio
    async def test_fallback_merges_usage_across_attempts(self):
        client = _ScriptedClient(tools=True, strict=False)
        tool_model = _ScriptedModel([_provider_error(422)])
        json_model = _ScriptedModel([
            _json_response('{"text": "先听后置位发言"}', _usage_metadata(33, 7, 40)),
        ])
        client.get_model_with_json_tool = lambda tool_name, schema: tool_model
        client.get_action_model = lambda: json_model

        result = await client.ainvoke_json(
            [{"role": "user"}], tool_name="submit",
            schema={"required": ["text"]},
        )

        assert result.usage == LLMUsage(33, 7, 40)
        assert result.llm_attempts == 2

    @pytest.mark.asyncio
    async def test_without_tools_uses_json_model(self):
        client = _ScriptedClient(tools=False, strict=False)
        json_model = _ScriptedModel([
            _json_response('{"text": "平安夜信息很少"}'),
        ])
        client.get_action_model = lambda: json_model

        result = await client.ainvoke_json(
            [{"role": "user"}], tool_name="submit",
            schema={"required": ["text"]},
        )

        assert result.llm_attempts == 1
        assert result.usage is None


class TestProbe:
    @patch("app.agents.llm_client.ChatOpenAI", new=MagicMock)
    def test_probe_returns_capability_flags(self):
        client = LLMClient(model="deepseek-chat")

        capabilities = client.probe()

        for flag in (
            "tools", "strict_tools", "json_output", "reasoning_effort",
            "temperature",
        ):
            assert capabilities[flag] is True
