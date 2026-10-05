"""Dancer: three seats into the pool, and the minority camp leaves.

Two things make this role unusual.

* The command carries a **list** of targets (``multi_target_actions``), so the
  model gateway asks for exactly three seats in one call.
* The verdict is **not** decided by the hook. The hook only marks the three
  seats and records them; ``core/night_settlement.settle_pool`` reads the real
  camps at settlement time, because a good-camp role must never be handed the
  camp of another seat — not even inside a pure hook. The Mask's flip therefore
  stays what the rules say it is: a mask worn inside the pool, not a changed
  camp.

The dance is mandatory from the second night on (the rules say 必须), but the
registry forbids a fallback action that requires a target, so ``pass`` exists as
the engine's safety valve and only ever fires when the model itself fails —
exactly like the Dream Weaver's.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import DANCE_POOL_STATUS, POISON_IMMUNE_RESOURCE
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

#: The pool shield: cancels the knife only, and never the "guard + antidote"
#: breach — the dance veil is not a guard.
DANCE_VEIL_SOURCE = "dance_veil"
POOLED_KEY = "pooled_seats"
#: 第二夜起（官方卡牌）。
_FIRST_DANCE_ROUND = 2
_POOL_SIZE = 3


def _pooled_seats(context: ActionContext) -> tuple[int, ...]:
    pooled = context.facts.get(POOLED_KEY) or ()
    if not isinstance(pooled, (list, tuple)):
        raise TypeError("pooled_seats must be a sequence")
    return tuple(seat for seat in pooled if type(seat) is int)


def _eligible_seats(context: ActionContext) -> tuple[int, ...]:
    """Living seats that have never been in a pool, in seat order."""
    alive = context.facts.get("alive_seats") or ()
    if not isinstance(alive, (list, tuple)):
        raise TypeError("alive_seats must be a sequence")
    pooled = set(_pooled_seats(context))
    return tuple(seat for seat in alive if type(seat) is int and seat not in pooled)


def dancer_applicable(context: ActionContext) -> bool:
    return (
        context.actor_alive
        and context.round_number >= _FIRST_DANCE_ROUND
        and len(_eligible_seats(context)) >= _POOL_SIZE
    )


def validate_dance_pool(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "pool" or command.target_seats is None:
        return ()
    pooled = set(_pooled_seats(context))
    repeated = sorted(seat for seat in command.target_seats if seat in pooled)
    if repeated:
        return (RuleViolation(
            "pool_repeat_forbidden",
            "a player can only join the dance once per game",
        ),)
    return ()


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.action_type == "pool" and command.target_seats:
        seats = "、".join(f"{seat}号" for seat in command.target_seats)
        veil = (
            "，舞者在池内因此三人免疫狼刀"
            if context.actor_seat in command.target_seats else ""
        )
        thought = (
            f"决定邀请 {seats}共舞{veil}：{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不发动起舞：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "DANCER_REASONING",
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seat": None,
                "reasoning": command.reasoning,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_dance_pool(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    targets = tuple(command.target_seats or ())
    if command.action_type != "pool" or not targets:
        return (_reasoning_effect(context, command, 1, **common),)
    actor = context.actor_seat
    effects: list[GameEffect] = []
    ordinal = 1
    for seat in targets:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.ADD_STATUS,
            context.action_key, target_seat=seat,
            payload={"target": seat, "status": DANCE_POOL_STATUS},
            preconditions={
                "status_present": {"status": DANCE_POOL_STATUS, "present": False},
            },
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
    effects.append(GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.SET_PRIVATE_DATA,
        context.action_key, target_seat=actor,
        payload={
            "target": actor, "key": POOLED_KEY,
            "value": [*_pooled_seats(context), *targets],
        },
        sort_key=(ordinal,), **common,
    ))
    ordinal += 1
    effects.append(GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "DANCE_POOL",
            "payload": {
                "seat": actor, "pool_seats": list(targets),
                "round_number": context.round_number,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    ))
    ordinal += 1
    if actor in targets:
        # The dancer joined the dance: the whole pool is shielded from the knife.
        for seat in targets:
            effects.append(GameEffect(
                derive_effect_id(context.action_key, ordinal),
                EffectKind.SUBMIT_PROTECTION,
                context.action_key, target_seat=seat,
                payload={"target": seat, "amount": 1, "source": DANCE_VEIL_SOURCE},
                sort_key=(ordinal,), **common,
            ))
            ordinal += 1
    effects.append(_reasoning_effect(context, command, ordinal, **common))
    return tuple(effects)


DANCE_POOL_CONTRACT = ActionContract(
    contract_id="dance_pool",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # After the Nightmare (10) and before the Mask (30): the mask has to be able
    # to see tonight's pool, which is what its own check reads.
    order=20,
    action_types=("pool", "pass"),
    actions_requiring_target=frozenset(),
    multi_target_actions={"pool": _POOL_SIZE},
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.ADD_STATUS, EffectKind.SUBMIT_PROTECTION,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=dancer_applicable,
    validate=validate_dance_pool,
    resolve=resolve_dance_pool,
)

DANCER_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-dancer",
    display_name="Dancer",
    camp_id="good",
    contracts=(DANCE_POOL_CONTRACT,),
    initial_resources={POISON_IMMUNE_RESOURCE: 1},
    initial_private_data={POOLED_KEY: ()},
    allowed_effects=frozenset({
        EffectKind.ADD_STATUS, EffectKind.SUBMIT_PROTECTION,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "你是舞者（好人神职，来自「假面舞会」板）：舞者免疫女巫的毒药。"
        "从第二夜起，每晚**必须**邀请恰好三名存活玩家共舞（选择由你决定，不可放弃）。"
        "结算时看的是这三人**真实阵营**的多数与少数：三人同一阵营则无事发生，"
        "否则人数少的一方当夜出局。"
        "每名玩家整局只能参加一次共舞，所以被邀请过的人不会再出现在可选名单里；"
        "当可选的存活玩家不足三人时，起舞自动失效。"
        "若你把**自己**选进舞池，则这三人当夜免疫狼刀（但挡不住女巫的毒药）。"
        "舞池里的阵营比较会被「假面」的面具翻转：面具可以把一名成员在本次比较中算作对立方，"
        "所以「两好人一狼」这种看似安全的组合也可能被改成单一阵营（无人出局）或反过来。"
        "战术提示：多数情况下把自己或神职放进舞池以触发免疫，并尽量制造 2:1 的少数方击杀；"
        "但要注意别把三个好人都圈进去——那会让全场毫无收获。"
    ),
)


class Dancer(BaseRole):
    """Dancer: the nightly pool is the pipeline contract; the settlement judges it."""
