"""Contracts whose fallback action belongs to the engine alone.

Two rules are worded as an obligation — the Dancer "must" invite exactly three
seats, the Dream Weaver "must" name a sleeper, and neither may give up. The
registry forbids a fallback action that requires a target (``registry``:
"fallback action cannot require a target"), so ``pass`` has to stay the
contract's ``fallback_action_type``. That made it a first-class choice for the
model as well: a model that answered ``pass`` was recorded as a player decision
and was indistinguishable from the engine's own placeholder.

This helper is the machine check the rules were missing: a *model* answer may
not be the fallback action. The engine's placeholder still passes, and the check
must read ``command.degraded`` — never ``context.degraded``. The context is
marked one step later (``Scheduler._fallback`` applies ``_as_degraded`` after it
has validated the fallback command), so reading the context would reject the
engine's own fallback and pause the whole game with "fallback command invalid".
"""

from __future__ import annotations

from app.models.pipeline import ActionCommand, RuleViolation


def engine_only_fallback_violation(
    command: ActionCommand, *, fallback: str, code: str,
) -> tuple[RuleViolation, ...]:
    """Reject a model-submitted fallback action on a mandatory contract.

    ``fallback`` is the contract's own ``fallback_action_type`` and ``code`` the
    role-specific violation code (it travels into the ``action_rejected`` fault
    row, which is how a benchmark can count "the model tried to give up").
    """
    if command.degraded or command.action_type != fallback:
        return ()
    return (RuleViolation(
        code, "this action is mandatory and cannot be declined",
    ),)
