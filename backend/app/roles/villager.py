from app.roles.base import BaseRole
from app.models.pipeline import RoleSpec


VILLAGER_SPEC = RoleSpec(
    role_id="wolf-killer-villager",
    display_name="Villager",
    camp_id="good",
    contracts=(),
    allowed_effects=frozenset(),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions="Use public discussion and voting; this role has no private action.",
)


class Villager(BaseRole):
    """Villager role: no special abilities. Relies on speech and voting."""
    pass
