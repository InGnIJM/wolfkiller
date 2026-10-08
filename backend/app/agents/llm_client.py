from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

from app.config import config as app_config
from app.agents.output_parser import (
    StrictCapabilityError, coerce_payload_to_schema, extract_json_object,
    extract_tool_call_xml,
)
# ``UnusableModelOutput`` is the one exception the parser raises for "the
# provider answered, but we cannot use it" -- the distinction ``retry_plan``
# reads to choose a repair reminder over a verbatim resend.
from app.agents.retry import UnusableModelOutput
from app.agents.providers.base import (
    API_MODE_ANTHROPIC_MESSAGES, API_MODE_CHAT_COMPLETIONS, CallPurpose,
)
# Re-exported so game code can classify provider failures without importing
# the provider layer (architecture boundary: core/roles never import providers).
from app.agents.providers.errors import (  # noqa: F401
    CAPABILITY_REJECTION_ERRORS,
    CONNECTION_ERRORS,
    FAILURE_REFUSED,
    RATE_LIMIT_ERRORS,
    REFUSED_STATUS_CODES,
    SERVER_ERRORS,
    TIMEOUT_ERRORS,
    TRANSIENT_PROVIDER_ERRORS,
    classify_provider_error,
    is_refused_error,
    status_code_of,
)
from app.agents.providers.registry import ProviderRegistry
from app.agents.providers.transports import transport_for
from app.models.contracts import ActionContract


_DEEPSEEK_STRICT_HOST = "api.deepseek.com"
_ACTION_TEMPERATURE = 0.1
_DEFAULT_CHAT_OPENAI = ChatOpenAI
logger = logging.getLogger(__name__)


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
    action_final_retry_timeout_seconds: float = 30.0
    provider_profile: str = "auto"
    #: Extra request headers. Held as pairs, not a dict, because a frozen
    #: config is used as a cache key and therefore has to stay hashable.
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LLMUsage:
    """Token consumption reported by the provider for one or more model calls."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class StructuredResponse:
    """Provider-neutral structured payload returned by the model gateway."""

    payload: dict[str, Any]
    transport: str
    has_tool_calls: bool
    finish_reason: str | None = None
    content_length: int = 0
    usage: LLMUsage | None = None
    elapsed_ms: int = 0
    llm_attempts: int = 1


def _usage_from_map(mapping: Any) -> LLMUsage | None:
    """Build LLMUsage from a LangChain or OpenAI-style token mapping."""
    if not isinstance(mapping, Mapping):
        return None
    prompt_tokens = mapping.get("input_tokens", mapping.get("prompt_tokens"))
    completion_tokens = mapping.get("output_tokens", mapping.get("completion_tokens"))
    if type(prompt_tokens) is not int or type(completion_tokens) is not int:
        return None
    total_tokens = mapping.get("total_tokens")
    if type(total_tokens) is not int:
        total_tokens = prompt_tokens + completion_tokens
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def extract_usage(response: Any) -> LLMUsage | None:
    """Read token usage from a model response in either metadata dialect."""
    usage = _usage_from_map(getattr(response, "usage_metadata", None))
    if usage is not None:
        return usage
    response_metadata = getattr(response, "response_metadata", None)
    token_usage = (
        response_metadata.get("token_usage")
        if isinstance(response_metadata, Mapping) else None
    )
    return _usage_from_map(token_usage)


def merge_usage(first: LLMUsage | None, second: LLMUsage | None) -> LLMUsage | None:
    """Accumulate token usage across attempts, tolerating missing reports."""
    if first is None:
        return second
    if second is None:
        return first
    return LLMUsage(
        prompt_tokens=first.prompt_tokens + second.prompt_tokens,
        completion_tokens=first.completion_tokens + second.completion_tokens,
        total_tokens=first.total_tokens + second.total_tokens,
    )


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
        action_final_retry_timeout_seconds=getattr(
            llm_cfg, "action_final_retry_timeout_seconds", 30.0,
        ),
        provider_profile=getattr(llm_cfg, "provider_profile", "auto"),
    )


def _content_text(content: Any) -> str:
    """Flatten a chat message's content to plain text.

    OpenAI-style models return a string; Anthropic returns a list of content
    blocks whenever tools or thinking are involved. Text, thinking, and
    reasoning blocks can all carry parseable JSON or XML tool calls.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, Mapping):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
        elif block_type == "thinking":
            text = block.get("thinking")
        elif block_type == "reasoning":
            text = block.get("reasoning", block.get("text"))
        else:
            continue
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def extract_partial_speech(raw: str) -> str:
    """Extract speech text from raw chunks, supporting JSON arguments or plain text."""
    if not raw:
        return ""
    trimmed = raw.strip()
    if "text" in trimmed and ("{" in trimmed or '"text"' in trimmed):
        match = re.search(r'"text"\s*:\s*"', trimmed)
        if match:
            start = match.end()
            res = []
            i = start
            n = len(trimmed)
            while i < n:
                c = trimmed[i]
                if c == "\\":
                    if i + 1 < n:
                        next_c = trimmed[i + 1]
                        if next_c == '"':
                            res.append('"')
                        elif next_c == "n":
                            res.append("\n")
                        elif next_c == "t":
                            res.append("\t")
                        elif next_c == "\\":
                            res.append("\\")
                        else:
                            res.append(next_c)
                        i += 2
                        continue
                    break
                elif c == '"':
                    break
                else:
                    res.append(c)
                    i += 1
            return "".join(res)
    if trimmed.startswith("{"):
        return ""
    return trimmed


class LLMClient:
    """Thin wrapper around LangChain ChatModels behind a provider transport.

    The resolved provider profile selects the transport (OpenAI-compatible
    chat completions or Anthropic Messages). Configuration comes from an
    explicit LLMClientConfig; when omitted the .env defaults are used so
    existing callers keep working. The ``model`` and ``temperature``
    arguments, when provided, override the corresponding config values.
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
        self.action_final_retry_timeout_seconds = self._config.action_final_retry_timeout_seconds
        self.provider_profile = ProviderRegistry().resolve(
            self._config.provider_profile,
            self._config.base_url,
            self.model_name,
        )
        self._transport = transport_for(self.provider_profile)
        # One ChatModel per (purpose, config) instead of one per call: every
        # build creates a fresh httpx SDK client, and unclosed clients
        # segfault the interpreter at shutdown on Windows.
        self._built_models: dict[tuple[CallPurpose, LLMClientConfig], BaseChatModel] = {}
        #: Set once this provider has shown it will not honour an action tool --
        #: either it rejected the schema (400/422) or it answered in content
        #: instead of calling the tool. Learned per client, i.e. per seat for one
        #: game: the failure is a property of the provider, so paying for the
        #: discovery on every single action is pure waste. glm-5.3-flash spent
        #: 24 of one game's 30 ballots that way.
        self._prefers_json_actions = False

    @property
    def prefers_json_actions(self) -> bool:
        """Whether later actions should skip the native action tool."""
        return self._prefers_json_actions

    def note_action_tools_unsupported(self) -> None:
        """Remember that this provider will not do native action tools."""
        self._prefers_json_actions = True

    @property
    def supports_strict_actions(self) -> bool:
        """Whether this provider profile supports strict action tools."""
        return self.provider_profile.capabilities.strict_tools

    @property
    def supports_action_tools(self) -> bool:
        """Whether actions can use native tools, strict or otherwise."""
        return self.provider_profile.capabilities.tools

    def _build(
        self, purpose: CallPurpose, config: LLMClientConfig | None = None,
    ) -> BaseChatModel:
        effective = config or self._config
        key = (purpose, effective)
        model = self._built_models.get(key)
        if model is None:
            # Tests patch ``app.agents.llm_client.ChatOpenAI``; honour that
            # only for the chat-completions transport it stands in for.
            factory = (
                ChatOpenAI
                if ChatOpenAI is not _DEFAULT_CHAT_OPENAI
                and self.provider_profile.api_mode == API_MODE_CHAT_COMPLETIONS
                else None
            )
            model = self._transport.build(
                effective,
                self.provider_profile,
                purpose,
                chat_model_factory=factory,
            )
            self._built_models[key] = model
        return model

    async def aclose(self) -> None:
        """Close every httpx SDK client behind the cached chat models.

        ChatOpenAI exposes ``root_async_client`` / ``async_client`` /
        ``root_client``; ChatAnthropic exposes ``_async_client`` / ``_client``.
        """
        for model in self._built_models.values():
            for attr in (
                "root_async_client", "async_client", "root_client",
                "_async_client", "_client",
            ):
                client = getattr(model, attr, None)
                closer = getattr(client, "aclose", None) or getattr(
                    client, "close", None,
                )
                if not callable(closer):
                    continue
                try:
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.debug("Failed to close %s client", attr, exc_info=True)
        self._built_models.clear()
        # LangChain caches httpx clients process-wide. Closing the SDK wrapper
        # above shuts that shared pool; drop the cache so the next client does
        # not inherit a closed connection (AnthropicConnectionError / APIConnectionError).
        self._transport.discard_shared_http_clients()

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

    def get_model_with_json_tool(
        self, tool_name: str, schema: dict[str, Any],
    ) -> BaseChatModel:
        """Bind a generic non-strict JSON submission tool."""
        if not self.supports_action_tools:
            raise StrictCapabilityError(
                f"Provider profile '{self.provider_profile.profile_id}' does not "
                "support tools"
            )
        tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": "Submit the requested structured JSON response.",
                "parameters": schema,
            },
        }
        model = self._build(
            CallPurpose.NIGHT_JSON,
            replace(self._config, temperature=_ACTION_TEMPERATURE),
        )
        choice = self._action_tool_choice(tool_name)
        if choice is None:
            return model.bind_tools([tool])
        return model.bind_tools([tool], tool_choice=choice)

    def probe(self) -> dict[str, bool]:
        """Invoke a minimal text request and return this profile's capabilities."""
        self.get_model().invoke([HumanMessage(content="ping")])
        return asdict(self.provider_profile.capabilities)

    @staticmethod
    def map_strict_capability_error(error: Exception) -> Exception:
        """Treat client rejections (including wrapped SDK errors) as fallback."""
        if isinstance(error, StrictCapabilityError):
            return error
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, CAPABILITY_REJECTION_ERRORS):
                response = getattr(current, "response", None)
                status = getattr(current, "status_code", None)
                if not isinstance(status, int):
                    status = getattr(response, "status_code", None)
                if status in (400, 422):
                    return StrictCapabilityError(str(error))
            current = current.__cause__ or current.__context__
        return error

    def get_model_with_action_tool(self, contract: ActionContract) -> BaseChatModel:
        """Bind one action contract using the strongest supported tool mode."""
        if not self.supports_action_tools:
            raise StrictCapabilityError(
                f"Provider profile '{self.provider_profile.profile_id}' does not "
                "support action tools"
            )
        strict = self.supports_strict_actions
        model = self._build(
            CallPurpose.ACTION_STRICT if strict else CallPurpose.TOOLS,
            replace(self._config, temperature=_ACTION_TEMPERATURE),
        )
        tool = {
            "type": "function",
            "function": {
                "name": contract.resolved_tool_name,
                "description": "Submit the issued game action.",
                "parameters": contract.json_schema(),
            },
        }
        if strict:
            tool["function"]["strict"] = True
            return model.bind_tools(
                [tool], tool_choice=contract.resolved_tool_name, strict=True
            )
        choice = self._action_tool_choice(contract.resolved_tool_name)
        if choice is None:
            return model.bind_tools([tool])
        return model.bind_tools([tool], tool_choice=choice)

    def _forced_tool_choice(self) -> bool:
        """Duck-type friendly read; scripted/partial profiles default to True."""
        return bool(getattr(
            self.provider_profile.capabilities, "forced_tool_choice", True,
        ))

    def _action_tool_choice(self, tool_name: str) -> str | None:
        """How this profile forces a tool, if it forces one at all.

        Anthropic thinking rejects a named ``tool_choice``; ``any`` still
        requires a tool call and is the documented thinking-compatible mode.
        """
        if not self._forced_tool_choice():
            return None
        if getattr(self.provider_profile, "api_mode", None) == API_MODE_ANTHROPIC_MESSAGES:
            return "any"
        return tool_name

    @staticmethod
    def _coerce_payload_types(
        result: StructuredResponse, schema: dict[str, Any],
    ) -> StructuredResponse:
        """Coerce string parameter values to the schema's primitive types."""
        return replace(
            result,
            payload=coerce_payload_to_schema(result.payload, schema),
        )

    @staticmethod
    def _structured_response(response, expected_tool_name: str) -> StructuredResponse:
        """Read one structured answer, or say why it could not be used.

        Every rejection here is an *answer we could not use*, not a transport
        fault, so it raises :class:`UnusableModelOutput` rather than a bare
        ``ValueError``. That distinction is what lets ``retry_plan`` send a
        repair reminder instead of resending the identical request: the four
        cases below are exactly the ones a sharper request can fix, and a bare
        ``ValueError`` classified them as "no answer arrived" -- so the repair
        path never ran for any of them.
        """
        tool_calls = getattr(response, "tool_calls", None) or []
        content = _content_text(getattr(response, "content", ""))
        content_length = len(content)
        # OpenAI reports ``finish_reason``; Anthropic reports ``stop_reason``.
        metadata = getattr(response, "response_metadata", {})
        finish_reason = metadata.get("finish_reason", metadata.get("stop_reason"))

        if tool_calls:
            if len(tool_calls) != 1:
                raise UnusableModelOutput(
                    "structured response must contain exactly one tool call",
                    code="response_multiple_tool_calls",
                )
            call = tool_calls[0]
            if call.get("name") != expected_tool_name:
                raise UnusableModelOutput(
                    "structured response used an unexpected tool",
                    code="response_unexpected_tool",
                )
            payload = call.get("args", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise UnusableModelOutput(
                    "tool arguments must be a JSON object",
                    code="tool_args_not_object",
                )
            return StructuredResponse(
                payload=payload,
                transport="tool",
                has_tool_calls=True,
                finish_reason=finish_reason,
                content_length=content_length,
            )

        xml_call = extract_tool_call_xml(content)
        if xml_call is not None and xml_call[0] == expected_tool_name:
            return StructuredResponse(
                payload=dict(xml_call[1]),
                transport="xml_tool_fallback",
                has_tool_calls=False,
                finish_reason=finish_reason,
                content_length=content_length,
            )

        payload = extract_json_object(content)
        if not isinstance(payload, dict):
            raise UnusableModelOutput(
                "structured response contains neither a tool call nor JSON",
                code="response_no_tool_call",
            )
        return StructuredResponse(
            payload=payload,
            transport="json_fallback",
            has_tool_calls=False,
            finish_reason=finish_reason,
            content_length=content_length,
        )

    @staticmethod
    def _require_schema_fields(
        result: StructuredResponse, schema: dict[str, Any],
    ) -> StructuredResponse:
        """Reject an answer that parsed but does not honour the issued schema.

        Also an unusable answer, so it also asks for a repair: the model
        answered in the right shape but skipped a required field, and the
        missing-field names travel in ``code``-adjacent text so a log can say
        which one without decoding a sentence.
        """
        required = schema.get("required", ())
        if not isinstance(required, (list, tuple)) or any(
            type(field) is not str for field in required
        ):
            raise ValueError("JSON schema required fields must be strings")
        missing = [field for field in required if field not in result.payload]
        if missing:
            raise UnusableModelOutput(
                "structured response omitted required fields: "
                + ", ".join(missing),
                code="response_missing_fields",
            )
        return result

    def invoke_action(self, messages, contract: ActionContract) -> StructuredResponse:
        """Invoke one action contract through native tools with JSON fallback.

        A provider that has already refused or ignored the action tool goes
        straight to JSON: the discovery is a property of the provider, so paying
        for it once per action is pure waste.
        """
        started = time.monotonic()
        usage = None
        attempts = 0
        if self.supports_action_tools and not self._prefers_json_actions:
            try:
                attempts += 1
                response = self.get_model_with_action_tool(contract).invoke(messages)
                usage = merge_usage(usage, extract_usage(response))
                result = self._structured_response(
                    response, contract.resolved_tool_name,
                )
            except (*CAPABILITY_REJECTION_ERRORS, ValueError) as error:
                logger.warning(
                    "Native action tool failed for provider=%s model=%s; "
                    "retrying as JSON (%s)",
                    self.provider_profile.profile_id,
                    self.model_name,
                    type(error).__name__,
                )
                self.note_action_tools_unsupported()
                attempts += 1
                response = self.get_action_model().invoke(messages)
                usage = merge_usage(usage, extract_usage(response))
                result = self._structured_response(
                    response, contract.resolved_tool_name,
                )
        else:
            attempts += 1
            response = self.get_action_model().invoke(messages)
            usage = merge_usage(usage, extract_usage(response))
            result = self._structured_response(
                response, contract.resolved_tool_name,
            )
        if self.supports_strict_actions and result.transport == "tool":
            result = replace(result, transport="strict_tool")
        result = self._coerce_payload_types(result, contract.json_schema())
        return replace(
            result,
            usage=usage,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            llm_attempts=attempts,
        )

    def invoke_json(
        self, messages, *, tool_name: str, schema: dict[str, Any],
    ) -> StructuredResponse:
        """Request arbitrary structured JSON through tools when available."""
        started = time.monotonic()
        usage = None
        attempts = 0
        if self.supports_action_tools:
            try:
                attempts += 1
                response = self.get_model_with_json_tool(tool_name, schema).invoke(messages)
                usage = merge_usage(usage, extract_usage(response))
                result = self._structured_response(response, tool_name)
                result = self._coerce_payload_types(result, schema)
                return replace(
                    self._require_schema_fields(result, schema),
                    usage=usage,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    llm_attempts=attempts,
                )
            except (*CAPABILITY_REJECTION_ERRORS, ValueError) as error:
                logger.warning(
                    "Native JSON tool failed for provider=%s model=%s; "
                    "retrying as JSON (%s)",
                    self.provider_profile.profile_id,
                    self.model_name,
                    type(error).__name__,
                )
                attempts += 1
                response = self.get_action_model().invoke(messages)
                usage = merge_usage(usage, extract_usage(response))
        else:
            attempts += 1
            response = self.get_action_model().invoke(messages)
            usage = merge_usage(usage, extract_usage(response))
        result = self._structured_response(response, tool_name)
        result = self._require_schema_fields(result, schema)
        result = self._coerce_payload_types(result, schema)
        return replace(
            result,
            usage=usage,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            llm_attempts=attempts,
        )

    async def _stream_and_accumulate(
        self, model, messages, on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ):
        """Consume model.astream chunks, notify on_chunk with partial speech, and return accumulated response."""
        acc = None
        args_acc = ""
        last_emitted = ""
        async for chunk in model.astream(messages):
            acc = chunk if acc is None else acc + chunk
            if on_chunk is not None:
                tool_chunks = getattr(chunk, "tool_call_chunks", None) or []
                if tool_chunks:
                    for tc in tool_chunks:
                        args_val = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", "")
                        if args_val:
                            args_acc += args_val
                    partial = extract_partial_speech(args_acc)
                    if partial and partial != last_emitted:
                        last_emitted = partial
                        await on_chunk(partial)
                elif hasattr(acc, "content") and acc.content:
                    partial = extract_partial_speech(str(acc.content))
                    if partial and partial != last_emitted:
                        last_emitted = partial
                        await on_chunk(partial)
        return acc if acc is not None else AIMessage(content="")

    async def ainvoke_json(
        self, messages, *, tool_name: str, schema: dict[str, Any],
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> StructuredResponse:
        """Async structured JSON request with native-tool to JSON fallback."""
        started = time.monotonic()
        usage = None
        attempts = 0
        if self.supports_action_tools:
            attempts += 1
            try:
                model = self.get_model_with_json_tool(tool_name, schema)
                if on_chunk and hasattr(model, "astream") and callable(getattr(model, "astream", None)):
                    response = await self._stream_and_accumulate(model, messages, on_chunk=on_chunk)
                else:
                    response = await model.ainvoke(messages)
                usage = merge_usage(usage, extract_usage(response))
                result = self._structured_response(response, tool_name)
                result = self._coerce_payload_types(result, schema)
                return replace(
                    self._require_schema_fields(result, schema),
                    usage=usage,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    llm_attempts=attempts,
                )
            except (*CAPABILITY_REJECTION_ERRORS, ValueError) as error:
                logger.warning(
                    "Native async JSON tool failed for provider=%s model=%s; "
                    "retrying as JSON (%s)",
                    self.provider_profile.profile_id,
                    self.model_name,
                    type(error).__name__,
                )
        attempts += 1
        action_model = self.get_action_model()
        if on_chunk and hasattr(action_model, "astream") and callable(getattr(action_model, "astream", None)):
            response = await self._stream_and_accumulate(action_model, messages, on_chunk=on_chunk)
        else:
            response = await action_model.ainvoke(messages)
        usage = merge_usage(usage, extract_usage(response))
        result = self._structured_response(response, tool_name)
        result = self._require_schema_fields(result, schema)
        return replace(
            result,
            usage=usage,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            llm_attempts=attempts,
        )
