from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, role_resource_view
from app.core.scheduler import Scheduler
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.registry import builtin_registry
from app.roles.witch import WITCH_SPEC, Witch, resolve_witch_action, validate_witch_action, witch_applicable
from app.models.actions import NightAction
from app.models.game import GameState, PlayerState


def context(resources=None, target=2, counters=None) -> ActionContext:
    contract = WITCH_SPEC.contracts[0]
    return ActionContext(
        "g", 0, {"alive_seats": (1, 2, 3), "wolf_kill_target": target},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=1, phase="night", window_id="w", schedule_point=contract.schedule_point,
        actor_seat=1, actor_role_id=WITCH_SPEC.role_id, actor_alive=True,
        resources=resources or {"antidote": 1, "poison": 1}, counters=counters or {}, action_key="a",
    )


def command(action, target=None):
    return ActionCommand(action_type=action, target_seat=target, reasoning="ok")


def test_applicability_and_semantic_validation() -> None:
    assert witch_applicable(context()) is True
    assert witch_applicable(context({"antidote": 0, "poison": 0})) is False
    assert validate_witch_action(context(), command("save", 2)) == ()
    assert validate_witch_action(context(), command("poison", 3)) == ()
    assert validate_witch_action(context(), command("pass")) == ()
    assert validate_witch_action(context(), command("save", 3))
    assert validate_witch_action(context({"antidote": 0, "poison": 1}), command("save", 2))
    assert validate_witch_action(context({"antidote": 1, "poison": 0}), command("poison", 3))


def test_resolve_save_poison_and_pass_effects_are_canonical() -> None:
    saved = resolve_witch_action(context(), command("save", 2))
    poisoned = resolve_witch_action(context(), command("poison", 3))
    assert [item.kind for item in saved] == [EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_PROTECTION]
    assert [item.kind for item in poisoned] == [EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE]
    assert [item.sort_key for item in saved] == [(1,), (2,)]
    assert saved[0].target_seat == 1 and saved[1].target_seat == 2
    assert saved[1].payload == {"target": 2, "amount": 1}
    assert poisoned[1].payload == {"target": 3, "amount": 1, "cause": "poison"}
    assert saved[0].preconditions["resource_equals"] == {"resource": "antidote", "value": 1}
    assert resolve_witch_action(context(), command("pass")) == ()


def test_validator_enforces_alive_target_and_once_per_round() -> None:
    contract = WITCH_SPEC.contracts[0]; validator = ActionValidator()
    assert validator.validate(replace(context(), facts={"alive_seats": (1, 2), "wolf_kill_target": 2}), contract, command("poison", 3))
    assert validator.validate(context(counters={"round": 1}), contract, command("pass"))
    assert contract.per_window_limit == contract.per_round_limit == 1


def test_resolver_adds_accept_and_registry_preserves_legacy() -> None:
    snapshot = builtin_registry.freeze(); spec = snapshot.require("wolf-killer-witch")
    effects = ActionResolver().resolve_effects(context(), spec, spec.contracts[0], command("save", 2))
    assert [item.kind for item in effects] == [EffectKind.ACCEPT_ACTION, EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_PROTECTION]
    assert spec is WITCH_SPEC and spec.initial_resources == {"antidote": 1, "poison": 1}
    assert builtin_registry.require("wolf-killer-witch").role_factory is not None


def test_spec_declares_dynamic_wolf_kill_target() -> None:
    assert WITCH_SPEC.initial_private_data == {"wolf_kill_target": None}


def test_scheduler_save_projects_target_and_applies_resources_atomically() -> None:
    registry = builtin_registry.freeze()
    game = GameState(
        "save-game", phase="night", round_number=1,
        players={
            1: PlayerState(1, WITCH_SPEC.role_id, "good"),
            2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        },
        last_wolf_kill_target=2,
    )
    seen = []

    def provider(request, projected, attempt):
        if request.role_id == WITCH_SPEC.role_id:
            seen.append(projected)
            return command("save", 2)
        return command("pass")

    engine = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(),
        provider,
    )
    issued = engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)
    witch_request = next(item for item in issued if item.role_id == WITCH_SPEC.role_id)
    assert witch_request.context_revision == 1
    projected = ContextProjector().project(game, witch_request, registry)
    assert projected.resources == {"antidote": 1, "poison": 1}
    assert projected.facts["wolf_kill_target"] == 2

    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert seen[0].facts["wolf_kill_target"] == 2
    assert role_resource_view(game, 1) == {"antidote": 0, "poison": 1}
    assert game._pipeline_runtime.pending_protection == ({"target": 2, "amount": 1},)
    assert result.commits[-1].revision == game._pipeline_runtime.revision == 3

    engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert role_resource_view(game, 1) == {"antidote": 0, "poison": 1}
    assert len(seen) == 1
    assert all(
        request.role_id != WITCH_SPEC.role_id
        for request in engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)
    )


def test_scheduler_poison_applies_damage_and_consumes_only_poison() -> None:
    registry = builtin_registry.freeze()
    game = GameState(
        "poison-game", phase="night", round_number=1,
        players={
            1: PlayerState(1, WITCH_SPEC.role_id, "good"),
            2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        },
    )

    def provider(request, projected, attempt):
        return command("poison", 2) if request.role_id == WITCH_SPEC.role_id else command("pass")

    engine = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(),
        provider,
    )
    result = engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert result.commits[-1].revision == 3
    assert role_resource_view(game, 1) == {"antidote": 1, "poison": 0}
    assert game._pipeline_runtime.pending_damage == ({"target": 2, "amount": 1, "cause": "poison"},)


def test_legacy_target_validation_branches_remain_available() -> None:
    role = object.__new__(Witch); role.seat = 1
    state = GameState("g", players={1: PlayerState(1, "wolf-killer-witch", "good"), 2: PlayerState(2, "x", "good", is_alive=False), 3: PlayerState(3, "x", "good")})
    passed = NightAction(1, "pass"); none = NightAction(1, "poison", None)
    assert role.get_skills() == ["save", "poison"]
    assert role._validate_poison_target(state, passed) is passed
    assert role._validate_poison_target(state, none) is none
    assert role._validate_poison_target(state, NightAction(1, "poison", 99)).action_type == "pass"
    assert role._validate_poison_target(state, NightAction(1, "poison", 2)).action_type == "pass"
    alive = NightAction(1, "poison", 3)
    assert role._validate_poison_target(state, alive) is alive
    assert role._was_rejected(NightAction(1, "pass", reasoning="ok")) is False


@pytest.mark.asyncio
async def test_legacy_save_and_poison_retry_remain_available() -> None:
    class Builder:
        def build_action_prompt(self, *args, **kwargs): return "prompt"
    class Parser:
        def __init__(self): self.actions = [NightAction(1, "poison", 2), NightAction(1, "poison", 3)]
        def parse_night_action(self, raw, seat): return self.actions.pop(0)
    role = object.__new__(Witch); role.seat = 1; role.role_name = "wolf-killer-witch"
    role.prompt_builder = Builder(); role.output_parser = Parser()
    async def invoke(prompt): return "raw"
    role._invoke_llm = invoke
    state = GameState("g", players={1: PlayerState(1, role.role_name, "good"), 2: PlayerState(2, "x", "good", is_alive=False), 3: PlayerState(3, "x", "good")})
    role.output_parser.actions = [NightAction(1, "save", 2)]
    assert await role.save(state, object(), 2) is True
    role.output_parser.actions = [NightAction(1, "poison", 2), NightAction(1, "poison", 3)]
    assert (await role.poison(state, object(), 2)).target_seat == 3
    role.output_parser.actions = [NightAction(1, "pass")]
    assert (await role.poison(state, object(), 2)).action_type == "pass"
