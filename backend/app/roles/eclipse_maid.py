"""Eclipse Maid: a wolf that devours a good player's skill for one night.

Official card: 「从第二晚开始，每晚可以选择一名好人阵营的玩家的技能进行吞噬，获得该玩家的技能，
并可在当晚使用。被吞噬的玩家当晚失去技能，直到天亮。同时不能连续两晚对同一个人使用技能。」
The card says nothing about the pack, so the Maid is modelled on the Gargoyle with the one-way
variant the plan reserved for her: she **knows** the other wolves (``wolf_knows_pack``) while
they do not know her, takes no part in the night discussion, and only attacks once the pack is
gone (``hidden_wolf_role_spec``).

Mechanics, in the order they matter:

* **Devour (order 3, ``NIGHT_ACTION``).** A good target loses its skill for the night — the
  one-night ``silenced`` status, which the scheduler's execution-time gate already honours —
  and the Maid records ``devoured = {role, seat, round}``. The role is read the way every
  inspection reads it, so a disguised seat is devoured as the role it shows.
* **The skill is usable the same night, and only that night.** Every skill she can borrow is a
  clone of the original contract on her own spec (the same predeclared-pool idea the Awakened
  Hidden Wolf uses; no contract is created at run time), gated on ``devoured.role`` matching
  *and* ``devoured.round`` being tonight. Requests for a point are issued before that point's
  members act, so a skill that sits at ``NIGHT_ACTION`` itself (guard, dream, shelter) would
  never see tonight's devour; those clones are moved to ``NIGHT_WITCH_ACTION``, the last
  point before the commit. Potions and the gun are inherited **as the target currently holds
  them**: a witch who already spent her antidote yields only the poison.
* **Backlash.** Devouring the Radiance Earl, or a seat the Earl sheltered tonight, costs the
  Maid her life after the next day's speeches (``radiance_backlash``, delayed like a hunt).
  She still gets the skill — the card does not take it away.

Known simplification: the Earl acts before the Maid (so the shelter mark exists when she looks),
which means devouring the Earl cannot undo a shelter already cast this night.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import RADIANCE_BACKLASH_CAUSE, RADIANCE_SHIELDED_STATUS, SILENCED_STATUS
from app.core.wolf_pack import WOLF_KNOWS_PACK_RESOURCE
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.dream_weaver import DREAM_WEAVER_SPEC, LAST_DREAMED_KEY
from app.roles.guard import GUARD_SPEC, LAST_GUARDED_KEY
from app.roles.hidden_wolf import hidden_wolf_role_spec
from app.roles.hunter import HUNTER_SPEC, hunter_applicable
from app.roles.mirror_girl import MIRROR_GIRL_SPEC, MIRROR_INSPECT_CONTRACT
from app.roles.radiance_earl import (
    LAST_SHIELDED_KEY, RADIANCE_EARL_SPEC, RADIANCE_SHIELD_CONTRACT, radiance_applicable,
)
from app.roles.seer import SEER_SPEC
from app.roles.target_memory import is_consecutive_repeat, target_memory
from app.roles.witch import WITCH_SPEC

#: Private slots: who she devoured last (for the no-repeat rule) and what she holds tonight.
LAST_DEVOURED_KEY = "last_devoured"
DEVOURED_KEY = "devoured"
_EVENT = "ECLIPSE_MAID_REASONING"
_GOOD_CAMP = "good"

_SEER = SEER_SPEC.role_id
_WITCH = WITCH_SPEC.role_id
_GUARD = GUARD_SPEC.role_id
_HUNTER = HUNTER_SPEC.role_id
_MIRROR = MIRROR_GIRL_SPEC.role_id
_DREAM = DREAM_WEAVER_SPEC.role_id
_EARL = RADIANCE_EARL_SPEC.role_id

#: Devoured role -> the resource markers it carries, read off the target's own state.
#: Everything not listed here has either no copyable skill or a standing one that needs no marker.
INHERITED_MARKERS: Mapping[str, tuple[str, ...]] = {
    _WITCH: ("antidote", "poison"),
    _HUNTER: ("gun",),
}


# ── the devour itself ──────────────────────────────────────────────────────


def eclipse_applicable(context: ActionContext) -> bool:
    """From the second night on, while alive."""
    return context.actor_alive and context.round_number >= 2


def validate_eclipse_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "devour" or command.target_seat is None:
        return ()
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_devour_forbidden", "the Maid cannot devour herself"),)
    if is_consecutive_repeat(context, LAST_DEVOURED_KEY, command.target_seat):
        return (RuleViolation(
            "consecutive_devour_forbidden",
            "the same seat cannot be devoured on two consecutive nights",
        ),)
    return ()


def _eclipse_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int,
    *, role_label: str | None = None, backlash: bool = False, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "devour" and command.target_seat is not None:
        if role_label is None:
            thought = (
                f"决定吞噬 {command.target_seat} 号玩家，但对方不是好人阵营，吞噬落空："
                f"{command.reasoning or '无理由'}"
            )
        else:
            cost = "，但流光反噬会让自己次日出局" if backlash else ""
            thought = (
                f"决定吞噬 {command.target_seat} 号玩家的技能（身份「{role_label}」）{cost}："
                f"{command.reasoning or '无理由'}"
            )
    else:
        thought = f"决定今晚不吞噬任何人：{command.reasoning or '无理由'}"
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


def resolve_eclipse_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "devour" or command.target_seat is None:
        return (_eclipse_reasoning_effect(context, command, 1, **common),)
    target, actor = command.target_seat, context.actor_seat
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != target:
        raise ValueError("selected target fact is missing or mismatched")
    camp, role = selected.get("camp_label"), selected.get("role_label")
    if type(camp) is not str or type(role) is not str:
        raise TypeError("selected target camp and role labels must be strings")
    effects: list[GameEffect] = []

    def add(kind: EffectKind, *, target_seat: int | None = None, **fields: object) -> None:
        ordinal = len(effects) + 1
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), kind, context.action_key,
            target_seat=target_seat, sort_key=(ordinal,), **fields, **common,
        ))

    # The use is recorded either way: a wasted night still counts as "last night".
    add(EffectKind.SET_PRIVATE_DATA, target_seat=actor, payload=target_memory(
        actor, LAST_DEVOURED_KEY, target, context.round_number,
    ))
    if camp != _GOOD_CAMP:
        effects.append(_eclipse_reasoning_effect(
            context, command, len(effects) + 1, role_label=None, **common,
        ))
        return tuple(effects)
    add(EffectKind.SET_PRIVATE_DATA, target_seat=actor, payload={
        "target": actor, "key": DEVOURED_KEY,
        "value": {"role": role, "seat": target, "round": context.round_number},
    })
    add(EffectKind.ADD_STATUS, target_seat=target,
        payload={"target": target, "status": SILENCED_STATUS})
    add(EffectKind.EMIT_EVENT, visibility=("PUBLIC",), payload={
        "event_type": "STATUS_ADDED",
        "payload": {
            "seat": target, "status": SILENCED_STATUS, "round_number": context.round_number,
        },
    })
    held = frozenset(selected.get("resource_labels") or ())
    for marker in INHERITED_MARKERS.get(role, ()):
        add(EffectKind.SET_RESOURCE, target_seat=actor, payload={
            "target": actor, "resource": marker, "value": 1 if marker in held else 0,
        })
    statuses = frozenset(selected.get("status_labels") or ())
    backlash = role == _EARL or RADIANCE_SHIELDED_STATUS in statuses
    if backlash:
        add(EffectKind.SUBMIT_DAMAGE, target_seat=actor, payload={
            "target": actor, "amount": 1, "cause": RADIANCE_BACKLASH_CAUSE,
        })
    effects.append(_eclipse_reasoning_effect(
        context, command, len(effects) + 1, role_label=role, backlash=backlash, **common,
    ))
    return tuple(effects)


DEVOUR_CONTRACT = ActionContract(
    contract_id="eclipse_devour",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # After the Earl (2) so its shelter mark is visible; before every skill she can
    # take (the Dream Weaver is 45, the Seer / Witch sit at later points).
    order=3,
    action_types=("devour", "pass"), actions_requiring_target=frozenset({"devour"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_STATUS, EffectKind.SET_RESOURCE,
        EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({
        "camp_label", "role_label", "status_labels", "resource_labels",
    }),
    per_window_limit=1, per_round_limit=1,
    is_applicable=eclipse_applicable, validate=validate_eclipse_action,
    resolve=resolve_eclipse_action,
)


# ── the borrowed skills: gated clones of the originals ─────────────────────


def _devoured_this_round(context: ActionContext, role_id: str) -> bool:
    """The devour record names ``role_id`` on this round, whether or not she lives.

    The hunter shot is a death reaction: she is already marked dead when that
    window runs, so the alive bit would close a gun she still holds tonight.
    """
    record = context.facts.get(DEVOURED_KEY)
    if not hasattr(record, "get"):
        return False
    return record.get("role") == role_id and record.get("round") == context.round_number


def _devoured_now(context: ActionContext, role_id: str) -> bool:
    """She holds ``role_id``'s skill only on the night she devoured it."""
    return context.actor_alive and _devoured_this_round(context, role_id)


def devour_check_applicable(context: ActionContext) -> bool:
    return _devoured_now(context, _SEER)


def devour_witch_applicable(context: ActionContext) -> bool:
    return _devoured_now(context, _WITCH) and (
        context.resources.get("antidote", 0) > 0 or context.resources.get("poison", 0) > 0
    )


def devour_guard_applicable(context: ActionContext) -> bool:
    return _devoured_now(context, _GUARD)


def devour_hunter_applicable(context: ActionContext) -> bool:
    # Not ``_devoured_now``: that requires her to still be alive, and the shot
    # only opens after she has been marked dead.
    return _devoured_this_round(context, _HUNTER) and hunter_applicable(context)


def devour_mirror_applicable(context: ActionContext) -> bool:
    return _devoured_now(context, _MIRROR)


def devour_dream_applicable(context: ActionContext) -> bool:
    return _devoured_now(context, _DREAM)


def devour_shield_applicable(context: ActionContext) -> bool:
    return _devoured_now(context, _EARL) and radiance_applicable(context)


def validate_devour_dream_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    """The borrowed weave may be declined: only the Weaver herself is bound to dream."""
    if command.action_type == "dream" and command.target_seat == context.actor_seat:
        return (RuleViolation("dream_self_forbidden", "the Maid cannot dream herself"),)
    return ()


_WITCH_POINT = SchedulePoint.NIGHT_WITCH_ACTION

DEVOUR_CHECK_CONTRACT = replace(
    SEER_SPEC.contracts[0], contract_id="devour_seer_check", order=32,
    is_applicable=devour_check_applicable,
)
DEVOUR_WITCH_CONTRACT = replace(
    WITCH_SPEC.contracts[0], contract_id="devour_witch_action", order=24,
    is_applicable=devour_witch_applicable,
)
DEVOUR_MIRROR_CONTRACT = replace(
    MIRROR_INSPECT_CONTRACT, contract_id="devour_mirror_inspect", order=39,
    is_applicable=devour_mirror_applicable,
)
DEVOUR_HUNTER_CONTRACT = replace(
    HUNTER_SPEC.contracts[0], contract_id="devour_hunter_shoot", order=42,
    is_applicable=devour_hunter_applicable,
)
# These three sit at NIGHT_ACTION in the original, where this night's devour has not
# happened yet when their requests are issued — so the clones wait for the last point.
DEVOUR_GUARD_CONTRACT = replace(
    GUARD_SPEC.contracts[0], contract_id="devour_guard_action",
    schedule_point=_WITCH_POINT, order=60, is_applicable=devour_guard_applicable,
)
DEVOUR_DREAM_CONTRACT = replace(
    DREAM_WEAVER_SPEC.contracts[0], contract_id="devour_dream_weave",
    schedule_point=_WITCH_POINT, order=61, is_applicable=devour_dream_applicable,
    validate=validate_devour_dream_action,
)
DEVOUR_SHIELD_CONTRACT = replace(
    RADIANCE_SHIELD_CONTRACT, contract_id="devour_radiance_shield",
    schedule_point=_WITCH_POINT, order=62, is_applicable=devour_shield_applicable,
)

POOL = (
    DEVOUR_CHECK_CONTRACT, DEVOUR_WITCH_CONTRACT, DEVOUR_MIRROR_CONTRACT,
    DEVOUR_HUNTER_CONTRACT, DEVOUR_GUARD_CONTRACT, DEVOUR_DREAM_CONTRACT,
    DEVOUR_SHIELD_CONTRACT,
)
_POOL_EFFECTS = frozenset().union(*(c.allowed_effects for c in POOL))

ECLIPSE_MAID_SPEC = hidden_wolf_role_spec(
    "wolf-killer-eclipse-maid", "Eclipse Maid",
    extra_contracts=(DEVOUR_CONTRACT, *POOL),
    extra_effects=DEVOUR_CONTRACT.allowed_effects | _POOL_EFFECTS,
    extra_resources={
        # The one-way variant: she reads the pack, the pack does not read her.
        WOLF_KNOWS_PACK_RESOURCE: 1,
        # Inherited markers: closed until a devour of the matching role opens them.
        "antidote": 0, "poison": 0, "gun": 0,
    },
    initial_private_data={
        DEVOURED_KEY: None, LAST_DEVOURED_KEY: None, "private_checks": (),
        "role_checks": (), LAST_GUARDED_KEY: None, LAST_DREAMED_KEY: None,
        LAST_SHIELDED_KEY: None, "wolf_kill_target": None, "saved_seat": None,
    },
    opening_narration=("蚀日侍女请睁眼", "请选择一名好人玩家，吞噬其技能。"),
    # Only the devour is always true tonight; her borrowed skills sit at the shared
    # seer / witch points and must not wake her there.
    opening_points=frozenset({SchedulePoint.NIGHT_ACTION.value}),
    instructions=(
        "你是蚀日侍女（狼人阵营，但**不属于狼窝**）：你**知道**其他狼人是谁，其他狼人却不知道你；"
        "你不参与狼队的夜间讨论与狼刀投票，不能自爆，只有在其他狼全部出局后才获得带刀权。"
        "从第二晚开始，每晚可以**吞噬一名好人阵营玩家的技能**（devour）："
        "你当晚获得该玩家的技能并可立刻使用，被吞噬的玩家当晚失去技能直到天亮。"
        "你只在吞噬的那一夜拥有该技能，第二天晚上要重新吞噬；不能连续两晚吞噬同一个人。"
        "可吞噬的技能：预言家的查验、女巫的解药与毒药（按对方当前剩余的药来继承）、猎人的枪（"
        "只在你被狼刀或被放逐时可开）、守卫的守护、摄梦人的梦游、魔镜少女的具体身份查验、"
        "流光伯爵的庇护。吞噬没有夜间技能的角色（如村民）只会让对方当晚什么也没失去。"
        "**流光反噬**：如果你吞噬了流光伯爵，或吞噬了当晚被流光伯爵庇护的玩家，"
        "你会在次日发言结束后出局（技能仍然照常获得）。"
        "战术提示：吞噬预言家既能当晚验人又能让真预言家当晚失明；吞噬女巫能拿到毒药；"
        "吞噬时尽量避开你怀疑是流光伯爵的玩家。"
    ),
)


class EclipseMaid(BaseRole):
    """Daytime instance: the devour and every borrowed skill are pipeline contracts."""

    pass
