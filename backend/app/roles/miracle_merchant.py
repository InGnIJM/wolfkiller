"""Miracle Merchant: lends a one-shot skill to another seat, and pays if it was a wolf.

Official card: 「每夜第一个行动，选择一名其他玩家成为幸运儿，使其获得三个一次性技能中的
一个：预言家的查验、女巫的毒或者守卫的守护，每局游戏限一次。若幸运儿是狼人，则 TA 不会
获得技能，且次日奇迹商人出局。」

The lucky one is a *state*, not a role — a lucky villager stays a villager for every
camp-based rule — so the three skills cannot live on the lucky seat's own spec. They
are **granted contracts** on the Merchant's spec (``RoleSpec.granted_contracts``):

* the Merchant's own effect writes a holder marker (``lucky_seer`` / ``lucky_poison``
  / ``lucky_guard``) onto the lucky seat;
* the scheduler issues a granted contract to whichever living seat holds its
  ``required_resources`` marker (``Scheduler._issue_granted``), with the seat's own
  role id, so every identity check downstream still holds;
* the skill is spent by consuming that marker, not by a quota counter, so a lucky
  seat that declines tonight still holds the skill tomorrow.

Where the skills sit: the Merchant acts first (``NIGHT_ACTION`` order 1), so a skill it
grants is already held when the point of the matching skill is issued — the lucky Seer
check at ``NIGHT_SEER_ACTION``, the lucky poison and guard at ``NIGHT_WITCH_ACTION``.
That is why the lucky Guard does not sit at the Guard's own point: requests for one
point are issued together, before the Merchant has acted, so a ``NIGHT_ACTION`` guard
would only ever become available a night late. Protection is only a pending effect
until ``NIGHT_COMMIT``, so the later point changes nothing the Guard's rules can see.

The backlash is a delayed death (``miracle_backlash``) landing after the next day's
speeches, the way a hunt does, so it needs no ``delayable`` mark of the Merchant's own.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.core.reflect import (
    REFLECT_CHECK_RESOURCE, REFLECT_POISON_RESOURCE, reflect_effects,
)
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

#: The private slot listing the skills already lent out (each at most once a game).
GRANTED_SKILLS_KEY = "granted_skills"
BACKLASH_CAUSE = "miracle_backlash"
_WOLF_CAMP = "werewolf"
_MERCHANT_EVENT = "MIRACLE_MERCHANT_REASONING"
_LUCKY_EVENT = "LUCKY_REASONING"

#: Merchant action -> (holder marker, what the lucky seat gets, in words).
SKILLS: Mapping[str, tuple[str, str]] = {
    "grant_seer": ("lucky_seer", "预言家的查验"),
    "grant_poison": ("lucky_poison", "女巫的毒药"),
    "grant_guard": ("lucky_guard", "守卫的守护"),
}
_CAMP_ZH = {"good": "好人", "werewolf": "狼人"}


# ── The Merchant's own choice ──────────────────────────────────────────────


def merchant_applicable(context: ActionContext) -> bool:
    """Every night while a skill is left to lend."""
    lent = context.facts.get(GRANTED_SKILLS_KEY) or ()
    return context.actor_alive and len(lent) < len(SKILLS)


def validate_merchant_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type not in SKILLS or command.target_seat is None:
        return ()
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_target", "the lucky one must be another player"),)
    if command.action_type in (context.facts.get(GRANTED_SKILLS_KEY) or ()):
        return (RuleViolation("skill_already_granted", "each skill can be lent only once a game"),)
    return ()


def _merchant_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int,
    *, backlash: bool = False, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type in SKILLS and command.target_seat is not None:
        gift = SKILLS[command.action_type][1]
        thought = (
            f"决定让 {command.target_seat} 号玩家成为幸运儿，赠予{gift}"
            f"：{command.reasoning or '无理由'}"
        )
        if backlash:
            thought += "（幸运儿是狼人：技能落空，次日商人出局）"
    else:
        thought = f"决定今晚不赠予技能：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _MERCHANT_EVENT,
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


def resolve_merchant_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type not in SKILLS or command.target_seat is None:
        return (_merchant_reasoning_effect(context, command, 1, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    camp = selected.get("camp_label")
    if type(camp) is not str:
        raise TypeError("selected target camp label must be a string")
    marker = SKILLS[command.action_type][0]
    actor, target = context.actor_seat, command.target_seat
    lent = tuple(context.facts.get(GRANTED_SKILLS_KEY) or ()) + (command.action_type,)
    backlash = camp == _WOLF_CAMP
    effects = [
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload={"target": actor, "key": GRANTED_SKILLS_KEY, "value": lent},
            sort_key=(1,), **common,
        ),
    ]
    if backlash:
        # A wolf takes nothing, and the Merchant is the one who pays — the skill
        # counts as spent either way, so it cannot be tried on the next seat.
        effects.append(GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "amount": 1, "cause": BACKLASH_CAUSE},
            sort_key=(2,), **common,
        ))
    else:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=target,
            payload={"target": target, "resource": marker, "value": 1},
            sort_key=(2,), **common,
        ))
    effects.append(
        _merchant_reasoning_effect(context, command, 3, backlash=backlash, **common)
    )
    return tuple(effects)


# ── The three lent skills ──────────────────────────────────────────────────


def _lucky_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int,
    *, outcome: str | None = None, **common: object,
) -> GameEffect:
    gift = {"check": "查验", "poison": "毒杀", "guard": "守护"}.get(command.action_type)
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif gift is not None and command.target_seat is not None:
        thought = (
            f"幸运儿决定{gift} {command.target_seat} 号玩家"
            f"{'（结果：' + outcome + '）' if outcome else ''}"
            f"：{command.reasoning or '无理由'}"
        )
    else:
        thought = f"幸运儿决定今晚不使用技能：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _LUCKY_EVENT,
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


def _spend(context: ActionContext, marker: str, ordinal: int, **common: object) -> GameEffect:
    """Spend the holder marker; the precondition keeps it atomic."""
    actor = context.actor_seat
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.CONSUME_RESOURCE,
        context.action_key, target_seat=actor,
        payload={"target": actor, "resource": marker, "amount": 1},
        preconditions={"resource_equals": {"resource": marker, "value": 1}},
        sort_key=(ordinal,), **common,
    )


def lucky_applicable(context: ActionContext) -> bool:
    """The scheduler already gated on the holder marker; only liveness is left."""
    return context.actor_alive


def validate_lucky_check(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "check" and command.target_seat == context.actor_seat:
        return (RuleViolation("self_check_forbidden", "the lucky seer cannot check itself"),)
    return ()


def resolve_lucky_check(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "check" or command.target_seat is None:
        return (_lucky_reasoning_effect(context, command, 1, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    camp = selected.get("camp_label")
    if type(camp) is not str:
        raise TypeError("selected target camp label must be a string")
    return (
        _spend(context, "lucky_seer", 1, **common),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.RECORD_PRIVATE_FACT,
            context.action_key, target_seat=context.actor_seat,
            payload={
                "target": context.actor_seat, "namespace": "private_checks",
                "fact": {"target": command.target_seat, "camp": camp},
            },
            visibility=("ACTOR",), sort_key=(2,), **common,
        ),
        _lucky_reasoning_effect(
            context, command, 3, outcome=_CAMP_ZH.get(camp, camp), **common,
        ),
    ) + reflect_effects(context, selected, REFLECT_CHECK_RESOURCE, 4)


def resolve_lucky_poison(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "poison" or command.target_seat is None:
        return (_lucky_reasoning_effect(context, command, 1, **common),)
    target = command.target_seat
    effects = (
        _spend(context, "lucky_poison", 1, **common),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": "poison"},
            sort_key=(2,), **common,
        ),
        _lucky_reasoning_effect(context, command, 3, **common),
    )
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping):
        return effects
    return effects + reflect_effects(context, selected, REFLECT_POISON_RESOURCE, 4)


def resolve_lucky_guard(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "guard" or command.target_seat is None:
        return (_lucky_reasoning_effect(context, command, 1, **common),)
    target = command.target_seat
    return (
        _spend(context, "lucky_guard", 1, **common),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_PROTECTION,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "source": "guard"},
            sort_key=(2,), **common,
        ),
        _lucky_reasoning_effect(context, command, 3, **common),
    )


def validate_lucky_pass_through(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    """Poison and guard have no rule of their own beyond the generic target check."""
    return ()


_LUCKY_EFFECTS = frozenset({
    EffectKind.CONSUME_RESOURCE, EffectKind.EMIT_EVENT,
})

LUCKY_CHECK_CONTRACT = ActionContract(
    contract_id="lucky_seer_check",
    schedule_point=SchedulePoint.NIGHT_SEER_ACTION, order=90,
    action_types=("check", "pass"),
    actions_requiring_target=frozenset({"check"}), fallback_action_type="pass",
    allowed_effects=_LUCKY_EFFECTS | {
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.SET_RESOURCE, EffectKind.SUBMIT_DAMAGE,
    },
    required_resources={"lucky_seer": 1},
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"camp_label", "resource_labels"}),
    per_window_limit=1, per_round_limit=1,
    is_applicable=lucky_applicable, validate=validate_lucky_check,
    resolve=resolve_lucky_check,
)

LUCKY_POISON_CONTRACT = ActionContract(
    contract_id="lucky_poison",
    schedule_point=SchedulePoint.NIGHT_WITCH_ACTION, order=90,
    action_types=("poison", "pass"),
    actions_requiring_target=frozenset({"poison"}), fallback_action_type="pass",
    allowed_effects=_LUCKY_EFFECTS | {EffectKind.SUBMIT_DAMAGE, EffectKind.SET_RESOURCE},
    required_resources={"lucky_poison": 1},
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"resource_labels"}),
    per_window_limit=1, per_round_limit=1,
    is_applicable=lucky_applicable, validate=validate_lucky_pass_through,
    resolve=resolve_lucky_poison,
)

LUCKY_GUARD_CONTRACT = ActionContract(
    contract_id="lucky_guard",
    schedule_point=SchedulePoint.NIGHT_WITCH_ACTION, order=95,
    action_types=("guard", "pass"),
    actions_requiring_target=frozenset({"guard"}), fallback_action_type="pass",
    allowed_effects=_LUCKY_EFFECTS | {EffectKind.SUBMIT_PROTECTION},
    required_resources={"lucky_guard": 1},
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    per_window_limit=1, per_round_limit=1,
    is_applicable=lucky_applicable, validate=validate_lucky_pass_through,
    resolve=resolve_lucky_guard,
)

MIRACLE_GRANT_CONTRACT = ActionContract(
    contract_id="miracle_grant",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # 每夜第一个行动：earlier than every other night role, the Lonely Girl's first
    # night pick (5) included, so the skill is already held when the later points
    # of the same night are issued.
    order=1,
    action_types=(*SKILLS, "pass"),
    actions_requiring_target=frozenset(SKILLS), fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.SET_RESOURCE, EffectKind.SET_PRIVATE_DATA,
        EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"camp_label"}),
    per_window_limit=1, per_round_limit=1,
    is_applicable=merchant_applicable, validate=validate_merchant_action,
    resolve=resolve_merchant_action,
)

MIRACLE_MERCHANT_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-miracle-merchant",
    display_name="Miracle Merchant", camp_id="good",
    opening_narration=("奇迹商人请睁眼", "请选择一名玩家成为幸运儿，并决定赠予他的技能。"),
    contracts=(MIRACLE_GRANT_CONTRACT,),
    granted_contracts=(
        LUCKY_CHECK_CONTRACT, LUCKY_POISON_CONTRACT, LUCKY_GUARD_CONTRACT,
    ),
    granted_notes={
        "lucky_seer_check": (
            "LUCKY SKILL — you were chosen as the lucky one and hold a one-shot Seer's "
            "check: on any night you may name one other living player and learn their "
            "camp. You keep your own role, camp and win condition; this is only an extra "
            "ability that you can use once, now or on a later night, and you may keep "
            "it. The answer is private to you."
        ),
        "lucky_poison": (
            "LUCKY SKILL — you were chosen as the lucky one and hold a one-shot Witch's "
            "poison: on any night you may poison one living player, who dies "
            "(Guard protection does not stop poison). You keep your own role, camp and "
            "win condition; the poison can be used once, now or on a later night."
        ),
        "lucky_guard": (
            "LUCKY SKILL — you were chosen as the lucky one and hold a one-shot Guard's "
            "protection: on any night you may protect one living player (yourself "
            "included) from the werewolf kill. Protection stacked with the Witch's "
            "antidote on the same target still dies (double-save). You keep your own "
            "role, camp and win condition; the protection can be used once, now or on a "
            "later night."
        ),
    },
    initial_private_data={GRANTED_SKILLS_KEY: ()},
    allowed_effects=frozenset({
        EffectKind.SET_RESOURCE, EffectKind.SET_PRIVATE_DATA, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.CONSUME_RESOURCE,
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.SUBMIT_PROTECTION,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "Miracle Merchant is a good-camp god role and acts first every night. Each night "
        "you may choose one other living player as the lucky one and lend them one of "
        "three one-shot skills: the Seer's check (grant_seer), the Witch's poison "
        "(grant_poison) or the Guard's protection (grant_guard). Each skill can be lent "
        "only once per game, so at most three times. The lucky one is told what they hold "
        "and may use it once, tonight or later; nobody else, including you, learns how it "
        "was used. If the lucky one is a werewolf (any werewolf-camp seat) they receive "
        "nothing and you die after the next day's speeches — so the choice is a bet on "
        "who is good. You may pass to hold a skill back."
    ),
)


class MiracleMerchant(BaseRole):
    """Daytime instance: the lending is a pipeline contract, so the role itself
    only speaks and votes like any other god."""

    pass
