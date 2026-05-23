from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from app.config import config as app_config


class LLMClient:
    """Thin wrapper around LangChain ChatModel for DeepSeek (OpenAI-compatible)."""

    def __init__(self, model: str | None = None, temperature: float | None = None):
        llm_cfg = app_config.llm
        self.model_name = model or llm_cfg.models[0]
        self.temperature = temperature if temperature is not None else llm_cfg.temperature
        self.max_tokens = llm_cfg.max_tokens

    def get_model(self) -> BaseChatModel:
        llm_cfg = app_config.llm
        return ChatOpenAI(
            model=self.model_name,
            api_key=llm_cfg.api_key,
            base_url=llm_cfg.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    def get_model_with_temperature(self, temperature: float) -> BaseChatModel:
        llm_cfg = app_config.llm
        return ChatOpenAI(
            model=self.model_name,
            api_key=llm_cfg.api_key,
            base_url=llm_cfg.base_url,
            temperature=temperature,
            max_tokens=self.max_tokens,
        )

    def get_model_with_tools(self, tools: list[dict]) -> BaseChatModel:
        """Return a model with function-calling tools bound."""
        llm_cfg = app_config.llm
        model = ChatOpenAI(
            model=self.model_name,
            api_key=llm_cfg.api_key,
            base_url=llm_cfg.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return model.bind_tools(tools)
