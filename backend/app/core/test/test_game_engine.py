import asyncio

import pytest

from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.models.actions import VoteAction
from app.models.game import GamePhase, PlayerState


def prepare_engine(game_id: str, seats: range) -> GameEngine:
    engine = GameEngine(game_id=game_id, event_bus=EventBus())
    engine.state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", "good")
        for seat in seats
    }
    engine.sm.set_state(GamePhase.VOTE_CASTING)
    engine.state.phase = GamePhase.VOTE_CASTING
    engine.state.round_number = 1
    return engine


@pytest.mark.asyncio
async def test_vote_scheduler_logs_queue_wait_for_second_batch():
    engine = prepare_engine("queue-log", range(1, 7))
    engine._vote_concurrency = 5
    calls = []
    engine.game_logger.log_vote_queue_telemetry = lambda *args, **kwargs: calls.append((args, kwargs))

    async def vote(seat):
        if seat <= 5:
            await asyncio.sleep(0.02)
        return VoteAction(seat, 1)

    engine.vote = vote
    await engine._execute_vote_casting()

    seat_six = next(kwargs for args, kwargs in calls if args[2] == 6)
    assert seat_six["queue_wait_ms"] >= 10
    assert seat_six["worker_limit"] == 5
    assert seat_six["vote_round"] == 1


@pytest.mark.asyncio
async def test_vote_phase_timeout_logs_exact_completed_and_missing_seats():
    engine = prepare_engine("phase-timeout-log", range(1, 4))
    engine._vote_phase_timeout_seconds = 0.01
    calls = []
    engine.game_logger.log_vote_phase_timeout = lambda *args, **kwargs: calls.append((args, kwargs))

    async def vote(seat):
        if seat == 1:
            return VoteAction(seat, 2)
        await asyncio.sleep(0.05)

    engine.vote = vote
    await engine._execute_vote_casting()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:3] == ("phase-timeout-log", 1)
    assert kwargs == {
        "timeout_seconds": 0.01,
        "completed_seats": [1],
        "missing_seats": [2, 3],
        "vote_round": 1,
    }
    assert [(vote.voter_seat, vote.target_seat) for vote in engine.state.votes] == [
        (1, 2), (2, None), (3, None),
    ]
    assert engine.state.voted_seats == {1, 2, 3}


@pytest.mark.asyncio
async def test_vote_exception_becomes_logged_technical_abstention():
    engine = prepare_engine("vote-error", range(1, 2))
    calls = []
    engine.game_logger.log_vote_technical_abstain = (
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    class BrokenRole:
        async def request_action(self, *args):
            raise RuntimeError("provider disconnected")

    engine.roles = {1: BrokenRole()}

    vote = await engine.vote(1)

    assert vote == VoteAction(1, None, "technical abstain: invoke_error")
    assert calls == [(
        ('vote-error', 1, 1),
        {
            'failure_code': 'invoke_error',
            'window_id': '1:vote_casting:1:1:exile_vote',
        },
    )]
