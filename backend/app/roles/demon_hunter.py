"""Demon Hunter: a good god that hunts one player a night from the second night.

The swing is settled on the *next* day, so both outcomes ride the delay channel
(``hunt`` is an always-delayed cause): hitting a werewolf sentences the target,
hitting a good player sentences the hunter. The camp it reads comes from
``selected_target``, which never reaches the model, so the hunter still has to
judge from the day's talk.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

HUNT_CAUSE = "hunt"
_WEREWOLF_CAMP = "werewolf"
#: 从第二晚开始（官方卡牌）：第一夜只能观望。
_FIRST_HUNT_ROUND = 2
#: 女巫的毒药对猎魔人无效。
POISON_IMMUNE_RESOURCE = "poison_immune"


def demon_hunter_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.round_number >= _FIRST_HUNT_ROUND


def validate_demon_hunter_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "hunt" and command.target_seat == context.actor_seat:
        return (RuleViolation(
            "self_hunt_forbidden", "the Demon Hunter cannot hunt itself",
        ),)
    return ()


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, *,
    verdict: str, **common: object,
) -> GameEffect:
    if command.action_type == "hunt" and command.target_seat is not None:
        thought = (
            f"决定狩猎 {command.target_seat} 号玩家，{verdict}："
            f"{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不狩猎：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "DEMON_HUNTER_REASONING",
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


def resolve_demon_hunter_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "hunt" or command.target_seat is None:
        return (_reasoning_effect(context, command, 1, verdict="", **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    camp = selected.get("camp_label")
    if type(camp) is not str:
        raise TypeError("selected target camp label must be a string")
    hit_a_wolf = camp == _WEREWOLF_CAMP
    victim = command.target_seat if hit_a_wolf else context.actor_seat
    verdict = "对方是狼人，次日出局" if hit_a_wolf else "对方是好人，自己次日以死谢罪"
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=victim,
            payload={"target": victim, "amount": 1, "cause": HUNT_CAUSE},
            sort_key=(1,), **common,
        ),
        _reasoning_effect(context, command, 2, verdict=verdict, **common),
    )


DEMON_HUNT_CONTRACT = ActionContract(
    contract_id="demon_hunt",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # Before the Guard (50): the verdict lands on the next day, so nothing here
    # reads the knife, and a stable early slot keeps the audience feed readable.
    order=40,
    action_types=("hunt", "pass"),
    actions_requiring_target=frozenset({"hunt"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"camp_label"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=demon_hunter_applicable,
    validate=validate_demon_hunter_action,
    resolve=resolve_demon_hunter_action,
)

DEMON_HUNTER_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-demon-hunter",
    display_name="Demon Hunter",
    camp_id="good",
    contracts=(DEMON_HUNT_CONTRACT,),
    initial_resources={POISON_IMMUNE_RESOURCE: 1},
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "Demon Hunter is a good-camp god. From the second night on it may hunt one living "
        "player each night (hunting is optional, and the same player may be hunted on "
        "consecutive nights). The verdict lands on the NEXT day, after the speeches: if the "
        "hunted player is a werewolf, that player dies; if the hunted player is a good "
        "player, YOU die instead. The witch's poison never kills you. You do not learn the "
        "camp at night — you have to judge from the day's talk, so hunting without evidence "
        "risks killing yourself by mistake. Weigh every hunt against win probability: a "
        "confirmed werewolf is the obvious target, while a night you spend on a coin flip "
        "costs the good camp either a villager or its hunter."
    ),
)


class DemonHunter(BaseRole):
    """Demon Hunter: its nightly hunt is the pipeline contract."""
