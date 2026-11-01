"""The reflect marker protocol shared by every "bounce it back" role.

A seat declares ``reflect_check`` / ``reflect_poison`` in ``initial_resources``
to bounce the matching skill at whoever casts it (the Evil Knight does; the Time
Wolf reuses the same protocol for its lock). The *producer* builds the revenge —
the seer, the witch, the guard — because only the caster knows its own seat and
only its contract may submit damage on its own behalf.

Dedup rides on ``reflect_used``: the first skill of the night consumes it, so a
second one lands normally instead of firing a second revenge. The producer
checks the marker through ``selected_target.resource_labels`` before emitting,
and the ``resource_equals`` precondition keeps the write atomic. That fact never
reaches the model's prompt — ``selected_target`` is projected for the pure
``validate`` / ``resolve`` hooks only — so the producer learns what the rules
make observable without the prompt leaking the role.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import ActionContext, EffectKind, GameEffect

#: Declared by the bouncing seat, per skill it bounces.
REFLECT_CHECK_RESOURCE = "reflect_check"
REFLECT_POISON_RESOURCE = "reflect_poison"

#: The one-shot marker: 0 while the bounce is still armed.
REFLECT_USED_RESOURCE = "reflect_used"
#: Death cause shared by every bounce, so the audience reads one label.
REFLECT_CAUSE = "reflect"


def reflect_effects(
    context: ActionContext, selected: Mapping[str, object], marker: str, ordinal: int,
) -> tuple[GameEffect, ...]:
    """Bounce ``marker`` back at the actor, or nothing when it does not apply.

    Returns two effects — disarm the target's marker, then damage the caster —
    or an empty tuple when the target does not bounce this skill or has already
    bounced one tonight.
    """
    labels = tuple(selected.get("resource_labels") or ())
    target = selected.get("seat")
    if (
        marker not in labels
        or REFLECT_USED_RESOURCE in labels
        or type(target) is not int
        or target <= 0
    ):
        return ()
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    caster = context.actor_seat
    return (
        GameEffect(
            derive_effect_id(context.action_key, ordinal), EffectKind.SET_RESOURCE,
            context.action_key, target_seat=target,
            payload={"target": target, "resource": REFLECT_USED_RESOURCE, "value": 1},
            preconditions={
                "resource_equals": {"resource": REFLECT_USED_RESOURCE, "value": 0},
            },
            sort_key=(ordinal,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, ordinal + 1), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=caster,
            payload={"target": caster, "amount": 1, "cause": REFLECT_CAUSE},
            sort_key=(ordinal + 1,), **common,
        ),
    )
