import os
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


API_MODE_CHAT_COMPLETIONS = "chat_completions"
API_MODE_ANTHROPIC_MESSAGES = "anthropic_messages"
API_MODE_OPENAI_RESPONSES = "openai_responses"

#: Path suffix that selects the OpenAI Responses dialect. Providers publish
#: endpoints in this form (e.g. ``https://opencode.ai/zen/v1/responses``) and
#: some models answer only there — the same host returns HTTP 500 for them on
#: ``/chat/completions``.
_RESPONSES_PATH_SUFFIX = "/responses"


def is_responses_endpoint(path: str) -> bool:
    """Whether an endpoint path selects the OpenAI Responses API."""
    return path.rstrip("/").endswith(_RESPONSES_PATH_SUFFIX)


def strip_responses_suffix(base_url: str) -> str:
    """Drop a trailing ``/responses`` before handing the URL to the SDK.

    The OpenAI SDK appends the resource path itself, so a Base URL copied
    verbatim from a provider's model table would otherwise be posted to as
    ``/responses/responses``.
    """
    trimmed = base_url.rstrip("/")
    if not is_responses_endpoint(trimmed):
        return base_url
    return trimmed[: -len(_RESPONSES_PATH_SUFFIX)]

# ── custom request headers ────────────────────────────────────

#: Upper bound on user-supplied headers per model configuration.
MAX_CUSTOM_HEADERS = 32

#: RFC 7230 token charset — the only characters legal in a header field name.
HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")

#: Characters that would let a value forge extra header lines.
_HEADER_VALUE_FORBIDDEN = ("\r", "\n", "\x00")

#: Names owned by the SDK or the gateway: hop-by-hop headers, request framing
#: and credentials. A custom value is ignored at best and corrupts the request
#: at worst, so these are rejected instead of being silently overridden.
RESERVED_HEADER_NAMES = frozenset({
    "accept-encoding", "authorization", "connection", "content-length",
    "content-type", "host", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade",
})


def header_error(name: str, value: str) -> str | None:
    """Return why a custom header is unusable, or None when it is fine.

    Shared by the API validator (which rejects a request) and the transport
    (which drops the entry), so both agree on what is acceptable.
    """
    if not HEADER_NAME_PATTERN.match(name):
        return "name must be an RFC 7230 token"
    if any(char in value for char in _HEADER_VALUE_FORBIDDEN):
        return "value must not contain CR, LF or NUL"
    if name.lower() in RESERVED_HEADER_NAMES:
        return "header is managed by the SDK or gateway"
    return None


def new_session_id() -> str:
    """Generate an opaque session id shaped like the reference client's.

    The reference client sends a 26-character random suffix behind a ``ses_``
    prefix; matching that shape keeps relays which validate the format happy.
    """
    return f"ses_{secrets.token_hex(13)}"


def merge_request_headers(
    profile: "ProviderProfile",
    config_headers: Iterable[tuple[str, str]] = (),
    session_id: str | None = None,
) -> dict[str, str]:
    """Layer profile defaults, the generated session header and user headers.

    Precedence, later wins: profile defaults → generated session → user
    headers, so an explicitly configured value always beats a built-in one.
    Names are matched without regard to case. Unusable entries are dropped
    rather than sent.
    """
    # Keyed by the lower-case name so a user header replaces a built-in one
    # even when only the letter case differs. The winning entry keeps its
    # own spelling.
    merged: dict[str, tuple[str, str]] = {}

    def put(name: str, value: str) -> None:
        if header_error(name, value) is None:
            merged[name.lower()] = (name, value)

    for name, value in profile.default_headers:
        put(name, value)
    if profile.session_header and session_id:
        put(profile.session_header, session_id)
    for name, value in config_headers:
        put(name, value)
    return {name: value for name, value in merged.values()}


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
    #: Headers every request to this provider must carry.
    default_headers: tuple[tuple[str, str], ...] = ()
    #: Header that carries the per-client generated session id, when the
    #: provider requires one. The value is minted per LLMClient, never here,
    #: so a frozen profile stays stateless and hashable.
    session_header: str | None = None


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
