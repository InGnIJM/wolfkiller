from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from openai import BadRequestError, UnprocessableEntityError

from app.config import config as app_config
from app.agents.output_parser import StrictCapabilityError
from app.models.contracts import ActionContract


_DEEPSEEK_STRICT_HOST = "api.deepseek.com"
_ACTION_TEMPERATURE = 0.1
_ACTION_MAX_TOKENS = 768


def derive_strict_base_url(
    base_url: str, explicit_strict_base_url: str | None = None,
) -> str:
    """Return the strict-mode endpoint for a configured model.

    The official DeepSeek endpoint serves strict tool calling from its
    dedicated beta host; every other provider reuses its base URL. An
    explicitly configured strict address always wins.
    """
    if explicit_strict_base_url:
        return explicit_strict_base_url
    if urlparse(base_url).hostname == _DEEPSEEK_STRICT_HOST:
        return "https://api.deepseek.com/beta"
    return base_url


@dataclass(frozen=True)
class LLMClientConfig:
    """Explicit per-client model configuration (reads no global state)."""

    base_url: str
    api_key: str
    model_id: str
    temperature: float
    max_tokens: int
    strict_base_url: str
    action_max_tokens: int = 2048
    action_timeout_seconds: float = 90.0
    action_retry_timeout_seconds: float = 120.0


def env_default_client_config() -> LLMClientConfig:
    """Materialize the .env fallback configuration."""
    llm_cfg = app_config.llm
    return LLMClientConfig(
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key,
        model_id=llm_cfg.models[0] if llm_cfg.models else "deepseek-v4-pro",
        temperature=llm_cfg.temperature,
        max_tokens=llm_cfg.max_tokens,
        strict_base_url=llm_cfg.strict_base_url,
        action_max_tokens=getattr(llm_cfg, "action_max_tokens", 2048),
        action_timeout_seconds=getattr(llm_cfg, "action_timeout_seconds", 90.0),
        action_retry_timeout_seconds=getattr(
            llm_cfg, "action_retry_timeout_seconds", 120.0,
        ),
    )


class LLMClient:
    """Thin wrapper around LangChain ChatModel (OpenAI-compatible).

    Configuration comes from an explicit LLMClientConfig; when omitted the
    .env defaults are used so existing callers keep working. The ``model``
    and ``temperature`` arguments, when provided, override the corresponding
    config values.
    """

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        config: LLMClientConfig | None = None,
    ):
        self._config = config if config is not None else env_default_client_config()
        self.model_name = model or self._config.model_id
        self.temperature = (
            temperature if temperature is not None else self._config.temperature
        )
        self.max_tokens = self._config.max_tokens
        self.action_max_tokens = self._config.action_max_tokens
        self.action_timeout_seconds = self._config.action_timeout_seconds
        self.action_retry_timeout_seconds = self._config.action_retry_timeout_seconds

    @property
    def supports_strict_actions(self) -> bool:
        """Attempt a forced tool first; unsupported providers fall back safely."""
        return True

    def _build(self) -> ChatOpenAI:
        return ChatOpenAI(
            model=self.model_name,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.action_timeout_seconds,
        )

    def get_model(self) -> BaseChatModel:
        return self._build()

    def get_action_model(self) -> BaseChatModel:
        """Return a JSON action model with enough output budget to finish."""
        return ChatOpenAI(
            model=self.model_name,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            temperature=_ACTION_TEMPERATURE,
            max_tokens=min(self.action_max_tokens, _ACTION_MAX_TOKENS),
            timeout=max(
                self.action_timeout_seconds, self.action_retry_timeout_seconds,
            ) + 5.0,
        )

    def get_model_with_temperature(self, temperature: float) -> BaseChatModel:
        return ChatOpenAI(
            model=self.model_name,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            temperature=temperature,
            max_tokens=self.max_tokens,
            timeout=self.action_timeout_seconds,
        )

    def get_model_with_tools(self, tools: list[dict]) -> BaseChatModel:
        """Return a model with function-calling tools bound."""
        return self._build().bind_tools(tools)

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
        model = ChatOpenAI(
            model=self.model_name,
            api_key=self._config.api_key,
            base_url=self._config.strict_base_url,
            temperature=_ACTION_TEMPERATURE,
            max_tokens=min(self.action_max_tokens, _ACTION_MAX_TOKENS),
            timeout=max(
                self.action_timeout_seconds, self.action_retry_timeout_seconds,
            ) + 5.0,
        )
        tool = {
            "type": "function",
            "function": {
                "name": contract.resolved_tool_name,
                "description": "Submit the issued game action.",
                "parameters": contract.json_schema(),
                "strict": True,
            },
        }
        return model.bind_tools(
            [tool], tool_choice=contract.resolved_tool_name, strict=True
        )
