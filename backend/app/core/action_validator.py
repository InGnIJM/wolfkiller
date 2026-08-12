from __future__ import annotations

from pydantic import ValidationError

from app.models.contracts import AcceptedAction, ActionCommand, ActionRequest
from app.models.game import GameState
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext,
    ActionContract as PipelineActionContract,
    RuleViolation,
)


class ActionValidationError(ValueError):
    """Raised when an action does not satisfy its issued contract."""


class ActionValidator:
    """The single state-changing acceptance point for role action commands."""

    def validate(
        self,
        context: ActionContext,
        contract: PipelineActionContract,
        command: PipelineActionCommand,
    ) -> tuple[RuleViolation, ...]:
        """Validate a frozen pipeline command without reading or changing game state."""
        if type(context) is not ActionContext:
            raise TypeError("context must be an exact ActionContext")
        if type(contract) is not PipelineActionContract:
            raise TypeError("contract must be an exact ActionContract")
        if type(command) is not PipelineActionCommand:
            raise TypeError("command must be an exact ActionCommand")

        violations: list[RuleViolation] = []
        if context.contract_id != contract.contract_id:
            violations.append(self._violation(
                "contract_id_mismatch", "context contract id does not match contract"
            ))
        if context.contract_version != contract.schema_version:
            violations.append(self._violation(
                "contract_version_mismatch",
                "context contract version does not match contract schema version",
            ))
        if context.schedule_point != contract.schedule_point:
            violations.append(self._violation(
                "schedule_point_mismatch", "context schedule point does not match contract"
            ))
        if context.actor_seat <= 0:
            violations.append(self._violation(
                "invalid_actor_seat", "actor seat must be a positive integer"
            ))
        if not context.actor_role_id:
            violations.append(self._violation(
                "invalid_actor_role", "actor role id must not be empty"
            ))
        if not context.actor_alive and not contract.response_event_types:
            violations.append(self._violation("actor_not_alive", "actor must be alive"))
        if context.revision < 0:
            violations.append(self._violation(
                "invalid_revision", "context revision must be non-negative"
            ))
        if context.round_number < 0:
            violations.append(self._violation(
                "invalid_round", "round number must be non-negative"
            ))
        if not context.phase:
            violations.append(self._violation("invalid_phase", "phase must not be empty"))
        if not context.action_key:
            violations.append(self._violation(
                "invalid_action_key", "action key must not be empty"
            ))

        action_allowed = command.action_type in contract.action_types
        if not action_allowed:
            violations.append(self._violation(
                "action_not_allowed", "action type is not allowed by contract"
            ))
        requires_target = command.action_type in contract.actions_requiring_target
        if requires_target and command.target_seat is None:
            violations.append(self._violation(
                "target_required", "action requires a target"
            ))
        if not requires_target and command.target_seat is not None:
            violations.append(self._violation(
                "target_forbidden", "action does not allow a target"
            ))
        if command.target_seat is not None and command.target_seat <= 0:
            violations.append(self._violation(
                "invalid_target", "target seat must be a positive integer"
            ))

        alive_seats = context.facts.get("alive_seats")
        valid_alive_seats = (
            type(alive_seats) is tuple
            and all(type(seat) is int and seat > 0 for seat in alive_seats)
        )
        if not valid_alive_seats:
            violations.append(self._violation(
                "invalid_alive_seats", "alive_seats must be a tuple of positive integers"
            ))
        elif command.target_seat is not None and command.target_seat not in alive_seats:
            violations.append(self._violation(
                "target_not_alive", "target must exist and be alive"
            ))

        self._validate_counters(context, contract, violations)
        self._validate_resources(context, contract, violations)

        if not violations and contract.validate is not None:
            hook_violations = contract.validate(context, command)
            if type(hook_violations) is not tuple or any(
                type(item) is not RuleViolation for item in hook_violations
            ):
                raise TypeError("contract validate hook must return an exact tuple of RuleViolation")
            violations.extend(hook_violations)
        return tuple(violations)

    @staticmethod
    def _violation(code: str, message: str) -> RuleViolation:
        return RuleViolation(code=code, message=message)

    @classmethod
    def _validate_counters(
        cls,
        context: ActionContext,
        contract: PipelineActionContract,
        violations: list[RuleViolation],
    ) -> None:
        expected = frozenset({"window", "round", "game"})
        if any(
            key not in expected or type(value) is not int or value < 0
            for key, value in context.counters.items()
        ):
            violations.append(cls._violation(
                "invalid_counter", "counters must be non-negative window, round, or game integers"
            ))
            return
        limits = (
            ("window", contract.per_window_limit, "window_limit_reached"),
            ("round", contract.per_round_limit, "round_limit_reached"),
            ("game", contract.per_game_limit, "game_limit_reached"),
        )
        for name, limit, code in limits:
            if limit is not None and context.counters.get(name, 0) >= limit:
                violations.append(cls._violation(
                    code, f"{name} action limit has been reached"
                ))

    @classmethod
    def _validate_resources(
        cls,
        context: ActionContext,
        contract: PipelineActionContract,
        violations: list[RuleViolation],
    ) -> None:
        for name, required in contract.required_resources.items():
            if name not in context.resources:
                violations.append(cls._violation(
                    "resource_missing", f"required resource is missing: {name}"
                ))
                continue
            available = context.resources[name]
            if type(available) is bool:
                quantity = int(available)
            elif type(available) is int and available >= 0:
                quantity = available
            else:
                violations.append(cls._violation(
                    "resource_invalid", f"resource must be a non-negative integer or boolean: {name}"
                ))
                continue
            if quantity < required:
                violations.append(cls._violation(
                    "resource_insufficient", f"required resource is insufficient: {name}"
                ))

    def validate_and_accept(
        self,
        state: GameState,
        request: ActionRequest,
        payload: dict,
    ) -> AcceptedAction:
        try:
            command = ActionCommand.model_validate(payload)
        except ValidationError as error:
            raise ActionValidationError("invalid action command") from error

        if state.phase != request.phase:
            raise ActionValidationError("request phase does not match game phase")
        if request.contract.phase != request.phase:
            raise ActionValidationError("contract phase does not match request phase")

        actor = state.players.get(request.actor_seat)
        if actor is None or not actor.is_alive:
            raise ActionValidationError("actor must exist and be alive")
        if actor.role != request.role_id:
            raise ActionValidationError("actor role does not match request")

        accepted_keys = state.accepted_action_keys
        if request.idempotency_key in accepted_keys:
            raise ActionValidationError("action already accepted")

        contract = request.contract
        if command.action_type not in contract.action_types:
            raise ActionValidationError("action type is not permitted by contract")

        if command.action_type in contract.actions_requiring_target:
            if command.target_seat is None:
                raise ActionValidationError("action requires a target")
            target = state.players.get(command.target_seat)
            if target is None:
                raise ActionValidationError("target must exist")
            if not target.is_alive:
                raise ActionValidationError("target must be alive")
        elif command.action_type in {"pass", "abstain"} and command.target_seat is not None:
            raise ActionValidationError("pass and abstain require no target")

        if command.action_type == "save":
            if command.target_seat != state.last_wolf_kill_target:
                raise ActionValidationError("save target must match wolf target")
            if not actor.has_antidote:
                raise ActionValidationError("actor has no antidote")
        elif command.action_type == "poison" and not actor.has_poison:
            raise ActionValidationError("actor has no poison")

        accepted_keys.add(request.idempotency_key)
        if command.action_type == "save":
            actor.has_antidote = False
        elif command.action_type == "poison":
            actor.has_poison = False
        return AcceptedAction(request=request, command=command)

    def safe_fallback(
        self, state: GameState, request: ActionRequest
    ) -> AcceptedAction:
        """Create fallback payload and route it through normal acceptance."""
        return self.validate_and_accept(
            state,
            request,
            {
                "action_type": request.contract.fallback_action_type,
                "target_seat": None,
                "reasoning": "safe fallback",
            },
        )
