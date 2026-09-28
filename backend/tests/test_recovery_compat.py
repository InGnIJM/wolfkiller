"""Compatibility verdicts for a resume: exact / compatible / drift / incompatible."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from app.core.effect_applier import _Runtime
from app.core.point_journal import PointCheckpoint, PointKey, WorkCursor, point_journal
from app.core.role_runtime import initialize_role_resources, resource_declaration_marker
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import IssuedActionRequest, SchedulePoint
from app.persistence.checkpoint_codec import CheckpointCodec
from app.persistence.recovery_compat import (
    COMPATIBLE, CONTRACT_DIGEST_DRIFT, CONTRACT_INCOMPATIBLE, DRIFT, EXACT,
    INCOMPATIBLE, REGISTRY_IDENTITY_UNKNOWN, REGISTRY_INCOMPATIBLE,
    ROLE_DECLARATION_DRIFT, RecoveryAssessment, assess,
)
from app.roles.registry import RegistrySnapshot, builtin_registry

WEREWOLF = "wolf-killer-werewolf"
WITCH = "wolf-killer-witch"
VILLAGER = "wolf-killer-villager"


def registry() -> RegistrySnapshot:
    return builtin_registry.freeze()


def drifted(registry: RegistrySnapshot, *, digest: str = "b" * 64, **specs) -> RegistrySnapshot:
    """The same registry, but with the given role specs replaced."""
    table = dict(registry.specs)
    table.update(specs)
    return RegistrySnapshot(table, digest)


def game(registry: RegistrySnapshot) -> GameState:
    state = GameState(
        game_id="g", phase=GamePhase.NIGHT, round_number=1,
        config=GameConfig(role_counts={WEREWOLF: 1, WITCH: 1, VILLAGER: 1}, reveal_on_death=True),
        players={
            1: PlayerState(1, WEREWOLF, "werewolf"),
            2: PlayerState(2, WITCH, "good"),
            3: PlayerState(3, VILLAGER, "good"),
        },
        pipeline_version="v2", registry_digest=registry.digest,
        spec_versions={role_id: spec.schema_version for role_id, spec in registry.specs.items()},
        effect_schema_version=1,
    )
    initialize_role_resources(state, registry.specs, registry.digest)
    # A journalled point for the witch, so the document records the contract the
    # game actually used — the only place a stored contract digest survives.
    contract = registry.require(WITCH).contracts[0]
    request = IssuedActionRequest(2, WITCH, contract, 0, 1, "night", "window-w", "action-w")
    point_journal(state).put(
        PointKey(state.game_id, 1, "night", SchedulePoint.NIGHT_WITCH_ACTION, registry.digest),
        PointCheckpoint((request,), (request,), (), (), (), (), WorkCursor("main", 0, 0), work_count=1),
    )
    return state


def document(registry: RegistrySnapshot, state: GameState) -> dict:
    codec = CheckpointCodec(registry)
    return copy.deepcopy(codec.encode(state, orchestration={}))


def test_an_unchanged_registry_is_exact() -> None:
    live = registry()
    state = game(live)
    verdict = assess(document(live, state), state, live)
    assert verdict == RecoveryAssessment(EXACT)
    assert verdict.resumable and not verdict.needs_confirmation


def test_an_unrelated_registry_change_is_compatible() -> None:
    """Adding a role the game does not use must not block the resume."""
    old = registry()
    state = game(old)
    doc = document(old, state)
    new = drifted(old, **{VILLAGER: replace(old.require(VILLAGER), display_name="村民（新）")})
    verdict = assess(doc, state, new)
    assert verdict.level == COMPATIBLE and verdict.code is None
    assert verdict.resumable and not verdict.needs_confirmation


def test_a_changed_used_contract_digest_is_drift() -> None:
    old = registry()
    state = game(old)
    doc = document(old, state)
    spec = old.require(WITCH)
    contract = spec.contracts[0]
    new = drifted(old, **{WITCH: replace(spec, contracts=(replace(contract, order=contract.order + 1),))})
    verdict = assess(doc, state, new)
    assert verdict.level == DRIFT and verdict.code == CONTRACT_DIGEST_DRIFT
    assert verdict.needs_confirmation and verdict.resumable
    assert f"contract_digest:{contract.contract_id}" in verdict.reasons


def test_a_changed_resource_declaration_is_drift() -> None:
    old = registry()
    state = game(old)
    doc = document(old, state)
    spec = old.require(WITCH)
    new = drifted(old, **{WITCH: replace(spec, initial_resources={**spec.initial_resources, "new_potion": 1})})
    verdict = assess(doc, state, new)
    assert verdict.level == DRIFT and verdict.code == ROLE_DECLARATION_DRIFT
    assert verdict.reasons == ("resource_declaration",)


def test_a_dropped_resource_declaration_is_drift_too() -> None:
    old = registry()
    state = game(old)
    doc = document(old, state)
    spec = old.require(WITCH)
    new = drifted(old, **{WITCH: replace(spec, initial_resources={})})
    assert assess(doc, state, new).code == ROLE_DECLARATION_DRIFT


def test_a_newly_declared_resource_is_drift() -> None:
    old = registry()
    state = game(old)
    doc = document(old, state)
    spec = old.require(VILLAGER)
    new = drifted(old, **{VILLAGER: replace(spec, initial_resources={"token": 1})})
    assert assess(doc, state, new).code == ROLE_DECLARATION_DRIFT


def test_a_missing_seated_role_is_incompatible() -> None:
    old = registry()
    state = game(old)
    doc = document(old, state)
    table = dict(old.specs)
    table.pop(WITCH)
    verdict = assess(doc, state, RegistrySnapshot(table, "b" * 64))
    assert verdict.level == INCOMPATIBLE and verdict.code == REGISTRY_INCOMPATIBLE
    assert not verdict.resumable and not verdict.needs_confirmation
    assert verdict.reasons == (f"role_missing:{WITCH}",)


def test_a_journaled_role_the_game_no_longer_seats_is_incompatible() -> None:
    """Committed requests are durable truth: the registry must cover them too."""
    old = registry()
    state = game(old)
    del state.players[1]
    contract = old.require(WEREWOLF).contracts[0]
    request = IssuedActionRequest(1, WEREWOLF, contract, 0, 1, "night", "window-w", "action-w")
    point_journal(state).put(
        PointKey(state.game_id, 1, "night", SchedulePoint.NIGHT_WOLF_VOTE, old.digest),
        PointCheckpoint((request,), (request,), (), (), (), (), WorkCursor("main", 0, 0), work_count=1),
    )
    doc = document(old, state)
    table = dict(old.specs)
    table.pop(WEREWOLF)
    verdict = assess(doc, state, RegistrySnapshot(table, "b" * 64))
    assert verdict.level == INCOMPATIBLE and verdict.code == REGISTRY_INCOMPATIBLE
    assert verdict.reasons == (f"role_missing:{WEREWOLF}",)


def test_a_missing_used_contract_is_incompatible() -> None:
    old = registry()
    state = game(old)
    doc = document(old, state)
    spec = old.require(WITCH)
    new = drifted(old, **{WITCH: replace(spec, contracts=())})
    verdict = assess(doc, state, new)
    assert verdict.code == REGISTRY_INCOMPATIBLE
    assert verdict.reasons == (f"contract_missing:{WITCH}:{spec.contracts[0].contract_id}",)


def test_a_bumped_contract_schema_version_is_incompatible() -> None:
    """An archive written under a newer contract schema cannot be continued."""
    old = registry()
    state = game(old)
    doc = document(old, state)
    doc["point_journal"][0]["checkpoint"]["issued"][0]["contract_version"] = 2
    contract = old.require(WITCH).contracts[0]
    verdict = assess(doc, state, old)
    assert verdict.level == INCOMPATIBLE and verdict.code == CONTRACT_INCOMPATIBLE
    assert verdict.reasons == (f"contract_schema_version:{contract.contract_id}:2:1",)


def test_a_bumped_role_spec_version_is_incompatible() -> None:
    old = registry()
    state = game(old)
    doc = document(old, state)
    state.spec_versions[WITCH] = 7
    verdict = assess(doc, state, old)
    assert verdict.level == INCOMPATIBLE and verdict.code == REGISTRY_INCOMPATIBLE
    assert verdict.reasons == (f"role_spec_version:{WITCH}:7:2",)


def test_a_request_from_a_role_the_registry_lost_is_incompatible() -> None:
    old = registry()
    state = game(old)
    doc = document(old, state)
    doc["point_journal"].append(copy.deepcopy(doc["point_journal"][0]))
    doc["point_journal"][-1]["checkpoint"]["issued"][0]["role_id"] = VILLAGER
    table = dict(old.specs)
    table.pop(VILLAGER)
    verdict = assess(doc, state, RegistrySnapshot(table, "b" * 64))
    assert verdict.code == REGISTRY_INCOMPATIBLE


def test_an_unknown_identity_requires_confirmation() -> None:
    live = registry()
    state = game(live)
    doc = document(live, state)
    state.registry_digest = ""
    verdict = assess(doc, state, live)
    assert verdict.level == DRIFT and verdict.code == REGISTRY_IDENTITY_UNKNOWN
    assert verdict.resumable and verdict.needs_confirmation


def test_a_malformed_identity_is_treated_as_unknown() -> None:
    live = registry()
    state = game(live)
    doc = document(live, state)
    state.registry_digest = "not-a-digest"
    assert assess(doc, state, live).code == REGISTRY_IDENTITY_UNKNOWN


def test_a_matching_marker_and_identity_ignore_unused_roles_without_resources() -> None:
    """The marker is only meaningful for roles that declare resources."""
    live = registry()
    state = game(live)
    doc = document(live, state)
    marker = resource_declaration_marker(state, live.specs, live.digest)
    assert state._pipeline_runtime.resource_setup_digest == marker
    assert assess(doc, state, live).level == EXACT


def test_a_game_without_declared_resources_has_no_marker() -> None:
    live = registry()
    state = GameState(
        game_id="no-resources", phase=GamePhase.NIGHT, round_number=1,
        players={1: PlayerState(1, VILLAGER, "good")},
        pipeline_version="v2", registry_digest=live.digest,
        spec_versions={role_id: spec.schema_version for role_id, spec in live.specs.items()},
        effect_schema_version=1,
    )
    assert initialize_role_resources(state, live.specs, live.digest) is None
    doc = document(live, state)
    assert assess(doc, state, live).level == EXACT


def test_a_marker_survives_a_document_round_trip() -> None:
    live = registry()
    state = game(live)
    doc = document(live, state)
    restored, _ = CheckpointCodec(live).decode(doc)
    assert assess(doc, restored, live).level == EXACT


def test_a_tolerant_reader_ignores_a_malformed_journal() -> None:
    live = registry()
    state = game(live)
    doc = document(live, state)
    doc["point_journal"] = [None, {"checkpoint": None}, {"checkpoint": {"issued": None}},
                            {"checkpoint": {"issued": [None, {"role_id": 1, "contract_id": 2}]}}]
    assert assess(doc, state, live).level == EXACT
    doc["point_journal"] = "nonsense"
    assert assess(doc, state, live).level == EXACT


def test_assess_validates_its_inputs() -> None:
    live = registry()
    state = game(live)
    doc = document(live, state)
    with pytest.raises(TypeError, match="document must be a mapping"):
        assess(None, state, live)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="state must be GameState"):
        assess(doc, object(), live)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="registry must be RegistrySnapshot"):
        assess(doc, state, object())  # type: ignore[arg-type]


def test_verdicts_are_frozen_and_validated() -> None:
    verdict = RecoveryAssessment(COMPATIBLE)
    assert verdict.reasons == ()
    with pytest.raises(ValueError, match="invalid recovery level"):
        RecoveryAssessment("maybe")
    with pytest.raises(ValueError, match="invalid recovery code"):
        RecoveryAssessment(DRIFT, "")
    with pytest.raises(ValueError, match="invalid recovery code"):
        RecoveryAssessment(DRIFT, 7)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid recovery reasons"):
        RecoveryAssessment(DRIFT, ROLE_DECLARATION_DRIFT, ["why"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid recovery reasons"):
        RecoveryAssessment(DRIFT, ROLE_DECLARATION_DRIFT, ("",))
    with pytest.raises(ValueError, match="requires a code"):
        RecoveryAssessment(DRIFT)
    with pytest.raises(ValueError, match="requires a code"):
        RecoveryAssessment(INCOMPATIBLE)
    with pytest.raises(Exception):
        verdict.level = COMPATIBLE  # type: ignore[misc]


def test_a_runtime_without_a_marker_is_drift() -> None:
    live = registry()
    state = game(live)
    doc = document(live, state)
    state._pipeline_runtime = _Runtime(revision=0)
    assert assess(doc, state, live).code == ROLE_DECLARATION_DRIFT
