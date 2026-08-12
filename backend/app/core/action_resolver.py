import hashlib
from typing import Optional
from app.core.action_validator import ActionValidationError
from app.core.effect_applier import derive_effect_id
from app.models.contracts import AcceptedAction
from app.models.game import GameState
from app.models.actions import NightAction, DeathReport
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
    """Resolves all night actions: wolf kill → witch save/poison → seer check → hunter check."""

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
            source_action_key=context.action_key, expected_revision=context.revision,
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

    def resolve(
        self, state: GameState, actions: list[AcceptedAction]
    ) -> list[DeathReport]:
        """Resolve night actions and return deaths. Does NOT handle hunter shoot —
        caller must check for hunter death and prompt the hunter separately."""
        self._validate_witch_actions(state, actions)
        resolved_actions = [self._as_night_action(action) for action in actions]
        wolf_actions = [a for a in resolved_actions if a.action_type == "kill"]
        witch_actions = [a for a in resolved_actions if a.action_type in ("save", "poison")]
        seer_actions = [a for a in resolved_actions if a.action_type == "check"]

        # 1. Resolve wolf kill target (majority vote)
        wolf_target = self._resolve_wolf_kill(wolf_actions)
        state.last_wolf_kill_target = wolf_target

        # 2. Process witch actions (one potion per night enforced by engine)
        saved = self._process_witch_save(wolf_target, witch_actions)
        poisoned_target = self._process_witch_poison(witch_actions)

        # 3. Process seer checks
        self._process_seer_checks(state, seer_actions)

        # 4. Mark deaths
        deaths: list[DeathReport] = []

        if wolf_target is not None and not saved:
            player = state.players.get(wolf_target)
            if player and player.is_alive:
                player.mark_dead("wolf_kill")
                deaths.append(DeathReport(
                    player_seat=wolf_target, cause="wolf_kill",
                    round_number=state.round_number,
                ))

        if poisoned_target is not None:
            player = state.players.get(poisoned_target)
            if player and player.is_alive:
                player.mark_dead("poison")
                deaths.append(DeathReport(
                    player_seat=poisoned_target, cause="poison",
                    round_number=state.round_number,
                ))

        return deaths

    def has_hunter_died(self, state: GameState, deaths: list[DeathReport]) -> Optional[int]:
        """Check if a hunter with a gun died (not from poison).
        Returns the hunter's seat number, or None."""
        for death in deaths:
            if death.cause == "poison":
                continue
            player = state.players.get(death.player_seat)
            if player and "hunter" in player.role and player.has_gun:
                return death.player_seat
        return None

    def resolve_hunter_shoot(
        self, state: GameState, hunter_seat: int, action: AcceptedAction
    ) -> Optional[DeathReport]:
        """Resolve hunter's shot. Returns DeathReport on success, None if invalid.
        Does NOT consume the gun if the target is invalid (dead or missing)."""
        if not isinstance(action, AcceptedAction):
            raise TypeError("resolver requires AcceptedAction")
        hunter = state.players.get(hunter_seat)
        if not hunter or not hunter.has_gun:
            return None

        if action.request.actor_seat != hunter_seat:
            return None
        if action.command.action_type != "shoot":
            return None
        target_seat = action.command.target_seat
        if target_seat is None:
            return None

        target = state.players.get(target_seat)
        if not target or not target.is_alive:
            return None  # don't consume gun for invalid target

        hunter.has_gun = False
        target.mark_dead("hunter_shot")
        return DeathReport(
            player_seat=target_seat, cause="hunter_shot",
            round_number=state.round_number,
        )

    # ── Private helpers ───────────────────────────────────────────

    def _validate_witch_actions(
        self, state: GameState, actions: list[AcceptedAction]
    ) -> None:
        """Reject multiple actions from one witch in the same round."""
        witch_action_keys: set[tuple[int, int]] = set()
        for action in actions:
            if not isinstance(action, AcceptedAction):
                continue
            actor = state.players.get(action.request.actor_seat)
            if (
                action.request.contract.contract_id != "witch_action"
                or action.request.role_id != "wolf-killer-witch"
                or actor is None
                or actor.role != "wolf-killer-witch"
            ):
                continue
            if action.command.action_type not in {"save", "poison", "pass"}:
                continue
            key = (action.request.actor_seat, action.request.round_id)
            if key in witch_action_keys:
                raise ActionValidationError(
                    "multiple witch actions for one actor in the same round"
                )
            witch_action_keys.add(key)

    def _as_night_action(self, action: AcceptedAction) -> NightAction:
        if isinstance(action, AcceptedAction):
            return NightAction(
                player_seat=action.request.actor_seat,
                action_type=action.command.action_type,
                target_seat=action.command.target_seat,
                reasoning=action.command.reasoning,
            )
        raise TypeError("resolver requires AcceptedAction")

    def _resolve_wolf_kill(self, actions: list[NightAction]) -> Optional[int]:
        if not actions:
            return None
        targets = [
            a.target_seat
            for a in actions
            if isinstance(a.target_seat, int) and not isinstance(a.target_seat, bool)
            and a.target_seat > 0
        ]
        if not targets:
            return None

        vote_counts: dict[int, int] = {}
        for t in targets:
            vote_counts[t] = vote_counts.get(t, 0) + 1

        max_votes = max(vote_counts.values())
        top = [t for t, c in vote_counts.items() if c == max_votes]
        return min(top)

    def _process_witch_save(
        self,
        wolf_target: Optional[int],
        actions: list[NightAction],
    ) -> bool:
        for action in actions:
            if action.action_type != "save":
                continue
            if action.target_seat is not None and action.target_seat == wolf_target:
                return True
        return False

    def _process_witch_poison(
        self,
        actions: list[NightAction],
    ) -> Optional[int]:
        for action in actions:
            if action.action_type != "poison":
                continue
            if action.target_seat is None or action.target_seat == 0:
                return None
            return action.target_seat
        return None

    def _process_seer_checks(
        self, state: GameState, actions: list[NightAction]
    ) -> None:
        for action in actions:
            if action.action_type != "check":
                continue
            if action.target_seat is None:
                continue
            target = state.players.get(action.target_seat)
            if target is None or not target.is_alive:
                continue
            result = "werewolf" if "werewolf" in target.role else "good"
            seer = state.players.get(action.player_seat)
            if seer:
                seer.check_results.append({
                    "target_seat": action.target_seat,
                    "result": result,
                    "round": state.round_number,
                })
