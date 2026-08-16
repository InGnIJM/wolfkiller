from __future__ import annotations

import gc
from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

import app.core.point_journal as journal_module
from app.core.effect_applier import CommitResult
from app.core.point_journal import PendingEvent, PointCheckpoint, PointKey, WorkCursor, point_journal
from app.models.game import GameState
from app.models.pipeline import ActionContract, EffectKind, IssuedActionRequest, SchedulePoint


def request(key: str = "action-1") -> IssuedActionRequest:
    contract = ActionContract(
        contract_id="night-action", schedule_point=SchedulePoint.NIGHT_ACTION,
        order=10, action_types=("pass",), actions_requiring_target=frozenset(),
        fallback_action_type="pass", allowed_effects=frozenset({EffectKind.ACCEPT_ACTION}),
    )
    return IssuedActionRequest(1, "role", contract, 0, 1, "night", "window", key)


def key(**changes) -> PointKey:
    values = dict(game_id="game-1", round_number=1, phase="night",
                  point=SchedulePoint.NIGHT_ACTION, registry_digest="registry-digest")
    values.update(changes); return PointKey(**values)


def checkpoint(**changes) -> PointCheckpoint:
    commit = CommitResult("action-1", ("effect-1",), 1, (), "digest")
    values = dict(
        issued=(request(),), actual=(request(),), commits=(commit,),
        events=({"event_type": "E", "payload": {"items": [1]}},),
        faults=({"code": "slow_rule"},), pending=(), work_count=1,
        cursor=WorkCursor("main", 1, 0), complete_result=None,
    )
    values.update(changes); return PointCheckpoint(**values)


def test_point_key_and_cursor_are_exact_frozen_and_bounded() -> None:
    value = key(); assert value.point is SchedulePoint.NIGHT_ACTION
    with pytest.raises(FrozenInstanceError): value.phase = "day"
    for call in (
        lambda: key(game_id=1), lambda: key(game_id=""), lambda: key(game_id="\ud800"),
        lambda: key(round_number=True), lambda: key(round_number=-1),
        lambda: key(point="night_action"), lambda: key(registry_digest="x" * 2001),
        lambda: WorkCursor("unknown", 0, 0), lambda: WorkCursor("main", True, 0),
        lambda: WorkCursor("response", 0, -1),
    ):
        with pytest.raises((TypeError, ValueError)): call()
    assert WorkCursor("response", 2, 3) == WorkCursor("response", 2, 3)


def test_checkpoint_event_strings_accept_up_to_2000_chars() -> None:
    checkpoint(events=({"event_type": "E", "payload": {"channel": "a" * 2000, "cjk": "汉" * 300}},))
    with pytest.raises(ValueError):
        checkpoint(events=({"event_type": "E", "payload": {"channel": "a" * 2001}},))


def test_pending_event_is_exact_frozen_and_bounded() -> None:
    event = PendingEvent(0, 1, 8); assert event.depth == 8
    assert len(event) == 3
    assert dict(event) == {"commit_index": 0, "ordinal": 1, "depth": 8}
    with pytest.raises(KeyError): event["unknown"]
    with pytest.raises(FrozenInstanceError): event.depth = 2
    for values in ((True, 0, 0), (0, -1, 0), (0, 0, 9)):
        with pytest.raises((TypeError, ValueError)): PendingEvent(*values)


def test_checkpoint_is_exact_deep_frozen_and_old_snapshot_is_stable() -> None:
    raw = {"event_type": "E", "payload": {"items": [1]}}
    value = checkpoint(cursor=WorkCursor("done", 0, 0), pending=(),
                       events=(raw,), complete_result={"state_digest": "done", "events": [raw]})
    raw["payload"]["items"].append(2)
    assert value.events[0]["payload"]["items"] == (1,)
    assert value.complete_result["events"][0]["payload"]["items"] == (1,)
    with pytest.raises(TypeError): value.events[0]["payload"]["items"] += (2,)
    newer = checkpoint(events=({"event_type": "NEW"},))
    assert value.events[0]["event_type"] == "E" and newer.events[0]["event_type"] == "NEW"


def test_checkpoint_rejects_subclasses_bad_dtos_and_closed_json() -> None:
    sub_request = type("SubRequest", (IssuedActionRequest,), {})
    sub_commit = type("SubCommit", (CommitResult,), {})
    base = request(); child = sub_request(
        base.actor_seat, base.role_id, base.contract, base.context_revision,
        base.round_number, base.phase, base.window_id, base.action_key,
    )
    for call in (
        lambda: checkpoint(issued=[request()]), lambda: checkpoint(actual=(object(),)),
        lambda: checkpoint(issued=(child,)),
        lambda: checkpoint(commits=(sub_commit("a", (), 0, (), "d"),)),
        lambda: checkpoint(cursor=object()), lambda: checkpoint(complete_result=[]),
        lambda: checkpoint(events=[]), lambda: checkpoint(events=({1: "bad"},)),
        lambda: checkpoint(events=({"value": object()},)), lambda: checkpoint(faults=({"n": -1},)),
        lambda: checkpoint(pending=(object(),)), lambda: checkpoint(work_count=True),
        lambda: checkpoint(pending=[]),
        lambda: checkpoint(events=({"number": float("nan")},)),
        lambda: checkpoint(events=({"text": "\ud800"},)),
    ):
        with pytest.raises((TypeError, ValueError)): call()
    cycle = {}; cycle["self"] = cycle
    with pytest.raises(ValueError): checkpoint(events=(cycle,))
    deep = {}; current = deep
    for _ in range(65): current["next"] = {}; current = current["next"]
    with pytest.raises(ValueError): checkpoint(events=(deep,))
    with pytest.raises(ValueError): checkpoint(events=({"items": [None] * 10_001},))
    assert checkpoint(events=({"number": 1.5},)).events[0]["number"] == 1.5


def test_checkpoint_validates_cursor_pending_and_commit_references() -> None:
    commit = CommitResult("a", (), 1, ({"event_type": "E"},), "d")
    valid = checkpoint(commits=(commit,), pending=(PendingEvent(0, 0, 0),),
                       cursor=WorkCursor("response", 0, 0))
    assert valid.pending == (PendingEvent(0, 0, 0),)
    converted = checkpoint(commits=(commit,), pending=({"commit_index": 0, "ordinal": 0, "depth": 0},),
                           cursor=WorkCursor("response", 0, 0))
    assert type(converted.pending[0]) is PendingEvent
    for call in (
        lambda: checkpoint(cursor=WorkCursor("main", 2, 0)),
        lambda: checkpoint(cursor=WorkCursor("main", 1, 1)),
        lambda: checkpoint(commits=(commit,), pending=(PendingEvent(1, 0, 0),)),
        lambda: checkpoint(commits=(commit,), pending=(PendingEvent(0, 1, 0),)),
        lambda: checkpoint(commits=(commit,), pending=(PendingEvent(0, 0, 0),),
                           cursor=WorkCursor("response", 2, 0)),
        lambda: checkpoint(commits=(commit,), pending=(PendingEvent(0, 0, 0),),
                           cursor=WorkCursor("response", 1, 1)),
        lambda: checkpoint(cursor=WorkCursor("done", 1, 0), pending=()),
        lambda: checkpoint(cursor=WorkCursor("done", 0, 0), pending=(PendingEvent(0, 0, 0),)),
        lambda: checkpoint(complete_result={"state_digest": "d"}),
        lambda: checkpoint(cursor=WorkCursor("done", 0, 0), complete_result=[]),
    ):
        with pytest.raises((TypeError, ValueError)): call()


def test_identity_journal_put_get_clear_and_key_isolation() -> None:
    state = GameState("game-1"); store = point_journal(state)
    first, second = key(), key(round_number=2)
    old = checkpoint(); store.put(first, old)
    assert store.get(first) is old and store.get(second) is None
    replacement = checkpoint(cursor=WorkCursor("done", 0, 0), pending=(), complete_result={"state_digest": "done"})
    store.put(first, replacement); assert store.get(first) is replacement
    assert store.clear(second) is False and store.clear(first) is True and store.get(first) is None
    store.put(first, old); store.clear(); assert store.get(first) is None
    with pytest.raises(TypeError): store.get(object())
    with pytest.raises(TypeError): store.put(object(), old)
    with pytest.raises(TypeError): store.put(first, object())
    with pytest.raises(TypeError): store.clear(object())


def test_journal_is_per_state_weak_and_rejects_nonstate() -> None:
    with pytest.raises(TypeError): point_journal(object())
    first = GameState("same"); second = GameState("same")
    assert point_journal(first) is point_journal(first)
    assert point_journal(first) is not point_journal(second)
    stale_identity = id(second); stale_reference = journal_module._JOURNALS[stale_identity][0]
    journal_module._drop(stale_identity, __import__("weakref").ref(first))
    assert journal_module._JOURNALS[stale_identity][0] is stale_reference
    third = GameState("third"); foreign = point_journal(second)
    journal_module._JOURNALS[id(third)] = (__import__("weakref").ref(second), foreign)
    assert point_journal(third) is not foreign
    identity = id(first); point_journal(first).put(key(), checkpoint())
    stale = point_journal(first); stale._state = lambda: None
    with pytest.raises(RuntimeError): stale.get(key())
    stale._state = __import__("weakref").ref(first)
    del first; gc.collect()
    assert identity not in journal_module._JOURNALS


def test_journal_drop_is_reentrant_under_guard() -> None:
    """The weakref callback may fire synchronously while the guard is held;
    it must not deadlock the calling thread."""
    from threading import Thread
    from weakref import ref

    state = GameState("reentrant-drop")
    identity = id(state); reference = ref(state)
    journal_module._JOURNALS[identity] = (reference, journal_module.PointJournal(state))
    results: list[bool] = []

    def work() -> None:
        with journal_module._GUARD:
            journal_module._drop(identity, reference)
        results.append(identity not in journal_module._JOURNALS)

    thread = Thread(target=work, daemon=True); thread.start(); thread.join(timeout=5)
    assert not thread.is_alive(), "journal drop deadlocked while guard was held"
    assert results == [True]
