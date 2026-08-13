from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage

from app.agents.output_parser import StrictCapabilityError
from app.core.game_engine import GameEngine, VOTE_CONTRACT
from app.models.contracts import AcceptedAction, ActionCommand, ActionRequest
from app.models.game import Camp, GamePhase, GameState, PlayerState
from app.roles.base import BaseRole


class PromptBuilderStub:
    def get_system_prompt(self):
        return "system"

    def build_vote_prompt(self, *args, **kwargs):
        return "choose a vote"


class ModelStub:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class ClientStub:
    def __init__(self, strict_responses, json_responses=()):
        self.strict_model = ModelStub(strict_responses)
        self.json_model = ModelStub(json_responses)
        self.contracts = []

    def get_model_with_action_tool(self, contract):
        self.contracts.append(contract)
        return self.strict_model

    def get_model(self):
        return self.json_model


def request_for(state: GameState) -> ActionRequest:
    return ActionRequest(
        actor_seat=1,
        role_id="wolf-killer-villager",
        contract=VOTE_CONTRACT,
        phase=GamePhase.VOTE_CASTING,
        round_id=state.round_number,
        idempotency_key="1:vote_casting:1:1:exile_vote",
    )


def make_state() -> GameState:
    state = GameState(game_id="base-action", phase=GamePhase.VOTE_CASTING, round_number=1)
    state.players = {
        1: PlayerState(1, "wolf-killer-villager", Camp.GOOD.value),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }
    return state


def action_tool_response(*, target=2):
    return AIMessage(
        content="private reasoning must not be persisted",
        tool_calls=[{
            "name": "exile_vote",
            "args": {"action_type": "vote", "target_seat": target, "reasoning": "x"},
            "id": "call_1",
        }],
    )


@pytest.mark.asyncio
async def test_request_action_uses_strict_tool_and_returns_validator_accepted_action():
    state = make_state()
    client = ClientStub([action_tool_response()])
    role = BaseRole(1, "wolf-killer-villager", PromptBuilderStub(), client)

    accepted = await role.request_action(state, object(), request_for(state))

    assert accepted.command.action_type == "vote"
    assert accepted.command.target_seat == 2
    assert client.contracts == [accepted.request.contract]
    assert state.accepted_action_keys == {accepted.request.idempotency_key}


@pytest.mark.asyncio
async def test_request_action_falls_back_to_strict_json_only_for_capability_error():
    state = make_state()
    client = ClientStub(
        [StrictCapabilityError("strict schema is unsupported")],
        [AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}')],
    )
    role = BaseRole(1, "wolf-killer-villager", PromptBuilderStub(), client)

    accepted = await role.request_action(state, object(), request_for(state))

    assert accepted.command.target_seat == 2
    assert len(client.strict_model.messages) == 1
    assert len(client.json_model.messages) == 1


@pytest.mark.asyncio
async def test_request_action_retries_invalid_strict_action_once_with_generic_correction():
    state = make_state()
    client = ClientStub([action_tool_response(target=None), action_tool_response(target=2)])
    role = BaseRole(1, "wolf-killer-villager", PromptBuilderStub(), client)

    accepted = await role.request_action(state, object(), request_for(state))

    assert accepted.command.target_seat == 2
    assert len(client.strict_model.messages) == 2
    correction = client.strict_model.messages[1][-1].content
    assert "previous action was invalid" in correction
    assert "2" not in correction
    assert "werewolf" not in correction


@pytest.mark.asyncio
async def test_request_action_uses_safe_fallback_after_two_invalid_actions():
    state = make_state()
    client = ClientStub([action_tool_response(target=None), action_tool_response(target=None)])
    role = BaseRole(1, "wolf-killer-villager", PromptBuilderStub(), client)

    accepted = await role.request_action(state, object(), request_for(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.command.target_seat is None
    assert len(client.strict_model.messages) == 2


@pytest.mark.asyncio
async def test_request_action_does_not_downgrade_network_errors_to_json():
    state = make_state()
    client = ClientStub([ConnectionError("network unavailable")])
    role = BaseRole(1, "wolf-killer-villager", PromptBuilderStub(), client)

    with pytest.raises(ConnectionError, match="network unavailable"):
        await role.request_action(state, object(), request_for(state))

    assert client.json_model.messages == []


def test_action_transport_does_not_expose_thought_recording_or_content_preview():
    source = open(BaseRole.__module__.replace(".", "/") + ".py", encoding="utf-8").read()

    assert not hasattr(BaseRole, "_record_thought")
    assert "content_preview" not in source


class RequestingVoteRole:
    async def request_action(self, state, conversation_log, request):
        self.request = request
        return AcceptedAction(
            request=request,
            command=ActionCommand(
                action_type="vote", target_seat=2, reasoning="x"
            ),
        )


@pytest.mark.asyncio
async def test_engine_vote_uses_role_strict_action_request_instead_of_legacy_raw_vote():
    role = RequestingVoteRole()
    engine = GameEngine(game_id="strict-vote", roles={1: role})
    engine.state.phase = GamePhase.VOTE_CASTING
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-villager", Camp.GOOD.value),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }

    vote = await engine.vote(1)

    assert vote.target_seat == 2
    assert role.request.contract.contract_id == "exile_vote"
