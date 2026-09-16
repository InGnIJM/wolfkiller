from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf

_EXPLODE_CAUSE = "self_explode"


def werewolf_king_applicable(context: ActionContext) -> bool:
    return context.actor_alive and context.resources.get("explode", 0) > 0


def validate_werewolf_king_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "explode":
        return ()
    if context.resources.get("explode", 0) <= 0:
        return (RuleViolation("explode_unavailable", "self-destruct is no longer available"),)
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_target", "the self-destruct must take another player along"),)
    return ()


def _king_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.action_type == "explode" and command.target_seat is not None:
        thought = f"决定自爆并带走 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定暂不自爆：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "WEREWOLF_KING_REASONING",
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


def resolve_werewolf_king_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_king_reasoning_effect(context, command, 1, **common),)
    actor, target = context.actor_seat, command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": "explode", "amount": 1},
            preconditions={"resource_equals": {"resource": "explode", "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "amount": 1, "cause": _EXPLODE_CAUSE},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": _EXPLODE_CAUSE},
            sort_key=(3,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 4), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "SELF_EXPLODE",
                "payload": {
                    "seat": actor, "target_seat": target,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(4,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 5), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "DAY_INTERRUPTED",
                "payload": {
                    "seat": actor, "target_seat": target, "cause": _EXPLODE_CAUSE,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(5,), **common,
        ),
        _king_reasoning_effect(context, command, 6, **common),
    )


WEREWOLF_KING_EXPLODE_CONTRACT = ActionContract(
    contract_id="werewolf_king_explode", schedule_point=SchedulePoint.DAY_ACTION,
    order=20, action_types=("explode", "pass"),
    actions_requiring_target=frozenset({"explode"}), fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    # No per-game limit: a "pass" also counts as an accepted action, and the
    # king must be asked again before every later speaker. The one-shot is
    # enforced by the ``explode`` resource instead.
    per_window_limit=1,
    is_applicable=werewolf_king_applicable, validate=validate_werewolf_king_action,
    resolve=resolve_werewolf_king_action,
)

WEREWOLF_KING_SPEC = RoleSpec(
    role_id="wolf-killer-werewolf-king", display_name="Werewolf King", camp_id="werewolf",
    contracts=(WEREWOLF_KILL_CONTRACT, WEREWOLF_KING_EXPLODE_CONTRACT),
    initial_resources={"explode": 1},
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    instructions=(
        "Werewolf King is a werewolf: at night it joins the wolf channel and the shared kill "
        "vote like any other werewolf. During the day, before each player's speech in the "
        "speech phase, Werewolf King is asked once whether to self-destruct (explode) and "
        "take one other living player along, or pass. Exploding ends the day at once: both "
        "players are eliminated without last words, the remaining speeches and this day's "
        "vote are cancelled, and the game goes straight to night. The self-destruct can be "
        "used only once and only while alive; when eliminated by poison, exile, or another "
        "player's skill, Werewolf King leaves normally and takes nobody along. Exploding is "
        "optional and usually a late-game or emergency play; pass whenever staying hidden "
        "serves the wolf team better."
    ),
)


class WerewolfKing(Werewolf):
    """Werewolf King: a werewolf whose daytime self-destruct is handled by the
    pipeline contract; the daytime instance speaks and votes like a werewolf."""

    def get_skills(self) -> list[str]:
        return ["kill", "explode"]
