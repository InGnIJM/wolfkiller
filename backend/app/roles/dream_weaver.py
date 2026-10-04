"""Dream Weaver: shields one dreamer a night, and dies with them.

Three rules, none of which needs role-specific engine code:

* **The dreamer is shielded against every night damage**, the vial included.
  The shield travels as ``SUBMIT_PROTECTION(source="dream")``, which
  ``core/night_settlement.py`` reads through its any-damage tier — a plain guard
  shield would not stop the poison.
* **The dreamer never learns about it.** Nothing public is emitted for the
  weave; only the Dream Weaver records who it visited.
* **Two nights in a row on the same seat kills it.** The second visit submits
  damage instead of a shield, which is why the record is private data the
  ``resolve`` hook reads back rather than a validator rule.
* **If the Dream Weaver dies at night, so does that night's dreamer** — a
  ``react`` contract on the death response window, with the daytime ways out
  excluded.

Note on "may not decline": the registry forbids a fallback action that requires
a target (``registry.py:203``), so ``pass`` has to exist as the engine's safety
valve. The prompt states the choice is mandatory; ``pass`` only ever appears
when the model itself fails.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

#: Death cause shared by both ways the Dream Weaver kills: the second night on
#: the same seat, and dragging that night's dreamer along on its own death.
DREAM_DEATH_CAUSE = "dream_death"

#: Ways out that drag the dreamer along. The daytime ones are excluded: the
#: rules only bind the dreamer to a *night* death, so an exile or a duel leaves
#: the dreamer alive.
_DREAM_REVENGE_REASONS = frozenset({
    "wolf_kill", "poison", "hunter_shot", "charm", "wolf_king_shot",
    "reflect", DREAM_DEATH_CAUSE,
})


def dream_weaver_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_dream_weaver_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "dream" and command.target_seat == context.actor_seat:
        return (RuleViolation(
            "dream_self_forbidden", "the Dream Weaver cannot dream itself",
        ),)
    return ()


def _dream_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.action_type == "dream" and command.target_seat is not None:
        thought = f"决定让 {command.target_seat} 号玩家梦游：{command.reasoning or '无理由'}"
    else:
        thought = f"决定今晚不发动摄梦：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "DREAM_WEAVER_REASONING",
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


def resolve_dream_weaver_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "dream" or command.target_seat is None:
        return (_dream_reasoning_effect(context, command, 1, **common),)
    target = command.target_seat
    if context.facts.get("last_dreamed") == target:
        # Two nights running on the same seat: the weave turns lethal instead of
        # shielding. No protection is submitted, so nothing can save it.
        first = GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": DREAM_DEATH_CAUSE},
            sort_key=(1,), **common,
        )
    else:
        first = GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_PROTECTION,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "source": "dream"},
            sort_key=(1,), **common,
        )
    return (
        first,
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "key": "last_dreamed", "value": target},
            sort_key=(2,), **common,
        ),
        _dream_reasoning_effect(context, command, 3, **common),
    )


def dream_weaver_revenge_applicable(context: ActionContext) -> bool:
    target = context.facts.get("last_dreamed")
    return (
        context.source_event_id is not None
        and context.trigger_event is not None
        and context.trigger_event.get("target_seat") == context.actor_seat
        and context.trigger_reason in _DREAM_REVENGE_REASONS
        and type(target) is int
        and target > 0
        and target != context.actor_seat
    )


def react_dream_weaver_revenge(context: ActionContext) -> tuple[GameEffect, ...]:
    """Drag this night's dreamer along. Pure and deterministic — no model call.

    Response windows do not consult ``is_applicable`` before reacting, so the
    hook re-checks it and stays silent for every way out the rules exclude.
    """
    if not dream_weaver_revenge_applicable(context):
        return ()
    target = context.facts["last_dreamed"]
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_DAMAGE,
        context.action_key, target_seat=target,
        payload={"target": target, "amount": 1, "cause": DREAM_DEATH_CAUSE},
        expected_revision=context.revision,
        source_event_id=context.source_event_id,
        sort_key=(1,),
    ),)


DREAM_WEAVER_REVENGE_CONTRACT = ActionContract(
    contract_id="dream_weaver_revenge",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=45,
    action_types=("revenge",),
    actions_requiring_target=frozenset(),
    fallback_action_type="revenge",
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_DREAM_REVENGE_REASONS,
    per_window_limit=1,
    is_applicable=dream_weaver_revenge_applicable,
    react=react_dream_weaver_revenge,
)

DREAM_WEAVER_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-dream-weaver",
    display_name="Dream Weaver",
    camp_id="good",
    contracts=(ActionContract(
        contract_id="dream_weaver_dream",
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=55,
        action_types=("dream", "pass"),
        actions_requiring_target=frozenset({"dream"}),
        fallback_action_type="pass",
        allowed_effects=frozenset({
            EffectKind.SUBMIT_PROTECTION, EffectKind.SUBMIT_DAMAGE,
            EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
        }),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        is_applicable=dream_weaver_applicable,
        validate=validate_dream_weaver_action,
        resolve=resolve_dream_weaver_action,
    ), DREAM_WEAVER_REVENGE_CONTRACT),
    initial_private_data={"last_dreamed": None},
    allowed_effects=frozenset({
        EffectKind.SUBMIT_PROTECTION, EffectKind.SUBMIT_DAMAGE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "你是摄梦人（好人神职）：每晚**必须**指定一名自己以外的存活玩家为梦游者，不可放弃。"
        "梦游者本人不会知道自己被摄梦，也看不到任何提示。"
        "梦游者当夜免疫一切夜间伤害——狼刀和女巫的毒药都杀不死他。"
        "如果你连续两晚选择同一名玩家，该玩家会在第二夜出局（死因「摄梦」）。"
        "如果你在夜间出局，当夜的梦游者会与你一同出局；但你在白天被放逐或被骑士决斗出局时，"
        "梦游者不受影响。"
        "战术提示：摄梦是强力的保护技能，优先保护你判断的真预言家或其他关键神职；"
        "因为不能连续两晚摄同一人，要提前规划轮换顺序；"
        "如果怀疑某人是狼人，也可以故意连续两晚摄他，用第二夜的死亡带走他。"
    ),
)


class DreamWeaver(BaseRole):
    """Dream Weaver: the weave and its revenge are handled by the contracts."""

    pass
