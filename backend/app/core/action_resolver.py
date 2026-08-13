import hashlib
from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext,
    ActionContract as PipelineActionContract,
    EffectKind,
    GameEffect,
    RoleSpec,
)


class RuleExecutionError(RuntimeError):
    """Sanitized failure raised when trusted role rule execution is invalid."""

    def __init__(self, role_version: int, config_version: str, action_key: str):
        self.role_version = role_version
        self.correlation_id = hashlib.sha256(
            f"{config_version}\0{action_key}".encode("utf-8", errors="strict")
        ).hexdigest()[:16]
        self.failure_type = "Exception"
        super().__init__(
            f"rule execution failed (role_version={role_version}, "
            f"correlation_id={self.correlation_id})"
        )


class ActionResolver:
    """Calls pure role hooks and turns their results into deterministic effect batches."""

    def resolve_effects(
        self, context: ActionContext, role_spec: RoleSpec,
        contract: PipelineActionContract, command: PipelineActionCommand,
    ) -> tuple[GameEffect, ...]:
        self._validate_pipeline_inputs(context, role_spec, contract)
        if type(command) is not PipelineActionCommand:
            raise TypeError("command must be an exact ActionCommand")
        if contract.resolve is None:
            raise ValueError("contract must define a resolve hook")
        return self._execute_hook(
            context, role_spec, contract, lambda: contract.resolve(context, command)
        )

    def aggregate_effects(
        self, context: ActionContext, role_spec: RoleSpec,
        contract: PipelineActionContract, commands: tuple[PipelineActionCommand, ...],
    ) -> tuple[GameEffect, ...]:
        self._validate_pipeline_inputs(context, role_spec, contract)
        if type(commands) is not tuple:
            raise TypeError("commands must be an exact tuple")
        if any(type(command) is not PipelineActionCommand for command in commands):
            raise TypeError("commands must contain exact ActionCommand values")
        if contract.aggregate is None:
            raise ValueError("contract must define an aggregate hook")
        ordered = tuple(sorted(commands, key=lambda command: (
            command.action_type, command.target_seat is None,
            -1 if command.target_seat is None else command.target_seat,
            command.stable_digest(),
        )))
        return self._execute_hook(
            context, role_spec, contract, lambda: contract.aggregate(context, ordered)
        )

    def react_effects(
        self, context: ActionContext, role_spec: RoleSpec,
        contract: PipelineActionContract,
    ) -> tuple[GameEffect, ...]:
        self._validate_pipeline_inputs(context, role_spec, contract)
        if contract.react is None:
            raise ValueError("contract must define a react hook")
        if not contract.response_event_types or context.source_event_id is None or context.trigger_event is None:
            raise ValueError("react hook requires a bound response context")
        return self._execute_hook(
            context, role_spec, contract, lambda: contract.react(context)
        )

    @staticmethod
    def _validate_pipeline_inputs(
        context: ActionContext, role_spec: RoleSpec, contract: PipelineActionContract,
    ) -> None:
        if type(context) is not ActionContext:
            raise TypeError("context must be an exact ActionContext")
        if type(role_spec) is not RoleSpec:
            raise TypeError("role_spec must be an exact RoleSpec")
        if type(contract) is not PipelineActionContract:
            raise TypeError("contract must be an exact ActionContract")
        if not any(item is contract for item in role_spec.contracts):
            raise ValueError("contract must be the registered contract instance")
        bindings = (
            (context.contract_id == contract.contract_id, "contract id mismatch"),
            (context.contract_version == contract.schema_version, "contract version mismatch"),
            (context.contract_digest == contract.stable_digest(), "contract digest mismatch"),
            (context.schedule_point == contract.schedule_point, "schedule point mismatch"),
            (context.actor_role_id == role_spec.role_id, "actor role mismatch"),
        )
        for valid, message in bindings:
            if not valid:
                raise ValueError(message)

    def _execute_hook(self, context, role_spec, contract, call) -> tuple[GameEffect, ...]:
        try:
            return self._validate_effects(context, role_spec, contract, call())
        except Exception as error:
            public_error = RuleExecutionError(
                role_spec.schema_version, context.config_version, context.action_key
            )
            public_error.failure_type = type(error).__name__
            raise public_error from None

    @staticmethod
    def _validate_effects(context, role_spec, contract, raw) -> tuple[GameEffect, ...]:
        if type(raw) is not tuple:
            raise TypeError("hook must return an exact tuple of exact GameEffect values")
        if len(raw) > 64:
            raise ValueError("hook must return at most 64 effects")
        if any(type(effect) is not GameEffect for effect in raw):
            raise TypeError("hook must return an exact tuple of exact GameEffect values")
        allowed_effects = role_spec.allowed_effects & contract.allowed_effects
        allowed_visibility = role_spec.visibility_namespaces & contract.visibility_namespaces
        visible_targets = ActionResolver._visible_targets(context, contract)
        seen_ids: set[str] = set()
        for ordinal, effect in enumerate(raw, start=1):
            if effect.kind is EffectKind.ACCEPT_ACTION:
                raise ValueError("hook must not return ACCEPT_ACTION")
            if effect.kind not in allowed_effects:
                raise ValueError("effect kind is not permitted")
            if effect.source_action_key != context.action_key:
                raise ValueError("effect source action key mismatch")
            if effect.expected_revision != context.revision:
                raise ValueError("effect revision mismatch")
            if effect.source_event_id != context.source_event_id:
                raise ValueError("effect source event mismatch")
            if effect.target_seat is not None and (
                type(effect.target_seat) is not int or effect.target_seat <= 0
                or effect.target_seat not in visible_targets
            ):
                raise ValueError("effect target is not visible and allowed")
            if len(effect.visibility) != len(set(effect.visibility)) or not set(effect.visibility) <= allowed_visibility:
                raise ValueError("effect visibility is not permitted")
            if effect.effect_id in seen_ids:
                raise ValueError("duplicate effect id")
            seen_ids.add(effect.effect_id)
            if effect.sort_key != (ordinal,):
                raise ValueError("effect sort key is not canonical")
            if effect.effect_id != derive_effect_id(context.action_key, ordinal):
                raise ValueError("effect id is not canonical")
        accept = GameEffect(
            effect_id=derive_effect_id(context.action_key, 0), kind=EffectKind.ACCEPT_ACTION,
            source_action_key=context.action_key,
            payload={"actor_seat": context.actor_seat, "contract_id": context.contract_id,
                     "window_id": context.window_id, "round_number": context.round_number},
            expected_revision=context.revision,
            source_event_id=context.source_event_id, sort_key=(0,),
        )
        return (accept, *raw)

    @staticmethod
    def _visible_targets(
        context: ActionContext, contract: PipelineActionContract
    ) -> frozenset[int]:
        alive = context.facts.get("alive_seats", ())
        if type(alive) is not tuple or any(type(seat) is not int or seat <= 0 for seat in alive):
            raise ValueError("alive_seats must contain positive integers")
        targets = set(alive)
        if (
            contract.response_event_types
            and context.source_event_id is not None
            and context.trigger_event is not None
        ):
            targets.add(context.actor_seat)
        return frozenset(targets)
