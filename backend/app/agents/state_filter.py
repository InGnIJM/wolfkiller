from __future__ import annotations

from collections.abc import Mapping

from app.core.context_projector import ContextProjector
from app.models.game import GameState
from app.roles.registry import builtin_registry


def _plain(value: object, *, depth: int = 0, active: set[int] | None = None) -> object:
    """Convert a frozen projected value into a plain, serializable copy."""
    if depth > 64:
        raise ValueError("view exceeds maximum depth")
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, str):
        return value
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError("view contains a cycle")
    if isinstance(value, Mapping):
        active.add(identity)
        try:
            return {
                key: _plain(item, depth=depth + 1, active=active)
                for key, item in value.items()
            }
        finally:
            active.remove(identity)
    if isinstance(value, (tuple, list)):
        active.add(identity)
        try:
            return [_plain(item, depth=depth + 1, active=active) for item in value]
        finally:
            active.remove(identity)
    raise TypeError("unsupported view value")


class StateFilter:
    """Delegates role views to the pipeline's frozen context projection."""

    def __init__(self):
        self.projector = ContextProjector()

    def filter_for_role(self, state: GameState, player_id: int, role_name: str) -> dict:
        """Return a plain, safe copy of the projected observation context.

        The projection is produced by ContextProjector from the frozen role
        registry, so no role-specific branch exists here; private facts and
        resources are included only for the requesting seat.
        """
        player = state.players.get(player_id)
        if player is None:
            raise ValueError("player seat does not exist in state")
        if player.role != role_name:
            raise ValueError("player role does not match requested role")
        context = self.projector.project_view(state, builtin_registry.freeze(), player_id)
        view = {field: getattr(context, field) for field in context.__dataclass_fields__}
        return _plain(view)
