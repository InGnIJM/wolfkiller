"""Knight: the daytime duel that reveals a camp and decides the day.

Mirrors ``test_guard_extension.py`` / ``test_werewolf_king_extension.py``: pure
hook tests, registry checks and engine integrations proving the new
*post-speech* DAY_ACTION window needs no role-specific engine code.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, derive_effect_id, role_resource_view
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.game_engine import GameEngine
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.knight import (
    KNIGHT_DUEL_CONTRACT, KNIGHT_SPEC, Knight, knight_applicable,
    resolve_knight_action, validate_knight_action,
)
from app.roles.registry import builtin_registry

KNIGHT, WOLF, HUNTER, VILLAGER = (
    "wolf-killer-knight", "wolf-killer-werewolf", "wolf-killer-hunter", "wolf-killer-villager",
)


def context(
    *, duel: int = 1, alive: bool = True, target: int | None = 2,
    camp: str | None = "werewolf", with_fact: bool = True,
) -> ActionContext:
    contract = KNIGHT_DUEL_CONTRACT
    facts: dict[str, object] = {"alive_seats": (1, 2, 3)}
    if with_fact and target is not None:
        selected: dict[str, object] = {"seat": target}
        if camp is not None:
            selected["camp_label"] = camp
        facts["selected_target"] = selected
    return ActionContext(
        "g", 5, facts, config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=2, phase="speech", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=KNIGHT, actor_alive=alive, resources={"duel": duel},
        action_key="knight:1",
    )


def command(action: str, target: int | None = None, reasoning: str = "ok") -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning=reasoning)


def test_spec_declares_a_one_shot_day_action_duel() -> None:
    contract = KNIGHT_DUEL_CONTRACT
    assert KNIGHT_SPEC.role_id == KNIGHT and KNIGHT_SPEC.camp_id == "good"
    assert KNIGHT_SPEC.max_count == 1
    assert KNIGHT_SPEC.initial_resources == {"duel": 1}
    assert KNIGHT_SPEC.tags == {"god"}
    assert KNIGHT_SPEC.contracts == (contract,)
    assert contract.schedule_point is SchedulePoint.POST_SPEECH_ACTION
    assert contract.action_types == ("duel", "pass")
    assert contract.fallback_action_type == "pass"
    assert contract.selected_target_fact_namespaces == {"camp_label"}
    assert contract.per_window_limit == 1 and contract.per_game_limit is None
    assert contract.is_applicable is knight_applicable
    assert contract.validate is validate_knight_action
    assert contract.resolve is resolve_knight_action
    assert "Knight" in KNIGHT_SPEC.instructions


def test_applicable_only_while_alive_with_the_duel_left() -> None:
    assert knight_applicable(context()) is True
    assert knight_applicable(context(duel=0)) is False
    assert knight_applicable(context(alive=False)) is False


def test_validate_rejects_a_spent_duel_and_a_self_challenge() -> None:
    assert validate_knight_action(context(), command("duel", 2)) == ()
    assert validate_knight_action(context(), command("pass")) == ()
    spent = validate_knight_action(context(duel=0), command("duel", 2))
    assert [(v.code, v.message) for v in spent] == [
        ("duel_unavailable", "the duel is no longer available")]
    self_target = validate_knight_action(context(), command("duel", 1))
    assert [(v.code, v.message) for v in self_target] == [
        ("self_target", "the knight must challenge another player")]


def test_challenging_a_wolf_kills_it_and_ends_the_day() -> None:
    ctx = context(camp="werewolf", target=2)
    effects = resolve_knight_action(ctx, command("duel", 2, "他是狼"))
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert [effect.effect_id for effect in effects] == [
        derive_effect_id("knight:1", index) for index in range(1, 6)
    ]
    assert all(effect.expected_revision == 5 for effect in effects)
    assert effects[0].payload == {"target": 1, "resource": "duel", "amount": 1}
    assert effects[0].preconditions == {"resource_equals": {"resource": "duel", "value": 1}}
    assert effects[1].payload == {"target": 2, "amount": 1, "cause": "knight_duel"}
    assert effects[2].payload == {
        "event_type": "KNIGHT_DUEL",
        "payload": {"seat": 1, "target_seat": 2, "camp": "werewolf", "round_number": 2},
    }
    assert effects[3].payload == {
        "event_type": "DAY_INTERRUPTED",
        "payload": {"seat": 1, "target_seat": 2, "cause": "knight_duel", "round_number": 2},
    }
    assert effects[4].payload["event_type"] == "KNIGHT_REASONING"
    assert effects[4].payload["payload"]["thought"] == "决定翻牌决斗 2 号玩家：他是狼"
    assert effects[2].visibility == effects[3].visibility == ("PUBLIC",)


def test_challenging_a_good_player_kills_the_knight_and_keeps_the_day() -> None:
    effects = resolve_knight_action(context(camp="good", target=2), command("duel", 2))
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
        EffectKind.EMIT_EVENT,
    ]
    assert effects[1].payload == {"target": 1, "amount": 1, "cause": "knight_duel"}
    assert effects[2].payload["payload"]["camp"] == "good"
    assert all(effect.payload.get("event_type") != "DAY_INTERRUPTED" for effect in effects)


def test_passing_keeps_the_duel_and_records_the_reasoning() -> None:
    effects = resolve_knight_action(context(), command("pass", None, "再等等"))
    assert [effect.kind for effect in effects] == [EffectKind.EMIT_EVENT]
    assert effects[0].payload["payload"] == {
        "seat": 1, "action_type": "pass", "target_seat": None,
        "reasoning": "再等等", "thought": "决定暂不发动决斗：再等等",
    }
    assert role_resource_view_guard(effects) is True


def role_resource_view_guard(effects) -> bool:
    """The pass branch must not spend the one-shot."""
    return all(effect.kind is not EffectKind.CONSUME_RESOURCE for effect in effects)


def test_resolve_rejects_a_missing_or_mismatched_target_fact() -> None:
    with pytest.raises(ValueError, match="selected target fact"):
        resolve_knight_action(context(with_fact=False), command("duel", 2))
    with pytest.raises(ValueError, match="selected target fact"):
        resolve_knight_action(context(target=3), command("duel", 2))
    with pytest.raises(TypeError, match="camp label"):
        resolve_knight_action(context(camp=None), command("duel", 2))


def test_knight_is_registered_in_both_registries() -> None:
    assert builtin_registry.freeze().require(KNIGHT) is KNIGHT_SPEC
    roles = builtin_registry.create_roles(
        {KNIGHT: 1, WOLF: 1}, 2, object(), lambda seat: object(),
    )
    assert any(isinstance(role, Knight) for role in roles.values())
    knight = next(role for role in roles.values() if isinstance(role, Knight))
    assert knight.get_skills() == ["duel"]


def _scheduler(provider) -> Scheduler:
    return Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )


def test_post_speech_slot_asks_once_and_spends_the_duel() -> None:
    asked: list[str] = []
    answers = iter([command("pass"), command("duel", 3, "查杀")])

    def provider(request, projected, attempt):
        asked.append(request.contract.contract_id)
        return next(answers)

    game = GameState("knight-window", phase=GamePhase.SPEECH, round_number=2, players={
        1: PlayerState(1, KNIGHT, "good"), 2: PlayerState(2, VILLAGER, "good"),
        3: PlayerState(3, WOLF, "werewolf"),
    })
    runner = _scheduler(provider)
    first = runner.run_point(game, SchedulePoint.POST_SPEECH_ACTION, slot="post_speech")
    assert asked == ["knight_duel"]
    assert [e["event_type"] for e in first.events] == ["KNIGHT_REASONING"]
    assert role_resource_view(game, 1) == {"duel": 1}

    # A slot is journalled once: re-entering the same window replays the
    # checkpoint instead of asking again.
    again = runner.run_point(game, SchedulePoint.POST_SPEECH_ACTION, slot="post_speech")
    assert asked == ["knight_duel"]
    assert [e["event_type"] for e in again.events] == ["KNIGHT_REASONING"]
    assert role_resource_view(game, 1) == {"duel": 1}

    # The next day's window is a fresh slot, so the duel is offered again.
    second = runner.run_point(game, SchedulePoint.POST_SPEECH_ACTION, slot="post_speech:day3")
    assert len(asked) == 2
    assert [e["event_type"] for e in second.events] == [
        "KNIGHT_DUEL", "DAY_INTERRUPTED", "KNIGHT_REASONING"]
    assert role_resource_view(game, 1) == {"duel": 0}
    assert game._pipeline_runtime.pending_damage == (
        {"target": 3, "amount": 1, "cause": "knight_duel"},
    )

    third = runner.run_point(game, SchedulePoint.POST_SPEECH_ACTION, slot="post_speech:day4")
    assert len(asked) == 2 and third.requests == () and third.commits == ()


def _mock_role(seat: int, role_name: str) -> MagicMock:
    role = MagicMock()
    role.seat, role.role_name = seat, role_name

    async def speak(state, conversation_log, context):
        return f"{seat}号发言"

    role.speak = speak
    return role


def _engine(tmp_path, provider, game_id: str = "knight-day") -> GameEngine:
    scheduler = _scheduler(provider)
    seats = {
        1: KNIGHT, 2: VILLAGER, 3: HUNTER, 4: VILLAGER, 5: WOLF,
        6: "wolf-killer-seer", 7: VILLAGER, 8: WOLF,
    }
    roles = {seat: _mock_role(seat, role) for seat, role in seats.items()}
    bus = EventBus()
    engine = GameEngine(game_id, roles=roles, event_bus=bus, pipeline_scheduler=scheduler,
                        data_dir=str(tmp_path))
    engine.state = GameState(game_id, phase=GamePhase.SPEECH, round_number=2, players={
        seat: PlayerState(seat, role, "werewolf" if role == WOLF else "good")
        for seat, role in seats.items()
    })
    engine.sm.set_state(GamePhase.SPEECH)
    initialize_role_resources(engine.state, scheduler.registry.specs, scheduler.registry.digest)
    engine.published = []

    async def on_died(**kwargs):
        engine.published.append(kwargs["death"])

    bus.subscribe(BusEvent.PLAYER_DIED, on_died)
    return engine


@pytest.mark.asyncio
async def test_duel_on_a_wolf_interrupts_the_day_after_every_speech(tmp_path) -> None:
    def provider(request, projected, attempt):
        if request.contract.contract_id == "knight_duel":
            return command("duel", 5, "查杀 5 号")
        return command("pass")

    engine = _engine(tmp_path, provider)

    interrupted = await engine._execute_speech_round()

    assert interrupted is True
    assert engine.sm.get_state() is GamePhase.NIGHT
    # Every living seat spoke before the duel: the window is post-speech, not
    # per-speaker.
    assert [s.player_seat for s in engine.state.speeches] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert engine.state.current_speaker is None and engine.state.speaking_order == []
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(5, "knight_duel")]
    assert [(d.player_seat, d.cause) for d in engine.published] == [(5, "knight_duel")]
    assert engine.state._pipeline_runtime.pending_damage == ()
    assert role_resource_view(engine.state, 1) == {"duel": 0}
    records = [json.loads(line) for line in
               (tmp_path / "games" / "knight-day" / "game.log").read_text("utf-8").splitlines()]
    audience = [r["data"]["event_type"] for r in records if r["operation"] == "audience_action"]
    assert audience.count("KNIGHT_DUEL") == 1 and audience.count("DAY_INTERRUPTED") == 1
    assert audience.count("KNIGHT_REASONING") == 1


@pytest.mark.asyncio
async def test_duel_on_a_good_player_leaves_the_day_running(tmp_path) -> None:
    def provider(request, projected, attempt):
        if request.contract.contract_id == "knight_duel":
            return command("duel", 2, "我怀疑 2 号")
        return command("pass")

    engine = _engine(tmp_path, provider, "knight-good")

    interrupted = await engine._execute_speech_round()

    # The day keeps its vote; the knight is dead by the end of the speeches.
    assert interrupted is False
    assert engine.sm.get_state() is GamePhase.VOTE_CASTING
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(1, "knight_duel")]
    assert [(d.player_seat, d.cause) for d in engine.published] == [(1, "knight_duel")]
    assert engine.state.players[1].is_alive is False
    assert role_resource_view(engine.state, 1) == {"duel": 0}


@pytest.mark.asyncio
async def test_resumed_speech_round_replays_the_post_speech_interruption(tmp_path) -> None:
    def provider(request, projected, attempt):
        if request.contract.contract_id == "knight_duel":
            return command("duel", 5, "查杀 5 号")
        return command("pass")

    engine = _engine(tmp_path, provider, "knight-resume")
    assert await engine._execute_speech_round() is True
    deaths = list(engine.state.death_history)
    revision = engine.state._pipeline_runtime.revision

    # Re-entering the round replays the journal instead of duelling again.
    assert await engine._execute_speech_round() is True
    assert engine.state.death_history == deaths
    assert engine.state._pipeline_runtime.revision == revision
    assert role_resource_view(engine.state, 1) == {"duel": 0}


def test_post_speech_window_is_skipped_without_a_day_action_role() -> None:
    game = GameState("no-day-action", phase=GamePhase.SPEECH, round_number=1, players={
        1: PlayerState(1, VILLAGER, "good"), 2: PlayerState(2, WOLF, "werewolf"),
    })
    runner = _scheduler(lambda request, projected, attempt: command("pass"))
    result = runner.run_point(game, SchedulePoint.POST_SPEECH_ACTION, slot="post_speech")
    assert result.requests == () and result.commits == () and result.events == ()


def test_validate_ignores_unknown_action_types() -> None:
    assert validate_knight_action(context(), command("sing", 2)) == ()


@pytest.mark.asyncio
async def test_journaled_interruption_ignores_other_rounds(tmp_path) -> None:
    from app.core.point_journal import PointCheckpoint, PointKey, WorkCursor, point_journal

    def provider(request, projected, attempt):
        return command("duel", 5)

    engine = _engine(tmp_path, provider, "knight-journal")
    await engine._run_post_speech_day_action()
    assert engine._journaled_day_interruption() is not None

    # A checkpoint from an earlier round is not this round's interruption.
    engine.state.round_number = 99
    assert engine._journaled_day_interruption() is None

    # Neither is a checkpoint of an unrelated point.
    engine.state.round_number = 2
    point_journal(engine.state).put(
        PointKey(engine.state.game_id, 2, "speech#post_speech",
                 SchedulePoint.EXILE_VERDICT, "digest"),
        PointCheckpoint((), (), (), (), (), (), WorkCursor("response", 0, 0), work_count=0),
    )
    assert engine._journaled_day_interruption() is not None
