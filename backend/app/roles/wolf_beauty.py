"""Wolf Beauty: a werewolf that charms one good player each night and drags the
charmed player along when it leaves the game.

Mirrors the other extension samples: a frozen declaration plus pure hooks. It
reuses the shared ``werewolf_kill`` contract verbatim (the registry only merges
contracts whose declarations are identical) and adds a separate charm contract
on the same night stage the witch acts in, i.e. after the wolf kill vote.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, Werewolf

# Private relation name recorded on the charmed seat.
_CHARM_RELATION = "charmed_by"
# The charm drags the charmed player along on every way out of the game except
# the two the rules forbid Wolf Beauty from using at all: self-destruct and
# self-kill. ``self_explode`` is therefore deliberately absent.
_REVENGE_REASONS = frozenset(
    {"wolf_kill", "poison", "exile", "hunter_shot", "knight_duel"}
)
_WOLF_CAMP = "werewolf"


def wolf_beauty_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_wolf_beauty_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type != "charm":
        return ()
    if command.target_seat is None:
        return (RuleViolation("charm_target_required", "the charm needs a target"),)
    if command.target_seat == context.actor_seat:
        return (RuleViolation("self_target", "Wolf Beauty cannot charm itself"),)
    if context.facts.get("last_charmed") == command.target_seat:
        return (RuleViolation(
            "consecutive_charm",
            "the same player cannot be charmed two nights in a row",
        ),)
    selected = context.facts.get("selected_target")
    if isinstance(selected, Mapping):
        if selected.get("camp_label") == _WOLF_CAMP:
            return (RuleViolation("charm_wolf", "only good-camp players can be charmed"),)
        if "charm_immune" in tuple(selected.get("resource_labels") or ()):
            return (RuleViolation("charm_immune", "the target is immune to the charm"),)
    return ()


def _charm_reasoning_effect(
    context: ActionContext, command: ActionCommand, ordinal: int, **common: object,
) -> GameEffect:
    if command.action_type == "charm" and command.target_seat is not None:
        thought = f"决定魅惑 {command.target_seat} 号玩家：{command.reasoning or '无理由'}"
    else:
        thought = f"决定本晚不魅惑：{command.reasoning or '无理由'}"
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "WOLF_BEAUTY_REASONING",
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


def resolve_wolf_beauty_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    if command.action_type == "pass":
        return (_charm_reasoning_effect(context, command, 1, **common),)
    actor, target = context.actor_seat, command.target_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SET_PRIVATE_DATA,
            context.action_key, target_seat=actor,
            payload={"target": actor, "key": "last_charmed", "value": target},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.ADD_RELATION,
            context.action_key, target_seat=target,
            payload={"target": target, "relation": _CHARM_RELATION, "other_seat": actor},
            sort_key=(2,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "WOLF_BEAUTY_CHARM",
                # The charmed seat stays private: the public log only records
                # that a charm happened this night.
                "payload": {"seat": actor, "round_number": context.round_number},
            },
            visibility=("PUBLIC",), sort_key=(3,), **common,
        ),
        _charm_reasoning_effect(context, command, 4, **common),
    )


def wolf_beauty_revenge_applicable(context: ActionContext) -> bool:
    """True when this seat is Wolf Beauty and it charmed somebody this night."""
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason in _REVENGE_REASONS
        and not context.actor_alive
        and context.facts.get("last_charmed") is not None
    )


def react_wolf_beauty_revenge(context: ActionContext) -> tuple[GameEffect, ...]:
    """Drag the charmed player along. Pure and deterministic — no model call.

    Response windows do not consult ``is_applicable`` before reacting, so the
    hook re-checks it and stays silent for every way out the rules exclude.
    """
    if not wolf_beauty_revenge_applicable(context):
        return ()
    charmed = context.facts.get("last_charmed")
    if type(charmed) is not int or charmed <= 0:
        return ()
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=charmed,
            payload={"target": charmed, "amount": 1, "cause": "charm"},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "WOLF_BEAUTY_REVENGE",
                "payload": {
                    "seat": context.actor_seat,
                    "target_seat": charmed,
                    "cause": context.trigger_reason,
                    "round_number": context.round_number,
                },
            },
            visibility=("PUBLIC",), sort_key=(2,), **common,
        ),
    )


WOLF_BEAUTY_CHARM_CONTRACT = ActionContract(
    contract_id="wolf_beauty_charm",
    # The witch stage runs after the wolf kill vote, so the charm lands "after
    # taking part in the kill" and still settles with the same night commit.
    schedule_point=SchedulePoint.NIGHT_WITCH_ACTION,
    order=40,
    action_types=("charm", "pass"),
    actions_requiring_target=frozenset({"charm"}),
    fallback_action_type="pass",
    allowed_effects=frozenset(
        {EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_RELATION, EffectKind.EMIT_EVENT}
    ),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    selected_target_fact_namespaces=frozenset({"camp_label", "resource_labels"}),
    per_window_limit=1,
    is_applicable=wolf_beauty_applicable,
    validate=validate_wolf_beauty_action,
    resolve=resolve_wolf_beauty_action,
)

WOLF_BEAUTY_REVENGE_CONTRACT = ActionContract(
    contract_id="wolf_beauty_revenge",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=40,
    action_types=("revenge",),
    actions_requiring_target=frozenset(),
    fallback_action_type="revenge",
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=_REVENGE_REASONS,
    per_window_limit=1,
    is_applicable=wolf_beauty_revenge_applicable,
    react=react_wolf_beauty_revenge,
)

WOLF_BEAUTY_SPEC = RoleSpec(
    role_id="wolf-killer-wolf-beauty", display_name="Wolf Beauty", camp_id="werewolf",
    contracts=(
        WEREWOLF_KILL_CONTRACT,
        WOLF_BEAUTY_CHARM_CONTRACT,
        WOLF_BEAUTY_REVENGE_CONTRACT,
    ),
    initial_private_data={"last_charmed": None},
    # Wolf Beauty may never be the wolf team's own kill target. The kill
    # contract is shared byte-identically with the whole camp, so the rule
    # travels as a declared resource and ``validate_werewolf_action`` rejects
    # the self-vote for whoever declares it.
    initial_resources={"self_kill_forbidden": 1},
    allowed_effects=frozenset(
        {EffectKind.SUBMIT_DAMAGE, EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_RELATION,
         EffectKind.EMIT_EVENT}
    ),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    tags=frozenset({"wolf"}),
    max_count=1,
    instructions=(
        "Wolf Beauty is a werewolf: at night it joins the wolf channel and the shared kill "
        "vote like any other werewolf. After taking part in the kill it may separately charm "
        "one living good-camp player; the same player cannot be charmed two nights in a row, "
        "and a player who is immune to the charm cannot be charmed at all. When Wolf Beauty "
        "leaves the game, the player charmed that night is dragged along and dies too. Wolf "
        "Beauty can never self-destruct and can never be the wolf team's own kill target; "
        "every other way out of the game still takes the charmed player along. Weigh every "
        "charm against win probability: charming a suspected god forces the good camp to spend "
        "a day on Wolf Beauty or lose that god, while charming a plain villager is nearly "
        "worthless. Remember that the charm only pays off when Wolf Beauty actually leaves, so "
        "a charm placed on a god also commits Wolf Beauty to looking exilable later. Keep the "
        "charm target hidden from the public log, and agree with the team how the next night "
        "and day are played before choosing the moment to be exiled. Charming is optional; "
        "judge each night by its expected value for the wolf team and pass whenever staying "
        "hidden serves the wolf team better."
    ),
)


class WolfBeauty(Werewolf):
    """Wolf Beauty: a werewolf whose charm and revenge are handled by the
    pipeline contracts; the daytime instance speaks and votes like a werewolf."""

    def get_skills(self) -> list[str]:
        return ["kill", "charm"]
