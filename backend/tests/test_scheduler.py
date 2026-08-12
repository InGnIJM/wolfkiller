from __future__ import annotations

from dataclasses import FrozenInstanceError
import traceback
from threading import Barrier, Event, Lock, Thread
from time import monotonic

import pytest
import app.core.scheduler as scheduler_module

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, EffectPermission, derive_effect_id
from app.core.scheduler import (
    DomainEvent, PipelinePaused, PointResult, ResponseLimitExceeded,
    ResponseQueue, ResponseWindow, Scheduler, stable_window_id,
)
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect,
    RoleSpec, SchedulePoint,
)
from app.roles.registry import RegistrySnapshot


class CustomControlFlow(BaseException):
    pass


def applicable(context: ActionContext) -> bool:
    return True


def not_applicable(context: ActionContext) -> bool:
    return False


def explode(context: ActionContext) -> bool:
    raise RuntimeError("secret")


def explode_resolve(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    raise RuntimeError("secret")


def resolve_event(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.EMIT_EVENT,
        context.action_key, payload={"event_type": "DONE", "payload": {"target_seat": context.actor_seat}},
        expected_revision=context.revision, sort_key=(1,),
    ),)


def fallback_resolve(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    if command.action_type != "pass":
        raise RuntimeError("secret")
    return resolve_event(context, command)


def always_fail(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    raise RuntimeError("secret")


def react_event(context: ActionContext) -> tuple[GameEffect, ...]:
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.EMIT_EVENT,
        context.action_key, payload={"event_type": "REACTED", "payload": {"target_seat": context.actor_seat}},
        expected_revision=context.revision, source_event_id=context.source_event_id, sort_key=(1,),
    ),)


def aggregate_event(context: ActionContext, commands: tuple[ActionCommand, ...]) -> tuple[GameEffect, ...]:
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.EMIT_EVENT,
        context.action_key, payload={"event_type": "GROUP", "payload": {"count": len(commands)}},
        expected_revision=context.revision, sort_key=(1,),
    ),)


def fallback_aggregate(context: ActionContext, commands: tuple[ActionCommand, ...]) -> tuple[GameEffect, ...]:
    if any(command.action_type != "pass" for command in commands):
        raise RuntimeError("secret")
    return aggregate_event(context, commands)


def failing_aggregate(context: ActionContext, commands: tuple[ActionCommand, ...]) -> tuple[GameEffect, ...]:
    raise RuntimeError("secret")


def response_event(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.EMIT_EVENT,
        context.action_key, payload={"event_type": "REACTED", "payload": {"target_seat": context.actor_seat}},
        expected_revision=context.revision, source_event_id=context.source_event_id, sort_key=(1,),
    ),)


def contract(cid: str, order: int = 1, *, point=SchedulePoint.NIGHT_ACTION,
             applies=applicable, aggregate=False, responses=frozenset()) -> ActionContract:
    return ActionContract(
        contract_id=cid, schedule_point=point, order=order,
        action_types=("act", "pass"), actions_requiring_target=frozenset(),
        fallback_action_type="pass", allowed_effects=frozenset({EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC"}), response_event_types=responses,
        is_applicable=applies, aggregate=aggregate_event if aggregate else None,
        resolve=None if aggregate else resolve_event,
    )


def snapshot(*specs: RoleSpec) -> RegistrySnapshot:
    return RegistrySnapshot({spec.role_id: spec for spec in specs}, "a" * 64)


def spec(role: str, *contracts: ActionContract) -> RoleSpec:
    return RoleSpec(role_id=role, camp_id="good", contracts=contracts,
                    allowed_effects=frozenset({EffectKind.EMIT_EVENT}),
                    visibility_namespaces=frozenset({"PUBLIC"}))


def state(*roles: str) -> GameState:
    return GameState("g", phase=GamePhase.NIGHT, round_number=1,
                     players={i: PlayerState(i, role, "good") for i, role in enumerate(roles, 1)})


def scheduler(registry, provider=lambda request, context, attempt: ActionCommand(action_type="act", target_seat=None, reasoning="ok")):
    return Scheduler(registry, ContextProjector(), ActionValidator(), ActionResolver(), EffectApplier(), provider)


def test_issue_is_stable_and_filters_point_alive_and_applicability() -> None:
    a, b, no = contract("a", 2), contract("b", 1), contract("no", 0, applies=not_applicable)
    registry = snapshot(spec("r2", a), spec("r1", b, no), spec("passive"))
    game = state("r2", "r1", "r1", "passive"); game.players[3].is_alive = False
    requests = scheduler(registry).issue(game, SchedulePoint.NIGHT_ACTION, registry)
    assert [(r.contract.order, r.role_id, r.contract.contract_id, r.actor_seat) for r in requests] == [(1, "r1", "b", 2), (2, "r2", "a", 1)]
    assert scheduler(registry).issue(game, SchedulePoint.DAY_ACTION, registry) == ()
    assert requests == scheduler(registry).issue(game, SchedulePoint.NIGHT_ACTION, registry)


def test_issue_rejects_unknown_role_and_sanitizes_hook_failure() -> None:
    registry = snapshot(spec("known", contract("x", applies=explode)))
    with pytest.raises(ValueError, match="unknown role"):
        scheduler(registry).issue(state("missing"), SchedulePoint.NIGHT_ACTION, registry)
    with pytest.raises(PipelinePaused) as caught:
        scheduler(registry).issue(state("known"), SchedulePoint.NIGHT_ACTION, registry)
    assert "secret" not in str(caught.value)


def test_run_point_retries_once_then_falls_back_and_applies() -> None:
    calls = []
    def provider(request, context, attempt):
        calls.append(attempt)
        return ActionCommand(action_type="invalid", target_seat=None, reasoning="x")
    registry = snapshot(spec("r", contract("c")))
    result = scheduler(registry, provider).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert calls == [0, 1]
    assert len(result.requests) == len(result.commits) == 1
    assert result.events[0]["event_type"] == "DONE"
    assert result.commits[0].revision == 1


def test_run_point_uses_aggregate_group_key_once() -> None:
    registry = snapshot(spec("r", contract("group", aggregate=True)))
    game = state("r", "r")
    result = scheduler(registry).run_point(game, SchedulePoint.NIGHT_ACTION)
    assert len(result.requests) == 2 and len(result.commits) == 1
    assert result.events[0]["payload"]["count"] == 2
    assert result.commits[0].action_key not in {request.action_key for request in result.requests}


def test_provider_wrong_type_and_invalid_fallback_pause() -> None:
    registry = snapshot(spec("r", contract("c")))
    with pytest.raises(TypeError):
        scheduler(registry, lambda *args: {}).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    bad = contract("bad")
    object.__setattr__(bad, "fallback_action_type", "invalid")
    registry = snapshot(spec("r", bad))
    with pytest.raises(PipelinePaused):
        scheduler(registry, lambda *args: ActionCommand(action_type="invalid", target_seat=None, reasoning="x")).run_point(state("r"), SchedulePoint.NIGHT_ACTION)


def test_domain_values_are_strict_frozen_and_window_id_deterministic() -> None:
    event = DomainEvent("event:" + "1" * 16, "PLAYER_DIED", {"target_seat": 1, "cause": "attack"})
    assert stable_window_id("g", event.event_id, "c", 1) == stable_window_id("g", event.event_id, "c", 1)
    with pytest.raises(FrozenInstanceError): event.event_type = "X"
    with pytest.raises(TypeError): DomainEvent(1, "X", {})
    with pytest.raises(ValueError): DomainEvent("bad", "X", {})


def test_response_queue_is_bfs_filtered_idempotent_and_bounded() -> None:
    c = contract("react", responses=frozenset({"PLAYER_DIED"}))
    registry = snapshot(spec("r", c))
    queue = ResponseQueue(registry, max_depth=1, max_events=3)
    event = DomainEvent("event:" + "2" * 16, "PLAYER_DIED", {"target_seat": 1, "cause": "attack"})
    game = state("r")
    first = queue.open(game, event, depth=0)
    assert first == queue.open(game, event, depth=0)
    assert len(first) == 1 and first[0].window_id == stable_window_id("g", event.event_id, "react", 1)
    assert queue.open(game, DomainEvent("event:" + "3" * 16, "OTHER", {"target_seat": 1}), depth=0) == ()
    with pytest.raises(ResponseLimitExceeded): queue.open(game, DomainEvent("event:" + "4" * 16, "PLAYER_DIED", {"target_seat": 1}), depth=2)
    queue.open(game, DomainEvent("event:" + "5" * 16, "PLAYER_DIED", {"target_seat": 1}), depth=0)
    with pytest.raises(ResponseLimitExceeded): queue.open(game, DomainEvent("event:" + "6" * 16, "PLAYER_DIED", {"target_seat": 1}), depth=0)


@pytest.mark.parametrize("values,expected", [((0, 0, 0), True), ((1, 0, 0), False)])
def test_phase_gate(values, expected) -> None:
    assert Scheduler.can_advance(pending_requests=values[0], pending_effects=values[1], queued_events=values[2]) is expected
    with pytest.raises((TypeError, ValueError)): Scheduler.can_advance(pending_requests=True, pending_effects=0, queued_events=0)


def test_point_result_is_frozen_and_exact() -> None:
    result = PointResult((), (), (), "digest")
    assert result.faults == ()
    with pytest.raises(FrozenInstanceError): result.state_digest = "x"
    with pytest.raises(TypeError): PointResult([], (), (), "d")


def test_strict_value_and_queue_boundaries() -> None:
    with pytest.raises(ValueError): DomainEvent("event:" + "1" * 16, "X", {}, reason="bad reason")
    with pytest.raises(TypeError): DomainEvent("event:" + "1" * 16, "X", [])
    with pytest.raises(TypeError): DomainEvent("event:" + "1" * 16, "X", {1: "bad"})
    with pytest.raises(TypeError): DomainEvent("event:" + "1" * 16, "X", {"x": object()})
    with pytest.raises(ValueError): ResponseWindow("w", "e", "c", 0, 0)
    with pytest.raises(ValueError): ResponseWindow("w", "e", "c", 1, -1)
    with pytest.raises(ValueError): stable_window_id("g", "e", "c", 0)
    registry = snapshot()
    with pytest.raises(TypeError): ResponseQueue(object())
    with pytest.raises(ValueError): ResponseQueue(registry, max_depth=-1)
    queue = ResponseQueue(registry)
    with pytest.raises(TypeError): queue.open(object(), DomainEvent("event:" + "1" * 16, "X", {}), depth=0)
    with pytest.raises(ValueError): queue.open(state(), DomainEvent("event:" + "1" * 16, "X", {}), depth=True)


def test_response_filters_missing_target_player_role_and_reason() -> None:
    c = contract("react", responses=frozenset({"E"}))
    object.__setattr__(c, "response_reasons", frozenset({"ok"}))
    registry = snapshot(spec("r", c))
    game = state("r")
    assert ResponseQueue(registry).open(game, DomainEvent("event:" + "a" * 16, "E", {}), depth=0) == ()
    assert ResponseQueue(registry).open(game, DomainEvent("event:" + "b" * 16, "E", {"target_seat": 99}), depth=0) == ()
    game.players[1].role = "missing"
    assert ResponseQueue(registry).open(game, DomainEvent("event:" + "c" * 16, "E", {"target_seat": 1}), depth=0) == ()
    game.players[1].role = "r"
    assert ResponseQueue(registry).open(game, DomainEvent("event:" + "d" * 16, "E", {"target_seat": 1}, "wrong"), depth=0) == ()


def test_issue_strict_inputs_and_non_boolean_applicability() -> None:
    registry = snapshot(spec("r", contract("c")))
    with pytest.raises(TypeError): scheduler(registry).issue(object(), SchedulePoint.NIGHT_ACTION, registry)
    c = contract("bad"); object.__setattr__(c, "is_applicable", lambda context: 1)
    registry = snapshot(spec("r", c))
    with pytest.raises(PipelinePaused): scheduler(registry).issue(state("r"), SchedulePoint.NIGHT_ACTION, registry)


def test_empty_point_and_negative_gate() -> None:
    registry = snapshot()
    result = scheduler(registry).run_point(state(), SchedulePoint.NIGHT_ACTION)
    assert result.requests == result.commits == result.events == ()
    with pytest.raises(ValueError): Scheduler.can_advance(pending_requests=-1, pending_effects=0, queued_events=0)


def test_invalid_utf8_and_rule_execution_pause_are_sanitized() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        DomainEvent("event:" + "1" * 16, "\ud800", {})
    c = contract("broken"); object.__setattr__(c, "resolve", explode_resolve)
    registry = snapshot(spec("r", c))
    with pytest.raises(PipelinePaused) as caught:
        scheduler(registry).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert "secret" not in str(caught.value)


def test_validation_once_per_attempt_and_provider_failure_is_sanitized() -> None:
    registry = snapshot(spec("r", contract("c")))
    engine = scheduler(registry)
    calls = 0
    original = engine.validator.validate
    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)
    engine.validator.validate = counted
    engine.run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert calls == 1
    with pytest.raises(PipelinePaused) as caught:
        scheduler(registry, lambda *args: (_ for _ in ()).throw(RuntimeError("secret"))).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert "secret" not in str(caught.value)


def test_nonaggregate_reprojects_each_actor_at_current_revision() -> None:
    registry = snapshot(spec("r", contract("c")))
    seen = []
    def provider(request, context, attempt):
        seen.append((request.actor_seat, request.context_revision, context.revision))
        return ActionCommand(action_type="act", target_seat=None, reasoning="ok")
    result = scheduler(registry, provider).run_point(state("r", "r"), SchedulePoint.NIGHT_ACTION)
    assert seen == [(1, 0, 0), (2, 1, 1)]
    assert [item.context_revision for item in result.requests] == [0, 1]
    assert [item.revision for item in result.commits] == [1, 2]


def test_rule_failure_retries_fallback_once_and_failure_pauses() -> None:
    c = contract("c"); object.__setattr__(c, "resolve", fallback_resolve)
    registry = snapshot(spec("r", c))
    result = scheduler(registry).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert result.commits[0].revision == 1
    object.__setattr__(c, "resolve", always_fail)
    with pytest.raises(PipelinePaused):
        scheduler(registry, lambda *args: ActionCommand(action_type="pass", target_seat=None, reasoning="ok")).run_point(state("r"), SchedulePoint.NIGHT_ACTION)


def test_response_queue_enqueues_events_fifo_and_windows_stably() -> None:
    c = contract("react", responses=frozenset({"E"}))
    registry = snapshot(spec("r", c)); queue = ResponseQueue(registry)
    first = DomainEvent("event:" + "1" * 16, "E", {"target_seat": 1})
    second = DomainEvent("event:" + "2" * 16, "E", {"target_seat": 1})
    assert queue.enqueue(first, depth=0) is True
    assert queue.enqueue(first, depth=0) is False
    assert queue.enqueue(second, depth=1) is True
    assert queue.pop(state("r"))[0].event_id == first.event_id
    assert queue.pop(state("r"))[0].event_id == second.event_id
    assert queue.pop(state("r")) is None


def test_run_point_drains_reaction_events_breadth_first() -> None:
    primary = contract("primary")
    response = contract("response", point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    object.__setattr__(response, "react", react_event)
    registry = snapshot(spec("r", primary, response))
    result = scheduler(registry).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert [event["event_type"] for event in result.events] == ["DONE", "REACTED"]
    assert [commit.revision for commit in result.commits] == [1, 2]
    assert len(result.requests) == 2


def test_point_result_validates_elements_and_bounded_json() -> None:
    with pytest.raises(TypeError): PointResult((object(),), (), (), "d")
    with pytest.raises(TypeError): PointResult((), (object(),), (), "d")
    with pytest.raises(TypeError): PointResult((), (), (object(),), "d")
    cycle = {}; cycle["self"] = cycle
    with pytest.raises(ValueError): PointResult((), (), (cycle,), "d")


def test_aggregate_rerun_is_idempotent() -> None:
    registry = snapshot(spec("r", contract("group", aggregate=True))); game = state("r", "r")
    engine = scheduler(registry)
    first = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    second = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert second.commits == first.commits
    assert engine._revision(game) == 1


def test_frozen_json_limits_and_scalar_paths() -> None:
    payload = {"none": None, "bool": True, "int": 1, "float": 1.5, "list": ["x"]}
    event = DomainEvent("event:" + "e" * 16, "E", payload)
    assert event.payload["float"] == 1.5 and event.payload["list"] == ("x",)
    for bad in (-1, 2_147_483_648, float("nan"), object(), {1: "x"}):
        with pytest.raises((TypeError, ValueError)):
            DomainEvent("event:" + "e" * 16, "E", {"bad": bad})
    deep = value = {}
    for _ in range(65): value["x"] = {}; value = value["x"]
    with pytest.raises(ValueError): DomainEvent("event:" + "e" * 16, "E", deep)
    with pytest.raises(ValueError): DomainEvent("event:" + "e" * 16, "E", {str(i): i for i in range(10_001)})


def test_queue_strict_enqueue_pop_open_limits_and_reason_policy() -> None:
    registry = snapshot(spec("r", contract("react", responses=frozenset({"E"}))))
    queue = ResponseQueue(registry, max_events=1)
    with pytest.raises(TypeError): queue.enqueue(object(), depth=0)
    with pytest.raises(TypeError): queue.pop(object())
    first = DomainEvent("event:" + "7" * 16, "E", {"target_seat": 1}, "reason")
    queue.enqueue(first, depth=0)
    assert queue.pop(state("r"))[2] == ()
    with pytest.raises(ResponseLimitExceeded): queue.enqueue(DomainEvent("event:" + "8" * 16, "E", {}), depth=0)
    queue = ResponseQueue(registry, max_events=0)
    with pytest.raises(ResponseLimitExceeded): queue.open(state("r"), DomainEvent("event:" + "9" * 16, "E", {}), depth=0)


def test_response_without_react_uses_provider_and_resolver() -> None:
    response = contract("response", point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    object.__setattr__(response, "resolve", response_event)
    registry = snapshot(spec("r", contract("primary"), response))
    result = scheduler(registry).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert [event["event_type"] for event in result.events] == ["DONE", "REACTED"]


def test_aggregate_fallback_success_and_failures_pause() -> None:
    c = contract("group", aggregate=True); object.__setattr__(c, "aggregate", fallback_aggregate)
    registry = snapshot(spec("r", c))
    assert scheduler(registry).run_point(state("r", "r"), SchedulePoint.NIGHT_ACTION).commits[0].revision == 1
    object.__setattr__(c, "aggregate", failing_aggregate)
    with pytest.raises(PipelinePaused): scheduler(registry).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    with pytest.raises(PipelinePaused):
        scheduler(registry, lambda *args: ActionCommand(action_type="pass", target_seat=None, reasoning="ok")).run_point(state("r"), SchedulePoint.NIGHT_ACTION)


def test_internal_response_failures_are_sanitized() -> None:
    response = contract("response", point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    object.__setattr__(response, "react", always_fail)
    registry = snapshot(spec("r", contract("primary"), response))
    with pytest.raises(PipelinePaused): scheduler(registry).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    primary = contract("bad")
    object.__setattr__(primary, "resolve", lambda context, command: (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.EMIT_EVENT, context.action_key,
        payload={"event_type": "DONE", "payload": {"target_seat": context.actor_seat, "cause": "bad reason"}},
        expected_revision=context.revision, sort_key=(1,),
    ),))
    with pytest.raises(PipelinePaused): scheduler(snapshot(spec("r", primary))).run_point(state("r"), SchedulePoint.NIGHT_ACTION)


def test_defensive_response_boundaries(monkeypatch) -> None:
    registry = snapshot(spec("r", contract("c"))); game = state("r")
    queue = ResponseQueue(registry)
    event = DomainEvent("event:" + "a" * 16, "E", {"target_seat": 1})
    queue.enqueue(event, depth=0)
    assert queue.open(game, event, depth=0) == ()
    assert queue._open_new(game, event, 0) == ()
    with pytest.raises(PipelinePaused): Scheduler._domain(object(), 0, {})

    bad_window = ResponseWindow("w", event.event_id, "missing", 1, 0)
    calls = iter([(event, 0, (bad_window,)), None])
    monkeypatch.setattr(ResponseQueue, "pop", lambda self, state: next(calls))
    with pytest.raises(PipelinePaused): scheduler(registry).run_point(game, SchedulePoint.DAY_ACTION)


def test_response_projector_failure_is_sanitized() -> None:
    response = contract("response", point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    registry = snapshot(spec("r", contract("primary"), response)); engine = scheduler(registry)
    original = engine.projector.project
    def failing(*args, **kwargs):
        if kwargs.get("source_event_id") is not None: raise RuntimeError("secret")
        return original(*args, **kwargs)
    engine.projector.project = failing
    with pytest.raises(PipelinePaused) as caught: engine.run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert "secret" not in str(caught.value)


def test_validation_hook_failure_is_sanitized_and_control_exceptions_escape() -> None:
    c = contract("c")
    def fail(context, command): raise RuntimeError("SECRET")
    object.__setattr__(c, "validate", fail); registry = snapshot(spec("r", c))
    with pytest.raises(PipelinePaused) as caught:
        scheduler(registry).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert "SECRET" not in str(caught.value) + repr(caught.value) + rendered
    assert caught.value.__cause__ is None
    for exception in (KeyboardInterrupt, SystemExit):
        object.__setattr__(c, "validate", lambda context, command, kind=exception: (_ for _ in ()).throw(kind()))
        with pytest.raises(exception): scheduler(registry).run_point(state("r"), SchedulePoint.NIGHT_ACTION)


def test_second_actor_failure_preserves_first_committed_transaction() -> None:
    c = contract("c")
    def validate(context, command):
        if context.actor_seat == 2: raise RuntimeError("SECRET")
        return ()
    object.__setattr__(c, "validate", validate); registry = snapshot(spec("r", c)); game = state("r", "r")
    with pytest.raises(PipelinePaused): scheduler(registry).run_point(game, SchedulePoint.NIGHT_ACTION)
    assert scheduler(registry)._revision(game) == 1
    assert len(game._pipeline_runtime.commits) == 1


def test_fallback_validation_failure_is_sanitized() -> None:
    c = contract("c")
    def validate(context, command):
        if command.action_type == "pass": raise RuntimeError("SECRET")
        return ()
    object.__setattr__(c, "validate", validate); registry = snapshot(spec("r", c))
    with pytest.raises(PipelinePaused) as caught:
        scheduler(registry, lambda *args: ActionCommand(action_type="invalid", target_seat=None, reasoning="x")).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert "SECRET" not in str(caught.value) and caught.value.__cause__ is None


def test_run_point_serializes_same_state_across_scheduler_instances() -> None:
    registry = snapshot(spec("r", contract("c"))); game = state("r")
    first_entered, release_first, second_attempted, second_entered = Event(), Event(), Event(), Event()
    guard = Lock(); calls = 0; failures = []
    def provider(request, context, attempt):
        nonlocal calls
        with guard: calls += 1; current = calls
        if current == 1: first_entered.set(); release_first.wait()
        else: second_entered.set()
        return ActionCommand(action_type="act", target_seat=None, reasoning="ok")
    def run(engine, attempted=None):
        if attempted is not None: attempted.set()
        try: engine.run_point(game, SchedulePoint.NIGHT_ACTION)
        except BaseException as error: failures.append(error)
    first = Thread(target=run, args=(scheduler(registry, provider),)); first.start(); first_entered.wait()
    second = Thread(target=run, args=(scheduler(registry, provider), second_attempted)); second.start(); second_attempted.wait()
    try: assert not second_entered.is_set()
    finally: release_first.set(); first.join(); second.join()
    assert failures == [] and second_entered.is_set()
    assert scheduler(registry)._revision(game) == 1 and len(game._pipeline_runtime.commits) == 1


def test_rule_hard_timeout_returns_and_soft_budget_records_fault() -> None:
    blocked = Event(); c = contract("c")
    object.__setattr__(c, "validate", lambda context, command: blocked.wait() or ())
    registry = snapshot(spec("r", c)); start = monotonic()
    with pytest.raises(PipelinePaused, match="timed out"):
        Scheduler(registry, ContextProjector(), ActionValidator(), ActionResolver(), EffectApplier(),
                  lambda *args: ActionCommand(action_type="act", target_seat=None, reasoning="ok"),
                  hook_soft_ms=5, hook_hard_ms=15).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert monotonic() - start < .5
    blocked.set()

    delayed = Event()
    def slow(context, command): delayed.wait(.02); return ()
    object.__setattr__(c, "validate", slow)
    result = Scheduler(registry, ContextProjector(), ActionValidator(), ActionResolver(), EffectApplier(),
                       lambda *args: ActionCommand(action_type="act", target_seat=None, reasoning="ok"),
                       hook_soft_ms=5, hook_hard_ms=100).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert {fault["label"] for fault in result.faults} >= {"validation"}
    assert all(fault["code"] == "slow_rule" and fault["elapsed_bucket"] == "soft_exceeded" for fault in result.faults)


def test_scheduler_budget_config_is_strict() -> None:
    registry = snapshot()
    dependencies = (registry, ContextProjector(), ActionValidator(), ActionResolver(), EffectApplier(), lambda *args: None)
    for soft, hard in ((True, 10), (0, 10), (20, 10)):
        with pytest.raises((TypeError, ValueError)): Scheduler(*dependencies, hook_soft_ms=soft, hook_hard_ms=hard)


def test_response_queue_enqueue_is_thread_safe_and_idempotent() -> None:
    queue = ResponseQueue(snapshot(), max_events=1)
    event = DomainEvent("event:" + "f" * 16, "E", {})
    barrier = Barrier(3); results = []
    def enqueue(): barrier.wait(); results.append(queue.enqueue(event, depth=0))
    threads = [Thread(target=enqueue), Thread(target=enqueue)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join()
    assert sorted(results) == [False, True]


def test_run_point_serializes_with_direct_effect_applier_mutation() -> None:
    registry = snapshot(spec("r", contract("c"))); game = state("r")
    provider_entered, release_provider = Event(), Event()
    direct_started, direct_done, scheduler_done = Event(), Event(), Event()
    failures = []

    def provider(request, context, attempt):
        provider_entered.set()
        release_provider.wait()
        return ActionCommand(action_type="act", target_seat=None, reasoning="ok")

    external_key = "external-action"
    effects = (GameEffect(
        derive_effect_id(external_key, 0), EffectKind.ACCEPT_ACTION, external_key,
        expected_revision=0, sort_key=(0,),
    ),)
    permission = EffectPermission(
        1, frozenset(), frozenset(), frozenset({1}), frozenset()
    )

    def run_scheduler():
        try: scheduler(registry, provider).run_point(game, SchedulePoint.NIGHT_ACTION)
        except BaseException as error: failures.append(error)
        finally: scheduler_done.set()

    def apply_directly():
        direct_started.set()
        try: EffectApplier().apply(game, effects, permission)
        except BaseException as error: failures.append(error)
        finally: direct_done.set()

    scheduler_thread = Thread(target=run_scheduler); scheduler_thread.start()
    assert provider_entered.wait(1)
    direct_thread = Thread(target=apply_directly); direct_thread.start()
    assert direct_started.wait(1)
    try:
        assert not direct_done.wait(.05)
    finally:
        release_provider.set()
    assert scheduler_done.wait(1)
    assert direct_done.wait(1)
    scheduler_thread.join(); direct_thread.join()
    assert failures == []
    assert game._pipeline_runtime.revision == 2
    assert tuple(game._pipeline_runtime.commits)[-1] == external_key


def test_rule_call_propagates_arbitrary_non_exception_base_exception() -> None:
    registry = snapshot(); engine = scheduler(registry)

    def raise_control_flow():
        raise CustomControlFlow()

    with pytest.raises(CustomControlFlow):
        engine._rule_call("control", raise_control_flow)


def test_rule_capacity_and_public_run_boundaries(monkeypatch) -> None:
    registry = snapshot(); engine = scheduler(registry)
    monkeypatch.setattr(scheduler_module._RULE_SLOTS, "acquire", lambda blocking=False: False)
    with pytest.raises(PipelinePaused, match="capacity"): engine._rule_call("x", lambda: None)
    with pytest.raises(TypeError): engine.run_point(object(), SchedulePoint.NIGHT_ACTION)


def test_queue_locked_branches_remain_bounded() -> None:
    registry = snapshot(); event = DomainEvent("event:" + "0" * 16, "E", {})
    queue = ResponseQueue(registry, max_events=1)
    assert queue.enqueue(event, depth=0) and not queue.enqueue(event, depth=0)
    assert queue.pop(state()) is not None and queue.pop(state()) is None
    assert queue.open(state(), event, depth=0) == ()
    full = ResponseQueue(registry, max_events=0)
    with pytest.raises(ResponseLimitExceeded): full.enqueue(event, depth=0)
    full._seen.add("occupied")
    with pytest.raises(ResponseLimitExceeded): full.open(state(), event, depth=0)
