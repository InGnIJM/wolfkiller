from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Event, Thread
from types import MappingProxyType
import json

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.scheduler import Scheduler
from app.models.contracts import RoleSpec
from app.models.game import Camp, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand,
    ActionContext,
    ActionContract as PipelineActionContract,
    EffectKind,
    GameEffect,
    RoleSpec as PipelineRoleSpec,
    RuleViolation,
    SchedulePoint,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC, Villager


@dataclass
class StubRole:
    seat: int
    role_name: str


class DuckSpec:
    role_id = "duck-role"


class LegacyRoleSpecSubclass(RoleSpec):
    pass


class PipelineRoleSpecSubclass(PipelineRoleSpec):
    pass


def applicable_hook(context: ActionContext) -> bool:
    return context.actor_alive


def validate_hook(
    context: ActionContext, command: ActionCommand
) -> tuple[RuleViolation, ...]:
    del context, command
    return ()


def resolve_hook(
    context: ActionContext, command: ActionCommand
) -> tuple[GameEffect, ...]:
    del context, command
    return ()


def react_hook(context: ActionContext) -> tuple[GameEffect, ...]:
    del context
    return ()


def aggregate_hook(
    context: ActionContext, commands: tuple[ActionCommand, ...]
) -> tuple[GameEffect, ...]:
    del context, commands
    return ()


def bad_hook(context: ActionContext, extra: object) -> bool:
    del context, extra
    return True


def wrong_name_hook(value: ActionContext) -> bool:
    del value
    return True


def defaulted_hook(context: ActionContext | None = None) -> bool:
    del context
    return True


def wrong_parameter_type_hook(context: object) -> bool:
    del context
    return True


def wrong_return_hook(context: ActionContext) -> object:
    del context
    return True


def unresolved_type_hook(context: "MissingContext") -> bool:
    del context
    return True


def broken_signature_hook(context: ActionContext) -> bool:
    del context
    return True


def _pipeline_contract(**changes: object) -> PipelineActionContract:
    values: dict[str, object] = {
        "contract_id": "night-action",
        "schedule_point": SchedulePoint.NIGHT_ACTION,
        "order": 10,
        "action_types": ("act", "pass"),
        "actions_requiring_target": frozenset({"act"}),
        "fallback_action_type": "pass",
        "allowed_effects": frozenset({EffectKind.SUBMIT_DAMAGE}),
        "visibility_namespaces": frozenset({"ACTOR"}),
        "is_applicable": applicable_hook,
        "validate": validate_hook,
        "resolve": resolve_hook,
    }
    values.update(changes)
    return PipelineActionContract(**values)


def _pipeline_spec(role_id: str = "pipeline-role", **changes: object) -> PipelineRoleSpec:
    values: dict[str, object] = {
        "role_id": role_id,
        "display_name": role_id,
        "camp_id": "good",
        "contracts": (_pipeline_contract(),),
        "visibility_namespaces": frozenset({"ACTOR"}),
        "allowed_effects": frozenset({EffectKind.SUBMIT_DAMAGE}),
    }
    values.update(changes)
    return PipelineRoleSpec(**values)


class TestActionContracts:
    def test_contract_schema_requires_action_type_target_and_reasoning(self):
        contract = builtin_registry.require("wolf-killer-werewolf").contracts[0]

        schema = contract.json_schema()

        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["action_type", "target_seat", "reasoning"]
        assert schema["properties"]["action_type"]["enum"] == ["kill", "pass"]
        assert schema["properties"]["target_seat"]["type"] == ["integer", "null"]
        assert schema["properties"]["reasoning"]["maxLength"] == 500

    def test_pipeline_contract_exposes_model_gateway_tool_schema(self):
        contract = _pipeline_contract(action_types=("check", "pass"))

        schema = contract.json_schema()

        assert contract.resolved_tool_name == "night-action"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["schema_version"] == {
            "type": "integer",
            "const": 1,
        }
        assert schema["properties"]["action_type"]["enum"] == ["check", "pass"]
        assert schema["required"] == [
            "schema_version", "action_type", "target_seat", "reasoning",
        ]

    def test_selected_target_fact_namespaces_are_strict_and_stable(self):
        first = _pipeline_contract(selected_target_fact_namespaces=frozenset({"camp_label"}))
        second = _pipeline_contract()
        assert first.selected_target_fact_namespaces == frozenset({"camp_label"})
        assert first.stable_digest() != second.stable_digest()
        raw = first.to_json(); document = json.loads(raw)
        for hook in ("is_applicable", "validate", "resolve", "react", "aggregate"): document[hook] = None
        assert PipelineActionContract.from_json(json.dumps(document)).selected_target_fact_namespaces == frozenset({"camp_label"})
        with pytest.raises(TypeError): _pipeline_contract(selected_target_fact_namespaces=("camp_label",))
        with pytest.raises(ValueError): _pipeline_contract(selected_target_fact_namespaces=frozenset({"bad token"}))

    @pytest.mark.parametrize("changes", [
        {"selected_target_fact_namespaces": frozenset({"unknown"})},
        {"selected_target_fact_namespaces": frozenset({"camp_label"}), "resolve": None, "aggregate": aggregate_hook},
    ])
    def test_registry_rejects_invalid_selected_target_fact_declarations(self, changes):
        registry = RoleRegistry(); registry.register_pipeline(_pipeline_spec(contracts=(_pipeline_contract(**changes),)))
        with pytest.raises(ValueError, match="selected target"):
            registry.freeze()


class TestRoleRegistry:
    def test_rejects_unknown_role(self):
        with pytest.raises(ValueError, match="unknown role"):
            builtin_registry.validate_role_counts({"unknown": 1}, player_count=1)

    def test_rejects_duplicate_registration(self):
        registry = RoleRegistry()
        spec = builtin_registry.require("wolf-killer-villager")
        registry.register(spec)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(spec)

    def test_rejects_count_sum_mismatch(self):
        with pytest.raises(ValueError, match="sum"):
            builtin_registry.validate_role_counts(
                {"wolf-killer-villager": 1}, player_count=2
            )

    @pytest.mark.parametrize("count", [-1, 1.5, True])
    def test_rejects_negative_or_non_integer_count(self, count):
        with pytest.raises(ValueError, match="non-negative integer"):
            builtin_registry.validate_role_counts(
                {"wolf-killer-villager": count}, player_count=0
            )

    def test_create_roles_validates_then_assigns_registered_factories(self):
        registry = RoleRegistry()
        spec = builtin_registry.require("wolf-killer-villager")
        registry.register(spec)

        roles = registry.create_roles(
            {"wolf-killer-villager": 2},
            player_count=2,
            prompt_builder=object(),
            llm_client_factory=lambda: object(),
        )

        assert sorted(roles) == [1, 2]
        assert all(role.role_name == "wolf-killer-villager" for role in roles.values())

    def test_create_roles_uses_each_mixed_role_factory_with_constructor_arguments(self):
        registry = RoleRegistry()
        calls = []
        prompt_builder = object()
        clients = iter([object(), object(), object()])

        def factory(factory_name):
            def create(seat, role_name, received_prompt_builder, llm_client):
                calls.append(
                    (factory_name, seat, role_name, received_prompt_builder, llm_client)
                )
                return StubRole(seat, role_name)

            return create

        registry.register(RoleSpec("role-a", Camp.GOOD, factory("a"), ()))
        registry.register(RoleSpec("role-b", Camp.WEREWOLF, factory("b"), ()))

        roles = registry.create_roles(
            {"role-a": 2, "role-b": 1},
            player_count=3,
            prompt_builder=prompt_builder,
            llm_client_factory=lambda: next(clients),
        )

        assert sorted(roles) == [1, 2, 3]
        assert sorted(role.role_name for role in roles.values()) == [
            "role-a", "role-a", "role-b"
        ]
        assert Counter(factory_name for factory_name, *_ in calls) == {"a": 2, "b": 1}
        assert all(roles[seat].role_name == role_name for _, seat, role_name, *_ in calls)
        assert all(builder is prompt_builder for *_, builder, _ in calls)
        assert len({id(client) for *_, client in calls}) == 3

    def test_build_requests_only_for_alive_roles_with_current_phase_contracts(self):
        state = GameState(game_id="contracts", phase=GamePhase.NIGHT, round_number=4)
        state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", Camp.WEREWOLF.value),
            2: PlayerState(2, "wolf-killer-seer", Camp.GOOD.value),
            3: PlayerState(3, "wolf-killer-villager", Camp.GOOD.value),
            4: PlayerState(4, "wolf-killer-witch", Camp.GOOD.value, is_alive=False),
        }
        roles = {
            seat: StubRole(seat, player.role)
            for seat, player in state.players.items()
        }

        requests = builtin_registry.build_requests(state, roles, GamePhase.NIGHT)

        assert [(request.actor_seat, request.contract.contract_id) for request in requests] == [
            (1, "werewolf_kill"),
            (2, "seer_check"),
        ]
        assert all(request.round_id == 4 for request in requests)

    def test_build_requests_skips_previously_accepted_idempotency_key(self):
        state = GameState(game_id="contracts", phase=GamePhase.NIGHT, round_number=2)
        state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", Camp.WEREWOLF.value),
        }
        roles = {1: StubRole(1, "wolf-killer-werewolf")}
        state.accepted_action_keys = {"2:night:1:werewolf_kill"}

        assert builtin_registry.build_requests(state, roles, GamePhase.NIGHT) == []

    def test_build_requests_skips_contracts_from_other_phases(self):
        state = GameState(game_id="contracts", phase=GamePhase.NIGHT, round_number=2)
        state.players = {
            1: PlayerState(1, "wolf-killer-hunter", Camp.GOOD.value),
        }

        assert builtin_registry.build_requests(
            state, {1: StubRole(1, "wolf-killer-hunter")}, GamePhase.NIGHT
        ) == []

    def test_build_requests_returns_empty_for_a_phase_other_than_game_state(self):
        state = GameState(
            game_id="contracts", phase=GamePhase.VOTE_CASTING, round_number=2
        )
        state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", Camp.WEREWOLF.value),
        }

        assert builtin_registry.build_requests(
            state, {1: StubRole(1, "wolf-killer-werewolf")}, GamePhase.NIGHT
        ) == []

    def test_witch_has_one_combined_action_contract(self):
        contracts = builtin_registry.require("wolf-killer-witch").contracts

        assert len(contracts) == 1
        assert contracts[0].contract_id == "witch_action"
        assert contracts[0].action_types == ("save", "poison", "pass")


class TestPipelineRoleRegistry:
    def test_builtin_passive_villager_spec_preserves_legacy_factory(self):
        snapshot = builtin_registry.freeze()

        assert snapshot.require(VILLAGER_SPEC.role_id) is VILLAGER_SPEC
        assert VILLAGER_SPEC.role_id == "wolf-killer-villager"
        assert VILLAGER_SPEC.contracts == ()
        assert VILLAGER_SPEC.allowed_effects == frozenset()
        assert VILLAGER_SPEC.visibility_namespaces == frozenset({"PUBLIC", "ACTOR"})
        assert builtin_registry.require(VILLAGER_SPEC.role_id).role_factory is Villager

    def test_passive_villager_never_issues_or_blocks_any_schedule_point(self):
        registry = RoleRegistry(); registry.register_pipeline(VILLAGER_SPEC)
        snapshot = registry.freeze()
        state = GameState("passive", players={
            1: PlayerState(1, VILLAGER_SPEC.role_id, Camp.GOOD.value),
        })
        calls = []
        scheduler = Scheduler(
            snapshot, ContextProjector(), ActionValidator(), ActionResolver(),
            EffectApplier(), lambda *args: calls.append(args),
        )

        for point in SchedulePoint:
            assert scheduler.issue(state, point, snapshot) == ()
            result = scheduler.run_point(state, point)
            assert result.requests == result.commits == result.events == ()
        assert calls == []
        assert not hasattr(state, "_pipeline_runtime")

    def test_passive_snapshot_accepts_valid_counts_and_rejects_unknown_role(self):
        registry = RoleRegistry(); registry.register_pipeline(VILLAGER_SPEC)
        snapshot = registry.freeze()

        snapshot.validate_role_counts({VILLAGER_SPEC.role_id: 3}, 3)
        snapshot.validate_role_counts({}, 0)
        with pytest.raises(ValueError, match="unknown role"):
            snapshot.validate_role_counts({"unknown": 1}, 1)

    @pytest.mark.parametrize("bad_spec", [_pipeline_spec(), DuckSpec()])
    def test_legacy_registration_rejects_non_legacy_specs_without_pollution(
        self, bad_spec
    ):
        registry = RoleRegistry()

        with pytest.raises(TypeError, match="legacy register"):
            registry.register(bad_spec)

        with pytest.raises(ValueError, match="unknown role"):
            registry.require("pipeline-role" if isinstance(bad_spec, PipelineRoleSpec) else "duck-role")
        assert dict(registry.freeze().specs) == {}

    def test_freeze_returns_deeply_immutable_stable_snapshot(self):
        registry = RoleRegistry()
        registry.register_pipeline(_pipeline_spec())

        first = registry.freeze()
        second = registry.freeze()

        assert isinstance(first, RegistrySnapshot)
        assert isinstance(first.specs, MappingProxyType)
        assert first.require("pipeline-role").role_id == "pipeline-role"
        assert first.digest == second.digest
        with pytest.raises(TypeError):
            first.specs["other"] = _pipeline_spec("other")

    def test_freeze_without_pipeline_specs_is_valid_and_legacy_api_is_unchanged(self):
        registry = RoleRegistry()
        legacy = builtin_registry.require("wolf-killer-villager")
        registry.register(legacy)

        snapshot = registry.freeze()

        assert dict(snapshot.specs) == {}
        assert registry.require(legacy.role_id) is legacy
        assert snapshot.digest == registry.freeze().digest

    def test_legacy_and_pipeline_specs_can_share_role_id_during_migration(self):
        registry = RoleRegistry()
        legacy = builtin_registry.require("wolf-killer-villager")
        pipeline = _pipeline_spec(legacy.role_id)

        registry.register(legacy)
        registry.register_pipeline(pipeline)

        assert registry.require(legacy.role_id) is legacy
        assert registry.freeze().require(legacy.role_id) is pipeline

    def test_pipeline_registration_rejects_duplicate_role_id(self):
        registry = RoleRegistry()
        registry.register_pipeline(_pipeline_spec())

        with pytest.raises(ValueError, match="already registered"):
            registry.register_pipeline(_pipeline_spec())

    def test_pipeline_registration_requires_pipeline_spec(self):
        registry = RoleRegistry()
        legacy = builtin_registry.require("wolf-killer-villager")

        with pytest.raises(TypeError, match="pipeline register"):
            registry.register_pipeline(legacy)

        assert dict(registry.freeze().specs) == {}
        with pytest.raises(ValueError, match="unknown role"):
            registry.require(legacy.role_id)

    def test_pipeline_registration_rejects_duck_spec_without_pollution(self):
        registry = RoleRegistry()

        with pytest.raises(TypeError, match="pipeline register"):
            registry.register_pipeline(DuckSpec())

        assert dict(registry.freeze().specs) == {}
        with pytest.raises(ValueError, match="unknown role"):
            registry.require("duck-role")

    def test_both_registration_entries_reject_spec_subclasses(self):
        legacy = builtin_registry.require("wolf-killer-villager")
        legacy_subclass = LegacyRoleSpecSubclass(
            legacy.role_id, legacy.camp, legacy.role_factory, legacy.contracts
        )
        pipeline = _pipeline_spec()
        pipeline_subclass = PipelineRoleSpecSubclass(
            role_id=pipeline.role_id,
            display_name=pipeline.display_name,
            camp_id=pipeline.camp_id,
            contracts=pipeline.contracts,
            visibility_namespaces=pipeline.visibility_namespaces,
            allowed_effects=pipeline.allowed_effects,
        )
        registry = RoleRegistry()

        with pytest.raises(TypeError, match="legacy register"):
            registry.register(legacy_subclass)
        with pytest.raises(TypeError, match="pipeline register"):
            registry.register_pipeline(pipeline_subclass)

        with pytest.raises(ValueError, match="unknown role"):
            registry.require(legacy.role_id)
        assert dict(registry.freeze().specs) == {}

    def test_snapshot_is_unchanged_by_later_registration(self):
        registry = RoleRegistry()
        registry.register_pipeline(_pipeline_spec("first"))

        first = registry.freeze()
        registry.register_pipeline(_pipeline_spec("second", contracts=()))

        assert tuple(first.specs) == ("first",)
        assert tuple(registry.freeze().specs) == ("first", "second")

    def test_freeze_atomically_copies_specs_before_concurrent_registration(
        self, monkeypatch
    ):
        registry = RoleRegistry()
        registry.register_pipeline(_pipeline_spec("first"))
        validation_started = Event()
        continue_validation = Event()
        original = RoleRegistry._validate_pipeline_spec
        results = []
        failures = []

        def gated_validation(spec, contract_ids, known_roles):
            validation_started.set()
            assert continue_validation.wait(timeout=5)
            return original(spec, contract_ids, known_roles)

        monkeypatch.setattr(
            RoleRegistry, "_validate_pipeline_spec", staticmethod(gated_validation)
        )

        def freeze_in_thread():
            try:
                results.append(registry.freeze())
            except BaseException as error:
                failures.append(error)

        thread = Thread(target=freeze_in_thread)
        thread.start()
        assert validation_started.wait(timeout=5)
        registry.register_pipeline(_pipeline_spec("second", contracts=()))
        continue_validation.set()
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert failures == []
        assert tuple(results[0].specs) == ("first",)
        assert tuple(registry.freeze().specs) == ("first", "second")

    def test_snapshot_require_rejects_unknown_role(self):
        snapshot = RoleRegistry().freeze()
        with pytest.raises(ValueError, match="unknown role"):
            snapshot.require("unknown")

    @pytest.mark.parametrize(
        ("specs", "message"),
        [
            (
                (
                    _pipeline_spec("one"),
                    _pipeline_spec("two"),
                ),
                "duplicate contract",
            ),
            (
                (_pipeline_spec(contracts=(_pipeline_contract(action_types=()),)),),
                "action types",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(action_types=("act", "act", "pass")),
                        )
                    ),
                ),
                "action types",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(fallback_action_type="unknown"),
                        )
                    ),
                ),
                "fallback",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(
                                actions_requiring_target=frozenset({"unknown"})
                            ),
                        )
                    ),
                ),
                "target rule",
            ),
            (
                (_pipeline_spec(contracts=(_pipeline_contract(order=-1),)),),
                "order",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(_pipeline_contract(per_window_limit=0),)
                    ),
                ),
                "limit",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(
                                response_event_types=frozenset({""}),
                            ),
                        )
                    ),
                ),
                "response",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(
                                response_reasons=frozenset({"poison"}),
                                response_event_types=frozenset(),
                                react=react_hook,
                            ),
                        )
                    ),
                ),
                "response",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(_pipeline_contract(is_applicable=bad_hook),)
                    ),
                ),
                "hook signature",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(resolve=None, aggregate=None),
                        )
                    ),
                ),
                "resolution hook",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(aggregate=aggregate_hook),
                        )
                    ),
                ),
                "resolution hook",
            ),
            (
                (
                    _pipeline_spec(
                        allowed_effects=frozenset(),
                    ),
                ),
                "effect permission",
            ),
            (
                (
                    _pipeline_spec(
                        visibility_namespaces=frozenset({"ACTOR"}),
                        contracts=(
                            _pipeline_contract(
                                visibility_namespaces=frozenset({"CAMP"})
                            ),
                        ),
                    ),
                ),
                "visibility",
            ),
            (
                (_pipeline_spec(visibility_namespaces=frozenset({"UNKNOWN"})),),
                "visibility",
            ),
            ((_pipeline_spec("Bad Role"),), "role id"),
            ((_pipeline_spec(min_count=-1),), "minimum"),
            ((_pipeline_spec(min_count=2, max_count=1),), "maximum"),
            (
                (
                    _pipeline_spec(
                        dependencies=frozenset({"other"}),
                        exclusions=frozenset({"other"}),
                    ),
                    _pipeline_spec("other", contracts=()),
                ),
                "overlap",
            ),
            (
                (_pipeline_spec(dependencies=frozenset({"pipeline-role"})),),
                "itself",
            ),
            (
                (_pipeline_spec(dependencies=frozenset({"missing"})),),
                "unknown role",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(_pipeline_contract(contract_id="Bad Contract"),)
                    ),
                ),
                "contract id",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(action_types=("act", " ", "pass")),
                        )
                    ),
                ),
                "action types",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(
                                fallback_action_type="act",
                                actions_requiring_target=frozenset({"act"}),
                            ),
                        )
                    ),
                ),
                "fallback",
            ),
            (
                (_pipeline_spec(contracts=(_pipeline_contract(order=1_000_001),)),),
                "order",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(_pipeline_contract(per_round_limit=0),)
                    ),
                ),
                "limit",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(_pipeline_contract(per_game_limit=0),)
                    ),
                ),
                "limit",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(
                            _pipeline_contract(
                                response_event_types=frozenset({"PLAYER_DIED"}),
                                response_reasons=frozenset({" "}),
                            ),
                        )
                    ),
                ),
                "response reason",
            ),
            (
                (
                    _pipeline_spec(
                        contracts=(_pipeline_contract(is_applicable=None),)
                    ),
                ),
                "is_applicable",
            ),
        ],
    )
    def test_freeze_rejects_invalid_static_pipeline_specs(self, specs, message):
        registry = RoleRegistry()
        for spec in specs:
            registry.register_pipeline(spec)

        with pytest.raises(ValueError, match=message):
            registry.freeze()

    def test_freeze_accepts_complete_hook_shapes_and_response_contract(self):
        contract = _pipeline_contract(
            is_applicable=applicable_hook,
            validate=validate_hook,
            resolve=None,
            react=react_hook,
            aggregate=aggregate_hook,
            response_event_types=frozenset({"PLAYER_DIED"}),
            response_reasons=frozenset({"vote", "night"}),
        )
        registry = RoleRegistry()
        registry.register_pipeline(_pipeline_spec(contracts=(contract,)))

        assert registry.freeze().require("pipeline-role").contracts == (contract,)

    @pytest.mark.parametrize(
        "hook",
        [
            wrong_name_hook,
            defaulted_hook,
            wrong_parameter_type_hook,
            wrong_return_hook,
            unresolved_type_hook,
        ],
    )
    def test_freeze_rejects_each_exact_hook_signature_mismatch(self, hook):
        registry = RoleRegistry()
        registry.register_pipeline(
            _pipeline_spec(
                contracts=(_pipeline_contract(is_applicable=hook),)
            )
        )

        with pytest.raises(ValueError, match="hook signature"):
            registry.freeze()

    def test_freeze_wraps_inspect_signature_errors(self, monkeypatch):
        monkeypatch.setattr(
            broken_signature_hook, "__signature__", "invalid", raising=False
        )
        registry = RoleRegistry()
        registry.register_pipeline(
            _pipeline_spec(
                contracts=(
                    _pipeline_contract(is_applicable=broken_signature_hook),
                )
            )
        )

        with pytest.raises(ValueError, match="hook signature mismatch"):
            registry.freeze()

    @pytest.mark.parametrize("control_error", [KeyboardInterrupt(), SystemExit()])
    def test_freeze_does_not_wrap_process_control_errors(
        self, monkeypatch, control_error
    ):
        def raise_control_error(_hook):
            raise control_error

        monkeypatch.setattr(
            "app.roles.registry.inspect.signature", raise_control_error
        )
        registry = RoleRegistry()
        registry.register_pipeline(_pipeline_spec())

        with pytest.raises(type(control_error)):
            registry.freeze()

    @pytest.mark.parametrize("count", [-1, 1.5, True])
    def test_snapshot_rejects_invalid_counts_strictly(self, count):
        registry = RoleRegistry()
        registry.register_pipeline(_pipeline_spec())
        snapshot = registry.freeze()

        with pytest.raises(ValueError, match="non-negative integer"):
            snapshot.validate_role_counts({"pipeline-role": count}, 0)

    def test_snapshot_rejects_unknown_role_and_total_mismatch(self):
        registry = RoleRegistry()
        registry.register_pipeline(_pipeline_spec())
        snapshot = registry.freeze()

        with pytest.raises(ValueError, match="unknown role"):
            snapshot.validate_role_counts({"unknown": 1}, 1)
        with pytest.raises(ValueError, match="sum"):
            snapshot.validate_role_counts({"pipeline-role": 1}, 2)

    @pytest.mark.parametrize("player_count", [-1, 1.5, True])
    def test_snapshot_rejects_invalid_player_count_strictly(self, player_count):
        with pytest.raises(ValueError, match="player_count"):
            RoleRegistry().freeze().validate_role_counts({}, player_count)

    def test_snapshot_enforces_minimum_maximum_dependency_and_exclusion(self):
        registry = RoleRegistry()
        registry.register_pipeline(
            _pipeline_spec(
                "seer",
                min_count=1,
                max_count=2,
                dependencies=frozenset({"villager"}),
                exclusions=frozenset({"wolf"}),
            )
        )
        registry.register_pipeline(_pipeline_spec("villager", contracts=()))
        registry.register_pipeline(_pipeline_spec("wolf", contracts=()))
        snapshot = registry.freeze()

        with pytest.raises(ValueError, match="minimum"):
            snapshot.validate_role_counts({"seer": 0, "villager": 2}, 2)
        with pytest.raises(ValueError, match="maximum"):
            snapshot.validate_role_counts({"seer": 3, "villager": 1}, 4)
        with pytest.raises(ValueError, match="dependency"):
            snapshot.validate_role_counts({"seer": 1, "villager": 0}, 1)
        with pytest.raises(ValueError, match="exclusion"):
            snapshot.validate_role_counts(
                {"seer": 1, "villager": 1, "wolf": 1}, 3
            )

        snapshot.validate_role_counts({"seer": 1, "villager": 1}, 2)
