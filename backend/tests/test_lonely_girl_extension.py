"""Lonely Girl: one seat whose win follows somebody else's camp.

The role itself is deliberately thin — she is a villager for every camp-based
rule — so what this file pins is the *attribution*: the camp that ends the game
never moves, the seat list can, and the public shapes stay exactly as they were
on every board without her.
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.effect_applier import (
    EffectApplier, EffectPermission, _Runtime, derive_effect_id,
)
from app.core.rule_engine import RuleEngine
from app.core.win_attribution import IDOL_KEY, idol_seat, winner_seats
from app.models.actions import WinResult
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, EffectKind, GameEffect,
)
from app.roles.base import BaseRole
from app.roles.lonely_girl import (
    LONELY_GIRL_SPEC, WORSHIP_RESOURCE, LonelyGirl, lonely_girl_applicable,
    resolve_lonely_girl_action, validate_lonely_girl_action,
)
from app.roles.registry import builtin_registry

GIRL = "wolf-killer-lonely-girl"
ALL = frozenset(EffectKind)
CONTRACT = LONELY_GIRL_SPEC.contracts[0]


def girl_context(
    *, actor: int = 5, alive: bool = True, worship: int = 1, round_number: int = 1,
    phase: str = "night", degraded: bool = False,
) -> ActionContext:
    return ActionContext(
        "g", 3, {"alive_seats": (1, 2, 3, 4, 5)}, config_version="a" * 64,
        contract_id=CONTRACT.contract_id, contract_version=CONTRACT.schema_version,
        contract_digest=CONTRACT.stable_digest(), round_number=round_number,
        phase=phase, window_id="w", schedule_point=CONTRACT.schedule_point,
        actor_seat=actor, actor_role_id=GIRL, actor_alive=alive,
        resources={WORSHIP_RESOURCE: worship}, action_key="worship:1",
        degraded=degraded,
    )


def command(action_type: str = "worship", target: int | None = 1) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="ok")


def board(*, idol: int | None = None) -> GameState:
    """12 seats: wolves 1-4, girl 5, villagers 6-8, gods 9-12."""
    players = {seat: PlayerState(seat, "wolf-killer-werewolf", "werewolf") for seat in (1, 2, 3, 4)}
    players[5] = PlayerState(5, GIRL, "good")
    for seat in (6, 7, 8):
        players[seat] = PlayerState(seat, "wolf-killer-villager", "good")
    for seat, role in ((9, "wolf-killer-seer"), (10, "wolf-killer-witch"),
                       (11, "wolf-killer-hunter"), (12, "wolf-killer-guard")):
        players[seat] = PlayerState(seat, role, "good")
    state = GameState("attribution", phase=GamePhase.NIGHT, round_number=1, players=players)
    state._pipeline_runtime = _Runtime()
    if idol is not None:
        EffectApplier().apply(state, (
            GameEffect(
                derive_effect_id("worship:1", 0), EffectKind.ACCEPT_ACTION,
                "worship:1", payload={}, expected_revision=0, sort_key=(0,),
            ),
            GameEffect(
                derive_effect_id("worship:1", 1), EffectKind.SET_PRIVATE_DATA,
                "worship:1", target_seat=5,
                payload={"target": 5, "key": IDOL_KEY, "value": {"seat": idol, "round": 1}},
                expected_revision=0, sort_key=(1,),
            ),
        ), EffectPermission(
            actor_seat=5, role_effects=ALL, contract_effects=ALL,
            allowed_targets=frozenset({5}),
            allowed_visibility=frozenset({"PUBLIC", "ACTOR"}),
        ))
    return state


def test_spec_is_a_plain_villager_that_acts_first_on_the_first_night() -> None:
    assert LONELY_GIRL_SPEC.camp_id == "good"
    assert LONELY_GIRL_SPEC.tags == frozenset({"villager"})
    assert LONELY_GIRL_SPEC.max_count == 1
    assert LONELY_GIRL_SPEC.initial_resources == {WORSHIP_RESOURCE: 1}
    assert LONELY_GIRL_SPEC.initial_private_data == {IDOL_KEY: None}
    assert CONTRACT.order == 5, "she picks before every other night role"
    assert CONTRACT.per_game_limit == 1
    assert LONELY_GIRL_SPEC.opening_narration == (
        "孤独少女请睁眼", "请选择一名玩家作为你的崇拜对象。",
    )
    assert issubclass(LonelyGirl, BaseRole)
    assert builtin_registry.require(GIRL).role_factory is LonelyGirl


def test_the_pick_is_offered_once_on_the_first_night() -> None:
    assert lonely_girl_applicable(girl_context()) is True
    assert lonely_girl_applicable(girl_context(round_number=2)) is False
    assert lonely_girl_applicable(girl_context(worship=0)) is False
    assert lonely_girl_applicable(girl_context(alive=False)) is False


def test_validate_forbids_worshipping_yourself() -> None:
    assert validate_lonely_girl_action(girl_context(), command()) == ()
    assert validate_lonely_girl_action(girl_context(), command("pass", None)) == ()
    missing = validate_lonely_girl_action(girl_context(), command("worship", None))
    assert [item.code for item in missing] == ["worship_target_required"]
    itself = validate_lonely_girl_action(girl_context(), command("worship", 5))
    assert [item.code for item in itself] == ["self_target"]


def test_resolve_spends_the_one_shot_and_records_the_idol() -> None:
    effects = resolve_lonely_girl_action(girl_context(), command("worship", 9))
    assert [effect.kind.value for effect in effects] == [
        "consume_resource", "set_private_data", "emit_event",
    ]
    assert effects[0].payload == {"target": 5, "resource": WORSHIP_RESOURCE, "amount": 1}
    assert effects[0].preconditions == {
        "resource_equals": {"resource": WORSHIP_RESOURCE, "value": 1},
    }
    assert effects[1].payload["value"] == {"seat": 9, "round": 1}
    assert effects[2].payload["event_type"] == "LONELY_GIRL_REASONING"
    assert "崇拜 9 号" in effects[2].payload["payload"]["thought"]


def test_passing_and_degrading_still_record_a_line() -> None:
    passed = resolve_lonely_girl_action(girl_context(), command("pass", None))
    assert [effect.kind.value for effect in passed] == ["emit_event"]
    assert passed[0].payload["payload"]["thought"].startswith("没有选择崇拜对象")
    degraded = resolve_lonely_girl_action(
        girl_context(degraded=True),
        ActionCommand(action_type="pass", target_seat=None,
                      reasoning="本轮未提交决策", degraded=True),
    )
    assert degraded[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert degraded[0].payload["payload"]["degraded"] is True


def test_the_declared_batch_passes_the_resolver_and_validator() -> None:
    registry = builtin_registry.freeze()
    spec = registry.require(GIRL)
    resolved = ActionResolver().resolve_effects(
        girl_context(), spec, CONTRACT, command("worship", 9),
    )
    assert resolved[0].kind is EffectKind.ACCEPT_ACTION


def test_idol_seat_reads_back_only_a_real_seat() -> None:
    assert idol_seat(board(idol=1), 5) == 1
    assert idol_seat(board(), 5) is None
    state = board()
    state._pipeline_runtime.private_data[5] = {IDOL_KEY: {"seat": 99}}
    assert idol_seat(state, 5) is None
    state._pipeline_runtime.private_data[5] = {IDOL_KEY: 4}
    assert idol_seat(state, 5) is None


def test_no_idol_anywhere_means_the_plain_camp_result() -> None:
    """Every existing board keeps the historical shape: no winners key at all."""
    state = board()
    assert winner_seats(state, "good") is None
    assert RuleEngine().check_win(state) is None
    for seat in (1, 2, 3, 4):
        state.players[seat].is_alive = False
    result = RuleEngine().check_win(state)
    assert result is not None and result.winners is None
    assert result.to_dict() == {"winning_camp": "good", "reason": "all_wolves_dead"}


def test_the_girl_wins_with_her_idol_and_loses_with_it() -> None:
    """The camp is decided by the living counts; only the seats follow the idol."""
    with_wolf_idol = board(idol=1)
    for seat in (1, 2, 3, 4):
        with_wolf_idol.players[seat].is_alive = False
    won = RuleEngine().check_win(with_wolf_idol)
    assert won is not None and won.winning_camp == "good"
    # The good camp took the game, and the girl — who bet on a wolf — did not.
    assert won.winners == (6, 7, 8, 9, 10, 11, 12)
    assert won.to_dict()["winners"] == [6, 7, 8, 9, 10, 11, 12]

    with_god_idol = board(idol=9)
    for seat in (1, 2, 3, 4):
        with_god_idol.players[seat].is_alive = False
    other = RuleEngine().check_win(with_god_idol)
    assert other is not None and other.winners == (5, 6, 7, 8, 9, 10, 11, 12)


def test_she_wins_a_game_her_own_camp_lost() -> None:
    """A wolf idol takes the game: the girl shares the win, the villagers do not."""
    state = board(idol=1)
    # Wolves maul the four gods and outnumber the rest.
    for seat in (9, 10, 11, 12):
        state.players[seat].is_alive = False
    result = RuleEngine().check_win(state)
    assert result is not None and result.winning_camp == "werewolf"
    assert result.winners == (1, 2, 3, 4, 5)
    assert 6 not in result.winners


def test_a_dead_girl_still_shares_the_win_she_bet_on() -> None:
    """Her bet is decided at the first night, not by surviving to the end."""
    state = board(idol=1)
    for seat in (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12):
        state.players[seat].is_alive = False
    state.players[5].is_alive = False
    result = RuleEngine().check_win(state)
    assert result is not None and result.winning_camp == "werewolf"
    assert result.winners == (1, 2, 3, 4, 5)


def test_pending_win_rebuilds_the_same_seats() -> None:
    from app.core.game_engine import GameEngine, _PendingWin

    state = board(idol=9)
    engine = object.__new__(GameEngine)
    engine.state = state
    rebuilt = engine._pending_win_result(_PendingWin("good", "all_wolves_dead"))
    assert rebuilt.winning_camp == "good" and rebuilt.reason == "all_wolves_dead"
    assert rebuilt.winners == (5, 6, 7, 8, 9, 10, 11, 12)
    # The pending snapshot itself has no winners field to persist.
    assert set(_PendingWin.__dataclass_fields__) == {"winning_camp", "reason"}


def test_the_state_checkpoint_round_trips_the_seat_list() -> None:
    from app.persistence.checkpoint_codec import CheckpointCodec

    codec = CheckpointCodec(builtin_registry.freeze())
    state = board(idol=1)
    state.win_result = {
        "winning_camp": "good", "reason": "all_wolves_dead", "winners": [6, 7, 8],
    }
    document = codec.encode(state, orchestration={})
    decoded, _ = CheckpointCodec(builtin_registry.freeze()).decode(document)
    assert decoded.win_result == state.win_result


def test_the_audience_stream_carries_the_seat_list() -> None:
    """The god view names the seats; the projector's whitelist has to allow it."""
    from app.services.audience_projector import AudienceProjector

    projected = AudienceProjector().project_events("g", [{
        "event_id": "evt", "event_type": "GAME_OVER",
        "payload": {
            "winning_camp": "good", "reason": "all_wolves_dead", "winners": [5, 6],
        },
        "visibility": ["PUBLIC"],
    }])
    assert [event["event_type"] for event in projected] == ["winner"]
    assert projected[0]["payload"] == {
        "winning_camp": "good", "reason": "all_wolves_dead", "winners": [5, 6],
    }
    legacy = AudienceProjector().project_events("g", [{
        "event_id": "evt", "event_type": "GAME_OVER",
        "payload": {"winning_camp": "good", "reason": "all_wolves_dead"},
        "visibility": ["PUBLIC"],
    }])
    assert legacy[0]["payload"] == {
        "winning_camp": "good", "reason": "all_wolves_dead",
    }


def test_the_pick_lands_through_the_scheduler_and_decides_the_result() -> None:
    """The whole path: contract issued → idol written → win attribution reads it."""
    from app.core.action_validator import ActionValidator
    from app.core.context_projector import ContextProjector
    from app.core.effect_applier import EffectApplier
    from app.core.scheduler import Scheduler
    from app.models.pipeline import SchedulePoint
    from app.roles.registry import RoleRegistry
    from app.roles.villager import VILLAGER_SPEC
    from app.roles.werewolf import WEREWOLF_SPEC

    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, LONELY_GIRL_SPEC):
        registry.register_pipeline(spec)

    def provider(request, projected, attempt):
        return ActionCommand(action_type="worship", target_seat=1, reasoning="赌狼")

    state = GameState(
        "worship-e2e", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            5: PlayerState(5, GIRL, "good"),
        },
    )
    Scheduler(
        registry.freeze(), ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    ).run_point(state, SchedulePoint.NIGHT_ACTION)

    assert state._pipeline_runtime.private_data[5][IDOL_KEY] == {"seat": 1, "round": 1}
    assert state._pipeline_runtime.role_resources[5][WORSHIP_RESOURCE] == 0
    assert idol_seat(state, 5) == 1
    # The wolves outnumber the single good seat, so the camp is the wolves' — and
    # the girl, who bet on them, is in the seat list this time.
    result = RuleEngine().check_win(state)
    assert result is not None and result.winning_camp == "werewolf"
    assert result.winners == (1, 5)


@pytest.mark.parametrize("winners", [None, (), (3,)])
def test_the_logged_result_keeps_its_historical_shape(winners) -> None:
    from app.core.game_logger import GameLogger

    calls: list[tuple[str, int, str, dict]] = []

    class Recorder(GameLogger):
        def __init__(self) -> None:  # noqa: D107 - test double
            pass

        def log_operation(self, game_id, operation, round_num, label, data=None):
            calls.append((operation, round_num, label, data))

    Recorder().log_game_over("g", 3, "good", "all_wolves_dead", winners=winners)
    operation, round_num, label, data = calls[0]
    assert (operation, round_num, label) == ("game_over", 3, "game_over")
    if winners is None:
        assert data == {"winner": "good", "reason": "all_wolves_dead"}
    else:
        assert data == {
            "winner": "good", "reason": "all_wolves_dead", "winners": list(winners),
        }
