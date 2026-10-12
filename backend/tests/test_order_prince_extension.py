"""Order Prince: flip after the day's first vote and send the table back to the ballot.

Hook-level checks for the card, then the engine path it rides on: a generic
``VOTE_ACTION`` window after the first tally whose ``VOTE_REWOUND`` event turns the
day into a re-vote built from the tie-break machinery. The engine never names the role.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.agents.prompt_builder import PromptBuilder
from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, derive_effect_id, role_resource_view
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.core.vote_service import eligible_exile_targets
from app.models.actions import VoteAction
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.order_prince import (
    ORDER_PRINCE_SPEC, ORDER_REWIND_CONTRACT, REWIND_RESOURCE, OrderPrince,
    order_prince_applicable, resolve_order_prince_action, validate_order_prince_action,
)
from app.roles.registry import builtin_registry

PRINCE_ROLE = "wolf-killer-order-prince"
PRINCE, VILL, WOLF, VILL2 = 1, 2, 3, 4


def cmd(action: str, *, degraded: bool = False) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=None, reasoning="理由", degraded=degraded)


def context(*, rewind: int = 1, alive: bool = True, identity: bool = True) -> ActionContext:
    contract = ORDER_REWIND_CONTRACT
    facts: dict[str, object] = {"alive_seats": (1, 2, 3)}
    if identity:
        facts["actor_identity"] = {"seat": 1, "role_id": PRINCE_ROLE, "camp_id": "good"}
    return ActionContext(
        "g", 3, facts, config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=2, phase="vote_resolution", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=PRINCE, actor_role_id=PRINCE_ROLE,
        actor_alive=alive, resources={REWIND_RESOURCE: rewind}, action_key="prince:1",
    )


# ── declarations and hooks ─────────────────────────────────────────────────


def test_the_card_declares_a_one_shot_vote_window_ability() -> None:
    assert ORDER_PRINCE_SPEC.camp_id == "good" and ORDER_PRINCE_SPEC.tags == frozenset({"god"})
    assert ORDER_PRINCE_SPEC.max_count == 1
    assert ORDER_PRINCE_SPEC.initial_resources == {REWIND_RESOURCE: 1}
    assert ORDER_REWIND_CONTRACT.schedule_point is SchedulePoint.VOTE_ACTION
    assert ORDER_REWIND_CONTRACT.action_types == ("rewind", "pass")
    assert ORDER_REWIND_CONTRACT.fallback_action_type == "pass"
    # A pass must not burn the card, so the one-shot is the resource, never a game quota.
    assert not ORDER_REWIND_CONTRACT.per_game_limit
    assert builtin_registry.require(PRINCE_ROLE).role_factory is OrderPrince
    assert builtin_registry.freeze().require(PRINCE_ROLE) is ORDER_PRINCE_SPEC


def test_the_ability_needs_a_living_prince_with_the_card_left() -> None:
    assert order_prince_applicable(context()) is True
    assert order_prince_applicable(context(rewind=0)) is False
    assert order_prince_applicable(context(alive=False)) is False


def test_a_spent_card_cannot_be_played_again_but_may_still_pass() -> None:
    assert validate_order_prince_action(context(), cmd("rewind")) == ()
    assert validate_order_prince_action(context(rewind=0), cmd("pass")) == ()
    violations = validate_order_prince_action(context(rewind=0), cmd("rewind"))
    assert [v.code for v in violations] == ["rewind_unavailable"]


def test_rewinding_spends_the_card_flips_it_and_announces_the_reversal() -> None:
    effects = resolve_order_prince_action(context(), cmd("rewind"))
    assert [e.kind for e in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
        EffectKind.EMIT_EVENT,
    ]
    assert [e.effect_id for e in effects] == [
        derive_effect_id("prince:1", index) for index in range(1, 5)
    ]
    assert effects[0].payload == {"target": 1, "resource": REWIND_RESOURCE, "amount": 1}
    assert effects[0].preconditions == {
        "resource_equals": {"resource": REWIND_RESOURCE, "value": 1},
    }
    assert effects[1].payload == {"event_type": "PLAYER_REVEALED", "payload": {
        "seat_number": 1, "role": PRINCE_ROLE, "camp": "good"}}
    assert effects[2].payload == {"event_type": "VOTE_REWOUND", "payload": {
        "seat": 1, "round_number": 2}}
    assert effects[3].payload["event_type"] == "ORDER_PRINCE_REASONING"
    assert "逆转时空" in effects[3].payload["payload"]["thought"]
    assert all(e.visibility == ("PUBLIC",) for e in effects[1:])


def test_the_flip_falls_back_to_the_context_identity() -> None:
    reveal = resolve_order_prince_action(context(identity=False), cmd("rewind"))[1]
    assert reveal.payload["payload"] == {"seat_number": 1, "role": PRINCE_ROLE, "camp": ""}


def test_passing_only_reports_the_thought_and_degraded_turns_say_so() -> None:
    passed = resolve_order_prince_action(context(), cmd("pass"))
    assert [e.kind for e in passed] == [EffectKind.EMIT_EVENT]
    assert passed[0].payload["payload"]["thought"].startswith("决定不发动逆转")
    degraded = resolve_order_prince_action(context(), cmd("pass", degraded=True))
    assert degraded[0].payload["payload"]["thought"] == DEGRADED_THOUGHT


# ── the engine path ────────────────────────────────────────────────────────


def _engine(tmp_path, answer: str, game_id: str, *, prince_alive: bool = True):
    asked: list[int] = []

    def provider(request, projected, attempt):
        asked.append(request.actor_seat)
        return cmd(answer)

    scheduler = Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )
    engine = GameEngine(game_id, event_bus=EventBus(), pipeline_scheduler=scheduler,
                        data_dir=str(tmp_path))
    engine.state = GameState(
        game_id, phase=GamePhase.VOTE_RESOLUTION, round_number=2,
        players={
            PRINCE: PlayerState(PRINCE, PRINCE_ROLE, "good"),
            VILL: PlayerState(VILL, "wolf-killer-villager", "good"),
            WOLF: PlayerState(WOLF, "wolf-killer-werewolf", "werewolf"),
            VILL2: PlayerState(VILL2, "wolf-killer-villager", "good"),
        },
    )
    engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
    engine.give_last_words = AsyncMock()
    initialize_role_resources(engine.state, scheduler.registry.specs, scheduler.registry.digest)
    if not prince_alive:
        engine.state.players[PRINCE].is_alive = False
    engine.state.votes = [VoteAction(voter_seat=seat, target_seat=WOLF) for seat in (PRINCE, VILL, VILL2)]
    return engine, asked


def _messages(engine) -> list[str]:
    return [record.content for record in engine.conversation_log.get_public()]


@pytest.mark.asyncio
async def test_a_pass_leaves_the_vote_alone_and_asks_once_per_day(tmp_path) -> None:
    engine, asked = _engine(tmp_path, "pass", "prince-pass")

    await engine._execute_vote_resolution()

    assert asked == [PRINCE]
    assert engine.state.players[WOLF].is_alive is False
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(WOLF, "exile")]
    assert engine.state.is_tiebreak is False and engine.state.vote_round == 1
    assert role_resource_view(engine.state, PRINCE) == {REWIND_RESOURCE: 1}
    assert engine.state.players[PRINCE].revealed_role is None
    # The prince is told the result before the window opens.
    assert any("3号玩家得票最多（3票），尚未执行放逐" in text for text in _messages(engine))


@pytest.mark.asyncio
async def test_a_dead_prince_is_not_asked_and_no_result_is_pre_announced(tmp_path) -> None:
    engine, asked = _engine(tmp_path, "rewind", "prince-dead", prince_alive=False)
    engine.state.votes = [VoteAction(voter_seat=seat, target_seat=WOLF) for seat in (VILL, VILL2)]

    await engine._execute_vote_resolution()

    assert asked == []
    assert not any("尚未执行放逐" in text for text in _messages(engine))
    assert engine.state.players[WOLF].is_alive is False


@pytest.mark.asyncio
async def test_a_rewind_voids_the_vote_and_opens_a_revote_for_every_living_seat(tmp_path) -> None:
    engine, asked = _engine(tmp_path, "rewind", "prince-rewind")
    engine._execute_tiebreak = AsyncMock()

    await engine._execute_vote_resolution()

    assert asked == [PRINCE]
    state = engine.state
    # Nobody was exiled and the card is spent and face up.
    assert state.death_history == [] and state.players[WOLF].is_alive is True
    assert role_resource_view(state, PRINCE) == {REWIND_RESOURCE: 0}
    assert state.players[PRINCE].revealed_role == PRINCE_ROLE
    # The tie-break machinery, cast differently: round 2, everyone is a candidate,
    # and only the prince still owes a (supplemental) speech.
    assert state.is_tiebreak is True and state.vote_round == 2
    alive = {PRINCE, VILL, WOLF, VILL2}
    assert state.tiebreak_candidates == alive
    assert state.supplemental_speakers == alive - {PRINCE}
    assert state.voted_seats == set()
    assert eligible_exile_targets(state) == frozenset(alive)
    engine._execute_tiebreak.assert_awaited_once_with(sorted(alive))
    assert any("放逐投票作废" in text for text in _messages(engine))
    # The voided ballot is recorded as "nobody exiled", never as the leader's exile.
    records = [json.loads(line) for line in
               (tmp_path / "games" / "prince-rewind" / "game.log").read_text("utf-8").splitlines()]
    results = [r for r in records if r["operation"] == "vote_result"]
    assert len(results) == 1 and results[0]["data"]["exiled"] is None
    # The journal is the single record of the decision (a resume reads it back).
    assert engine._journaled_vote_rewinds() == (PRINCE,)


@pytest.mark.asyncio
async def test_the_revote_runs_to_a_real_exile_through_the_tiebreak_path(tmp_path) -> None:
    engine, _ = _engine(tmp_path, "rewind", "prince-revote")
    alive = {PRINCE, VILL, WOLF, VILL2}

    async def speech_round() -> bool:
        engine.state.supplemental_speakers |= {PRINCE}
        return False

    async def cast_votes() -> None:
        engine.state.voted_seats = set(alive)
        engine.state.votes = [VoteAction(voter_seat=seat, target_seat=VILL) for seat in (PRINCE, WOLF, VILL2)]
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)

    engine._execute_speech_round = AsyncMock(side_effect=speech_round)
    engine._execute_vote_casting = AsyncMock(side_effect=cast_votes)

    await engine._execute_vote_resolution()

    engine._execute_speech_round.assert_awaited_once()
    assert engine.state.players[VILL].is_alive is False
    assert engine.state.players[WOLF].is_alive is True, "the first ballot no longer counts"
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(VILL, "exile")]
    assert engine.state.is_tiebreak is False and engine.state.vote_round == 1
    assert engine.sm.get_state() is GamePhase.NIGHT


@pytest.mark.asyncio
async def test_a_spent_card_stops_the_window_from_opening_a_second_rewind(tmp_path) -> None:
    engine, asked = _engine(tmp_path, "rewind", "prince-spent")
    engine._execute_tiebreak = AsyncMock()
    await engine._execute_vote_resolution()
    assert asked == [PRINCE]

    # Next day: the same prince is still asked (a window exists), but the card is
    # gone, so the contract is not applicable and nobody is questioned.
    engine._clear_tiebreak_state()
    engine.state.round_number = 3
    engine.state.votes = [VoteAction(voter_seat=seat, target_seat=WOLF) for seat in (PRINCE, VILL, VILL2)]
    engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
    asked.clear()
    await engine._execute_vote_resolution()
    assert asked == []
    assert engine.state.players[WOLF].is_alive is False


# ── prompt wording ─────────────────────────────────────────────────────────


def test_a_revote_prompt_does_not_claim_a_tie() -> None:
    state = GameState(
        "g", phase=GamePhase.VOTE_CASTING, round_number=2,
        players={
            seat: PlayerState(seat, "wolf-killer-villager", "good") for seat in (1, 2, 3)
        },
    )
    state.is_tiebreak, state.vote_round = True, 2
    state.supplemental_speakers = {2, 3}
    state.tiebreak_candidates = {1, 2, 3}
    revote = PromptBuilder._vote_instruction(state)
    assert "重新投票" in revote and "上一轮投票已作废" in revote
    assert "平票" not in revote
    state.tiebreak_candidates = {1, 2}
    assert "平票复投" in PromptBuilder._vote_instruction(state)
