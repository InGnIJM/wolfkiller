"""Who actually won, seat by seat, and which side each seat is on right now.

The camp that ends an ordinary game is decided by the living counts and never
moves. Two private slots can move a seat's *allegiance* without touching its
stored camp:

* ``lover_pair`` on the seat that linked two players. Both lovers good, and the
  linker counts as good; both werewolves, and the linker counts as a werewolf;
  any other pair, and the linker plus both lovers are the third party.
* ``crush`` on a seat that follows somebody. It takes that seat's allegiance
  after the link is applied, so following a lover who was pulled into the third
  party joins the third party too.

With neither slot written, allegiance is the stored camp and the public result
stays what it has always been.

The Lonely Girl is a different rule and stays one: her *victory* follows the
player she worships, but the living counts do not. ``winner_seats`` returns
``None`` when nothing in the game would put a seat on a side other than its
own camp, so boards without these slots keep the historical shape.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.models.game import GameState
from app.core.role_runtime import role_private_data_view

#: The private slot the Lonely Girl writes her worship choice into.
IDOL_KEY = "idol"
#: The two seats a linker joined. Shape: ``{"seats": [a, b], "round": n}``.
LOVER_PAIR_KEY = "lover_pair"
#: The other lover's seat, written onto each of the two. No identity.
LOVER_PEER_KEY = "lover_peer"
#: The seat a follower's victory copies. Shape: ``{"seat": n, "round": n}``.
CRUSH_KEY = "crush"

GOOD = "good"
WOLF = "werewolf"
THIRD = "third_party"


def _camp_value(camp: object) -> str:
    value = camp.value if hasattr(camp, "value") else camp
    return value if type(value) is str else ""


def _mapped_seat(value: object, players: Mapping[int, object]) -> int | None:
    if not isinstance(value, Mapping):
        return None
    seat = value.get("seat")
    if type(seat) is not int or seat not in players:
        return None
    return seat


def idol_seat(state: GameState, seat: int) -> int | None:
    """The seat ``seat`` worships, or ``None`` when it follows nobody."""
    return _mapped_seat(
        role_private_data_view(state, seat).get(IDOL_KEY), state.players,
    )


def lover_pair(state: GameState, seat: int) -> tuple[int, int] | None:
    """The two distinct seats ``seat`` linked, or ``None``.

    The slot is also the bond the response queue reads, so the stored value is
    a two-seat list. A mapping ``{"seats": [a, b]}`` is accepted as the same
    fact.
    """
    value = role_private_data_view(state, seat).get(LOVER_PAIR_KEY)
    seats = value.get("seats") if isinstance(value, Mapping) else value
    if isinstance(seats, (list, tuple)) and len(seats) == 2:
        first, second = seats
        if (
            type(first) is int and type(second) is int and first != second
            and first in state.players and second in state.players
        ):
            return (first, second)
    return None


def crush_seat(state: GameState, seat: int) -> int | None:
    """The seat ``seat`` follows, or ``None`` when it follows nobody."""
    return _mapped_seat(
        role_private_data_view(state, seat).get(CRUSH_KEY), state.players,
    )


def _follow_crush(state: GameState, seat: int, resolved: Mapping[int, str]) -> str:
    """Walk ``crush`` links. A cycle stops on the last new seat."""
    seen: set[int] = set()
    current = seat
    while True:
        nxt = crush_seat(state, current)
        if nxt is None or nxt in seen:
            return resolved[current]
        seen.add(current)
        current = nxt


def seat_allegiances(state: GameState) -> dict[int, str]:
    """Each seat's side for the win check: ``good``, ``werewolf`` or ``third_party``."""
    base = {
        seat: _camp_value(player.camp) for seat, player in state.players.items()
    }
    resolved = dict(base)
    for seat in state.players:
        pair = lover_pair(state, seat)
        if pair is None:
            continue
        first, second = pair
        left, right = base[first], base[second]
        if left == right == GOOD:
            resolved[seat] = GOOD
        elif left == right == WOLF:
            resolved[seat] = WOLF
        else:
            resolved[seat] = THIRD
            resolved[first] = THIRD
            resolved[second] = THIRD
    return {
        seat: _follow_crush(state, seat, resolved) for seat in state.players
    }


def winner_seats(state: GameState, winning_camp: str) -> tuple[int, ...] | None:
    """Every seat that shares the win, or ``None`` when it is the plain camp.

    A seat with no idol and no redirected allegiance wins with its own camp. A
    seat that worships somebody wins with that seat's stored camp. A redirected
    allegiance (a link or a crush) wins with the side it was moved to.
    """
    idols = {seat: idol_seat(state, seat) for seat in state.players}
    allegiances = seat_allegiances(state)
    redirected = any(
        allegiances[seat] != _camp_value(player.camp)
        for seat, player in state.players.items()
    )
    if not any(idol is not None for idol in idols.values()) and not redirected:
        return None
    winners = []
    for seat, player in sorted(state.players.items()):
        idol = idols[seat]
        if idol is not None:
            side = _camp_value(state.players[idol].camp)
        else:
            side = allegiances[seat]
        if side == winning_camp:
            winners.append(seat)
    return tuple(winners)
