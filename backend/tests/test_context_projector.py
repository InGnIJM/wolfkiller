from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from app.core.context_projector import ContextProjector
from app.models.actions import SpeechRecord, VoteAction
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionContract,
    IssuedActionRequest,
    RoleSpec,
    SchedulePoint,
)
from app.roles.registry import RegistrySnapshot


def _contract(*namespaces: str, contract_id: str = "night-action") -> ActionContract:
    return ActionContract(
        contract_id=contract_id,
        schedule_point=SchedulePoint.NIGHT_ACTION,
        order=10,
        action_types=("act", "pass"),
        actions_requiring_target=frozenset({"act"}),
        fallback_action_type="pass",
        visibility_namespaces=frozenset(namespaces),
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
    context = ContextProjector().project(
        state,
        _request(registry, 1, "wolf"),
        registry,
        trigger_event={"type": "PLAYER_DIED", "target_seat": 5},
        trigger_reason="wolf_kill",
        source_event_id="event-1",
        accepted_command_summaries=({"action_type": "act", "seats": [6]},),
        aggregate_result={"target_seat": 6, "votes": [1, 2]},
        counters={"window": 1},
    )

    assert isinstance(context.facts, MappingProxyType)
    assert isinstance(context.trigger_event, MappingProxyType)
    assert context.trigger_event == {"type": "PLAYER_DIED", "target_seat": 5}
    assert context.accepted_command_summaries == ({"action_type": "act"},)
    assert context.aggregate_result == {"target_seat": 6}
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

    projected = projector.project(
        state,
        request,
        registry,
        trigger_event={
            "event_id": "event-2",
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
                "contract_id": "seer-action",
                "action_type": "act",
                "target_seat": 1,
                "revealed_role": "wolf",
                "nested": {"has_poison": True},
            },
        ),
        aggregate_result={
            "contract_id": "seer-action",
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
        "event_id": "event-2",
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
            "contract_id": "seer-action",
            "action_type": "act",
            "target_seat": 1,
        },
    )
    assert projected.aggregate_result == {
        "contract_id": "seer-action",
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
    with pytest.raises(TypeError, match="mapping"):
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
    with pytest.raises((TypeError, ValueError), match=error):
        ContextProjector().project(
            state,
            _request(registry, 1, "wolf"),
            registry,
            **kwargs,  # type: ignore[arg-type]
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
        trigger_event={"type": "PLAYER_DIED", "cause": "poison"},
        trigger_reason="poison",
    )
    assert context.trigger_reason == "poison"

    with pytest.raises(ValueError, match="event type"):
        ContextProjector().project(
            state,
            request,
            registry,
            trigger_event={"type": "player_died"},
        )
    with pytest.raises(ValueError, match="trigger reason"):
        ContextProjector().project(
            state, request, registry, trigger_reason="wolf_kill"
        )
