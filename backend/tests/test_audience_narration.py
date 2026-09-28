"""A narration is a public timeline beat and must reach the durable stream.

The narration is not a pipeline event: the engine writes it to the JSONL log and
to the conversation log only, so the god view skipped straight from the night to
the speeches. ``_narrate`` now hands the text to its own step and the mapping
turns it into the ``narration`` event the frontend already renders.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.game_engine import GameEngine
from app.models.game import GameState
from app.services.audience_projector import AudienceProjector
from app.services.game_service import GameService


def test_a_narration_step_is_mapped_from_the_pending_text() -> None:
    state = GameState(game_id="game")
    state.round_number = 3
    engine = SimpleNamespace(game_id="game", state=state, _pending_narration={
        "round_number": 3, "title": "天黑请闭眼", "text": "狼人请睁眼",
    })
    events = GameService._checkpoint_domain_events(
        engine, "00000051:narration:3",
    )
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "NARRATION"
    assert event["payload"] == {
        "round_number": 3, "title": "天黑请闭眼", "text": "狼人请睁眼",
    }
    assert event["visibility"] == ["PUBLIC"]
    assert event["schema_version"] == 1


def test_a_narration_step_without_pending_text_stays_empty() -> None:
    # An engine restored from a checkpoint has no pending narration until the
    # next ``_narrate``; the step then publishes nothing but its own marker.
    state = GameState(game_id="game")
    engine = SimpleNamespace(game_id="game", state=state, _pending_narration=None)
    events = GameService._checkpoint_domain_events(engine, "00000052:narration:1")
    assert events[0]["event_type"] == "NARRATION"
    assert events[0]["payload"] == {}


@pytest.mark.asyncio
async def test_narrating_publishes_the_text_to_the_god_view(tmp_path) -> None:
    engine = GameEngine(game_id="narration", data_dir=str(tmp_path))
    engine.state.round_number = 1
    labels: list[str] = []
    engine._checkpoint_hook = labels.append

    await engine._narrate("天黑请闭眼", "狼人请睁眼")

    label = next(key for key in labels if ":narration:" in key)
    assert label == "00000000:narration:1"
    projected = AudienceProjector().project_events(
        engine.game_id, GameService._checkpoint_domain_events(engine, label),
    )
    assert [row["event_type"] for row in projected] == ["narration"]
    assert projected[0]["payload"] == {
        "round_number": 1, "title": "天黑请闭眼", "text": "狼人请睁眼",
    }

    # The JSONL log keeps its own narration record: the durable stream is an
    # addition, never a replacement.
    await engine._narrate("天亮了", "昨晚是平安夜", phase="dawn")
    assert [key.split(":", 1)[1] for key in labels] == [
        "narration:1", "narration:1",
    ]
    second = AudienceProjector().project_events(
        engine.game_id,
        GameService._checkpoint_domain_events(engine, labels[-1]),
    )
    assert second[0]["payload"]["title"] == "天亮了"
