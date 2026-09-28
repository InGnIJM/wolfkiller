"""Which registry identity a game's durable keys belong to.

Every persisted identity in the pipeline is derived from a registry digest: the
point-journal key, each issued request's ``window_id``/``action_key`` token, and
the role resource setup marker. Deriving them from the *live* registry is a trap
— ``RegistrySnapshot.digest`` changes with any role declaration, so after a code
change a resumed game would look up journal keys that no longer exist (the point
re-runs) and mint idempotency tokens the effect ledger has never seen.

Durable identities therefore use the digest the game was created with
(``GameState.registry_digest``), which ``GameService.create_game`` stamps from
the frozen snapshot. For a game that never crossed a code change the two are
equal, so this is a no-op for normal play; for a resumed game it is what keeps
the journal hit instead of a replay.

Whether the *live* registry is still compatible with that frozen identity is a
separate question, answered once at the recovery entry point (see
``app.persistence.recovery_compat``).
"""

from __future__ import annotations

import re

from app.models.game import GameState
from app.roles.registry import RegistrySnapshot

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def frozen_registry_digest(state: GameState, registry: RegistrySnapshot) -> str:
    """The registry digest this game's durable identities are keyed by.

    Falls back to the live digest when the state carries none (legacy states and
    tests that build a bare ``GameState``), which keeps pre-existing behavior.
    The registry is only consulted on that fallback path, so a state that knows
    its own identity never depends on the caller's registry object; the digest
    is an opaque key, so the fallback only requires a non-empty string.
    """
    if type(state) is not GameState:
        raise TypeError("state must be GameState")
    stored = state.registry_digest
    if type(stored) is str and _DIGEST.fullmatch(stored) is not None:
        return stored
    digest = getattr(registry, "digest", None)
    if type(digest) is not str or not digest:
        raise TypeError("registry must expose a digest")
    return digest
