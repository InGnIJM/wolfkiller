"""Awakened Hidden Wolf: a wolf outside the pack that borrows one player's identity.

Official card: 「不能自曝身份，夜间无法参与狼队讨论，无法参与行动。不知道其他狼队友的身份，
其他狼队友也不知道觉醒隐狼的身份。其余狼人出局后，可以在夜间发动选中出局的技能；觉醒隐狼
可以选择一名玩家进行模仿，立刻获得目标的身份并在当夜结束后获得目标技能。」

The hidden-wolf half (no pack, no explode, lone kill once the pack is gone) is the
shared ``hidden_wolf_role_spec``. The imitation is two separate things, on two
separate clocks — conflating them is how this card goes wrong:

* **The identity is immediate.** The imitation writes a ``disguise`` private-data
  record; the projector's selected-target row (``core/disguise.py``) then shows the
  imitated role and camp to every inspection from the moment the night resolves.
  Nothing else changes — the real camp still wins, votes and dies.
* **The skill arrives when the night is over.** Every skill she could copy is
  *pre-declared* on her own spec (the card's list is exhaustive — check, poison,
  protect, shoot, inspect — so no contract has to be created at runtime, which would
  collide with the frozen registry identity). Each is a clone of the original contract
  under a new id, gated on a holder marker *and* on ``round_number`` being later than
  the night of the imitation.

How many times she may imitate is not stated by any source we could find; the safest
reading is once a game (``imitate`` resource, spent on use), recorded in
``docs/plans``. The imitation grants fresh resources, not a copy of the target's
current ones.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from app.core.disguise import DISGUISE_KEY
from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.guard import GUARD_SPEC, LAST_GUARDED_KEY
from app.roles.hidden_wolf import hidden_wolf_role_spec
from app.roles.hunter import HUNTER_SPEC
from app.roles.mirror_girl import MIRROR_INSPECT_CONTRACT
from app.roles.seer import SEER_SPEC
from app.roles.witch import WITCH_SPEC

IMITATE_RESOURCE = "imitate"
_EVENT = "AWAKENED_HIDDEN_WOLF_REASONING"

#: Imitated role -> the fresh resources that role's skill is gated on. A role that
#: is not listed has no copyable skill: the imitation still changes how she is read.
GRANTS: Mapping[str, Mapping[str, int]] = {
    "wolf-killer-seer": {"mimic_check": 1},
    "wolf-killer-witch": {"antidote": 1, "poison": 1},
    "wolf-killer-guard": {"mimic_guard": 1},
    "wolf-killer-hunter": {"gun": 1},
    "wolf-killer-mirror-girl": {"mimic_inspect": 1},
}


# ── the imitation itself ───────────────────────────────────────────────────


def imitate_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.resources.get(IMITATE_RESOURCE, 0) > 0


def validate_imitate_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "imitate" and command.target_seat == context.actor_seat:
        return (RuleViolation("self_imitate_forbidden", "a player cannot imitate itself"),)
    return ()


def _awakened_hidden_wolf_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int,
    role_label: str | None, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "imitate" and command.target_seat is not None:
        shown = f"，获得其身份「{role_label}」" if role_label else ""
        thought = f"决定模仿 {command.target_seat} 号玩家{shown}：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不模仿：{command.reasoning or '无理由'}"
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


def resolve_imitate_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "imitate" or command.target_seat is None:
        return (_awakened_hidden_wolf_reasoning_effect(context, command, 1, None, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    role, camp = selected.get("role_label"), selected.get("camp_label")
    if type(role) is not str or type(camp) is not str:
        raise TypeError("selected target role and camp labels must be strings")
    actor = context.actor_seat
    effects = [
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": IMITATE_RESOURCE, "amount": 1},
            preconditions={"resource_equals": {"resource": IMITATE_RESOURCE, "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "key": DISGUISE_KEY,
                "value": {
                    "role": role, "camp": camp,
                    "seat": command.target_seat, "round": context.round_number,
                },
            },
            sort_key=(2,), **common,
        ),
    ]
    for name, value in GRANTS.get(role, {}).items():
        effects.append(GameEffect(
            derive_effect_id(context.action_key, len(effects) + 1), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": name, "value": value},
            sort_key=(len(effects) + 1,), **common,
        ))
    effects.append(_awakened_hidden_wolf_reasoning_effect(
        context, command, len(effects) + 1, role, **common,
    ))
    return tuple(effects)


IMITATE_CONTRACT = ActionContract(
    contract_id="hidden_wolf_imitate",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # After every other NIGHT_ACTION skill (guard is 50): requests are issued
    # together, so ordering here is only about a readable journal — the same-night
    # gate on the copied skills is the round comparison below, not this number.
    order=60,
    action_types=("imitate", "pass"), actions_requiring_target=frozenset({"imitate"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_PRIVATE_DATA,
        EffectKind.SET_RESOURCE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"role_label", "camp_label"}),
    per_window_limit=1, per_round_limit=1,
    is_applicable=imitate_applicable, validate=validate_imitate_action,
    resolve=resolve_imitate_action,
)


# ── the copied skills: gated clones of the originals ───────────────────────


def _skill_ready(context: ActionContext) -> bool:
    """A copied skill opens the night *after* the imitation, never the same one."""
    record = context.facts.get(DISGUISE_KEY)
    if not hasattr(record, "get"):
        return False
    round_taken = record.get("round")
    return (
        context.actor_alive
        and type(round_taken) is int
        and context.round_number > round_taken
    )


def mimic_check_applicable(context: ActionContext) -> bool:
    return _skill_ready(context) and context.resources.get("mimic_check", 0) > 0


def mimic_witch_applicable(context: ActionContext) -> bool:
    return _skill_ready(context) and (
        context.resources.get("antidote", 0) > 0 or context.resources.get("poison", 0) > 0
    )


def mimic_guard_applicable(context: ActionContext) -> bool:
    return _skill_ready(context) and context.resources.get("mimic_guard", 0) > 0


def mimic_inspect_applicable(context: ActionContext) -> bool:
    return _skill_ready(context) and context.resources.get("mimic_inspect", 0) > 0


MIMIC_CHECK_CONTRACT = replace(
    SEER_SPEC.contracts[0], contract_id="mimic_seer_check", order=36,
    is_applicable=mimic_check_applicable,
)
MIMIC_WITCH_CONTRACT = replace(
    WITCH_SPEC.contracts[0], contract_id="mimic_witch_action", order=26,
    is_applicable=mimic_witch_applicable,
)
MIMIC_GUARD_CONTRACT = replace(
    GUARD_SPEC.contracts[0], contract_id="mimic_guard_action", order=55,
    is_applicable=mimic_guard_applicable,
)
# The shot keeps the hunter's own gate (a gun in hand plus a qualifying death), so
# it needs no wrapper: the gun only appears once a hunter has been imitated.
MIMIC_HUNTER_CONTRACT = replace(
    HUNTER_SPEC.contracts[0], contract_id="mimic_hunter_shoot", order=41,
)
MIMIC_INSPECT_CONTRACT = replace(
    MIRROR_INSPECT_CONTRACT, contract_id="mimic_mirror_inspect", order=38,
    is_applicable=mimic_inspect_applicable,
)

_POOL = (
    MIMIC_CHECK_CONTRACT, MIMIC_WITCH_CONTRACT, MIMIC_GUARD_CONTRACT,
    MIMIC_HUNTER_CONTRACT, MIMIC_INSPECT_CONTRACT,
)
_POOL_EFFECTS = frozenset().union(*(c.allowed_effects for c in _POOL))

AWAKENED_HIDDEN_WOLF_SPEC = hidden_wolf_role_spec(
    "wolf-killer-awakened-hidden-wolf", "Awakened Hidden Wolf",
    extra_contracts=(IMITATE_CONTRACT, *_POOL),
    extra_effects=IMITATE_CONTRACT.allowed_effects | _POOL_EFFECTS,
    extra_resources={
        IMITATE_RESOURCE: 1,
        # Copyable-skill holder markers: all closed until an imitation opens one.
        "mimic_check": 0, "mimic_guard": 0, "mimic_inspect": 0,
        "gun": 0, "antidote": 0, "poison": 0,
    },
    initial_private_data={
        DISGUISE_KEY: None, "private_checks": (), "role_checks": (),
        LAST_GUARDED_KEY: None, "wolf_kill_target": None, "saved_seat": None,
    },
    # The line sits on a spec shared across several points (imitation at the
    # night's start, the copied seer / witch / mirror checks later); it is generic
    # so it reads correctly at each of them.
    opening_narration=("觉醒隐狼请睁眼", "请选择一名玩家进行模仿，或使用已获得的技能。"),
    opening_points=frozenset({SchedulePoint.NIGHT_ACTION.value}),
    instructions=(
        "你是觉醒隐狼（狼人阵营，但**不属于狼窝**）：你不参与狼队的夜间讨论与狼刀投票，"
        "其他狼也不知道你是谁，你同样看不到他们是谁——你只知道场上还剩几只狼。"
        "你不能自爆。其他狼全部出局后，你获得带刀权，每晚可以独自刀人。"
        "**整局一次**，你可以夜间模仿一名其他存活玩家（imitate）："
        "模仿成功后，**所有查验都会把你显示成那名玩家的身份与阵营**（立刻生效，"
        "但你自己的阵营和胜负不变）；并且**从下一夜起**你获得对方的技能——"
        "预言家的查验、女巫的解药与毒药、守卫的守护、猎人的枪、魔镜少女的具体身份查验。"
        "模仿一个没有夜间技能的角色（如平民）只改变你被查验时的显示，不给技能。"
        "战术提示：模仿查验过的金水可以让你在预言家面前洗白；模仿神职能让你拿到真正的技能，"
        "但被模仿的玩家仍在场，可能暴露你的身份。"
    ),
)


class AwakenedHiddenWolf(BaseRole):
    """Daytime instance: the imitation and every copied skill are pipeline contracts."""

    pass
