from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, derive_effect_id
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
        context.action_key, payload={"event_type": "DONE", "payload": {"seat": context.actor_seat}},
        expected_revision=context.revision, sort_key=(1,),
    ),)


def aggregate_event(context: ActionContext, commands: tuple[ActionCommand, ...]) -> tuple[GameEffect, ...]:
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.EMIT_EVENT,
        context.action_key, payload={"event_type": "GROUP", "payload": {"count": len(commands)}},
        expected_revision=context.revision, sort_key=(1,),
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
