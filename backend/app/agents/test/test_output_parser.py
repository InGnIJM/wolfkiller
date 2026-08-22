import pytest
from langchain_core.messages import AIMessage

from app.agents.output_parser import OutputParser, _schema_failure_code
from app.core.action_validator import ActionValidationError
from app.core.game_engine import VOTE_CONTRACT


@pytest.mark.parametrize(
    ("payload", "failure_code"),
    [
        ({"target_seat": 1, "reasoning": "x"}, "action_payload_missing_field"),
        (
            {
                "action_type": "vote", "target_seat": 1,
                "reasoning": "x", "extra": True,
            },
            "action_payload_extra_field",
        ),
        (
            {"action_type": "vote", "target_seat": "1", "reasoning": "x"},
            "action_payload_wrong_type",
        ),
    ],
)
def test_schema_validation_exposes_stable_specific_failure_code(payload, failure_code):
    with pytest.raises(ActionValidationError) as caught:
        OutputParser().parse_action_payload(payload, VOTE_CONTRACT)

    assert caught.value.code == failure_code


def test_non_text_json_response_has_stable_failure_code():
    error = ActionValidationError(
        "JSON action response must be text", code="action_response_not_text"
    )

    assert error.code == "action_response_not_text"


def test_unknown_schema_error_has_stable_generic_failure_code():
    assert _schema_failure_code({"future_constraint"}) == "action_payload_schema_invalid"


def test_strict_response_missing_tool_has_stable_failure_code():
    with pytest.raises(ActionValidationError) as caught:
        OutputParser().parse_strict_action_response(AIMessage(content="{}"), VOTE_CONTRACT)

    assert caught.value.code == "strict_tool_missing"


def test_strict_response_multiple_tools_has_stable_failure_code():
    response = AIMessage(
        content="",
        tool_calls=[
            {"name": "exile_vote", "args": {}, "id": "call-1", "type": "tool_call"},
            {"name": "exile_vote", "args": {}, "id": "call-2", "type": "tool_call"},
        ],
    )

    with pytest.raises(ActionValidationError) as caught:
        OutputParser().parse_strict_action_response(response, VOTE_CONTRACT)

    assert caught.value.code == "strict_tool_count_invalid"


def test_strict_response_wrong_tool_has_stable_failure_code():
    response = AIMessage(
        content="",
        tool_calls=[{"name": "wrong", "args": {}, "id": "call-1", "type": "tool_call"}],
    )

    with pytest.raises(ActionValidationError) as caught:
        OutputParser().parse_strict_action_response(response, VOTE_CONTRACT)

    assert caught.value.code == "strict_tool_name_mismatch"
