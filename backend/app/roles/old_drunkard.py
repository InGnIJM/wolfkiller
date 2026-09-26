"""Old Drunkard: a plain good-camp villager that shrugs off a charm and dies late.

Two declarative marks, no contract of its own:

* ``charm_immune`` — the wolf beauty's charm rejects this seat, and the mark is
  public enough for the charmer to see it before wasting the charm.
* ``delayable`` — a lethal hit from the witch's vial or the hunter's gun does not
  kill on the spot. ``night_settlement.settle()`` spares the seat, the applier
  records the pending death, and the engine finalises it once the next day's
  speeches are over and before the exile vote starts.
"""

from __future__ import annotations

from app.models.pipeline import RoleSpec
from app.roles.villager import Villager

OLD_DRUNKARD_SPEC = RoleSpec(
    role_id="wolf-killer-old-drunkard", display_name="Old Drunkard", camp_id="good",
    contracts=(),
    initial_resources={"charm_immune": 1, "delayable": 1},
    allowed_effects=frozenset(),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    # A plain villager for every counting rule: it is not a god.
    tags=frozenset({"villager"}),
    max_count=1,
    instructions=(
        "Old Drunkard is a good-camp villager with no night action and no vote of special "
        "power. It cannot be charmed by Wolf Beauty: any charm aimed here simply fails, so "
        "Wolf Beauty that wastes a charm on this seat gains nothing. It is also slow to die "
        "from the witch's vial or the hunter's gun: such a hit does not kill it that day. It "
        "is instead marked as poisoned or wounded, keeps speaking normally, and dies right "
        "after the next day's speeches end and before that day's exile vote. A wolf kill, an "
        "exile or a self-destruct still removes it at once. Use the extra day: because a "
        "failed poison or gunshot is publicly visible, the good camp learns that the shot "
        "seat was the Old Drunkard, so speak up while still alive to stop the wolf team from "
        "claiming that identity, and help steer the day's exile onto a wolf. Judge each day "
        "by what the good camp gains from the information a surviving hit reveals."
    ),
)


class OldDrunkard(Villager):
    """Old Drunkard: both of its abilities are declarative marks read by the
    settlement and by Wolf Beauty's charm validation, so the role itself only
    speaks and votes like a villager."""

    def get_skills(self) -> list[str]:
        return []


__all__ = ["OLD_DRUNKARD_SPEC", "OldDrunkard"]
