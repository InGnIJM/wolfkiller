from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from app.core.context_projector import ContextProjector
from app.core.effect_applier import initialize_role_resources
from app.core.role_runtime import role_action_counters
from app.models.actions import SpeechRecord, VoteAction
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionContext,
    ActionContract,
    IssuedActionRequest,
    RoleSpec,
    SchedulePoint,
)
from app.roles.registry import RegistrySnapshot


def _contract(
    *namespaces: str,
    contract_id: str = "night-action",
    response_event_types: frozenset[str] = frozenset(),
    response_reasons: frozenset[str] = frozenset(),
) -> ActionContract:
    return ActionContract(
        contract_id=contract_id,
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=10,
        action_types=("act", "pass"),
        actions_requiring_target=frozenset({"act"}),
        fallback_action_type="pass",
        visibility_namespaces=frozenset(namespaces),
        response_event_types=response_event_types,
        response_reasons=response_reasons,
    )


def _spec(
    role_id: str,
    camp_id: str,
    *namespaces: str,
    resources: dict[str, object] | None = None,
    private_data: dict[str, object] | None = None,
) -> RoleSpec:
    contract = _contract(*namespaces, contract_id=f"{role_id}-action")
    return RoleSpec(
        role_id=role_id,
        camp_id=camp_id,
        contracts=(contract,),
        visibility_namespaces=frozenset({"PUBLIC", *namespaces}),
        initial_resources=resources or {},
        initial_private_data=private_data or {},
    )


@pytest.fixture
def registry() -> RegistrySnapshot:
    specs = {
        "wolf": _spec("wolf", "werewolf", "ACTOR", "CAMP"),
        "seer": _spec(
            "seer", "good", "ACTOR", private_data={"private_checks": ()}
        ),
        "witch": _spec(
            "witch",
            "good",
            "ACTOR",
            resources={"antidote": True, "poison": True},
            private_data={"wolf_kill_target": None},
        ),
        "hunter": _spec(
            "hunter", "good", "ACTOR", resources={"gun": True}
        ),
        "villager": _spec("villager", "good"),
    }
    return RegistrySnapshot(specs=specs, digest="registry-v1")


@pytest.fixture
def state() -> GameState:
    game = GameState(game_id="game-1", phase=GamePhase.NIGHT, round_number=2)
    game.players = {
        1: PlayerState(1, "wolf", "werewolf", is_sheriff=True),
        2: PlayerState(2, "wolf", "werewolf"),
        3: PlayerState(
            3,
            "seer",
            "good",
            check_results=[
                {
                    "target_seat": 1,
                    "result": "werewolf",
                    "round": 1,
                    "reasoning": "must never escape",
                }
            ],
        ),
        4: PlayerState(
            4, "witch", "good", has_antidote=True, has_poison=False
        ),
        5: PlayerState(5, "hunter", "good", is_alive=False, has_gun=True),
        6: PlayerState(6, "villager", "good"),
    }
    game.sheriff = 1
    game.last_wolf_kill_target = 6
    game.speeches = [
        SpeechRecord(6, "public speech", 2),
        {
            "player_seat": 3,
            "text": "mapped speech",
            "round_number": 2,
            "thinking": "hidden",
            "role": "seer",
        },
    ]
    game.votes = [
        VoteAction(1, 6, reasoning="private reasoning", thinking="chain of thought"),
        {
            "voter_seat": 6,
            "target_seat": 1,
            "round_number": 2,
            "private": {"guess": "wolf"},
        },
    ]
    game.night_actions = [{"reasoning": "secret", "target_seat": 6}]
    return game


def _request(
    registry: RegistrySnapshot,
    seat: int,
    role_id: str,
    *,
    revision: int = 17,
) -> IssuedActionRequest:
    contract = registry.require(role_id).contracts[0]
    return IssuedActionRequest(
        actor_seat=seat,
        role_id=role_id,
        contract=contract,
        context_revision=revision,
        round_number=2,
        phase="night",
        window_id=f"window-{seat}",
        action_key=f"action-{seat}",
    )


def test_projects_public_and_only_declared_actor_and_camp_namespaces(
    state: GameState, registry: RegistrySnapshot
) -> None:
    projector = ContextProjector()
    wolf = projector.project(state, _request(registry, 1, "wolf"), registry)
    seer = projector.project(state, _request(registry, 3, "seer"), registry)
    villager = projector.project(state, _request(registry, 6, "villager"), registry)

    assert wolf.revision == 17
    assert wolf.config_version == "registry-v1"
    assert wolf.round_number == 2
    assert wolf.phase == "night"
    assert wolf.schedule_point is SchedulePoint.NIGHT_ACTION
    assert wolf.actor_seat == 1
    assert wolf.actor_role_id == "wolf"
    assert wolf.actor_alive is True
    assert wolf.facts["alive_seats"] == (1, 2, 3, 4, 6)
    assert wolf.facts["dead_seats"] == (5,)
    assert wolf.facts["sheriff"] == 1
    assert wolf.facts["phase"] == "night"
    assert wolf.facts["round_number"] == 2
    assert wolf.facts["camp_members"] == (1, 2)
    assert wolf.facts["actor_identity"] == {
        "seat": 1,
        "role_id": "wolf",
        "camp_id": "werewolf",
    }

    assert seer.facts["private_checks"] == (
        {"target": 1, "camp": "werewolf"},
    )
    assert "camp_members" not in seer.facts
    assert "private_checks" not in villager.facts
    assert "actor_identity" not in villager.facts
    forbidden = repr(villager)
    for secret in ("werewolf", "seer", "has_poison", "wolf_kill_target"):
        assert secret not in forbidden


def test_public_history_is_allowlisted_and_drops_reasoning_and_private_fields(
    state: GameState, registry: RegistrySnapshot
) -> None:
    context = ContextProjector().project(
        state, _request(registry, 6, "villager"), registry
    )

    assert context.facts["speeches"] == (
        {"player_seat": 6, "text": "public speech", "round_number": 2},
        {"player_seat": 3, "text": "mapped speech", "round_number": 2},
    )
    assert context.facts["votes"] == (
        {"voter_seat": 1, "target_seat": 6},
        {"voter_seat": 6, "target_seat": 1, "round_number": 2},
    )
    serialized = context.to_json()
    for secret in (
        "private reasoning",
        "chain of thought",
        "must never escape",
        '"thinking"',
        '"reasoning"',
        '"private"',
        '"night_actions"',
    ):
        assert secret not in serialized


def test_actor_resources_and_private_facts_are_minimal_and_role_name_agnostic(
    state: GameState, registry: RegistrySnapshot
) -> None:
    projector = ContextProjector()
    witch = projector.project(state, _request(registry, 4, "witch"), registry)
    hunter = projector.project(state, _request(registry, 5, "hunter"), registry)

    assert witch.resources == {"antidote": True, "poison": False}
    assert witch.facts["wolf_kill_target"] == 6
    assert hunter.resources == {"gun": True}
    assert hunter.actor_alive is False
    assert "wolf_kill_target" not in hunter.facts

    state.players[4].has_antidote = False
    no_antidote = projector.project(state, _request(registry, 4, "witch"), registry)
    assert no_antidote.resources == {"antidote": False, "poison": False}
    assert "wolf_kill_target" not in no_antidote.facts


def test_contract_visibility_intersects_role_visibility_and_relation_is_empty(
    state: GameState,
) -> None:
    actor_only = _contract("ACTOR", contract_id="hybrid-action")
    spec = RoleSpec(
        role_id="hybrid",
        camp_id="werewolf",
        contracts=(actor_only,),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP", "RELATION"}),
    )
    registry = RegistrySnapshot(specs={"hybrid": spec}, digest="v")
    state.players[1].role = "hybrid"
    request = IssuedActionRequest(
        actor_seat=1,
        role_id="hybrid",
        contract=actor_only,
        context_revision=1,
        round_number=2,
        phase="night",
        window_id="w",
        action_key="a",
    )

    context = ContextProjector().project(state, request, registry)
    assert "actor_identity" in context.facts
    assert "camp_members" not in context.facts
    assert "relations" not in context.facts


def test_context_is_deep_frozen_detached_and_contains_no_state_or_callbacks(
    state: GameState, registry: RegistrySnapshot
) -> None:
    contract = _contract(
        "ACTOR", "CAMP", contract_id="wolf-response",
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"wolf_kill"}),
    )
    response_registry = RegistrySnapshot(
        specs={
            "wolf": RoleSpec(
                role_id="wolf", camp_id="werewolf", contracts=(contract,),
                visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
            )
        },
        digest="response-v1",
    )
    context = ContextProjector().project(
        state,
        _request(response_registry, 1, "wolf"),
        response_registry,
        trigger_event={
            "event_id": "event:0123456789abcdef",
            "type": "PLAYER_DIED",
            "target_seat": 5,
        },
        trigger_reason="wolf_kill",
        source_event_id="event:0123456789abcdef",
        accepted_command_summaries=(
            {"contract_id": "wolf-response", "action_type": "act", "seats": [6]},
        ),
        aggregate_result={
            "contract_id": "wolf-response", "action_type": "act",
            "target_seat": 6, "votes": [1, 2],
        },
        counters={"window": 1},
    )

    assert isinstance(context.facts, MappingProxyType)
    assert isinstance(context.trigger_event, MappingProxyType)
    assert context.trigger_event == {
        "event_id": "event:0123456789abcdef",
        "type": "PLAYER_DIED",
        "target_seat": 5,
    }
    assert context.accepted_command_summaries == (
        {"contract_id": "wolf-response", "action_type": "act"},
    )
    assert context.aggregate_result == {
        "contract_id": "wolf-response", "action_type": "act", "target_seat": 6
    }
    with pytest.raises(TypeError):
        context.facts["alive_seats"] = ()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        context.actor_seat = 9  # type: ignore[misc]

    state.players[2].is_alive = False
    state.speeches[0].text = "mutated"
    assert 2 in context.facts["alive_seats"]
    assert context.facts["speeches"][0]["text"] == "public speech"
    assert "GameState" not in repr(context)
    assert "function" not in repr(context)


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda state, request: state.players.pop(request.actor_seat), "actor seat"),
        (
            lambda state, request: setattr(
                state.players[request.actor_seat], "role", "villager"
            ),
            "actor role",
        ),
        (lambda state, request: setattr(state, "phase", GamePhase.SPEECH), "phase"),
        (lambda state, request: setattr(state, "round_number", 3), "round"),
    ],
)
def test_rejects_request_that_does_not_match_state(
    state: GameState,
    registry: RegistrySnapshot,
    mutate: object,
    error: str,
) -> None:
    request = _request(registry, 1, "wolf")
    mutate(state, request)  # type: ignore[operator]
    with pytest.raises(ValueError, match=error):
        ContextProjector().project(state, request, registry)


def test_rejects_wrong_boundaries_contract_and_unknown_namespace(
    state: GameState, registry: RegistrySnapshot
) -> None:
    projector = ContextProjector()
    request = _request(registry, 1, "wolf")
    with pytest.raises(TypeError, match="state"):
        projector.project(object(), request, registry)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="request"):
        projector.project(state, object(), registry)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="registry"):
        projector.project(state, request, object())  # type: ignore[arg-type]

    other_contract = _contract("ACTOR", contract_id="other")
    bad_request = IssuedActionRequest(
        actor_seat=1,
        role_id="wolf",
        contract=other_contract,
        context_revision=1,
        round_number=2,
        phase="night",
        window_id="w",
        action_key="a",
    )
    with pytest.raises(ValueError, match="contract"):
        projector.project(state, bad_request, registry)

    unknown_contract = _contract("UNKNOWN", contract_id="unknown-action")
    unknown_spec = RoleSpec(
        role_id="wolf",
        camp_id="werewolf",
        contracts=(unknown_contract,),
        visibility_namespaces=frozenset({"UNKNOWN"}),
    )
    unknown_registry = RegistrySnapshot(
        specs={"wolf": unknown_spec}, digest="invalid-snapshot"
    )
    unknown_request = IssuedActionRequest(
        actor_seat=1,
        role_id="wolf",
        contract=unknown_contract,
        context_revision=1,
        round_number=2,
        phase="night",
        window_id="w",
        action_key="a",
    )
    with pytest.raises(ValueError, match="namespace"):
        projector.project(state, unknown_request, unknown_registry)


def test_rejects_actor_camp_that_disagrees_with_registered_role(
    state: GameState, registry: RegistrySnapshot
) -> None:
    state.players[1].camp = "good"
    with pytest.raises(ValueError, match="actor camp"):
        ContextProjector().project(state, _request(registry, 1, "wolf"), registry)


def test_optional_response_data_is_explicit_and_sensitive_keys_are_removed(
    state: GameState, registry: RegistrySnapshot
) -> None:
    projector = ContextProjector()
    request = _request(registry, 3, "seer")
    plain = projector.project(state, request, registry)
    assert plain.source_event_id is None
    assert plain.trigger_event is None
    assert plain.accepted_command_summaries == ()
    assert plain.aggregate_result is None

    contract = _contract(
        "ACTOR", contract_id="seer-response",
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"wolf_kill"}),
    )
    response_registry = RegistrySnapshot(
        specs={
            "seer": RoleSpec(
                role_id="seer", camp_id="good", contracts=(contract,),
                visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
            )
        }, digest="response-v1"
    )
    response_request = IssuedActionRequest(
        actor_seat=3, role_id="seer", contract=contract, context_revision=17,
        round_number=2, phase="night", window_id="window-3", action_key="action-3",
    )
    projected = projector.project(
        state,
        response_request,
        response_registry,
        source_event_id="event:abcdef0123456789",
        trigger_event={
            "event_id": "event:abcdef0123456789",
            "type": "PLAYER_DIED",
            "source_seat": 3,
            "target_seat": 1,
            "cause": "wolf_kill",
            "round_number": 2,
            "phase": "night",
            "revealed_role": "wolf",
            "has_poison": True,
            "wolf_kill_target": 6,
            "check_history": [{"target": 1, "camp": "werewolf"}],
            "nested": {
                "event_id": "nested-event",
                "revealed_role": "seer",
                "has_poison": True,
            },
        },
        accepted_command_summaries=(
            {
                "actor_seat": 3,
                "contract_id": "seer-response",
                "action_type": "act",
                "target_seat": 1,
                "revealed_role": "wolf",
                "nested": {"has_poison": True},
            },
        ),
        aggregate_result={
            "contract_id": "seer-response",
            "action_type": "act",
            "target_seat": 1,
            "count": 1,
            "tied": False,
            "selected_seat": 1,
            "wolf_kill_target": 6,
            "nested": {"check_history": [1]},
        },
    )
    assert projected.trigger_event == {
        "event_id": "event:abcdef0123456789",
        "type": "PLAYER_DIED",
        "source_seat": 3,
        "target_seat": 1,
        "cause": "wolf_kill",
        "round_number": 2,
        "phase": "night",
    }
    assert projected.accepted_command_summaries == (
        {
            "actor_seat": 3,
            "contract_id": "seer-response",
            "action_type": "act",
            "target_seat": 1,
        },
    )
    assert projected.aggregate_result == {
        "contract_id": "seer-response",
        "action_type": "act",
        "target_seat": 1,
        "count": 1,
        "tied": False,
        "selected_seat": 1,
    }
    serialized = projected.to_json()
    for secret in (
        "revealed_role",
        "has_poison",
        "wolf_kill_target",
        "check_history",
        "nested-event",
    ):
        assert secret not in serialized


@pytest.mark.parametrize(
    "reason",
    ["secret: target is the seer", "hunter_response", "", "WOLF_KILL"],
)
def test_trigger_reason_rejects_free_text_and_unknown_values(
    state: GameState,
    registry: RegistrySnapshot,
    reason: str,
) -> None:
    with pytest.raises(ValueError, match="trigger reason"):
        ContextProjector().project(
            state,
            _request(registry, 1, "wolf"),
            registry,
            trigger_reason=reason,
        )


def test_public_facts_are_unconditional_and_revision_comes_from_signed_request(
    state: GameState,
) -> None:
    contract = _contract(contract_id="publicless-action")
    spec = RoleSpec(
        role_id="villager",
        camp_id="good",
        contracts=(contract,),
        visibility_namespaces=frozenset(),
    )
    registry = RegistrySnapshot(specs={"villager": spec}, digest="v")
    request = IssuedActionRequest(
        actor_seat=6,
        role_id="villager",
        contract=contract,
        context_revision=23,
        round_number=2,
        phase="night",
        window_id="w",
        action_key="a",
    )

    context = ContextProjector().project(state, request, registry)
    assert context.revision == 23
    assert context.contract_id == contract.contract_id
    assert context.contract_version == contract.schema_version
    assert context.contract_digest == contract.stable_digest()
    restored = ActionContext.from_json(context.to_json())
    assert restored.contract_id == contract.contract_id
    assert restored.contract_version == contract.schema_version
    assert restored.contract_digest == contract.stable_digest()
    assert context.facts["alive_seats"] == (1, 2, 3, 4, 6)
    assert context.facts["phase"] == "night"


def test_rejects_actor_whose_embedded_seat_disagrees_with_request(
    state: GameState, registry: RegistrySnapshot
) -> None:
    state.players[1].seat_number = 99
    with pytest.raises(ValueError, match="seat number"):
        ContextProjector().project(state, _request(registry, 1, "wolf"), registry)


def test_rejects_negative_signed_context_revision(
    state: GameState, registry: RegistrySnapshot
) -> None:
    with pytest.raises(ValueError, match="context revision"):
        ContextProjector().project(
            state,
            _request(registry, 1, "wolf", revision=-1),
            registry,
        )


def test_generic_declared_resources_and_private_data_use_safe_fallbacks(
    state: GameState,
) -> None:
    actor = state.players[6]
    actor.token = 3  # type: ignore[attr-defined]
    actor.revealed_role = "wolf"
    contract = _contract("ACTOR", contract_id="generic-action")
    spec = RoleSpec(
        role_id="villager",
        camp_id="good",
        contracts=(contract,),
        visibility_namespaces=frozenset({"ACTOR"}),
        initial_resources={
            "is_alive": False,
            "has_token": 0,
            "role": "registered-default",
            "camp": "registered-camp",
            "future_charge": 2,
        },
        initial_private_data={
            "is_sheriff": False,
            "revealed_role": "registered-hidden",
            "future_fact": [1],
        },
    )
    registry = RegistrySnapshot(specs={"villager": spec}, digest="generic")
    request = IssuedActionRequest(
        actor_seat=6,
        role_id="villager",
        contract=contract,
        context_revision=1,
        round_number=2,
        phase="night",
        window_id="w",
        action_key="a",
    )

    context = ContextProjector().project(state, request, registry)
    assert context.resources == {
        "is_alive": False,
        "has_token": 0,
        "role": "registered-default",
        "camp": "registered-camp",
        "future_charge": 2,
    }
    assert context.facts["is_sheriff"] is False
    assert context.facts["revealed_role"] == "registered-hidden"
    assert context.facts["future_fact"] == (1,)
    assert "wolf" not in context.to_json()


def test_defensive_projection_handles_malformed_public_and_private_records(
    state: GameState,
) -> None:
    state.phase = "night"  # type: ignore[assignment]
    state.speeches.append(object())
    state.players[3].check_results = [
        object(),
        {"target": 2, "camp": "good"},
        {"round": 2},
    ]
    contract = _contract("ACTOR", contract_id="seer-action")
    spec = RoleSpec(
        role_id="seer",
        camp_id="good",
        contracts=(contract,),
        visibility_namespaces=frozenset({"ACTOR"}),
        initial_private_data={"check_results": ()},
    )
    registry = RegistrySnapshot(specs={"seer": spec}, digest="v")
    request = IssuedActionRequest(
        actor_seat=3,
        role_id="seer",
        contract=contract,
        context_revision=1,
        round_number=2,
        phase="night",
        window_id="w",
        action_key="a",
    )

    context = ContextProjector().project(state, request, registry)
    assert context.phase == "night"
    assert context.facts["phase"] == "night"
    assert context.facts["speeches"][-1]["text"] == "mapped speech"
    assert context.facts["private_checks"] == (
        {"target": 2, "camp": "good"},
    )


@pytest.mark.parametrize("field", ["trigger_event", "aggregate_result"])
def test_optional_projection_mappings_reject_non_mapping_values(
    state: GameState,
    registry: RegistrySnapshot,
    field: str,
) -> None:
    kwargs = {field: ["not", "a", "mapping"]}
    with pytest.raises(TypeError, match="exact dict"):
        ContextProjector().project(
            state,
            _request(registry, 1, "wolf"),
            registry,
            **kwargs,  # type: ignore[arg-type]
        )


def test_accepted_summary_requires_mapping_and_private_defaults_are_detached(
    state: GameState, registry: RegistrySnapshot
) -> None:
    with pytest.raises(TypeError, match="accepted command summary"):
        ContextProjector().project(
            state,
            _request(registry, 1, "wolf"),
            registry,
            accepted_command_summaries=("not-a-mapping",),
        )

    contract = _contract("ACTOR", contract_id="mapping-private-action")
    spec = RoleSpec(
        role_id="villager",
        camp_id="good",
        contracts=(contract,),
        visibility_namespaces=frozenset({"ACTOR"}),
        initial_private_data={"future_mapping": {"seats": [1]}},
    )
    private_registry = RegistrySnapshot(specs={"villager": spec}, digest="v")
    request = IssuedActionRequest(
        actor_seat=6,
        role_id="villager",
        contract=contract,
        context_revision=0,
        round_number=2,
        phase="night",
        window_id="w",
        action_key="a",
    )
    context = ContextProjector().project(state, request, private_registry)
    assert context.facts["future_mapping"] == {"seats": (1,)}


def test_legacy_resource_aliases_are_the_only_player_attribute_reads(
    state: GameState,
) -> None:
    contract = _contract("ACTOR", contract_id="legacy-action")
    spec = RoleSpec(
        role_id="witch",
        camp_id="good",
        contracts=(contract,),
        visibility_namespaces=frozenset({"ACTOR"}),
        initial_resources={
            "antidote": False,
            "has_poison": True,
            "gun": False,
            "role": "default-role",
        },
    )
    registry = RegistrySnapshot(specs={"witch": spec}, digest="legacy")
    state.players[4].has_antidote = True
    state.players[4].has_poison = False
    state.players[4].has_gun = True

    context = ContextProjector().project(
        state, _request(registry, 4, "witch"), registry
    )

    assert context.resources == {
        "antidote": True,
        "has_poison": False,
        "gun": True,
        "role": "default-role",
    }


def test_canonical_runtime_resources_override_legacy_even_when_zero(
    state: GameState,
) -> None:
    contract = _contract("ACTOR", contract_id="runtime-action")
    spec = RoleSpec("witch", camp_id="good", contracts=(contract,),
                    visibility_namespaces=frozenset({"ACTOR"}),
                    initial_resources={"antidote": 0, "poison": 1, "future": 7})
    registry = RegistrySnapshot({"witch": spec}, "a" * 64)
    state.players = {4: state.players[4]}; state.players[4].has_antidote = True
    initialize_role_resources(state, registry.specs, registry.digest)
    state._pipeline_runtime.role_resources[4].pop("future")
    projected = ContextProjector().project(state, _request(registry, 4, "witch", revision=1), registry)
    assert projected.resources == {"antidote": 0, "poison": 1, "future": 7}
    state._pipeline_runtime.role_resources[4]["antidote"] = 9
    assert projected.resources["antidote"] == 0


def test_action_counts_are_projected_by_exact_actor_contract_window_and_round(
    state: GameState, registry: RegistrySnapshot,
) -> None:
    from app.core.effect_applier import _Runtime

    request = _request(registry, 1, "wolf")
    state._pipeline_runtime = _Runtime(action_counts={
        "window": {f"1\0wolf-action\0{request.window_id}": 2, "2\0wolf-action\0x": 9},
        "round": {f"1\0wolf-action\0{request.round_number}": 3},
        "game": {"1\0wolf-action": 4},
    })
    projected = ContextProjector().project(state, request, registry)
    assert projected.counters == {"window": 2, "round": 3, "game": 4}
    explicit = ContextProjector().project(state, request, registry, counters={"window": 8})
    assert explicit.counters == {"window": 8}


def test_action_counter_view_is_zeroed_strict_immutable_and_validates_runtime(
    state: GameState,
) -> None:
    empty = role_action_counters(state, 1, "c", "w", 0)
    assert empty == {"window": 0, "round": 0, "game": 0}
    with pytest.raises(TypeError): empty["game"] = 1
    for arguments in ((object(), 1, "c", "w", 0), (state, True, "c", "w", 0), (state, 1, 1, "w", 0), (state, 1, "", "w", 0), (state, 1, "c", "w", -1)):
        with pytest.raises((TypeError, ValueError)): role_action_counters(*arguments)
    state._pipeline_runtime = object()
    with pytest.raises(Exception): role_action_counters(state, 1, "c", "w", 0)
    with pytest.raises(ValueError): role_action_counters(GameState("g"), 1, "c", "\ud800", 0)
    assert ContextProjector._project_counters(None) == {}


def test_camp_identity_knowledge_includes_dead_members(
    state: GameState, registry: RegistrySnapshot
) -> None:
    state.players[2].is_alive = False

    context = ContextProjector().project(
        state, _request(registry, 1, "wolf"), registry
    )

    assert context.facts["camp_members"] == (1, 2)


def test_public_history_never_executes_arbitrary_to_dict_and_is_bounded(
    state: GameState, registry: RegistrySnapshot
) -> None:
    class Bomb:
        def to_dict(self) -> object:
            raise AssertionError("untrusted to_dict executed")

    state.speeches = [
        SpeechRecord(index, f"speech-{index}", index) for index in range(1, 23)
    ] + [
        Bomb(),
        {"player_seat": True, "text": "bad seat", "round_number": 2},
        {"player_seat": 1, "text": "\ud800", "round_number": 2},
        {"player_seat": 1, "text": "x" * 2001, "round_number": 2},
    ]
    state.votes = [VoteAction(index, 1) for index in range(1, 23)] + [
        Bomb(),
        {"voter_seat": 1, "target_seat": False, "round_number": 2},
        {"voter_seat": 1, "target_seat": 2, "round_number": -1},
    ]

    context = ContextProjector().project(
        state, _request(registry, 6, "villager"), registry
    )

    assert len(context.facts["speeches"]) == 16
    assert context.facts["speeches"][0]["text"] == "speech-7"
    assert context.facts["speeches"][-1]["text"] == "speech-22"
    assert len(context.facts["votes"]) == 17
    assert context.facts["votes"][0]["voter_seat"] == 6
    assert context.facts["votes"][-1]["voter_seat"] == 22


def test_private_checks_are_semantically_valid_and_bounded(
    state: GameState,
) -> None:
    valid = [
        {"target": index, "camp": "good" if index % 2 else "werewolf"}
        for index in range(1, 23)
    ]
    state.players[3].check_results = valid + [
        object(),
        {"target": True, "camp": "good"},
        {"target": 1, "camp": "secret-seer"},
        {"target": 0, "camp": "third_party"},
    ]
    contract = _contract("ACTOR", contract_id="seer-action")
    spec = RoleSpec(
        role_id="seer",
        camp_id="good",
        contracts=(contract,),
        visibility_namespaces=frozenset({"ACTOR"}),
        initial_private_data={"check_results": ()},
    )
    registry = RegistrySnapshot(specs={"seer": spec}, digest="checks")

    context = ContextProjector().project(
        state, _request(registry, 3, "seer"), registry
    )

    assert len(context.facts["private_checks"]) == 16
    assert context.facts["private_checks"][0] == {"target": 7, "camp": "good"}
    assert context.facts["private_checks"][-1] == {
        "target": 22,
        "camp": "werewolf",
    }


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"source_event_id": "x" * 129}, "source_event_id"),
        ({"source_event_id": 1}, "source_event_id"),
        ({"trigger_event": {"event_id": "x" * 129}}, "event_id"),
        ({"trigger_event": {"type": "secret_event"}}, "event type"),
        ({"trigger_event": {"cause": "the seer is seat 3"}}, "cause"),
        ({"trigger_event": {"source_seat": True}}, "source_seat"),
        ({"trigger_event": {"target_seat": 0}}, "target_seat"),
        ({"trigger_event": {"round_number": -1}}, "round_number"),
        ({"trigger_event": {"phase": "secret"}}, "phase"),
        (
            {"accepted_command_summaries": ({"contract_id": "other"},)},
            "contract_id",
        ),
        (
            {"accepted_command_summaries": ({"action_type": "secret"},)},
            "action_type",
        ),
        (
            {"accepted_command_summaries": ({"actor_seat": False},)},
            "actor_seat",
        ),
        ({"aggregate_result": {"count": -1}}, "count"),
        ({"aggregate_result": {"tied": 1}}, "tied"),
        ({"aggregate_result": {"selected_seat": True}}, "selected_seat"),
    ],
)
def test_response_projection_rejects_semantically_invalid_values(
    state: GameState,
    registry: RegistrySnapshot,
    kwargs: dict[str, object],
    error: str,
) -> None:
    contract = _contract(
        "ACTOR", contract_id="wolf-response",
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"wolf_kill"}),
    )
    spec = RoleSpec(
        role_id="wolf", camp_id="werewolf", contracts=(contract,),
        visibility_namespaces=frozenset({"ACTOR"}),
    )
    response_registry = RegistrySnapshot(specs={"wolf": spec}, digest="response")
    normalized = dict(kwargs)
    if "trigger_event" in normalized:
        trigger = dict(normalized["trigger_event"])
        trigger.setdefault("event_id", "event:0123456789abcdef")
        trigger.setdefault("type", "PLAYER_DIED")
        normalized["trigger_event"] = trigger
        normalized["source_event_id"] = "event:0123456789abcdef"
    if "accepted_command_summaries" in normalized:
        summary = dict(normalized["accepted_command_summaries"][0])  # type: ignore[index]
        summary.setdefault("contract_id", "wolf-response")
        summary.setdefault("action_type", "act")
        normalized["accepted_command_summaries"] = (summary,)
    if "aggregate_result" in normalized and type(normalized["aggregate_result"]) is dict:
        aggregate = dict(normalized["aggregate_result"])
        aggregate.setdefault("contract_id", "wolf-response")
        aggregate.setdefault("action_type", "act")
        normalized["aggregate_result"] = aggregate
    with pytest.raises((TypeError, ValueError), match=error):
        ContextProjector().project(
            state,
            _request(response_registry, 1, "wolf"),
            response_registry,
            **normalized,  # type: ignore[arg-type]
        )


def test_response_event_and_reason_are_bound_to_contract_declarations(
    state: GameState,
) -> None:
    contract = ActionContract(
        contract_id="hunter-response",
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=10,
        action_types=("act", "pass"),
        actions_requiring_target=frozenset({"act"}),
        fallback_action_type="pass",
        visibility_namespaces=frozenset({"ACTOR"}),
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"poison"}),
    )
    spec = RoleSpec(
        role_id="hunter",
        camp_id="good",
        contracts=(contract,),
        visibility_namespaces=frozenset({"ACTOR"}),
    )
    registry = RegistrySnapshot(specs={"hunter": spec}, digest="response")
    request = IssuedActionRequest(
        actor_seat=5,
        role_id="hunter",
        contract=contract,
        context_revision=1,
        round_number=2,
        phase="night",
        window_id="w",
        action_key="a",
    )

    context = ContextProjector().project(
        state,
        request,
        registry,
        source_event_id="event:0123456789abcdef",
        trigger_event={
            "event_id": "event:0123456789abcdef",
            "type": "PLAYER_DIED",
            "cause": "poison",
        },
        trigger_reason="poison",
    )
    assert context.trigger_reason == "poison"

    with pytest.raises(ValueError, match="event type"):
        ContextProjector().project(
            state,
            request,
            registry,
            source_event_id="event:0123456789abcdef",
            trigger_event={
                "event_id": "event:0123456789abcdef", "type": "player_died"
            },
        )
    with pytest.raises(ValueError, match="trigger reason"):
        ContextProjector().project(
            state, request, registry, trigger_reason="wolf_kill"
        )


def test_response_payload_requires_an_explicit_response_contract(
    state: GameState, registry: RegistrySnapshot
) -> None:
    request = _request(registry, 1, "wolf")
    with pytest.raises(ValueError, match="response event"):
        ContextProjector().project(
            state, request, registry,
            source_event_id="event:0123456789abcdef",
            trigger_event={
                "event_id": "event:0123456789abcdef", "type": "PLAYER_DIED"
            },
        )
    with pytest.raises(ValueError, match="response reason"):
        ContextProjector().project(
            state, request, registry, trigger_reason="wolf_kill"
        )
    with pytest.raises(ValueError, match="paired"):
        ContextProjector().project(
            state, request, registry, source_event_id="event-1"
        )


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"source_event_id": "the-seer-is-seat-3"}, "opaque event id"),
        ({"trigger_event": {"event_id": "the-seer-is-seat-3"}}, "opaque event id"),
        (
            {"accepted_command_summaries": (
                {"contract_id": "wolf-response", "action_type": "the seer is seat 3"},
            )},
            "stable token",
        ),
    ],
)
def test_response_identifiers_reject_secret_shaped_text(
    state: GameState, kwargs: dict[str, object], error: str
) -> None:
    contract = _contract(
        "ACTOR", contract_id="wolf-response",
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"wolf_kill"}),
    )
    spec = RoleSpec(
        role_id="wolf", camp_id="werewolf", contracts=(contract,),
        visibility_namespaces=frozenset({"ACTOR"}),
    )
    registry = RegistrySnapshot(specs={"wolf": spec}, digest="response")
    normalized = dict(kwargs)
    if "source_event_id" in normalized:
        normalized["trigger_event"] = {
            "event_id": normalized["source_event_id"], "type": "PLAYER_DIED"
        }
    elif "trigger_event" in normalized:
        normalized["trigger_event"] = {
            **normalized["trigger_event"],  # type: ignore[dict-item]
            "type": "PLAYER_DIED",
        }
        normalized["source_event_id"] = normalized["trigger_event"]["event_id"]  # type: ignore[index]
    with pytest.raises(ValueError, match=error):
        ContextProjector().project(
            state, _request(registry, 1, "wolf"), registry, **normalized  # type: ignore[arg-type]
        )


def test_response_summaries_are_bounded_and_require_identity_fields(
    state: GameState, registry: RegistrySnapshot
) -> None:
    request = _request(registry, 1, "wolf")
    valid = {"contract_id": "wolf-action", "action_type": "act"}
    with pytest.raises(ValueError, match="at most 64"):
        ContextProjector().project(
            state, request, registry, accepted_command_summaries=(valid,) * 65
        )
    for summary in ({"action_type": "act"}, {"contract_id": "wolf-action"}):
        with pytest.raises(ValueError, match="required"):
            ContextProjector().project(
                state, request, registry, accepted_command_summaries=(summary,)
            )
    for aggregate in ({"action_type": "act"}, {"contract_id": "wolf-action"}):
        with pytest.raises(ValueError, match="required"):
            ContextProjector().project(
                state, request, registry, aggregate_result=aggregate
            )


def test_projected_integers_have_a_32_bit_bound(
    state: GameState, registry: RegistrySnapshot
) -> None:
    huge = 10**5000
    state.speeches.append(
        {"player_seat": huge, "text": "ignored", "round_number": 2}
    )
    state.players[3].check_results.append({"target": huge, "camp": "good"})
    public = ContextProjector().project(
        state, _request(registry, 6, "villager"), registry
    )
    private = ContextProjector().project(
        state, _request(registry, 3, "seer"), registry
    )
    assert all(item["text"] != "ignored" for item in public.facts["speeches"])
    assert all(item["target"] != huge for item in private.facts["private_checks"])

    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector().project(
            state,
            _request(registry, 1, "wolf"),
            registry,
            accepted_command_summaries=(
                {"contract_id": "wolf-action", "action_type": "act", "actor_seat": huge},
            ),
        )


def test_projection_never_executes_custom_mapping_methods(
    state: GameState, registry: RegistrySnapshot
) -> None:
    class BombMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise AssertionError("custom mapping executed")

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("custom mapping executed")

        def __len__(self) -> int:
            raise AssertionError("custom mapping executed")

    bomb = BombMapping()
    state.speeches.append(bomb)
    state.votes.append(bomb)
    state.players[3].check_results.append(bomb)  # type: ignore[arg-type]
    ContextProjector().project(state, _request(registry, 3, "seer"), registry)

    contract = _contract(
        "ACTOR", contract_id="wolf-response",
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"wolf_kill"}),
    )
    response_registry = RegistrySnapshot(
        specs={
            "wolf": RoleSpec(
                role_id="wolf", camp_id="werewolf", contracts=(contract,),
                visibility_namespaces=frozenset({"ACTOR"}),
            )
        }, digest="response"
    )

    for kwargs, error in (
        ({"trigger_event": bomb}, "trigger_event"),
        ({"accepted_command_summaries": (bomb,)}, "accepted command summary"),
        ({"aggregate_result": bomb}, "aggregate_result"),
    ):
        with pytest.raises(TypeError, match=error):
            ContextProjector().project(
                state,
                _request(response_registry, 1, "wolf"),
                response_registry,
                **kwargs,  # type: ignore[arg-type]
            )


def test_response_sequence_type_and_remaining_numeric_bounds(
    state: GameState,
) -> None:
    contract = _contract(
        "ACTOR", contract_id="wolf-response",
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"wolf_kill"}),
    )
    registry = RegistrySnapshot(
        specs={
            "wolf": RoleSpec(
                role_id="wolf", camp_id="werewolf", contracts=(contract,),
                visibility_namespaces=frozenset({"ACTOR"}),
            )
        }, digest="response"
    )
    request = _request(registry, 1, "wolf")
    with pytest.raises(TypeError, match="sized sequence"):
        ContextProjector().project(
            state, request, registry, accepted_command_summaries=iter(())  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="trigger_event"):
        ContextProjector._project_trigger_event(object(), contract)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cause"):
        ContextProjector().project(
            state, request, registry,
            source_event_id="event:0123456789abcdef",
            trigger_event={
                "event_id": "event:0123456789abcdef", "type": "PLAYER_DIED",
                "cause": "exile",
            },
        )
    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector().project(
            state,
            request,
            registry,
            aggregate_result={
                "contract_id": "wolf-response", "action_type": "act",
                "count": 2_147_483_648,
            },
        )


def test_identifier_and_integer_helpers_reject_uncovered_invalid_shapes() -> None:
    with pytest.raises(TypeError, match="string"):
        ContextProjector._text(1, "event type", 64)
    with pytest.raises(ValueError, match="stable token"):
        ContextProjector._token("INVALID", "contract_id", 128)
    with pytest.raises(ValueError, match="event type"):
        ContextProjector._event_token("invalid event", "event type", 64)
    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector._nonnegative_int(2_147_483_648, "count")


def test_remaining_projected_integer_boundaries_are_checked(
    state: GameState, registry: RegistrySnapshot
) -> None:
    request = _request(registry, 1, "wolf")
    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector().project(
            state, request, registry, counters={"window": 2_147_483_648}
        )
    with pytest.raises(TypeError, match="counters"):
        ContextProjector().project(
            state, request, registry, counters=MappingProxyType({"window": 1})
        )

    state.sheriff = 2_147_483_648
    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector().project(state, request, registry)
    state.sheriff = 1

    state.players[1].seat_number = 2_147_483_648
    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector().project(state, request, registry)
    state.players[1].seat_number = 1

    state.players[1].seat_number = 0
    with pytest.raises(ValueError, match="positive"):
        ContextProjector().project(state, request, registry)
    state.players[1].seat_number = 1

    state.round_number = 2_147_483_648
    huge_round_request = IssuedActionRequest(
        actor_seat=1, role_id="wolf", contract=request.contract,
        context_revision=1, round_number=2_147_483_648, phase="night",
        window_id="w", action_key="a",
    )
    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector().project(state, huge_round_request, registry)
    state.round_number = 2

    huge_revision_request = IssuedActionRequest(
        actor_seat=1, role_id="wolf", contract=request.contract,
        context_revision=2_147_483_648, round_number=2, phase="night",
        window_id="w", action_key="a",
    )
    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector().project(state, huge_revision_request, registry)


def test_witch_target_seat_is_bounded(
    state: GameState, registry: RegistrySnapshot
) -> None:
    state.last_wolf_kill_target = 2_147_483_648
    with pytest.raises(ValueError, match="32-bit"):
        ContextProjector().project(
            state, _request(registry, 4, "witch"), registry
        )


def test_response_event_id_has_a_fixed_opaque_prefix(
    state: GameState,
) -> None:
    contract, registry, request = _response_fixture(state)
    with pytest.raises(ValueError, match="opaque event id"):
        ContextProjector().project(
            state,
            request,
            registry,
            source_event_id="seer_is_seat_3:0123456789abcdef",
            trigger_event={
                "event_id": "seer_is_seat_3:0123456789abcdef",
                "type": "PLAYER_DIED",
            },
        )


@pytest.mark.parametrize(
    "source_event_id,trigger_event,error",
    [
        ("event:0123456789abcdef", None, "paired"),
        (None, {"event_id": "event:0123456789abcdef", "type": "PLAYER_DIED"}, "paired"),
        ("event:0123456789abcdef", {}, "event_id.*required"),
        (
            "event:0123456789abcdef",
            {"event_id": "event:0123456789abcdef"},
            "type.*required",
        ),
        (
            "event:0123456789abcdef",
            {"event_id": "event:fedcba9876543210", "type": "PLAYER_DIED"},
            "must match",
        ),
    ],
)
def test_response_source_and_trigger_are_complete_and_bound(
    state: GameState,
    source_event_id: str | None,
    trigger_event: dict[str, object] | None,
    error: str,
) -> None:
    _contract_value, registry, request = _response_fixture(state)
    with pytest.raises(ValueError, match=error):
        ContextProjector().project(
            state,
            request,
            registry,
            source_event_id=source_event_id,
            trigger_event=trigger_event,
        )


def _response_fixture(
    state: GameState,
) -> tuple[ActionContract, RegistrySnapshot, IssuedActionRequest]:
    contract = _contract(
        "ACTOR", contract_id="wolf-response",
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"wolf_kill"}),
    )
    registry = RegistrySnapshot(
        specs={
            "wolf": RoleSpec(
                role_id="wolf", camp_id="werewolf", contracts=(contract,),
                visibility_namespaces=frozenset({"ACTOR"}),
            )
        }, digest="response"
    )
    return contract, registry, _request(registry, 1, "wolf")
