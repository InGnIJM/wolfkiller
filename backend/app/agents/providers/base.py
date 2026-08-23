from dataclasses import dataclass
from enum import StrEnum


class CallPurpose(StrEnum):
    TEXT = "text"
    ACTION_JSON = "action_json"
    TOOLS = "tools"
    ACTION_STRICT = "action_strict"


@dataclass(frozen=True)
class ModelCapabilities:
    tools: bool
    strict_tools: bool
    json_output: bool
    reasoning_effort: bool
    temperature: bool


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    api_mode: str
    capabilities: ModelCapabilities
    default_action_max_tokens: int
    strict_endpoint: bool = False
