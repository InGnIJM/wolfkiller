"""The hidden wolf's lone kill must reach a model.

``hidden_wolf_kill`` and the pack's ``werewolf_kill`` share one schedule point,
``NIGHT_WOLF_VOTE``. The engine used to answer *every* request at that point out
of ``NightDirector.collected_vote`` — and only pack members ever balloted, so a
``no_wolf_pack`` role got the contract's safe fallback (``pass``) every night.
The lone kill was dead code: nights 5 and 6 of a real archive show the request
accepted, no model call, no kill and a "平安夜" announcement, while the role's own
instructions promise it a nightly kill once the pack is gone.

These tests pin the routing both ways, because the seal depends on it: the
Nightmare's fear stops the whole pack by leaving ``record_votes(())`` empty, and
that must keep degrading to ``pass`` rather than waking a model up.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agents.output_parser import extract_json_object
from app.core.context_projector import ContextProjector
from app.core.night_flow import NightDirector, WolfVote
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import IssuedActionRequest, SchedulePoint
from app.roles.registry import builtin_registry
from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.services.game_service import GameService

GAME = "g"
GARGOYLE = "wolf-killer-gargoyle"
WEREWOLF = "wolf-killer-werewolf"
VILLAGER = "wolf-killer-villager"
SEER = "wolf-killer-seer"


def state(*, pack_alive: bool) -> GameState:
    """One hidden wolf, one good seat per camp, and a pack that may be gone."""
    game = GameState(game_id=GAME, phase=GamePhase.NIGHT, round_number=5, config=GameConfig(
        role_counts={GARGOYLE: 1, WEREWOLF: 1, VILLAGER: 1, SEER: 1},
    ))
    game.players = {
        1: PlayerState(1, WEREWOLF, "werewolf", is_alive=pack_alive),
        2: PlayerState(2, GARGOYLE, "werewolf"),
        3: PlayerState(3, VILLAGER, "good"),
        4: PlayerState(4, SEER, "good"),
    }
    return game


def request_for(role_id: str, contract_id: str, seat: int = 2) -> IssuedActionRequest:
    spec = builtin_registry.freeze().require(role_id)
    contract = next(item for item in spec.contracts if item.contract_id == contract_id)
    return IssuedActionRequest(seat, role_id, contract, 0, 5, "night", "w", "k")


def establish_resources(game: GameState) -> None:
    """``Scheduler.issue`` is the only place role resources are set up.

    ``no_wolf_pack`` travels as a resource, so anything that reads pack
    membership has to run after this — which in a real night is guaranteed,
    because requests are issued before the provider is ever asked.
    """
    from app.core.role_runtime import initialize_role_resources

    specs = builtin_registry.freeze().specs
    initialize_role_resources(game, specs, "a" * 64)


def service_for(game: GameState, pcm, llm) -> GameService:
    service = GameService(WSManager(), EventBus())
    service._engines[GAME] = SimpleNamespace(
        state=game,
        conversation_log=MagicMock(**{
            "get_conversations_for_role.return_value": [],
        }),
        announce_actor_blocking=MagicMock(),
        _rng=random.Random(0),
    )
    return service


class TestPackBallotRouting:
    def test_only_the_kill_point_is_routed_to_the_director(self) -> None:
        service = GameService(WSManager(), EventBus())
        context = MagicMock(game_id=GAME)
        other = request_for(GARGOYLE, "gargoyle_check")
        assert service._pack_ballot_point(other, context) is False

    def test_without_a_live_state_the_historical_hand_off_stands(self) -> None:
        service = GameService(WSManager(), EventBus())
        context = MagicMock(game_id=GAME)
        kill = request_for(WEREWOLF, "werewolf_kill", seat=1)
        assert service._pack_ballot_point(kill, context) is True

    @pytest.mark.parametrize("pack_alive", [True, False])
    def test_pack_membership_decides_the_hand_off(self, pack_alive) -> None:
        """The predicate reads the ``no_wolf_pack`` marker, not the seat's fate.

        Membership is what decides who the director can answer for. A dead pack
        wolf is never issued a request at all, so its seat answering True here
        costs nothing.
        """
        game = state(pack_alive=pack_alive)
        establish_resources(game)
        service = service_for(game, None, None)
        context = MagicMock(game_id=GAME)
        assert service._pack_ballot_point(
            request_for(WEREWOLF, "werewolf_kill", seat=1), context,
        ) is True
        assert service._pack_ballot_point(
            request_for(GARGOYLE, "hidden_wolf_kill"), context,
        ) is False


class TestProviderRouting:
    def _provider(self, game, *, vote=None):
        establish_resources(game)
        snapshot = builtin_registry.freeze()
        renderer = MagicMock()
        renderer.render.return_value = "prompt"
        llm = MagicMock()
        payload = extract_json_object('{"action_type":"kill","target_seat":3,"reasoning":"lone"}')
        llm.invoke_action.return_value = SimpleNamespace(payload=payload)
        director = NightDirector(snapshot, lambda messages: None)
        if vote is not None:
            director.record_votes(vote)
        service = service_for(game, None, llm)
        provider = service._command_provider(snapshot, renderer, lambda seat: llm, director)
        return provider, llm

    def test_the_lone_kill_is_asked_of_the_model(self) -> None:
        provider, llm = self._provider(state(pack_alive=False))
        command = provider(
            request_for(GARGOYLE, "hidden_wolf_kill"), MagicMock(game_id=GAME), 0,
        )
        llm.invoke_action.assert_called_once()
        assert command.action_type == "kill"
        assert command.target_seat == 3

    def test_a_pack_wolf_still_answers_from_the_collected_ballot(self) -> None:
        provider, llm = self._provider(
            state(pack_alive=True), vote=(WolfVote(1, "kill", 4, "刀4号"),),
        )
        command = provider(
            request_for(WEREWOLF, "werewolf_kill", seat=1),
            MagicMock(game_id=GAME, facts={}), 0,
        )
        assert (command.action_type, command.target_seat) == ("kill", 4)
        llm.invoke_action.assert_not_called()

    def test_a_sealed_pack_still_degrades_to_pass(self) -> None:
        """The Nightmare's seal silences the camp by leaving the ballot empty."""
        provider, llm = self._provider(state(pack_alive=True), vote=())
        command = provider(
            request_for(WEREWOLF, "werewolf_kill", seat=1),
            MagicMock(game_id=GAME, facts={}), 0,
        )
        assert command.action_type == "pass"
        llm.invoke_action.assert_not_called()


class TestLoneKillThroughTheScheduler:
    def test_a_lone_kill_becomes_a_wolf_kill_death(self) -> None:
        """The whole point: the request must end as damage, not as a pass."""
        from app.core.action_resolver import ActionResolver
        from app.core.action_validator import ActionValidator
        from app.core.effect_applier import EffectApplier
        from app.core.scheduler import Scheduler

        game = state(pack_alive=False)
        snapshot = builtin_registry.freeze()
        renderer = MagicMock()
        renderer.render.return_value = "prompt"
        llm = MagicMock()
        llm.invoke_action.return_value = SimpleNamespace(
            payload=extract_json_object(
                '{"action_type":"kill","target_seat":3,"reasoning":"lone"}'
            ),
        )
        director = NightDirector(snapshot, lambda messages: None)
        director.record_votes(())          # what the engine records with no pack
        service = service_for(game, None, llm)
        provider = service._command_provider(snapshot, renderer, lambda seat: llm, director)
        scheduler = Scheduler(
            snapshot, ContextProjector(), ActionValidator(), ActionResolver(),
            EffectApplier(), provider,
        )
        issued = scheduler.issue(game, SchedulePoint.NIGHT_WOLF_VOTE, snapshot)
        contracts = {(item.actor_seat, item.contract.contract_id) for item in issued}
        assert (2, "hidden_wolf_kill") in contracts
        assert (1, "werewolf_kill") not in contracts

        result = scheduler.run_point(game, SchedulePoint.NIGHT_WOLF_VOTE)
        kills = [
            event for commit in result.commits for event in commit.events
            if event["event_type"] == "WEREWOLF_KILL"
        ]
        assert kills, "the lone kill produced no WEREWOLF_KILL event"
        assert kills[0]["payload"]["target_seat"] == 3
        pending = getattr(game, "_pipeline_runtime").pending_damage
        assert [item["target"] for item in pending] == [3]
        assert pending[0]["cause"] == "wolf_kill"
