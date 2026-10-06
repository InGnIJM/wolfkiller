"""Mask: the wolf that flips one seat's camp *inside the dance pool*.

It runs after the Dancer (``order=30`` against the pool's 20), and that ordering
is the card: the Mask's own check reads whether its target is dancing tonight,
which is only knowable once the pool exists. The check travels through
``selected_target.status_labels`` — the projector's whitelist carries ``in_pool``
for exactly this role, and like every selected-target fact it never enters the
model's prompt, so the answer reaches the hook and then the Mask's own private
data.

The mask itself is a runtime status. It does not touch the seat's real camp, so
the win check, the Seer's reading and every other camp-based rule are untouched;
only ``settle_pool`` folds it in, for that one comparison.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import MASKED_STATUS, POISON_IMMUNE_RESOURCE
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.target_memory import is_consecutive_repeat, target_memory
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf

LAST_MASKED_KEY = "last_masked"
POOL_CHECKS_KEY = "pool_checks"
#: 第二夜起（官方口径：假面从第二夜开始单独行动）。
_FIRST_MASK_ROUND = 2


def mask_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.round_number >= _FIRST_MASK_ROUND


def validate_mask_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "mask" or command.target_seat is None:
        return ()
    if is_consecutive_repeat(context, LAST_MASKED_KEY, command.target_seat):
        return (RuleViolation(
            "consecutive_mask_forbidden",
            "the same seat cannot be masked on two consecutive nights",
        ),)
    return ()


def _checked_in_pool(context: ActionContext, target: int) -> bool:
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != target:
        raise ValueError("selected target fact is missing or mismatched")
    labels = selected.get("status_labels") or ()
    if not isinstance(labels, (list, tuple)):
        raise TypeError("selected target status labels must be a sequence")
    return "in_pool" in labels


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, *,
    in_pool: bool | None, **common: object,
) -> GameEffect:
    if command.action_type == "mask" and command.target_seat is not None:
        state = "，对方正在舞池中" if in_pool else "，对方不在舞池中"
        thought = (
            f"决定给 {command.target_seat} 号玩家戴上面具{state}："
            f"{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不发动面具：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "MASK_REASONING",
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seat": command.target_seat,
                "reasoning": command.reasoning,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_mask_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "mask" or command.target_seat is None:
        return (_reasoning_effect(
            context, command, 1, in_pool=None, **common,
        ),)
    target = command.target_seat
    in_pool = _checked_in_pool(context, target)
    checks = context.facts.get(POOL_CHECKS_KEY) or ()
    if not isinstance(checks, (list, tuple)):
        raise TypeError("pool_checks must be a sequence")
    actor = context.actor_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload=target_memory(actor, LAST_MASKED_KEY, target, context.round_number),
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "key": POOL_CHECKS_KEY,
                "value": [*checks, {"target": target, "in_pool": in_pool}],
            },
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.ADD_STATUS,
            context.action_key, target_seat=target,
            payload={"target": target, "status": MASKED_STATUS},
            preconditions={
                "status_present": {"status": MASKED_STATUS, "present": False},
            },
            sort_key=(3,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 4), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "MASK_GRANTED",
                "payload": {
                    "seat": actor, "target_seat": target, "in_pool": in_pool,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(4,), **common,
        ),
        _reasoning_effect(context, command, 5, in_pool=in_pool, **common),
    )


MASK_GRANT_CONTRACT = ActionContract(
    contract_id="mask_grant",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # After the Dancer (20): the pool has to exist before the mask can read it.
    order=30,
    action_types=("mask", "pass"),
    actions_requiring_target=frozenset({"mask"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.ADD_STATUS, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    selected_target_fact_namespaces=frozenset({"status_labels"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=mask_applicable,
    validate=validate_mask_action,
    resolve=resolve_mask_action,
)

MASK_SPEC = RoleSpec(
    tags=frozenset({"wolf"}),
    role_id="wolf-killer-mask",
    display_name="Mask",
    camp_id="werewolf",
    opening_narration=("假面请睁眼", "请选择今晚要戴面具的玩家。"),
    contracts=(WEREWOLF_KILL_CONTRACT, MASK_GRANT_CONTRACT),
    initial_resources={POISON_IMMUNE_RESOURCE: 1},
    initial_private_data={LAST_MASKED_KEY: None, POOL_CHECKS_KEY: ()},
    allowed_effects=frozenset({
        EffectKind.SUBMIT_DAMAGE, EffectKind.ADD_STATUS,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    instructions=(
        "你是假面（狼人阵营，「假面舞会」板）：你与狼队共用同一次狼刀，参与讨论与投票，"
        "并且**免疫女巫的毒药**。"
        "从第二夜起，你可以在舞者之后单独行动：给一名存活玩家戴上面具。"
        "面具只在一件事上生效——**当晚舞池里的阵营比较**：被戴面具的人会被算作对立方，"
        "所以给舞池里的狼队友戴面具可以让整个舞池变成单一阵营（无人出局），"
        "给舞池里的好人戴面具则会把他算成狼，从而把少数方出局名额转给另一个人。"
        "面具**不改变**任何人的真实阵营：不影响屠边判胜、不影响预言家查验、也不影响狼刀。"
        "行动时你会同时知道目标**是否正在舞池中**（这个答案只有你知道）。"
        "不能连续两晚给同一个人戴面具。"
        "战术提示：优先确认舞池名单再决定面具给谁；把票投给被面具保护/被牺牲的那一边，"
        "并注意别让好人从「面具改变了阵营比较」推出你是假面。"
    ),
)


class Mask(Werewolf):
    """Mask: a werewolf whose nightly mask is a pipeline contract."""

    def get_skills(self) -> list[str]:
        return ["kill", "mask"]
