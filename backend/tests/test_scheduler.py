from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
import traceback
from threading import Barrier, Event, Lock, Thread
from time import monotonic, sleep

import pytest
import app.core.scheduler as scheduler_module

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import (
    CommitResult, EffectApplier, EffectPermission, EffectRejected, _Runtime, derive_effect_id,
)
from app.core.scheduler import (
    DomainEvent, PipelinePaused, PointResult, ResponseLimitExceeded,
    ResponseQueue, ResponseWindow, Scheduler, stable_window_id,
)
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect,
    RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.registry import RegistrySnapshot
from app.roles.registry import builtin_registry


class CustomControlFlow(BaseException):
    pass


APPLICABILITY_CALLS = 0


def counted_applicable(context: ActionContext) -> bool:
    global APPLICABILITY_CALLS
    APPLICABILITY_CALLS += 1
    return True


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


def react_final(context: ActionContext) -> tuple[GameEffect, ...]:
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.EMIT_EVENT,
        context.action_key, payload={"event_type": "FINAL", "payload": {"target_seat": context.actor_seat}},
        expected_revision=context.revision, source_event_id=context.source_event_id, sort_key=(1,),
    ),)


def slow_react_event(context: ActionContext) -> tuple[GameEffect, ...]:
    sleep(.02)
    return react_event(context)


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


SELECTED_CONTEXTS = []


def validate_selected(context: ActionContext, command: ActionCommand) -> tuple:
    SELECTED_CONTEXTS.append(("validate", command.target_seat, context))
    return ()


def validate_selected_retry(context: ActionContext, command: ActionCommand) -> tuple:
    SELECTED_CONTEXTS.append(("validate", command.target_seat, context))
    return () if command.target_seat == 2 else (RuleViolation("retry", "retry"),)


def resolve_selected(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    SELECTED_CONTEXTS.append(("resolve", command.target_seat, context))
    return ()


def resolve_selected_fail(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    SELECTED_CONTEXTS.append(("resolve", command.target_seat, context))
    if command.action_type != "pass": raise RuntimeError("retry fallback")
    return ()


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


def selected_contract(*, validate=validate_selected, resolve=resolve_selected) -> ActionContract:
    return ActionContract(
        contract_id="selected", schedule_point=SchedulePoint.NIGHT_ACTION, order=1,
        action_types=("act", "pass"), actions_requiring_target=frozenset({"act"}),
        fallback_action_type="pass", visibility_namespaces=frozenset({"PUBLIC"}),
        selected_target_fact_namespaces=frozenset({"camp_label"}),
        is_applicable=applicable, validate=validate, resolve=resolve,
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


def test_main_journal_resumes_normal_commit_before_domain_without_provider_repeat(monkeypatch) -> None:
    calls = []
    registry = snapshot(spec("r", contract("c"))); game = state("r")
    engine = scheduler(registry, lambda *args: (calls.append(args), ActionCommand(action_type="act", target_seat=None, reasoning="ok"))[1])
    original = engine._domain; failed = [False]
    def once(*args):
        if not failed[0]: failed[0] = True; raise PipelinePaused("after commit")
        return original(*args)
    monkeypatch.setattr(engine, "_domain", once)
    with pytest.raises(PipelinePaused, match="after commit"): engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert len(calls) == 1 and len(result.commits) == 1 and result.events[0]["event_type"] == "DONE"
    assert game._pipeline_runtime.revision == 1


def test_main_journal_resumes_aggregate_and_settlement_before_domain(monkeypatch) -> None:
    aggregate_registry = snapshot(spec("r", contract("group", aggregate=True)))
    aggregate_game = state("r", "r"); aggregate_engine = scheduler(aggregate_registry)
    aggregate_calls = []; original_aggregate = aggregate_engine.resolver.aggregate_effects
    def counted(*args): aggregate_calls.append(1); return original_aggregate(*args)
    aggregate_engine.resolver.aggregate_effects = counted
    original_domain = aggregate_engine._domain; failed = [False]
    def aggregate_domain(*args):
        if not failed[0]: failed[0] = True; raise PipelinePaused("aggregate domain")
        return original_domain(*args)
    monkeypatch.setattr(aggregate_engine, "_domain", aggregate_domain)
    with pytest.raises(PipelinePaused): aggregate_engine.run_point(aggregate_game, SchedulePoint.NIGHT_ACTION)
    aggregate = aggregate_engine.run_point(aggregate_game, SchedulePoint.NIGHT_ACTION)
    assert len(aggregate_calls) == 1 and len(aggregate.commits) == 1

    settlement_registry = snapshot(spec("r")); settlement_game = state("r")
    settlement_game._pipeline_runtime = _Runtime(pending_damage=({"target": 1, "amount": 1, "cause": "poison"},))
    settlement_engine = scheduler(settlement_registry); settle_calls = []
    original_settle = settlement_engine.applier.settle_pending
    def settle(*args, **kwargs): settle_calls.append(1); return original_settle(*args, **kwargs)
    settlement_engine.applier.settle_pending = settle
    original_domain = settlement_engine._domain; failed = [False]
    def settlement_domain(*args):
        if not failed[0]: failed[0] = True; raise PipelinePaused("settlement domain")
        return original_domain(*args)
    monkeypatch.setattr(settlement_engine, "_domain", settlement_domain)
    with pytest.raises(PipelinePaused): settlement_engine.run_point(settlement_game, SchedulePoint.NIGHT_COMMIT)
    settled = settlement_engine.run_point(settlement_game, SchedulePoint.NIGHT_COMMIT)
    assert len(settle_calls) == 1 and len(settled.commits) == 1


def test_main_journal_completion_replays_and_conversion_failure_skips_main(monkeypatch) -> None:
    calls = []; registry = snapshot(spec("r", contract("c"))); game = state("r")
    engine = scheduler(registry, lambda *args: (calls.append(args), ActionCommand(action_type="act", target_seat=None, reasoning="ok"))[1])
    real_result = scheduler_module.PointResult; attempts = []
    def fail_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1: raise RuntimeError("result conversion")
        return real_result(*args, **kwargs)
    monkeypatch.setattr(scheduler_module, "PointResult", fail_once)
    with pytest.raises(RuntimeError, match="result conversion"): engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    first = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    second = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert len(calls) == 1 and first == second and len(first.commits) == 1
    assert len(attempts) == 3


def test_point_journal_key_isolates_round_and_registry() -> None:
    calls = []
    first_registry = snapshot(spec("r", contract("c")))
    second_registry = RegistrySnapshot(first_registry.specs, "b" * 64)
    game = state("r")
    provider = lambda *args: (calls.append(args), ActionCommand(action_type="act", target_seat=None, reasoning="ok"))[1]
    scheduler(first_registry, provider).run_point(game, SchedulePoint.NIGHT_ACTION)
    game.round_number = 2
    scheduler(first_registry, provider).run_point(game, SchedulePoint.NIGHT_ACTION)
    scheduler(second_registry, provider).run_point(game, SchedulePoint.NIGHT_ACTION)
    assert len(calls) == 3


def test_response_journal_resumes_after_committed_event_domain_failure(monkeypatch) -> None:
    response = contract("response", point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    object.__setattr__(response, "react", react_event)
    registry = snapshot(spec("r", contract("primary"), response)); game = state("r")
    engine = scheduler(registry); calls = []; original_react = engine.resolver.react_effects
    def counted(*args): calls.append(args[2].contract_id); return original_react(*args)
    engine.resolver.react_effects = counted
    original_domain = engine._domain; failed = [False]
    def fail_new_event(commit, ordinal, raw):
        if raw["event_type"] == "REACTED" and not failed[0]: failed[0] = True; raise PipelinePaused("response domain")
        return original_domain(commit, ordinal, raw)
    monkeypatch.setattr(engine, "_domain", fail_new_event)
    with pytest.raises(PipelinePaused, match="response domain"): engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert calls == ["response"] and [event["event_type"] for event in result.events] == ["DONE", "REACTED"]
    assert [commit.revision for commit in result.commits] == [1, 2]


def test_response_journal_resumes_second_layer_without_repeating_hooks(monkeypatch) -> None:
    first = contract("first", point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    second = contract("second", point=SchedulePoint.DAY_ACTION, responses=frozenset({"REACTED"}))
    object.__setattr__(first, "react", react_event); object.__setattr__(second, "react", react_final)
    registry = snapshot(spec("r", contract("primary"), first, second)); game = state("r")
    engine = scheduler(registry); calls = []; original_react = engine.resolver.react_effects
    def counted(*args): calls.append(args[2].contract_id); return original_react(*args)
    engine.resolver.react_effects = counted
    original_domain = engine._domain; failed = [False]
    def fail_final(commit, ordinal, raw):
        if raw["event_type"] == "FINAL" and not failed[0]: failed[0] = True; raise PipelinePaused("second layer")
        return original_domain(commit, ordinal, raw)
    monkeypatch.setattr(engine, "_domain", fail_final)
    with pytest.raises(PipelinePaused): engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert calls == ["first", "second"]
    assert [event["event_type"] for event in result.events] == ["DONE", "REACTED", "FINAL"]


def test_response_journal_checkpoints_skipped_window_before_later_failure(monkeypatch) -> None:
    skipped = contract("skip", 1, point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    active = contract("active", 2, point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    object.__setattr__(skipped, "react", react_event); object.__setattr__(active, "react", react_event)
    registry = snapshot(spec("r", contract("primary"), skipped, active)); game = state("r")
    engine = scheduler(registry); limits = []; original_limit = engine._limit_reached
    def limit(context, contract):
        limits.append(contract.contract_id)
        return contract.contract_id == "skip" or original_limit(context, contract)
    monkeypatch.setattr(engine, "_limit_reached", limit)
    original_domain = engine._domain; failed = [False]
    def fail_reacted(commit, ordinal, raw):
        if raw["event_type"] == "REACTED" and not failed[0]: failed[0] = True; raise PipelinePaused("later")
        return original_domain(commit, ordinal, raw)
    monkeypatch.setattr(engine, "_domain", fail_reacted)
    with pytest.raises(PipelinePaused): engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert limits.count("skip") == 1 and limits.count("active") == 1


def test_response_journal_preserves_slow_fault_across_retry(monkeypatch) -> None:
    response = contract("response", point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    object.__setattr__(response, "react", slow_react_event)
    registry = snapshot(spec("r", contract("primary"), response)); game = state("r")
    engine = Scheduler(registry, ContextProjector(), ActionValidator(), ActionResolver(), EffectApplier(),
                       lambda *args: ActionCommand(action_type="act", target_seat=None, reasoning="ok"),
                       hook_soft_ms=1, hook_hard_ms=100)
    original_domain = engine._domain; failed = [False]
    def fail_reacted(commit, ordinal, raw):
        if raw["event_type"] == "REACTED" and not failed[0]: failed[0] = True; raise PipelinePaused("slow retry")
        return original_domain(commit, ordinal, raw)
    monkeypatch.setattr(engine, "_domain", fail_reacted)
    with pytest.raises(PipelinePaused): engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert [fault["label"] for fault in result.faults].count("react") == 1


def test_journal_empty_settlement_and_gated_aggregate_continue_are_covered() -> None:
    empty = scheduler(snapshot(spec("passive"))).run_point(state("passive"), SchedulePoint.NIGHT_COMMIT)
    assert empty.commits == ()
    grouped = contract("group", aggregate=True); object.__setattr__(grouped, "per_round_limit", 1)
    later = contract("later", order=2)
    registry = snapshot(spec("r", grouped, later)); game = state("r")
    game._pipeline_runtime = _Runtime(action_counts={
        "window": {}, "round": {"1\0group\0" + "1": 1}, "game": {},
    })
    issued = scheduler(registry).issue(game, SchedulePoint.NIGHT_ACTION, registry)
    assert [item.contract.contract_id for item in issued] == ["later"]


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


def test_validation_once_per_attempt_and_provider_failure_is_sanitized(caplog) -> None:
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
    with caplog.at_level(logging.ERROR, logger="app.core.scheduler"):
        with pytest.raises(PipelinePaused) as caught:
            scheduler(registry, lambda *args: (_ for _ in ()).throw(RuntimeError("secret"))).run_point(state("r"), SchedulePoint.NIGHT_ACTION)
    assert "secret" not in str(caught.value)
    assert any("Command provider failed" in record.message for record in caplog.records)
    assert any("secret" in record.exc_text for record in caplog.records)


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


def test_night_commit_only_settles_pending_in_stable_order_and_is_idempotent() -> None:
    forbidden = contract("forbidden", point=SchedulePoint.NIGHT_COMMIT)
    registry = snapshot(spec("r", forbidden)); game = state("r", "r", "r")
    game._pipeline_runtime = _Runtime(
        pending_damage=(
            {"target": 3, "amount": 2, "cause": "wolf_kill"},
            {"target": 1, "amount": 1, "cause": "poison"},
            {"target": 2, "amount": 1, "cause": "wolf_kill"},
        ),
        pending_protection=({"target": 2, "amount": 1},),
    )
    calls = []
    engine = scheduler(registry, lambda *args: calls.append(args))
    first = engine.run_point(game, SchedulePoint.NIGHT_COMMIT)
    revision = game._pipeline_runtime.revision
    second = engine.run_point(game, SchedulePoint.NIGHT_COMMIT)
    assert calls == [] and first.requests == second.requests == ()
    assert [event["payload"]["seat"] for event in first.events] == [1, 3]
    assert [report.player_seat for report in game.death_history] == [1, 3]
    assert len(first.commits) == 1 and second == first
    assert game._pipeline_runtime.revision == revision == 1
    assert game.players[2].is_alive and game._pipeline_runtime.pending_damage == ()


@pytest.mark.parametrize("cause,reacts", [("poison", False), ("wolf_kill", True)])
def test_night_commit_routes_public_deaths_through_response_queue(cause, reacts) -> None:
    registry = builtin_registry.freeze()
    game = GameState("night-commit", phase=GamePhase.NIGHT, round_number=1, players={
        1: PlayerState(1, "wolf-killer-hunter", "good"),
        2: PlayerState(2, "wolf-killer-villager", "good"),
    })
    game._pipeline_runtime = _Runtime(
        role_resources={1: {"gun": 1}},
        pending_damage=({"target": 1, "amount": 1, "cause": cause},),
    )
    calls = []
    def provider(request, context, attempt):
        calls.append((request.actor_seat, context.revision))
        return ActionCommand(action_type="shoot", target_seat=2, reasoning="ok")
    result = scheduler(registry, provider).run_point(game, SchedulePoint.NIGHT_COMMIT)
    assert bool(calls) is reacts
    assert len(result.commits) == (3 if reacts else 1)
    assert calls in ([], [(1, 1)])
    if reacts:
        # The hunter's reaction damage is settled by a same-point follow-up
        # settlement: the victim dies this night instead of lingering pending.
        assert game._pipeline_runtime.pending_damage == ()
        assert game.players[2].is_alive is False
        assert [(item.player_seat, item.cause) for item in game.death_history] == [
            (1, cause), (2, "hunter_shot"),
        ]
        assert [event["event_type"] for event in result.events] == [
            "PLAYER_DIED", "HUNTER_SHOT", "HUNTER_REASONING", "PLAYER_DIED",
        ]
    else:
        assert game._pipeline_runtime.pending_damage == ()
    assert result.state_digest == result.commits[-1].state_digest


def _chain_damage(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    if command.action_type != "hit" or command.target_seat is None:
        return ()
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_DAMAGE,
        context.action_key, target_seat=command.target_seat,
        payload={"target": command.target_seat, "amount": 1, "cause": "bomb"},
        expected_revision=context.revision, source_event_id=context.source_event_id,
        sort_key=(1,),
    ),)


def _chain_contract() -> ActionContract:
    return ActionContract(
        contract_id="chain", schedule_point=SchedulePoint.DAWN_REACTION, order=1,
        action_types=("hit", "pass"), actions_requiring_target=frozenset({"hit"}),
        fallback_action_type="pass", allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE}),
        visibility_namespaces=frozenset({"PUBLIC"}),
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=frozenset({"wolf_kill", "bomb"}),
        resolve=_chain_damage,
    )


def _chain_spec() -> RoleSpec:
    return RoleSpec(
        role_id="bomb", camp_id="good", contracts=(_chain_contract(),),
        allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE}),
        visibility_namespaces=frozenset({"PUBLIC"}),
    )


def _chain_game(size: int, pending: tuple[dict[str, object], ...]) -> GameState:
    game = GameState("bomb-game", phase=GamePhase.NIGHT, round_number=1, players={
        i: PlayerState(i, "bomb", "good") for i in range(1, size + 1)
    })
    game._pipeline_runtime = _Runtime(pending_damage=pending)
    return game


def test_followup_batch_detects_next_unused_batch_and_cap() -> None:
    from app.core.night_settlement import settlement_key
    game = state("r")
    commits = [
        CommitResult(settlement_key(game.game_id, 1, batch), (), 0, (), "digest")
        for batch in range(1, 9)
    ]
    assert Scheduler._followup_batch(game, commits) == 9
    assert Scheduler._followup_batch(game, commits[:3]) == 4
    assert Scheduler._followup_batch(game, []) == 1


def test_settle_pending_rejects_non_game_state() -> None:
    engine = scheduler(builtin_registry.freeze())
    with pytest.raises(TypeError, match="state must be GameState"):
        engine.settle_pending(object())  # type: ignore[arg-type]


def test_settle_pending_returns_none_without_pending_effects() -> None:
    registry = builtin_registry.freeze()
    game = GameState("settle-none", phase=GamePhase.VOTE_RESOLUTION, round_number=2, players={
        1: PlayerState(1, "wolf-killer-villager", "good"),
    })
    engine = scheduler(registry)
    assert engine.settle_pending(game) is None
    game._pipeline_runtime = _Runtime()
    assert engine.settle_pending(game) is None


def test_settle_pending_uses_next_free_batch_and_is_idempotent() -> None:
    from app.core.night_settlement import settlement_key
    registry = builtin_registry.freeze()
    game = GameState("settle-day", phase=GamePhase.VOTE_RESOLUTION, round_number=2, players={
        1: PlayerState(1, "wolf-killer-villager", "good"),
        2: PlayerState(2, "wolf-killer-villager", "good"),
    })
    key0 = settlement_key("settle-day", 2, 0)
    stale = CommitResult(key0, ("e",), 0, (), "digest")
    game._pipeline_runtime = _Runtime(
        commits={key0: stale},
        pending_damage=({"target": 2, "amount": 1, "cause": "hunter_shot"},),
    )
    engine = scheduler(registry)
    settlement = engine.settle_pending(game)
    assert settlement is not None
    assert settlement.action_key == settlement_key("settle-day", 2, 1)
    assert game.players[2].is_alive is False
    assert game._pipeline_runtime.pending_damage == ()
    assert [(item.player_seat, item.cause, item.round_number) for item in game.death_history] == [
        (2, "hunter_shot", 2),
    ]
    assert engine.settle_pending(game) is None


def test_settle_pending_raises_when_settlement_batches_are_exhausted() -> None:
    from app.core.night_settlement import settlement_key
    registry = builtin_registry.freeze()
    game = GameState("settle-cap", phase=GamePhase.VOTE_RESOLUTION, round_number=2, players={
        1: PlayerState(1, "wolf-killer-villager", "good"),
        2: PlayerState(2, "wolf-killer-villager", "good"),
    })
    commits = {
        settlement_key("settle-cap", 2, batch): CommitResult(
            settlement_key("settle-cap", 2, batch), ("e",), 0, (), "digest",
        )
        for batch in range(1, 9)
    }
    game._pipeline_runtime = _Runtime(
        commits=commits,
        pending_damage=({"target": 2, "amount": 1, "cause": "hunter_shot"},),
    )
    engine = scheduler(registry)
    with pytest.raises(PipelinePaused, match="settlement batch cap exceeded"):
        engine.settle_pending(game)


def test_night_commit_followup_settles_reaction_damage_in_chain() -> None:
    registry = snapshot(_chain_spec())
    game = _chain_game(3, ({"target": 1, "amount": 1, "cause": "wolf_kill"},))
    calls = []
    def provider(request, context, attempt):
        calls.append(request.actor_seat)
        if request.actor_seat == 3:
            return ActionCommand(action_type="pass", target_seat=None, reasoning="stop")
        return ActionCommand(action_type="hit", target_seat=request.actor_seat + 1, reasoning="chain")
    result = scheduler(registry, provider).run_point(game, SchedulePoint.NIGHT_COMMIT)
    assert calls == [1, 2, 3]
    assert [(item.player_seat, item.cause) for item in game.death_history] == [
        (1, "wolf_kill"), (2, "bomb"), (3, "bomb"),
    ]
    assert game._pipeline_runtime.pending_damage == ()
    assert [event["event_type"] for event in result.events] == [
        "PLAYER_DIED", "PLAYER_DIED", "PLAYER_DIED",
    ]
    # settlement + 3 responses (the last is a pass) + 2 follow-up settlements
    assert len(result.commits) == 6


def test_night_commit_followup_chain_stops_at_settlement_cap() -> None:
    registry = snapshot(_chain_spec())
    game = _chain_game(10, ({"target": 1, "amount": 1, "cause": "wolf_kill"},))
    calls = []
    def provider(request, context, attempt):
        calls.append(request.actor_seat)
        return ActionCommand(action_type="hit", target_seat=request.actor_seat + 1, reasoning="chain")
    scheduler(registry, provider).run_point(game, SchedulePoint.NIGHT_COMMIT)
    assert [player.is_alive for player in game.players.values()] == [False] * 9 + [True]
    assert [(item.player_seat, item.cause) for item in game.death_history] == [
        (1, "wolf_kill"),
        *[(seat, "bomb") for seat in range(2, 10)],
    ]
    assert len(calls) == 9
    assert game._pipeline_runtime.pending_damage == (
        {"target": 10, "amount": 1, "cause": "bomb"},
    )


def test_night_commit_followup_settlement_resumes_without_repeat(monkeypatch) -> None:
    registry = snapshot(_chain_spec())
    game = _chain_game(3, ({"target": 1, "amount": 1, "cause": "wolf_kill"},))
    calls = []
    def provider(request, context, attempt):
        calls.append(request.actor_seat)
        if request.actor_seat == 3:
            return ActionCommand(action_type="pass", target_seat=None, reasoning="stop")
        return ActionCommand(action_type="hit", target_seat=request.actor_seat + 1, reasoning="chain")
    engine = scheduler(registry, provider)
    settle_batches = []
    original_settle = engine.applier.settle_pending
    def counted_settle(*args, **kwargs):
        settle_batches.append(kwargs.get("batch", 0))
        return original_settle(*args, **kwargs)
    engine.applier.settle_pending = counted_settle
    original_domain = engine._domain; failed = [False]
    def flaky_domain(commit, ordinal, raw):
        if raw.get("payload", {}).get("seat") == 2 and not failed[0]:
            failed[0] = True; raise PipelinePaused("follow-up domain")
        return original_domain(commit, ordinal, raw)
    monkeypatch.setattr(engine, "_domain", flaky_domain)
    with pytest.raises(PipelinePaused, match="follow-up domain"):
        engine.run_point(game, SchedulePoint.NIGHT_COMMIT)
    result = engine.run_point(game, SchedulePoint.NIGHT_COMMIT)
    assert settle_batches == [0, 1, 2]
    assert calls == [1, 2, 3]
    assert [(item.player_seat, item.cause) for item in game.death_history] == [
        (1, "wolf_kill"), (2, "bomb"), (3, "bomb"),
    ]
    assert result.state_digest == result.commits[-1].state_digest


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
    assert len(first.commits) == 1
    assert second == first
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


def test_event_payload_strings_accept_up_to_2000_chars() -> None:
    event = DomainEvent("event:" + "e" * 16, "E", {"text": "a" * 2000, "cjk": "汉" * 300})
    assert len(event.payload["text"]) == 2000
    assert len(event.payload["cjk"]) == 300
    result = PointResult((), (), ({"event_type": "E", "payload": {"text": "a" * 1500}},), "d")
    assert len(result.events[0]["payload"]["text"]) == 1500
    with pytest.raises(ValueError):
        DomainEvent("event:" + "e" * 16, "E", {"text": "a" * 2001})
    with pytest.raises(ValueError):
        PointResult((), (), ({"event_type": "E", "payload": {"text": "a" * 2001}},), "d")


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

    emitted = CommitResult("manual", (), 0, ({"event_type": "E", "payload": {"target_seat": 1}, "visibility": ("PUBLIC",)},), "d")
    game._pipeline_runtime = _Runtime(revision=0, events=emitted.events, commits={"manual": emitted})
    game.accepted_action_keys.add("manual")
    bad_window = ResponseWindow("w", event.event_id, "missing", 1, 0)
    monkeypatch.setattr(ResponseQueue, "open", lambda self, state, raw, depth: (bad_window,))
    engine = scheduler(registry)
    point_key = scheduler_module.PointKey("g", 1, "night", SchedulePoint.DAY_ACTION, registry.digest)
    scheduler_module.point_journal(game).put(point_key, scheduler_module.PointCheckpoint(
        (), (), (emitted,), emitted.events, (), (scheduler_module.PendingEvent(0, 0, 0),),
        scheduler_module.WorkCursor("response", 0, 0), work_count=0,
    ))
    with pytest.raises(PipelinePaused): engine.run_point(game, SchedulePoint.DAY_ACTION)


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
    assert failures == [] and not second_entered.is_set() and calls == 1
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


def test_model_collection_releases_state_lock_and_rejects_late_stale_result() -> None:
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
    # Model I/O must not hold the state transaction lock. A competing writer
    # can commit, and the collected provider result is then rejected by CAS.
    assert direct_done.wait(.2)
    release_provider.set()
    assert scheduler_done.wait(1)
    assert direct_done.wait(1)
    scheduler_thread.join(); direct_thread.join()
    assert len(failures) == 1
    assert isinstance(failures[0], PipelinePaused)
    assert "stale state revision" in str(failures[0])
    assert game._pipeline_runtime.revision == 1
    assert tuple(game._pipeline_runtime.commits) == (external_key,)


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


def test_issue_initializes_role_resources_before_context_and_is_idempotent() -> None:
    c = contract("c")
    role = spec("r", c)
    object.__setattr__(role, "initial_resources", {"charge": 1})
    registry = snapshot(role); game = state("r"); engine = scheduler(registry)
    first = engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)
    second = engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)
    assert first[0].context_revision == second[0].context_revision == 1
    assert engine._revision(game) == 1
    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert result.commits[0].revision == 2


def test_issue_without_declared_resources_remains_revision_zero() -> None:
    registry = snapshot(spec("r", contract("c"))); game = state("r")
    assert scheduler(registry).issue(game, SchedulePoint.NIGHT_ACTION, registry)[0].context_revision == 0
    assert not hasattr(game, "_pipeline_runtime")


def test_issue_propagates_invalid_initial_resource_without_runtime() -> None:
    role = spec("r", contract("c"))
    object.__setattr__(role, "initial_resources", {"charge": "invalid"})
    registry = snapshot(role); game = state("r")
    with pytest.raises(EffectRejected, match="invalid initial resource"):
        scheduler(registry).issue(game, SchedulePoint.NIGHT_ACTION, registry)
    assert not hasattr(game, "_pipeline_runtime")


def test_concurrent_issue_initializes_resources_once() -> None:
    role = spec("r", contract("c"))
    object.__setattr__(role, "initial_resources", {"charge": 1})
    registry = snapshot(role); game = state("r"); engine = scheduler(registry)
    barrier = Barrier(3); revisions = []; failures = []

    def issue() -> None:
        barrier.wait()
        try:
            revisions.append(
                engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)[0].context_revision
            )
        except Exception as error:
            failures.append(error)

    threads = (Thread(target=issue), Thread(target=issue))
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert failures == []
    assert revisions == [1, 1]
    assert engine._revision(game) == 1


def test_accepted_action_limits_gate_before_provider_and_applicability() -> None:
    global APPLICABILITY_CALLS
    APPLICABILITY_CALLS = 0
    calls = {"provider": 0, "applicable": 0}

    def provider(request, context, attempt):
        calls["provider"] += 1
        return ActionCommand(action_type="act", target_seat=None, reasoning="ok")

    c = contract("c", applies=counted_applicable)
    object.__setattr__(c, "per_round_limit", 1)
    object.__setattr__(c, "per_game_limit", 1)
    registry = snapshot(spec("r", c)); game = state("r"); engine = scheduler(registry, provider)
    assert len(engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)) == 1
    assert calls["provider"] == 0 and APPLICABILITY_CALLS == 1
    engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    before = (calls["provider"], APPLICABILITY_CALLS)
    assert engine.issue(game, SchedulePoint.NIGHT_ACTION, registry) == ()
    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert result.requests and result.commits
    assert (calls["provider"], APPLICABILITY_CALLS) == before


def test_provider_sees_base_context_while_validation_and_resolve_see_selected_target() -> None:
    SELECTED_CONTEXTS.clear(); c = selected_contract()
    registry = snapshot(spec("r", c), spec("other")); game = state("r", "other")
    game.players[2].camp = "werewolf"
    provider_contexts = []
    def provider(request, context, attempt):
        provider_contexts.append(context)
        return ActionCommand(action_type="act", target_seat=2, reasoning="check")
    scheduler(registry, provider).run_point(game, SchedulePoint.NIGHT_ACTION)
    assert "selected_target" not in provider_contexts[0].facts
    assert [(kind, target) for kind, target, _ in SELECTED_CONTEXTS] == [
        ("validate", 2), ("resolve", 2),
    ]
    assert all(item.facts["selected_target"] == {
        "seat": 2, "camp_label": "werewolf",
    } for _, _, item in SELECTED_CONTEXTS)


def test_retry_reprojects_each_target_without_leaking_previous_selection() -> None:
    SELECTED_CONTEXTS.clear(); c = selected_contract(validate=validate_selected_retry)
    registry = snapshot(spec("r", c), spec("other")); game = state("r", "other", "other")
    game.players[2].camp = "werewolf"
    targets = iter((3, 2))
    def provider(request, context, attempt):
        assert "selected_target" not in context.facts
        return ActionCommand(action_type="act", target_seat=next(targets), reasoning="check")
    scheduler(registry, provider).run_point(game, SchedulePoint.NIGHT_ACTION)
    projected = [item.facts["selected_target"] for kind, _, item in SELECTED_CONTEXTS if kind == "validate"]
    assert projected == [
        {"seat": 3, "camp_label": "good"},
        {"seat": 2, "camp_label": "werewolf"},
    ]
    assert SELECTED_CONTEXTS[-1][2].facts["selected_target"]["seat"] == 2


def test_resolver_fallback_reprojects_pass_without_selected_target() -> None:
    SELECTED_CONTEXTS.clear(); c = selected_contract(resolve=resolve_selected_fail)
    registry = snapshot(spec("r", c), spec("other")); game = state("r", "other")
    scheduler(registry, lambda *args: ActionCommand(
        action_type="act", target_seat=2, reasoning="check"
    )).run_point(game, SchedulePoint.NIGHT_ACTION)
    assert [(kind, target) for kind, target, _ in SELECTED_CONTEXTS] == [
        ("validate", 2), ("resolve", 2), ("validate", None), ("resolve", None),
    ]
    assert "selected_target" not in SELECTED_CONTEXTS[-1][2].facts


def test_contract_without_selected_declaration_preserves_context_identity() -> None:
    registry = snapshot(spec("r", contract("c"))); game = state("r")
    identities = []
    original = scheduler(registry)
    validate = original.validator.validate
    def capture(context, contract, command):
        identities.append(context)
        return validate(context, contract, command)
    original.validator.validate = capture
    original.command_provider = lambda request, context, attempt: (
        identities.append(context) or ActionCommand(action_type="act", target_seat=None, reasoning="ok")
    )
    original.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert identities[0] is identities[1]


@pytest.mark.parametrize("scope", ["window", "round", "game"])
def test_each_action_limit_gates_at_boundary_but_not_below(scope) -> None:
    c = contract("c")
    object.__setattr__(c, "per_window_limit", 2)
    object.__setattr__(c, "per_round_limit", 2)
    object.__setattr__(c, "per_game_limit", 2)
    registry = snapshot(spec("r", c)); game = state("r"); engine = scheduler(registry)
    request = engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)[0]
    key = {
        "window": f"1\0c\0{request.window_id}",
        "round": "1\0c\0" + "1",
        "game": "1\0c",
    }[scope]
    from app.core.effect_applier import _Runtime
    counts = {name: {} for name in ("window", "round", "game")}
    counts[scope][key] = 1
    game._pipeline_runtime = _Runtime(action_counts=counts)
    assert len(engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)) == 1
    game._pipeline_runtime.action_counts[scope][key] = 2
    assert engine.issue(game, SchedulePoint.NIGHT_ACTION, registry) == ()


def test_action_limits_are_isolated_by_actor() -> None:
    c = contract("c"); registry = snapshot(spec("r", c)); game = state("r", "r")
    from app.core.effect_applier import _Runtime
    requests = scheduler(registry).issue(game, SchedulePoint.NIGHT_ACTION, registry)
    first = next(item for item in requests if item.actor_seat == 1)
    game._pipeline_runtime = _Runtime(action_counts={
        "window": {f"1\0c\0{first.window_id}": 1},
        "round": {"1\0c\0" + "1": 1}, "game": {"1\0c": 1},
    })
    assert [item.actor_seat for item in scheduler(registry).issue(
        game, SchedulePoint.NIGHT_ACTION, registry
    )] == [2]


def test_response_limit_gate_skips_react_hook(monkeypatch) -> None:
    primary = contract("primary")
    response = contract("response", point=SchedulePoint.DAY_ACTION, responses=frozenset({"DONE"}))
    object.__setattr__(response, "react", react_event)
    registry = snapshot(spec("r", primary, response)); game = state("r")
    event = DomainEvent("event:" + "a" * 16, "DONE", {"target_seat": 1})
    window = ResponseWindow("response-window", event.event_id, "response", 1, 0)
    monkeypatch.setattr(ResponseQueue, "open", lambda self, state, raw, depth: (window,))
    from app.core.effect_applier import _Runtime
    game._pipeline_runtime = _Runtime(action_counts={
        "window": {"1\0response\0response-window": 1},
        "round": {"1\0response\0" + "1": 1}, "game": {"1\0response": 1},
    })
    result = scheduler(registry).run_point(game, SchedulePoint.DAY_ACTION)
    assert len(result.requests) == len(result.commits) == 1
    assert all(event["event_type"] != "REACTED" for event in result.events)



def test_model_work_rejects_invalid_and_stale_inputs_before_provider_call():
    from dataclasses import replace
    from unittest.mock import Mock

    registry = snapshot(spec("r", contract("work")))
    game = state("r")
    provider = Mock(return_value=ActionCommand(action_type="pass", target_seat=None, reasoning=""))
    runner = scheduler(registry, provider)
    request = runner.issue(game, SchedulePoint.NIGHT_ACTION, registry)[0]
    context = runner.projector.project(game, request, registry)
    for args in ((object(), request, context), (game, object(), context), (game, request, object())):
        with pytest.raises(TypeError): runner.prepare_next_work(*args)
    with pytest.raises(PipelinePaused, match="stale revision"):
        runner.prepare_next_work(game, replace(request, context_revision=request.context_revision + 1), context)
    with pytest.raises(TypeError): runner.collect_model_results(object())
    with pytest.raises(TypeError): runner.apply_collected_results(game, object())
    provider.assert_not_called()


def test_stale_execution_lock_finalizer_cannot_remove_a_replacement_lock():
    game = state("r")
    first = scheduler_module._execution_lock(game)
    reference, _ = scheduler_module._EXECUTION_LOCKS[id(game)]
    reference.__callback__(reference)
    reference.__callback__(reference)
    second = scheduler_module._execution_lock(game)
    reference.__callback__(reference)
    assert first is not second
    assert scheduler_module._execution_lock(game) is second
