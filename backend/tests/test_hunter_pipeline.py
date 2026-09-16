from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, derive_effect_id, role_resource_view
from app.core.scheduler import DomainEvent, ResponseQueue, Scheduler
from app.models.actions import NightAction
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    SchedulePoint,
)
from app.roles.hunter import (
    HUNTER_SPEC, Hunter, hunter_applicable, resolve_hunter_action,
    validate_hunter_action,
)
from app.roles.registry import RoleRegistry, builtin_registry


def context(*, gun: int = 1, alive=(2, 3), reason="wolf_kill") -> ActionContext:
    contract = HUNTER_SPEC.contracts[0]
    event_id = "event:" + "1" * 16
    return ActionContext(
        "g", 0, {"alive_seats": alive, "dead_seats": (1,)},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="dawn",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=HUNTER_SPEC.role_id, actor_alive=False,
        resources={"gun": gun}, action_key="a", source_event_id=event_id,
        trigger_event={"event_id": event_id, "type": "PLAYER_DIED",
                       "target_seat": 1, "cause": reason},
        trigger_reason=reason,
    )


def command(action: str, target: int | None = None) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="ok")


def test_spec_declares_bounded_death_response_and_gun() -> None:
    contract = HUNTER_SPEC.contracts[0]
    assert HUNTER_SPEC.role_id == "wolf-killer-hunter"
    assert HUNTER_SPEC.initial_resources == {"gun": 1}
    assert HUNTER_SPEC.allowed_effects == {
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    }
    assert contract.schedule_point is SchedulePoint.DAWN_REACTION
    assert contract.order == 40
    assert contract.action_types == ("shoot", "pass")
    assert contract.actions_requiring_target == {"shoot"}
    assert contract.response_event_types == {"PLAYER_DIED"}
    assert contract.response_reasons == {"wolf_kill", "exile", "hunter_shot", "self_explode"}
    assert contract.per_window_limit == contract.per_game_limit == 1


def test_spec_instructions_state_shooting_is_optional() -> None:
    text = HUNTER_SPEC.instructions
    assert "可选" in text
    assert "pass" in text
    assert "自行判断" in text
    assert "误伤" in text


def test_applicable_requires_bound_allowed_own_death_and_gun() -> None:
    assert hunter_applicable(context()) is True
    assert hunter_applicable(context(reason="exile")) is True
    assert hunter_applicable(context(reason="hunter_shot")) is True
    assert hunter_applicable(context(reason="poison")) is False
    assert hunter_applicable(context(gun=0)) is False
    assert hunter_applicable(replace(context(), source_event_id=None, trigger_event=None)) is False
    assert hunter_applicable(replace(context(), trigger_event={
        "event_id": "event:" + "1" * 16, "type": "PLAYER_DIED",
        "target_seat": 2, "cause": "wolf_kill",
    })) is False
    assert hunter_applicable(replace(context(), trigger_event=None)) is False


def test_validate_and_resolve_shoot_or_pass() -> None:
    assert validate_hunter_action(context(), command("shoot", 2)) == ()
    assert validate_hunter_action(context(), command("pass")) == ()
    assert validate_hunter_action(context(gun=0), command("shoot", 2))
    passed = resolve_hunter_action(context(), command("pass"))
    assert [effect.kind for effect in passed] == [EffectKind.EMIT_EVENT]
    assert passed[0].sort_key == (1,)
    assert passed[0].payload == {
        "event_type": "HUNTER_REASONING",
        "payload": {
            "seat": 1, "action_type": "pass", "target_seat": None,
            "reasoning": "ok", "thought": "决定不开枪：ok",
        },
    }
    effects = resolve_hunter_action(context(), command("shoot", 2))
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {"target": 1, "resource": "gun", "amount": 1}
    assert effects[0].preconditions == {"resource_equals": {"resource": "gun", "value": 1}}
    assert effects[1].payload == {"target": 2, "amount": 1, "cause": "hunter_shot"}
    assert effects[2].payload == {"event_type": "HUNTER_SHOT", "payload": {"target_seat": 2}}
    assert effects[2].visibility == ("PUBLIC",)
    assert effects[3].payload == {
        "event_type": "HUNTER_REASONING",
        "payload": {
            "seat": 1, "action_type": "shoot", "target_seat": 2,
            "reasoning": "ok", "thought": "决定开枪带走 2 号玩家：ok",
        },
    }
    assert effects[3].visibility == ("PUBLIC",)
    assert [effect.target_seat for effect in effects] == [1, 2, None, None]
    assert [effect.sort_key for effect in effects] == [(1,), (2,), (3,), (4,)]
    assert all(effect.source_event_id == context().source_event_id for effect in effects)


def test_generic_validator_allows_dead_responder_but_rejects_dead_target() -> None:
    contract = HUNTER_SPEC.contracts[0]
    validator = ActionValidator()
    assert validator.validate(context(), contract, command("shoot", 2)) == ()
    violations = validator.validate(context(), contract, command("shoot", 1))
    assert {item.code for item in violations} == {"target_not_alive"}


def test_registry_preserves_pipeline_and_legacy_hunter() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(HUNTER_SPEC.role_id) is HUNTER_SPEC
    assert builtin_registry.require(HUNTER_SPEC.role_id).role_factory is Hunter


def _killer_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def _kill_hunter(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]:
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.MARK_DEATH,
        context.action_key, target_seat=1,
        payload={"target": 1, "cause": "wolf_kill"},
        expected_revision=context.revision, sort_key=(1,),
    ),)


KILLER_SPEC = RoleSpec(
    role_id="test-killer", display_name="Killer", camp_id="werewolf",
    contracts=(ActionContract(
        contract_id="test_kill", schedule_point=SchedulePoint.NIGHT_ACTION,
        order=1, action_types=("kill", "pass"),
        actions_requiring_target=frozenset(), fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.MARK_DEATH}),
        visibility_namespaces=frozenset({"PUBLIC"}), per_game_limit=1,
        is_applicable=_killer_applicable, resolve=_kill_hunter,
    ),), allowed_effects=frozenset({EffectKind.MARK_DEATH}),
    visibility_namespaces=frozenset({"PUBLIC"}),
)


def test_response_queue_filters_reason_before_opening_window() -> None:
    registry = RoleRegistry(); registry.register_pipeline(HUNTER_SPEC)
    snapshot = registry.freeze()
    game = GameState("g", players={1: PlayerState(1, HUNTER_SPEC.role_id, "good", False)})
    allowed = DomainEvent("event:" + "2" * 16, "PLAYER_DIED", {"target_seat": 1}, "exile")
    poison = DomainEvent("event:" + "3" * 16, "PLAYER_DIED", {"target_seat": 1}, "poison")
    unknown = DomainEvent("event:" + "4" * 16, "PLAYER_DIED", {"target_seat": 1}, "unknown")
    queue = ResponseQueue(snapshot)
    assert len(queue.open(game, allowed, depth=0)) == 1
    assert queue.open(game, poison, depth=0) == ()
    assert queue.open(game, unknown, depth=0) == ()
    assert queue.open(game, allowed, depth=0) == queue.open(game, allowed, depth=0)


def test_scheduler_dead_hunter_shoots_once_and_consumes_gun() -> None:
    registry = RoleRegistry()
    registry.register_pipeline(HUNTER_SPEC); registry.register_pipeline(KILLER_SPEC)
    snapshot = registry.freeze()
    game = GameState(
        "hunter-game", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, HUNTER_SPEC.role_id, "good"),
            2: PlayerState(2, KILLER_SPEC.role_id, "werewolf"),
        },
    )
    hunter_calls = []

    def provider(request, projected, attempt):
        if request.role_id == HUNTER_SPEC.role_id:
            hunter_calls.append(projected)
            assert projected.actor_alive is False
            return command("shoot", 2)
        return command("kill")

    engine = Scheduler(
        snapshot, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert len(hunter_calls) == 1
    assert hunter_calls[0].trigger_reason == "wolf_kill"
    assert game.players[1].is_alive is False
    assert role_resource_view(game, 1) == {"gun": 0}
    assert game._pipeline_runtime.pending_damage == ({"target": 2, "amount": 1, "cause": "hunter_shot"},)
    assert [commit.revision for commit in result.commits] == [2, 3]

    engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert len(hunter_calls) == 1


def test_no_gun_makes_response_inapplicable() -> None:
    registry = RoleRegistry(); registry.register_pipeline(HUNTER_SPEC)
    snapshot = registry.freeze()
    game = GameState("g", players={1: PlayerState(1, HUNTER_SPEC.role_id, "good", False)})
    from app.core.role_runtime import initialize_role_resources
    initialize_role_resources(game, snapshot.specs, snapshot.digest)
    game._pipeline_runtime.role_resources[1]["gun"] = 0
    event = DomainEvent("event:" + "5" * 16, "PLAYER_DIED", {"target_seat": 1}, "exile")
    window = ResponseQueue(snapshot).open(game, event, depth=0)[0]
    request_context = ContextProjector().project(
        game,
        __import__("app.models.pipeline", fromlist=["IssuedActionRequest"]).IssuedActionRequest(
            1, HUNTER_SPEC.role_id, HUNTER_SPEC.contracts[0],
            game._pipeline_runtime.revision, 0, "waiting", window.window_id, window.window_id,
        ), snapshot, source_event_id=event.event_id,
        trigger_event={"event_id": event.event_id, "type": "PLAYER_DIED", "target_seat": 1},
        trigger_reason="exile",
    )
    assert hunter_applicable(request_context) is False


@pytest.mark.asyncio
async def test_legacy_hunter_skill_and_shoot_remain_available() -> None:
    class Builder:
        def build_action_prompt(self, *args): return "prompt"
    class Parser:
        def parse_night_action(self, raw, seat): return NightAction(seat, "shoot", 2)
    role = object.__new__(Hunter); role.seat = 1; role.role_name = HUNTER_SPEC.role_id
    role.prompt_builder = Builder(); role.output_parser = Parser()
    async def invoke(prompt): return "raw"
    role._invoke_llm = invoke
    assert role.get_skills() == ["shoot"]
    assert (await role.shoot(GameState("g"), object())).target_seat == 2
