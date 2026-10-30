"""Classify one failed model call for the game-level abort policy.

The retry ladder already answers *what to do about this attempt*
(:mod:`app.agents.retry`) and the provider layer answers *which fault is this*
(:mod:`app.agents.providers.errors`). This module answers the third question:
**what does this failure mean for the game as a whole?**

The answer is deliberately a small, closed vocabulary:

``TERMINAL``
    Deterministic provider/configuration fault. Another attempt cannot help;
    a live game pauses and a benchmark fails the item.
``TRANSIENT``
    Network/provider capacity fault. Isolated instances are retried and
    degraded; a sustained cross-seat pattern trips the breaker.
``REQUEST_SHAPE``
    A 400/422 that may be one contract's schema or the whole gateway rejecting
    every request. It only trips when several distinct contracts and seats fail
    together, so a single bad tool schema never stops the game.
``QUALITY``
    The provider answered, but the payload was unusable (parse/schema/speech
    quality). Repair first; a sustained cross-seat pattern trips the quality
    breaker.
``IGNORED``
    Cancellation, stale results, scheduling timeouts and other non-provider
    events. They are never counted as failures.
``INTERNAL``
    Structural/durability defects. They must surface as ``failed``, not as a
    recoverable pause.

The registry is intentionally explicit. A new failure code that is not listed
here falls back to ``TRANSIENT`` *and* is reported as unclassified by the
taxonomy gate test, so a silent default cannot hide a new failure mode.
"""

from __future__ import annotations

from enum import StrEnum

from app.agents.providers.errors import (
    FAILURE_AUTH,
    FAILURE_CONTEXT_TOO_LARGE,
    FAILURE_MODEL_NOT_FOUND,
    FAILURE_QUOTA,
    FAILURE_REGION_BLOCKED,
    classify_provider_error,
)

#: Terminal codes: another attempt cannot help. Keep this list in sync with
#: ``app.agents.providers.errors``.
TERMINAL_CODES = frozenset({
    FAILURE_AUTH,
    FAILURE_QUOTA,
    FAILURE_MODEL_NOT_FOUND,
    FAILURE_CONTEXT_TOO_LARGE,
    FAILURE_REGION_BLOCKED,
})

#: Transient codes: retry/backoff may recover; only a sustained pattern trips.
TRANSIENT_CODES = frozenset({
    "provider_timeout",
    "provider_rate_limit",
    "provider_unavailable",
    "provider_server_error",
    "provider_connection_error",
    "model_invocation_error",
    "invoke_error",
})

#: Request-shape codes: need cross-contract evidence before tripping.
REQUEST_SHAPE_CODES = frozenset({
    "provider_bad_request",
})

#: Unusable-answer codes. These are the ``code`` values raised by
#: ``UnusableModelOutput``, ``ToolCallError`` and ``ActionValidationError``.
QUALITY_CODES = frozenset({
    "response_no_tool_call",
    "response_multiple_tool_calls",
    "response_unexpected_tool",
    "tool_args_not_object",
    "response_missing_fields",
    "speech_tool_not_called",
    "speech_wrong_function",
    "speech_empty_text",
    "speech_text_too_short",
    "action_payload_not_json",
    "action_payload_missing_field",
    "action_payload_extra_field",
    "action_payload_wrong_type",
    "action_payload_schema_invalid",
    "action_response_not_text",
    "action_type_not_permitted",
    "target_required",
    "reasoning_too_long",
    "strict_tool_name_mismatch",
    "strict_tool_missing",
    "strict_tool_count_invalid",
    "action_validation_error",
    "unusable_model_output",
    "sheriff_vote_unusable",
})

#: Codes that never count toward a provider breaker.
IGNORED_CODES = frozenset({
    "request_timeout",          # phase deadline / local deadline handled separately
    "missing_vote_result",
    "stale_execution_generation",
    "process_interrupted",
    "effect_rejected",
    "strict_capability_fallback",
    "invalid",
})

#: Structural/durability codes that must end the game as ``failed``.
INTERNAL_CODES = frozenset({
    "pipeline_paused",
    "commit_conflict",
    "repository_version_conflict",
    "checkpoint_corrupt",
    "checkpoint_missing",
    "checkpoint_version_unsupported",
    "model_config_missing",
    "model_key_unavailable",
    "registry_incompatible",
    "recovery_required",
})

#: Telemetry ``parse_result`` values that mean "the logical call succeeded".
_OK_PARSE_RESULTS = frozenset({"ok", "accepted", "strict_content_fallback"})

#: Telemetry ``parse_result`` values for an intermediate attempt. The final row
#: for the same logical call carries the real outcome.
_RETRY_PARSE_SUFFIX = "_retry"
_SKIP_PARSE_RESULTS = frozenset({
    "strict_capability_fallback",
    "invalid",
})

#: Telemetry ``parse_result`` values that mean "the logical call ended unused".
_FAILED_PARSE_RESULTS = frozenset({
    "fallback",
    "error",
    "degraded",
    "timeout_fallback",
    "invoke_error_fallback",
})


class FailureKind(StrEnum):
    TERMINAL = "terminal"
    TRANSIENT = "transient"
    REQUEST_SHAPE = "request_shape"
    QUALITY = "quality"
    IGNORED = "ignored"
    INTERNAL = "internal"


class Disposition(StrEnum):
    """What the game should do about one failure kind."""

    CONTINUE = "continue"
    PAUSE = "pause"
    FAIL = "fail"
    IGNORE = "ignore"


_REGISTRY: dict[str, FailureKind] = {
    **{code: FailureKind.TERMINAL for code in TERMINAL_CODES},
    **{code: FailureKind.TRANSIENT for code in TRANSIENT_CODES},
    **{code: FailureKind.REQUEST_SHAPE for code in REQUEST_SHAPE_CODES},
    **{code: FailureKind.QUALITY for code in QUALITY_CODES},
    **{code: FailureKind.IGNORED for code in IGNORED_CODES},
    **{code: FailureKind.INTERNAL for code in INTERNAL_CODES},
}

#: ``UnusableModelOutput`` / ``ToolCallError`` / ``ActionValidationError``
#: carry a ``code`` attribute. These are answer-quality failures, never
#: provider-health failures.
_PARSE_ERROR_CODES = frozenset(QUALITY_CODES) | {"action_payload_not_json"}


def failure_kind(code: str | None) -> FailureKind:
    """The registry kind for ``code``; unknown codes fall back to TRANSIENT.

    Unknown codes are also surfaced by :func:`unclassified_codes` and the
    taxonomy gate test. ``TRANSIENT`` is the conservative choice: it needs a
    cross-seat pattern before it can pause anything, so a newly introduced code
    cannot stop a game on its own.
    """
    if code is None:
        return FailureKind.QUALITY
    return _REGISTRY.get(code, FailureKind.TRANSIENT)


def unclassified_codes(codes: set[str] | frozenset[str]) -> list[str]:
    """Codes that have no explicit registry entry (test/gate helper)."""
    return sorted(code for code in codes if code not in _REGISTRY)


def normalize_failure_code(error: BaseException) -> str:
    """One canonical code for a failed call.

    Parse-layer exceptions carry their own stable token; everything else goes
    through the provider classifier. This replaces the four path-specific
    implementations that used to record ``error.code`` first (so a gateway's
    ``upstream_error`` shadowed the HTTP status) or ``type(error).__name__``
    (so a 400 read as ``BadRequestError``).
    """
    code = getattr(error, "code", None)
    if type(code) is str and code in _PARSE_ERROR_CODES:
        return code
    return classify_provider_error(error)


def disposition_for(kind: FailureKind, *, mode: str) -> Disposition:
    """Map one failure kind to what the game should do.

    ``mode`` is the configured abort mode (``off``/``warn``/``pause``/``fail``).
    The breaker itself only calls this once it has decided a *pattern* is
    present; single calls continue until then.
    """
    if kind is FailureKind.IGNORED:
        return Disposition.IGNORE
    if kind is FailureKind.INTERNAL:
        return Disposition.FAIL
    if kind is FailureKind.TERMINAL:
        if mode == "fail":
            return Disposition.FAIL
        if mode == "pause":
            return Disposition.PAUSE
        return Disposition.CONTINUE
    return Disposition.CONTINUE


def sample_kind(
    *, failure_code: str | None, parse_result: str | None,
    timeout_type: str | None = None,
) -> FailureKind | None:
    """Classify one telemetry row; ``None`` means "not a countable sample".

    Telemetry is written per attempt on the vote path and per logical call on
    the night/speech/pipeline paths, so this function is the single place that
    knows which rows are final and which are noise.
    """
    if parse_result is not None and parse_result.endswith(_RETRY_PARSE_SUFFIX):
        return None
    if parse_result in _SKIP_PARSE_RESULTS:
        return None
    if timeout_type == "phase_deadline":
        # The vote window's own deadline is scheduling, not a provider fault.
        return None
    if failure_code is None and parse_result in _OK_PARSE_RESULTS:
        return None
    if failure_code == "missing_vote_result":
        return None
    if failure_code == "request_timeout":
        # The phase deadline returned above is scheduling; anything else is a
        # local/product deadline or a provider timeout and counts as transient.
        return FailureKind.TRANSIENT
    if failure_code is None:
        if parse_result in _FAILED_PARSE_RESULTS:
            # A fallback row must carry a failure code in production; treat a
            # missing one as an unusable answer rather than a provider fault.
            return FailureKind.QUALITY
        return None
    return failure_kind(failure_code)
