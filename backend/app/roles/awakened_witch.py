"""Awakened Witch: one antidote, three brews, and a committee for each poison.

The official 2024 card (see ``docs/reference/netease-role-skills.md``) gives the
Witch a normal antidote and three poison-brewing uses. A brew is not a normal
poison action: after naming a target the Witch must name 1 / 2 / 3 living
assistants (one more each use). The assistants learn the target and may talk in
a private night channel; the poison only lands when every one of them agrees.
Any veto, abstention, or missing answer wastes that brew, but the use is still
spent. The Witch never learns their choices.

That shape needs three sub-steps after her normal ``NIGHT_WITCH_ACTION``: pick
the assistants, let them talk/vote, then tally. The three schedule points live
outside ``_NIGHT_POINTS`` and are executed inside the existing witch stage by
the engine, so persisted stage numbers stay untouched. The tally is a
deterministic contract (the command provider synthesises its ``resolve``); the
votes reach it through the hook-only ``assist_votes`` projected fact, never
through the Witch's prompt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.core.effect_applier import derive_effect_id
from app.models.game import DEFAULT_WITCH_SELF_SAVE
from app.roles.witch import Witch
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)

BREW_RESOURCE = "brew"
BREW_USED_RESOURCE = "brew_used"
BREW_PLAN_KEY = "brew_plan"
ASSIST_HISTORY_KEY = "assist_history"
SAVED_SEAT_KEY = "saved_seat"
WOLF_KILL_TARGET_KEY = "wolf_kill_target"

ASSIST_TOKEN_RESOURCE = "assist_token"
ASSIST_TARGET_RESOURCE = "assist_target"
ASSIST_COUNT_RESOURCE = "assist_count"
ASSIST_ROUND_RESOURCE = "assist_round"
ASSIST_USED_RESOURCE = "assist_used"
ASSIST_VOTE_RESOURCE = "assist_vote"

_POISON_CAUSE = "poison"
_BREW_PICK_ID = "awakened_witch_assist_pick"
_ASSIST_VOTE_ID = "awakened_witch_assist_vote"
_TALLY_ID = "awakened_witch_assist_tally"
_ASSIST_NOTE = (
    "你是觉醒女巫毒药调配的协助者。你已知道本次毒药目标与所有协助者的座位；"
    "你们可以在夜里短暂交流。请判断是否同意毒杀该目标：全部协助者都同意时毒药才生效，"
    "任何一人反对都会让毒药失效。女巫不知道你的选择。输出 action_type=agree 表示同意、"
    "action_type=veto 表示反对；两种动作都必须把 target_seat 写成毒药目标座位；"
    "不想表态时用 action_type=pass。"
)


def _kill_target(context: ActionContext) -> int | None:
    value = context.facts.get(WOLF_KILL_TARGET_KEY)
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise TypeError("wolf kill target must be a positive seat or null")
    return value


def _plan(context: ActionContext) -> Mapping[str, object] | None:
    value = context.facts.get(BREW_PLAN_KEY)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("brew plan must be a mapping")
    return value


def _plan_round(plan: Mapping[str, object]) -> int | None:
    value = plan.get("round")
    return value if type(value) is int else None


def _plan_target(plan: Mapping[str, object]) -> int:
    value = plan.get("target")
    if type(value) is not int or value <= 0:
        raise TypeError("brew plan target must be a positive seat")
    return value


def _plan_use_index(plan: Mapping[str, object]) -> int:
    value = plan.get("use_index")
    if type(value) is not int or value not in (1, 2, 3):
        raise TypeError("brew plan use index must be 1, 2, or 3")
    return value


def _plan_assist_seats(plan: Mapping[str, object]) -> tuple[int, ...]:
    value = plan.get("assist_seats") or ()
    if not isinstance(value, (list, tuple)):
        raise TypeError("brew plan assist seats must be a sequence")
    seats = tuple(seat for seat in value if type(seat) is int)
    if len(seats) != len(value):
        raise TypeError("brew plan assist seats must be integers")
    return seats


def _selected_rows(context: ActionContext) -> tuple[Mapping[str, object], ...]:
    value = context.facts.get("selected_targets") or ()
    if not isinstance(value, (list, tuple)):
        raise TypeError("selected targets must be a sequence")
    rows = tuple(row for row in value if isinstance(row, Mapping))
    if len(rows) != len(value):
        raise TypeError("selected target rows must be mappings")
    return rows


def _selected_labels(row: Mapping[str, object]) -> tuple[object, ...]:
    value = row.get("resource_labels") or ()
    if not isinstance(value, (list, tuple)):
        raise TypeError("resource labels must be a sequence")
    return tuple(value)


def _witch_reasoning(
    context: ActionContext, command: ActionCommand, ordinal: int,
    *, action_type: str, target: int | None, thought: str, **common: object,
) -> GameEffect:
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "WITCH_REASONING",
            "payload": {
                "seat": context.actor_seat,
                "action_type": action_type,
                "target_seat": target,
                "reasoning": command.reasoning,
                "degraded": command.degraded,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )


# ── The brew: antidote / poison target / pass ───────────────────────────────


def awakened_witch_brew_applicable(context: ActionContext) -> bool:
    # She still receives the knife information after all potions are spent.
    return context.actor_alive


def validate_awakened_witch_brew(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "save":
        if context.resources.get("antidote", 0) <= 0:
            return (RuleViolation("antidote_unavailable", "antidote is unavailable"),)
        if command.target_seat != _kill_target(context):
            return (RuleViolation("invalid_save_target", "save target must be the wolf kill target"),)
        if command.target_seat == context.actor_seat:
            policy = context.facts.get("witch_self_save", DEFAULT_WITCH_SELF_SAVE)
            if policy == "never":
                return (RuleViolation(
                    "witch_self_save_forbidden",
                    "the witch can never save herself in this game",
                ),)
            if context.round_number != 1:
                return (RuleViolation(
                    "self_save_first_night_only",
                    "the witch can only save herself on the first night",
                ),)
    if command.action_type == "poison":
        if context.resources.get(BREW_RESOURCE, 0) <= 0:
            return (RuleViolation("brew_unavailable", "no poison brew is left"),)
        if command.target_seat is None:
            return (RuleViolation("brew_target_required", "a poison target is required"),)
        if command.target_seat == context.actor_seat:
            return (RuleViolation("self_poison_forbidden", "the witch cannot poison herself"),)
    return ()


def _brew_reasoning_thought(context: ActionContext, command: ActionCommand) -> str:
    if command.degraded:
        return DEGRADED_THOUGHT
    if command.action_type == "save" and command.target_seat is not None:
        return f"决定使用解药救 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    if command.action_type == "poison" and command.target_seat is not None:
        return f"决定调配毒药毒杀 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    return f"决定今晚不使用药水：{command.reasoning or '无理由'}"


def resolve_awakened_witch_brew(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_witch_reasoning(
            context, command, 1, action_type="pass", target=None,
            thought=_brew_reasoning_thought(context, command), **common,
        ),)
    actor = context.actor_seat
    target = command.target_seat
    assert target is not None
    if command.action_type == "save":
        return (
            GameEffect(
                derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
                context.action_key, target_seat=actor,
                payload={"target": actor, "resource": "antidote", "amount": 1},
                preconditions={"resource_equals": {"resource": "antidote", "value": 1}},
                sort_key=(1,), **common,
            ),
            GameEffect(
                derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_PROTECTION,
                context.action_key, target_seat=target,
                payload={"target": target, "amount": 1, "source": "witch_antidote"},
                sort_key=(2,), **common,
            ),
            GameEffect(
                derive_effect_id(context.action_key, 3), EffectKind.SET_PRIVATE_DATA,
                context.action_key, target_seat=actor,
                payload={"target": actor, "key": SAVED_SEAT_KEY, "value": target},
                visibility=("ACTOR",), sort_key=(3,), **common,
            ),
            GameEffect(
                derive_effect_id(context.action_key, 4), EffectKind.EMIT_EVENT,
                context.action_key,
                payload={"event_type": "WITCH_SAVE", "payload": {
                    "target_seat": target, "seat": actor,
                }},
                visibility=("PUBLIC",), sort_key=(4,), **common,
            ),
            _witch_reasoning(
                context, command, 5, action_type="save", target=target,
                thought=_brew_reasoning_thought(context, command), **common,
            ),
        )
    used = context.resources.get(BREW_USED_RESOURCE, 0)
    if type(used) is not int or used < 0 or used >= 3:
        raise TypeError("brew use counter is out of range")
    use_index = used + 1
    plan = {
        "target": target, "round": context.round_number,
        "use_index": use_index, "assist_count": use_index,
        "assist_seats": (),
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": BREW_RESOURCE, "amount": 1},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=actor,
            payload={"target": actor, "resource": BREW_USED_RESOURCE, "value": use_index},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload={"target": actor, "key": BREW_PLAN_KEY, "value": plan},
            sort_key=(3,), **common,
        ),
        _witch_reasoning(
            context, command, 4, action_type="poison", target=target,
            thought=_brew_reasoning_thought(context, command), **common,
        ),
    )


AWAKENED_WITCH_BREW_CONTRACT = ActionContract(
    contract_id="awakened_witch_brew",
    schedule_point=SchedulePoint.NIGHT_WITCH_ACTION,
    order=20,
    action_types=("save", "poison", "pass"),
    actions_requiring_target=frozenset({"save", "poison"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_RESOURCE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.SUBMIT_PROTECTION,
        EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"resource_labels"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=awakened_witch_brew_applicable,
    validate=validate_awakened_witch_brew,
    resolve=resolve_awakened_witch_brew,
)


# ── Pick 1 / 2 / 3 assistants ──────────────────────────────────────────────


def awakened_witch_assist_pick_applicable(context: ActionContext) -> bool:
    expected = {
        "awakened_witch_assist_pick_1": 1,
        "awakened_witch_assist_pick_2": 2,
        "awakened_witch_assist_pick_3": 3,
    }.get(context.contract_id)
    if expected is None or not context.actor_alive:
        return False
    plan = _plan(context)
    if plan is None or _plan_round(plan) != context.round_number:
        return False
    if _plan_assist_seats(plan):
        return False
    return _plan_use_index(plan) == expected


def _command_targets(command: ActionCommand) -> tuple[int, ...]:
    if command.target_seats:
        return tuple(command.target_seats)
    if command.target_seat is not None:
        return (command.target_seat,)
    return ()


def _pick_validate(context: ActionContext, command: ActionCommand) -> tuple[RuleViolation, ...]:
    if command.action_type != "assist":
        return ()
    plan = _plan(context)
    if plan is None:
        return (RuleViolation("brew_plan_missing", "there is no brew plan to assist"),)
    targets = _command_targets(command)
    if len(set(targets)) != len(targets):
        return (RuleViolation("assist_duplicate_target", "assistants must be distinct"),)
    if len(targets) == 1:
        selected = context.facts.get("selected_target")
        rows = {selected.get("seat"): selected} if isinstance(selected, Mapping) else {}
    else:
        rows = {row.get("seat"): row for row in _selected_rows(context)}
    for seat in targets:
        row = rows.get(seat)
        if row is None:
            continue
        if ASSIST_USED_RESOURCE in _selected_labels(row):
            return (RuleViolation(
                "assist_already_used", "every player can assist only once a game",
            ),)
    return ()


def _pick_reasoning_thought(command: ActionCommand, target: int) -> str:
    if command.degraded:
        return DEGRADED_THOUGHT
    if command.action_type == "assist":
        seats = _command_targets(command)
        return f"决定为毒杀 {target} 号调配协助者：{'、'.join(str(seat) for seat in seats)}；{command.reasoning or '无理由'}"
    return f"决定放弃本次毒药调配：{command.reasoning or '无理由'}"


def _resolve_pick(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    plan = _plan(context)
    if plan is None:
        return ()
    actor = context.actor_seat
    target = _plan_target(plan)
    use_index = _plan_use_index(plan)
    if command.action_type != "assist":
        return (
            GameEffect(
                derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
                context.action_key, target_seat=actor,
                payload={"target": actor, "key": BREW_PLAN_KEY, "value": None},
                sort_key=(1,), **common,
            ),
            GameEffect(
                derive_effect_id(context.action_key, 2), EffectKind.EMIT_EVENT,
                context.action_key,
                payload={
                    "event_type": "AWAKENED_WITCH_POISON_RESULT",
                    "payload": {
                        "round_number": context.round_number,
                        "target_seat": target, "result": "aborted",
                    },
                },
                visibility=("PUBLIC",), sort_key=(2,), **common,
            ),
            _witch_reasoning(
                context, command, 3, action_type="assist", target=target,
                thought=_pick_reasoning_thought(command, target), **common,
            ),
        )
    seats = _command_targets(command)
    effects: list[GameEffect] = []
    ordinal = 1
    for seat in seats:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": ASSIST_TOKEN_RESOURCE, "value": 1},
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": ASSIST_TARGET_RESOURCE, "value": target},
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": ASSIST_COUNT_RESOURCE, "value": use_index},
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=seat,
            payload={
                "target": seat, "resource": ASSIST_ROUND_RESOURCE,
                "value": context.round_number,
            },
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=seat,
            payload={"target": seat, "resource": ASSIST_USED_RESOURCE, "value": 1},
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
    updated = dict(plan)
    updated["assist_seats"] = seats
    effects.append(GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.SET_PRIVATE_DATA,
        context.action_key, target_seat=actor,
        payload={"target": actor, "key": BREW_PLAN_KEY, "value": updated},
        sort_key=(ordinal,), **common,
    ))
    ordinal += 1
    history = context.facts.get(ASSIST_HISTORY_KEY) or ()
    if not isinstance(history, (list, tuple)):
        raise TypeError("assist history must be a sequence")
    merged = tuple(int(seat) for seat in history) + seats
    effects.append(GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.SET_PRIVATE_DATA,
        context.action_key, target_seat=actor,
        payload={"target": actor, "key": ASSIST_HISTORY_KEY, "value": merged},
        sort_key=(ordinal,), **common,
    ))
    ordinal += 1
    effects.append(GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "AWAKENED_WITCH_ASSIST_PICK",
            "payload": {
                "round_number": context.round_number,
                "target_seat": target, "assistant_seats": list(seats),
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    ))
    ordinal += 1
    effects.append(_witch_reasoning(
        context, command, ordinal, action_type="assist", target=target,
        thought=_pick_reasoning_thought(command, target), **common,
    ))
    return tuple(effects)


def _pick_contract(index: int) -> ActionContract:
    common = {
        "contract_id": f"{_BREW_PICK_ID}_{index}",
        "schedule_point": SchedulePoint.NIGHT_WITCH_ASSIST_PICK,
        "order": 10 + index,
        "action_types": ("assist", "pass"),
        "fallback_action_type": "pass",
        "allowed_effects": frozenset({
            EffectKind.SET_RESOURCE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
        }),
        "visibility_namespaces": frozenset({"PUBLIC", "ACTOR"}),
        "selected_target_fact_namespaces": frozenset({"resource_labels"}),
        "per_window_limit": 1,
        "per_round_limit": 1,
        "is_applicable": awakened_witch_assist_pick_applicable,
        "validate": _pick_validate,
        "resolve": _resolve_pick,
    }
    if index == 1:
        # ``multi_target_actions`` cannot express a one-target action (the
        # registry requires at least two); the first assistant travels in the
        # ordinary ``target_seat`` field instead.
        return ActionContract(
            **common, actions_requiring_target=frozenset({"assist"}),
        )
    return ActionContract(
        **common, actions_requiring_target=frozenset(),
        multi_target_actions={"assist": index},
    )


AWAKENED_WITCH_PICK_1_CONTRACT = _pick_contract(1)
AWAKENED_WITCH_PICK_2_CONTRACT = _pick_contract(2)
AWAKENED_WITCH_PICK_3_CONTRACT = _pick_contract(3)


# ── One assistant's private vote ────────────────────────────────────────────


def _vote_applicable(context: ActionContext) -> bool:
    round_number = context.resources.get(ASSIST_ROUND_RESOURCE, 0)
    return (
        context.actor_alive
        and context.resources.get(ASSIST_TOKEN_RESOURCE, 0) >= 1
        and round_number == context.round_number
    )


def _vote_target(context: ActionContext) -> int:
    value = context.resources.get(ASSIST_TARGET_RESOURCE)
    if type(value) is not int or value <= 0:
        raise TypeError("assist target is missing")
    return value


def validate_awakened_witch_assist_vote(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "pass":
        return ()
    if command.target_seat != _vote_target(context):
        return (RuleViolation(
            "not_the_poison_target", "the vote target must be the poison target",
        ),)
    return ()


def _vote_reasoning_thought(command: ActionCommand, target: int) -> str:
    if command.action_type == "agree":
        return f"同意毒杀 {target} 号玩家：{command.reasoning or '无理由'}"
    if command.action_type == "veto":
        return f"反对毒杀 {target} 号玩家：{command.reasoning or '无理由'}"
    return f"未对毒杀 {target} 号表态：{command.reasoning or '无理由'}"


def resolve_awakened_witch_assist_vote(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    actor = context.actor_seat
    target = _vote_target(context)
    agree = command.action_type == "agree"
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "resource": ASSIST_TOKEN_RESOURCE, "amount": 1,
            },
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "resource": ASSIST_VOTE_RESOURCE,
                "value": 1 if agree else 0,
            },
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "AWAKENED_WITCH_ASSIST_VOTE",
                "payload": {
                    "round_number": context.round_number,
                    "seat": actor, "target_seat": target,
                    "vote": command.action_type,
                    "reasoning": command.reasoning,
                },
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _witch_reasoning(
            context, command, 4, action_type="assist", target=target,
            thought=_vote_reasoning_thought(command, target), **common,
        ),
    )


AWAKENED_WITCH_ASSIST_VOTE_CONTRACT = ActionContract(
    contract_id=_ASSIST_VOTE_ID,
    schedule_point=SchedulePoint.NIGHT_WITCH_ASSIST_VOTE,
    order=10,
    action_types=("agree", "veto", "pass"),
    actions_requiring_target=frozenset({"agree", "veto"}),
    fallback_action_type="pass",
    # The vote needs the round and the poison target as well as the token: the
    # recipient is typically NOT the witch, so its own spec does not declare
    # these runtime resources. Declaring them here makes the projector add the
    # defaults and project the canonical values for any holder.
    required_resources={
        ASSIST_TOKEN_RESOURCE: 1,
        ASSIST_ROUND_RESOURCE: 1,
        ASSIST_TARGET_RESOURCE: 1,
    },
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_RESOURCE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=_vote_applicable,
    validate=validate_awakened_witch_assist_vote,
    resolve=resolve_awakened_witch_assist_vote,
)


# ── The deterministic tally ─────────────────────────────────────────────────


def _tally_applicable(context: ActionContext) -> bool:
    plan = _plan(context)
    return (
        plan is not None
        and _plan_round(plan) == context.round_number
        and bool(_plan_assist_seats(plan))
    )


def _tally_votes(context: ActionContext) -> tuple[Mapping[str, object], ...]:
    value = context.facts.get("assist_votes") or ()
    if not isinstance(value, (list, tuple)):
        raise TypeError("assist votes must be a sequence")
    return tuple(row for row in value if isinstance(row, Mapping))


def resolve_awakened_witch_assist_tally(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    plan = _plan(context)
    if plan is None:
        return ()
    actor = context.actor_seat
    target = _plan_target(plan)
    seats = _plan_assist_seats(plan)
    votes = _tally_votes(context)
    expected = len(seats)
    agree = len(votes) == expected and all(row.get("vote") == 1 for row in votes)
    result = "passed" if agree else ("incomplete" if len(votes) != expected else "vetoed")
    effects: list[GameEffect] = []
    ordinal = 1
    if agree:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": _POISON_CAUSE},
            sort_key=(ordinal,), **common,
        ))
        ordinal += 1
        effects.append(GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": "WITCH_POISON", "payload": {
                "target_seat": target, "seat": actor,
            }},
            visibility=("PUBLIC",), sort_key=(ordinal,), **common,
        ))
        ordinal += 1
    effects.append(GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "AWAKENED_WITCH_POISON_RESULT",
            "payload": {
                "round_number": context.round_number,
                "target_seat": target, "result": result,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    ))
    ordinal += 1
    effects.append(GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.SET_PRIVATE_DATA,
        context.action_key, target_seat=actor,
        payload={"target": actor, "key": BREW_PLAN_KEY, "value": None},
        sort_key=(ordinal,), **common,
    ))
    ordinal += 1
    for seat in seats:
        for resource in (
            ASSIST_TOKEN_RESOURCE, ASSIST_TARGET_RESOURCE,
            ASSIST_COUNT_RESOURCE, ASSIST_ROUND_RESOURCE, ASSIST_VOTE_RESOURCE,
        ):
            effects.append(GameEffect(
                derive_effect_id(context.action_key, ordinal), EffectKind.SET_RESOURCE,
                context.action_key, target_seat=seat,
                payload={"target": seat, "resource": resource, "value": 0},
                sort_key=(ordinal,), **common,
            ))
            ordinal += 1
    return tuple(effects)


AWAKENED_WITCH_TALLY_CONTRACT = ActionContract(
    contract_id=_TALLY_ID,
    schedule_point=SchedulePoint.NIGHT_WITCH_ASSIST_TALLY,
    order=10,
    action_types=("resolve",),
    actions_requiring_target=frozenset(),
    fallback_action_type="resolve",
    allowed_effects=frozenset({
        EffectKind.SUBMIT_DAMAGE, EffectKind.SET_RESOURCE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=_tally_applicable,
    resolve=resolve_awakened_witch_assist_tally,
)


AWAKENED_WITCH_SPEC = RoleSpec(
    tags=frozenset({"god"}),
    role_id="wolf-killer-awakened-witch",
    display_name="Awakened Witch",
    camp_id="good",
    contracts=(
        AWAKENED_WITCH_BREW_CONTRACT,
        AWAKENED_WITCH_PICK_1_CONTRACT,
        AWAKENED_WITCH_PICK_2_CONTRACT,
        AWAKENED_WITCH_PICK_3_CONTRACT,
        AWAKENED_WITCH_TALLY_CONTRACT,
    ),
    granted_contracts=(AWAKENED_WITCH_ASSIST_VOTE_CONTRACT,),
    granted_notes={_ASSIST_VOTE_ID: _ASSIST_NOTE},
    initial_resources={
        "antidote": 1, BREW_RESOURCE: 3, BREW_USED_RESOURCE: 0,
        # The vote context only receives resources declared here (or required by
        # the contract); these five carry the assistant state set by the pick.
        ASSIST_TOKEN_RESOURCE: 0, ASSIST_TARGET_RESOURCE: 0,
        ASSIST_COUNT_RESOURCE: 0, ASSIST_ROUND_RESOURCE: 0,
        ASSIST_VOTE_RESOURCE: 0,
    },
    initial_private_data={
        WOLF_KILL_TARGET_KEY: None,
        SAVED_SEAT_KEY: None,
        BREW_PLAN_KEY: None,
        ASSIST_HISTORY_KEY: (),
    },
    projected_facts=frozenset({"assist_votes"}),
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_RESOURCE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.SUBMIT_PROTECTION,
        EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions=(
        "你是觉醒女巫（好人阵营神职）：一瓶解药、三瓶毒药，每晚只能用其中一个技能。"
        "解药只能救当晚被狼刀的目标；12 人场全程不能自救。解药与守卫的守护同时落在"
        "同一刀口上时目标仍然死亡（同守同救）。毒药无视守卫的守护。"
        "毒药调配：先选一名存活目标（不能毒自己），再按第 1 / 2 / 3 次分别选择 1 / 2 / 3 名"
        "存活、尚未协助过的玩家作为协助者；可以选自己，也可以选毒药目标。"
        "协助者会知道彼此座位与被毒目标，≥2 人时可以在夜里短暂交流；他们不知道彼此的身份，"
        "也不知道彼此的选择。所有协助者都同意时毒药才生效；任意一人反对或未表态，毒药失效。"
        "你不知道协助者的选择结果。无论是否生效，发动一次调配就消耗一次毒药次数。"
        "战术提示：把自己放进协助者可以保证第一瓶毒必开（只有你一人时由你决定）；"
        "第二、三瓶需要更多人一致，狼人协助者可以故意反对，所以不要把关键毒药完全交给不确定的人。"
    ),
)


class AwakenedWitch(Witch):
    """Legacy role object: the pipeline contracts carry the actual rules."""

