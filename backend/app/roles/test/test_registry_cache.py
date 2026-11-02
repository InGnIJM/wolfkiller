"""``freeze()`` is cached: it runs on every catalog request and prompt render.

The registry is immutable after import, but the freeze step re-validates every
spec, serializes every contract and hashes the lot. Rebuilding that snapshot on
each request cost tens of milliseconds; the cache must still be dropped as soon
as a new role is registered (tests and plugins do that after construction).
"""

import pytest

from app.roles.guard import GUARD_SPEC
from app.roles.registry import RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC


def _registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register_pipeline(VILLAGER_SPEC)
    return registry


def test_freeze_returns_the_same_snapshot_until_a_role_is_registered() -> None:
    registry = _registry()
    first = registry.freeze()

    assert registry.freeze() is first

    registry.register_pipeline(GUARD_SPEC)
    second = registry.freeze()

    assert second is not first
    assert second.digest != first.digest
    assert set(second.specs) == {VILLAGER_SPEC.role_id, GUARD_SPEC.role_id}


def test_a_rejected_duplicate_registration_keeps_the_cached_snapshot() -> None:
    registry = _registry()
    frozen = registry.freeze()

    with pytest.raises(ValueError, match="already registered"):
        registry.register_pipeline(VILLAGER_SPEC)

    assert registry.freeze() is frozen


def test_the_builtin_snapshot_is_stable_across_freeze_calls() -> None:
    assert builtin_registry.freeze() is builtin_registry.freeze()
