"""The published ballot must agree with the exile it is published next to.

``VOTE_RESULT.counts`` used to be a plain head count while the exile is decided
by ``ballot_weight`` — sheriff 3, everyone else 2, a half-vote unit. With a
sheriff in play the two numbers can disagree: the seat that actually goes out
can look tied, or even behind, in the tally printed beside it. The projection now
publishes both, so the decision is auditable from the record alone.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.sheriff_flow import ballot_weight_for
from app.models.actions import VoteAction
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.services.audience_projector import AudienceProjector
from app.services.game_service import GameService

GAME = "tally"
SEATS = {
    1: ("wolf-killer-seer", "good"),
    2: ("wolf-killer-villager", "good"),
    3: ("wolf-killer-villager", "good"),
    4: ("wolf-killer-werewolf", "werewolf"),
}


def engine_with(votes, *, sheriff: int | None) -> SimpleNamespace:
    state = GameState(
        game_id=GAME, phase=GamePhase.VOTE_RESOLUTION, round_number=1,
        config=GameConfig(enable_sheriff=True),
    )
    state.players = {
        seat: PlayerState(seat, role_id, camp, is_sheriff=(seat == sheriff))
        for seat, (role_id, camp) in SEATS.items()
    }
    state.sheriff = sheriff
    state.votes = list(votes)
    return SimpleNamespace(game_id=GAME, state=state)


def result_frame(engine, exiled: int | None) -> dict:
    label = "vote_result:1:1:" + ("none" if exiled is None else str(exiled))
    events = GameService._checkpoint_domain_events(engine, f"0:{label}")
    assert len(events) == 1
    assert events[0]["event_type"] == "VOTE_RESULT"
    return events[0]["payload"]


def test_both_counts_are_published() -> None:
    """Without a sheriff every ballot weighs one, so the two agree."""
    engine = engine_with([VoteAction(1, 4), VoteAction(2, 4)], sheriff=None)
    payload = result_frame(engine, 4)
    assert payload["counts"] == {"4": 2}
    assert payload["weighted_counts"] == {"4": 2}


def test_the_sheriff_ballot_is_visible_in_the_weighted_tally() -> None:
    """The case the head count hides: 1.5 votes against 1."""
    engine = engine_with([VoteAction(1, 4), VoteAction(2, 3)], sheriff=1)
    payload = result_frame(engine, 4)
    assert payload["counts"] == {"3": 1, "4": 1}          # looks tied
    assert payload["weighted_counts"] == {"3": 2, "4": 3}  # the sheriff decides it


def test_the_exile_is_the_unique_maximum_of_the_weighted_tally() -> None:
    engine = engine_with(
        [VoteAction(1, 4), VoteAction(2, 3), VoteAction(3, 4)], sheriff=1,
    )
    payload = result_frame(engine, 4)
    weighted = payload["weighted_counts"]
    top = [seat for seat, value in weighted.items() if value == max(weighted.values())]
    assert top == [str(payload["exiled_seat"])]


def test_the_published_tally_uses_the_frozen_window_basis() -> None:
    """The 37f99754 round-3 shape: the sheriff is dead when the frame is built.

    The engine's ``vote_ballot_weight`` reads the sheriff snapshot taken when
    the ballot opened. A projection that re-read the live office (none here)
    would print the head count 6/2 for a decision made at 13/4.
    """
    votes = [
        VoteAction(1, 7), VoteAction(2, 7), VoteAction(3, 4), VoteAction(4, 7),
        VoteAction(5, 7), VoteAction(7, 4), VoteAction(9, 7), VoteAction(10, 7),
    ]
    engine = engine_with(votes, sheriff=None)
    engine.vote_ballot_weight = lambda seat: ballot_weight_for(4, seat)

    payload = result_frame(engine, 7)

    assert payload["counts"] == {"4": 2, "7": 6}
    assert payload["weighted_counts"] == {"4": 4, "7": 13}


def test_a_tie_has_no_unique_maximum() -> None:
    engine = engine_with([VoteAction(2, 4), VoteAction(3, 3)], sheriff=None)
    payload = result_frame(engine, None)
    assert payload["exiled_seat"] is None
    weighted = payload["weighted_counts"]
    assert len([seat for seat, value in weighted.items()
                if value == max(weighted.values())]) == 2


def test_abstentions_never_reach_either_tally() -> None:
    engine = engine_with(
        [VoteAction(1, 4), VoteAction(2, None), VoteAction(3, 0)], sheriff=None,
    )
    payload = result_frame(engine, 4)
    assert payload["counts"] == {"4": 1}
    assert payload["weighted_counts"] == {"4": 1}


def test_a_seat_number_alone_carries_no_weight() -> None:
    """``ballot_weight`` follows the live office, not the seat number."""
    engine = engine_with([VoteAction(1, 4), VoteAction(2, 3)], sheriff=None)
    payload = result_frame(engine, None)
    assert payload["weighted_counts"] == {"3": 1, "4": 1}
    assert payload["counts"] == {"3": 1, "4": 1}


def test_the_projection_keeps_the_weighted_tally() -> None:
    """域事件带上加权票型还不够：投影器按白名单**过滤**载荷。

    `weighted_counts` 曾不在 `_EVENTS["VOTE_RESULT"]` 的允许集里，于是观众流与回放
    都只有人头数，`game_routes._AUDIENCE_ACTION_OPTIONAL["VOTE_RESULT"]` 成了一条
    永远不会被触发的配置。
    """
    engine = engine_with([VoteAction(1, 4), VoteAction(2, 3)], sheriff=1)
    payload = result_frame(engine, 4)
    event = {
        "event_id": "domain:tally:1", "event_type": "VOTE_RESULT",
        "payload": payload, "visibility": ["PUBLIC"], "schema_version": 1,
    }

    projected = AudienceProjector().project_events(GAME, [event])

    assert projected[0]["payload"]["weighted_counts"] == {"3": 2, "4": 3}
    assert projected[0]["payload"]["counts"] == {"3": 1, "4": 1}
