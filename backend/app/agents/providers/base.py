import os
from dataclasses import dataclass
from enum import StrEnum


API_MODE_CHAT_COMPLETIONS = "chat_completions"
API_MODE_ANTHROPIC_MESSAGES = "anthropic_messages"


class CallPurpose(StrEnum):
    TEXT = "text"
    ACTION_JSON = "action_json"
    TOOLS = "tools"
    ACTION_STRICT = "action_strict"


ACTION_PURPOSES = frozenset({
    CallPurpose.ACTION_JSON,
    CallPurpose.ACTION_STRICT,
    CallPurpose.TOOLS,
})


@dataclass(frozen=True)
class ModelCapabilities:
    tools: bool
    strict_tools: bool
    json_output: bool
    reasoning_effort: bool
    temperature: bool
    # Some OpenAI-compatible gateways reject a named tool_choice with 400
    # (e.g. scnet); binding the tool and letting the model choose still works.
    forced_tool_choice: bool = True


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    api_mode: str
    capabilities: ModelCapabilities
    default_action_max_tokens: int
    strict_endpoint: bool = False


@dataclass(frozen=True)
class CallBudget:
    """Output token budget and request timeout shared by every transport."""

    max_tokens: int
    timeout: float


def provider_max_retries() -> int:
    """SDK-level retries for transient 429/5xx; honors Retry-After headers."""
    return int(os.getenv("LLM_PROVIDER_MAX_RETRIES", "4"))


def call_budget(config, purpose: CallPurpose) -> CallBudget:
    """Pick the token/timeout policy for a call purpose from the client config.

    Action calls (JSON, tools, strict) get the dedicated action budget and a
    timeout with headroom above the retry budget; plain text calls use the
    general budget and the primary action timeout.
    """
    if purpose in ACTION_PURPOSES:
        return CallBudget(
            max_tokens=config.action_max_tokens,
            timeout=max(
                config.action_timeout_seconds, config.action_retry_timeout_seconds,
            ) + 5.0,
        )
    return CallBudget(
        max_tokens=config.max_tokens,
        timeout=config.action_timeout_seconds,
    )
