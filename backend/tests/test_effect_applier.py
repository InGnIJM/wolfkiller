from __future__ import annotations

from dataclasses import FrozenInstanceError
from threading import Barrier, Event, Thread
import gc
from types import MappingProxyType

import pytest

from app.core.effect_applier import (
    CommitResult,
    EffectApplier,
    EffectPermission,
    EffectRejected,
    derive_effect_id,
    initialize_role_resources,
    role_resource_view,
)
from app.models.pipeline import RoleSpec
from app.core.state_transaction import state_transaction_lock
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
    accept_payload: dict[str, object] | None = None,
) -> tuple[GameEffect, ...]:
    rows = [(EffectKind.ACCEPT_ACTION, accept_payload or {}, None), *specs]
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
            (EffectKind.SUBMIT_DAMAGE, {"target": 2, "amount": 1, "cause": "attack"}, 2),
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
    assert runtime.pending_damage == ({"target": 2, "amount": 1, "cause": "attack"},)
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


def test_accept_metadata_increments_window_round_and_game_counts_idempotently() -> None:
    s = state()
    metadata = {
        "actor_seat": 1, "contract_id": "witch_action",
        "window_id": "window-1", "round_number": 2,
    }
    first = EffectApplier().apply(
        s, batch([], action="first", accept_payload=metadata), permission()
    )
    duplicate = EffectApplier().apply(
        s, batch([], action="first", accept_payload=metadata), permission()
    )
    assert duplicate is first
    assert s._pipeline_runtime.action_counts == {
        "window": {"1\0witch_action\0window-1": 1},
        "round": {"1\0witch_action\0" + "2": 1},
        "game": {"1\0witch_action": 1},
    }

    EffectApplier().apply(
        s, batch([], action="second", revision=1, accept_payload=metadata), permission()
    )
    changed = dict(metadata); changed["window_id"] = "window-2"
    EffectApplier().apply(
        s, batch([], action="third", revision=2, accept_payload=changed), permission()
    )
    changed["round_number"] = 3
    EffectApplier().apply(
        s, batch([], action="fourth", revision=3, accept_payload=changed), permission()
    )
    assert s._pipeline_runtime.action_counts == {
        "window": {
            "1\0witch_action\0window-1": 2,
            "1\0witch_action\0window-2": 2,
        },
        "round": {
            "1\0witch_action\0" + "2": 3,
            "1\0witch_action\0" + "3": 1,
        },
        "game": {"1\0witch_action": 4},
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"actor_seat": 1, "contract_id": "c", "window_id": "w"},
        {"actor_seat": 1, "contract_id": "c", "window_id": "w", "round_number": 1, "extra": 1},
        {"actor_seat": True, "contract_id": "c", "window_id": "w", "round_number": 1},
        {"actor_seat": 99, "contract_id": "c", "window_id": "w", "round_number": 1},
        {"actor_seat": 1, "contract_id": "bad value", "window_id": "w", "round_number": 1},
        {"actor_seat": 1, "contract_id": "c", "window_id": "", "round_number": 1},
        {"actor_seat": 1, "contract_id": "c", "window_id": "w", "round_number": -1},
        {"actor_seat": 1, "contract_id": "c", "window_id": "w", "round_number": True},
        {"actor_seat": 1, "contract_id": "c", "window_id": 1, "round_number": 1},
        {"actor_seat": 1, "contract_id": "c", "window_id": "x" * 257, "round_number": 1},
    ],
)
def test_accept_metadata_is_closed_and_atomic(metadata) -> None:
    s = state()
    with pytest.raises(EffectRejected):
        EffectApplier().apply(
            s, batch([], accept_payload=metadata), permission()
        )
    assert not hasattr(s, "_pipeline_runtime")


def test_action_count_overflow_and_invalid_runtime_are_atomic() -> None:
    from app.core.effect_applier import _Runtime

    key = "1\0c"
    metadata = {
        "actor_seat": 1, "contract_id": "c",
        "window_id": "w", "round_number": 1,
    }
    for counts in (
        {"window": {}, "round": {}, "game": {key: 2_147_483_647}},
        {"window": {}, "round": {}, "game": {key: True}},
        {"window": {}, "round": {}},
        {"window": [], "round": {}, "game": {}},
        {"window": {"\ud800": 1}, "round": {}, "game": {}},
        {"window": {}, "round": {}, "game": {"x": -1}},
    ):
        s = state(); runtime = _Runtime(action_counts=counts); s._pipeline_runtime = runtime
        with pytest.raises(EffectRejected):
            EffectApplier().apply(
                s, batch([], accept_payload=metadata), permission()
            )
        assert s._pipeline_runtime is runtime


def test_accept_metadata_defensively_rejects_invalid_utf8() -> None:
    from app.core.role_runtime import record_accepted_action

    counts = {"window": {}, "round": {}, "game": {}}
    with pytest.raises(EffectRejected, match="window"):
        record_accepted_action(
            counts,
            {"actor_seat": 1, "contract_id": "c", "window_id": "\ud800", "round_number": 1},
            {1},
        )


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


def test_concurrent_distinct_actions_serialize_commits(monkeypatch) -> None:
    import app.core.effect_applier as module

    s = state()
    both_entered = Barrier(2)
    original = module.state_transaction_lock

    def gated_lock(current):
        both_entered.wait()
        return original(current)

    monkeypatch.setattr(module, "state_transaction_lock", gated_lock)
    results, errors = [], []

    def apply(action: str) -> None:
        try:
            results.append(EffectApplier().apply(s, batch([], action=action), permission()))
        except Exception as error:  # pragma: no branch - assertion reports it
            errors.append(error)

    threads = [Thread(target=apply, args=(action,)) for action in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert {result.revision for result in results} == {1, 2}
    assert s._pipeline_runtime.revision == 2
    assert set(s._pipeline_runtime.commits) == {"a", "b"}


def test_runtime_clone_deeply_isolates_nested_values() -> None:
    from app.core.effect_applier import _Runtime

    s = state()
    nested = {"items": [{"seat": 2}]}
    runtime = _Runtime(
        private_data={1: {"nested": nested}},
        private_facts={1: [{"namespace": "n", "fact": nested}]},
        pending_damage=({"target": 2, "meta": nested},),
        events=({"event_type": "OLD", "payload": nested},),
    )
    s._pipeline_runtime = runtime
    result = EffectApplier().apply(
        s,
        batch([(EffectKind.EMIT_EVENT, {"event_type": "NEW", "payload": nested}, None)]),
        permission(),
    )
    nested["items"][0]["seat"] = 99
    assert s._pipeline_runtime.private_data[1]["nested"]["items"][0]["seat"] == 2
    assert s._pipeline_runtime.private_facts[1][0]["fact"]["items"][0]["seat"] == 2
    assert s._pipeline_runtime.pending_damage[0]["meta"]["items"][0]["seat"] == 2
    assert result.events[0]["payload"]["items"][0]["seat"] == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"x": object()},
        {"x": float("nan")},
        {"x": 2_147_483_648},
        {"x": "\ud800"},
    ],
)
def test_invalid_existing_runtime_is_rejected_without_state_change(bad) -> None:
    from app.core.effect_applier import _Runtime

    s = state()
    runtime = _Runtime(private_data={1: bad})
    s._pipeline_runtime = runtime
    with pytest.raises(EffectRejected):
        EffectApplier().apply(s, batch([]), permission())
    assert s._pipeline_runtime is runtime and runtime.revision == 0


def test_cyclic_existing_runtime_is_rejected() -> None:
    from app.core.effect_applier import _Runtime

    s = state()
    cyclic = {}
    cyclic["self"] = cyclic
    runtime = _Runtime(private_data={1: cyclic})
    s._pipeline_runtime = runtime
    with pytest.raises(EffectRejected):
        EffectApplier().apply(s, batch([]), permission())
    assert s._pipeline_runtime is runtime


def test_state_lock_registry_reuses_identity_and_cleans_up() -> None:
    import app.core.state_transaction as module

    s = state()
    key = id(s)
    assert module.state_transaction_lock(s) is module.state_transaction_lock(s)
    assert key in module._LOCKS
    del s
    gc.collect()
    assert key not in module._LOCKS


def test_invalid_runtime_container_shape_is_rejected() -> None:
    from app.core.effect_applier import _Runtime

    s = state()
    runtime = _Runtime(role_resources=object())
    s._pipeline_runtime = runtime
    with pytest.raises(EffectRejected, match="runtime"):
        EffectApplier().apply(s, batch([]), permission())
    assert s._pipeline_runtime is runtime


def test_finite_nested_float_is_supported() -> None:
    result = EffectApplier().apply(
        state(),
        batch([(EffectKind.EMIT_EVENT, {"event_type": "FLOAT", "payload": {"value": 1.5}}, None)]),
        permission(),
    )
    assert result.events[0]["payload"]["value"] == 1.5


@pytest.mark.parametrize(
    "event",
    [
        {"event_type": "X", "payload": bytearray(b"secret")},
        {"event_type": "X", "payload": {"value": float("nan")}},
        {"event_type": "X", "payload": {1: "bad-key"}},
    ],
)
def test_commit_result_rejects_non_json_event_values(event) -> None:
    with pytest.raises((TypeError, ValueError)):
        CommitResult("a", ("e",), 1, (event,), "d")


def test_commit_result_rejects_cycles_and_excessive_depth() -> None:
    cyclic = {}
    cyclic["self"] = cyclic
    deep = value = {}
    for _ in range(66):
        child = {}
        value["next"] = child
        value = child
    for payload in (cyclic, deep):
        with pytest.raises((TypeError, ValueError), match="cycle|depth"):
            CommitResult("a", ("e",), 1, ({"event_type": "X", "payload": payload},), "d")


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"role_resources": object()},
        {"role_resources": {0: {"r": 1}}},
        {"role_resources": {1: {"r": -1}}},
        {"role_resources": {1: object()}},
        {"statuses": {1: object()}},
        {"statuses": {1: {1}}},
        {"statuses": object()},
        {"relations": {1: {"bad"}}},
        {"relations": {1: {("r", 0)}}},
        {"private_facts": {1: object()}},
        {"private_data": object()},
        {"pending_damage": object()},
        {"events": object()},
        {"commits": {"a": object()}},
        {"commits": object()},
        {"commits": {"wrong": CommitResult("a", ("e",), 1, (), "d")}},
    ],
)
def test_every_invalid_runtime_field_is_rejected_atomically(changes) -> None:
    from app.core.effect_applier import _Runtime

    s = state()
    runtime = _Runtime(**changes)
    s._pipeline_runtime = runtime
    with pytest.raises(EffectRejected):
        EffectApplier().apply(s, batch([]), permission())
    assert s._pipeline_runtime is runtime


def test_committed_runtime_has_no_payload_aliases() -> None:
    payload = {"items": [{"seat": 2}]}
    s = state()
    result = EffectApplier().apply(
        s,
        batch([
            (EffectKind.SET_PRIVATE_DATA, {"target": 1, "key": "k", "value": payload}, 1),
            (EffectKind.RECORD_PRIVATE_FACT, {"target": 1, "namespace": "n", "fact": payload}, 1),
        ]),
        permission(),
    )
    payload["items"][0]["seat"] = 99
    assert s._pipeline_runtime.private_data[1]["k"]["items"][0]["seat"] == 2
    assert s._pipeline_runtime.private_facts[1][0]["fact"]["items"][0]["seat"] == 2
    assert EffectApplier().apply(s, batch([], action="a"), permission()) == result


def test_commit_result_rejects_oversized_node_count() -> None:
    with pytest.raises(ValueError, match="large"):
        CommitResult("a", ("e",), 1, ({"event_type": "X", "payload": [None] * 10_001},), "d")


def test_runtime_relation_and_fact_shape_are_strict() -> None:
    from app.core.effect_applier import _Runtime

    for runtime in (
        _Runtime(relations={1: {(1, 2)}}),
        _Runtime(private_facts={1: {"not": "sequence"}}),
    ):
        s = state(); s._pipeline_runtime = runtime
        with pytest.raises(EffectRejected):
            EffectApplier().apply(s, batch([]), permission())


def test_valid_premounted_status_runtime_is_preserved() -> None:
    from app.core.effect_applier import _Runtime

    s = state(); s._pipeline_runtime = _Runtime(statuses={1: {"ready"}})
    EffectApplier().apply(s, batch([]), permission())
    assert s._pipeline_runtime.statuses == {1: {"ready"}}


def test_premounted_private_fact_remains_mutable_and_new_fact_appends() -> None:
    from app.core.effect_applier import _Runtime

    old = {"namespace": "old", "fact": {}}
    incoming = {"nested": [{"seat": 2}]}
    s = state(); s._pipeline_runtime = _Runtime(private_facts={1: [old]})
    EffectApplier().apply(
        s,
        batch([(EffectKind.RECORD_PRIVATE_FACT, {"target": 1, "namespace": "new", "fact": incoming}, 1)]),
        permission(),
    )
    incoming["nested"][0]["seat"] = 99
    assert s._pipeline_runtime.private_facts[1][0] == old
    assert s._pipeline_runtime.private_facts[1][1]["fact"]["nested"][0]["seat"] == 2


@pytest.mark.parametrize(
    "private_facts",
    [
        {1: ["not-record"]},
        {1: [{"namespace": "old"}]},
        {1: [{"namespace": "old", "fact": {}, "extra": True}]},
        {1: [{"namespace": 1, "fact": {}}]},
        {1: [{"namespace": "old", "fact": []}]},
    ],
)
def test_invalid_private_fact_records_are_rejected(private_facts) -> None:
    from app.core.effect_applier import _Runtime

    s = state(); runtime = _Runtime(private_facts=private_facts); s._pipeline_runtime = runtime
    with pytest.raises(EffectRejected):
        EffectApplier().apply(s, batch([]), permission())
    assert s._pipeline_runtime is runtime


def test_non_mapping_private_data_value_is_rejected_atomically() -> None:
    from app.core.effect_applier import _Runtime

    s = state(); runtime = _Runtime(private_data={1: 42}); s._pipeline_runtime = runtime
    with pytest.raises(EffectRejected):
        EffectApplier().apply(s, batch([]), permission())
    assert s._pipeline_runtime is runtime


def test_empty_premounted_private_fact_map_is_valid() -> None:
    from app.core.effect_applier import _Runtime

    s = state(); s._pipeline_runtime = _Runtime(private_facts={})
    EffectApplier().apply(s, batch([]), permission())
    assert s._pipeline_runtime.private_facts == {}


def test_shared_state_transaction_lock_is_identity_bound() -> None:
    import app.core.state_transaction as module

    current = state()
    assert state_transaction_lock(current) is state_transaction_lock(current)
    with pytest.raises(TypeError):
        state_transaction_lock(object())
    key, reference = id(current), module._LOCKS[id(current)][0]
    module._drop(key, object())
    assert key in module._LOCKS
    module._drop(key, reference)
    assert key not in module._LOCKS


def test_initialize_role_resources_is_stable_idempotent_and_immutable() -> None:
    s = state(); specs = {
        "witch": RoleSpec("witch", initial_resources={"poison": 1, "antidote": 1}),
        "wolf": RoleSpec("wolf"), "villager": RoleSpec("villager"),
    }
    first = initialize_role_resources(s, specs, "a" * 64)
    assert first is not None and first.revision == 1
    assert role_resource_view(s, 1) == {"antidote": 1, "poison": 1}
    with pytest.raises(TypeError): role_resource_view(s, 1)["poison"] = 0
    assert initialize_role_resources(s, specs, "a" * 64) is first
    assert s._pipeline_runtime.revision == 1
    from app.core.effect_applier import _digest
    alive = {seat: player.is_alive for seat, player in s.players.items()}
    assert first.state_digest == _digest(s, s._pipeline_runtime, alive)
    assert initialize_role_resources(s, specs, "a" * 64).state_digest == _digest(s, s._pipeline_runtime, alive)


def test_resource_initialization_never_resets_consumed_or_changes_config() -> None:
    s = state(); specs = {role: RoleSpec(role, initial_resources={"r": 1} if role == "witch" else {}) for role in ("witch", "wolf", "villager")}
    initialize_role_resources(s, specs, "a" * 64)
    EffectApplier().apply(s, batch([(EffectKind.CONSUME_RESOURCE, {"target": 1, "resource": "r", "amount": 1}, 1)], revision=1, action="consume"), permission())
    assert role_resource_view(s, 1)["r"] == 0
    assert initialize_role_resources(s, specs, "a" * 64).revision == 1
    with pytest.raises(EffectRejected): initialize_role_resources(s, specs, "b" * 64)


def test_resource_setup_marker_survives_runtime_clone_and_keys_match_specs() -> None:
    s = state(); specs = {role: RoleSpec(role, initial_resources={"r": 1} if role == "witch" else {}) for role in ("witch", "wolf", "villager")}
    initialize_role_resources(s, specs, "a" * 64)
    EffectApplier().apply(s, batch([], revision=1, action="clone-runtime"), permission())
    with pytest.raises(EffectRejected): initialize_role_resources(s, specs, "b" * 64)
    forged = dict(specs); forged["witch"] = RoleSpec("different", initial_resources={"r": 1})
    with pytest.raises(ValueError): initialize_role_resources(state(), forged, "a" * 64)
    s._pipeline_runtime.resource_setup_digest = "bad marker"
    with pytest.raises(EffectRejected): role_resource_view(s, 1)


def test_resource_initialization_validates_inputs_and_empty_specs() -> None:
    empty = state(); specs = {role: RoleSpec(role) for role in ("witch", "wolf", "villager")}
    assert initialize_role_resources(empty, specs, "a" * 64) is None
    assert not hasattr(empty, "_pipeline_runtime") and role_resource_view(empty, 1) == {}
    for bad in (object(), {"witch": object()}):
        with pytest.raises(TypeError): initialize_role_resources(state(), bad, "a" * 64)
    with pytest.raises(ValueError): initialize_role_resources(state(), {"witch": RoleSpec("witch")}, "a" * 64)
    boolean = {role: RoleSpec(role, initial_resources={"r": True} if role == "witch" else {}) for role in ("witch", "wolf", "villager")}
    boolean_state = state(); initialize_role_resources(boolean_state, boolean, "a" * 64)
    assert role_resource_view(boolean_state, 1)["r"] == 1
    with pytest.raises((TypeError, ValueError)): initialize_role_resources(state(), specs, "bad config")
    with pytest.raises(TypeError): initialize_role_resources(object(), specs, "a" * 64)
    with pytest.raises(TypeError): role_resource_view(object(), 1)
    with pytest.raises(ValueError): role_resource_view(state(), 0)
    invalid = state(); invalid._pipeline_runtime = object()
    with pytest.raises(EffectRejected): role_resource_view(invalid, 1)
    oversized = {role: RoleSpec(role, initial_resources={"r": 2_147_483_648} if role == "witch" else {}) for role in ("witch", "wolf", "villager")}
    with pytest.raises(EffectRejected): initialize_role_resources(state(), oversized, "a" * 64)


def test_resource_initialization_is_concurrently_idempotent() -> None:
    s = state(); specs = {role: RoleSpec(role, initial_resources={"r": 1}) for role in ("witch", "wolf", "villager")}
    barrier = Barrier(3); results = []
    def initialize(): barrier.wait(); results.append(initialize_role_resources(s, specs, "a" * 64))
    threads = [Thread(target=initialize), Thread(target=initialize)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join()
    assert results[0] is results[1] and s._pipeline_runtime.revision == 1


def test_settle_pending_is_atomic_stable_and_idempotent() -> None:
    s = state()
    EffectApplier().apply(s, batch([
        (EffectKind.SUBMIT_DAMAGE, {"target": 3, "amount": 1, "cause": "z_damage"}, 3),
        (EffectKind.SUBMIT_DAMAGE, {"target": 2, "amount": 2, "cause": "wolf_kill"}, 2),
        (EffectKind.SUBMIT_DAMAGE, {"target": 2, "amount": 1, "cause": "poison"}, 2),
        (EffectKind.SUBMIT_PROTECTION, {"target": 2, "amount": 1}, 2),
        (EffectKind.SUBMIT_PROTECTION, {"target": 3, "amount": 1}, 3),
    ], visibility=("PUBLIC",)), permission())
    before = s._pipeline_runtime.revision

    first = EffectApplier().settle_pending(s, round_number=4)
    second = EffectApplier().settle_pending(s, round_number=4)

    assert first is second and first.revision == before + 1
    assert first.events == ({
        "event_type": "PLAYER_DIED",
        "payload": {"seat": 2, "cause": "wolf_kill", "round_number": 4},
        "visibility": ("PUBLIC",),
    },)
    assert s.players[2].is_alive is False and s.players[3].is_alive is True
    assert [(item.player_seat, item.cause, item.round_number) for item in s.death_history] == [
        (2, "wolf_kill", 4),
    ]
    assert s._pipeline_runtime.pending_damage == ()
    assert s._pipeline_runtime.pending_protection == ()
    assert s._pipeline_runtime.commits[first.action_key] is first


def test_settle_pending_empty_is_noop_and_protection_only_commits() -> None:
    empty = state()
    assert EffectApplier().settle_pending(empty, round_number=0) is None
    assert not hasattr(empty, "_pipeline_runtime")

    protected = state()
    EffectApplier().apply(protected, batch([
        (EffectKind.SUBMIT_PROTECTION, {"target": 2, "amount": 1}, 2),
    ]), permission())
    result = EffectApplier().settle_pending(protected, round_number=1)
    assert result is not None and result.events == ()
    assert result.effect_ids and protected._pipeline_runtime.pending_protection == ()
    assert all(player.is_alive for player in protected.players.values())


def test_damage_payload_requires_closed_cause() -> None:
    valid = state()
    EffectApplier().apply(valid, batch([
        (EffectKind.SUBMIT_DAMAGE, {"target": 2, "amount": 1, "cause": "future.cause"}, 2),
    ]), permission())
    assert valid._pipeline_runtime.pending_damage == (
        {"target": 2, "amount": 1, "cause": "future.cause"},
    )

    for payload in (
        {"target": 2, "amount": 1, "cause": "bad cause"},
        {"target": 2, "amount": 1, "cause": "ok", "extra": 1},
    ):
        with pytest.raises(EffectRejected):
            EffectApplier().apply(state(), batch([
                (EffectKind.SUBMIT_DAMAGE, payload, 2),
            ]), permission())

    legacy = state()
    with pytest.raises(EffectRejected, match="invalid payload fields"):
        EffectApplier().apply(legacy, batch([
            (EffectKind.SUBMIT_DAMAGE, {"target": 2, "amount": 1}, 2),
        ]), permission())
    assert not hasattr(legacy, "_pipeline_runtime") and legacy.players[2].is_alive


@pytest.mark.parametrize("round_number", [True, -1, 2_147_483_648])
def test_settle_pending_rejects_bad_input_and_runtime_atomically(round_number) -> None:
    s = state()
    with pytest.raises((TypeError, ValueError)):
        EffectApplier().settle_pending(s, round_number=round_number)
    assert not hasattr(s, "_pipeline_runtime")


def test_settle_pending_rejects_non_state() -> None:
    with pytest.raises(TypeError): EffectApplier().settle_pending(object(), round_number=1)


def test_settle_pending_rejects_malformed_or_overflowing_runtime() -> None:
    from app.core.effect_applier import _Runtime

    malformed = state(); malformed.death_history = object()
    malformed._pipeline_runtime = _Runtime(
        pending_damage=({"target": 2, "amount": 1, "cause": "attack"},)
    )
    with pytest.raises(EffectRejected): EffectApplier().settle_pending(malformed, round_number=1)
    assert malformed.players[2].is_alive

    for pending in (
        ({"target": 99, "amount": 1, "cause": "attack"},),
        ({"target": 2, "amount": 0, "cause": "attack"},),
        ({"target": 2, "amount": 2_147_483_647, "cause": "attack"},
         {"target": 2, "amount": 1, "cause": "attack"}),
    ):
        bad = state(); runtime = _Runtime(pending_damage=pending); bad._pipeline_runtime = runtime
        with pytest.raises(EffectRejected): EffectApplier().settle_pending(bad, round_number=1)
        assert bad._pipeline_runtime is runtime and bad.players[2].is_alive


def test_night_settlement_pure_boundaries_are_strict() -> None:
    from app.core.night_settlement import settle, settlement_key

    for game_id in ("", "\ud800"):
        with pytest.raises(ValueError): settlement_key(game_id, 1)
    for round_number in (True, -1):
        with pytest.raises((TypeError, ValueError)): settlement_key("g", round_number)
    for args in (
        ([], (), {1}, {1: True}, 1),
        ((), (), {0}, {0: True}, 1),
        ((), (), {1}, {1: 1}, 1),
    ):
        with pytest.raises((TypeError, ValueError)): settle(*args)
    for pending in (
        (object(),),
        ({"target": 1, "amount": 1},),
        ({"target": 1, "amount": 1, "cause": 1},),
        ({"target": 1, "amount": 1, "cause": "bad cause"},),
    ):
        with pytest.raises(ValueError): settle(pending, (), {1}, {1: True}, 1)


def test_settle_pending_is_concurrently_idempotent() -> None:
    s = state()
    EffectApplier().apply(s, batch([
        (EffectKind.SUBMIT_DAMAGE, {"target": 2, "amount": 1, "cause": "attack"}, 2),
    ]), permission())
    barrier = Barrier(3); results = []
    def settle(): barrier.wait(); results.append(EffectApplier().settle_pending(s, round_number=1))
    threads = [Thread(target=settle), Thread(target=settle)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join()
    assert results[0] is results[1]
    assert len(s.death_history) == 1 and s._pipeline_runtime.revision == 2
