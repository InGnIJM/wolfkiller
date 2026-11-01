"""Wolf Witch: a werewolf that checks identities outside its own camp.

It sits in the pack like any other werewolf — same byte-identical kill
contract, same discussion, same vote — and adds one nightly check. The check may
not name a teammate (it knows who they are) and from the second night on, if it
names the Pure White, the Pure White dies that same night.

The answer is recorded in the ``role_checks`` namespace so the actor's own
prompt carries it; the target's exact role travels through ``selected_target``,
which never reaches the model.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.roles.reasoning import reasoning_effect
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, ActionContract, EffectKind,
    GameEffect, RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.pure_white import ROLE_CHECK_NAMESPACE
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf
from app.roles.wolf_claw import (
    AWAKENED_CLAW_GIFT_CONTRACT, AWAKENED_CLAW_GIFT_KEY,
)

#: The one identity the Wolf Witch can sentence, and the cause it dies of.
PURE_WHITE_ROLE_ID = "wolf-killer-pure-white"
_VERIFY_CAUSE = "verify_kill"
#: The sentence starts on the second night, exactly like the Pure White's.
_FIRST_KILL_ROUND = 2


def wolf_witch_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_wolf_witch_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "check" or command.target_seat is None:
        return ()
    if command.target_seat == context.actor_seat:
        return (RuleViolation(
            "self_check_forbidden", "the Wolf Witch cannot check itself",
        ),)
    members = context.facts.get("camp_members")
    if isinstance(members, (list, tuple)) and command.target_seat in members:
        return (RuleViolation(
            "teammate_check_forbidden",
            "the Wolf Witch can only check players outside the werewolf camp",
        ),)
    return ()


def _reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, *,
    kills: bool, **common: object,
) -> GameEffect:
    if command.degraded:
        thought = DEGRADED_THOUGHT
    elif command.action_type == "check" and command.target_seat is not None:
        verdict = "，正是纯白之女，当夜处决" if kills else ""
        thought = (
            f"决定查验 {command.target_seat} 号玩家{verdict}："
            f"{command.reasoning or '无理由'}"
        )
    else:
        thought = f"决定今晚不查验：{command.reasoning or '无理由'}"
    return reasoning_effect(
        context, command, ordinal, event_type="WOLF_WITCH_REASONING",
        thought=thought, **common,
    )


def resolve_wolf_witch_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type != "check" or command.target_seat is None:
        return (_reasoning_effect(context, command, 1, kills=False, **common),)
    selected = context.facts.get("selected_target")
    if not isinstance(selected, Mapping) or selected.get("seat") != command.target_seat:
        raise ValueError("selected target fact is missing or mismatched")
    identity = selected.get("role_label")
    if type(identity) is not str:
        raise TypeError("selected target role label must be a string")
    kills = identity == PURE_WHITE_ROLE_ID and context.round_number >= _FIRST_KILL_ROUND
    actor, target = context.actor_seat, command.target_seat
    effects = [
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.RECORD_PRIVATE_FACT,
            context.action_key, target_seat=actor,
            payload={
                "target": actor, "namespace": ROLE_CHECK_NAMESPACE,
                "fact": {"target": target, "role": identity},
            },
            visibility=("ACTOR",), sort_key=(1,), **common,
        ),
    ]
    if kills:
        effects.append(GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": _VERIFY_CAUSE},
            sort_key=(2,), **common,
        ))
    effects.append(_reasoning_effect(
        context, command, len(effects) + 1, kills=kills, **common,
    ))
    return tuple(effects)


WOLF_WITCH_CHECK_CONTRACT = ActionContract(
    contract_id="wolf_witch_check",
    schedule_point=SchedulePoint.NIGHT_SEER_ACTION,
    # After the Seer (30) and the Gargoyle's lone check (35), before the witch's
    # potions: the order only has to stay stable, the roles never share a board.
    order=40,
    action_types=("check", "pass"),
    actions_requiring_target=frozenset({"check"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    # CAMP carries the teammate roster, which is what makes the "not a teammate"
    # rule enforceable — and the Wolf Witch is told who its teammates are anyway.
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    selected_target_fact_namespaces=frozenset({"role_label"}),
    per_window_limit=1,
    per_round_limit=1,
    is_applicable=wolf_witch_applicable,
    validate=validate_wolf_witch_action,
    resolve=resolve_wolf_witch_action,
)

WOLF_WITCH_SPEC = RoleSpec(
    tags=frozenset({"wolf"}),
    role_id="wolf-killer-wolf-witch",
    display_name="Wolf Witch",
    camp_id="werewolf",
    opening_narration=("狼巫请睁眼", "请查验一名非狼阵营玩家的具体身份；第二夜起验中纯白之女即当夜处决。"),
    contracts=(WEREWOLF_KILL_CONTRACT, WOLF_WITCH_CHECK_CONTRACT, AWAKENED_CLAW_GIFT_CONTRACT),
    initial_private_data={
        ROLE_CHECK_NAMESPACE: (), AWAKENED_CLAW_GIFT_KEY: None,
    },
    allowed_effects=frozenset({
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.SET_PRIVATE_DATA,
        EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    instructions=(
        "Wolf Witch is a werewolf: at night it joins the wolf channel, the shared discussion "
        "and the shared kill vote like any other werewolf. It also checks one living player "
        "who is NOT in the werewolf camp and learns that player's EXACT identity, privately. "
        "From the second night on, if the checked player is the Pure White, that player dies "
        "the same night automatically. The check is for the pack: a night you spend naming a "
        "villager is a night you could have spent finding the Pure White, the Seer or the "
        "Witch, so weigh it against the kill plan. Never check a teammate — the judge rejects "
        "it and the night's check is lost. The identity answer is yours alone; the pack learns "
        "it only if you say so in the wolf channel or in the daytime speeches."
    ),
)


class WolfWitch(Werewolf):
    """Wolf Witch: a werewolf whose nightly identity check is a pipeline contract."""

