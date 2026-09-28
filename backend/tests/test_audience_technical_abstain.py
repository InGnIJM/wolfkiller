"""A ballot the system cast for a seat must reach the god view with its reason.

The recorded vote of a seat whose model failed has no target, so the plain vote
step published a silent abstention while the failure code stayed in the private
ledger. The engine now commits a mutually exclusive
``vote_technical_abstain:<round>:<vote_round>:<seat>:<failure_code>`` step and
the mapping turns it into the ``technical_abstain`` audience event the frontend
already renders.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.models.actions import VoteAction
from app.models.game import GamePhase, GameState, PlayerState
from app.models.vote import CastVoteArgs
from app.services.audience_projector import AudienceProjector
from app.services.game_service import GameService


def _engine(game_id: str, seats: tuple[int, ...]) -> GameEngine:
    # The vote service binds the state at construction, so configure the state
    # the engine already owns instead of replacing it.
    engine = GameEngine(game_id=game_id, event_bus=EventBus())
    engine.state.phase = GamePhase.VOTE_CASTING
    engine.state.round_number = 2
    engine.state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", "good") for seat in seats
    }
    engine.sm.set_state(GamePhase.VOTE_CASTING)
    return engine


def test_a_system_cast_ballot_is_mapped_with_its_failure_code() -> None:
    state = GameState(game_id="game")
    state.round_number = 4
    engine = SimpleNamespace(game_id="game", state=state)
    events = GameService._checkpoint_domain_events(
        engine, "00000077:vote_technical_abstain:4:1:7:provider_connection_error",
    )
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "TECHNICAL_ABSTAIN"
    assert event["payload"] == {
        "round_number": 4,
        "voter_seat": 7,
        "failure_code": "provider_connection_error",
    }
    assert event["visibility"] == ["PUBLIC"]
    assert event["schema_version"] == 1

    # The audience contract the frontend renders names the voter, not the actor.
    projected = AudienceProjector().project_events("game", events)
    assert [row["event_type"] for row in projected] == ["technical_abstain"]
    assert projected[0]["payload"] == {
        "round_number": 4,
        "voter_seat": 7,
        "failure_code": "provider_connection_error",
    }


@pytest.mark.asyncio
async def test_a_failed_vote_step_announces_the_abstention_instead_of_the_ballot() -> None:
    engine = _engine("vote-abstain", (1, 2))
    labels: list[str] = []
    engine._checkpoint_hook = labels.append

    async def vote(seat: int):
        # Mirrors ``GameEngine.vote``: the seat's own step is what commits the
        # ballot, and a model failure commits a technical abstention instead.
        if seat == 1:
            engine._vote_service.technical_abstain(
                engine._active_vote_window_id, seat,
                failure_code="provider_connection_error",
            )
            return VoteAction(seat, None, "technical abstain")
        engine._vote_service.submit(
            engine._active_vote_window_id, seat,
            CastVoteArgs(action_type="vote", target_seat=1, reasoning="投1号"),
        )
        return VoteAction(seat, 1)

    engine.vote = vote

    await engine._execute_vote_casting()

    round_number, vote_round = engine.state.round_number, engine.state.vote_round
    assert f"vote_technical_abstain:{round_number}:{vote_round}:1:provider_connection_error" in [
        label.split(":", 1)[1] for label in labels
    ]
    assert f"vote_received:{round_number}:{vote_round}:2" in [
        label.split(":", 1)[1] for label in labels
    ]
    # Mutually exclusive: the abstaining seat never also commits a vote step, so
    # no ballot is published twice.
    assert not any(
        label.endswith(f":vote_received:{round_number}:{vote_round}:1")
        for label in labels
    )

    abstain_label = next(
        label for label in labels if ":vote_technical_abstain:" in label
    )
    events = GameService._checkpoint_domain_events(engine, abstain_label)
    projected = AudienceProjector().project_events(engine.game_id, events)
    assert [(row["event_type"], row["payload"]["voter_seat"]) for row in projected] == [
        ("technical_abstain", 1)
    ]


@pytest.mark.asyncio
async def test_a_missing_vote_result_is_announced_as_a_system_abstention() -> None:
    engine = _engine("vote-missing", (1, 2))
    labels: list[str] = []
    engine._checkpoint_hook = labels.append
    engine.vote = AsyncMockVotes([VoteAction(1, 2), None])

    await engine._execute_vote_casting()

    assert [
        label.split(":", 1)[1] for label in labels if ":vote_technical_abstain:" in label
    ] == [
        f"vote_technical_abstain:{engine.state.round_number}"
        f":{engine.state.vote_round}:2:missing_vote_result"
    ]


@pytest.mark.asyncio
async def test_the_vote_phase_timeout_announces_every_abstention_it_casts() -> None:
    engine = _engine("vote-timeout", (1, 2, 3))
    engine._vote_phase_timeout_seconds = 0.1
    labels: list[str] = []
    engine._checkpoint_hook = labels.append
    never = asyncio.Event()

    async def vote(seat: int):
        if seat == 1:
            engine._vote_service.submit(
                engine._active_vote_window_id, seat,
                CastVoteArgs(action_type="vote", target_seat=2, reasoning="投2号"),
            )
            return VoteAction(1, 2)
        await never.wait()

    engine.vote = vote

    await asyncio.wait_for(engine._execute_vote_casting(), timeout=1.0)

    round_number, vote_round = engine.state.round_number, engine.state.vote_round
    announced = sorted(
        label.split(":", 1)[1] for label in labels
        if ":vote_technical_abstain:" in label
    )
    assert announced == [
        f"vote_technical_abstain:{round_number}:{vote_round}:2:request_timeout",
        f"vote_technical_abstain:{round_number}:{vote_round}:3:request_timeout",
    ]
    # The seat that did vote keeps the ordinary ballot step.
    assert f"vote_received:{round_number}:{vote_round}:1" in [
        label.split(":", 1)[1] for label in labels
    ]


class AsyncMockVotes:
    """A ``vote`` stand-in that hands out scripted results in seat order."""

    def __init__(self, results: list[VoteAction | None]) -> None:
        self._results = list(results)

    async def __call__(self, seat: int) -> VoteAction | None:
        return self._results.pop(0)
