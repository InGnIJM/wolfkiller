"""The Time Wolf's lock: a skill cast at the locked seat lands on the caster instead.

Official card: 「选择场上任意一位玩家封锁到时间的夹缝中，当晚场上好人阵营玩家对被封锁玩家
释放的验人、毒药和守护技能，均视为对自身释放。蚀时狼妃在技能生效的下一夜永久失去技能。」

The rule is a **redirection**, not a bounce: the seer checks herself (and learns she is
good), the witch poisons herself, the guard guards himself. So the producers — seer,
witch, guard, the only roles that cast those three skills — rewrite their own target when
the seat they picked carries the one-night ``time_locked`` status, which they can see
through ``selected_target.status_labels`` (hook-only; it never reaches a prompt).

Two details the card forces:

* Every cast is redirected on its own (「均」): there is no per-night "first cast only"
  gate. ``time_lock_hit`` is *not* such a gate — it only records that the lock took
  effect at least once, which is what costs the Time Wolf her skill from the next night.
* The antidote is not in the card's list, so a save is never redirected.

Known simplification: the card says the *good camp's* casters are redirected; the
producers do not see their own camp, so a wolf-camp holder of a borrowed seer / witch /
guard skill (an Awakened Hidden Wolf or Eclipse Maid copy) is redirected too. Those roles
are not on the Time Wolf's board.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import TIME_LOCKED_STATUS
from app.models.pipeline import ActionContext, EffectKind, GameEffect

#: Set on the *locked* seat when a cast was redirected; read back (through the
#: ``time_lock_spent`` derived fact) to take the skill away from the Time Wolf.
TIME_LOCK_HIT_RESOURCE = "time_lock_hit"
#: What a redirected seer check reports: the seer is looking at herself.
SELF_CHECK_CAMP = "good"


def is_locked(selected: object) -> bool:
    """True when the seat the caster picked carries tonight's lock."""
    if not isinstance(selected, Mapping):
        return False
    return TIME_LOCKED_STATUS in tuple(selected.get("status_labels") or ())


def lock_hit_effect(
    context: ActionContext, selected: Mapping[str, object], ordinal: int,
) -> GameEffect:
    """Record on the locked seat that the lock took effect.

    A plain ``SET_RESOURCE`` to 1 with no precondition: several casters can hit the
    same lock in one night and each must succeed.
    """
    target = selected.get("seat")
    if type(target) is not int or target <= 0:
        raise TypeError("a locked selected target needs a positive seat")
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.SET_RESOURCE,
        context.action_key, target_seat=target,
        payload={"target": target, "resource": TIME_LOCK_HIT_RESOURCE, "value": 1},
        expected_revision=context.revision, source_event_id=context.source_event_id,
        sort_key=(ordinal,),
    )
