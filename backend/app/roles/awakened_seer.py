"""Awakened Seer: two seats in one look, one binary answer.

The official announcement is explicit about the granularity: the pair is
"窥视其身份" but what the answer says is only **whether a wolf is among them**
("从而判断其中是否有狼人") — not which one, not the camps. So the record this role
writes carries exactly two seats and one boolean, and the prompt reads back that
same shape (``pair_checks``).

The two seats ride the contract's declared ``multi_target_actions`` (the Dancer's
machinery), and the camps they need come from the plural selected-target
projection: ``selected_targets`` rows are hook-only, so the pair's identities
never reach any prompt but the seer's own answer.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole

CHECK_ACTION = "check"
PAIR_SIZE = 2
#: The private-fact namespace the answer is stored under; the projector reads it
#: back into the actor's own prompt (see ``_actor_private_facts``).
PAIR_CHECK_NAMESPACE = "pair_checks"
_REASONING_EVENT = "AWAKENED_SEER_REASONING"
_WOLF_CAMP = "werewolf"


def awakened_seer_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_awakened_seer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    # The declared multi-target count already forces exactly two distinct
    # living seats; the only extra rule is that the pair is two *other* players.
    if command.action_type == CHECK_ACTION and context.actor_seat in (
        command.target_seats or ()
    ):
        return (RuleViolation("self_check_forbidden", "the seer cannot look at itself"),)
    return ()


def _pair_camps(
    context: ActionContext, command: ActionCommand,
) -> tuple[tuple[int, int], tuple[str, str]]:
    """The two seats and their camps, from the plural selected-target facts.

    A missing or mismatched projection is a programming error, not a player
    mistake: the plural key is absent only when the sockets did not line up, so
    it raises the same way the single-target Seer does.
    """
    rows = context.facts.get("selected_targets")
    seats = tuple(command.target_seats or ())
    if (
        not isinstance(rows, tuple)
        or len(rows) != PAIR_SIZE
        or len(seats) != PAIR_SIZE
    ):
        raise ValueError("selected target facts are missing or mismatched")
    camps: list[str] = []
    for row, seat in zip(rows, seats):
        if not isinstance(row, Mapping) or row.get("seat") != seat:
            raise ValueError("selected target facts are missing or mismatched")
        camp = row.get("camp_label")
        if type(camp) is not str:
            raise TypeError("selected target camp label must be a string")
        camps.append(camp)
    return (seats[0], seats[1]), (camps[0], camps[1])


def _awakened_seer_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int,
    has_wolf: bool | None, **common: object,
) -> GameEffect:
    seats = tuple(command.target_seats or ())
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == CHECK_ACTION and seats:
        thought = (
            f"决定窥视 {'、'.join(str(seat) for seat in seats)} 号玩家"
            f"：{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不窥视：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": _REASONING_EVENT,
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type,
                "target_seats": list(seats),
                # The god view gets the binary answer; the players' own prompts
                # never see this event at all.
                "has_wolf": has_wolf,
                "reasoning": command.reasoning,
                "degraded": command.degraded,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


def resolve_awakened_seer_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != CHECK_ACTION or not command.target_seats:
        return (
            _awakened_seer_reasoning_effect(context, command, 1, None, **common),
        )
    seats, camps = _pair_camps(context, command)
    has_wolf = _WOLF_CAMP in camps
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.RECORD_PRIVATE_FACT,
            context.action_key, target_seat=context.actor_seat,
            payload={
                "target": context.actor_seat,
                "namespace": PAIR_CHECK_NAMESPACE,
                "fact": {"seats": list(seats), "has_wolf": has_wolf},
            },
            visibility=("ACTOR",), sort_key=(1,), **common,
        ),
        _awakened_seer_reasoning_effect(context, command, 2, has_wolf, **common),
    )


AWAKENED_SEER_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-awakened-seer", display_name="Awakened Seer", camp_id="good",
    # The identity-check point is shared with the ordinary Seer, the Pure White
    # and the Wolf Witch, so every skill on it declares its own line.
    opening_narration=("觉醒预言家请睁眼", "请选择两名玩家，判断其中是否有狼人。"),
    contracts=(ActionContract(
        contract_id="awakened_seer_check",
        schedule_point=SchedulePoint.NIGHT_SEER_ACTION,
        order=10,
        action_types=(CHECK_ACTION, "pass"),
        actions_requiring_target=frozenset(),
        multi_target_actions={CHECK_ACTION: PAIR_SIZE},
        fallback_action_type="pass",
        allowed_effects=frozenset({
            EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT,
        }),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        selected_target_fact_namespaces=frozenset({"camp_label"}),
        per_window_limit=1,
        per_round_limit=1,
        is_applicable=awakened_seer_applicable,
        validate=validate_awakened_seer_action,
        resolve=resolve_awakened_seer_action,
    ),),
    initial_private_data={PAIR_CHECK_NAMESPACE: ()},
    allowed_effects=frozenset({
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "Awakened Seer is a good-camp god role. Each night you name exactly two "
        "other living players and the engine tells you one thing: whether at least "
        "one werewolf is among them. You are never told which of the two, and you "
        "do not learn their exact roles — only that answer. The same pair may be "
        "looked at again on a later night, and you may keep one seat in the pair "
        "across nights. Your answer is private: nobody else sees it, so it is "
        "yours to reveal or to keep. Counts as a god for win checks."
    ),
)


class AwakenedSeer(BaseRole):
    """Daytime instance: the pair check is a pipeline contract, so the role
    itself only speaks and votes like any other god."""

    pass
