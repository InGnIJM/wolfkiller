from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage

from app.agents.output_parser import StrictCapabilityError
from app.agents.prompt_builder import PromptBuilder
from app.core.game_engine import GameEngine
from app.models.contracts import AcceptedAction, ActionCommand, ActionRequest
from app.models.game import Camp, GamePhase, GameState, PlayerState
from app.roles.base import BaseRole
from app.roles.registry import builtin_registry


class PromptBuilderStub:
    def get_system_prompt(self):
        return "system"

    def build_action_prompt(self, *args, **kwargs):
        return "choose an action"


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
    return next(
        request
        for request in builtin_registry.build_requests(
            state, {seat: object() for seat in state.players}, state.phase
        )
        if request.actor_seat == 1
    )


def make_state() -> GameState:
    state = GameState(game_id="base-action", phase=GamePhase.NIGHT, round_number=1)
    state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", Camp.WEREWOLF.value),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }
    return state


def action_tool_response(*, target=2):
    return AIMessage(
        content="private reasoning must not be persisted",
        tool_calls=[{
            "name": "werewolf_kill",
            "args": {"action_type": "kill", "target_seat": target, "reasoning": "x"},
            "id": "call_1",
        }],
    )


@pytest.mark.asyncio
async def test_request_action_uses_strict_tool_and_returns_validator_accepted_action():
    state = make_state()
    client = ClientStub([action_tool_response()])
    role = BaseRole(1, "wolf-killer-werewolf", PromptBuilderStub(), client)

    accepted = await role.request_action(state, object(), request_for(state))

    assert accepted.command.action_type == "kill"
    assert accepted.command.target_seat == 2
    assert client.contracts == [accepted.request.contract]
    assert state.accepted_action_keys == {accepted.request.idempotency_key}


@pytest.mark.asyncio
async def test_request_action_falls_back_to_strict_json_only_for_capability_error():
    state = make_state()
    client = ClientStub(
        [StrictCapabilityError("strict schema is unsupported")],
        [AIMessage(content='{"action_type":"kill","target_seat":2,"reasoning":"x"}')],
    )
    role = BaseRole(1, "wolf-killer-werewolf", PromptBuilderStub(), client)

    accepted = await role.request_action(state, object(), request_for(state))

    assert accepted.command.target_seat == 2
    assert len(client.strict_model.messages) == 1
    assert len(client.json_model.messages) == 1


@pytest.mark.asyncio
async def test_request_action_retries_invalid_strict_action_once_with_generic_correction():
    state = make_state()
    client = ClientStub([action_tool_response(target=None), action_tool_response(target=2)])
    role = BaseRole(1, "wolf-killer-werewolf", PromptBuilderStub(), client)

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
    role = BaseRole(1, "wolf-killer-werewolf", PromptBuilderStub(), client)

    accepted = await role.request_action(state, object(), request_for(state))

    assert accepted.command.action_type == "pass"
    assert accepted.command.target_seat is None
    assert len(client.strict_model.messages) == 2


@pytest.mark.asyncio
async def test_request_action_does_not_downgrade_network_errors_to_json():
    state = make_state()
    client = ClientStub([ConnectionError("network unavailable")])
    role = BaseRole(1, "wolf-killer-werewolf", PromptBuilderStub(), client)

    with pytest.raises(ConnectionError, match="network unavailable"):
        await role.request_action(state, object(), request_for(state))

    assert client.json_model.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("has_antidote", "has_poison", "action_type", "target_seat", "expected_actions", "target_visible"),
    [
        (False, True, "poison", 2, ("poison", "pass"), False),
        (True, False, "save", 3, ("save", "pass"), True),
    ],
)
async def test_witch_request_action_sends_only_issued_capabilities_and_private_target(
    tmp_path,
    has_antidote: bool,
    has_poison: bool,
    action_type: str,
    target_seat: int,
    expected_actions: tuple[str, ...],
    target_visible: bool,
):
    client = ClientStub([
        AIMessage(
            content="",
            tool_calls=[{
                "name": "witch_action",
                "args": {
                    "action_type": action_type,
                    "target_seat": target_seat,
                    "reasoning": "x",
                },
                "id": "call_1",
            }],
        ),
    ])
    role = BaseRole(1, "wolf-killer-witch", PromptBuilder(), client)
    engine = GameEngine(
        game_id="base-witch-prompt", roles={1: role}, data_dir=str(tmp_path)
    )
    engine.state.phase = GamePhase.NIGHT
    engine.state.round_number = 1
    engine.state.players = {
        1: PlayerState(
            1,
            "wolf-killer-witch",
            Camp.GOOD.value,
            has_antidote=has_antidote,
            has_poison=has_poison,
        ),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
        3: PlayerState(3, "wolf-killer-villager", Camp.GOOD.value),
    }
    engine.state.last_wolf_kill_target = 3

    accepted = await engine._request_night_action(1, "witch_action")

    prompt = client.strict_model.messages[0][1].content
    assert accepted.request.contract.action_types == expected_actions
    assert client.contracts == [accepted.request.contract]
    assert ("今晚狼人刀了 3 号玩家" in prompt) is target_visible
    assert ("狼人刀口（银水信息）：3号玩家" in prompt) is target_visible
    assert ('"action_type":"save"' in prompt) is target_visible


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


class RequestingWolfRole:
    async def request_action(self, state, conversation_log, request):
        self.request = request
        return AcceptedAction(
            request=request,
            command=ActionCommand(
                action_type="kill", target_seat=2, reasoning="x"
            ),
        )


@pytest.mark.asyncio
async def test_engine_werewolf_action_uses_role_strict_action_request():
    role = RequestingWolfRole()
    engine = GameEngine(game_id="strict-wolf", roles={1: role})
    engine.state.phase = GamePhase.NIGHT
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", Camp.WEREWOLF.value),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }

    actions, target = await engine.werewolf_kill([1])

    assert [(action.action_type, action.target_seat) for action in actions] == [("kill", 2)]
    assert target == 2
    assert role.request.contract.contract_id == "werewolf_kill"


class RequestingSeerRole:
    async def request_action(self, state, conversation_log, request):
        self.request = request
        return AcceptedAction(
            request=request,
            command=ActionCommand(
                action_type="check", target_seat=2, reasoning="x"
            ),
        )


@pytest.mark.asyncio
async def test_engine_seer_action_uses_role_strict_action_request():
    role = RequestingSeerRole()
    engine = GameEngine(game_id="strict-seer", roles={1: role})
    engine.state.phase = GamePhase.NIGHT
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-seer", Camp.GOOD.value),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }

    action = await engine.seer_check(1)

    assert (action.action_type, action.target_seat) == ("check", 2)
    assert role.request.contract.contract_id == "seer_check"


class RequestingWitchRole:
    async def request_action(self, state, conversation_log, request):
        self.request = request
        return AcceptedAction(
            request=request,
            command=ActionCommand(
                action_type="save", target_seat=2, reasoning="x"
            ),
        )


@pytest.mark.asyncio
async def test_engine_witch_save_uses_role_strict_action_request():
    role = RequestingWitchRole()
    engine = GameEngine(game_id="strict-witch", roles={1: role})
    engine.state.phase = GamePhase.NIGHT
    engine.state.players = {
        1: PlayerState(
            1, "wolf-killer-witch", Camp.GOOD.value, has_antidote=True
        ),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }
    engine.state.last_wolf_kill_target = 2

    used = await engine.witch_save(1, 2)

    assert used is True
    assert role.request.contract.contract_id == "witch_action"
    assert role.request.idempotency_key.endswith(":witch_action")


class RequestingPoisonRole:
    async def request_action(self, state, conversation_log, request):
        self.request = request
        return AcceptedAction(
            request=request,
            command=ActionCommand(
                action_type="poison", target_seat=2, reasoning="x"
            ),
        )


@pytest.mark.asyncio
async def test_engine_witch_poison_uses_role_strict_action_request():
    role = RequestingPoisonRole()
    engine = GameEngine(game_id="strict-poison", roles={1: role})
    engine.state.phase = GamePhase.NIGHT
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-witch", Camp.GOOD.value, has_poison=True),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }

    action = await engine.witch_poison(1, None)

    assert (action.action_type, action.target_seat) == ("poison", 2)
    assert role.request.contract.contract_id == "witch_action"
    assert role.request.idempotency_key.endswith(":witch_action")


@pytest.mark.asyncio
async def test_engine_witch_operations_share_one_idempotency_key():
    class RecordingWitchRole:
        def __init__(self):
            self.requests = []

        async def request_action(self, state, conversation_log, request):
            self.requests.append(request)
            state.accepted_action_keys.add(request.idempotency_key)
            return AcceptedAction(
                request=request,
                command=ActionCommand(
                    action_type="pass", target_seat=None, reasoning="x"
                ),
            )

    role = RecordingWitchRole()
    engine = GameEngine(game_id="strict-witch-key", roles={1: role})
    engine.state.phase = GamePhase.NIGHT
    engine.state.players = {
        1: PlayerState(
            1, "wolf-killer-witch", Camp.GOOD.value,
            has_antidote=True, has_poison=True,
        ),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }

    assert await engine.witch_save(1, 2) is False
    assert await engine.witch_poison(1, 2) is None

    assert [request.idempotency_key for request in role.requests] == [
        "0:night:1:witch_action"
    ]
    assert engine.state.players[1].has_poison is True


class RequestingHunterRole:
    async def request_action(self, state, conversation_log, request):
        self.request = request
        return AcceptedAction(
            request=request,
            command=ActionCommand(
                action_type="shoot", target_seat=2, reasoning="x"
            ),
        )


@pytest.mark.asyncio
async def test_engine_hunter_shot_uses_role_strict_action_request_at_night():
    role = RequestingHunterRole()
    engine = GameEngine(game_id="strict-hunter", roles={1: role})
    engine.state.phase = GamePhase.NIGHT
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-hunter", Camp.GOOD.value, has_gun=True),
        2: PlayerState(2, "wolf-killer-villager", Camp.GOOD.value),
    }

    resolve_hunter_shoot = MagicMock(
        wraps=engine.action_resolver.resolve_hunter_shoot
    )
    engine.action_resolver.resolve_hunter_shoot = resolve_hunter_shoot

    death = await engine.hunter_shoot(1)

    assert death.player_seat == 2
    assert role.request.contract.contract_id == "hunter_shoot"
    assert role.request.phase == GamePhase.NIGHT
    assert isinstance(resolve_hunter_shoot.call_args.args[2], AcceptedAction)
