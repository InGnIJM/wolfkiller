import pytest

from app.core.effect_applier import (
    EffectApplier,
    EffectPermission,
    EffectRejected,
    derive_effect_id,
)
from app.models.game import GameState, PlayerState
from app.models.pipeline import EffectKind, GameEffect


def make_state() -> GameState:
    state = GameState(game_id="g")
    state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", "good")
        for seat in (1, 2)
    }
    return state


def permission(*targets: int) -> EffectPermission:
    return EffectPermission(
        1, frozenset({EffectKind.RECORD_VOTE}),
        frozenset({EffectKind.RECORD_VOTE}), frozenset(targets), frozenset(),
    )


def vote_effects(
    *, key: str = "vote-key", target: int | None = 2,
    payload_updates: dict | None = None, effect_target: int | None = None,
) -> tuple[GameEffect, GameEffect]:
    payload = {
        "window_id": "window", "action_key": key, "voter_seat": 1,
        "round_number": 1, "vote_round": 1, "status": "accepted_vote",
        "target_seat": target, "command_digest": "a" * 64,
        "accepted_at": 1.0, "failure_code": None, "timeout_type": None,
    }
    payload.update(payload_updates or {})
    actual_target = target if effect_target is None else effect_target
    return (
        GameEffect(
            derive_effect_id(key, 0), EffectKind.ACCEPT_ACTION, key,
            payload={
                "actor_seat": 1, "contract_id": "cast_vote",
                "window_id": "window", "round_number": 1,
            }, expected_revision=0, sort_key=(0,),
        ),
        GameEffect(
            derive_effect_id(key, 1), EffectKind.RECORD_VOTE, key,
            target_seat=actual_target, payload=payload,
            expected_revision=0, sort_key=(1,),
        ),
    )


@pytest.mark.parametrize(
    ("updates", "target", "effect_target", "message"),
    [
        ({"action_key": "other"}, 2, 2, "vote action key mismatch"),
        ({"voter_seat": 9}, 2, 2, "vote actor does not exist"),
        ({"accepted_at": 1}, 2, 2, "invalid vote accepted_at"),
        ({"accepted_at": -1.0}, 2, 2, "invalid vote accepted_at"),
        ({"status": "unknown"}, 2, 2, "invalid vote status"),
        ({"target_seat": 9}, 9, 9, "vote target does not exist"),
        ({}, 2, 1, "vote target does not match target_seat"),
        ({"status": "voluntary_abstain"}, 2, 2, "vote status and target disagree"),
        ({"status": "accepted_vote", "target_seat": None}, None, None, "vote status and target disagree"),
        ({"status": "technical_abstain", "target_seat": None}, None, None, "technical vote failure metadata invalid"),
        ({"failure_code": "provider_error"}, 2, 2, "technical vote failure metadata invalid"),
    ],
)
def test_record_vote_rejects_invalid_terminal_payloads(
    updates, target, effect_target, message,
):
    state = make_state()
    effects = vote_effects(
        target=target, payload_updates=updates, effect_target=effect_target,
    )

    with pytest.raises(EffectRejected, match=message):
        EffectApplier().apply(state, effects, permission(1, 2, 9))


@pytest.mark.parametrize(
    "corrupt",
    [
        [],
        {1: {}},
        {"wrong": {"action_key": "different"}},
    ],
)
def test_runtime_clone_rejects_corrupt_vote_receipt_ledgers(corrupt):
    state = make_state()
    EffectApplier().apply(state, vote_effects(), permission(1, 2))
    state._pipeline_runtime.vote_receipts = corrupt

    with pytest.raises(EffectRejected, match="invalid pipeline runtime"):
        EffectApplier().apply(
            state, vote_effects(key="second"), permission(1, 2),
        )


@pytest.mark.parametrize("field", ["votes", "voted_seats"])
def test_record_vote_rejects_corrupt_legacy_projection(field):
    state = make_state()
    setattr(state, field, ())

    with pytest.raises(EffectRejected, match="invalid vote projection"):
        EffectApplier().apply(state, vote_effects(), permission(1, 2))


def test_record_vote_rejects_existing_projection_for_same_voter():
    state = make_state()
    state.voted_seats.add(1)

    with pytest.raises(EffectRejected, match="vote projection conflict"):
        EffectApplier().apply(state, vote_effects(), permission(1, 2))


def test_record_vote_rejects_legacy_commit_without_matching_receipt():
    state = make_state()
    key = "vote-key"
    accept_only = (vote_effects(key=key)[0],)
    EffectApplier().apply(state, accept_only, permission(1, 2))

    with pytest.raises(EffectRejected, match="vote receipt missing"):
        EffectApplier().apply(state, vote_effects(key=key), permission(1, 2))


def test_record_vote_effect_replay_returns_original_commit():
    state = make_state()
    effects = vote_effects()
    original = EffectApplier().apply(state, effects, permission(1, 2))

    replay = EffectApplier().apply(state, effects, permission(1, 2))

    assert replay is original
    assert len(state.votes) == 1


def test_record_vote_effect_replay_rejects_changed_semantic_digest():
    state = make_state()
    EffectApplier().apply(state, vote_effects(), permission(1, 2))
    changed = vote_effects(payload_updates={"command_digest": "b" * 64})

    with pytest.raises(EffectRejected, match="vote_conflict"):
        EffectApplier().apply(state, changed, permission(1, 2))


def test_non_vote_effect_replay_does_not_require_vote_receipt():
    state = make_state()
    accept_only = (vote_effects(key="plain-action")[0],)
    original = EffectApplier().apply(state, accept_only, permission(1, 2))

    replay = EffectApplier().apply(state, accept_only, permission(1, 2))

    assert replay is original
