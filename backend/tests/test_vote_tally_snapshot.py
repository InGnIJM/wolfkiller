"""The 1.5-vote sheriff weight is fixed when the ballot opens, not when it is read.

``VoteWindow`` used to carry no sheriff snapshot, so every recomputation of the
tally asked ``ballot_weight(state, seat)`` about the *current* office. A sheriff
killed during the exile reaction (the hunter's shot) made the same ballots weigh
1 instead of 3, and the published ``VOTE_RESULT.weighted_counts`` (13/4) turned
into the head count (6/2). These tests pin the frozen basis on the window itself
and its durable round-trip.
"""

from __future__ import annotations

import pytest

from app.core.sheriff_flow import clear_office
from app.core.vote_service import VoteService
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.vote import CastVoteArgs, VoteError

GAME = "tally-snapshot"
ROUND = 3
#: The round-3 ballot from game 37f99754: seats 1, 2, 4, 5, 9, 10 vote 7;
#: seats 3 and 7 vote 4; seat 4 is the sheriff, so 7 gets 5*2 + 3 = 13.
VOTES = {1: 7, 2: 7, 3: 4, 4: 7, 5: 7, 7: 4, 9: 7, 10: 7}


def _state(*, sheriff: int | None = 4) -> GameState:
    state = GameState(
        game_id=GAME, phase=GamePhase.VOTE_CASTING, round_number=ROUND,
        config=GameConfig(enable_sheriff=True),
    )
    state.players = {
        seat: PlayerState(
            seat, "wolf-killer-villager", "good", is_sheriff=(seat == sheriff),
        )
        for seat in (1, 2, 3, 4, 5, 7, 9, 10)
    }
    state.sheriff = sheriff
    return state


def _opened(state: GameState) -> tuple[VoteService, str]:
    service = VoteService(state, clock=lambda: 100.0)
    window = service.open_window(timeout_seconds=30.0)
    for seat, target in VOTES.items():
        service.submit(
            window.window_id, seat,
            CastVoteArgs(action_type="vote", target_seat=target, reasoning=""),
        )
    return service, window.window_id


def _kill_sheriff_and_tear_badge(state: GameState) -> None:
    state.players[4].mark_dead("hunter_shot")
    clear_office(state, destroyed=True)


def test_a_sheriff_killed_after_the_ballot_does_not_change_what_it_weighed() -> None:
    state = _state(sheriff=4)
    service, window_id = _opened(state)
    assert service.tally(window_id) == {7: 13, 4: 4}

    _kill_sheriff_and_tear_badge(state)

    # The office is gone, but the ballot was cast while it was alive.
    assert service.tally(window_id) == {7: 13, 4: 4}
    assert service.window_weight(window_id, 4) == 3
    assert service.window_weight(window_id, 1) == 2


def test_the_frozen_basis_survives_a_checkpoint_round_trip() -> None:
    state = _state(sheriff=4)
    service, window_id = _opened(state)
    saved = service.checkpoint()
    assert saved["windows"][0]["sheriff_seat"] == 4

    # The office is already gone when the checkpoint is restored, as it is in
    # the crash-after-the-hunter-shot path.
    _kill_sheriff_and_tear_badge(state)
    restored = VoteService(state, clock=lambda: 200.0)
    restored.restore_checkpoint(saved)

    assert restored.tally(window_id) == {7: 13, 4: 4}


def test_an_old_checkpoint_without_the_snapshot_falls_back_to_the_live_office() -> None:
    state = _state(sheriff=4)
    service, window_id = _opened(state)
    saved = service.checkpoint()
    for window in saved["windows"]:
        window.pop("sheriff_seat")

    restored = VoteService(state, clock=lambda: 200.0)
    restored.restore_checkpoint(saved)
    assert restored.tally(window_id) == {7: 13, 4: 4}

    # With no live sheriff the fallback keeps the historical 1-vote reading.
    state_without_sheriff = _state(sheriff=None)
    old_service, old_window = _opened(state_without_sheriff)
    old_saved = old_service.checkpoint()
    for window in old_saved["windows"]:
        window.pop("sheriff_seat")
    restored_old = VoteService(state_without_sheriff, clock=lambda: 200.0)
    restored_old.restore_checkpoint(old_saved)
    assert restored_old.tally(old_window) == {7: 6, 4: 2}


def test_the_snapshot_must_be_an_integer_seat_or_none() -> None:
    state = _state(sheriff=4)
    service, _ = _opened(state)
    saved = service.checkpoint()
    saved["windows"][0]["sheriff_seat"] = "4"

    restored = VoteService(state, clock=lambda: 200.0)
    with pytest.raises(ValueError):
        restored.restore_checkpoint(saved)


def test_a_ballot_without_a_sheriff_weighs_one_per_vote() -> None:
    state = _state(sheriff=None)
    service, window_id = _opened(state)

    assert service.tally(window_id) == {7: 6, 4: 2}
    assert service.window_weight(window_id, 1) == 1


def test_an_unknown_window_has_no_weight() -> None:
    state = _state(sheriff=4)
    service, _ = _opened(state)

    with pytest.raises(VoteError):
        service.window_weight("missing-window", 1)


# ── Engine / published-record integration ─────────────────────────────────


def test_the_engine_publishes_and_logs_the_frozen_round_three_tally() -> None:
    """The exact 37f99754 round-3 shape: 13/4, not the post-shot head count."""
    from unittest.mock import MagicMock

    from app.core.game_engine import GameEngine
    from app.models.actions import VoteAction
    from app.services.game_service import GameService

    state = _state(sheriff=4)
    service, window_id = _opened(state)
    state.phase = GamePhase.VOTE_RESOLUTION
    state.votes = [
        VoteAction(voter_seat=seat, target_seat=target)
        for seat, target in VOTES.items()
    ]
    _kill_sheriff_and_tear_badge(state)

    engine = GameEngine(GAME)
    engine.state = state
    engine._vote_service = service
    engine._active_vote_window_id = window_id
    engine.game_logger = MagicMock()

    assert engine.vote_ballot_weight(4) == 3
    assert engine.vote_ballot_weight(1) == 2
    engine._log_vote_result(7)
    logged = engine.game_logger.log_vote_result.call_args
    assert logged.args[2] == 7
    assert logged.args[3] == {7: 13, 4: 4}

    events = GameService._checkpoint_domain_events(
        engine, "0:vote_result:3:1:7",
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "VOTE_RESULT"
    assert events[0]["payload"]["counts"] == {"4": 2, "7": 6}
    assert events[0]["payload"]["weighted_counts"] == {"4": 4, "7": 13}


def test_the_legacy_live_office_fallback_still_works_without_a_window() -> None:
    """Old/replayed engines (no active window) keep the pre-fix reading."""
    from unittest.mock import MagicMock

    from app.core.game_engine import GameEngine
    from app.models.actions import VoteAction

    state = _state(sheriff=4)
    state.votes = [
        VoteAction(voter_seat=seat, target_seat=target)
        for seat, target in VOTES.items()
    ]
    engine = GameEngine(GAME)
    engine.state = state
    engine._active_vote_window_id = None
    engine.game_logger = MagicMock()

    assert engine.vote_ballot_weight(4) == 3
    engine._log_vote_result(7)
    assert engine.game_logger.log_vote_result.call_args.args[3] == {7: 13, 4: 4}


@pytest.mark.asyncio
async def test_vote_result_is_checkpointed_before_the_window_is_cleared() -> None:
    """The active window must survive into the checkpoint that publishes it.

    Otherwise the domain-event builder recomputes the weight from the live
    office after the death reaction cleared it, which is exactly how 37f99754
    published 6/2 for a 13/4 decision.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.core.game_engine import GameEngine
    from app.models.actions import VoteAction

    state = _state(sheriff=4)
    service, window_id = _opened(state)
    state.phase = GamePhase.VOTE_RESOLUTION
    state.votes = [
        VoteAction(voter_seat=seat, target_seat=target)
        for seat, target in VOTES.items()
    ]

    engine = GameEngine(GAME)
    engine.state = state
    engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
    engine._vote_service = service
    engine._active_vote_window_id = window_id
    engine.game_logger = MagicMock()

    async def exile_kills_the_sheriff(seat: int) -> bool:
        state.players[4].mark_dead("hunter_shot")
        clear_office(state, destroyed=True)
        return False

    engine._apply_exile = AsyncMock(side_effect=exile_kills_the_sheriff)
    checkpoint_windows: dict[str, str | None] = {}

    async def capture_checkpoint(label: str) -> None:
        if label.startswith("vote_result:"):
            checkpoint_windows[label] = engine._active_vote_window_id

    engine._durable_checkpoint = AsyncMock(side_effect=capture_checkpoint)
    engine._check_game_over = AsyncMock(return_value=False)
    engine._broadcast_phase_change = AsyncMock()

    await engine._execute_vote_resolution()

    assert checkpoint_windows == {"vote_result:3:1:7": window_id}
    assert engine.game_logger.log_vote_result.call_args.args[3] == {7: 13, 4: 4}
    assert engine._active_vote_window_id is None


def test_the_snapshot_rejects_a_non_positive_seat() -> None:
    state = _state(sheriff=4)
    service, _ = _opened(state)
    saved = service.checkpoint()
    saved["windows"][0]["sheriff_seat"] = 0

    restored = VoteService(state, clock=lambda: 200.0)
    with pytest.raises(ValueError):
        restored.restore_checkpoint(saved)


def test_a_no_sheriff_window_round_trips_with_none() -> None:
    state = _state(sheriff=None)
    service, window_id = _opened(state)
    saved = service.checkpoint()
    assert saved["windows"][0]["sheriff_seat"] is None

    restored = VoteService(state, clock=lambda: 200.0)
    restored.restore_checkpoint(saved)
    assert restored.tally(window_id) == {7: 6, 4: 2}
    assert restored.window_weight(window_id, 1) == 1
