"""Who actually won, seat by seat.

The camp that ends a game is decided by the living counts and never moves. The
Lonely Girl does not change that: her *victory* follows the player she worships,
so when her idol's camp takes the game she shares the win even though her own
camp lost — and the other way round. That is the only per-seat rule this project
carries, so it is answered here, once, and read by the rule engine.

``None`` means "nothing in this game follows anybody": the result is exactly the
camp-based one every other board produces, and the public shape stays what it
has always been (``winners`` is emitted only when it can differ from the camp).

The idol is stored as private data on the girl's own seat, the same generic slot
``SET_PRIVATE_DATA`` writes and the projector reads back for her prompt — so no
new ``GameState`` field, and therefore no change to the checkpoint field set.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.models.game import GameState
from app.core.role_runtime import role_private_data_view

#: The private slot the Lonely Girl writes her worship choice into.
IDOL_KEY = "idol"


def _camp_value(camp: object) -> object:
    return camp.value if hasattr(camp, "value") else camp


def idol_seat(state: GameState, seat: int) -> int | None:
    """The seat ``seat`` worships, or ``None`` when it follows nobody."""
    value = role_private_data_view(state, seat).get(IDOL_KEY)
    if not isinstance(value, Mapping):
        return None
    idol = value.get("seat")
    if type(idol) is not int or idol not in state.players:
        return None
    return idol


def winner_seats(state: GameState, winning_camp: str) -> tuple[int, ...] | None:
    """Every seat that shares the win, or ``None`` when it is the plain camp.

    A seat with no idol wins with its own camp; a seat that worships somebody
    wins with *that* seat's camp. The result is a seat list, not a camp: the
    whole point is that two seats of the same camp can end on opposite sides.
    """
    idols = {seat: idol_seat(state, seat) for seat in state.players}
    if not any(idol is not None for idol in idols.values()):
        return None
    winners = []
    for seat, player in sorted(state.players.items()):
        idol = idols[seat]
        camp = state.players[idol].camp if idol is not None else player.camp
        if _camp_value(camp) == winning_camp:
            winners.append(seat)
    return tuple(winners)
