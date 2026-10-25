from __future__ import annotations

import hashlib
import inspect
import random
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from threading import RLock
from types import MappingProxyType
from typing import get_type_hints

from app.models.contracts import ActionContract, ActionRequest, RoleSpec as LegacyRoleSpec
from app.models.game import Camp, GamePhase, GameState
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext as PipelineActionContext,
    ActionContract as PipelineActionContract,
    EffectKind,
    GameEffect,
    RoleSpec as PipelineRoleSpec,
    RuleViolation,
)
from app.roles.hunter import HUNTER_SPEC, Hunter
from app.roles.awakened_hunter import AWAKENED_HUNTER_SPEC, AwakenedHunter
from app.roles.guard import GUARD_SPEC, Guard
from app.roles.idiot import IDIOT_SPEC, Idiot
from app.roles.knight import KNIGHT_SPEC, Knight
from app.roles.old_drunkard import OLD_DRUNKARD_SPEC, OldDrunkard
from app.roles.seer import SEER_SPEC, Seer
from app.roles.villager import VILLAGER_SPEC, Villager
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, WEREWOLF_SPEC, Werewolf
from app.roles.werewolf_king import WEREWOLF_KING_SPEC, WerewolfKing
from app.roles.dream_weaver import DREAM_WEAVER_SPEC, DreamWeaver
from app.roles.evil_knight import EVIL_KNIGHT_SPEC, EvilKnight
from app.roles.gargoyle import GARGOYLE_SPEC, Gargoyle
from app.roles.gravekeeper import GRAVEKEEPER_SPEC, Gravekeeper
from app.roles.pure_white import PURE_WHITE_SPEC, PureWhite
from app.roles.wolf_witch import WOLF_WITCH_SPEC, WolfWitch
from app.roles.demon_hunter import DEMON_HUNTER_SPEC, DemonHunter
from app.roles.crimson_envoy import CRIMSON_ENVOY_SPEC, CrimsonEnvoy
from app.roles.nightmare import NIGHTMARE_SPEC, Nightmare
from app.roles.time_wolf import TIME_WOLF_SPEC, TimeWolf
from app.roles.order_prince import ORDER_PRINCE_SPEC, OrderPrince
from app.roles.dancer import DANCER_SPEC, Dancer
from app.roles.mask import MASK_SPEC, Mask
from app.roles.lonely_girl import LONELY_GIRL_SPEC, LonelyGirl
from app.roles.miracle_merchant import MIRACLE_MERCHANT_SPEC, MiracleMerchant
from app.roles.mirror_girl import MIRROR_GIRL_SPEC, MirrorGirl
from app.roles.awakened_hidden_wolf import AWAKENED_HIDDEN_WOLF_SPEC, AwakenedHiddenWolf
from app.roles.radiance_earl import RADIANCE_EARL_SPEC, RadianceEarl
from app.roles.eclipse_maid import ECLIPSE_MAID_SPEC, EclipseMaid
from app.roles.awakened_seer import AWAKENED_SEER_SPEC, AwakenedSeer
from app.roles.fragrance_phantom import FRAGRANCE_PHANTOM_SPEC, FragrancePhantom
from app.roles.wolf_king import WOLF_KING_SPEC, WolfKing
from app.roles.awakened_wolf_king import AWAKENED_WOLF_KING_SPEC, AwakenedWolfKing
from app.roles.wolf_beauty import WOLF_BEAUTY_SPEC, WolfBeauty
from app.roles.awakened_wolf_beauty import (
    AWAKENED_WOLF_BEAUTY_SPEC, AwakenedWolfBeauty,
)
from app.roles.witch import WITCH_SPEC, Witch
from app.roles.awakened_witch import AWAKENED_WITCH_SPEC, AwakenedWitch
from app.roles.fool_seer import FOOL_SEER_SPEC, FoolSeer
from app.roles.iron_villager import IRON_VILLAGER_SPEC, IronVillager
from app.roles.crow import CROW_SPEC, Crow
from app.roles.cupid import CUPID_SPEC, Cupid
from app.roles.admirer import ADMIRER_SPEC, Admirer


_VISIBLE_NAMESPACES = frozenset({"PUBLIC", "ACTOR", "CAMP", "RELATION"})
# Selected-target facts a contract may request. Kept in sync with the
# projector's whitelists: requesting a namespace is what makes the target's
# observable camp/statuses/public resources/exact role visible to the hook.
_SELECTED_TARGET_NAMESPACES = frozenset(
    {"camp_label", "status_labels", "resource_labels", "role_label"}
)
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

    def granted_pairs(self) -> tuple[tuple[PipelineRoleSpec, PipelineActionContract], ...]:
        """Every ``(owner, contract)`` some role hands out to other seats."""
        return tuple(
            (spec, contract)
            for spec in self.specs.values()
            for contract in spec.granted_contracts
        )

    def contracts_for(self, role_id: str) -> tuple[PipelineActionContract, ...]:
        """Contracts a seat of ``role_id`` can legitimately be asked for.

        Its own, plus every granted one: whether a seat actually *holds* a grant
        is a runtime fact the scheduler gates on when issuing, not something the
        static registry can know. Callers that only need to recognise a stored or
        projected request use this instead of ``spec.contracts``.
        """
        return self.require(role_id).contracts + tuple(
            contract for _, contract in self.granted_pairs()
        )

    def acting_spec(
        self, role_id: str, contract: PipelineActionContract,
    ) -> PipelineRoleSpec:
        """The spec a seat acts under for ``contract``.

        For a contract the role owns that is the role's own spec. For a granted
        one it is the same role widened by exactly what the borrowed skill needs
        — the contract itself, its effects and namespaces, and the one note that
        explains it — so the resolver and the write entry check a lucky villager
        against the grant's declarations while the seat keeps its own identity,
        camp and instructions.
        """
        spec = self.require(role_id)
        if any(item is contract for item in spec.contracts):
            return spec
        for owner, granted in self.granted_pairs():
            if granted is contract:
                note = owner.granted_notes[granted.contract_id]
                return replace(
                    spec,
                    contracts=spec.contracts + (granted,),
                    allowed_effects=spec.allowed_effects | granted.allowed_effects,
                    visibility_namespaces=(
                        spec.visibility_namespaces | granted.visibility_namespaces
                    ),
                    instructions=f"{spec.instructions}\n\n{note}",
                )
        # Not granted either: hand the seat's own spec back and let the resolver's
        # identity check reject the stray contract, exactly as before grants existed.
        return spec

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

    def pipeline_spec(self, role_id: str) -> PipelineRoleSpec | None:
        """The declared spec for ``role_id``, or None when it has none."""
        with self._lock:
            return self._pipeline_specs.get(role_id)

    def freeze(self) -> RegistrySnapshot:
        with self._lock:
            local_specs = dict(self._pipeline_specs)
        contract_ids: dict[str, str] = {}
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
        contract_ids: dict[str, str],
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
        if spec.bond_slot and spec.bond_slot not in spec.initial_private_data:
            # The queue reads the slot straight off the seat, and the projector
            # is what turns a *declared* slot into a fact. A bond slot nobody
            # declared would open windows for a hook that cannot see its pair.
            raise ValueError("bond slot must be declared in initial_private_data")
        granted_ids = {contract.contract_id for contract in spec.granted_contracts}
        if set(spec.granted_notes) != granted_ids:
            # The holder's prompt is the only place a borrowed skill is
            # explained, so a grant without a note would be an unexplained
            # request and a note without a grant is a typo.
            raise ValueError("granted notes must match the granted contracts")
        for contract in spec.contracts + spec.granted_contracts:
            # Several roles may share one contract (e.g. a whole camp voting
            # through the same aggregate) only when the declarations are
            # byte-identical; any divergence is still a duplicate id.
            serialized = PipelineActionContract.to_json(contract)
            existing = contract_ids.get(contract.contract_id)
            if existing is not None and existing != serialized:
                raise ValueError(f"duplicate contract id: {contract.contract_id}")
            contract_ids[contract.contract_id] = serialized
            RoleRegistry._validate_pipeline_contract(spec, contract)
        for contract in spec.granted_contracts:
            RoleRegistry._validate_granted_contract(spec, contract)

    @staticmethod
    def _validate_granted_contract(
        spec: PipelineRoleSpec, contract: PipelineActionContract
    ) -> None:
        """A granted contract is issued by resource, one seat at a time."""
        if any(item is contract for item in spec.contracts):
            raise ValueError("a contract cannot be both owned and granted")
        if not contract.required_resources:
            # The marker the owner writes is the only thing that makes a seat a
            # holder; without it the contract would be issued to nobody (or,
            # were the gate ever skipped, to everybody).
            raise ValueError("a granted contract must require a holder resource")
        if contract.aggregate is not None or contract.react is not None:
            raise ValueError("a granted contract must be a plain per-seat command")

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
        counts = set(contract.multi_target_actions.values())
        if len(counts) > 1:
            raise ValueError("multi-target actions in one contract must share one count")
        for action, count in contract.multi_target_actions.items():
            if action not in actions:
                raise ValueError(
                    f"multi-target rule references an unknown action type: {action}"
                )
            if count < 2:
                raise ValueError(
                    f"multi-target action needs at least two targets: {action}"
                )
            if action in contract.actions_requiring_target:
                raise ValueError(
                    "multi-target action cannot also require a single target: "
                    f"{action}"
                )
            if action == contract.fallback_action_type:
                raise ValueError(
                    f"fallback action cannot be a multi-target action: {action}"
                )
        if contract.order < 0 or contract.order > _MAX_ORDER:
            raise ValueError("contract order is outside the supported range")
        for limit in (
            contract.per_window_limit,
            contract.per_round_limit,
            contract.per_game_limit,
        ):
            if limit is not None and limit <= 0:
                raise ValueError("contract limit must be positive")
        # A per_game_limit of 1 is the one-shot card, and its quota counter and
        # its resource are two ledgers for the same fact. Only the resource is
        # allowed to be the authority: `record_accepted_action` deliberately does
        # not spend the game quota on an engine-made command, so that one model
        # failure cannot void the card. That is safe exactly while the resource
        # enforces the one-shot — require the pairing rather than trusting every
        # new role to remember it.
        if contract.per_game_limit == 1 and EffectKind.CONSUME_RESOURCE not in contract.allowed_effects:
            raise ValueError("a per_game_limit contract must consume a resource")
        if not contract.allowed_effects <= spec.allowed_effects:
            raise ValueError("effect permission exceeds role declaration")
        if not contract.visibility_namespaces <= spec.visibility_namespaces:
            raise ValueError("contract visibility exceeds role declaration")
        if not contract.selected_target_fact_namespaces <= _SELECTED_TARGET_NAMESPACES:
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
        # A contract resolves commands with exactly one of resolve/aggregate;
        # a pure react hook may stand alone because it needs no command.
        command_hooks = sum(
            hook is not None for hook in (contract.resolve, contract.aggregate)
        )
        if command_hooks > 1 or (command_hooks == 0 and contract.react is None):
            raise ValueError("exactly one resolution hook is required")
        if contract.react is not None and not contract.response_event_types:
            raise ValueError("react hook requires response event types")
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
builtin_registry.register_pipeline(AWAKENED_WITCH_SPEC)
builtin_registry.register_pipeline(SEER_SPEC)
builtin_registry.register_pipeline(HUNTER_SPEC)
builtin_registry.register_pipeline(AWAKENED_HUNTER_SPEC)
builtin_registry.register_pipeline(VILLAGER_SPEC)
builtin_registry.register_pipeline(GUARD_SPEC)
builtin_registry.register_pipeline(IDIOT_SPEC)
builtin_registry.register_pipeline(KNIGHT_SPEC)
builtin_registry.register_pipeline(OLD_DRUNKARD_SPEC)
builtin_registry.register_pipeline(WOLF_BEAUTY_SPEC)
builtin_registry.register_pipeline(AWAKENED_WOLF_BEAUTY_SPEC)
builtin_registry.register_pipeline(WEREWOLF_KING_SPEC)
builtin_registry.register_pipeline(WOLF_KING_SPEC)
builtin_registry.register_pipeline(AWAKENED_WOLF_KING_SPEC)
builtin_registry.register_pipeline(EVIL_KNIGHT_SPEC)
builtin_registry.register_pipeline(DREAM_WEAVER_SPEC)
builtin_registry.register_pipeline(GARGOYLE_SPEC)
builtin_registry.register_pipeline(GRAVEKEEPER_SPEC)
builtin_registry.register_pipeline(PURE_WHITE_SPEC)
builtin_registry.register_pipeline(WOLF_WITCH_SPEC)
builtin_registry.register_pipeline(DEMON_HUNTER_SPEC)
builtin_registry.register_pipeline(CRIMSON_ENVOY_SPEC)
builtin_registry.register_pipeline(NIGHTMARE_SPEC)
builtin_registry.register_pipeline(TIME_WOLF_SPEC)
builtin_registry.register_pipeline(ORDER_PRINCE_SPEC)
builtin_registry.register_pipeline(DANCER_SPEC)
builtin_registry.register_pipeline(MASK_SPEC)
builtin_registry.register_pipeline(LONELY_GIRL_SPEC)
builtin_registry.register_pipeline(MIRACLE_MERCHANT_SPEC)
builtin_registry.register_pipeline(MIRROR_GIRL_SPEC)
builtin_registry.register_pipeline(AWAKENED_HIDDEN_WOLF_SPEC)
builtin_registry.register_pipeline(RADIANCE_EARL_SPEC)
builtin_registry.register_pipeline(ECLIPSE_MAID_SPEC)
builtin_registry.register_pipeline(AWAKENED_SEER_SPEC)
builtin_registry.register_pipeline(FRAGRANCE_PHANTOM_SPEC)
builtin_registry.register_pipeline(FOOL_SEER_SPEC)
builtin_registry.register_pipeline(IRON_VILLAGER_SPEC)
builtin_registry.register_pipeline(CROW_SPEC)
builtin_registry.register_pipeline(CUPID_SPEC)
builtin_registry.register_pipeline(ADMIRER_SPEC)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-awakened-wolf-king", Camp.WEREWOLF, AwakenedWolfKing, ())
)
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
    LegacyRoleSpec("wolf-killer-awakened-witch", Camp.GOOD, AwakenedWitch, ())
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
# Same shot, different rules: the awakened hunter's contract lives on its own
# spec, so the legacy registration carries no contracts at all.
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-awakened-hunter", Camp.GOOD, AwakenedHunter, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-idiot", Camp.GOOD, Idiot, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-knight", Camp.GOOD, Knight, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-old-drunkard", Camp.GOOD, OldDrunkard, ())
)
builtin_registry.register(
    LegacyRoleSpec(
        "wolf-killer-wolf-beauty",
        Camp.WEREWOLF,
        WolfBeauty,
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
        "wolf-killer-awakened-wolf-beauty",
        Camp.WEREWOLF,
        AwakenedWolfBeauty,
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
        "wolf-killer-werewolf-king",
        Camp.WEREWOLF,
        WerewolfKing,
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
        "wolf-killer-wolf-king",
        Camp.WEREWOLF,
        WolfKing,
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
    LegacyRoleSpec("wolf-killer-dream-weaver", Camp.GOOD, DreamWeaver, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-gravekeeper", Camp.GOOD, Gravekeeper, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-pure-white", Camp.GOOD, PureWhite, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-wolf-witch", Camp.WEREWOLF, WolfWitch, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-demon-hunter", Camp.GOOD, DemonHunter, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-crimson-envoy", Camp.WEREWOLF, CrimsonEnvoy, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-nightmare", Camp.WEREWOLF, Nightmare, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-time-wolf", Camp.WEREWOLF, TimeWolf, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-order-prince", Camp.GOOD, OrderPrince, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-dancer", Camp.GOOD, Dancer, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-mask", Camp.WEREWOLF, Mask, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-lonely-girl", Camp.GOOD, LonelyGirl, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-miracle-merchant", Camp.GOOD, MiracleMerchant, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-mirror-girl", Camp.GOOD, MirrorGirl, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-radiance-earl", Camp.GOOD, RadianceEarl, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-eclipse-maid", Camp.WEREWOLF, EclipseMaid, ())
)
builtin_registry.register(
    LegacyRoleSpec(
        "wolf-killer-awakened-hidden-wolf", Camp.WEREWOLF, AwakenedHiddenWolf, (),
    )
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-awakened-seer", Camp.GOOD, AwakenedSeer, ())
)
builtin_registry.register(
    LegacyRoleSpec(
        "wolf-killer-fragrance-phantom",
        Camp.WEREWOLF,
        FragrancePhantom,
        (
            _contract(
                "hidden_wolf_kill",
                GamePhase.NIGHT,
                ("kill", "pass"),
                frozenset({"kill"}),
                resolution_priority=10,
            ),
        ),
    )
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-fool-seer", Camp.GOOD, FoolSeer, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-iron-villager", Camp.GOOD, IronVillager, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-crow", Camp.GOOD, Crow, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-cupid", Camp.THIRD_PARTY, Cupid, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-admirer", Camp.GOOD, Admirer, ())
)
builtin_registry.register(
    LegacyRoleSpec("wolf-killer-gargoyle", Camp.WEREWOLF, Gargoyle, ())
)
builtin_registry.register(
    LegacyRoleSpec(
        "wolf-killer-evil-knight",
        Camp.WEREWOLF,
        EvilKnight,
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
