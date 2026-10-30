"""Provider-neutral exception groups.

Each SDK (openai, anthropic) raises its own exception hierarchy. Callers that
need to classify failures (capability rejection, rate limit, server error,
connection, timeout) should catch these tuples instead of a single SDK's
classes so every transport degrades the same way.
"""

import anthropic
import openai


# 400/422 from the provider: the request shape (usually a tool definition or
# tool_choice) was rejected. Mapped to a JSON fallback, never retried as-is.
CAPABILITY_REJECTION_ERRORS: tuple[type[Exception], ...] = (
    openai.BadRequestError,
    openai.UnprocessableEntityError,
    anthropic.BadRequestError,
    anthropic.UnprocessableEntityError,
)

RATE_LIMIT_ERRORS: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    anthropic.RateLimitError,
)

# 5xx and Anthropic's 529 "overloaded" both mean "the provider, not us".
SERVER_ERRORS: tuple[type[Exception], ...] = (
    openai.InternalServerError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
)

class ProviderDeadlineTimeout(TimeoutError):
    """The whole streamed call exceeded its wall-clock budget.

    ``ChatOpenAI``'s ``timeout`` is an httpx per-request/read timeout: a model
    that keeps trickling chunks can stay inside it forever while the total call
    grows without bound. The client raises this once the configured action
    budget is exhausted so the retry/fallback ladder sees a normal provider
    timeout. The class name contains ``Timeout`` on purpose:
    :func:`classify_provider_error` classifies timeouts by class name.
    """


# Timeouts subclass connection errors in both SDKs; catch TIMEOUT_ERRORS
# first when the two must be told apart.
TIMEOUT_ERRORS: tuple[type[Exception], ...] = (
    openai.APITimeoutError,
    anthropic.APITimeoutError,
    ProviderDeadlineTimeout,
)

CONNECTION_ERRORS: tuple[type[Exception], ...] = (
    openai.APIConnectionError,
    anthropic.APIConnectionError,
)

# Everything a transport may raise that warrants a retry-or-fallback rather
# than a crash: rate limits, server faults and transport-level failures.
TRANSIENT_PROVIDER_ERRORS: tuple[type[Exception], ...] = (
    *RATE_LIMIT_ERRORS,
    *SERVER_ERRORS,
    *CONNECTION_ERRORS,
)


#: HTTP status meaning "reachable, but it will not serve this request right
#: now": capacity, quota, or an emptied upstream account pool. Narrower than the
#: rest of the 5xx family on purpose -- a 503 is a capacity decision whose
#: cooldown is measured in minutes, so re-asking the very same request a second
#: later cannot help. 429 is deliberately absent: it carries a ``Retry-After``
#: hint the transport already honours, and its retries do succeed.
REFUSED_STATUS_CODES: frozenset[int] = frozenset({503})

#: The canonical failure code for a refused request. One vocabulary for every
#: path: before this existed the same HTTP 503 was recorded as
#: ``provider_server_error`` on the daytime ballot path and as
#: ``model_invocation_error`` on the night path, so a single outage could not be
#: counted, alerted on, or even grepped across one game log.
FAILURE_REFUSED = "provider_unavailable"


def error_chain(error: BaseException) -> list[BaseException]:
    """``error`` plus every cause/context link, outermost first.

    LangChain and both SDKs re-wrap the original error, so whichever link still
    carries the response object is the one worth reading. The walk is
    cycle-guarded: an exception that is its own context is legal in Python.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def status_code_of(error: BaseException) -> int | None:
    """The HTTP status carried anywhere in an error's chain, or ``None``."""
    for link in error_chain(error):
        for candidate in (
            getattr(link, "status_code", None),
            getattr(getattr(link, "response", None), "status_code", None),
        ):
            if type(candidate) is int:
                return candidate
    return None


def is_refused_error(error: BaseException) -> bool:
    """Whether a provider refused to serve the request for capacity reasons.

    The predicate the retry planner keys on, and the one a future circuit
    breaker should key on: a refusal says nothing about the request shape or the
    answer, only that the provider had nothing to give right now.
    """
    return status_code_of(error) in REFUSED_STATUS_CODES


#: Terminal provider failures. Retrying cannot fix them; the game should pause
#: (live) or fail (benchmark) so an operator can repair the configuration.
FAILURE_AUTH = "provider_auth_error"
FAILURE_QUOTA = "provider_quota_exhausted"
FAILURE_MODEL_NOT_FOUND = "provider_model_not_found"
FAILURE_CONTEXT_TOO_LARGE = "provider_context_too_large"
FAILURE_REGION_BLOCKED = "provider_region_blocked"

#: Message fragments the gateway/upstream puts in the error body when the
#: failure is deterministic. Status codes alone cannot distinguish a bad tool
#: schema (400) from a region restriction (400) or a context overflow (400).
_REGION_HINTS = (
    "location is not supported",
    "unsupported location",
    "not available in your region",
    "unsupported region",
    "region is not supported",
    "country not supported",
    "unsupported country",
)
_QUOTA_HINTS = (
    "insufficient_quota",
    "exceeded your current quota",
    "billing hard limit",
    "payment required",
    "no credit",
    "credit balance is too low",
)
_CONTEXT_HINTS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "reduce the length",
)


def _mentions(error: BaseException, hints: tuple[str, ...]) -> bool:
    """Whether any link's text carries one of ``hints`` (case-insensitive)."""
    for link in error_chain(error):
        text = str(link).lower()
        if any(hint in text for hint in hints):
            return True
    return False


def classify_provider_error(error: BaseException) -> str:
    """One canonical failure code for a provider error, shared by every path.

    Order matters, and it is deliberately *status first*. A timeout is checked
    before anything else and by class *name*, because the SDKs raise their own
    timeout types under several names and every caller already treats them as one
    bucket. After that the HTTP status decides, because it is ground truth: a
    gateway answering 429 or 503 through whatever class the SDK happened to pick
    must still read as a rate limit or a refusal. There is deliberately no
    ``RATE_LIMIT_ERRORS`` branch: both SDKs build that class from a 429 response,
    so the status check above always decides first. The remaining ``isinstance``
    buckets only fill in what no status can tell us.

    A few statuses are semantically overloaded. The message/body is consulted
    only for those: 400/403 can be a region restriction, a context overflow or a
    bad request shape, and a 429 may be a rate limit or an exhausted quota. The
    deterministic variants get their own terminal code so the game-level abort
    policy can fast-trip on them instead of waiting for a threshold.
    """
    for link in error_chain(error):
        if "timeout" in type(link).__name__.lower():
            return "provider_timeout"
    status_code = status_code_of(error)
    if status_code == 429:
        if _mentions(error, _QUOTA_HINTS):
            return FAILURE_QUOTA
        return "provider_rate_limit"
    if status_code in REFUSED_STATUS_CODES:
        return FAILURE_REFUSED
    if status_code == 504:
        return "provider_timeout"
    if status_code in (401, 403):
        if _mentions(error, _REGION_HINTS):
            return FAILURE_REGION_BLOCKED
        return FAILURE_AUTH
    if status_code == 402:
        return FAILURE_QUOTA
    if status_code == 404:
        return FAILURE_MODEL_NOT_FOUND
    if status_code == 408:
        return "provider_timeout"
    if status_code == 413:
        return FAILURE_CONTEXT_TOO_LARGE
    if status_code in (400, 422):
        if _mentions(error, _REGION_HINTS):
            return FAILURE_REGION_BLOCKED
        if _mentions(error, _CONTEXT_HINTS):
            return FAILURE_CONTEXT_TOO_LARGE
        return "provider_bad_request"
    if isinstance(error, SERVER_ERRORS):
        return "provider_server_error"
    if isinstance(error, CONNECTION_ERRORS):
        return "provider_connection_error"
    if status_code is not None and 500 <= status_code < 600:
        return "provider_server_error"
    return "model_invocation_error"
