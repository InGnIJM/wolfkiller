import importlib.util

import pytest
from pydantic import ValidationError


def test_vote_model_module_exists():
    assert importlib.util.find_spec("app.models.vote") is not None


def test_cast_vote_args_enforces_action_target_relationship_and_strict_types():
    from app.models.vote import CastVoteArgs

    vote = CastVoteArgs(action_type="vote", target_seat=2, reasoning="because")
    abstain = CastVoteArgs(action_type="abstain", target_seat=None, reasoning="")

    assert vote.target_seat == 2
    assert abstain.target_seat is None
    with pytest.raises(ValidationError, match="vote requires target_seat"):
        CastVoteArgs(action_type="vote", target_seat=None, reasoning="")
    with pytest.raises(ValidationError, match="abstain forbids target_seat"):
        CastVoteArgs(action_type="abstain", target_seat=2, reasoning="")
    with pytest.raises(ValidationError):
        CastVoteArgs(action_type="vote", target_seat="2", reasoning="")
    with pytest.raises(ValidationError, match="target_seat must be positive"):
        CastVoteArgs(action_type="vote", target_seat=0, reasoning="")


def test_cast_vote_digest_is_semantic_and_excludes_reasoning():
    from app.models.vote import CastVoteArgs

    first = CastVoteArgs(action_type="vote", target_seat=2, reasoning="first")
    retry = CastVoteArgs(action_type="vote", target_seat=2, reasoning="changed")
    conflict = CastVoteArgs(action_type="vote", target_seat=3, reasoning="first")

    assert first.command_digest() == retry.command_digest()
    assert first.command_digest() != conflict.command_digest()


def test_vote_window_binds_server_owned_identity_and_action_key():
    from app.models.vote import VoteWindow

    window = VoteWindow(
        game_id="g", round_number=3, vote_round=2,
        eligible_voters=frozenset({1, 2}), eligible_targets=frozenset({1, 2}),
        deadline=12.5,
    )

    assert window.window_id == "g:3:vote_casting:2:cast_vote:v2"
    assert window.action_key(2) == "g:3:vote_casting:2:cast_vote:v2:2"


def test_vote_receipt_replay_preserves_original_terminal_result():
    from app.models.vote import VoteReceipt, VoteStatus

    receipt = VoteReceipt(
        window_id="w", action_key="w:1", voter_seat=1,
        status=VoteStatus.ACCEPTED_VOTE, target_seat=2,
        command_digest="a" * 64, accepted_at=10.0,
    )

    replay = receipt.as_replay()

    assert replay.replayed is True
    assert replay.target_seat == 2
    assert receipt.replayed is False


def test_vote_error_exposes_stable_code():
    from app.models.vote import VoteError

    error = VoteError("vote_conflict")

    assert error.code == "vote_conflict"
    assert str(error) == "vote_conflict"
