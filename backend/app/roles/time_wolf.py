"""Time Wolf: the wolf that seals one seat in the seam of time.

Official card: 「每晚可以率先睁眼，选择场上任意一位玩家封锁到时间的夹缝中，当晚场上好人
阵营玩家对被封锁玩家释放的验人、毒药和守护技能，均视为对自身释放。蚀时狼妃在技能生效的
下一夜永久失去技能。」

The lock itself is only a one-night ``time_locked`` status on the chosen seat; the
*redirection* is the producers' job (seer / witch / guard read the status through
``selected_target.status_labels`` — see ``core/time_lock.py``), because only the caster
may write effects on its own behalf. That is why this contract sits at ``order=10`` of
``NIGHT_ACTION``: the status has to exist before the Guard (50) is asked, and the later
seer / witch points see it too.

The loss of the skill is the derived fact ``time_lock_spent``: once a cast was
redirected, the locked seat carries ``time_lock_hit`` and the Wolf's contract stops
being applicable from the next night, for good. A lock nobody cast a skill into does
not "take effect", so it costs her nothing — and there is no rule against locking the
same seat on consecutive nights, or against locking herself or a teammate.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import TIME_LOCKED_STATUS
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.target_memory import target_memory
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf
from app.roles.wolf_claw import (
    AWAKENED_CLAW_GIFT_CONTRACT, AWAKENED_CLAW_GIFT_KEY,
)

#: Private slot: whom the Wolf locked last, and on which night. The projector's
#: ``time_lock_spent`` fact reads it to find the seat that may carry the hit marker.
LOCKED_KEY = "time_lock"
_EVENT = "TIME_WOLF_REASONING"


def time_wolf_applicable(context: ActionContext) -> bool:
    """Alive, and the lock has not yet taken effect (it never returns afterwards)."""
    return context.actor_alive and context.facts.get("time_lock_spent") is not True


def _time_wolf_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "lock" and command.target_seat is not None:
        thought = (
            f"决定封锁 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不封锁任何人：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _EVENT,
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seat": command.target_seat,
                "reasoning": command.reasoning,
                "degraded": command.degraded,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_time_wolf_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "lock" or command.target_seat is None:
        return (_time_wolf_reasoning_effect(context, command, 1, **common),)
    target = command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload=target_memory(
                context.actor_seat, LOCKED_KEY, target, context.round_number,
            ),
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.ADD_STATUS,
            context.action_key, target_seat=target,
            payload={"target": target, "status": TIME_LOCKED_STATUS},
            preconditions={
                "status_present": {"status": TIME_LOCKED_STATUS, "present": False},
            },
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "STATUS_ADDED",
                "payload": {
                    "seat": target, "status": TIME_LOCKED_STATUS,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _time_wolf_reasoning_effect(context, command, 4, **common),
    )


TIME_LOCK_CONTRACT = ActionContract(
    contract_id="time_lock",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # Ahead of the Guard (50): the lock must already stand when the Guard is asked.
    order=10,
    action_types=("lock", "pass"),
    actions_requiring_target=frozenset({"lock"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=time_wolf_applicable,
    resolve=resolve_time_wolf_action,
)

TIME_WOLF_SPEC = RoleSpec(
    tags=frozenset({"wolf"}),
    role_id="wolf-killer-time-wolf",
    display_name="Time Wolf",
    camp_id="werewolf",
    # 它排在所有夜间角色之前，所以这一句也是当晚的第一句开眼旁白。
    opening_narration=("蚀时狼妃请睁眼", "请选择要封锁到时间夹缝中的玩家。"),
    contracts=(WEREWOLF_KILL_CONTRACT, TIME_LOCK_CONTRACT, AWAKENED_CLAW_GIFT_CONTRACT),
    initial_private_data={LOCKED_KEY: None, AWAKENED_CLAW_GIFT_KEY: None},
    projected_facts=frozenset({"time_lock_spent"}),
    allowed_effects=frozenset({
        EffectKind.SUBMIT_DAMAGE, EffectKind.SET_PRIVATE_DATA,
        EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    instructions=(
        "Time Wolf is a werewolf: at night it joins the wolf channel, the shared discussion "
        "and the shared kill vote like any other werewolf. It also acts FIRST each night and "
        "locks any one living player (yourself or a teammate included) in the seam of time. "
        "Tonight, every skill the good camp casts at the locked player — a Seer's check, a "
        "Witch's poison, a Guard's protection — is treated as cast at the caster instead: "
        "the Seer checks herself, the Witch poisons herself, the Guard guards himself. The "
        "antidote is not affected. You may lock the same seat on consecutive nights. The "
        "lock is your only skill and it is spent the moment it takes effect: from the night "
        "after a skill was redirected you lose it for good, so lock the seat a god is most "
        "likely to act on — and remember the good camp learns nothing about WHO was locked, "
        "only that their own skill misfired."
    ),
)


class TimeWolf(Werewolf):
    """Time Wolf: a werewolf whose nightly lock is a pipeline contract."""

    def get_skills(self) -> list[str]:
        return ["kill", "lock"]
