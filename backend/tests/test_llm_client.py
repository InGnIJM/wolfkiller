import runpy

import pytest
from unittest.mock import patch
from httpx import Request, Response
from openai import (
    APIConnectionError,
    BadRequestError,
    RateLimitError,
    UnprocessableEntityError,
)
import app.config as config_module
from app.agents.llm_client import LLMClient
from app.agents.output_parser import StrictCapabilityError
from app.config import LLMConfig
from app.roles.registry import builtin_registry


class TestLLMClient:
    @staticmethod
    def _provider_error(error_type, message, status_code, body):
        request = Request("POST", "https://provider.example/chat/completions")
        response = Response(status_code, request=request, json=body)
        return error_type(message, response=response, body=body)

    def test_maps_provider_strict_schema_rejection_to_capability_error(self):
        body = {
            "error": {
                "message": "This model does not support strict schema mode",
                "code": "unsupported_parameter",
                "param": "strict",
            }
        }
        error = self._provider_error(
            BadRequestError, body["error"]["message"], 400, body
        )

        mapped = LLMClient.map_strict_capability_error(error)

        assert isinstance(mapped, StrictCapabilityError)
        assert str(mapped) == body["error"]["message"]

    def test_maps_422_strict_schema_rejection_to_capability_error(self):
        body = {
            "error": {
                "message": "This model does not support strict schema mode",
                "code": "unsupported_parameter",
                "param": "strict",
            }
        }
        error = self._provider_error(
            UnprocessableEntityError, body["error"]["message"], 422, body
        )

        mapped = LLMClient.map_strict_capability_error(error)

        assert isinstance(mapped, StrictCapabilityError)
        assert str(mapped) == body["error"]["message"]

    def test_maps_tool_choice_rejection_to_capability_error(self):
        body = {
            "error": {
                "message": "Thinking mode does not support this tool_choice",
                "code": "invalid_request_error",
                "param": None,
            }
        }
        error = self._provider_error(
            BadRequestError, body["error"]["message"], 400, body
        )

        mapped = LLMClient.map_strict_capability_error(error)

        assert isinstance(mapped, StrictCapabilityError)
        assert str(mapped) == body["error"]["message"]

    def test_maps_opaque_422_from_strict_endpoint_to_fallback_signal(self):
        body = {
            "error": {
                "message": "The requested game state is invalid",
                "code": "invalid_request",
            }
        }
        error = self._provider_error(
            UnprocessableEntityError, body["error"]["message"], 422, body
        )

        assert isinstance(
            LLMClient.map_strict_capability_error(error), StrictCapabilityError,
        )

    def test_maps_opaque_400_body_from_strict_endpoint_to_fallback_signal(self):
        error = self._provider_error(
            BadRequestError, "Invalid request", 400,
            {"error": "Invalid request"},
        )

        assert isinstance(
            LLMClient.map_strict_capability_error(error), StrictCapabilityError,
        )

    def test_keeps_existing_strict_capability_error_unchanged(self):
        error = StrictCapabilityError("strict schema is unsupported")

        assert LLMClient.map_strict_capability_error(error) is error

    @pytest.mark.parametrize(
        "error_factory",
        [
            lambda self: self._provider_error(
                BadRequestError, "Invalid API key", 401,
                {"error": {"message": "Invalid API key", "code": "invalid_api_key"}},
            ),
            lambda self: self._provider_error(
                RateLimitError, "Too many requests", 429,
                {"error": {"message": "Too many requests", "code": "rate_limit"}},
            ),
            lambda self: APIConnectionError(
                message="Connection error.",
                request=Request("POST", "https://provider.example/chat/completions"),
            ),
        ],
    )
    def test_keeps_non_capability_provider_errors_unchanged(self, error_factory):
        error = error_factory(self)

        assert LLMClient.map_strict_capability_error(error) is error

    def test_init_defaults_from_config(self):
        client = LLMClient()
        assert client.model_name is not None
        assert client.temperature is not None

    def test_init_custom_model(self):
        client = LLMClient(model="custom-model", temperature=0.5)
        assert client.model_name == "custom-model"
        assert client.temperature == 0.5

    @patch("app.agents.llm_client.ChatOpenAI")
    def test_get_model(self, mock_chat):
        client = LLMClient(model="deepseek-chat", temperature=0.7)
        model = client.get_model()
        mock_chat.assert_called_once()
        assert mock_chat.call_args.kwargs["base_url"] == config_module.config.llm.base_url
        assert model is not None

    @patch("app.agents.llm_client.ChatOpenAI")
    def test_get_model_with_temperature(self, mock_chat):
        client = LLMClient(model="deepseek-chat")
        model = client.get_model_with_temperature(0.2)
        mock_chat.assert_called_once()
        assert model is not None

    @patch("app.agents.llm_client.ChatOpenAI")
    def test_get_model_with_tools(self, mock_chat):
        tools = [{"type": "function", "function": {"name": "speak"}}]

        model = LLMClient(model="deepseek-chat").get_model_with_tools(tools)

        assert model is mock_chat.return_value.bind_tools.return_value
        mock_chat.return_value.bind_tools.assert_called_once_with(tools)

    def test_llm_config_selects_models_from_environment(self, monkeypatch):
        monkeypatch.setenv("LLM_MODELS", "alpha, beta")
        assert LLMConfig().models == ["alpha"]

        monkeypatch.setenv("LLM_MODELS", "")
        monkeypatch.setenv("LLM_MODEL", "single")
        assert LLMConfig().models == ["single"]

        monkeypatch.delenv("LLM_MODEL")
        assert LLMConfig().models == ["deepseek-v4-pro"]

    def test_llm_config_loads_strict_base_url_from_environment(self, monkeypatch):
        with monkeypatch.context() as environment:
            environment.setenv("DEEPSEEK_STRICT_BASE_URL", "https://strict.example")
            isolated_config = runpy.run_path(config_module.__file__)
            assert isolated_config["config"].llm.strict_base_url == "https://strict.example"

    @patch("app.agents.llm_client.ChatOpenAI")
    def test_get_model_with_action_tool_binds_contract_schema_strictly(self, mock_chat):
        contract = builtin_registry.require("wolf-killer-werewolf").contracts[0]
        bound_model = mock_chat.return_value.bind_tools.return_value

        model = LLMClient(model="deepseek-chat", temperature=0.7).get_model_with_action_tool(contract)

        assert model is bound_model
        assert mock_chat.call_args.kwargs["base_url"] == "https://api.deepseek.com/beta"
        mock_chat.return_value.bind_tools.assert_called_once_with(
            [
                {
                    "type": "function",
                    "function": {
                        "name": contract.contract_id,
                        "description": "Submit the issued game action.",
                        "parameters": contract.json_schema(),
                        "strict": True,
                    },
                }
            ],
            tool_choice=contract.contract_id,
            strict=True,
        )

    @staticmethod
    def _custom_openai_client() -> LLMClient:
        from app.agents.llm_client import LLMClientConfig

        return LLMClient(config=LLMClientConfig(
            base_url="https://gateway.example/v1", api_key="key", model_id="m",
            temperature=0.7, max_tokens=512,
            strict_base_url="https://gateway.example/v1",
        ))

    @patch("app.agents.llm_client.ChatOpenAI")
    def test_custom_openai_binds_action_tool_without_forced_choice(self, mock_chat):
        """Gateways that reject named tool_choice still get tool transport."""
        contract = builtin_registry.require("wolf-killer-werewolf").contracts[0]

        self._custom_openai_client().get_model_with_action_tool(contract)

        mock_chat.return_value.bind_tools.assert_called_once_with(
            [
                {
                    "type": "function",
                    "function": {
                        "name": contract.contract_id,
                        "description": "Submit the issued game action.",
                        "parameters": contract.json_schema(),
                    },
                }
            ]
        )

    @patch("app.agents.llm_client.ChatOpenAI")
    def test_custom_openai_binds_json_tool_without_forced_choice(self, mock_chat):
        schema = {
            "type": "object", "required": ["x"],
            "properties": {"x": {"type": "integer"}},
        }

        self._custom_openai_client().get_model_with_json_tool("submit", schema)

        mock_chat.return_value.bind_tools.assert_called_once()
        assert mock_chat.return_value.bind_tools.call_args.kwargs == {}

    @staticmethod
    def _xml_response(content: str):
        from types import SimpleNamespace

        return SimpleNamespace(
            tool_calls=None, content=content, response_metadata={},
        )

    def test_structured_response_parses_pseudo_xml_tool_fallback(self):
        response = self._xml_response(
            '<tool_call>\n<function=cast_vote>\n'
            '<parameter=action_type>vote</parameter>\n'
            '<parameter=target_seat>4</parameter>\n'
            '<parameter=reasoning>4号发言矛盾</parameter>\n'
            '</function>\n</tool_call>'
        )

        result = LLMClient._structured_response(response, "cast_vote")

        assert result.transport == "xml_tool_fallback"
        assert result.payload == {
            "action_type": "vote", "target_seat": "4",
            "reasoning": "4号发言矛盾",
        }

    def test_structured_response_ignores_xml_with_unexpected_tool(self):
        response = self._xml_response(
            '<tool_call>\n<function=other_tool>\n'
            '<parameter=x>1</parameter>\n</function>\n</tool_call>'
        )

        with pytest.raises(ValueError):
            LLMClient._structured_response(response, "cast_vote")

    def test_coerce_payload_types_converts_strings_per_schema(self):
        from app.agents.llm_client import LLMClientConfig, StructuredResponse

        client = self._custom_openai_client()
        response = StructuredResponse(
            payload={
                "action_type": "vote", "target_seat": "4",
                "reasoning": "4号发言矛盾",
            },
            transport="xml_tool_fallback",
            has_tool_calls=False,
        )
        schema = {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "enum": ["vote", "abstain"]},
                "target_seat": {"type": ["integer", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["action_type", "target_seat", "reasoning"],
        }

        coerced = client._coerce_payload_types(response, schema)

        assert coerced.payload["target_seat"] == 4
        assert coerced.payload["action_type"] == "vote"

    def test_coerce_payload_types_maps_null_marker_to_none(self):
        from app.agents.llm_client import StructuredResponse

        response = StructuredResponse(
            payload={"action_type": "abstain", "target_seat": "null", "reasoning": "x"},
            transport="xml_tool_fallback", has_tool_calls=False,
        )
        schema = {
            "type": "object",
            "properties": {
                "action_type": {"type": "string"},
                "target_seat": {"type": ["integer", "null"]},
                "reasoning": {"type": "string"},
            },
        }

        coerced = self._custom_openai_client()._coerce_payload_types(response, schema)

        assert coerced.payload["target_seat"] is None

    @patch("app.agents.llm_client.ChatOpenAI")
    def test_build_caches_one_model_per_purpose(self, mock_chat):
        from app.agents.providers.base import CallPurpose

        client = LLMClient(model="deepseek-chat")

        first = client._build(CallPurpose.TEXT)
        second = client._build(CallPurpose.TEXT)

        assert first is second
        mock_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_aclose_closes_cached_clients_and_clears_cache(self):
        from types import SimpleNamespace

        client = LLMClient(model="deepseek-chat")
        closed = []

        async def fake_aclose():
            closed.append(True)

        fake_model = SimpleNamespace(
            root_async_client=SimpleNamespace(aclose=fake_aclose),
        )
        client._built_models[("text", client._config)] = fake_model

        await client.aclose()

        assert closed == [True]
        assert client._built_models == {}



@pytest.mark.asyncio
async def test_close_continues_after_a_client_cleanup_error():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    client = LLMClient()
    failed = Mock(side_effect=RuntimeError("already closed"))
    synchronous = Mock()
    asynchronous = AsyncMock()
    client._built_models = {"fake": SimpleNamespace(
        root_async_client=SimpleNamespace(aclose=failed),
        async_client=SimpleNamespace(aclose=asynchronous),
        root_client=SimpleNamespace(close=synchronous),
    )}
    await client.aclose()
    failed.assert_called_once()
    synchronous.assert_called_once()
    asynchronous.assert_awaited_once()
    assert client._built_models == {}


@pytest.mark.asyncio
async def test_aclose_does_not_poison_the_next_openai_http_client():
    from langchain_openai.chat_models._client_utils import (
        _cached_async_httpx_client, _cached_sync_httpx_client,
    )
    from app.agents.llm_client import LLMClientConfig

    _cached_sync_httpx_client.cache_clear()
    _cached_async_httpx_client.cache_clear()
    config = LLMClientConfig(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model_id="deepseek-v4-flash",
        temperature=1.2,
        max_tokens=512,
        strict_base_url="https://api.deepseek.com/beta",
        provider_profile="deepseek",
    )
    first = LLMClient(config=config)
    first.get_model()
    first.get_action_model()
    await first.aclose()

    second = LLMClient(config=config)
    text_http = second.get_model().root_client._client
    action_http = second.get_action_model().root_client._client
    try:
        assert text_http.is_closed is False
        assert action_http.is_closed is False
    finally:
        await second.aclose()
        _cached_sync_httpx_client.cache_clear()
        _cached_async_httpx_client.cache_clear()
