"""A seat that *looks like* another identity to whoever inspects it.

The Awakened Hidden Wolf imitates a player and "immediately takes on the target's
identity". The rules only make that observable one way: an inspection reads the
imitated identity instead of the real one. Nothing else moves — the real camp
still decides the win, the vote, the wolf roster, and the dance pool's minority.

The record is an ordinary private-data slot, ``disguise``, written by the role's
own hook. This module is the single reader of it, so the projector's
selected-target rows (the only place an inspection learns an identity) can honour
it without the engine or any other core module knowing which role writes it.
"""

from __future__ import annotations

from app.core.role_runtime import role_private_data_view
from app.models.game import GameState

#: The private-data slot: ``{"role", "camp", "seat", "round"}`` once imitated.
DISGUISE_KEY = "disguise"


def disguised_identity(state: GameState, seat: int) -> tuple[str, str] | None:
    """The ``(role_id, camp)`` a seat currently shows, or ``None`` when it shows its own."""
    record = role_private_data_view(state, seat).get(DISGUISE_KEY)
    if not hasattr(record, "get"):
        return None
    role, camp = record.get("role"), record.get("camp")
    if type(role) is not str or type(camp) is not str or not role or not camp:
        return None
    return role, camp
