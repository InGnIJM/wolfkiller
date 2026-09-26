from app.roles.base import BaseRole
from app.models.pipeline import RoleSpec


VILLAGER_SPEC = RoleSpec(
    role_id="wolf-killer-villager",
    display_name="Villager",
    camp_id="good",
    contracts=(),
    allowed_effects=frozenset(),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions=(
        "Villagers have no private night action. Use only public discussion, public results, "
        "and voting to identify the opposing camp. "
        "Tactical advice: Villagers are vital voting power and can proactively challenge suspicious claims. "
        "When dangerous werewolves are active, villagers may take calculated rhetorical risks to attract attention "
        "and protect key gods, but should balance this against confusing the good camp."
    ),
)


class Villager(BaseRole):
    """Villager role: no special abilities. Relies on speech and voting."""
    pass
