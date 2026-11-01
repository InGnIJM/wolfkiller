"""Fragrance Phantom: the bond that drags the other player along.

The official announcement gives this role three pieces, and they land in three
different places:

* **not in the pack** (``no_wolf_pack``) until every other wolf is gone, and only
  then the lone knife — that is ``roles/hidden_wolf.py``'s shared contract, used
  here byte-identically.
* **one other wolf's position, first night**: the projector derives a
  ``wolf_hint_seat`` for this seat's own contexts (a stable hash of the game id
  picks among the living pack, so the tip is arbitrary across games and identical
  on a resume). It is a *legitimate* fact for this role, so unlike the Awakened
  Hunter's directional scan it is **not** hoisted out of the prompt.
* **氤氲缠绕**: each night she binds two players; when one of them leaves the game
  the other goes with him. The pair lives in her declared ``bond_pair`` slot,
  which is also what makes the response queue open a window for *her* when either
  member dies (``RoleSpec.bond_slot``) — she is the one who answers for the bond,
  even though the seat that died is somebody else. The chain fires **once per
  game**: the react spends ``bond_available``, and the nightly bind closes with
  it.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.values import DeathCause
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.hidden_wolf import hidden_wolf_role_spec

BOND_ACTION = "bond"
PAIR_SIZE = 2
#: Declared private slot: the two seats of the *current* bond. Non-empty on the
#: spec (``bond_slot``) is what tells the response queue to wake her when one of
#: them dies, so the same name is the fact, the gate and the queue's index.
BOND_SLOT = "bond_pair"
#: The one-shot marker: spent by the chain, checked by the nightly bind.
BOND_MARKER = "bond_available"
#: The chain's death cause, in the authoritative ``DeathCause`` catalogue.
BOND_CAUSE = "bond_death"
_REASONING_EVENT = "FRAGRANCE_PHANTOM_REASONING"
_BOND_EVENT = "FRAGRANCE_BOND"
#: Every cause drags the partner. The queue matches on the event's reason, and an
#: empty ``response_reasons`` means "reasonless events only", so the whole
#: catalogue has to be declared explicitly — the same shape the Awakened
#: Hunter's hunt uses.
_CHAIN_REASONS = frozenset(cause.value for cause in DeathCause)


def fragrance_applicable(context: ActionContext) -> bool:
    """Alive, and the bond has not fired yet."""
    return context.actor_alive and context.resources.get(BOND_MARKER, 0) > 0


def validate_fragrance_bond(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    # The declared multi-target count already forces two distinct living seats;
    # binding herself or a teammate is explicitly allowed, so there is no extra
    # rule to add here.
    return ()


def _fragrance_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    seats = tuple(command.target_seats or ())
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == BOND_ACTION and seats:
        thought = (
            f"决定绑定 {'、'.join(str(seat) for seat in seats)} 号玩家"
            f"：{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不绑定：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _REASONING_EVENT,
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seats": list(seats),
                "reasoning": command.reasoning,
                "degraded": command.degraded,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_fragrance_bond(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != BOND_ACTION or not command.target_seats:
        return (_fragrance_reasoning_effect(context, command, 1, **common),)
    seats = tuple(command.target_seats)
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload={
                "target": context.actor_seat, "key": BOND_SLOT,
                "value": [seats[0], seats[1]],
            },
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": _BOND_EVENT,
                "payload": {
                    "seat": context.actor_seat, "target_seats": [seats[0], seats[1]],
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(2,), **common,
        ),
        _fragrance_reasoning_effect(context, command, 3, **common),
    )


def _current_pair(context: ActionContext) -> tuple[int, ...]:
    value = context.facts.get(BOND_SLOT)
    if type(value) not in (list, tuple):
        return ()
    return tuple(seat for seat in value if type(seat) is int)


def _chain_victim(context: ActionContext) -> int | None:
    """The seat that has to follow, or ``None`` when this death is not its turn.

    Every gate is repeated here on purpose: the response queue opens a window for
    a *declared* bond, and a pure react contract has no fallback — an effect the
    state does not allow would be fatal, so the hook answers "nothing to do"
    instead of guessing.
    """
    if context.resources.get(BOND_MARKER, 0) <= 0:
        return None
    trigger = context.trigger_event
    dead = trigger.get("target_seat") if isinstance(trigger, Mapping) else None
    if type(dead) is not int:
        return None
    pair = _current_pair(context)
    if dead not in pair:
        return None
    alive = context.facts.get("alive_seats", ())
    if type(alive) is not tuple:
        return None
    survivors = [seat for seat in pair if seat != dead and seat in alive]
    return survivors[0] if len(survivors) == 1 else None


def react_fragrance_chain(context: ActionContext) -> tuple[GameEffect, ...]:
    if context.source_event_id is None:
        return ()
    victim = _chain_victim(context)
    if victim is None:
        return ()
    seat = context.actor_seat
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": BOND_MARKER, "amount": 1},
            preconditions={"resource_equals": {"resource": BOND_MARKER, "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.MARK_DEATH,
            context.action_key, target_seat=victim,
            payload={"target": victim, "cause": BOND_CAUSE},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": _BOND_EVENT,
                "payload": {
                    "seat": seat, "target_seat": victim, "cause": BOND_CAUSE,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
    )


FRAGRANCE_BOND_CONTRACT = ActionContract(
    contract_id="fragrance_bond",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # Her own beat, between the Dancer (20) and the Mask (30): the pack's knife
    # comes later in the same night, so a bond made tonight already answers for a
    # knife made tonight only after the settlement — the death window is what
    # drags the partner, and it runs after every damage source has spoken.
    order=25,
    action_types=(BOND_ACTION, "pass"),
    actions_requiring_target=frozenset(),
    multi_target_actions={BOND_ACTION: PAIR_SIZE},
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=fragrance_applicable,
    validate=validate_fragrance_bond,
    resolve=resolve_fragrance_bond,
)

FRAGRANCE_CHAIN_CONTRACT = ActionContract(
    contract_id="fragrance_chain",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=50,
    action_types=("chain",),
    actions_requiring_target=frozenset(),
    fallback_action_type="chain",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.MARK_DEATH, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    # Every cause drags the partner: poisoned, shot, exiled, chained — the rule
    # does not distinguish. The one-shot marker is what closes the skill.
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_CHAIN_REASONS,
    per_window_limit=1,
    per_game_limit=1,
    is_applicable=fragrance_applicable,
    react=react_fragrance_chain,
)

FRAGRANCE_PHANTOM_SPEC = hidden_wolf_role_spec(
    "wolf-killer-fragrance-phantom", "Fragrance Phantom",
    opening_narration=("寻香魅影请睁眼", "请选择两名玩家进行绑定。"),
    extra_resources={BOND_MARKER: 1},
    extra_contracts=(FRAGRANCE_BOND_CONTRACT, FRAGRANCE_CHAIN_CONTRACT),
    extra_effects=frozenset({
        EffectKind.SET_PRIVATE_DATA, EffectKind.CONSUME_RESOURCE,
        EffectKind.MARK_DEATH, EffectKind.EMIT_EVENT,
    }),
    initial_private_data={BOND_SLOT: ()},
    bond_slot=BOND_SLOT,
    # One other wolf's position, first night onward. Unlike the Awakened
    # Hunter's directional scan this is knowledge the rules *give* her, so it is
    # deliberately left out of `_HOISTED_FACTS`: the fact is in her own prompt.
    projected_facts=frozenset({"wolf_hint_seat"}),
    instructions=(
        "Fragrance Phantom is a werewolf that starts outside the pack: you do not "
        "join the nightly discussion or the shared kill vote, the pack is never "
        "told who you are, and you cannot self-destruct. From the first night you "
        "know the position of one other wolf (the ``wolf_hint_seat`` fact), and you "
        "may join the pack's knife only after every other wolf is dead. Every night "
        "you may bind exactly two living players — yourself and a teammate "
        "included; the binding replaces the previous one. When one of the two "
        "leaves the game, the other follows him immediately, whatever the cause, "
        "and the skill is spent for the rest of the game: the pair you are holding "
        "when that happens is the last one you can make. Counts as a werewolf for "
        "win checks."
    ),
)


class FragrancePhantom(BaseRole):
    """Daytime instance: the bond and the chain are pipeline contracts, so the
    role itself only speaks and votes like the werewolf it counts as."""

