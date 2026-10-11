"""The Awakened Hunter: a directional shot that always lands on a werewolf.

The ordinary hunter names a seat and may shoot a good player; this one names a
*direction* and the rules resolve it into the closest living werewolf, so the
bullet can never hit its own camp. Two more differences matter enough that the
two roles cannot share a contract:

* every way out of the game opens this shot — the poison included, which is
  exactly the cause the ordinary hunter's whitelist excludes;
* the shot a night-dead hunter fires ignores special werewolf skills (the
  Awakened Wolf Beauty's stand-in among them), which travels as an
  ``unreplaceable`` damage mark rather than as a rule inside the engine.

The direction never reaches the model as a seat: ``directional_wolves`` is a
hook-only projected fact, so the seat the bullet lands on is decided by the rules
and only read by the pure hook.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.models.values import DeathCause
from app.roles.base import BaseRole

#: 死因目录里「巡猎」的那一格。普通猎人的枪是 ``hunter_shot``，两者分开记。
HUNT_CAUSE = "awakened_hunt"
#: The two directions: ascending seat order (正序) and descending (逆序).
_FORWARD = "hunt_forward"
_BACKWARD = "hunt_backward"
#: Action type -> the ``directional_wolves`` key it reads. The fact is named for
#: the geometry ("forward"/"backward") because the projector has no business
#: knowing the action vocabulary; the contract maps the two.
_DIRECTIONS = {_FORWARD: "forward", _BACKWARD: "backward"}

#: Every way out of the game opens this shot. The official card says so and its
#: example is the poison — the one cause the ordinary hunter's whitelist leaves
#: out. The response queue only opens a window for a reason it finds here, so
#: this set is the whole death-cause directory, read from its one authority.
_HUNT_REASONS = frozenset(cause.value for cause in DeathCause)


def _direction_target(context: ActionContext, command: ActionCommand) -> int | None:
    """The seat this direction resolves to, or ``None`` when it is empty."""
    key = _DIRECTIONS.get(command.action_type)
    wolves = context.facts.get("directional_wolves")
    if key is None or not isinstance(wolves, Mapping):
        return None
    seat = wolves.get(key)
    return seat if type(seat) is int and seat > 0 else None


def awakened_hunter_applicable(context: ActionContext) -> bool:
    """Open on this seat's own death, once, while a werewolf is still reachable."""
    trigger = context.trigger_event
    if (
        context.source_event_id is None
        or trigger is None
        or trigger.get("target_seat") != context.actor_seat
        or context.actor_alive
        or context.resources.get("gun", 0) <= 0
    ):
        return False
    wolves = context.facts.get("directional_wolves")
    if not isinstance(wolves, Mapping):
        return False
    return any(wolves.get(key) is not None for key in _DIRECTIONS.values())


def validate_awakened_hunter_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "pass":
        return ()
    if context.resources.get("gun", 0) <= 0:
        return (RuleViolation("gun_unavailable", "the awakened hunter's gun is spent"),)
    if _direction_target(context, command) is None:
        return (RuleViolation(
            "hunt_direction_empty", "no living werewolf lies in that direction",
        ),)
    return ()


def _awakened_hunter_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "pass":
        thought = f"决定不发动巡猎：{command.reasoning or '无理由'}"
    else:
        side = "正序" if command.action_type == _FORWARD else "逆序"
        target = _direction_target(context, command)
        thought = f"决定{side}发动巡猎，带走 {target} 号玩家：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "AWAKENED_HUNTER_REASONING",
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seat": _direction_target(context, command),
                "reasoning": command.reasoning,
                "degraded": command.degraded,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_awakened_hunter_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_awakened_hunter_reasoning_effect(context, command, 1, **common),)
    target = _direction_target(context, command)
    damage: dict[str, object] = {"target": target, "amount": 1, "cause": HUNT_CAUSE}
    if context.phase == "night":
        # A shot fired after a *night* death ignores the werewolves' protective
        # skills; the official example is the Awakened Wolf Beauty's illusion.
        # The mark rides on the damage so the settlement — not this hook — is
        # what decides whether a stand-in may replace the target.
        damage["unreplaceable"] = True
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "resource": "gun", "amount": 1},
            preconditions={"resource_equals": {"resource": "gun", "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload=damage,
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": "AWAKENED_HUNT", "payload": {"target_seat": target}},
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _awakened_hunter_reasoning_effect(context, command, 4, **common),
    )


AWAKENED_HUNT_CONTRACT = ActionContract(
    contract_id="awakened_hunt", schedule_point=SchedulePoint.DAWN_REACTION,
    order=45, action_types=(_FORWARD, _BACKWARD, "pass"),
    # The command carries a direction, never a seat: the seat is what the
    # rules derive from it, and the contract schema must not let a model
    # name a target directly.
    actions_requiring_target=frozenset(), fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_HUNT_REASONS, per_window_limit=1, per_game_limit=1,
    is_applicable=awakened_hunter_applicable,
    validate=validate_awakened_hunter_action,
    resolve=resolve_awakened_hunter_action,
)


AWAKENED_HUNTER_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-awakened-hunter", display_name="Awakened Hunter", camp_id="good",
    contracts=(AWAKENED_HUNT_CONTRACT,),
    initial_resources={"gun": 1},
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    projected_facts=frozenset({"directional_wolves"}),
    max_count=1,
    instructions=(
        "Awakened Hunter is a good-camp god. Any way out of the game — the wolf "
        "knife, a vote, the witch's poison, a duel, another hunter's bullet — "
        "opens one chance to fire the hunt, so unlike an ordinary hunter nothing "
        "about how you left the game takes the shot away. You never name a "
        "player: you pick a direction, hunt_forward (ascending seat numbers, "
        "wrapping around) or hunt_backward (descending), and the closest living "
        "werewolf in that direction dies. The shot is certain to hit a wolf, so "
        "the only real decision is whether its position is worth the bullet at "
        "all; pass when the information you leave behind would cost the good camp "
        "more than the kill earns. A shot fired after a *night* death ignores the "
        "werewolves' protective skills, so that is the one that cannot be dodged. "
        "Because the whole board knows a good player's death is followed by this "
        "shot, your threat also shapes how the wolves spend their night."
    ),
)


class AwakenedHunter(BaseRole):
    """Daytime instance: the hunt is a pipeline contract, so it only speaks and
    votes like the villager it otherwise is."""

    pass
