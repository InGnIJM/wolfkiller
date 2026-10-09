"""Wolf King: a werewolf that may drag one player along when it leaves the game.

Mirrors the hunter sample: the claw is a react-only contract on the death
response window, and every difference from the hunter travels in the declared
reason set rather than in engine code. The shared ``werewolf_kill`` contract is
reused verbatim, because the registry only merges contracts whose declarations
are identical and the scheduler aggregates all holders into one nightly vote.

Naming warning: this role is ``wolf-killer-wolf-king`` (狼王), the plain wolf
that carries one claw. The pre-existing ``wolf-killer-werewolf-king`` is 白狼王
(White Wolf King), which self-destructs during the day instead. The two ids
differ only in word order, so never abbreviate either one in code or UI copy.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf

#: Ways out that still let the Wolf King use its claw. Four are deliberately
#: absent:
#:
#: * ``self_explode`` — self-destructing never takes anyone along.
#: * ``poison`` — a poisoned Wolf King cannot use the claw.
#: * ``charm`` — a Wolf King dragged along by Wolf Beauty may not either.
#: * ``knight_duel`` — the duel is not one of the ways out that fire the claw,
#:   matching the official (NetEase) ruling that keeps it silent for the charm
#:   as well.
#:
#: A hunter the Wolf King shoots still fires back, so ``wolf_king_shot`` is a
#: valid reason for the *hunter*; it is absent here because it cannot kill the
#: Wolf King itself.
_CLAW_REASONS = frozenset({"wolf_kill", "exile", "hunter_shot"})


def wolf_king_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason in _CLAW_REASONS
        and context.resources.get("wolf_claw", 0) > 0
    )


def validate_wolf_king_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "shoot" and context.resources.get("wolf_claw", 0) <= 0:
        return (RuleViolation("wolf_claw_unavailable", "the wolf claw is unavailable"),)
    return ()


def _wolf_king_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "shoot" and command.target_seat is not None:
        thought = f"决定发动狼王爪带走 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定不发动狼王爪：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "WOLF_KING_REASONING",
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


def resolve_wolf_king_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    if command.action_type == "pass":
        return (_wolf_king_reasoning_effect(
            context, command, 1,
            expected_revision=context.revision,
            source_event_id=context.source_event_id,
        ),)
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "resource": "wolf_claw", "amount": 1},
            preconditions={"resource_equals": {"resource": "wolf_claw", "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=command.target_seat,
            payload={"target": command.target_seat, "amount": 1, "cause": "wolf_king_shot"},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={"event_type": "WOLF_KING_SHOT", "payload": {"target_seat": command.target_seat}},
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _wolf_king_reasoning_effect(context, command, 4, **common),
    )


WOLF_KING_SHOT_CONTRACT = ActionContract(
    contract_id="wolf_king_shot", schedule_point=SchedulePoint.DAWN_REACTION,
    order=40, action_types=("shoot", "pass"),
    actions_requiring_target=frozenset({"shoot"}), fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_CLAW_REASONS, per_window_limit=1, per_game_limit=1,
    is_applicable=wolf_king_applicable, validate=validate_wolf_king_action,
    resolve=resolve_wolf_king_action,
)

WOLF_KING_SPEC = RoleSpec(
    tags=frozenset({"wolf"}),
    role_id="wolf-killer-wolf-king", display_name="Wolf King", camp_id="werewolf",
    contracts=(WEREWOLF_KILL_CONTRACT, WOLF_KING_SHOT_CONTRACT),
    # The claw is a one-shot resource rather than a per-game counter: the
    # consume effect carries a ``resource_equals`` precondition, so a second
    # attempt in the same window is rejected as a conflict instead of firing.
    initial_resources={"wolf_claw": 1},
    allowed_effects=frozenset({
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    instructions=(
        "你是狼王（狼人阵营）：夜晚与狼队共用同一次狼刀，按阵营合并计票。"
        "你出局时可以发动一次「狼王爪」带走一名存活玩家（也可以不发动）。"
        "可以发动的出局方式：夜间被狼刀、白天被放逐、被猎人开枪带走。"
        "不能发动的出局方式：自爆、被女巫毒杀、被狼美人殉情带走、被骑士决斗出局。"
        "发动狼王爪不需要翻牌；被狼王爪带走的猎人**不能**开枪，被带走的狼美人也不会殉情。"
        "战术提示：狼王爪是狼队的追轮次手段，被放逐时带走一名神职通常比带走平民更有利；"
        "但没有把握时应选择 pass，避免误伤狼队友。"
    ),
)


class WolfKing(Werewolf):
    """Wolf King: a werewolf whose one-shot claw is handled by the pipeline."""

    def get_skills(self) -> list[str]:
        return ["kill", "shoot"]
