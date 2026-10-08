"""Failure classification decides repair vs resend, and the parser feeds it.

The bug this file pins down: every parse rejection used to raise a bare
``ValueError``, which is in neither ``retry_on`` nor ``repair_on``. On the call
sites that opt into ``retry_unknown_errors`` it was therefore classified as
"no answer arrived" and resent *verbatim* -- three identical questions, three
identical rejects. The repair reminder existed, was tested, and could not be
reached by the one failure shape it was written for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from app.agents.llm_client import LLMClient
from app.agents.output_parser import ToolCallError
from app.agents.providers.errors import classify_provider_error
from app.agents.retry import (
    KIND_REPAIR,
    KIND_TRANSPORT,
    FailureClass,
    RetryPolicy,
    UnusableModelOutput,
    classify_failure,
    retry_plan,
)

#: The four ladder shapes in production, kept in step with the call sites.
NIGHT = RetryPolicy(
    attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0,
    retry_unknown_errors=True,
)
PIPELINE = RetryPolicy(
    attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0,
    retry_unknown_errors=True,
)
SPEECH = RetryPolicy(
    attempts=4, base_delay_seconds=0.0, max_delay_seconds=0.0,
    retry_unknown_errors=True,
)
VOTE = RetryPolicy(attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0)
POLICIES = {"night": NIGHT, "pipeline": PIPELINE, "speech": SPEECH, "vote": VOTE}


class Refused(Exception):
    status_code = 503


@dataclass(frozen=True)
class Response:
    """The narrow shape ``_structured_response`` reads off a provider answer."""

    content: str = ""
    tool_calls: list | None = None
    response_metadata: dict = field(default_factory=dict)


def test_an_unusable_answer_asks_for_a_repair_on_every_ladder():
    """The whole point: three of the four ladders used to resend it verbatim."""
    error = UnusableModelOutput("no json", code="response_no_tool_call")
    for name, policy in POLICIES.items():
        assert classify_failure(policy, error) is FailureClass.REPAIR, name
        assert retry_plan(policy, error, 1) == (KIND_REPAIR, 0.0), name


def test_a_transport_fault_is_resent_on_every_ladder_that_allows_it():
    error = TimeoutError("slow")
    for name in ("night", "pipeline", "speech"):
        policy = POLICIES[name]
        assert classify_failure(policy, error) is FailureClass.TRANSPORT, name
        assert retry_plan(policy, error, 1)[0] == KIND_TRANSPORT, name
    # The vote policy never retries an unclassified error.
    assert classify_failure(VOTE, error) is FailureClass.STOP
    assert retry_plan(VOTE, error, 1) is None


def test_an_unclassified_error_is_resent_only_where_the_policy_opts_in():
    assert NIGHT.retry_unknown_errors is True
    assert classify_failure(NIGHT, ValueError("boom")) is FailureClass.TRANSPORT
    assert classify_failure(VOTE, ValueError("boom")) is FailureClass.STOP


def test_a_refusal_outranks_the_other_sets_and_stops_after_one_wait():
    policy = RetryPolicy(
        attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0,
        retry_unknown_errors=True, refused_delay_seconds=20.0,
    )
    assert classify_failure(policy, Refused("no capacity")) is FailureClass.REFUSED
    kind, delay = retry_plan(policy, Refused("no capacity"), 1)
    assert (kind, delay) == (KIND_TRANSPORT, 20.0)
    # A second refusal is final: waiting again re-learns the same thing.
    assert retry_plan(policy, Refused("no capacity"), 2) is None


def test_the_attempt_budget_ends_the_plan_regardless_of_the_failure():
    error = UnusableModelOutput("no json", code="response_no_tool_call")
    assert retry_plan(VOTE, error, VOTE.attempts) is None
    with pytest.raises(ValueError):
        retry_plan(VOTE, error, 0)
    with pytest.raises(ValueError):
        retry_plan(VOTE, error, "1")  # type: ignore[arg-type]


def test_a_schema_rejection_asks_for_a_repair_too():
    """Pydantic's own rejection is in the repair set; ours now matches it."""
    error = ValidationError.from_exception_data(
        "payload", [{"type": "missing", "loc": ("action_type",), "input": None}],
    )
    assert classify_failure(NIGHT, error) is FailureClass.REPAIR


def test_a_tool_call_error_keeps_its_pass_through_classification():
    """``ToolCallError`` subclasses ``ActionValidationError``, not ValueError.

    The vote ladder decides for itself (its hand-written branches treat it as a
    validation failure and re-ask with a repair prompt), so classification here
    only needs to not claim it is something else.
    """
    error = ToolCallError("bad payload", code="action_payload_not_json")
    assert classify_failure(NIGHT, error) is FailureClass.TRANSPORT
    assert classify_failure(VOTE, error) is FailureClass.STOP
    assert classify_provider_error(error) == "model_invocation_error"


class TestStructuredResponseCodes:
    """Every parse rejection is an unusable answer, with a stable code."""

    def test_neither_a_tool_call_nor_json(self):
        with pytest.raises(UnusableModelOutput) as caught:
            LLMClient._structured_response(Response(content="no json here"), "vote")
        assert caught.value.code == "response_no_tool_call"

    def test_more_than_one_tool_call(self):
        response = Response(tool_calls=[{"name": "vote"}, {"name": "vote"}])
        with pytest.raises(UnusableModelOutput) as caught:
            LLMClient._structured_response(response, "vote")
        assert caught.value.code == "response_multiple_tool_calls"

    def test_an_unexpected_tool(self):
        response = Response(tool_calls=[{"name": "shoot", "args": {}}])
        with pytest.raises(UnusableModelOutput) as caught:
            LLMClient._structured_response(response, "vote")
        assert caught.value.code == "response_unexpected_tool"

    def test_arguments_that_are_not_an_object(self):
        # A string that parses to JSON but is not an object (a bare list).
        response = Response(tool_calls=[{"name": "vote", "args": "[1, 2]"}])
        with pytest.raises(UnusableModelOutput) as caught:
            LLMClient._structured_response(response, "vote")
        assert caught.value.code == "tool_args_not_object"

    def test_a_valid_tool_call_still_parses(self):
        response = Response(tool_calls=[{"name": "vote", "args": {"target_seat": 3}}])
        result = LLMClient._structured_response(response, "vote")
        assert result.payload == {"target_seat": 3}
        assert result.transport == "tool"

    def test_a_content_json_fallback_still_parses(self):
        result = LLMClient._structured_response(
            Response(content='{"target_seat": 3}'), "vote",
        )
        assert result.payload == {"target_seat": 3}
        assert result.transport == "json_fallback"

    def test_a_missing_required_field_names_the_field(self):
        result = LLMClient._structured_response(
            Response(content='{"action_type": "pass"}'), "vote",
        )
        with pytest.raises(UnusableModelOutput) as caught:
            LLMClient._require_schema_fields(
                result, {"required": ["action_type", "target_seat"]},
            )
        assert caught.value.code == "response_missing_fields"
        assert "target_seat" in str(caught.value)

    def test_a_non_string_required_list_is_a_programming_error(self):
        result = LLMClient._structured_response(
            Response(content='{"action_type": "pass"}'), "vote",
        )
        with pytest.raises(ValueError, match="must be strings"):
            LLMClient._require_schema_fields(result, {"required": [1]})

    def test_every_required_field_present_passes_through(self):
        result = LLMClient._structured_response(
            Response(content='{"action_type": "pass", "target_seat": null}'), "vote",
        )
        assert LLMClient._require_schema_fields(
            result, {"required": ["action_type", "target_seat"]},
        ) is result
