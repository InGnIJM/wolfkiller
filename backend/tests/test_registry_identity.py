"""The registry identity a game was frozen under must not follow live code.

Persisted identities — point-journal keys, issued-request tokens, and the role
resource setup marker — are all derived from a registry digest. Using the *live*
digest means every role-declaration change re-keys work the game already
committed: the journal misses, the point re-runs, and idempotency tokens move.
These tests pin the frozen-digest rule and its fallback.
"""

from __future__ import annotations

import pytest

from app.core.registry_identity import frozen_registry_digest
from app.models.game import GamePhase, GameState, PlayerState
from app.roles.registry import RegistrySnapshot
from app.models.pipeline import EffectKind, RoleSpec

DIGEST = "a" * 64
OTHER = "b" * 64


def snapshot(digest: str = OTHER) -> RegistrySnapshot:
    spec = RoleSpec(
        role_id="r", camp_id="good",
        allowed_effects=frozenset({EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC"}),
    )
    return RegistrySnapshot({"r": spec}, digest)


def state(*, digest: str = "") -> GameState:
    game = GameState(
        "g", phase=GamePhase.NIGHT, round_number=1,
        players={1: PlayerState(1, "r", "good")},
    )
    game.registry_digest = digest
    return game


def test_stamped_digest_wins_over_the_live_registry() -> None:
    assert frozen_registry_digest(state(digest=DIGEST), snapshot()) == DIGEST


def test_missing_digest_falls_back_to_the_live_registry() -> None:
    assert frozen_registry_digest(state(), snapshot()) == OTHER


@pytest.mark.parametrize(
    "value",
    ["", "short", "A" * 64, "g" * 64, "a" * 63, "a" * 65, " " + "a" * 63],
)
def test_malformed_digest_falls_back_to_the_live_registry(value: str) -> None:
    assert frozen_registry_digest(state(digest=value), snapshot()) == OTHER


def test_non_string_digest_falls_back_to_the_live_registry() -> None:
    game = state()
    game.registry_digest = None  # type: ignore[assignment]
    assert frozen_registry_digest(game, snapshot()) == OTHER


def test_rejects_wrong_argument_types() -> None:
    with pytest.raises(TypeError, match="state must be GameState"):
        frozen_registry_digest(object(), snapshot())  # type: ignore[arg-type]
    # The live registry is only consulted on the fallback path, so a stamped
    # state never needs the caller to hand over a real snapshot.
    assert frozen_registry_digest(state(digest=DIGEST), object()) == DIGEST  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="registry must expose a digest"):
        frozen_registry_digest(state(), object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="registry must expose a digest"):
        frozen_registry_digest(state(), snapshot(digest=""))  # type: ignore[arg-type]
