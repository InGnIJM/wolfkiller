import asyncio

import pytest

from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.models.actions import VoteAction
from app.models.contracts import AcceptedAction, ActionCommand
from app.models.game import GamePhase, PlayerState
from app.models.vote import VoteStatus


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
    engine._vote_phase_timeout_seconds = 0.1
    calls = []
    engine.game_logger.log_vote_phase_timeout = lambda *args, **kwargs: calls.append((args, kwargs))

    async def vote(seat):
        if seat == 1:
            return VoteAction(seat, 2)
        await asyncio.sleep(0.2)

    engine.vote = vote
    await engine._execute_vote_casting()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:3] == ("phase-timeout-log", 1)
    assert kwargs == {
        "timeout_seconds": 0.1,
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
            'window_id': 'vote-error:1:vote_casting:1:cast_vote:v2:1',
        },
    )]


@pytest.mark.asyncio
async def test_vote_phase_commits_role_decisions_through_vote_service_once():
    engine = prepare_engine("vote-service", range(1, 3))

    class VotingRole:
        def __init__(self, target):
            self.target = target
            self.requests = []

        async def request_action(self, state, conversation_log, request):
            self.requests.append(request)
            return AcceptedAction(
                request=request,
                command=ActionCommand(
                    action_type="vote", target_seat=self.target, reasoning="x",
                ),
            )

    roles = {1: VotingRole(2), 2: VotingRole(1)}
    engine.roles = roles
    opened = []
    receipt_logs = []
    closed = []
    engine.game_logger.log_vote_window_opened = (
        lambda *args, **kwargs: opened.append((args, kwargs))
    )
    engine.game_logger.log_vote_receipt = (
        lambda *args, **kwargs: receipt_logs.append((args, kwargs))
    )
    engine.game_logger.log_vote_window_closed = (
        lambda *args, **kwargs: closed.append((args, kwargs))
    )

    await engine._execute_vote_casting()

    window_id = "vote-service:1:vote_casting:1:cast_vote:v2"
    receipts = engine._vote_service.receipts(window_id)
    assert [receipt.status for receipt in receipts] == [
        VoteStatus.ACCEPTED_VOTE, VoteStatus.ACCEPTED_VOTE,
    ]
    assert [(vote.voter_seat, vote.target_seat) for vote in engine.state.votes] == [
        (1, 2), (2, 1),
    ]
    assert roles[1].requests[0].idempotency_key == f"{window_id}:1"
    assert opened[0][1]["window_id"] == window_id
    assert [item[1]["status"] for item in receipt_logs] == [
        "accepted_vote", "accepted_vote",
    ]
    assert closed[0][1] == {
        "window_id": window_id, "vote_round": 1,
        "accepted_votes": 2, "voluntary_abstains": 0,
        "technical_abstains": 0, "missing_voters": 0,
    }


@pytest.mark.asyncio
async def test_role_technical_failure_is_a_distinct_ledger_terminal():
    engine = prepare_engine("technical-ledger", range(1, 2))

    class TechnicalRole:
        async def request_action(self, state, conversation_log, request):
            return AcceptedAction(
                request=request,
                command=ActionCommand(
                    action_type="abstain", target_seat=None,
                    reasoning="provider error fallback",
                ),
                technical_failure_code="provider_connection_error",
            )

    engine.roles = {1: TechnicalRole()}

    await engine._execute_vote_casting()

    receipt = engine._vote_service.receipts(
        "technical-ledger:1:vote_casting:1:cast_vote:v2",
    )[0]
    assert receipt.status is VoteStatus.TECHNICAL_ABSTAIN
    assert receipt.failure_code == "provider_connection_error"


@pytest.mark.asyncio
async def test_vote_phase_resume_reuses_ledger_without_duplicate_projection():
    engine = prepare_engine("resume-ledger", range(1, 2))

    async def vote_once(seat):
        return VoteAction(seat, None)

    engine.vote = vote_once
    await engine._execute_vote_casting()
    engine.sm.set_state(GamePhase.VOTE_CASTING)
    engine.state.phase = GamePhase.VOTE_CASTING

    await engine._execute_vote_casting()

    assert len(engine.state.votes) == 1
    assert len(engine._vote_service.receipts(
        "resume-ledger:1:vote_casting:1:cast_vote:v2",
    )) == 1


@pytest.mark.asyncio
async def test_vote_phase_refuses_to_advance_with_missing_terminal_receipt(monkeypatch):
    engine = prepare_engine("missing-terminal", range(1, 2))

    async def vote_once(seat):
        return VoteAction(seat, None)

    engine.vote = vote_once
    real_receipts = engine._vote_service.receipts
    calls = [0]

    def hide_final_receipt(window_id):
        calls[0] += 1
        return () if calls[0] >= 4 else real_receipts(window_id)

    monkeypatch.setattr(engine._vote_service, "receipts", hide_final_receipt)

    with pytest.raises(RuntimeError, match="missing terminal receipts"):
        await engine._execute_vote_casting()


@pytest.mark.asyncio
async def test_vote_error_after_existing_receipt_returns_first_terminal():
    engine = prepare_engine("first-terminal", range(1, 3))

    class FlakyRole:
        def __init__(self):
            self.calls = 0

        async def request_action(self, state, conversation_log, request):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("late duplicate failed")
            return AcceptedAction(
                request=request,
                command=ActionCommand(
                    action_type="vote", target_seat=2, reasoning="x",
                ),
            )

    engine.roles = {1: FlakyRole()}
    first = await engine.vote(1)

    replay = await engine.vote(1)

    assert first.target_seat == 2
    assert replay.target_seat == 2
    assert len(engine._vote_service.receipts(
        "first-terminal:1:vote_casting:1:cast_vote:v2",
    )) == 1


@pytest.mark.asyncio
async def test_vote_phase_rejects_wrong_state_machine_phase():
    engine = prepare_engine("wrong-phase", range(1, 2))
    engine.sm.set_state(GamePhase.SPEECH)

    with pytest.raises(RuntimeError, match="requires VOTE_CASTING"):
        await engine._execute_vote_casting()
