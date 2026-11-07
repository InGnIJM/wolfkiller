"""``sheriff_vote_decision``: the system-one spec derived from office state.

The choice question's options must be exactly what ``vote_turn`` would call
legal — the live candidate set for the step plus ``pass`` and, for a wolf the
pack allows to explode, ``explode``. Anything that cannot be derived returns
``None`` so the caller falls back to the seat model.
"""

from app.core.sheriff_flow import sheriff_vote_decision
from app.models.game import GameConfig, GameState, PlayerState


def _state(*, wolves=(), step="vote", active=(), pk=(), roles=None):
    state = GameState(game_id="g", config=GameConfig())
    state.players = {}
    for seat in range(1, 5):
        wolf = seat in wolves
        role = (roles or {}).get(
            seat,
            "wolf-killer-werewolf" if wolf else "wolf-killer-villager",
        )
        state.players[seat] = PlayerState(
            seat_number=seat,
            role=role,
            camp="werewolf" if wolf else "good",
        )
    office = state.sheriff_office
    office.step = step
    office.active = set(active)
    office.pk_seats = set(pk)
    return state


def test_vote_step_uses_the_active_candidates():
    state = _state(active={1, 3})

    decision = sheriff_vote_decision(state, 2)

    question = decision["questions"]["sheriff_vote"]
    assert question["type"] == "choice"
    assert question["instructions"]
    assert set(question["criteria"]) == {"1", "3", "pass"}
    assert question["criteria"]["3"] == "投票给3号当选警长"
    assert decision["answer_map"] == {
        "1": {"action_type": "vote", "target_seat": 1},
        "3": {"action_type": "vote", "target_seat": 3},
        "pass": {"action_type": "pass"},
    }


def test_pk_step_uses_the_runoff_candidates():
    state = _state(step="pk", active={1}, pk={2, 4})

    decision = sheriff_vote_decision(state, 3)

    assert set(decision["questions"]["sheriff_vote"]["criteria"]) == {
        "2", "4", "pass",
    }


def test_non_ballot_steps_return_none():
    for step in ("", "run", "withdraw", "done"):
        state = _state(step=step, active={1})
        assert sheriff_vote_decision(state, 2) is None


def test_empty_candidate_set_returns_none():
    state = _state(active=set())

    assert sheriff_vote_decision(state, 2) is None


def test_a_pack_wolf_gets_the_explode_option():
    state = _state(wolves={2}, active={1, 3})

    decision = sheriff_vote_decision(state, 2)

    criteria = decision["questions"]["sheriff_vote"]["criteria"]
    assert "explode" in criteria
    assert decision["answer_map"]["explode"] == {"action_type": "explode"}


def test_a_good_seat_gets_no_explode_option():
    state = _state(wolves={1}, active={1, 3})

    decision = sheriff_vote_decision(state, 4)

    assert "explode" not in decision["questions"]["sheriff_vote"]["criteria"]
    assert "explode" not in decision["answer_map"]


def test_a_wolf_forbidden_to_explode_gets_no_option():
    """Camp membership alone is not enough — the card must allow the move.

    A wolf-beauty (``self_explode_forbidden`` on the frozen spec) still counts
    as werewolf camp, so without the ``_may_explode`` gate JEV would offer a
    ballot option the parser could never honour.
    """
    state = _state(
        wolves={2}, active={1, 3}, roles={2: "wolf-killer-wolf-beauty"},
    )

    decision = sheriff_vote_decision(state, 2)

    assert "explode" not in decision["questions"]["sheriff_vote"]["criteria"]
    assert "explode" not in decision["answer_map"]


def test_a_wolf_with_an_unregistered_role_keeps_the_option():
    """A werewolf-camp seat whose role is absent from the registry has no
    ``self_explode_forbidden`` spec to consult, so the resource check alone
    decides — and a bare werewolf camp player may explode."""
    state = _state(wolves={2}, active={1, 3}, roles={2: "unregistered-wolf"})

    decision = sheriff_vote_decision(state, 2)

    assert "explode" in decision["questions"]["sheriff_vote"]["criteria"]


def test_an_unknown_seat_gets_no_explode_option():
    state = _state(wolves={1}, active={1, 3})

    decision = sheriff_vote_decision(state, 9)

    assert "explode" not in decision["questions"]["sheriff_vote"]["criteria"]
