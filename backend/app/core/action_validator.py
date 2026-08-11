from __future__ import annotations

from pydantic import ValidationError

from app.models.contracts import AcceptedAction, ActionCommand, ActionRequest
from app.models.game import GameState


class ActionValidationError(ValueError):
    """Raised when an action does not satisfy its issued contract."""


class ActionValidator:
    """The single state-changing acceptance point for role action commands."""

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
