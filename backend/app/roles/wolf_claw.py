"""The Awakened Wolf King's two claw contracts, shared with its pack.

The Awakened Wolf King owns two ``awakened_claw`` resources. It may hand one
per night to a living wolf-pack teammate; a teammate that receives one can use
it on death exactly like the King itself. The single-claw contract is attached
to every ordinary wolf-pack role as well, because a gifted teammate's spec has
to declare the reaction it may fire; its ``is_applicable`` gate reads the
resource, so a role that never received a claw never gets a window.

Two contracts are needed because the command schema pins one target count per
contract: ``awakened_wolf_claw_sweep`` spends both claws on two targets, and
``awakened_wolf_claw`` spends one on a single target. Responses run in
``order``: sweep (41) first, then single (42). If the sweep fires, the single
window re-evaluates ``is_applicable`` after the resource has dropped to zero and
skips itself; if the sweep is passed, the single window still fires.

The death-reason set deliberately mirrors the ordinary Wolf King's: a claw may
answer a wolf knife, an exile, or a hunter's shot. Poison, a daytime
self-destruct, a charm, a knight duel, and a claw striking a claw holder do not
fire it. The official 2023 Q&A also rules out the last surviving wolf; that
check is local to this new contract, so the ordinary Wolf King's historical
behaviour is untouched.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.roles.reasoning import reasoning_effect
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RuleViolation, SchedulePoint,
)

#: One claw, one death. A teammate receives the resource and the one-time marker.
AWAKENED_CLAW_RESOURCE = "awakened_claw"
#: Permanent "this teammate has already received a claw" marker. The resource
#: itself goes to zero when the claw is spent, so the pack needs a separate mark
#: to enforce "each teammate at most once a game".
AWAKENED_CLAW_RECEIVED_RESOURCE = "awakened_claw_received"
#: Private-data key the pass writes onto the receiver. The key is declared by
#: every wolf-pack role's ``initial_private_data`` so the projector reads it back
#: into the receiver's prompt; without the declaration the note would be stored
#: and invisible.
AWAKENED_CLAW_GIFT_KEY = "awakened_claw_gift"

#: Ways out that still let a claw fire. ``wolf_king_shot`` is absent for the
#: same reason it is absent from the ordinary Wolf King: a claw cannot kill the
#: seat whose own claw is resolving. Self-destruct, poison, charm, and the knight
#: duel are all explicitly out.
_CLAW_REASONS = frozenset({"wolf_kill", "exile", "hunter_shot"})

_AWAKENED_CLAW_GIFT_NOTE = (
    "觉醒狼王把一次狼王爪亲传给了你：你出局时（被狼刀、被放逐、被猎人带走；"
    "非毒、非自爆、非殉情、非决斗，且你不是最后一狼）可以发动它，带走一名存活玩家。"
)


def _last_wolf_standing(context: ActionContext) -> bool:
    """True when no other wolf-pack member is still alive.

    Mirrors the Crimson Envoy's helper: ``camp_members`` is the pack roster
    (dead members included) and ``alive_seats`` is the public liveness list, so
    the hook stays pure.
    """
    members = context.facts.get("camp_members") or ()
    alive = context.facts.get("alive_seats") or ()
    if not isinstance(members, (list, tuple)) or not isinstance(alive, (list, tuple)):
        return False
    living = {seat for seat in alive if type(seat) is int}
    return all(
        seat == context.actor_seat or seat not in living
        for seat in members
        if type(seat) is int
    )


def _living_teammates(context: ActionContext) -> tuple[int, ...]:
    members = context.facts.get("camp_members") or ()
    alive = context.facts.get("alive_seats") or ()
    if not isinstance(members, (list, tuple)) or not isinstance(alive, (list, tuple)):
        raise TypeError("camp_members and alive_seats must be sequences")
    living = {seat for seat in alive if type(seat) is int}
    return tuple(
        seat for seat in members
        if type(seat) is int and seat != context.actor_seat and seat in living
    )


def _selected_resource_labels(context: ActionContext) -> tuple[object, ...]:
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping):
        raise ValueError("selected target fact is missing")
    labels = selected.get("resource_labels") or ()
    if not isinstance(labels, (list, tuple)):
        raise TypeError("selected target resource labels must be a sequence")
    return tuple(labels)


def awakened_claw_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason in _CLAW_REASONS
        and context.resources.get(AWAKENED_CLAW_RESOURCE, 0) >= 1
        and not _last_wolf_standing(context)
    )


def awakened_claw_sweep_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason in _CLAW_REASONS
        and context.resources.get(AWAKENED_CLAW_RESOURCE, 0) >= 2
        and not _last_wolf_standing(context)
    )


def validate_awakened_claw_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "shoot":
        return ()
    if context.resources.get(AWAKENED_CLAW_RESOURCE, 0) < 1:
        return (RuleViolation("awakened_claw_unavailable", "the claw is unavailable"),)
    if command.target_seat is None:
        return (RuleViolation("awakened_claw_target_required", "a claw target is required"),)
    return ()


def validate_awakened_claw_sweep(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "sweep":
        return ()
    if context.resources.get(AWAKENED_CLAW_RESOURCE, 0) < 2:
        return (RuleViolation("awakened_claw_pair_unavailable", "both claws are required"),)
    targets = command.target_seats or ()
    if len(set(targets)) != len(targets):
        return (RuleViolation("awakened_claw_duplicate_target", "claw targets must be distinct"),)
    return ()


def _claw_reasoning_event(
    context: ActionContext, command: ActionCommand, ordinal: int,
    *, thought: str, **common: object,
) -> GameEffect:
    return reasoning_effect(
        context, command, ordinal, event_type="WOLF_KING_REASONING",
        thought=thought, **common,
    )


def _thought_for(context: ActionContext, command: ActionCommand) -> str:
    if command.degraded:
        return DEGRADED_THOUGHT
    if command.action_type == "shoot" and command.target_seat is not None:
        return f"决定发动狼王爪带走 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    if command.action_type == "sweep" and command.target_seats:
        seats = "、".join(str(seat) for seat in command.target_seats)
        return f"决定一次发动双爪带走 {seats} 号玩家：{command.reasoning or '无理由'}"
    return f"决定不发动狼王爪：{command.reasoning or '无理由'}"


def resolve_awakened_claw_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_claw_reasoning_event(
            context, command, 1, thought=_thought_for(context, command), **common,
        ),)
    target = command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={
                "target": context.actor_seat,
                "resource": AWAKENED_CLAW_RESOURCE, "amount": 1,
            },
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": "wolf_king_shot"},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": "WOLF_KING_SHOT", "payload": {
                "target_seat": target, "seat": context.actor_seat,
            }},
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _claw_reasoning_event(
            context, command, 4, thought=_thought_for(context, command), **common,
        ),
    )


def resolve_awakened_claw_sweep(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_claw_reasoning_event(
            context, command, 1, thought=_thought_for(context, command), **common,
        ),)
    targets = tuple(command.target_seats or ())
    effects: list[GameEffect] = [
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={
                "target": context.actor_seat,
                "resource": AWAKENED_CLAW_RESOURCE, "amount": 2,
            },
            sort_key=(1,), **common,
        ),
    ]
    ordinal = 2
    for target in targets:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": "wolf_king_shot"},
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
    for target in targets:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": "WOLF_KING_SHOT", "payload": {
                "target_seat": target, "seat": context.actor_seat,
            }},
            visibility=("PUBLIC",), sort_key=(ordinal,), **common,
        ))
        ordinal += 1
    effects.append(_claw_reasoning_event(
        context, command, ordinal, thought=_thought_for(context, command), **common,
    ))
    return tuple(effects)


def awakened_claw_pass_applicable(context: ActionContext) -> bool:
    return (
        context.actor_alive
        and context.resources.get(AWAKENED_CLAW_RESOURCE, 0) >= 1
        and bool(_living_teammates(context))
    )


def validate_awakened_claw_pass(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "pass_claw":
        return ()
    target = command.target_seat
    if target is None:
        return (RuleViolation("awakened_claw_target_required", "a teammate is required"),)
    if target == context.actor_seat:
        return (RuleViolation("awakened_claw_self_target", "the claw cannot be passed to yourself"),)
    if target not in _living_teammates(context):
        return (RuleViolation("awakened_claw_not_a_teammate", "the target must be a living wolf-pack teammate"),)
    labels = _selected_resource_labels(context)
    if AWAKENED_CLAW_RECEIVED_RESOURCE in labels:
        return (RuleViolation("awakened_claw_already_held", "that teammate already received a claw"),)
    return ()


def resolve_awakened_claw_pass(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_claw_reasoning_event(
            context, command, 1,
            thought=(
                DEGRADED_THOUGHT if command.degraded
                else f"决定今晚不亲传狼王爪：{command.reasoning or '无理由'}"
            ),
            **common,
        ),)
    target = command.target_seat
    assert target is not None
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={
                "target": context.actor_seat,
                "resource": AWAKENED_CLAW_RESOURCE, "amount": 1,
            },
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=target,
            payload={
                "target": target,
                "resource": AWAKENED_CLAW_RECEIVED_RESOURCE, "value": 1,
            },
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=target,
            payload={
                "target": target, "key": AWAKENED_CLAW_GIFT_KEY,
                "value": _AWAKENED_CLAW_GIFT_NOTE,
            },
            sort_key=(3,), **common,
        ),
        _claw_reasoning_event(
            context, command, 4,
            thought=(
                DEGRADED_THOUGHT if command.degraded
                else f"决定把一次狼王爪亲传给 {target} 号玩家：{command.reasoning or '无理由'}"
            ),
            **common,
        ),
    )


AWAKENED_CLAW_CONTRACT = ActionContract(
    contract_id="awakened_wolf_claw",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=42,
    action_types=("shoot", "pass"),
    actions_requiring_target=frozenset({"shoot"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_CLAW_REASONS,
    per_window_limit=1,
    is_applicable=awakened_claw_applicable,
    validate=validate_awakened_claw_action,
    resolve=resolve_awakened_claw_action,
)

AWAKENED_CLAW_SWEEP_CONTRACT = ActionContract(
    contract_id="awakened_wolf_claw_sweep",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=41,
    action_types=("sweep", "pass"),
    actions_requiring_target=frozenset(),
    multi_target_actions={"sweep": 2},
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_CLAW_REASONS,
    per_window_limit=1,
    is_applicable=awakened_claw_sweep_applicable,
    validate=validate_awakened_claw_sweep,
    resolve=resolve_awakened_claw_sweep,
)

AWAKENED_CLAW_PASS_CONTRACT = ActionContract(
    contract_id="awakened_wolf_claw_pass",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    order=56,
    action_types=("pass_claw", "pass"),
    actions_requiring_target=frozenset({"pass_claw"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_RESOURCE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    selected_target_fact_namespaces=frozenset({"resource_labels"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=awakened_claw_pass_applicable,
    validate=validate_awakened_claw_pass,
    resolve=resolve_awakened_claw_pass,
)


# ── The gifted claw carried by a wolf-pack teammate ─────────────────────────


def awakened_claw_gift_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason in _CLAW_REASONS
        and bool(context.facts.get(AWAKENED_CLAW_GIFT_KEY))
        and not _last_wolf_standing(context)
    )


def validate_awakened_claw_gift(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "shoot":
        return ()
    if not context.facts.get(AWAKENED_CLAW_GIFT_KEY):
        return (RuleViolation("awakened_claw_unavailable", "the claw is unavailable"),)
    if command.target_seat is None:
        return (RuleViolation("awakened_claw_target_required", "a claw target is required"),)
    return ()


def resolve_awakened_claw_gift(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_claw_reasoning_event(
            context, command, 1, thought=_thought_for(context, command), **common,
        ),)
    target = command.target_seat
    assert target is not None
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload={
                "target": context.actor_seat, "key": AWAKENED_CLAW_GIFT_KEY,
                "value": None,
            },
            visibility=("ACTOR",), sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": "wolf_king_shot"},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": "WOLF_KING_SHOT", "payload": {
                "target_seat": target, "seat": context.actor_seat,
            }},
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _claw_reasoning_event(
            context, command, 4, thought=_thought_for(context, command), **common,
        ),
    )


AWAKENED_CLAW_GIFT_CONTRACT = ActionContract(
    contract_id="awakened_wolf_claw_gift",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=42,
    action_types=("shoot", "pass"),
    actions_requiring_target=frozenset({"shoot"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.SET_PRIVATE_DATA, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_CLAW_REASONS,
    per_window_limit=1,
    is_applicable=awakened_claw_gift_applicable,
    validate=validate_awakened_claw_gift,
    resolve=resolve_awakened_claw_gift,
)
