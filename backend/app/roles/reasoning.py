"""Shared EMIT_EVENT reasoning effect used by every role's ``*_REASONING`` emitters.

All 21 reasoning emitters produce the same public event shape: the actor's
seat, the submitted action_type/target, the raw reasoning, the degraded flag,
and a human-readable thought. The only per-role variation is the event_type,
the thought text, and occasional action_type/target_seat remapping for roles
whose submitted command differs from what should be reported.
"""

from __future__ import annotations

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, GameEffect

_SAME = object()


def reasoning_effect(
    context: ActionContext,
    command: ActionCommand,
    ordinal: int,
    *,
    event_type: str,
    thought: str,
    action_type: object = _SAME,
    target_seat: object = _SAME,
    **common: object,
) -> GameEffect:
    return GameEffect(
        derive_effect_id(context.action_key, ordinal), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": event_type,
            "payload": {
                "seat": context.actor_seat,
                "action_type": command.action_type if action_type is _SAME else action_type,
                "target_seat": command.target_seat if target_seat is _SAME else target_seat,
                "reasoning": command.reasoning,
                "degraded": command.degraded,
                "thought": thought,
            },
        },
        visibility=("PUBLIC",), sort_key=(ordinal,), **common,
    )
