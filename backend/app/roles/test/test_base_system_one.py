"""System One (Jev) ballots: the decision-model path through request_action.

A Jev client answers ``state`` + ``choice`` questions instead of generating
JSON, so the ladder hands it a dedicated invoke whose options are the same
``eligible_exile_targets`` the validator enforces — a picked seat can never
be illegal, and everything else still rides the shared retry/fallback
machinery.
"""

from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, InternalServerError

from app.agents.prompt_builder import PromptBuilder
from app.models.game import PlayerState
from app.roles.base import BaseRole

from .test_base import (
    TelemetryLogger, make_log, make_request, make_state,
)


class SystemOneClient:
    is_system_one = True
    action_timeout_seconds = 0.1
    action_retry_timeout_seconds = 0.1
    action_final_retry_timeout_seconds = 0.1
    action_retry_backoff_seconds = 0.0
    action_refused_backoff_seconds = 0.0

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def ainvoke_system_one(self, state, questions):
        self.calls.append((state, questions))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _decision(choice, confidence=0.9, probabilities=None):
    # Shaped like SystemOneResponse without importing the provider layer
    # (roles must stay transport-agnostic, the boundary test enforces it).
    answer = {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities or {choice: confidence},
    }
    if confidence is not None:
        answer["confidence"] = confidence
    return SimpleNamespace(answers={"exile_vote": answer}, usage=None)


@pytest.mark.asyncio
async def test_system_one_vote_picks_a_seat_from_the_legal_options():
    client = SystemOneClient([_decision("2")])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(), make_request(state))

    assert accepted.command.action_type == "vote"
    assert accepted.command.target_seat == 2
    # The question offered exactly the seats the validator would accept —
    # the same eligible_exile_targets set — plus abstain.
    from app.core.vote_service import eligible_exile_targets
    criteria = client.calls[0][1]["exile_vote"]["criteria"]
    assert set(criteria) == {
        str(seat) for seat in eligible_exile_targets(state)
    } | {"abstain"}
    # The whole prompt becomes the decision state, system block included.
    assert "current-round-evidence" in client.calls[0][0]
    assert "reasoning" in accepted.command.reasoning or "JEV" in accepted.command.reasoning


@pytest.mark.asyncio
async def test_system_one_answer_without_confidence_gets_plain_reasoning():
    client = SystemOneClient([_decision("2", confidence=None)])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(), make_request(state))

    assert accepted.command.target_seat == 2
    assert accepted.command.reasoning == "JEV 决策"


@pytest.mark.asyncio
async def test_system_one_abstain_maps_to_the_fallback_action():
    client = SystemOneClient([_decision("abstain")])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(), make_request(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.command.target_seat is None


@pytest.mark.asyncio
async def test_system_one_timeout_retries_then_falls_back_to_abstain():
    client = SystemOneClient([
        APITimeoutError(request=None),
        APITimeoutError(request=None),
        APITimeoutError(request=None),
    ])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(
        state, make_log(logger), make_request(state),
    )

    assert accepted.command.action_type == "abstain"
    assert accepted.technical_failure_code == "request_timeout"
    assert len(client.calls) == 3
    assert logger.technical_abstentions


@pytest.mark.asyncio
async def test_system_one_transient_error_retries_the_same_decision():
    client = SystemOneClient([
        InternalServerError(
            "boom",
            response=httpx.Response(
                500, request=httpx.Request("POST", "https://x"),
            ),
            body=None,
        ),
        _decision("2"),
    ])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(), make_request(state))

    assert accepted.command.target_seat == 2
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_system_one_choice_outside_the_options_is_retried():
    client = SystemOneClient([
        _decision("seat-nine"),  # not a declared option at all
        _decision("2"),
    ])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(), make_request(state))

    assert accepted.command.target_seat == 2
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_system_one_vote_uses_the_dedicated_vote_client():
    from types import SimpleNamespace

    seat_client = SimpleNamespace(
        is_system_one=False,
        supports_strict_actions=False,
        action_timeout_seconds=0.1,
        action_retry_timeout_seconds=0.1,
        action_final_retry_timeout_seconds=0.1,
        action_retry_backoff_seconds=0.0,
        action_refused_backoff_seconds=0.0,
        get_action_model=lambda: None,
    )
    vote_client = SystemOneClient([_decision("2")])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), seat_client)
    role.vote_llm_client = vote_client

    accepted = await role.request_action(state, make_log(), make_request(state))

    assert accepted.command.target_seat == 2
    assert len(vote_client.calls) == 1
