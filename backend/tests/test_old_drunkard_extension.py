"""Old Drunkard: a plain villager that ignores the charm and dies late.

Mirrors the other extension samples: declarative spec checks plus the
settlement/engine integrations proving the delayable mark and the charm immunity
need no role-specific code.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, EffectRejected, role_resource_view
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.game_engine import GameEngine
from app.core.night_settlement import (
    DELAYABLE_RESOURCE, DELAYED_DEATH_STATUS, POISONED_STATUS, WOUNDED_STATUS,
    delayed_death_statuses, resolve_delayed_deaths, settle,
)
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import SchedulePoint
from app.roles.old_drunkard import OLD_DRUNKARD_SPEC, OldDrunkard
from app.roles.registry import builtin_registry

DRUNKARD, VILLAGER, WOLF, HUNTER = (
    "wolf-killer-old-drunkard", "wolf-killer-villager",
    "wolf-killer-werewolf", "wolf-killer-hunter",
)


def test_spec_marks_immunity_and_delay_without_any_contract() -> None:
    assert OLD_DRUNKARD_SPEC.role_id == DRUNKARD
    assert OLD_DRUNKARD_SPEC.camp_id == "good"
    assert OLD_DRUNKARD_SPEC.max_count == 1
    # A plain villager for every counting rule: it must never count as a god.
    assert OLD_DRUNKARD_SPEC.tags == {"villager"}
    assert OLD_DRUNKARD_SPEC.contracts == ()
    assert OLD_DRUNKARD_SPEC.initial_resources == {"charm_immune": 1, "delayable": 1}
    assert "Old Drunkard" in OLD_DRUNKARD_SPEC.instructions


def test_delayed_death_statuses_only_cover_poison_and_gunshot() -> None:
    assert delayed_death_statuses("poison") == (POISONED_STATUS, DELAYED_DEATH_STATUS)
    assert delayed_death_statuses("hunter_shot") == (WOUNDED_STATUS, DELAYED_DEATH_STATUS)
    assert delayed_death_statuses("wolf_kill") == ()
    assert delayed_death_statuses("exile") == ()
    assert delayed_death_statuses(object()) == ()


def test_resolve_delayed_deaths_maps_the_cause_and_clears_the_marks() -> None:
    deaths, cleared = resolve_delayed_deaths(
        {3: {POISONED_STATUS, DELAYED_DEATH_STATUS}, 5: {WOUNDED_STATUS, DELAYED_DEATH_STATUS},
         2: {"no_vote"}},
        4,
    )
    assert deaths == (
        {"seat": 3, "cause": "poison", "round_number": 4},
        {"seat": 5, "cause": "hunter_shot", "round_number": 4},
    )
    assert cleared == {
        3: (DELAYED_DEATH_STATUS, POISONED_STATUS),
        5: (DELAYED_DEATH_STATUS, WOUNDED_STATUS),
    }


def test_resolve_delayed_deaths_falls_back_and_ignores_seats_without_the_mark() -> None:
    deaths, cleared = resolve_delayed_deaths({3: {DELAYED_DEATH_STATUS}}, 1)
    assert deaths == ({"seat": 3, "cause": "delayed_death", "round_number": 1},)
    assert cleared == {3: (DELAYED_DEATH_STATUS,)}
    assert resolve_delayed_deaths({}, 1) == ((), {})
    assert resolve_delayed_deaths({4: {"no_vote"}}, 1) == ((), {})


@pytest.mark.parametrize("round_number", [-1, 2_147_483_648, True, "1", 1.5])
def test_resolve_delayed_deaths_validates_the_round_number(round_number) -> None:
    with pytest.raises(ValueError, match="round_number out of range"):
        resolve_delayed_deaths({}, round_number)


def test_settle_spares_a_delayable_seat_from_poison_and_gunshot() -> None:
    for cause, status in (("poison", POISONED_STATUS), ("hunter_shot", WOUNDED_STATUS)):
        deaths, alive, delayed = settle(
            ({"target": 2, "amount": 1, "cause": cause},), (), {1, 2},
            {1: True, 2: True}, 1, {2: {DELAYABLE_RESOURCE: 1}},
        )
        assert deaths == ()
        assert alive == {1: True, 2: True}
        assert delayed == {2: (status, DELAYED_DEATH_STATUS)}


def test_settle_still_kills_a_delayable_seat_with_a_wolf_knife() -> None:
    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},), (), {1, 2},
        {1: True, 2: True}, 1, {2: {DELAYABLE_RESOURCE: 1}},
    )
    assert deaths == ({"seat": 2, "cause": "wolf_kill", "round_number": 1},)
    assert alive == {1: True, 2: False}
    assert delayed == {}


def test_settle_ignores_a_delayable_mark_that_is_spent() -> None:
    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "poison"},), (), {1, 2},
        {1: True, 2: True}, 1, {2: {DELAYABLE_RESOURCE: 0}},
    )
    assert deaths == ({"seat": 2, "cause": "poison", "round_number": 1},)
    assert alive == {1: True, 2: False}
    assert delayed == {}


@pytest.mark.parametrize("role_resources", [object(), {1: 1}, {"1": {}}])
def test_settle_validates_the_role_resource_map(role_resources) -> None:
    with pytest.raises(ValueError, match="invalid role resource map"):
        settle((), (), {1}, {1: True}, 1, role_resources)


def _scheduler(provider) -> Scheduler:
    return Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )


def _engine(tmp_path, game_id: str) -> GameEngine:
    scheduler = _scheduler(lambda request, projected, attempt: None)

    def mock_role(seat: int, role_name: str) -> MagicMock:
        role = MagicMock()
        role.seat, role.role_name = seat, role_name

        async def speak(state, conversation_log, context):
            return f"{seat}号发言"

        role.speak = speak
        return role

    seats = {
        1: DRUNKARD, 2: VILLAGER, 3: HUNTER, 4: VILLAGER, 5: WOLF, 6: WOLF, 7: VILLAGER,
    }
    bus = EventBus()
    engine = GameEngine(
        game_id, roles={seat: mock_role(seat, role) for seat, role in seats.items()},
        event_bus=bus, pipeline_scheduler=scheduler, data_dir=str(tmp_path),
    )
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
    engine.give_last_words = AsyncMock()
    return engine


def test_old_drunkard_is_registered_and_gets_its_marks(tmp_path) -> None:
    assert builtin_registry.freeze().require(DRUNKARD) is OLD_DRUNKARD_SPEC
    roles = builtin_registry.create_roles(
        {DRUNKARD: 1, WOLF: 1}, 2, object(), lambda seat: object(),
    )
    drunkard = next(role for role in roles.values() if isinstance(role, OldDrunkard))
    assert drunkard.get_skills() == []
    engine = _engine(tmp_path, "drunkard-marks")
    assert role_resource_view(engine.state, 1) == {"charm_immune": 1, "delayable": 1}


@pytest.mark.asyncio
async def test_poison_is_delayed_and_lands_after_the_next_speech_round(tmp_path) -> None:
    engine = _engine(tmp_path, "drunkard-poison")
    engine.state._pipeline_runtime.pending_damage = (
        {"target": 1, "amount": 1, "cause": "poison"},
    )
    settlement = engine._pipeline_scheduler.settle_pending(engine.state)
    assert settlement is not None

    # Nobody died, and the seat carries the marks that explain why.
    assert engine.state.players[1].is_alive is True
    assert engine.state.death_history == []
    assert engine.state._pipeline_runtime.statuses[1] == frozenset(
        {POISONED_STATUS, DELAYED_DEATH_STATUS})
    assert [e["event_type"] for e in settlement.events] == ["STATUS_ADDED", "STATUS_ADDED"]

    # The delayed death needs a step of its own: no other announcement carries a
    # death that lands only after the speeches, so without it the god view would
    # show the seat dying with no cause at all.
    from app.services.game_service import GameService

    mapped: dict[str, list[dict]] = {}
    engine._checkpoint_hook = lambda step_key: mapped.__setitem__(
        step_key, GameService._checkpoint_domain_events(engine, step_key),
    )

    interrupted = await engine._execute_speech_round()

    # The whole day is spoken, then the pending death lands before the vote.
    assert interrupted is False
    assert [s.player_seat for s in engine.state.speeches] == [1, 2, 3, 4, 5, 6, 7]
    assert engine.sm.get_state() is GamePhase.VOTE_CASTING
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(1, "poison")]
    assert [(d.player_seat, d.cause) for d in engine.published] == [(1, "poison")]
    assert engine.state.players[1].is_alive is False
    assert engine.state._pipeline_runtime.statuses[1] == frozenset()

    delayed = next(key for key in mapped if ":delayed_death:2:1" in key)
    assert len(mapped[delayed]) == 1
    death = mapped[delayed][0]
    assert death["event_type"] == "PLAYER_DIED"
    assert death["payload"] == {
        "player_seat": 1, "cause": "poison", "round_number": 2,
    }
    assert death["visibility"] == ["PUBLIC"]
    assert death["event_id"].startswith("domain:")


@pytest.mark.asyncio
async def test_gunshot_is_delayed_the_same_way(tmp_path) -> None:
    engine = _engine(tmp_path, "drunkard-shot")
    engine.state._pipeline_runtime.pending_damage = (
        {"target": 1, "amount": 1, "cause": "hunter_shot"},
    )
    engine._pipeline_scheduler.settle_pending(engine.state)
    assert engine.state._pipeline_runtime.statuses[1] == frozenset(
        {WOUNDED_STATUS, DELAYED_DEATH_STATUS})

    await engine._execute_speech_round()

    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(1, "hunter_shot")]


@pytest.mark.asyncio
async def test_a_wolf_knife_kills_the_old_drunkard_that_same_night(tmp_path) -> None:
    engine = _engine(tmp_path, "drunkard-knife")
    engine.state._pipeline_runtime.pending_damage = (
        {"target": 1, "amount": 1, "cause": "wolf_kill"},
    )
    engine._pipeline_scheduler.settle_pending(engine.state)

    assert engine.state.players[1].is_alive is False
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [(1, "wolf_kill")]
    assert engine.state._pipeline_runtime.statuses.get(1, frozenset()) == frozenset()


@pytest.mark.asyncio
async def test_resuming_the_speech_round_does_not_kill_twice(tmp_path) -> None:
    engine = _engine(tmp_path, "drunkard-resume")
    engine.state._pipeline_runtime.pending_damage = (
        {"target": 1, "amount": 1, "cause": "poison"},
    )
    engine._pipeline_scheduler.settle_pending(engine.state)
    await engine._execute_speech_round()
    deaths = list(engine.state.death_history)
    published = list(engine.published)

    await engine._resolve_delayed_deaths()

    assert engine.state.death_history == deaths
    assert engine.published == published


def test_settle_pending_rejects_a_repeated_delay_mark(tmp_path) -> None:
    engine = _engine(tmp_path, "drunkard-repeat")
    runtime = engine.state._pipeline_runtime
    runtime.role_resources = dict(runtime.role_resources)
    runtime.role_resources[1] = dict(runtime.role_resources[1])
    runtime.statuses[1] = {DELAYED_DEATH_STATUS}
    runtime.pending_damage = ({"target": 1, "amount": 1, "cause": "poison"},)

    with pytest.raises(EffectRejected, match="delayed death status already present"):
        EffectApplier().settle_pending(engine.state, round_number=2)


def test_resolve_delayed_deaths_needs_a_runtime(tmp_path) -> None:
    engine = _engine(tmp_path, "drunkard-noruntime")
    del engine.state._pipeline_runtime
    import asyncio

    assert asyncio.run(engine._resolve_delayed_deaths()) == ()
