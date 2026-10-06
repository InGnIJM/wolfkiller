"""One night's target memory, shared by every "not two nights running" role.

Guard, Nightmare, Mask, Wolf Beauty and Dream Weaver all forbid the same
target on two *consecutive* nights, and each used to implement the check the
same way: compare tonight's choice against the bare seat they last resolved.
That is wrong the moment a night is skipped — a model failure, a deliberate
``pass``, or the seat being silenced all leave the record untouched, so the
next real choice is compared against a night that was not last night:

* the forbidding roles rejected a legal choice, then rejected the blind retry
  too and degraded to their fallback (``pass``), losing a second night;
* Dream Weaver turned the mistake lethal: a repeat two nights apart read as
  "two nights running" and submitted ``dream_death`` damage instead of the
  shield, killing the very seat it meant to protect.

The fix is to remember *when* as well as *who*. The value written here is
``{"seat": seat, "round": round_number}``; ``is_consecutive_repeat`` only
answers True when the round is exactly ``round_number - 1``.

Archives written before this change hold a bare ``int``. ``last_target`` reads
both shapes, and ``is_consecutive_repeat`` treats a value with no round as
consecutive — the historical reading — so a resumed old game keeps the old
behaviour instead of silently changing its mind mid-night. The helpers are
pure functions over ``ActionContext`` with no state access, like every other
role hook.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.models.pipeline import ActionContext


def last_target(context: ActionContext, key: str) -> int | None:
    """The seat this role last acted on, from either storage shape."""
    value = context.facts.get(key)
    if type(value) is int:
        return value
    if isinstance(value, Mapping):
        seat = value.get("seat")
        if type(seat) is int:
            return seat
    return None


def is_consecutive_repeat(context: ActionContext, key: str, target: int) -> bool:
    """True when ``target`` was also this role's choice on the immediately
    preceding night.

    A record without a round number is an archive from before the round was
    stored; it can only be read the old way, which is what it was written for.
    """
    if last_target(context, key) != target:
        return False
    value = context.facts.get(key)
    if not isinstance(value, Mapping):
        return True
    return value.get("round") == context.round_number - 1


def target_memory(
    actor: int, key: str, target: int, round_number: int,
) -> dict[str, object]:
    """The ``SET_PRIVATE_DATA`` payload recording tonight's choice.

    ``actor`` owns the private slot; ``target`` is who the role acted on. A role
    that passes writes nothing, which is exactly what makes the skipped night
    invisible to the check above.
    """
    return {
        "target": actor,
        "key": key,
        "value": {"seat": target, "round": round_number},
    }
