"""Knight: the daytime duel that reveals one player's camp and decides the day.

Mirrors the other extension samples (guard / idiot / werewolf king): a frozen
declaration plus pure hooks. The duel runs in the role-agnostic *post-speech*
DAY_ACTION window the engine opens after every living seat has spoken and
before the exile vote starts.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

_DUEL_CAUSE = "knight_duel"
_WEREWOLF_CAMP = "werewolf"


def knight_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.resources.get("duel", 0) > 0


def validate_knight_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "duel":
        return ()
    if context.resources.get("duel", 0) <= 0:
        return (RuleViolation("duel_unavailable", "the duel is no longer available"),)
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_target", "the knight must challenge another player"),)
    return ()


def _knight_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.action_type == "duel" and command.target_seat is not None:
        thought = f"决定翻牌决斗 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定暂不发动决斗：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "KNIGHT_REASONING",
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


def resolve_knight_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_knight_reasoning_effect(context, command, 1, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    camp = selected.get("camp_label")
    if type(camp) is not str:
        raise TypeError("selected target camp label must be a string")
    actor, target = context.actor_seat, command.target_seat
    challenged_a_wolf = camp == _WEREWOLF_CAMP
    # A wolf dies on the spot and the day ends at once; a good player leaves the
    # knight dead in penance while the day keeps its speeches and its vote.
    victim = target if challenged_a_wolf else actor
    effects = [
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": "duel", "amount": 1},
            preconditions={"resource_equals": {"resource": "duel", "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=victim,
            payload={"target": victim, "amount": 1, "cause": _DUEL_CAUSE},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "KNIGHT_DUEL",
                "payload": {
                    "seat": actor, "target_seat": target, "camp": camp,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
    ]
    if challenged_a_wolf:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, 4), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "DAY_INTERRUPTED",
                "payload": {
                    "seat": actor, "target_seat": target, "cause": _DUEL_CAUSE,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(4,), **common,
        ))
    effects.append(_knight_reasoning_effect(context, command, len(effects) + 1, **common))
    return tuple(effects)


KNIGHT_DUEL_CONTRACT = ActionContract(
    contract_id="knight_duel",
    # Its own schedule point, not DAY_ACTION: the duel belongs after the last
    # speech, while a per-speaker DAY_ACTION role would otherwise be asked in
    # this window too.
    schedule_point=SchedulePoint.POST_SPEECH_ACTION,
    order=30,
    action_types=("duel", "pass"),
    actions_requiring_target=frozenset({"duel"}),
    fallback_action_type="pass",
    allowed_effects=frozenset(
        {EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}
    ),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"camp_label"}),
    # No per-game limit: a "pass" also counts as an accepted action, and the
    # knight must be asked again before every later speaker. The one-shot is
    # enforced by the ``duel`` resource instead.
    per_window_limit=1,
    is_applicable=knight_applicable,
    validate=validate_knight_action,
    resolve=resolve_knight_action,
)

KNIGHT_SPEC = RoleSpec(
    role_id="wolf-killer-knight", display_name="Knight", camp_id="good",
    contracts=(KNIGHT_DUEL_CONTRACT,),
    initial_resources={"duel": 1},
    allowed_effects=frozenset(
        {EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}
    ),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    tags=frozenset({"god"}),
    max_count=1,
    instructions=(
        "Knight is a good-camp god with no night action. Once per game, after every living "
        "player has finished speaking and before the exile vote starts, Knight may reveal "
        "the card and challenge one other living player. The judge announces whether that "
        "player is a werewolf or a good player. If the challenged player is a werewolf, that "
        "player dies immediately, the day ends at once, the remaining business of the day "
        "(including this day's exile vote) is cancelled and the game goes straight to night. "
        "If the challenged player is a good player, Knight dies in penance with no last words "
        "and the day continues normally: the vote still happens. The duel is optional and can "
        "be used only once and only while alive. Weigh the window against win probability: "
        "challenging a suspected wolf can convert a day with no consensus into a confirmed "
        "kill, while challenging a good player trades Knight away for nothing and hands the "
        "wolf team a free round. Passing keeps the card for a later, better-informed day, but "
        "an unused duel is lost when Knight dies at night or is exiled, so compare that risk "
        "each time. Challenging is optional; judge each window by its expected value for the "
        "good camp and pass whenever waiting for more information serves the good camp better."
    ),
)


class Knight(BaseRole):
    """Knight role: the daytime duel is handled by the pipeline contract; the
    daytime instance only speaks and votes like a villager."""

    def get_skills(self) -> list[str]:
        return ["duel"]
