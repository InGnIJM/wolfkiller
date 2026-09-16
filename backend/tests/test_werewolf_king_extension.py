"""Werewolf King: a werewolf that may self-destruct during the day.

Mirrors ``test_guard_extension.py``: pure hook tests, registry checks and
pipeline/engine integrations proving the DAY_ACTION window, the shared kill
contract and the day interruption need no role-specific engine code.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, _Runtime, derive_effect_id, role_resource_view
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.game_engine import GameEngine
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.registry import builtin_registry
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, WEREWOLF_SPEC
from app.roles.werewolf_king import (
    WEREWOLF_KING_EXPLODE_CONTRACT, WEREWOLF_KING_SPEC, WerewolfKing,
    resolve_werewolf_king_action, validate_werewolf_king_action, werewolf_king_applicable,
)

KING, WOLF, HUNTER, VILLAGER = (
    "wolf-killer-werewolf-king", "wolf-killer-werewolf", "wolf-killer-hunter", "wolf-killer-villager",
)


def context(*, explode: int = 1, alive: bool = True) -> ActionContext:
    contract = WEREWOLF_KING_EXPLODE_CONTRACT
    return ActionContext(
        "g", 4, {"alive_seats": (1, 2, 3, 4)}, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=2, phase="speech",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=KING, actor_alive=alive, resources={"explode": explode},
        action_key="king:1",
    )


def command(action: str, target: int | None = None, reasoning: str = "ok") -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning=reasoning)


def test_spec_shares_the_kill_contract_and_adds_a_day_explode() -> None:
    assert WEREWOLF_KING_SPEC.role_id == KING and WEREWOLF_KING_SPEC.camp_id == "werewolf"
    assert WEREWOLF_KING_SPEC.max_count == 1
    assert WEREWOLF_KING_SPEC.initial_resources == {"explode": 1}
    assert WEREWOLF_KING_SPEC.contracts[0] is WEREWOLF_KILL_CONTRACT is WEREWOLF_SPEC.contracts[0]
    explode = WEREWOLF_KING_EXPLODE_CONTRACT
    assert explode.schedule_point is SchedulePoint.DAY_ACTION
    assert explode.action_types == ("explode", "pass")
    assert explode.actions_requiring_target == {"explode"}
    assert explode.per_window_limit == 1 and explode.per_game_limit is None
    assert {"PUBLIC", "ACTOR", "CAMP"} == explode.visibility_namespaces
    assert "sheriff" not in WEREWOLF_KING_SPEC.instructions.lower()


def test_applicable_while_alive_with_the_explode_left() -> None:
    assert werewolf_king_applicable(context()) is True
    assert werewolf_king_applicable(context(explode=0)) is False
    assert werewolf_king_applicable(context(alive=False)) is False


def test_validate_rejects_spent_or_self_targeted_explode() -> None:
    assert validate_werewolf_king_action(context(), command("pass")) == ()
    assert validate_werewolf_king_action(context(), command("explode", 2)) == ()
    assert [v.code for v in validate_werewolf_king_action(context(explode=0), command("explode", 2))] == [
        "explode_unavailable"]
    assert [v.code for v in validate_werewolf_king_action(context(), command("explode", 1))] == [
        "self_target"]


def test_pass_only_records_public_reasoning() -> None:
    effects = resolve_werewolf_king_action(context(), command("pass", reasoning=""))
    assert [e.kind for e in effects] == [EffectKind.EMIT_EVENT]
    payload = effects[0].payload
    assert payload["event_type"] == "WEREWOLF_KING_REASONING"
    assert payload["payload"]["action_type"] == "pass" and payload["payload"]["target_seat"] is None
    assert payload["payload"]["thought"] == "决定暂不自爆：无理由"
    assert effects[0].visibility == ("PUBLIC",)


def test_explode_consumes_the_card_and_interrupts_the_day() -> None:
    ctx = context()
    effects = resolve_werewolf_king_action(ctx, command("explode", 3, "带走预言家"))
    assert [e.kind for e in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert [e.effect_id for e in effects] == [derive_effect_id("king:1", i) for i in range(1, 7)]
    assert all(e.expected_revision == 4 for e in effects)
    assert effects[0].payload == {"target": 1, "resource": "explode", "amount": 1}
    assert effects[1].payload == {"target": 1, "amount": 1, "cause": "self_explode"}
    assert effects[2].payload == {"target": 3, "amount": 1, "cause": "self_explode"}
    assert effects[3].payload == {"event_type": "SELF_EXPLODE", "payload": {
        "seat": 1, "target_seat": 3, "round_number": 2}}
    assert effects[4].payload == {"event_type": "DAY_INTERRUPTED", "payload": {
        "seat": 1, "target_seat": 3, "cause": "self_explode", "round_number": 2}}
    assert effects[5].payload["payload"]["thought"] == "决定自爆并带走 3 号玩家：带走预言家"
    assert all(e.visibility == ("PUBLIC",) for e in effects[3:])


def test_werewolf_king_is_registered_in_both_registries() -> None:
    assert builtin_registry.freeze().require(KING) is WEREWOLF_KING_SPEC
    roles = builtin_registry.create_roles({KING: 1, VILLAGER: 1}, 2, object(), lambda seat: object())
    king = next(role for role in roles.values() if role.role_name == KING)
    assert isinstance(king, WerewolfKing) and king.get_skills() == ["kill", "explode"]
    assert king.is_good is False


def test_twelve_player_presets_create_roles_via_legacy_registry() -> None:
    from app.catalog import STANDARD_PRESETS

    for preset in STANDARD_PRESETS:
        if not preset["id"].startswith("twelve-player"):
            continue
        roles = builtin_registry.create_roles(preset["role_counts"], 12, object(), lambda seat: object())
        assert sorted(roles) == list(range(1, 13))
        counted: dict[str, int] = {}
        for role in roles.values():
            counted[role.role_name] = counted.get(role.role_name, 0) + 1
        assert counted == preset["role_counts"]


def _scheduler(provider) -> Scheduler:
    return Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )


def test_shared_kill_contract_aggregates_the_king_with_the_wolves() -> None:
    def provider(request, projected, attempt):
        assert projected.facts["camp_members"] == (1, 2)
        return command("kill", 3)

    game = GameState("shared-kill", phase=GamePhase.NIGHT, round_number=1, players={
        1: PlayerState(1, KING, "werewolf"), 2: PlayerState(2, WOLF, "werewolf"),
        3: PlayerState(3, VILLAGER, "good"),
    })
    result = _scheduler(provider).run_point(game, SchedulePoint.NIGHT_WOLF_VOTE)
    assert sorted((r.actor_seat, r.role_id) for r in result.requests) == [(1, KING), (2, WOLF)]
    assert len(result.commits) == 1
    assert game._pipeline_runtime.pending_damage == ({"target": 3, "amount": 1, "cause": "wolf_kill"},)
    assert result.events[0]["payload"]["vote_counts"] == {"3": 2}


def test_day_action_window_asks_the_king_once_per_slot_until_it_explodes() -> None:
    answers = iter([command("pass"), command("explode", 3, "带走")])
    asked = []

    def provider(request, projected, attempt):
        asked.append((request.contract.contract_id, projected.phase))
        return next(answers)

    game = GameState("day-king", phase=GamePhase.SPEECH, round_number=2, players={
        1: PlayerState(1, KING, "werewolf"), 2: PlayerState(2, VILLAGER, "good"),
        3: PlayerState(3, HUNTER, "good"),
    })
    runner = _scheduler(provider)
    first = runner.run_point(game, SchedulePoint.DAY_ACTION, slot="r1-s2")
    assert asked == [("werewolf_king_explode", "speech")]
    assert [e["event_type"] for e in first.events] == ["WEREWOLF_KING_REASONING"]
    assert role_resource_view(game, 1) == {"explode": 1}

    second = runner.run_point(game, SchedulePoint.DAY_ACTION, slot="r1-s3")
    assert len(asked) == 2
    assert [e["event_type"] for e in second.events] == [
        "SELF_EXPLODE", "DAY_INTERRUPTED", "WEREWOLF_KING_REASONING"]
    assert role_resource_view(game, 1) == {"explode": 0}
    assert game._pipeline_runtime.pending_damage == (
        {"target": 1, "amount": 1, "cause": "self_explode"},
        {"target": 3, "amount": 1, "cause": "self_explode"},
    )

    third = runner.run_point(game, SchedulePoint.DAY_ACTION, slot="r1-s4")
    assert len(asked) == 2 and third.requests == () and third.commits == ()


def _mock_role(seat: int, role_name: str) -> MagicMock:
    role = MagicMock()
    role.seat, role.role_name = seat, role_name

    async def speak(state, conversation_log, context):
        return f"{seat}号发言"

    role.speak = speak
    return role


def _engine(tmp_path, provider, game_id: str = "king-day") -> GameEngine:
    scheduler = _scheduler(provider)
    seats = {1: KING, 2: VILLAGER, 3: HUNTER, 4: VILLAGER, 5: WOLF, 6: "wolf-killer-seer", 7: VILLAGER}
    roles = {seat: _mock_role(seat, role) for seat, role in seats.items()}
    bus = EventBus()
    engine = GameEngine(game_id, roles=roles, event_bus=bus, pipeline_scheduler=scheduler,
                        data_dir=str(tmp_path))
    engine.state = GameState(game_id, phase=GamePhase.SPEECH, round_number=2, players={
        seat: PlayerState(seat, role, "werewolf" if "werewolf" in role else "good")
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
async def test_explode_interrupts_the_speech_round_and_lets_the_hunter_react(tmp_path) -> None:
    asked: list[str] = []

    def provider(request, projected, attempt):
        if request.contract.contract_id == "hunter_shoot":
            return command("shoot", 4, "带走一个")
        asked.append(projected.window_id)
        # Pass before seats 1 and 2 speak, explode before seat 3.
        return command("explode", 3, "带走猎人") if len(asked) == 3 else command("pass")

    engine = _engine(tmp_path, provider)

    interrupted = await engine._execute_speech_round()

    assert interrupted is True
    assert engine.sm.get_state() is GamePhase.NIGHT
    assert [s.player_seat for s in engine.state.speeches] == [1, 2]
    assert engine.state.current_speaker is None and engine.state.speaking_order == []
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [
        (1, "self_explode"), (3, "self_explode"), (4, "hunter_shot")]
    assert [(d.player_seat, d.cause) for d in engine.published] == [
        (1, "self_explode"), (3, "self_explode"), (4, "hunter_shot")]
    assert engine.state._pipeline_runtime.pending_damage == ()
    assert [seat for seat, p in engine.state.players.items() if p.is_alive] == [2, 5, 6, 7]
    assert role_resource_view(engine.state, 1) == {"explode": 0}
    messages = [r.content for r in engine.conversation_log.get_public()]
    assert any("白天进程被中断" in m and "1号、3号" in m for m in messages)
    records = [json.loads(line) for line in
               (tmp_path / "games" / "king-day" / "game.log").read_text("utf-8").splitlines()]
    audience = [r["data"]["event_type"] for r in records if r["operation"] == "audience_action"]
    assert audience.count("SELF_EXPLODE") == 1 and audience.count("DAY_INTERRUPTED") == 1
    assert audience.count("WEREWOLF_KING_REASONING") == 3 and "HUNTER_REASONING" in audience
    deaths = [r for r in records if r["operation"] == "night_deaths"]
    assert [d["player_seat"] for record in deaths for d in record["data"]["deaths"]] == [1, 3, 4]


@pytest.mark.asyncio
async def test_resumed_speech_round_replays_the_interruption_without_duplicate_deaths(tmp_path) -> None:
    def provider(request, projected, attempt):
        if request.contract.contract_id == "hunter_shoot":
            return command("pass")
        return command("explode", 2, "带走")

    engine = _engine(tmp_path, provider, "king-resume")
    assert await engine._execute_speech_round() is True
    deaths_before = list(engine.state.death_history)
    assert [(d.player_seat, d.cause) for d in deaths_before] == [(1, "self_explode"), (2, "self_explode")]
    assert len(engine.published) == 2
    messages_before = len(engine.conversation_log.get_public())

    # Crash after the checkpoint but before the phase advanced: SPEECH again,
    # with the exploded seats already dead and therefore no longer speakers.
    engine.sm.set_state(GamePhase.SPEECH)
    engine.state.phase = GamePhase.SPEECH
    assert await engine._execute_speech_round() is True

    assert engine.state.death_history == deaths_before
    assert len(engine.published) == 2
    assert len(engine.conversation_log.get_public()) == messages_before
    assert engine.sm.get_state() is GamePhase.NIGHT
    assert engine.state.speeches == []


@pytest.mark.asyncio
async def test_interruption_resume_without_journal_runs_the_round(tmp_path) -> None:
    engine = _engine(tmp_path, lambda *args: command("pass"), "king-fresh")
    assert engine._journaled_day_interruption() is None
    # A journaled window that merely passed is not an interruption either.
    await engine._run_day_action(1)
    assert engine._journaled_day_interruption() is None


@pytest.mark.asyncio
async def test_explode_that_ends_the_game_goes_to_game_over(tmp_path) -> None:
    def provider(request, projected, attempt):
        return command("explode", 3, "带走") if request.contract.contract_id == "werewolf_king_explode" else command("pass")

    engine = _engine(tmp_path, provider, "king-win")
    for seat in (2, 4, 6, 7):
        engine.state.players[seat].is_alive = False
    # Alive: king(1), hunter(3), wolf(5). Exploding on the hunter leaves 5 alone.

    assert await engine._execute_speech_round() is True
    assert engine.sm.get_state() is GamePhase.GAME_OVER
    assert engine.state.win_result["winning_camp"] == "werewolf"


@pytest.mark.asyncio
async def test_passing_king_lets_the_round_finish_normally(tmp_path) -> None:
    def provider(request, projected, attempt):
        return command("pass")

    engine = _engine(tmp_path, provider, "king-pass")
    assert await engine._execute_speech_round() is False
    assert [s.player_seat for s in engine.state.speeches] == [1, 2, 3, 4, 5, 6, 7]
    assert engine.sm.get_state() is GamePhase.VOTE_CASTING
    assert role_resource_view(engine.state, 1) == {"explode": 1}


def test_day_action_is_disabled_without_a_day_action_role_or_scheduler(tmp_path) -> None:
    engine = _engine(tmp_path, lambda *args: command("pass"), "king-none")
    engine.state.players[1] = PlayerState(1, WOLF, "werewolf")
    assert engine._day_action_enabled() is False
    engine.state.players[1] = PlayerState(1, KING, "werewolf", is_alive=False)
    assert engine._day_action_enabled() is True  # dead holders still replay the journal
    assert GameEngine("bare", pipeline_scheduler=None)._day_action_enabled() is False


@pytest.mark.asyncio
async def test_run_day_action_returns_false_when_disabled(tmp_path) -> None:
    engine = _engine(tmp_path, lambda *args: command("pass"), "king-disabled")
    engine.state.players[1] = PlayerState(1, WOLF, "werewolf")
    engine._run_pipeline_point = AsyncMock()
    assert await engine._run_day_action(2) is False
    engine._run_pipeline_point.assert_not_awaited()


@pytest.mark.asyncio
async def test_interruption_without_settled_deaths_still_ends_the_day(tmp_path) -> None:
    engine = _engine(tmp_path, lambda *args: command("pass"), "king-empty")
    engine.memory_service = MagicMock()

    await engine._resolve_day_interruption(2, ({"cause": "self_explode"},))

    assert engine.state.death_history == [] and engine.published == []
    assert engine.sm.get_state() is GamePhase.NIGHT
    engine.memory_service.save_memories.assert_called_once_with(engine.state)


@pytest.mark.asyncio
async def test_interrupted_supplemental_speech_round_cancels_the_re_vote(tmp_path) -> None:
    def provider(request, projected, attempt):
        return command("explode", 2, "带走") if request.contract.contract_id == "werewolf_king_explode" else command("pass")

    engine = _engine(tmp_path, provider, "king-tiebreak")
    engine.state.phase = GamePhase.VOTE_RESOLUTION
    engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
    engine.state.is_tiebreak = True
    engine.state.vote_round = 2
    engine.state.tiebreak_candidates = {2, 4}
    engine._execute_vote_casting = AsyncMock()

    await engine._execute_tiebreak([2, 4])

    engine._execute_vote_casting.assert_not_awaited()
    assert engine.sm.get_state() is GamePhase.NIGHT
    assert engine.state.is_tiebreak is False and engine.state.vote_round == 1
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [
        (1, "self_explode"), (2, "self_explode")]


@pytest.mark.asyncio
async def test_pipeline_point_requires_a_scheduler() -> None:
    engine = GameEngine("no-scheduler", pipeline_scheduler=None)
    with pytest.raises(ValueError, match="scheduler"):
        await engine._run_pipeline_point(SchedulePoint.DAY_ACTION)


def test_reveal_from_verdict_tolerates_unknown_seats_and_roles(tmp_path) -> None:
    engine = _engine(tmp_path, lambda *args: command("pass"), "king-reveal")
    before = len(engine.conversation_log.get_public())
    engine._reveal_from_verdict(99)
    assert len(engine.conversation_log.get_public()) == before
    engine.state.players[2] = PlayerState(2, "custom-role", "good")
    engine._reveal_from_verdict(2)
    assert engine.state.players[2].revealed_role == "custom-role"
    assert "custom-role" in engine.conversation_log.get_public()[-1].content


def test_synthetic_helpers_shape_events(tmp_path) -> None:
    from app.models.actions import DeathReport

    engine = _engine(tmp_path, lambda *args: command("pass"), "king-helpers")
    assert engine._died_event(DeathReport(3, "self_explode", 2)) == {
        "event_type": "PLAYER_DIED",
        "payload": {"target_seat": 3, "cause": "self_explode", "round_number": 2},
        "visibility": ("PUBLIC",),
    }
    assert engine._seat_list((DeathReport(1, "x", 2), DeathReport(3, "y", 2))) == "1-3"
    assert engine._seat_list(()) == ""
    assert engine._day_slot(2, 7) == "r2-s7"
