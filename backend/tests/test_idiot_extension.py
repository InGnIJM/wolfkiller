"""Idiot: a good god that survives the exile vote by flipping its card.

Mirrors ``test_guard_extension.py``: pure hook tests, registry checks and a
pipeline/engine integration that proves the exile verdict window works
without any role-specific engine code.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, derive_effect_id, role_resource_view
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.core.vote_service import eligible_exile_targets, eligible_exile_voters
from app.models.actions import VoteAction
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import ActionContext, EffectKind, SchedulePoint
from app.roles.idiot import IDIOT_SPEC, Idiot, idiot_applicable, react_idiot_flip
from app.roles.registry import builtin_registry

EVENT_ID = "event:" + "7" * 16


def context(*, flip: int = 1, alive: bool = True, target: int = 1, reason: str = "exile",
            identity: bool = True) -> ActionContext:
    contract = IDIOT_SPEC.contracts[0]
    facts: dict[str, object] = {"alive_seats": (1, 2, 3)}
    if identity:
        facts["actor_identity"] = {"seat": 1, "role_id": IDIOT_SPEC.role_id, "camp_id": "good"}
    return ActionContext(
        "g", 3, facts, config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=2, phase="vote_resolution", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=IDIOT_SPEC.role_id, actor_alive=alive, resources={"flip": flip},
        action_key="idiot:1", source_event_id=EVENT_ID,
        trigger_event={"event_id": EVENT_ID, "type": "EXILE_PENDING",
                       "target_seat": target, "cause": reason},
        trigger_reason=reason,
    )


def test_spec_declares_exile_verdict_reaction_with_one_flip() -> None:
    contract = IDIOT_SPEC.contracts[0]
    assert IDIOT_SPEC.role_id == "wolf-killer-idiot"
    assert IDIOT_SPEC.camp_id == "good" and IDIOT_SPEC.max_count == 1
    assert IDIOT_SPEC.initial_resources == {"flip": 1}
    assert contract.contract_id == "idiot_flip"
    assert contract.schedule_point is SchedulePoint.EXILE_VERDICT
    assert contract.response_event_types == {"EXILE_PENDING"}
    assert contract.response_reasons == {"exile"}
    assert contract.react is react_idiot_flip and contract.resolve is None
    assert contract.per_window_limit == contract.per_game_limit == 1
    assert "Idiot" in IDIOT_SPEC.instructions and "sheriff" not in IDIOT_SPEC.instructions.lower()


def test_applicable_only_for_own_pending_exile_with_a_flip_left() -> None:
    assert idiot_applicable(context()) is True
    assert idiot_applicable(context(flip=0)) is False
    assert idiot_applicable(context(alive=False)) is False
    assert idiot_applicable(context(target=2)) is False
    assert idiot_applicable(replace(context(), trigger_reason="poison")) is False
    assert idiot_applicable(replace(context(), source_event_id=None, trigger_event=None)) is False


def test_flip_effects_consume_card_grant_statuses_and_publish_reveal() -> None:
    ctx = context()
    effects = react_idiot_flip(ctx)
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.ADD_STATUS, EffectKind.ADD_STATUS,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert [effect.effect_id for effect in effects] == [
        derive_effect_id("idiot:1", index) for index in range(1, 6)
    ]
    assert all(effect.source_event_id == EVENT_ID and effect.expected_revision == 3 for effect in effects)
    assert effects[0].payload == {"target": 1, "resource": "flip", "amount": 1}
    assert effects[1].payload == {"target": 1, "status": "no_vote"}
    assert effects[2].payload == {"target": 1, "status": "exile_immune"}
    assert effects[3].payload == {"event_type": "PLAYER_REVEALED", "payload": {
        "seat_number": 1, "role": "wolf-killer-idiot", "camp": "good"}}
    assert effects[4].payload == {"event_type": "EXILE_CANCELLED", "payload": {
        "target_seat": 1, "round_number": 2}}
    assert effects[3].visibility == effects[4].visibility == ("PUBLIC",)


def test_flip_falls_back_to_context_identity_and_stays_silent_when_spent() -> None:
    reveal = react_idiot_flip(context(identity=False))[3]
    assert reveal.payload["payload"] == {"seat_number": 1, "role": "wolf-killer-idiot", "camp": ""}
    assert react_idiot_flip(context(flip=0)) == ()


def test_idiot_is_registered_in_both_registries() -> None:
    spec = builtin_registry.freeze().require("wolf-killer-idiot")
    assert spec is IDIOT_SPEC
    roles = builtin_registry.create_roles(
        {"wolf-killer-idiot": 1, "wolf-killer-werewolf": 1}, 2, object(), lambda seat: object(),
    )
    assert {type(role) for role in roles.values()} == {Idiot, type(roles[next(
        seat for seat, role in roles.items() if role.role_name == "wolf-killer-werewolf")])}
    assert any(isinstance(role, Idiot) for role in roles.values())


def _engine(tmp_path, game_id: str = "idiot-flip") -> GameEngine:
    def provider(request, projected, attempt):  # pragma: no cover - idiot never asks a model
        raise AssertionError("the flip must not consult the model")

    scheduler = Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )
    engine = GameEngine(game_id, event_bus=EventBus(), pipeline_scheduler=scheduler,
                        data_dir=str(tmp_path))
    engine.state = GameState(
        game_id, phase=GamePhase.VOTE_RESOLUTION, round_number=2,
        players={
            1: PlayerState(1, "wolf-killer-idiot", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
            3: PlayerState(3, "wolf-killer-werewolf", "werewolf"),
            4: PlayerState(4, "wolf-killer-villager", "good"),
        },
    )
    engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
    engine.give_last_words = AsyncMock()
    # The first night's schedule points normally seed every seat's resources.
    initialize_role_resources(engine.state, scheduler.registry.specs, scheduler.registry.digest)
    return engine


@pytest.mark.asyncio
async def test_exile_verdict_flips_idiot_instead_of_exiling(tmp_path) -> None:
    engine = _engine(tmp_path)

    assert await engine._apply_exile(1) is True

    player = engine.state.players[1]
    assert player.is_alive is True and player.revealed_role == "wolf-killer-idiot"
    assert engine.state.death_history == []
    assert role_resource_view(engine.state, 1) == {"flip": 0}
    assert eligible_exile_voters(engine.state) == frozenset({2, 3, 4})
    assert eligible_exile_targets(engine.state) == frozenset({2, 3, 4})
    engine.give_last_words.assert_not_awaited()
    messages = [record.content for record in engine.conversation_log.get_public()]
    assert any("翻牌" in text and "Idiot" in text for text in messages)

    # Resuming the same verdict replays the journal without a second flip.
    assert await engine._apply_exile(1) is True
    assert role_resource_view(engine.state, 1) == {"flip": 0}
    assert engine.state.players[1].is_alive is True


@pytest.mark.asyncio
async def test_exile_verdict_exiles_other_seats_normally(tmp_path) -> None:
    engine = _engine(tmp_path)

    assert await engine._apply_exile(2) is False

    assert engine.state.players[2].is_alive is False
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(2, "exile")]
    engine.give_last_words.assert_awaited_once_with(2, "exile", 2)
    assert role_resource_view(engine.state, 1) == {"flip": 1}


@pytest.mark.asyncio
async def test_vote_resolution_records_cancelled_exile_and_moves_on(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.state.votes = [VoteAction(voter_seat=seat, target_seat=1) for seat in (2, 3, 4)]

    await engine._execute_vote_resolution()

    assert engine.state.players[1].is_alive is True
    assert engine.sm.get_state() is GamePhase.NIGHT
    results = [r.content for r in engine.conversation_log.get_public() if r.content.startswith("投票结果")]
    assert results == ["投票结果：1号玩家得票最高，但翻牌免于出局。"]
    engine.give_last_words.assert_not_awaited()
    import json
    records = [json.loads(line) for line in
               (tmp_path / "games" / "idiot-flip" / "game.log").read_text("utf-8").splitlines()]
    vote_results = [r for r in records if r["operation"] == "vote_result"]
    assert len(vote_results) == 1 and vote_results[0]["data"]["exiled"] is None


@pytest.mark.asyncio
async def test_tiebreak_resolution_records_cancelled_exile(tmp_path) -> None:
    engine = _engine(tmp_path, "idiot-tiebreak")
    alive = set(engine.state.alive_players())
    engine.state.is_tiebreak = True
    engine.state.vote_round = 2
    engine.state.supplemental_speakers = set(alive)
    engine.state.voted_seats = set(alive)
    engine.state.votes = [VoteAction(voter_seat=seat, target_seat=1) for seat in (2, 3, 4)]

    await engine._execute_tiebreak([1, 2])

    assert engine.state.players[1].is_alive is True
    assert engine.state.is_tiebreak is False and engine.state.vote_round == 1
    results = [r.content for r in engine.conversation_log.get_public() if r.content.startswith("投票结果")]
    assert results == ["投票结果：1号玩家得票最高，但翻牌免于出局。"]
    assert engine.sm.get_state() is GamePhase.NIGHT
