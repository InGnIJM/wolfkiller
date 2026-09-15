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

# Timeouts subclass connection errors in both SDKs; catch TIMEOUT_ERRORS
# first when the two must be told apart.
TIMEOUT_ERRORS: tuple[type[Exception], ...] = (
    openai.APITimeoutError,
    anthropic.APITimeoutError,
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
