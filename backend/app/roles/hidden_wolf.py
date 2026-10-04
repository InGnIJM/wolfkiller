"""The lone kill a hidden wolf earns once its pack is gone.

A role declaring ``no_wolf_pack`` does not declare the shared
``werewolf_kill`` contract, so it never joins the nightly vote. It declares this
one instead: same schedule point, later order, and an applicability rule that
only opens when no pack member the hidden wolf can see is left alive.

Both hooks are marker-driven and role-agnostic, so every hidden-wolf role shares
this declaration byte-identically (the registry only merges contracts whose
declarations match). The vote aggregates, so two hidden wolves alive at the end
of the game still cast one kill between them rather than two.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)


def hidden_wolf_applicable(context: ActionContext) -> bool:
    """Open only once the pack is gone.

    ``wolf_pack_count`` is projected for a hidden wolf as the number of *living*
    pack members. A missing fact means the actor is not hidden at all, so the
    contract stays shut — the gate lives in the projector, where pack membership
    is already decided, instead of being re-derived here.
    """
    if not context.actor_alive or "wolf_pack_count" not in context.facts:
        return False
    return context.facts["wolf_pack_count"] == 0


def validate_hidden_wolf_kill(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "kill" and command.target_seat == context.actor_seat:
        return (RuleViolation(
            "self_kill_forbidden", "a hidden wolf may not cut itself",
        ),)
    return ()


def resolve_hidden_wolf_kill(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    if command.action_type == "pass":
        return ()
    target = command.target_seat
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": "wolf_kill"},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "WEREWOLF_KILL",
                "payload": {
                    "target_seat": target,
                    "vote_counts": {str(target): 1},
                },
            },
            visibility=("PUBLIC",), sort_key=(2,), **common,
        ),
    )


def hidden_wolf_kill_effects(
    context: ActionContext, commands: tuple[ActionCommand, ...],
) -> tuple[GameEffect, ...]:
    """Aggregate the hidden wolves' ballots into one kill, wolf-kill style."""
    counts: dict[int, int] = {}
    for command in commands:
        if command.action_type == "kill" and command.target_seat is not None:
            counts[command.target_seat] = counts.get(command.target_seat, 0) + 1
    if not counts:
        return ()
    target = min(counts, key=lambda seat: (-counts[seat], seat))
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=target,
            payload={"target": target, "amount": 1, "cause": "wolf_kill"},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.EMIT_EVENT,
            context.action_key,
            payload={
                "event_type": "WEREWOLF_KILL",
                "payload": {
                    "target_seat": target,
                    "vote_counts": {str(seat): count for seat, count in counts.items()},
                },
            },
            visibility=("PUBLIC",), sort_key=(2,), **common,
        ),
    )


HIDDEN_WOLF_KILL_CONTRACT = ActionContract(
    contract_id="hidden_wolf_kill", schedule_point=SchedulePoint.NIGHT_WOLF_VOTE,
    # After the pack's own contract: the two never coexist (this one only opens
    # when the pack is empty), but a stable order keeps the journal readable.
    order=60, action_types=("kill", "pass"),
    actions_requiring_target=frozenset({"kill"}), fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    is_applicable=hidden_wolf_applicable, validate=validate_hidden_wolf_kill,
    aggregate=hidden_wolf_kill_effects,
)


def hidden_wolf_role_spec(
    role_id: str, display_name: str, *, instructions: str,
    extra_resources: Mapping[str, int] | None = None,
    extra_contracts: tuple[ActionContract, ...] = (),
    extra_effects: frozenset[EffectKind] = frozenset(),
    initial_private_data: Mapping[str, object] | None = None,
) -> RoleSpec:
    """Build a hidden-wolf ``RoleSpec`` around the shared lone-kill contract.

    The extras carry whatever else the concrete role declares — the Gargoyle
    adds its identity check, the Eclipse Maid adds ``wolf_knows_pack`` for the
    one-way variant.
    """
    effects = (
        frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT})
        | extra_effects
    )
    return RoleSpec(
        tags=frozenset({"wolf"}), role_id=role_id, display_name=display_name,
        camp_id="werewolf",
        contracts=(HIDDEN_WOLF_KILL_CONTRACT, *extra_contracts),
        initial_resources={
            "no_wolf_pack": 1, **dict(extra_resources or {}),
        },
        initial_private_data=dict(initial_private_data or {}),
        allowed_effects=effects,
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
        max_count=1, instructions=instructions,
    )
