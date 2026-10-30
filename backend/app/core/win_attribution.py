"""Who actually won, seat by seat, and which side each seat is on right now.

The camp that ends an ordinary game is decided by the living counts and never
moves. Two private slots can move a seat's *allegiance* without touching its
stored camp:

* ``lover_pair`` on the seat that linked two players. Both lovers good, and the
  linker counts as good; both werewolves, and the linker counts as a werewolf;
  any other pair, and the linker plus both lovers are the third party.

A ``crush`` never moves the side a seat is counted on. The Admirer stays a
villager: a seer reads it as good, and the wolves have to kill it to win by
屠民. Only its *victory* follows the crush, so following a lover who was pulled
into the third party wins with that third party. The official rulings say it
three ways (admirer counts as a villager, a check reads it as good, and "all
wolves out" ends the game even when the crush was a wolf), which is why the
crush lives in ``winner_seats`` and not in ``seat_allegiances``.

With neither slot written, allegiance is the stored camp and the public result
stays what it has always been.

Two rules move only *victory*, never the living counts: the Lonely Girl follows
the seat she worships, and the Admirer follows the seat it crushes on.
``winner_seats`` returns ``None`` when nothing in the game would put a seat on a
side other than its own camp, so boards without these slots keep the historical
shape.
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


def _victory_side(state: GameState, seat: int, allegiances: Mapping[int, str]) -> str:
    """The side ``seat`` wins with: its own, or the end of its ``crush`` chain.

    The crush redirects only where a seat's *victory* is counted. A cycle stops
    on the last new seat, so a hand-written or replayed slot can never hang the
    win check.
    """
    seen: set[int] = set()
    current = seat
    while True:
        nxt = crush_seat(state, current)
        if nxt is None or nxt in seen:
            return allegiances[current]
        seen.add(current)
        current = nxt


def seat_allegiances(state: GameState) -> dict[int, str]:
    """Each seat's side for the win check: ``good``, ``werewolf`` or ``third_party``.

    This is the *counted* side, so the crush is deliberately absent: an Admirer
    stays a villager however its crush's side turns out. Only ``winner_seats``
    redirects a victory.
    """
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
    return resolved


def winner_seats(state: GameState, winning_camp: str) -> tuple[int, ...] | None:
    """Every seat that shares the win, or ``None`` when it is the plain camp.

    A seat wins with its own side unless a private slot moves only its victory:
    an idol follows the worshipped seat's stored camp, and a crush walks the
    chain to the side the crush wins with (a link may have moved that side into
    the third party). The counted sides themselves never move for a crush.
    """
    idols = {seat: idol_seat(state, seat) for seat in state.players}
    allegiances = seat_allegiances(state)
    crushes = {seat: crush_seat(state, seat) for seat in state.players}
    redirected = any(
        allegiances[seat] != _camp_value(player.camp)
        for seat, player in state.players.items()
    )
    if (
        not any(idol is not None for idol in idols.values())
        and not any(crush is not None for crush in crushes.values())
        and not redirected
    ):
        return None
    winners = []
    for seat, player in sorted(state.players.items()):
        idol = idols[seat]
        if idol is not None:
            side = _camp_value(state.players[idol].camp)
        else:
            side = _victory_side(state, seat, allegiances)
        if side == winning_camp:
            winners.append(seat)
    return tuple(winners)
