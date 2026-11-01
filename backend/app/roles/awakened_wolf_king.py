"""Awakened Wolf King: two claws, one of which can be handed to a teammate.

Rules and the compatibility argument live in ``docs/plans/2026-10-21-awakened-
wolf-king.md``. The implementation deliberately does not touch the ordinary
Wolf King's ``wolf_king_shot`` contract: the new claws are separate contracts,
so archived games that used the ordinary claw keep their exact digest and are
judged ``compatible`` rather than ``drift``.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf
from app.roles.wolf_claw import (
    AWAKENED_CLAW_CONTRACT, AWAKENED_CLAW_PASS_CONTRACT,
    AWAKENED_CLAW_RESOURCE, AWAKENED_CLAW_SWEEP_CONTRACT,
)

EXPLODE_RESOURCE = "explode"
EXPLODE_CAUSE = "self_explode"


def awakened_wolf_king_explode_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.resources.get(EXPLODE_RESOURCE, 0) > 0


def validate_awakened_wolf_king_explode(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "explode":
        return ()
    if context.resources.get(EXPLODE_RESOURCE, 0) <= 0:
        return (RuleViolation(
            "explode_unavailable", "the self-destruct is no longer available",
        ),)
    return ()


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "explode":
        thought = f"决定自爆，带走自己但不发动狼王爪：{command.reasoning or '无理由'}"
    else:
        thought = f"决定暂不自爆：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "WOLF_KING_REASONING",
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


def _identity(context: ActionContext) -> tuple[str, str]:
    identity = context.facts.get("actor_identity") or {}
    if isinstance(identity, Mapping):
        role = identity.get("role_id", context.actor_role_id)
        camp = identity.get("camp_id", "werewolf")
        if type(role) is str and type(camp) is str:
            return role, camp
    return context.actor_role_id, "werewolf"


def resolve_awakened_wolf_king_explode(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_reasoning_effect(context, command, 1, **common),)
    actor = context.actor_seat
    role, camp = _identity(context)
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "resource": EXPLODE_RESOURCE, "amount": 1,
            },
            preconditions={
                "resource_equals": {"resource": EXPLODE_RESOURCE, "value": 1},
            },
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "amount": 1, "cause": EXPLODE_CAUSE},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "PLAYER_REVEALED",
                "payload": {"seat_number": actor, "role": role, "camp": camp},
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 4), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "DAY_INTERRUPTED",
                "payload": {
                    "seat": actor, "cause": EXPLODE_CAUSE,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(4,), **common,
        ),
        _reasoning_effect(context, command, 5, **common),
    )


AWAKENED_WOLF_KING_EXPLODE_CONTRACT = ActionContract(
    contract_id="awakened_wolf_king_explode",
    schedule_point=SchedulePoint.DAY_ACTION,
    order=20,
    # No passenger: the self-destruct is the White Wolf King's shape minus the
    # target, and it never fires the claw.
    action_types=("explode", "pass"),
    actions_requiring_target=frozenset(),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    # No per-game limit: a ``pass`` is also an accepted action and the seat is
    # asked before every later speaker; the one-shot is the ``explode`` resource.
    per_window_limit=1,
    is_applicable=awakened_wolf_king_explode_applicable,
    validate=validate_awakened_wolf_king_explode,
    resolve=resolve_awakened_wolf_king_explode,
)

AWAKENED_WOLF_KING_SPEC = RoleSpec(
    tags=frozenset({"wolf"}),
    role_id="wolf-killer-awakened-wolf-king",
    display_name="Awakened Wolf King",
    camp_id="werewolf",
    contracts=(
        WEREWOLF_KILL_CONTRACT,
        AWAKENED_CLAW_PASS_CONTRACT,
        AWAKENED_WOLF_KING_EXPLODE_CONTRACT,
        AWAKENED_CLAW_SWEEP_CONTRACT,
        AWAKENED_CLAW_CONTRACT,
    ),
    initial_resources={AWAKENED_CLAW_RESOURCE: 2, EXPLODE_RESOURCE: 1},
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_RESOURCE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    opening_narration=("觉醒狼王请睁眼", "请选择是否将一次狼王爪亲传给存活狼队友。"),
    opening_points=frozenset({"night_action"}),
    instructions=(
        "你是觉醒狼王（狼人阵营），与狼队共用同一次狼刀，可以自爆身份，也可以自刀。"
        "你拥有两次狼王爪：每晚可以把其中一次亲传给一名存活、尚未持爪的狼窝队友"
        "（每名队友整局最多一次，不能传给自己，隐狼不在传递范围）。"
        "不亲传时，你出局后可以一次发动双爪带走两名存活玩家；已经传出一爪，"
        "则只剩一次单爪。可发动的出局方式：被狼刀、被放逐、被猎人带走；"
        "被毒、自爆、殉情、骑士决斗带走时不能发动；你成为最后一狼时也不能发动。"
        "被狼王爪带走的人死因是「狼王爪」，被带走的猎人不能开枪，被带走的狼美人不会殉情。"
        "白天发言前你可以自爆：公开身份、自己出局、当日结束、不带走任何人，且不触发狼王爪。"
        "亲传的爪会留在队友身上直到其出局；队友出局时的普通狼王爪窗口会自动出现。"
    ),
)


class AwakenedWolfKing(Werewolf):
    """Legacy role object: the pipeline contracts carry the actual rules."""

