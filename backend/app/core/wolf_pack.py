"""Who sits in the wolf pack, and who only counts as a werewolf.

Werewolf-camp membership and *pack* membership are two different things. The win
check (``core/rule_engine.py``) reads ``player.camp`` and must keep seeing every
werewolf, hidden or not. The nightly discussion, the kill vote, the wolf channel,
the teammate lists and the ``CAMP`` projection all read *pack* membership, and a
role that declares ``no_wolf_pack`` is excluded from every one of them.

The marker travels as a resource because the kill contract is byte-identical
across the pack: a per-role difference cannot live in the declaration, so it is
declared as ``initial_resources`` and read here. ``wolf_knows_pack`` is the
one-way variant — the Eclipse Maid sees the pack, the pack does not see her.

Defined in one module so the six read sites cannot drift apart.
"""

from __future__ import annotations

from app.core.role_runtime import has_role_marker
from app.models.game import Camp, GameState

#: Declared by a werewolf-camp role that is invisible to its own team.
NO_WOLF_PACK_RESOURCE = "no_wolf_pack"
#: Declared alongside ``no_wolf_pack`` by a role that still sees the pack.
WOLF_KNOWS_PACK_RESOURCE = "wolf_knows_pack"


def in_wolf_pack(state: GameState, seat: int) -> bool:
    """True when ``seat`` is a werewolf *the pack itself can see*.

    A hidden wolf answers ``False``: it never joins the discussion or the vote,
    and no teammate is told about it. It still answers ``True`` to
    ``is_werewolf_camp`` for the win check.
    """
    player = state.players.get(seat)
    if player is None or player.camp != Camp.WEREWOLF:
        return False
    return not has_role_marker(state, seat, NO_WOLF_PACK_RESOURCE)


def is_werewolf_camp(player: object) -> bool:
    """The win-check predicate: hidden wolves count, always."""
    return getattr(player, "camp", None) == Camp.WEREWOLF


def pack_seats(state: GameState) -> tuple[int, ...]:
    """Every seat the pack can see, in seat order."""
    return tuple(sorted(
        seat for seat in state.players if in_wolf_pack(state, seat)
    ))


def actor_sees_pack(state: GameState, seat: int) -> bool:
    """Whether ``seat`` is allowed to learn who its teammates are.

    A plain wolf does. A hidden wolf does not, unless it also declares
    ``wolf_knows_pack``. The asymmetry is the point: the Gargoyle may shoot a
    teammate it cannot identify, and that gamble is the whole board.

    A seat outside the werewolf camp never does — the gate is camp *and* pack,
    so it is safe to use as the only check where the old code tested the camp.
    """
    player = state.players.get(seat)
    if player is None or player.camp != Camp.WEREWOLF:
        return False
    return not has_role_marker(state, seat, NO_WOLF_PACK_RESOURCE) or has_role_marker(
        state, seat, WOLF_KNOWS_PACK_RESOURCE
    )
