"""A spared seat's marks must reach the god view.

A delayable role (the old drunkard) is not killed by a poison or a gunshot: the
settlement records ``poisoned`` / ``wounded`` plus ``delayed_death`` and the
death lands a day later. Those ``STATUS_ADDED`` events are public, but the
audience projection had no type for them, so the timeline showed a seat
surviving a lethal hit with no explanation — and a daytime settlement (a hunter's
shot during the exile reaction) did not even reach the durable stream.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.core.scheduler import Scheduler
from app.models.actions import DeathReport
from app.models.game import GamePhase, GameState, PlayerState
from app.roles.registry import builtin_registry
from app.core.role_runtime import initialize_role_resources
from app.services.audience_projector import AudienceProjector
from app.services.game_service import GameService

DRUNKARD = "wolf-killer-old-drunkard"
VILLAGER = "wolf-killer-villager"
HUNTER = "wolf-killer-hunter"
WOLF = "wolf-killer-werewolf"


def _engine(tmp_path, game_id: str) -> GameEngine:
    scheduler = Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), lambda request, projected, attempt: None,
    )
    seats = {1: DRUNKARD, 2: VILLAGER, 3: HUNTER, 4: VILLAGER, 5: WOLF, 6: WOLF, 7: VILLAGER}
    engine = GameEngine(
        game_id, roles={seat: MagicMock() for seat in seats},
        event_bus=EventBus(), pipeline_scheduler=scheduler, data_dir=str(tmp_path),
    )
    engine.state = GameState(game_id, phase=GamePhase.SPEECH, round_number=2, players={
        seat: PlayerState(seat, role, "werewolf" if role == WOLF else "good")
        for seat, role in seats.items()
    })
    engine.sm.set_state(GamePhase.SPEECH)
    initialize_role_resources(engine.state, scheduler.registry.specs, scheduler.registry.digest)
    engine.give_last_words = AsyncMock()
    return engine


def _poison(engine: GameEngine) -> None:
    engine.state._pipeline_runtime.pending_damage = (
        {"target": 1, "amount": 1, "cause": "poison"},
    )


def _record_steps(engine: GameEngine) -> dict[str, list[dict]]:
    mapped: dict[str, list[dict]] = {}
    engine._checkpoint_hook = lambda step_key: mapped.__setitem__(
        step_key, GameService._checkpoint_domain_events(engine, step_key),
    )
    return mapped


@pytest.mark.asyncio
async def test_a_daytime_settlement_hands_its_marks_to_the_next_step(tmp_path) -> None:
    engine = _engine(tmp_path, "status-day")
    _poison(engine)

    deaths = await engine._settle_and_publish()

    # Nobody died, so no death announcement would have carried the marks.
    assert deaths == ()
    assert [event_type for event_type, _ in engine._pending_point_events] == [
        "STATUS_ADDED", "STATUS_ADDED",
    ]
    mapped = _record_steps(engine)
    await engine._durable_checkpoint("day_reaction:2:1:0:")

    rows = mapped["00000000:day_reaction:2:1:0:"]
    assert [row["event_type"] for row in rows] == ["STATUS_ADDED", "STATUS_ADDED"]
    assert rows[0]["payload"] == {"seat": 1, "status": "poisoned", "round_number": 2}
    projected = AudienceProjector().project_events(engine.game_id, rows)
    assert [row["event_type"] for row in projected] == ["player_status", "player_status"]
    assert projected[0]["payload"] == {
        "player_seat": 1, "status": "poisoned", "round_number": 2,
    }

    # The settlement is in the JSONL log too, like every other public event.
    log = (tmp_path / "games" / "status-day" / "game.log").read_text(encoding="utf-8")
    assert '"event_type": "STATUS_ADDED"' in log


@pytest.mark.asyncio
async def test_a_settlement_that_settles_nothing_clears_the_slot(tmp_path) -> None:
    engine = _engine(tmp_path, "status-empty")
    engine._pending_point_events = (("KNIGHT_DUEL", {"seat": 3}),)
    mapped = _record_steps(engine)

    assert await engine._settle_and_publish() == ()
    # The window's own events went out through their ``day_point:`` step; the
    # slot must not carry them into this one as well.
    assert engine._pending_point_events == ()
    await engine._durable_checkpoint("day_reaction:2:1:0:")
    assert [row["event_type"] for row in mapped["00000000:day_reaction:2:1:0:"]] == [
        "STEP_COMMITTED",
    ]


def test_a_reaction_step_publishes_marks_and_deaths_without_colliding_ids() -> None:
    state = GameState("status-map", phase=GamePhase.SPEECH, round_number=2)
    state.death_history.append(DeathReport(player_seat=4, cause="hunter_shot", round_number=2))
    engine = SimpleNamespace(
        game_id="status-map", state=state,
        _pending_point_events=(("STATUS_ADDED", {"seat": 1, "status": "wounded"}),),
    )

    rows = GameService._checkpoint_domain_events(engine, "00000044:exile_reaction:2:1:1-4")

    assert [row["event_type"] for row in rows] == ["STATUS_ADDED", "PLAYER_DIED"]
    assert rows[0]["payload"] == {"seat": 1, "status": "wounded", "round_number": 2}
    assert rows[1]["payload"]["cause"] == "hunter_shot"
    assert len({row["event_id"] for row in rows}) == 2


def test_a_day_interruption_step_also_carries_the_settlement_marks() -> None:
    state = GameState("status-interrupt", phase=GamePhase.SPEECH, round_number=3)
    state.death_history.append(DeathReport(player_seat=5, cause="self_explode", round_number=3))
    engine = SimpleNamespace(
        game_id="status-interrupt", state=state,
        _pending_point_events=(("STATUS_ADDED", {"seat": 6, "status": "delayed_death"}),),
    )

    rows = GameService._checkpoint_domain_events(engine, "00000045:day_interrupted:3:1:2:5")

    assert [row["event_type"] for row in rows] == ["STATUS_ADDED", "PLAYER_DIED"]
    assert rows[1]["payload"]["player_seat"] == 5
