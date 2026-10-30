"""The failure vocabulary and its game-level meaning."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from app.agents.failure_taxonomy import (
    FailureKind,
    Disposition,
    IGNORED_CODES,
    QUALITY_CODES,
    TERMINAL_CODES,
    TRANSIENT_CODES,
    disposition_for,
    failure_kind,
    normalize_failure_code,
    sample_kind,
    unclassified_codes,
)
from app.agents.providers.errors import (
    FAILURE_AUTH,
    FAILURE_CONTEXT_TOO_LARGE,
    FAILURE_MODEL_NOT_FOUND,
    FAILURE_QUOTA,
    FAILURE_REGION_BLOCKED,
    classify_provider_error,
)
from app.agents.retry import UnusableModelOutput

APP = Path(__file__).resolve().parents[1] / "app"


class _BareStatus(Exception):
    def __init__(self, status_code: int, message: str | None = None) -> None:
        super().__init__(message or f"status {status_code}")
        self.status_code = status_code


def test_terminal_codes_cover_the_whole_provider_vocabulary():
    assert TERMINAL_CODES == {
        FAILURE_AUTH,
        FAILURE_QUOTA,
        FAILURE_MODEL_NOT_FOUND,
        FAILURE_CONTEXT_TOO_LARGE,
        FAILURE_REGION_BLOCKED,
    }


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("provider_timeout", FailureKind.TRANSIENT),
        ("provider_rate_limit", FailureKind.TRANSIENT),
        ("provider_unavailable", FailureKind.TRANSIENT),
        ("provider_server_error", FailureKind.TRANSIENT),
        ("provider_connection_error", FailureKind.TRANSIENT),
        ("model_invocation_error", FailureKind.TRANSIENT),
        ("invoke_error", FailureKind.TRANSIENT),
        ("provider_bad_request", FailureKind.REQUEST_SHAPE),
        ("response_no_tool_call", FailureKind.QUALITY),
        ("speech_empty_text", FailureKind.QUALITY),
        ("action_payload_not_json", FailureKind.QUALITY),
        ("missing_vote_result", FailureKind.IGNORED),
        ("stale_execution_generation", FailureKind.IGNORED),
        ("pipeline_paused", FailureKind.INTERNAL),
        ("commit_conflict", FailureKind.INTERNAL),
        (None, FailureKind.QUALITY),
        ("something_new", FailureKind.TRANSIENT),
    ],
)
def test_failure_kind_registry(code, expected):
    assert failure_kind(code) is expected


def test_every_provider_classifier_output_is_registered():
    codes = {
        classify_provider_error(_BareStatus(status))
        for status in (400, 401, 402, 403, 404, 408, 413, 429, 500, 502, 503, 504)
    }
    codes.update({
        classify_provider_error(UnusableModelOutput("x")),
        classify_provider_error(RuntimeError("plain")),
    })
    assert unclassified_codes(codes) == []


def test_source_code_literals_are_all_registered():
    """A new failure code must be classified, not silently fall through."""
    literal = re.compile(r'code="([a-z0-9_]+)"')
    seen: set[str] = set()
    for path in (
        APP / "agents" / "llm_client.py",
        APP / "agents" / "output_parser.py",
        APP / "roles" / "base.py",
    ):
        seen.update(literal.findall(path.read_text(encoding="utf-8")))
    assert seen
    assert unclassified_codes(seen) == []


def test_normalize_failure_code_prefers_a_parse_token():
    error = UnusableModelOutput("missing", code="response_missing_fields")
    assert normalize_failure_code(error) == "response_missing_fields"


def test_normalize_failure_code_uses_status_for_provider_errors():
    error = _BareStatus(401, "invalid token")
    assert normalize_failure_code(error) == FAILURE_AUTH


@pytest.mark.parametrize(
    ("failure_code", "parse_result", "timeout_type", "expected"),
    [
        (None, "ok", None, None),
        (None, "accepted", None, None),
        (None, "strict_content_fallback", None, None),
        (None, "strict_capability_fallback", None, None),
        (None, "invalid", None, None),
        ("request_timeout", "timeout_retry", None, None),
        ("request_timeout", "timeout_fallback", "phase_deadline", None),
        ("missing_vote_result", None, None, None),
        ("request_timeout", "timeout_fallback", None, FailureKind.TRANSIENT),
        ("request_timeout", "timeout_fallback", "local_deadline", FailureKind.TRANSIENT),
        ("response_no_tool_call", "fallback", None, FailureKind.QUALITY),
        ("provider_auth_error", "error", None, FailureKind.TERMINAL),
        ("provider_timeout", "error", None, FailureKind.TRANSIENT),
        (None, "fallback", None, FailureKind.QUALITY),
    ],
)
def test_sample_kind_filters_and_classifies(
    failure_code, parse_result, timeout_type, expected,
):
    assert sample_kind(
        failure_code=failure_code, parse_result=parse_result,
        timeout_type=timeout_type,
    ) is expected


@pytest.mark.parametrize(
    ("kind", "mode", "expected"),
    [
        (FailureKind.TERMINAL, "pause", Disposition.PAUSE),
        (FailureKind.TERMINAL, "fail", Disposition.FAIL),
        (FailureKind.TERMINAL, "warn", Disposition.CONTINUE),
        (FailureKind.TERMINAL, "off", Disposition.CONTINUE),
        (FailureKind.TRANSIENT, "pause", Disposition.CONTINUE),
        (FailureKind.QUALITY, "pause", Disposition.CONTINUE),
        (FailureKind.IGNORED, "pause", Disposition.IGNORE),
        (FailureKind.INTERNAL, "pause", Disposition.FAIL),
    ],
)
def test_disposition_for(kind, mode, expected):
    assert disposition_for(kind, mode=mode) is expected


def test_quality_and_ignored_sets_are_disjoint():
    assert QUALITY_CODES.isdisjoint(IGNORED_CODES)
    assert QUALITY_CODES.isdisjoint(TRANSIENT_CODES)


def test_asyncio_timeout_is_not_a_registry_code():
    """Cancellation/timeout instances are classified by callers, not by code."""
    assert "CancelledError" not in TRANSIENT_CODES
    assert issubclass(asyncio.TimeoutError, TimeoutError)


def test_an_unrecognised_parse_result_without_a_code_is_not_a_sample():
    assert sample_kind(failure_code=None, parse_result="mystery") is None
