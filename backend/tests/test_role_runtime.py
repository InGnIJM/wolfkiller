"""The role-resource setup marker must track *resources*, not declarations.

The marker used to fold every seated role's full ``stable_digest()``, so adding a
tag, rewording instructions, or bumping an unrelated contract rejected a resumed
game with ``role resource configuration changed`` — a permanent block, because
the game's resources had already been set up. It now covers the frozen registry
identity plus the declared resources themselves, and a game whose declaration
moved can adopt the new marker without replaying any resource effect.
"""

from __future__ import annotations

import pytest

from app.core.effect_applier import EffectApplier, EffectPermission, EffectRejected, derive_effect_id
from app.core.role_runtime import (
    adopt_resource_declaration,
    initialize_role_resources,
    resource_declaration_marker,
    role_resource_view,
)
from app.models.game import GameState, PlayerState
from app.models.pipeline import EffectKind, GameEffect, RoleSpec

DIGEST = "a" * 64


def state() -> GameState:
    return GameState(
        game_id="g",
        players={
            1: PlayerState(1, "witch", "good"),
            2: PlayerState(2, "wolf", "werewolf"),
            3: PlayerState(3, "villager", "good"),
        },
    )


def specs(**changes: object) -> dict[str, RoleSpec]:
    witch: dict[str, object] = {
        "camp_id": "good", "tags": frozenset({"god"}),
        "initial_resources": {"antidote": 1, "poison": 1},
    }
    witch.update(changes)
    return {
        "witch": RoleSpec("witch", **witch),  # type: ignore[arg-type]
        "wolf": RoleSpec("wolf", camp_id="werewolf"),
        "villager": RoleSpec("villager", camp_id="good"),
    }


def marker_of(game: GameState) -> str | None:
    return game._pipeline_runtime.resource_setup_digest


def test_marker_ignores_non_resource_declaration_changes() -> None:
    game = state()
    initialize_role_resources(game, specs(), DIGEST)
    original = marker_of(game)
    assert original is not None
    changed = specs(display_name="女巫", instructions="新的提示词", tags=frozenset({"villager"}))
    assert resource_declaration_marker(game, changed, DIGEST) == original
    # Re-running setup with the changed declarations is a no-op, not a rejection.
    assert initialize_role_resources(game, changed, DIGEST) is not None
    assert marker_of(game) == original


def test_marker_moves_only_when_declared_resources_change() -> None:
    game = state()
    initialize_role_resources(game, specs(), DIGEST)
    original = marker_of(game)
    for changed in (
        specs(initial_resources={"antidote": 1}),
        specs(initial_resources={"antidote": 1, "poison": 2}),
        specs(initial_resources={"antidote": 1, "poison": 1, "self_kill_forbidden": 1}),
    ):
        assert resource_declaration_marker(game, changed, DIGEST) != original
    with pytest.raises(EffectRejected, match="role resource configuration changed"):
        initialize_role_resources(game, specs(initial_resources={"antidote": 2, "poison": 1}), DIGEST)


def test_marker_is_none_without_any_declaration() -> None:
    empty = {role: RoleSpec(role) for role in ("witch", "wolf", "villager")}
    assert resource_declaration_marker(state(), empty, DIGEST) is None
    assert initialize_role_resources(state(), empty, DIGEST) is None


def test_marker_validates_inputs() -> None:
    with pytest.raises(TypeError, match="state must be GameState"):
        resource_declaration_marker(object(), specs(), DIGEST)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="specs must map role ids"):
        resource_declaration_marker(state(), {"witch": object()}, DIGEST)  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="spec key must match role id"):
        resource_declaration_marker(state(), {"wolf": RoleSpec("witch")}, DIGEST)
    with pytest.raises(ValueError, match="invalid config_version"):
        resource_declaration_marker(state(), specs(), "bad")
    with pytest.raises(ValueError, match="unknown role"):
        resource_declaration_marker(state(), {"witch": RoleSpec("witch")}, DIGEST)
    with pytest.raises(EffectRejected, match="invalid initial resource"):
        resource_declaration_marker(state(), specs(initial_resources={"antidote": -1}), DIGEST)


def test_adopt_moves_the_marker_without_touching_resources() -> None:
    game = state()
    initialize_role_resources(game, specs(), DIGEST)
    applier = EffectApplier()
    applier.apply(game, (
        GameEffect(derive_effect_id("consume", 0), EffectKind.ACCEPT_ACTION, "consume",
                   expected_revision=1, sort_key=(0,)),
        GameEffect(derive_effect_id("consume", 1), EffectKind.CONSUME_RESOURCE, "consume",
                   target_seat=1, payload={"target": 1, "resource": "antidote", "amount": 1},
                   expected_revision=1, sort_key=(1,)),
    ), EffectPermission(1, frozenset({EffectKind.CONSUME_RESOURCE}),
                        frozenset({EffectKind.CONSUME_RESOURCE}), frozenset({1}), frozenset()))
    assert role_resource_view(game, 1)["antidote"] == 0

    drifted = specs(initial_resources={"antidote": 1, "poison": 1, "self_kill_forbidden": 1})
    target = resource_declaration_marker(game, drifted, DIGEST)
    assert target is not None and target != marker_of(game)
    adopt_resource_declaration(game, target)
    assert marker_of(game) == target
    # Adoption is metadata only: nothing is granted, nothing is reset.
    assert role_resource_view(game, 1)["antidote"] == 0
    assert "self_kill_forbidden" not in role_resource_view(game, 1)
    assert initialize_role_resources(game, drifted, DIGEST) is not None


def test_adopt_validates_the_marker_and_the_runtime() -> None:
    game = state()
    with pytest.raises(TypeError, match="state must be GameState"):
        adopt_resource_declaration(object(), DIGEST)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid marker"):
        adopt_resource_declaration(game, "short")
    with pytest.raises(EffectRejected, match="pipeline runtime is missing"):
        adopt_resource_declaration(game, DIGEST)
    game._pipeline_runtime = object()
    with pytest.raises(EffectRejected, match="pipeline runtime is missing"):
        adopt_resource_declaration(game, DIGEST)
