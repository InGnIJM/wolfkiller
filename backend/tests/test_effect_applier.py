from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from app.core.effect_applier import (
    CommitResult,
    EffectApplier,
    EffectPermission,
    EffectRejected,
    derive_effect_id,
)
from app.models.game import GameState, PlayerState
from app.models.pipeline import EffectKind, GameEffect


def state() -> GameState:
    return GameState(
        game_id="g",
        players={
            1: PlayerState(1, "witch", "good"),
            2: PlayerState(2, "wolf", "werewolf"),
            3: PlayerState(3, "villager", "good"),
        },
    )


ALL = frozenset(EffectKind)


def permission(**changes: object) -> EffectPermission:
    values: dict[str, object] = {
        "actor_seat": 1,
        "role_effects": ALL,
        "contract_effects": ALL,
        "allowed_targets": frozenset({1, 2, 3}),
        "allowed_visibility": frozenset({"PUBLIC", "ACTOR", "SERVER_ONLY"}),
    }
    values.update(changes)
    return EffectPermission(**values)


def batch(
    specs: list[tuple[EffectKind, dict[str, object], int | None]],
    *,
    action: str = "a",
    revision: int = 0,
    visibility: tuple[str, ...] = (),
    preconditions: dict[str, object] | None = None,
) -> tuple[GameEffect, ...]:
    rows = [(EffectKind.ACCEPT_ACTION, {}, None), *specs]
    return tuple(
        GameEffect(
            effect_id=derive_effect_id(action, ordinal),
            kind=kind,
            source_action_key=action,
            payload=payload,
            target_seat=target,
            expected_revision=revision,
            visibility=visibility,
            preconditions=preconditions or {},
            sort_key=(ordinal,),
        )
        for ordinal, (kind, payload, target) in enumerate(rows)
    )


def test_types_are_strict_frozen_and_ids_are_stable() -> None:
    assert derive_effect_id("动作", 2) == derive_effect_id("动作", 2)
    assert derive_effect_id("动作", 2) != derive_effect_id("动作", 3)
    for args in ((1, 0), ("a", True), ("a", 0, True), ("a", -1), ("\ud800", 0)):
        with pytest.raises((TypeError, ValueError)):
            derive_effect_id(*args)
    with pytest.raises(ValueError, match="schema_version"):
        derive_effect_id("a", 0, 2)
    p = permission()
    with pytest.raises(FrozenInstanceError):
        p.actor_seat = 2
    with pytest.raises((TypeError, ValueError)):
        EffectPermission(1, frozenset({"bad"}), ALL, frozenset({1}), frozenset())
    with pytest.raises(TypeError, match="EffectKind"):
        permission(role_effects=frozenset({"mark_death"}))
    for kwargs in (
        {"actor_seat": True},
        {"allowed_targets": frozenset({0})},
        {"allowed_targets": {1}},
        {"allowed_visibility": frozenset({"\ud800"})},
        {"schema_version": 2},
    ):
        with pytest.raises((TypeError, ValueError)):
            permission(**kwargs)


def test_applies_all_effect_kinds_and_deep_freezes_result() -> None:
    s = state()
    effects = batch(
        [
            (EffectKind.SET_RESOURCE, {"target": 1, "resource": "antidote", "value": 1}, 1),
            (EffectKind.CONSUME_RESOURCE, {"target": 1, "resource": "antidote", "amount": 1}, 1),
            (EffectKind.SET_PRIVATE_DATA, {"target": 1, "key": "vision", "value": {"seat": 2}}, 1),
            (EffectKind.ADD_STATUS, {"target": 2, "status": "marked"}, 2),
            (EffectKind.REMOVE_STATUS, {"target": 2, "status": "marked"}, 2),
            (EffectKind.ADD_RELATION, {"target": 1, "relation": "known", "other_seat": 2}, 1),
            (EffectKind.REMOVE_RELATION, {"target": 1, "relation": "known", "other_seat": 2}, 1),
            (EffectKind.RECORD_PRIVATE_FACT, {"target": 1, "namespace": "seer", "fact": {"wolf": 2}}, 1),
            (EffectKind.SUBMIT_DAMAGE, {"target": 2, "amount": 1}, 2),
            (EffectKind.SUBMIT_PROTECTION, {"target": 3, "amount": 1}, 3),
            (EffectKind.MARK_DEATH, {"target": 2, "cause": "attack"}, 2),
            (EffectKind.EMIT_EVENT, {"event_type": "ACTION_DONE", "payload": {"seat": 1}}, None),
        ],
        visibility=("PUBLIC",),
    )
    result = EffectApplier().apply(s, effects, permission())
    runtime = s._pipeline_runtime
    assert result.revision == 1 and not s.players[2].is_alive
    assert runtime.role_resources[1]["antidote"] == 0
    assert runtime.private_data[1]["vision"] == {"seat": 2}
    assert runtime.statuses[2] == set() and runtime.relations[1] == set()
    assert runtime.private_facts[1][0]["namespace"] == "seer"
    assert runtime.pending_damage == ({"target": 2, "amount": 1},)
    assert runtime.pending_protection == ({"target": 3, "amount": 1},)
    assert [event["event_type"] for event in result.events] == ["PLAYER_DIED", "ACTION_DONE"]
    assert isinstance(result.events[0], MappingProxyType)
    with pytest.raises(TypeError):
        result.events[0]["event_type"] = "changed"
    with pytest.raises(FrozenInstanceError):
        result.revision = 9


def test_duplicate_action_returns_original_commit_without_writing_twice() -> None:
    s = state()
    effects = batch([
        (EffectKind.SET_RESOURCE, {"target": 1, "resource": "potion", "value": 1}, 1),
        (EffectKind.CONSUME_RESOURCE, {"target": 1, "resource": "potion", "amount": 1}, 1),
    ])
    first = EffectApplier().apply(s, effects, permission())
    second = EffectApplier().apply(s, tuple(reversed(effects)), permission())
    assert second == first
    assert s._pipeline_runtime.revision == 1
    assert s._pipeline_runtime.role_resources[1]["potion"] == 0


def test_stable_sorting_digest_and_tie_breaker() -> None:
    def make(order: tuple[int, int]) -> tuple[GameEffect, ...]:
        raw = [
            (EffectKind.ACCEPT_ACTION, {}, None, (0,)),
            (EffectKind.EMIT_EVENT, {"event_type": "B", "payload": {}}, None, (1,)),
            (EffectKind.EMIT_EVENT, {"event_type": "A", "payload": {}}, None, (1,)),
        ]
        stable = [raw[0], raw[2], raw[1]]
        built = [
            GameEffect(derive_effect_id("stable", i), k, "stable", payload=p, target_seat=t, sort_key=sk)
            for i, (k, p, t, sk) in enumerate(stable)
        ]
        return (built[0], built[order[0]], built[order[1]])
    r1 = EffectApplier().apply(state(), make((1, 2)), permission())
    r2 = EffectApplier().apply(state(), make((2, 1)), permission())
    assert r1 == r2
    assert [e["event_type"] for e in r1.events] == ["A", "B"]


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda xs: [xs[1]], "accept action"),
        (lambda xs: [xs[0], xs[0]], "duplicate effect"),
        (lambda xs: [xs[0], GameEffect(derive_effect_id("other", 1), EffectKind.EMIT_EVENT, "other", payload={"event_type": "X", "payload": {}}, sort_key=(1,))], "action key"),
        (lambda xs: [xs[0], GameEffect(xs[1].effect_id, xs[1].kind, xs[1].source_action_key, payload=xs[1].payload, sort_key=(0,))], "effect id"),
    ],
)
def test_rejects_invalid_batch_identity_without_attaching_runtime(mutate, match: str) -> None:
    s = state()
    valid = list(batch([(EffectKind.EMIT_EVENT, {"event_type": "X", "payload": {}}, None)]))
    with pytest.raises(EffectRejected, match=match):
        EffectApplier().apply(s, tuple(mutate(valid)), permission())
    assert not hasattr(s, "_pipeline_runtime")


def test_permission_revision_target_visibility_and_exact_input_types() -> None:
    valid = batch([(EffectKind.MARK_DEATH, {"target": 2, "cause": "x"}, 2)], visibility=("PUBLIC",))
    cases = [
        (permission(role_effects=frozenset({EffectKind.ACCEPT_ACTION})), "permission"),
        (permission(contract_effects=frozenset({EffectKind.ACCEPT_ACTION})), "permission"),
        (permission(allowed_targets=frozenset({1})), "target"),
        (permission(allowed_visibility=frozenset()), "visibility"),
    ]
    for p, match in cases:
        s = state()
        with pytest.raises(EffectRejected, match=match):
            EffectApplier().apply(s, valid, p)
        assert not hasattr(s, "_pipeline_runtime") and s.players[2].is_alive
    for bad in (list(valid), (), (object(),)):
        with pytest.raises((TypeError, EffectRejected)):
            EffectApplier().apply(state(), bad, permission())
    with pytest.raises(TypeError):
        EffectApplier().apply(object(), valid, permission())
    with pytest.raises(TypeError):
        EffectApplier().apply(state(), valid, object())
    wrong_revision = batch([], revision=1)
    with pytest.raises(EffectRejected, match="revision"):
        EffectApplier().apply(state(), wrong_revision, permission())


@pytest.mark.parametrize(
    "effect",
    [
        GameEffect("x", EffectKind.SET_RESOURCE, "a", payload={"target": 1, "resource": "r", "value": 1, "extra": 2}, target_seat=1),
        GameEffect("x", EffectKind.SET_RESOURCE, "a", payload={"target": 1, "resource": "r", "value": True}, target_seat=1),
        GameEffect("x", EffectKind.SET_RESOURCE, "a", payload={"target": 2, "resource": "r", "value": 1}, target_seat=1),
        GameEffect("x", EffectKind.EMIT_EVENT, "a", payload={"event_type": "not valid", "payload": {}}),
        GameEffect("x", EffectKind.MARK_DEATH, "a", payload={"target": 99, "cause": "x"}, target_seat=99),
    ],
)
def test_closed_payload_schemas_reject_extra_wrong_type_token_and_seat(effect: GameEffect) -> None:
    accept = GameEffect(derive_effect_id("a", 0), EffectKind.ACCEPT_ACTION, "a", sort_key=(0,))
    effect = GameEffect(derive_effect_id("a", 1), effect.kind, "a", payload=effect.payload, target_seat=effect.target_seat, sort_key=(1,))
    with pytest.raises(EffectRejected):
        EffectApplier().apply(state(), (accept, effect), permission(allowed_targets=frozenset({1, 2, 3, 99})))


def test_preconditions_are_closed_and_checked_against_simulated_batch() -> None:
    s = state()
    good = batch(
        [
            (EffectKind.SET_RESOURCE, {"target": 1, "resource": "r", "value": 2}, 1),
            (EffectKind.ADD_STATUS, {"target": 1, "status": "ready"}, 1),
            (EffectKind.SET_PRIVATE_DATA, {"target": 1, "key": "k", "value": 4}, 1),
            (EffectKind.EMIT_EVENT, {"event_type": "OK", "payload": {}}, 1),
        ]
    )
    rows = list(good)
    last = rows[-1]
    rows[-1] = GameEffect(
        last.effect_id, last.kind, last.source_action_key, payload=last.payload,
        target_seat=1, sort_key=last.sort_key,
        preconditions={
            "target_alive": True,
            "resource_equals": {"resource": "r", "value": 2},
            "private_equals": {"key": "k", "value": 4},
            "status_present": {"status": "ready", "present": True},
        },
    )
    assert EffectApplier().apply(s, tuple(rows), permission()).revision == 1
    for precondition in (
        {"unknown": True},
        {"target_alive": 1},
        {"resource_equals": {"resource": "r", "value": 9}},
        {"private_equals": {"key": "k", "value": 9}},
        {"status_present": {"status": "ready", "present": False, "extra": 1}},
    ):
        bad = list(batch([(EffectKind.EMIT_EVENT, {"event_type": "X", "payload": {}}, 1)]))
        item = bad[-1]
        bad[-1] = GameEffect(item.effect_id, item.kind, item.source_action_key, payload=item.payload, target_seat=1, sort_key=item.sort_key, preconditions=precondition)
        with pytest.raises(EffectRejected, match="precondition"):
            EffectApplier().apply(state(), tuple(bad), permission())


def test_conflicts_and_underflow_are_atomic() -> None:
    cases = [
        batch([(EffectKind.CONSUME_RESOURCE, {"target": 1, "resource": "r", "amount": 1}, 1)]),
        batch([(EffectKind.REMOVE_STATUS, {"target": 1, "status": "missing"}, 1)]),
        batch([(EffectKind.REMOVE_RELATION, {"target": 1, "relation": "x", "other_seat": 2}, 1)]),
        batch([(EffectKind.MARK_DEATH, {"target": 2, "cause": "x"}, 2), (EffectKind.MARK_DEATH, {"target": 2, "cause": "x"}, 2)]),
    ]
    for effects in cases:
        s = state()
        with pytest.raises(EffectRejected):
            EffectApplier().apply(s, effects, permission())
        assert not hasattr(s, "_pipeline_runtime") and all(p.is_alive for p in s.players.values())


def test_commit_result_constructor_is_strict_and_deep_frozen() -> None:
    result = CommitResult("a", ("e",), 1, ({"event_type": "X", "payload": {"x": [1]}},), "d")
    assert result.events[0]["payload"]["x"] == (1,)
    for args in (
        (1, ("e",), 1, (), "d"),
        ("a", ["e"], 1, (), "d"),
        ("a", ("e",), True, (), "d"),
        ("a", ("e",), 1, ("bad",), "d"),
        ("a", ("e",), 1, (), "d", 2),
    ):
        with pytest.raises((TypeError, ValueError)):
            CommitResult(*args)


def test_multiple_distinct_commits_advance_revision() -> None:
    s = state()
    first = EffectApplier().apply(
        s,
        batch([(EffectKind.EMIT_EVENT, {"event_type": "FIRST", "payload": {"nested": {"x": 1}}}, None)]),
        permission(),
    )
    second = EffectApplier().apply(
        s,
        batch([(EffectKind.EMIT_EVENT, {"event_type": "SECOND", "payload": {}}, None)], action="b", revision=1),
        permission(),
    )
    assert first.revision == 1 and second.revision == 2
    assert s._pipeline_runtime.commits == {"a": first, "b": second}


def test_remaining_strict_constructor_boundaries() -> None:
    with pytest.raises(TypeError, match="role_effects"):
        permission(role_effects=set(ALL))
    with pytest.raises(TypeError, match="schema_version"):
        permission(schema_version=True)
    with pytest.raises(TypeError, match="allowed_visibility"):
        permission(allowed_visibility={"PUBLIC"})
    with pytest.raises(TypeError, match="events"):
        CommitResult("a", ("e",), 1, [], "d")
    with pytest.raises(TypeError, match="schema_version"):
        CommitResult("a", ("e",), 1, (), "d", True)
    s = state()
    s._pipeline_runtime = object()
    with pytest.raises(EffectRejected, match="runtime"):
        EffectApplier().apply(s, batch([]), permission())


@pytest.mark.parametrize(
    "kind,payload,target",
    [
        (EffectKind.ACCEPT_ACTION, {}, 1),
        (EffectKind.EMIT_EVENT, {"event_type": "X", "payload": []}, None),
        (EffectKind.EMIT_EVENT, {"event_type": "X", "payload": {}}, 99),
        (EffectKind.ADD_RELATION, {"target": 1, "relation": "r", "other_seat": 99}, 1),
        (EffectKind.RECORD_PRIVATE_FACT, {"target": 1, "namespace": "n", "fact": []}, 1),
        (EffectKind.MARK_DEATH, {"target": 2, "cause": 1}, 2),
    ],
)
def test_remaining_closed_payload_rejections(kind, payload, target) -> None:
    action = "closed"
    accept = GameEffect(derive_effect_id(action, 0), EffectKind.ACCEPT_ACTION, action, sort_key=(0,))
    item = GameEffect(derive_effect_id(action, 1), kind, action, payload=payload, target_seat=target, sort_key=(1,))
    effects = (item,) if kind is EffectKind.ACCEPT_ACTION else (accept, item)
    if kind is EffectKind.ACCEPT_ACTION:
        item = GameEffect(derive_effect_id(action, 0), kind, action, payload=payload, target_seat=target, sort_key=(0,))
        effects = (item,)
    with pytest.raises(EffectRejected):
        EffectApplier().apply(state(), effects, permission(allowed_targets=frozenset({1, 2, 3, 99})))


@pytest.mark.parametrize(
    "kind,payload,target",
    [
        (EffectKind.SET_PRIVATE_DATA, {"target": 1, "key": "k", "value": -1}, 1),
        (EffectKind.RECORD_PRIVATE_FACT, {"target": 1, "namespace": "n", "fact": {"x": 2_147_483_648}}, 1),
        (EffectKind.EMIT_EVENT, {"event_type": "X", "payload": {"x": "a" * 257}}, None),
        (EffectKind.EMIT_EVENT, {"event_type": "X", "payload": {"x": ["a" * 257]}}, None),
    ],
)
def test_nested_payload_values_have_bounded_strings_and_integers(kind, payload, target) -> None:
    action = "bounded"
    effects = (
        GameEffect(derive_effect_id(action, 0), EffectKind.ACCEPT_ACTION, action, sort_key=(0,)),
        GameEffect(derive_effect_id(action, 1), kind, action, payload=payload, target_seat=target, sort_key=(1,)),
    )
    with pytest.raises(EffectRejected):
        EffectApplier().apply(state(), effects, permission())


@pytest.mark.parametrize(
    "precondition",
    [
        {"resource_equals": "bad"},
        {"private_equals": "bad"},
        {"status_present": "missing"},
        {"status_present": {"status": "ready", "present": 1}},
        {"status_present": []},
    ],
)
def test_remaining_precondition_shapes_reject(precondition) -> None:
    effects = list(batch([(EffectKind.EMIT_EVENT, {"event_type": "X", "payload": {}}, 1)]))
    item = effects[-1]
    effects[-1] = GameEffect(item.effect_id, item.kind, item.source_action_key, payload=item.payload, target_seat=1, sort_key=item.sort_key, preconditions=precondition)
    with pytest.raises(EffectRejected, match="precondition"):
        EffectApplier().apply(state(), tuple(effects), permission())


def test_duplicate_add_operations_are_atomic() -> None:
    for kind, payload in (
        (EffectKind.ADD_STATUS, {"target": 1, "status": "same"}),
        (EffectKind.ADD_RELATION, {"target": 1, "relation": "same", "other_seat": 2}),
    ):
        s = state()
        with pytest.raises(EffectRejected):
            EffectApplier().apply(s, batch([(kind, payload, 1), (kind, payload, 1)]), permission())
        assert not hasattr(s, "_pipeline_runtime")
