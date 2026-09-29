"""Resilience: a model-driven contract degrades to its fallback on a state conflict.

A model may legitimately pick something the state no longer allows. That is a
state conflict, not a bug: the game must keep going on the contract's fallback
action instead of dying. Structural violations stay fatal — an id or permission
mismatch is a programming error and must not be papered over.

The settlement is not a model-driven contract: it has no fallback to degrade to,
so its rejection stays fatal.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import (
    EffectApplier, EffectConflict, EffectPermission, EffectRejected, _runtime,
    derive_effect_id,
)
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContract, ActionContext, EffectKind, GameEffect,
    IssuedActionRequest, RoleSpec, SchedulePoint,
)
from app.roles.registry import builtin_registry

BEAUTY, WOLF, VILLAGER, DRUNKARD = (
    "wolf-killer-wolf-beauty", "wolf-killer-werewolf",
    "wolf-killer-villager", "wolf-killer-old-drunkard",
)

_captured: list[dict[str, object]] = []


def _test_validate(context, command):  # pragma: no cover - never rejects
    return ()


def _test_aggregate(context, commands):
    return (
        GameEffect(
            derive_effect_id(context.action_key, 0), EffectKind.ACCEPT_ACTION,
            context.action_key, expected_revision=context.revision, sort_key=(0,),
        ),
    )


def _boom_aggregate(context, ordered):  # pragma: no cover - never returns
    from app.core.action_resolver import RuleExecutionError
    raise RuleExecutionError("aggregate hook failed")


def _test_resolve(context, command):
    if command.action_type != "mark":
        return ()
    return (
        GameEffect(
            derive_effect_id(context.action_key, 0), EffectKind.ADD_STATUS,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "status": "already"},
            expected_revision=context.revision, sort_key=(0,),
        ),
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.ACCEPT_ACTION,
            context.action_key, expected_revision=context.revision, sort_key=(1,),
        ),
    )


def _conflicting_contract() -> RoleSpec:
    """A contract whose write conflicts with the runtime on purpose.

    It marks a status the runtime already holds, so the applier rejects the
    batch with a state conflict. Its fallback action is a pass.
    """
    contract = ActionContract(
        contract_id="test_conflict",
        schedule_point=SchedulePoint.NIGHT_WITCH_ACTION,
        order=99,
        action_types=("mark", "pass"),
        actions_requiring_target=frozenset(),
        fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.ADD_STATUS, EffectKind.ACCEPT_ACTION}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        per_window_limit=1,
        validate=_test_validate,
        resolve=_test_resolve,
        aggregate=_test_aggregate,
    )
    return RoleSpec(
        role_id="wolf-killer-test-conflict", display_name="Test", camp_id="good",
        contracts=(contract,), tags=frozenset({"villager"}),
        allowed_effects=frozenset({EffectKind.ADD_STATUS, EffectKind.ACCEPT_ACTION}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    )


def _game() -> GameState:
    game = GameState("resilience", phase=GamePhase.NIGHT, round_number=1, players={
        1: PlayerState(1, BEAUTY, "werewolf"), 2: PlayerState(2, WOLF, "werewolf"),
        3: PlayerState(3, VILLAGER, "good"), 4: PlayerState(4, DRUNKARD, "good"),
    })
    game._pipeline_runtime = _runtime(game)
    return game


def _context(game: GameState, seat: int) -> ActionContext:
    return ActionContext(
        game.game_id, game._pipeline_runtime.revision,
        {"alive_seats": tuple(game.players), "dead_seats": ()},
        config_version="a" * 64, contract_id="test_conflict", contract_version=1,
        contract_digest="b" * 64, actor_seat=seat,
    )


def _request(game: GameState, seat: int, contract: ActionContract,
             role_id: str = "wolf-killer-test-conflict") -> IssuedActionRequest:
    return IssuedActionRequest(
        seat, role_id, contract,
        game._pipeline_runtime.revision, 1, "night", "win", "win",
    )


def _conflicting_effects(game: GameState, seat: int) -> tuple[GameEffect, ...]:
    return (
        GameEffect(
            derive_effect_id("act", 0), EffectKind.ADD_STATUS, "act",
            target_seat=seat, payload={"target": seat, "status": "already"},
            expected_revision=game._pipeline_runtime.revision, sort_key=(0,),
        ),
        GameEffect(
            derive_effect_id("act", 1), EffectKind.ACCEPT_ACTION, "act",
            expected_revision=game._pipeline_runtime.revision, sort_key=(1,),
        ),
    )


def _pass_effects(game: GameState) -> tuple[GameEffect, ...]:
    return (
        GameEffect(
            derive_effect_id("act", 0), EffectKind.ACCEPT_ACTION, "act",
            expected_revision=game._pipeline_runtime.revision, sort_key=(0,),
        ),
    )


def _runner(capture: bool = True) -> Scheduler:
    _captured.clear()
    runner = Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), lambda *a, **k: None,
    )
    if not capture:
        return runner
    original = runner._record_effect_conflict

    def wrapped(state, contract, request, error):
        _captured.append({
            "code": "effect_rejected",
            "contract_id": contract.contract_id,
            "actor_seat": request.actor_seat,
            "error": str(error),
        })
        original(state, contract, request, error)

    runner._record_effect_conflict = wrapped
    return runner


def test_a_state_conflict_degrades_to_the_fallback() -> None:
    """A model-driven contract's state conflict degrades, not dies.

    This is the shape of the benchmark crash: the model's choice was legal to
    validate but the state no longer allowed the write. The game must keep going
    on the fallback instead of dying with an ``EffectRejected``.
    """
    game = _game()
    game._pipeline_runtime.statuses[1] = {"already"}
    spec = _conflicting_contract()
    runner = _runner()
    commits: list = []
    events: list = []

    def fallback():
        return _pass_effects(game), _context(game, 1)

    result = runner._apply_or_degrade(
        game, _context(game, 1), spec, spec.contracts[0],
        _conflicting_effects(game, 1),
        commits, events, _request(game, 1, spec.contracts[0]),
        retry=fallback,
    )

    assert result is not None
    # The fallback (pass) landed: the status was not re-added.
    assert game._pipeline_runtime.statuses[1] == {"already"}
    # A fault was recorded so the degradation is never silent.
    assert any(
        row.get("code") == "effect_rejected" and row.get("contract_id") == "test_conflict"
        for row in _captured
    )


def test_a_structural_violation_stays_fatal() -> None:
    """An id mismatch is a bug: it must not degrade to the fallback."""
    game = _game()
    effects = (
        GameEffect(
            derive_effect_id("bad", 0), EffectKind.ACCEPT_ACTION, "bad",
            expected_revision=0, sort_key=(0,),
        ),
        GameEffect(
            derive_effect_id("bad", 2), EffectKind.EMIT_EVENT, "bad",
            payload={"event_type": "X", "payload": {}},
            expected_revision=0, sort_key=(1,),
        ),
    )
    permission = EffectPermission(
        actor_seat=1,
        role_effects=frozenset({EffectKind.ACCEPT_ACTION, EffectKind.EMIT_EVENT}),
        contract_effects=frozenset({EffectKind.ACCEPT_ACTION, EffectKind.EMIT_EVENT}),
        allowed_targets=frozenset({1, 2, 3, 4}),
        allowed_visibility=frozenset({"PUBLIC"}),
    )

    with pytest.raises(EffectRejected) as err:
        EffectApplier().apply(game, effects, permission)
    assert not isinstance(err.value, EffectConflict)


def test_the_settlement_rejection_stays_fatal() -> None:
    """The settlement has no fallback: its rejection must still kill the run."""
    game = _game()
    runtime = game._pipeline_runtime
    runtime.role_resources[4] = {"charm_immune": 1, "delayable": 1}
    runtime.statuses[4] = {"poisoned"}
    runtime.pending_damage = ({"target": 4, "amount": 1, "cause": "poison"},)

    with pytest.raises(EffectRejected):
        EffectApplier().settle_pending(game, round_number=1)


def test_a_fallback_that_is_also_rejected_stays_fatal() -> None:
    """When the fallback itself is rejected the run must still stop."""
    game = _game()
    game._pipeline_runtime.statuses[1] = {"already"}
    spec = _conflicting_contract()
    runner = _runner(capture=False)
    commits: list = []
    events: list = []

    def bad_fallback():
        return _conflicting_effects(game, 1), _context(game, 1)

    with pytest.raises(EffectRejected):
        runner._apply_or_degrade(
            game, _context(game, 1), spec, spec.contracts[0],
            _conflicting_effects(game, 1),
            commits, events, _request(game, 1, spec.contracts[0]),
            retry=bad_fallback,
        )


def test_no_retry_stays_fatal() -> None:
    """A conflict with no fallback to try must still stop the run."""
    game = _game()
    game._pipeline_runtime.statuses[1] = {"already"}
    spec = _conflicting_contract()
    runner = _runner(capture=False)
    commits: list = []
    events: list = []

    with pytest.raises(EffectRejected):
        runner._apply_or_degrade(
            game, _context(game, 1), spec, spec.contracts[0],
            _conflicting_effects(game, 1),
            commits, events, _request(game, 1, spec.contracts[0]),
            retry=None,
        )


def test_the_fallback_effects_resolver_runs_the_fallback_command() -> None:
    """The fallback resolver produces the contract's fallback action's effects."""
    game = _game()
    game.players[1] = PlayerState(1, BEAUTY, "werewolf")
    spec = builtin_registry.freeze().require(BEAUTY)
    contract = next(
        c for c in spec.contracts if c.contract_id == "wolf_beauty_charm"
    )
    runner = _runner(capture=False)
    request = _request(game, 1, contract, BEAUTY)
    retry = runner._fallback_effects(game, request, spec, contract)

    effects, context = retry()

    # The fallback action is "pass": it resolves to its reasoning event and the
    # batch's accept action, with no state write.
    assert effects
    assert context.actor_seat == 1


def test_the_aggregate_fallback_resolver_re_aggregates() -> None:
    """The aggregate fallback re-aggregates the members' fallback commands."""
    game = _game()
    game.players[1] = PlayerState(1, WOLF, "werewolf")
    game.players[2] = PlayerState(2, WOLF, "werewolf")
    spec = builtin_registry.freeze().require(WOLF)
    contract = next(
        c for c in spec.contracts if c.contract_id == "werewolf_kill"
    )
    runner = _runner(capture=False)
    bound = (
        _request(game, 1, contract, WOLF),
        _request(game, 2, contract, WOLF),
    )
    projector = ContextProjector()
    registry = builtin_registry.freeze()
    contexts = tuple(projector.project(game, request, registry) for request in bound)
    retry = runner._fallback_aggregate_effects(game, bound, contexts, spec, contract)

    effects, context = retry()

    assert effects is not None
    assert context.actor_seat == 1


def test_an_aggregate_hook_that_fails_during_the_fallback_is_fatal() -> None:
    """A fallback aggregate that itself fails must stop the run."""
    game = _game()
    game.players[1] = PlayerState(1, WOLF, "werewolf")
    game.players[2] = PlayerState(2, WOLF, "werewolf")
    spec = builtin_registry.freeze().require(WOLF)
    contract = next(
        c for c in spec.contracts if c.contract_id == "werewolf_kill"
    )
    runner = _runner(capture=False)
    bound = (
        _request(game, 1, contract, WOLF),
        _request(game, 2, contract, WOLF),
    )
    projector = ContextProjector()
    registry = builtin_registry.freeze()
    contexts = tuple(projector.project(game, request, registry) for request in bound)

    # Break the aggregate hook so the fallback's re-aggregation fails.
    broken = replace(contract, aggregate=_boom_aggregate)
    retry = runner._fallback_aggregate_effects(game, bound, contexts, spec, broken)

    from app.core.scheduler import PipelinePaused

    with pytest.raises(PipelinePaused):
        retry()


def test_the_fault_recorder_survives_without_a_point_context() -> None:
    """The fault recorder must not require a running point to record."""
    game = _game()
    game._pipeline_runtime.statuses[1] = {"already"}
    spec = _conflicting_contract()
    runner = _runner(capture=False)
    commits: list = []
    events: list = []

    def fallback():
        return _pass_effects(game), _context(game, 1)

    # No point is running, so the ContextVar is unset: recording must not crash.
    result = runner._apply_or_degrade(
        game, _context(game, 1), spec, spec.contracts[0],
        _conflicting_effects(game, 1),
        commits, events, _request(game, 1, spec.contracts[0]),
        retry=fallback,
    )
    assert result is not None


def test_the_fault_recorder_appends_to_the_point_faults() -> None:
    """Inside a running point the fault lands on the point's fault list."""
    game = _game()
    game._pipeline_runtime.statuses[1] = {"already"}
    spec = _conflicting_contract()
    runner = _runner(capture=False)
    commits: list = []
    events: list = []
    faults: list[dict[str, object]] = []
    token = runner._faults.set(faults)

    def fallback():
        return _pass_effects(game), _context(game, 1)

    try:
        runner._apply_or_degrade(
            game, _context(game, 1), spec, spec.contracts[0],
            _conflicting_effects(game, 1),
            commits, events, _request(game, 1, spec.contracts[0]),
            retry=fallback,
        )
    finally:
        runner._faults.reset(token)

    assert faults and faults[0]["code"] == "effect_rejected"
