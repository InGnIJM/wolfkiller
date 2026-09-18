from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models.game import GamePhase, GameState, PlayerState
from app.models.vote import CastVoteArgs, VoteError, VoteStatus


def make_state() -> GameState:
    state = GameState(game_id="g", phase=GamePhase.VOTE_CASTING, round_number=3)
    state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", "good")
        for seat in (1, 2, 3)
    }
    return state


def vote(target: int, reasoning: str = "reason") -> CastVoteArgs:
    return CastVoteArgs(
        action_type="vote", target_seat=target, reasoning=reasoning,
    )


def test_checkpoint_restores_remaining_time_instead_of_old_monotonic_deadline():
    from app.core.vote_service import VoteService

    state = make_state()
    now = [100.0]
    service = VoteService(state, clock=lambda: now[0])
    window = service.open_window(timeout_seconds=30.0)
    service.arm_window(window.window_id, timeout_seconds=20.0)
    now[0] = 105.0

    snapshot = service.checkpoint()

    assert snapshot["windows"][0]["remaining_ms"] == 15000
    assert "deadline" not in snapshot["windows"][0]
    restored_now = [900.0]
    restored = VoteService(state, clock=lambda: restored_now[0])
    restored.restore_checkpoint(snapshot)
    assert restored.window(window.window_id).deadline == 915.0
    assert restored.missing_voters(window.window_id) == window.eligible_voters


def test_submit_atomically_records_receipt_and_legacy_projection():
    from app.core.vote_service import VoteService

    state = make_state()
    service = VoteService(state, clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=30.0)

    receipt = service.submit(window.window_id, 1, vote(2))

    assert receipt.status is VoteStatus.ACCEPTED_VOTE
    assert receipt.target_seat == 2
    assert state.voted_seats == {1}
    assert [(item.voter_seat, item.target_seat) for item in state.votes] == [(1, 2)]
    assert service.tally(window.window_id) == {2: 1}


def test_same_semantic_vote_replays_but_changed_target_conflicts():
    from app.core.vote_service import VoteService

    state = make_state()
    service = VoteService(state, clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=30.0)
    original = service.submit(window.window_id, 1, vote(2, "first"))

    replay = service.submit(window.window_id, 1, vote(2, "retry"))

    assert replay.replayed is True
    assert replay.command_digest == original.command_digest
    with pytest.raises(VoteError, match="vote_conflict"):
        service.submit(window.window_id, 1, vote(3))
    assert service.tally(window.window_id) == {2: 1}


def test_concurrent_different_submissions_commit_exactly_one_ballot():
    from app.core.vote_service import VoteService

    state = make_state()
    service = VoteService(state, clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=30.0)

    def submit(target):
        try:
            return service.submit(window.window_id, 1, vote(target)).status.value
        except VoteError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (2, 3)))

    assert sorted(results) == ["accepted_vote", "vote_conflict"]
    assert len(service.receipts(window.window_id)) == 1
    assert len(state.votes) == 1


def test_permissions_and_window_deadline_are_server_enforced():
    from app.core.vote_service import VoteService

    now = [10.0]
    state = make_state()
    service = VoteService(state, clock=lambda: now[0])
    window = service.open_window(timeout_seconds=5.0)

    state.players[1].is_alive = False
    with pytest.raises(VoteError, match="vote_actor_ineligible"):
        service.submit(window.window_id, 1, vote(2))
    state.players[1].is_alive = True
    state.players[2].is_alive = False
    with pytest.raises(VoteError, match="vote_target_ineligible"):
        service.submit(window.window_id, 1, vote(2))
    state.players[2].is_alive = True
    now[0] = 15.0
    with pytest.raises(VoteError, match="vote_window_closed"):
        service.submit(window.window_id, 1, vote(2))


def test_close_window_creates_terminal_technical_abstentions_for_every_missing_voter():
    from app.core.vote_service import VoteService

    now = [10.0]
    state = make_state()
    service = VoteService(state, clock=lambda: now[0])
    window = service.open_window(timeout_seconds=5.0)
    service.submit(window.window_id, 1, vote(2))
    now[0] = 15.0

    receipts = service.close_window(
        window.window_id, failure_code="request_timeout",
        timeout_type="phase_deadline",
    )

    assert [item.status for item in receipts] == [
        VoteStatus.ACCEPTED_VOTE,
        VoteStatus.TECHNICAL_ABSTAIN,
        VoteStatus.TECHNICAL_ABSTAIN,
    ]
    assert {item.voter_seat for item in receipts} == {1, 2, 3}
    assert state.voted_seats == {1, 2, 3}
    assert service.missing_voters(window.window_id) == frozenset()


def test_service_rejects_invalid_construction_window_and_command_inputs():
    from app.core.vote_service import VoteService

    with pytest.raises(TypeError, match="state must be GameState"):
        VoteService(object())
    state = make_state()
    state.phase = GamePhase.SPEECH
    service = VoteService(state, clock=lambda: 10.0)
    with pytest.raises(VoteError, match="vote_wrong_phase"):
        service.open_window(timeout_seconds=1.0)
    state.phase = GamePhase.VOTE_CASTING
    with pytest.raises(ValueError, match="positive float"):
        service.open_window(timeout_seconds=0.0)
    with pytest.raises(VoteError, match="vote_window_not_found"):
        service.window("missing")
    window = service.open_window(timeout_seconds=1.0)
    with pytest.raises(TypeError, match="command must be CastVoteArgs"):
        service.submit(window.window_id, 1, object())


def test_window_is_idempotent_and_cannot_close_before_deadline():
    from app.core.vote_service import VoteService

    state = make_state()
    service = VoteService(state, clock=lambda: 10.0)
    first = service.open_window(timeout_seconds=5.0)
    second = service.open_window(timeout_seconds=20.0)

    assert second is first
    with pytest.raises(VoteError, match="vote_deadline_not_reached"):
        service.close_window(first.window_id, failure_code="request_timeout")


def test_arm_window_starts_phase_deadline_after_orchestration_setup():
    from app.core.vote_service import VoteService

    now = [10.0]
    state = make_state()
    service = VoteService(state, clock=lambda: now[0])
    opened = service.open_window(timeout_seconds=5.0)
    now[0] = 12.0

    armed = service.arm_window(opened.window_id, timeout_seconds=5.0)

    assert armed.window_id == opened.window_id
    assert armed.deadline == 17.0
    assert service.window(opened.window_id) is armed
    with pytest.raises(ValueError, match="positive float"):
        service.arm_window(opened.window_id, timeout_seconds=0.0)


def test_voluntary_and_technical_abstentions_are_distinct_terminal_results():
    from app.core.vote_service import VoteService

    state = make_state()
    service = VoteService(state, clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=5.0)

    voluntary = service.submit(
        window.window_id, 1,
        CastVoteArgs(action_type="abstain", target_seat=None, reasoning="unsure"),
    )
    technical = service.technical_abstain(
        window.window_id, 2, failure_code="provider_rate_limit",
    )

    assert voluntary.status is VoteStatus.VOLUNTARY_ABSTAIN
    assert technical.status is VoteStatus.TECHNICAL_ABSTAIN
    assert technical.failure_code == "provider_rate_limit"
    assert service.tally(window.window_id) == {}


def test_state_change_after_window_open_is_revalidated():
    from app.core.vote_service import VoteService

    state = make_state()
    service = VoteService(state, clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=5.0)
    state.round_number = 4

    with pytest.raises(VoteError, match="vote_wrong_phase"):
        service.submit(window.window_id, 1, vote(2))


def test_submission_received_before_deadline_survives_lock_and_validation_delay():
    from app.core.vote_service import VoteService

    now = [10.0]
    state = make_state()
    service = VoteService(state, clock=lambda: now[0])
    window = service.open_window(timeout_seconds=5.0)
    now[0] = 16.0

    receipt = service.submit(
        window.window_id, 1, vote(2), received_at=14.9,
    )

    assert receipt.status is VoteStatus.ACCEPTED_VOTE


def test_submission_rejects_invalid_server_arrival_timestamp():
    from app.core.vote_service import VoteService

    state = make_state()
    service = VoteService(state, clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=5.0)

    with pytest.raises(TypeError, match="received_at"):
        service.submit(window.window_id, 1, vote(2), received_at=-1.0)


def test_effect_failures_are_mapped_or_propagated_and_missing_receipt_is_detected(monkeypatch):
    from app.core.effect_applier import EffectApplier, EffectRejected
    from app.core.vote_service import VoteService

    state = make_state()
    service = VoteService(state, clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=5.0)

    def conflict(*args, **kwargs):
        raise EffectRejected("vote_conflict")

    monkeypatch.setattr(EffectApplier, "apply", conflict)
    with pytest.raises(VoteError, match="vote_conflict"):
        service.submit(window.window_id, 1, vote(2))

    def rejected(*args, **kwargs):
        raise EffectRejected("effect permission denied")

    monkeypatch.setattr(EffectApplier, "apply", rejected)
    with pytest.raises(EffectRejected, match="effect permission denied"):
        service.submit(window.window_id, 1, vote(2))

    monkeypatch.setattr(EffectApplier, "apply", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="did not produce a receipt"):
        service.submit(window.window_id, 1, vote(2))



@pytest.mark.parametrize("corruption", [
    "root", "lists", "entry", "negative_time", "invalid_voters",
    "wrong_id", "duplicate_window", "unknown_closed", "duplicate_closed",
])
def test_invalid_vote_checkpoint_never_replaces_existing_windows(corruption):
    import copy
    from app.core.vote_service import VoteService

    service = VoteService(make_state(), clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=5.0)
    original = service.checkpoint()
    value = copy.deepcopy(original)
    if corruption == "root": value = []
    elif corruption == "lists": value["closed"] = "not-a-list"
    elif corruption == "entry": value["windows"][0] = {}
    elif corruption == "negative_time": value["windows"][0]["remaining_ms"] = -1
    elif corruption == "invalid_voters": value["windows"][0]["eligible_voters"] = None
    elif corruption == "wrong_id": value["windows"][0]["window_id"] = "different"
    elif corruption == "duplicate_window": value["windows"].append(value["windows"][0])
    elif corruption == "unknown_closed": value["closed"] = ["unknown"]
    elif corruption == "duplicate_closed": value["closed"] = [window.window_id, window.window_id]
    with pytest.raises(ValueError):
        service.restore_checkpoint(value)
    assert service.checkpoint() == original


def test_runtime_statuses_shape_exile_voters_and_targets():
    from app.core.effect_applier import _Runtime
    from app.core.vote_service import (
        EXILE_IMMUNE_STATUS, NO_VOTE_STATUS, VoteService,
        eligible_exile_targets, eligible_exile_voters, seats_with_status,
    )

    state = make_state()
    assert eligible_exile_voters(state) == eligible_exile_targets(state) == frozenset({1, 2, 3})
    state._pipeline_runtime = _Runtime(statuses={
        1: {NO_VOTE_STATUS, EXILE_IMMUNE_STATUS}, 2: {"unrelated"}, 3: "not-a-set",
    })
    assert seats_with_status(state, NO_VOTE_STATUS) == frozenset({1})
    assert eligible_exile_voters(state) == frozenset({2, 3})
    assert eligible_exile_targets(state) == frozenset({2, 3})

    window = VoteService(state, clock=lambda: 10.0).open_window(timeout_seconds=5.0)
    assert window.eligible_voters == frozenset({2, 3})
    assert window.eligible_targets == frozenset({2, 3})


def test_tally_uses_sheriff_half_votes_when_office_is_active():
    from app.core.sheriff_flow import set_sheriff
    from app.core.vote_service import VoteService

    state = make_state()
    state.config.enable_sheriff = True
    set_sheriff(state, 1)
    service = VoteService(state, clock=lambda: 10.0)
    window = service.open_window(timeout_seconds=30.0)
    service.submit(window.window_id, 1, vote(3))
    service.submit(window.window_id, 2, vote(2))
    service.submit(window.window_id, 3, vote(2))
    assert service.tally(window.window_id) == {3: 3, 2: 4}


def test_seats_with_status_rejects_invalid_inputs():
    from app.core.vote_service import seats_with_status

    with pytest.raises(TypeError, match="GameState"):
        seats_with_status(object(), "no_vote")
    for status in ("", None):
        with pytest.raises(ValueError, match="non-empty"):
            seats_with_status(make_state(), status)
