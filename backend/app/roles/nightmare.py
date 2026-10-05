"""Nightmare: the wolf that acts first and seals one seat — or the whole pack.

Its contract sits at ``NIGHT_ACTION`` with ``order=10``, ahead of the Guard's
50, which is the whole point: the scheduler runs one point's members in order,
so the status the Nightmare writes is already visible when the roles after it
are asked. Fearing a *werewolf* is not a refusal — the rules make it seal the
entire camp for that night, which the engine reads off the same durable status.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.core.night_settlement import SILENCED_STATUS
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf

FEARED_KEY = "last_feared"


def nightmare_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_nightmare_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "fear" or command.target_seat is None:
        return ()
    if command.target_seat == context.facts.get(FEARED_KEY):
        return (RuleViolation(
            "consecutive_fear_forbidden",
            "the same seat cannot be feared on two consecutive nights",
        ),)
    return ()


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.action_type == "fear" and command.target_seat is not None:
        thought = (
            f"决定恐惧 {command.target_seat} 号玩家，使其当夜无法行动："
            f"{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不恐惧任何人：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "NIGHTMARE_REASONING",
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


def resolve_nightmare_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "fear" or command.target_seat is None:
        return (_reasoning_effect(context, command, 1, **common),)
    target = command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "key": FEARED_KEY, "value": target},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.ADD_STATUS,
            context.action_key, target_seat=target,
            payload={"target": target, "status": SILENCED_STATUS},
            preconditions={
                "status_present": {"status": SILENCED_STATUS, "present": False},
            },
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "STATUS_ADDED",
                "payload": {
                    "seat": target, "status": SILENCED_STATUS,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _reasoning_effect(context, command, 4, **common),
    )


NIGHTMARE_FEAR_CONTRACT = ActionContract(
    contract_id="nightmare_fear",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    # Before every other night action: the Guard sits at 50, the Dancer at 20.
    order=10,
    action_types=("fear", "pass"),
    actions_requiring_target=frozenset({"fear"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=nightmare_applicable,
    validate=validate_nightmare_action,
    resolve=resolve_nightmare_action,
)

NIGHTMARE_SPEC = RoleSpec(
    tags=frozenset({"wolf"}),
    role_id="wolf-killer-nightmare",
    display_name="Nightmare",
    camp_id="werewolf",
    # 它排在所有夜间角色之前，所以这一句也是当晚的第一句开眼旁白。
    opening_narration=("噩梦之影请睁眼", "请选择今晚要恐惧的玩家。"),
    contracts=(WEREWOLF_KILL_CONTRACT, NIGHTMARE_FEAR_CONTRACT),
    initial_private_data={FEARED_KEY: None},
    # The role-level set is the union of what its own contract needs and what
    # the shared kill contract needs (SUBMIT_DAMAGE / EMIT_EVENT).
    allowed_effects=frozenset({
        EffectKind.SUBMIT_DAMAGE, EffectKind.SET_PRIVATE_DATA,
        EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    instructions=(
        "Nightmare is a werewolf: at night it joins the wolf channel, the shared discussion "
        "and the shared kill vote like any other werewolf. It also acts BEFORE every other "
        "night role and fears one living player, who then cannot use its skill for the rest "
        "of that night — a feared guard cannot protect, a feared seer cannot check, a feared "
        "witch cannot use a potion. The same seat cannot be feared on two consecutive nights. "
        "Fearing a WEREWOLF is allowed and seals the entire wolf camp for that night: the "
        "pack cannot attack at all. That is usually a serious accident, so check the roster "
        "before choosing, but it is also the reason you may fear yourself or a teammate "
        "deliberately if the board ever calls for it. What the good camp sees is a peaceful "
        "night, so a failed fear is expensive: pick the seat whose skill hurts the pack most "
        "if it fires."
    ),
)


class Nightmare(Werewolf):
    """Nightmare: a werewolf whose nightly fear is a pipeline contract."""

    def get_skills(self) -> list[str]:
        return ["kill", "fear"]
