from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from urllib.parse import urlparse

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from openai import BadRequestError, UnprocessableEntityError

from app.config import config as app_config
from app.agents.output_parser import StrictCapabilityError
from app.agents.providers.base import CallPurpose
from app.agents.providers.openai_compatible import OpenAICompatibleTransport
from app.agents.providers.registry import ProviderRegistry
from app.models.contracts import ActionContract


_DEEPSEEK_STRICT_HOST = "api.deepseek.com"
_ACTION_TEMPERATURE = 0.1
_DEFAULT_CHAT_OPENAI = ChatOpenAI


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
    provider_profile: str = "auto"


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
        provider_profile=getattr(llm_cfg, "provider_profile", "auto"),
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
        base_config = config if config is not None else env_default_client_config()
        self._config = replace(
            base_config,
            model_id=model or base_config.model_id,
            temperature=(
                temperature if temperature is not None else base_config.temperature
            ),
        )
        self.model_name = self._config.model_id
        self.temperature = self._config.temperature
        self.max_tokens = self._config.max_tokens
        self.action_max_tokens = self._config.action_max_tokens
        self.action_timeout_seconds = self._config.action_timeout_seconds
        self.action_retry_timeout_seconds = self._config.action_retry_timeout_seconds
        self.provider_profile = ProviderRegistry().resolve(
            self._config.provider_profile,
            self._config.base_url,
            self.model_name,
        )
        self._transport = OpenAICompatibleTransport()

    @property
    def supports_strict_actions(self) -> bool:
        """Whether this provider profile supports strict action tools."""
        return self.provider_profile.capabilities.strict_tools

    def _build(
        self, purpose: CallPurpose, config: LLMClientConfig | None = None,
    ) -> BaseChatModel:
        factory = ChatOpenAI if ChatOpenAI is not _DEFAULT_CHAT_OPENAI else None
        return self._transport.build(
            config or self._config,
            self.provider_profile,
            purpose,
            chat_model_factory=factory,
        )

    def get_model(self) -> BaseChatModel:
        return self._build(CallPurpose.TEXT)

    def get_action_model(self) -> BaseChatModel:
        """Return a JSON action model with enough output budget to finish."""
        return self._build(
            CallPurpose.ACTION_JSON,
            replace(self._config, temperature=_ACTION_TEMPERATURE),
        )

    def get_model_with_temperature(self, temperature: float) -> BaseChatModel:
        return self._build(
            CallPurpose.TEXT,
            replace(self._config, temperature=temperature),
        )

    def get_model_with_tools(self, tools: list[dict]) -> BaseChatModel:
        """Return a model with function-calling tools bound."""
        return self._build(CallPurpose.TOOLS).bind_tools(tools)

    def probe(self) -> dict[str, bool]:
        """Invoke a minimal text request and return this profile's capabilities."""
        self.get_model().invoke([HumanMessage(content="ping")])
        return asdict(self.provider_profile.capabilities)

    @staticmethod
    def map_strict_capability_error(error: Exception) -> Exception:
        """Treat strict-endpoint client rejections as a JSON fallback signal."""
        if isinstance(error, StrictCapabilityError):
            return error
        if not isinstance(error, (BadRequestError, UnprocessableEntityError)):
            return error

        response = error.response
        if response.status_code in (400, 422):
            return StrictCapabilityError(str(error))
        return error

    def get_model_with_action_tool(self, contract: ActionContract) -> BaseChatModel:
        """Return a strict model bound to the one action tool issued by a contract."""
        if not self.supports_strict_actions:
            raise StrictCapabilityError(
                f"Provider profile '{self.provider_profile.profile_id}' does not "
                "support strict actions"
            )
        model = self._build(
            CallPurpose.ACTION_STRICT,
            replace(self._config, temperature=_ACTION_TEMPERATURE),
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
