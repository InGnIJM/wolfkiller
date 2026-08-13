from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.scheduler import Scheduler
from app.models.actions import NightAction
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.registry import builtin_registry
from app.roles.seer import (
    SEER_SPEC, Seer, resolve_seer_action, seer_applicable,
    validate_seer_action,
)


def context(*, target: int | None = 2, camp: str = "werewolf") -> ActionContext:
    contract = SEER_SPEC.contracts[0]
    facts: dict[str, object] = {"alive_seats": (1, 2, 3)}
    if target is not None:
        facts["selected_target"] = {"seat": target, "camp_label": camp}
    return ActionContext(
        "g", 0, facts, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=SEER_SPEC.role_id, actor_alive=True, action_key="a",
    )


def command(action: str, target: int | None = None) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="ok")


def test_spec_and_hooks_are_closed_and_role_agnostic() -> None:
    contract = SEER_SPEC.contracts[0]
    assert SEER_SPEC.role_id == "wolf-killer-seer"
    assert SEER_SPEC.initial_private_data == {"private_checks": ()}
    assert SEER_SPEC.allowed_effects == {EffectKind.RECORD_PRIVATE_FACT}
    assert contract.schedule_point is SchedulePoint.NIGHT_ACTION and contract.order == 30
    assert contract.action_types == ("check", "pass")
    assert contract.selected_target_fact_namespaces == {"camp_label"}
    assert contract.per_window_limit == contract.per_round_limit == 1
    assert seer_applicable(context()) is True
    assert seer_applicable(replace(context(), actor_alive=False)) is False


def test_validation_rejects_self_and_generic_validator_rejects_dead_target() -> None:
    assert validate_seer_action(context(target=1, camp="good"), command("check", 1))
    assert validate_seer_action(context(), command("check", 2)) == ()
    assert validate_seer_action(context(target=None), command("pass")) == ()
    dead = replace(context(), facts={"alive_seats": (1, 3), "selected_target": {"seat": 2, "camp_label": "werewolf"}})
    assert ActionValidator().validate(dead, SEER_SPEC.contracts[0], command("check", 2))


def test_resolver_records_only_selected_camp_as_actor_private_fact() -> None:
    contract = SEER_SPEC.contracts[0]
    effects = ActionResolver().resolve_effects(
        context(), SEER_SPEC, contract, command("check", 2)
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.ACCEPT_ACTION, EffectKind.RECORD_PRIVATE_FACT,
    ]
    fact = effects[1]
    assert fact.target_seat == 1
    assert fact.payload == {
        "target": 1, "namespace": "private_checks",
        "fact": {"target": 2, "camp": "werewolf"},
    }
    assert fact.visibility == ("ACTOR",)
    assert fact.source_action_key == "a" and fact.expected_revision == 0
    assert fact.sort_key == (1,)
    assert resolve_seer_action(context(target=None), command("pass")) == ()


def test_resolve_rejects_missing_mismatched_or_malformed_selected_fact() -> None:
    with pytest.raises(ValueError, match="missing or mismatched"):
        resolve_seer_action(context(target=None), command("check", 2))
    with pytest.raises(ValueError, match="missing or mismatched"):
        resolve_seer_action(context(target=3), command("check", 2))
    with pytest.raises(TypeError, match="camp label"):
        resolve_seer_action(context(camp=7), command("check", 2))  # type: ignore[arg-type]


def test_registry_preserves_pipeline_and_legacy_seer() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(SEER_SPEC.role_id) is SEER_SPEC
    assert builtin_registry.require(SEER_SPEC.role_id).role_factory is Seer


def test_scheduler_persists_check_and_projects_it_only_to_same_actor_next_round() -> None:
    registry = builtin_registry.freeze()
    game = GameState(
        "seer-game", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, SEER_SPEC.role_id, "good"),
            2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        },
    )

    def provider(request, projected, attempt):
        if request.role_id == SEER_SPEC.role_id:
            assert "selected_target" not in projected.facts
            return command("check", 2)
        return command("pass")

    engine = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert game._pipeline_runtime.private_facts[1] == [{
        "namespace": "private_checks",
        "fact": {"target": 2, "camp": "werewolf"},
    }]

    game.round_number = 2
    requests = engine.issue(game, SchedulePoint.NIGHT_ACTION, registry)
    seer_request = next(item for item in requests if item.role_id == SEER_SPEC.role_id)
    projected = ContextProjector().project(game, seer_request, registry)
    assert projected.facts["private_checks"] == ({"target": 2, "camp": "werewolf"},)
    assert "private_checks" not in ContextProjector().project(
        game, next(item for item in requests if item.role_id != SEER_SPEC.role_id), registry,
    ).facts


def test_legacy_target_validation_helpers_remain_available() -> None:
    role = object.__new__(Seer); role.seat = 1
    game = GameState("g", players={
        1: PlayerState(1, SEER_SPEC.role_id, "good"),
        2: PlayerState(2, "x", "good", is_alive=False),
        3: PlayerState(3, "x", "good"),
    })
    for target in (1, 2, 99):
        action = NightAction(1, "check", target)
        assert role._validate_check_target(game, action).target_seat is None
    valid = NightAction(1, "check", 3)
    assert role._validate_check_target(game, valid) is valid
    assert role._is_invalid_check(NightAction(1, "pass")) is True
    assert role._is_invalid_check(valid) is False


@pytest.mark.asyncio
async def test_legacy_check_retries_then_returns_valid_target() -> None:
    class Builder:
        def build_action_prompt(self, *args): return "prompt"
    class Parser:
        def __init__(self): self.actions = [NightAction(1, "check", 1), NightAction(1, "check", 2)]
        def parse_night_action(self, raw, seat): return self.actions.pop(0)
    role = object.__new__(Seer); role.seat = 1; role.role_name = SEER_SPEC.role_id
    role.prompt_builder = Builder(); role.output_parser = Parser()
    async def invoke(prompt): return "raw"
    role._invoke_llm = invoke
    game = GameState("g", players={1: PlayerState(1, role.role_name, "good"), 2: PlayerState(2, "x", "good")})
    assert role.get_skills() == ["check"]
    assert (await role.check(game, object())).target_seat == 2


@pytest.mark.asyncio
async def test_legacy_check_valid_first_attempt_and_fallback_paths() -> None:
    class Builder:
        def build_action_prompt(self, *args): return "prompt"
    class Parser:
        def __init__(self, actions): self.actions = list(actions)
        def parse_night_action(self, raw, seat): return self.actions.pop(0)
    async def invoke(prompt): return "raw"

    def role_with(actions):
        role = object.__new__(Seer); role.seat = 1; role.role_name = SEER_SPEC.role_id
        role.prompt_builder = Builder(); role.output_parser = Parser(actions)
        role._invoke_llm = invoke
        return role

    game = GameState("g", players={
        1: PlayerState(1, SEER_SPEC.role_id, "good"),
        2: PlayerState(2, "x", "good"),
    })
    first = NightAction(1, "check", 2)
    assert await role_with([first]).check(game, object()) is first
    fallback = await role_with([
        NightAction(1, "pass"), NightAction(1, "pass"),
    ]).check(game, object())
    assert fallback.action_type == "check" and fallback.target_seat == 2
    alone = GameState("alone", players={1: PlayerState(1, SEER_SPEC.role_id, "good")})
    passed = await role_with([
        NightAction(1, "pass"), NightAction(1, "pass"),
    ]).check(alone, object())
    assert passed.action_type == "pass"
    empty = NightAction(1, "check", None)
    assert role_with([])._validate_check_target(game, empty) is empty
