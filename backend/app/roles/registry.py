from __future__ import annotations

import hashlib
import inspect
import random
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import get_type_hints

from app.models.contracts import ActionContract, ActionRequest, RoleSpec as LegacyRoleSpec
from app.models.game import Camp, GamePhase, GameState
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext as PipelineActionContext,
    ActionContract as PipelineActionContract,
    GameEffect,
    RoleSpec as PipelineRoleSpec,
    RuleViolation,
)
from app.roles.hunter import HUNTER_SPEC, Hunter
from app.roles.guard import GUARD_SPEC, Guard
from app.roles.seer import SEER_SPEC, Seer
from app.roles.villager import VILLAGER_SPEC, Villager
from app.roles.werewolf import WEREWOLF_SPEC, Werewolf
from app.roles.witch import WITCH_SPEC, Witch


_VISIBLE_NAMESPACES = frozenset({"PUBLIC", "ACTOR", "CAMP", "RELATION"})
_MAX_ORDER = 1_000_000
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")


@dataclass(frozen=True)
class RegistrySnapshot:
    specs: Mapping[str, PipelineRoleSpec]
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "specs",
            MappingProxyType(dict(sorted(self.specs.items()))),
        )

    def require(self, role_id: str) -> PipelineRoleSpec:
        try:
            return self.specs[role_id]
        except KeyError as error:
            raise ValueError(f"unknown role: {role_id}") from error

    def validate_role_counts(
        self, role_counts: Mapping[str, int], player_count: int
    ) -> None:
        if isinstance(player_count, bool) or not isinstance(player_count, int) or player_count < 0:
            raise ValueError("player_count must be a non-negative integer")
        counts = {role_id: 0 for role_id in self.specs}
        total = 0
        for role_id, count in role_counts.items():
            self.require(role_id)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("role count must be a non-negative integer")
            counts[role_id] = count
            total += count
        if total != player_count:
            raise ValueError("role count sum must equal player_count")
        for role_id, spec in self.specs.items():
            count = counts[role_id]
            if count < spec.min_count:
                raise ValueError(f"role minimum not satisfied: {role_id}")
            if spec.max_count is not None and count > spec.max_count:
                raise ValueError(f"role maximum exceeded: {role_id}")
            if count == 0:
                continue
            missing = sorted(
                dependency
                for dependency in spec.dependencies
                if counts[dependency] == 0
            )
            if missing:
                raise ValueError(
                    f"role dependency not satisfied: {role_id} requires {missing[0]}"
                )
            conflicts = sorted(
                exclusion for exclusion in spec.exclusions if counts[exclusion] > 0
            )
            if conflicts:
                raise ValueError(
                    f"role exclusion violated: {role_id} excludes {conflicts[0]}"
                )


class RoleRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, LegacyRoleSpec] = {}
        self._pipeline_specs: dict[str, PipelineRoleSpec] = {}
        self._lock = RLock()

    def register(self, spec: LegacyRoleSpec) -> None:
        if type(spec) is not LegacyRoleSpec:
            raise TypeError(
                "legacy register requires app.models.contracts.RoleSpec"
            )
        with self._lock:
            if spec.role_id in self._specs:
                raise ValueError(f"role already registered: {spec.role_id}")
            self._specs[spec.role_id] = spec

    def register_pipeline(self, spec: PipelineRoleSpec) -> None:
        if type(spec) is not PipelineRoleSpec:
            raise TypeError(
                "pipeline register requires app.models.pipeline.RoleSpec"
            )
        with self._lock:
            if spec.role_id in self._pipeline_specs:
                raise ValueError(f"role already registered: {spec.role_id}")
            self._pipeline_specs[spec.role_id] = spec

    def freeze(self) -> RegistrySnapshot:
        with self._lock:
            local_specs = dict(self._pipeline_specs)
        contract_ids: set[str] = set()
        known_roles = set(local_specs)
        for spec in dict(sorted(local_specs.items())).values():
            self._validate_pipeline_spec(spec, contract_ids, known_roles)
        specs = MappingProxyType(dict(sorted(local_specs.items())))
        digest_source = "[" + ",".join(
            PipelineRoleSpec.to_json(spec) for spec in specs.values()
        ) + "]"
        digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
        return RegistrySnapshot(specs=specs, digest=digest)

    @staticmethod
    def _validate_pipeline_spec(
        spec: PipelineRoleSpec,
        contract_ids: set[str],
        known_roles: set[str],
    ) -> None:
        if _STABLE_ID.fullmatch(spec.role_id) is None:
            raise ValueError("role id has an invalid stable format")
        if spec.min_count < 0:
            raise ValueError("role minimum must be non-negative")
        if spec.max_count is not None and spec.max_count < spec.min_count:
            raise ValueError("role maximum must be at least its minimum")
        if spec.dependencies & spec.exclusions:
            raise ValueError("role dependency and exclusion cannot overlap")
        if spec.role_id in spec.dependencies or spec.role_id in spec.exclusions:
            raise ValueError("role cannot depend on or exclude itself")
        unknown_constraints = (spec.dependencies | spec.exclusions) - known_roles
        if unknown_constraints:
            raise ValueError(
                f"unknown role dependency or exclusion: {min(unknown_constraints)}"
            )
        if not spec.visibility_namespaces <= _VISIBLE_NAMESPACES:
            raise ValueError("unknown visibility namespace")
        for contract in spec.contracts:
            if contract.contract_id in contract_ids:
                raise ValueError(f"duplicate contract id: {contract.contract_id}")
            contract_ids.add(contract.contract_id)
            RoleRegistry._validate_pipeline_contract(spec, contract)

    @staticmethod
    def _validate_pipeline_contract(
        spec: PipelineRoleSpec, contract: PipelineActionContract
    ) -> None:
        if _STABLE_ID.fullmatch(contract.contract_id) is None:
            raise ValueError("contract id has an invalid stable format")
        if not contract.action_types or any(
            not action.strip() for action in contract.action_types
        ):
            raise ValueError("action types must be non-empty")
        if len(set(contract.action_types)) != len(contract.action_types):
            raise ValueError("action types must be unique")
        actions = set(contract.action_types)
        if contract.fallback_action_type not in actions:
            raise ValueError("fallback action must belong to action types")
        if contract.fallback_action_type in contract.actions_requiring_target:
            raise ValueError("fallback action cannot require a target")
        if not contract.actions_requiring_target <= actions:
            raise ValueError("target rule references an unknown action type")
        if contract.order < 0 or contract.order > _MAX_ORDER:
            raise ValueError("contract order is outside the supported range")
        for limit in (
            contract.per_window_limit,
            contract.per_round_limit,
            contract.per_game_limit,
        ):
            if limit is not None and limit <= 0:
                raise ValueError("contract limit must be positive")
        if not contract.allowed_effects <= spec.allowed_effects:
            raise ValueError("effect permission exceeds role declaration")
        if not contract.visibility_namespaces <= spec.visibility_namespaces:
            raise ValueError("contract visibility exceeds role declaration")
        if not contract.selected_target_fact_namespaces <= {"camp_label"}:
            raise ValueError("unknown selected target fact namespace")
        if contract.selected_target_fact_namespaces and contract.aggregate is not None:
            raise ValueError("selected target facts cannot be used by aggregate contracts")
        if any(not event_type.strip() for event_type in contract.response_event_types):
            raise ValueError("response event type must not be empty")
        if any(not reason.strip() for reason in contract.response_reasons):
            raise ValueError("response reason must not be empty")
        if contract.response_reasons and not contract.response_event_types:
            raise ValueError("response reasons require response event types")
        if contract.is_applicable is None:
            raise ValueError("is_applicable hook is required")
        if (contract.resolve is None) == (contract.aggregate is None):
            raise ValueError("exactly one resolution hook is required")
        hooks = {
            "is_applicable": (
                contract.is_applicable,
                (("context", PipelineActionContext),),
                bool,
            ),
            "validate": (
                contract.validate,
                (("context", PipelineActionContext), ("command", PipelineActionCommand)),
                tuple[RuleViolation, ...],
            ),
            "resolve": (
                contract.resolve,
                (("context", PipelineActionContext), ("command", PipelineActionCommand)),
                tuple[GameEffect, ...],
            ),
            "react": (
                contract.react,
                (("context", PipelineActionContext),),
                tuple[GameEffect, ...],
            ),
            "aggregate": (
                contract.aggregate,
                (
                    ("context", PipelineActionContext),
                    ("commands", tuple[PipelineActionCommand, ...]),
                ),
                tuple[GameEffect, ...],
            ),
        }
        for name, (hook, parameters, return_type) in hooks.items():
            if hook is not None:
                RoleRegistry._validate_hook_signature(
                    name, hook, parameters, return_type
                )

    @staticmethod
    def _validate_hook_signature(
        name: str,
        hook: Callable[..., object],
        expected_parameters: tuple[tuple[str, object], ...],
        expected_return: object,
    ) -> None:
        try:
            signature = inspect.signature(hook)
            parameters = tuple(signature.parameters.values())
            hints = get_type_hints(hook)
        except Exception as error:
            raise ValueError(f"hook signature mismatch: {name}") from error
        if len(parameters) != len(expected_parameters):
            raise ValueError(f"hook signature mismatch: {name}")
        actual_parameters = tuple(
            (
                parameter.name,
                parameter.kind,
                parameter.default,
                hints.get(parameter.name),
            )
            for parameter in parameters
        )
        required_parameters = tuple(
            (
                expected_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.empty,
                expected_type,
            )
            for expected_name, expected_type in expected_parameters
        )
        if actual_parameters != required_parameters:
            raise ValueError(f"hook signature mismatch: {name}")
        if hints.get("return") != expected_return:
            raise ValueError(f"hook signature mismatch: {name}")

    def require(self, role_id: str) -> LegacyRoleSpec:
        with self._lock:
            try:
                return self._specs[role_id]
            except KeyError as error:
                raise ValueError(f"unknown role: {role_id}") from error

    def validate_role_counts(
        self, role_counts: Mapping[str, int], player_count: int
    ) -> None:
        total = 0
        for role_id, count in role_counts.items():
            self.require(role_id)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("role count must be a non-negative integer")
            total += count
        if total != player_count:
            raise ValueError("role count sum must equal player_count")

    def validate_role_assignments(
        self, role_counts: Mapping[str, int], player_count: int,
        role_by_seat: Mapping[int, str],
    ) -> None:
        self.validate_role_counts(role_counts, player_count)
        if (
            not isinstance(role_by_seat, Mapping)
            or any(type(seat) is not int for seat in role_by_seat)
            or set(role_by_seat) != set(range(1, player_count + 1))
        ):
            raise ValueError("role assignments must cover every seat with integer seats")
        if (
            any(type(role) is not str for role in role_by_seat.values())
            or Counter(role_by_seat.values()) != Counter(role_counts)
        ):
            raise ValueError("role assignments must match configured role counts")

    def create_roles(
        self,
        role_counts: Mapping[str, int],
        player_count: int,
        prompt_builder: object,
        llm_client_factory: Callable[[int], object],
        *,
        role_by_seat: Mapping[int, str] | None = None,
        rng: random.Random | None = None,
    ) -> dict[int, object]:
        self.validate_role_counts(role_counts, player_count)
        role_ids = [
            role_id
            for role_id, count in role_counts.items()
            for _ in range(count)
        ]
        if role_by_seat is None:
            (rng or random).shuffle(role_ids)
        else:
            self.validate_role_assignments(role_counts, player_count, role_by_seat)
            role_ids = [role_by_seat[seat] for seat in range(1, player_count + 1)]

        return {
            seat: self.require(role_id).role_factory(
                seat, role_id, prompt_builder, llm_client_factory(seat)
            )
            for seat, role_id in enumerate(role_ids, start=1)
        }

    def build_requests(
        self,
        state: GameState,
        roles: Mapping[int, object],
        phase: GamePhase,
    ) -> list[ActionRequest]:
        if phase != state.phase:
            return []
        accepted_keys = getattr(state, "accepted_action_keys", set())
        requests = []
        for seat, player in state.players.items():
            if not player.is_alive or seat not in roles:
                continue
            role_id = player.role
            for contract in self.require(role_id).contracts:
                if contract.phase != phase:
                    continue
                key = f"{state.round_number}:{phase.value}:{seat}:{contract.contract_id}"
                if key in accepted_keys:
                    continue
                requests.append(
                    ActionRequest(
                        actor_seat=seat,
                        role_id=role_id,
                        contract=contract,
                        phase=phase,
                        round_id=state.round_number,
                        idempotency_key=key,
                    )
                )
        return sorted(requests, key=lambda request: request.contract.resolution_priority)


def _contract(
    contract_id: str,
    phase: GamePhase,
    action_types: tuple[str, ...],
    actions_requiring_target: frozenset[str],
    resolution_priority: int,
    fallback_action_type: str = "pass",
) -> ActionContract:
    return ActionContract(
        contract_id=contract_id,
        phase=phase,
        action_types=action_types,
        actions_requiring_target=actions_requiring_target,
        resolution_priority=resolution_priority,
        fallback_action_type=fallback_action_type,
    )


builtin_registry = RoleRegistry()
builtin_registry.register_pipeline(WEREWOLF_SPEC)
builtin_registry.register_pipeline(WITCH_SPEC)
builtin_registry.register_pipeline(SEER_SPEC)
builtin_registry.register_pipeline(HUNTER_SPEC)
builtin_registry.register_pipeline(VILLAGER_SPEC)
builtin_registry.register_pipeline(GUARD_SPEC)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-villager", Camp.GOOD, Villager, ())
)
builtin_registry.register(
    LegacyRoleSpec(
        "wolf-killer-werewolf",
        Camp.WEREWOLF,
        Werewolf,
        (
            _contract(
                "werewolf_kill",
                GamePhase.NIGHT,
                ("kill", "pass"),
                frozenset({"kill"}),
                resolution_priority=10,
            ),
        ),
    )
)
builtin_registry.register(
    LegacyRoleSpec(
        "wolf-killer-seer",
        Camp.GOOD,
        Seer,
        (
            _contract(
                "seer_check",
                GamePhase.NIGHT,
                ("check", "pass"),
                frozenset({"check"}),
                resolution_priority=30,
            ),
        ),
    )
)
builtin_registry.register(
    LegacyRoleSpec(
        "wolf-killer-witch",
        Camp.GOOD,
        Witch,
        (
            _contract(
                "witch_action",
                GamePhase.NIGHT,
                ("save", "poison", "pass"),
                frozenset({"save", "poison"}),
                resolution_priority=20,
            ),
        ),
    )
)
builtin_registry.register(
    LegacyRoleSpec(
        "wolf-killer-hunter",
        Camp.GOOD,
        Hunter,
        (
            _contract(
                "hunter_shoot",
                GamePhase.DAWN,
                ("shoot", "pass"),
                frozenset({"shoot"}),
                resolution_priority=40,
            ),
        ),
    )
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-guard", Camp.GOOD, Guard, ())
)
