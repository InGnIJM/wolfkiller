from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from openai import BadRequestError, UnprocessableEntityError
from app.config import config as app_config
from app.agents.output_parser import StrictCapabilityError
from app.models.contracts import ActionContract


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

    @staticmethod
    def map_strict_capability_error(error: Exception) -> Exception:
        """Map only explicit provider strict-schema rejections to a fallback signal."""
        if isinstance(error, StrictCapabilityError):
            return error
        if not isinstance(error, (BadRequestError, UnprocessableEntityError)):
            return error

        response = error.response
        if response.status_code not in (400, 422):
            return error

        body = error.body if isinstance(error.body, dict) else {}
        detail = body.get("error", body)
        if not isinstance(detail, dict):
            detail = {}
        message = str(detail.get("message", error)).lower()
        code = str(detail.get("code", "")).lower()
        parameter = str(detail.get("param", "")).lower()
        strict_or_schema = any(
            marker in " ".join((message, code, parameter))
            for marker in ("strict", "schema", "response_format", "tool")
        )
        unsupported = any(
            marker in " ".join((message, code))
            for marker in (
                "unsupported", "not support", "does not support",
                "not available", "invalid_parameter",
            )
        )
        if strict_or_schema and unsupported:
            return StrictCapabilityError(str(error))
        return error

    def get_model_with_action_tool(self, contract: ActionContract) -> BaseChatModel:
        """Return a strict model bound to the one action tool issued by a contract."""
        llm_cfg = app_config.llm
        model = ChatOpenAI(
            model=self.model_name,
            api_key=llm_cfg.api_key,
            base_url=llm_cfg.strict_base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        tool = {
            "type": "function",
            "function": {
                "name": contract.contract_id,
                "description": "Submit the issued game action.",
                "parameters": contract.json_schema(),
                "strict": True,
            },
        }
        return model.bind_tools(
            [tool], tool_choice=contract.contract_id, strict=True
        )
