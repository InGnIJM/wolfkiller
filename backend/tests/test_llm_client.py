import pytest
from unittest.mock import patch
from app.agents.llm_client import LLMClient


class TestLLMClient:
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
        assert model is not None

    @patch("app.agents.llm_client.ChatOpenAI")
    def test_get_model_with_temperature(self, mock_chat):
        client = LLMClient(model="deepseek-chat")
        model = client.get_model_with_temperature(0.2)
        mock_chat.assert_called_once()
        assert model is not None
