import importlib

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

    def test_keeps_422_non_strict_schema_rejection_unchanged(self):
        body = {
            "error": {
                "message": "The requested game state is invalid",
                "code": "invalid_request",
            }
        }
        error = self._provider_error(
            UnprocessableEntityError, body["error"]["message"], 422, body
        )

        assert LLMClient.map_strict_capability_error(error) is error

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
                BadRequestError, "Invalid request", 400,
                {"error": "Invalid request"},
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
        assert LLMConfig().models == ["alpha", "beta"]

        monkeypatch.setenv("LLM_MODELS", "")
        monkeypatch.setenv("LLM_MODEL", "single")
        assert LLMConfig().models == ["single"]

        monkeypatch.delenv("LLM_MODEL")
        assert LLMConfig().models == ["deepseek-v4-pro"]

    def test_llm_config_loads_strict_base_url_from_environment(self, monkeypatch):
        with monkeypatch.context() as environment:
            environment.setenv("DEEPSEEK_STRICT_BASE_URL", "https://strict.example")
            reloaded_config = importlib.reload(config_module)
            assert reloaded_config.config.llm.strict_base_url == "https://strict.example"

        importlib.reload(config_module)

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
