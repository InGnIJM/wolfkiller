"""Demon Hunter: the hunt's two outcomes, plus the vial immunity it declares.

The role is deliberately thin — one contract, one camp read, one delayed damage
— because everything hard about it (a death that lands a day later) already
lives in the settlement. What this file pins is which way the damage points.
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, _Runtime, derive_effect_id
from app.core.night_settlement import POISON_IMMUNE_RESOURCE, settle
from app.core.role_runtime import initialize_role_resources
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.demon_hunter import (
    DEMON_HUNT_CONTRACT, DEMON_HUNTER_SPEC, DemonHunter,
    demon_hunter_applicable, resolve_demon_hunter_action,
    validate_demon_hunter_action,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC

HUNTER = "wolf-killer-demon-hunter"
WOLF = "wolf-killer-werewolf"
VILLAGER = "wolf-killer-villager"
EVENT_ID = "event:" + "6" * 16


def demon_hunter_registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, DEMON_HUNTER_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def context(
    *, target: int | None = 2, camp: str | None = "werewolf", round_number: int = 2,
    alive: bool = True, with_fact: bool = True, actor: int = 1,
    identity: str | None = None,
) -> ActionContext:
    contract = DEMON_HUNT_CONTRACT
    facts: dict[str, object] = {"alive_seats": (1, 2, 3)}
    if with_fact and target is not None:
        selected: dict[str, object] = {"seat": target}
        if camp is not None:
            selected["camp_label"] = camp
        if identity is not None:
            selected["role_label"] = identity
        facts["selected_target"] = selected
    return ActionContext(
        "g", 7, facts, config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=HUNTER, actor_alive=alive,
        resources={POISON_IMMUNE_RESOURCE: 1}, action_key="hunt:1",
        source_event_id=EVENT_ID,
    )


def command(action_type: str = "hunt", target: int | None = 2) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="ok")


def kinds(effects) -> list[str]:
    return [effect.kind.value for effect in effects]


def test_spec_declares_the_hunt_and_the_vial_immunity() -> None:
    assert DEMON_HUNTER_SPEC.camp_id == "good"
    assert DEMON_HUNTER_SPEC.tags == frozenset({"god"})
    assert DEMON_HUNTER_SPEC.max_count == 1
    assert DEMON_HUNTER_SPEC.contracts == (DEMON_HUNT_CONTRACT,)
    assert DEMON_HUNTER_SPEC.initial_resources == {POISON_IMMUNE_RESOURCE: 1}
    assert DEMON_HUNT_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert DEMON_HUNT_CONTRACT.order == 40
    assert DEMON_HUNT_CONTRACT.selected_target_fact_namespaces == frozenset({"camp_label"})


def test_the_hunt_starts_on_the_second_night() -> None:
    assert demon_hunter_applicable(context(round_number=1)) is False
    assert demon_hunter_applicable(context(round_number=2)) is True
    assert demon_hunter_applicable(context(round_number=3)) is True
    assert demon_hunter_applicable(context(alive=False)) is False


def test_validate_only_forbids_hunting_itself() -> None:
    assert validate_demon_hunter_action(context(), command()) == ()
    violations = validate_demon_hunter_action(context(target=1), command(target=1))
    assert [item.code for item in violations] == ["self_hunt_forbidden"]
    assert validate_demon_hunter_action(context(), command("pass", None)) == ()


def test_a_wolf_target_is_the_one_who_dies_next_day() -> None:
    effects = resolve_demon_hunter_action(context(camp="werewolf"), command())
    assert kinds(effects) == ["submit_damage", "emit_event"]
    assert effects[0].payload == {"target": 2, "amount": 1, "cause": "hunt"}
    assert effects[0].target_seat == 2
    assert "次日出局" in effects[1].payload["payload"]["thought"]


def test_a_good_target_makes_the_hunter_die_next_day() -> None:
    effects = resolve_demon_hunter_action(context(camp="good"), command())
    assert effects[0].payload == {"target": 1, "amount": 1, "cause": "hunt"}
    assert effects[0].target_seat == 1
    assert "以死谢罪" in effects[1].payload["payload"]["thought"]


def test_passing_only_records_a_reason() -> None:
    effects = resolve_demon_hunter_action(context(target=None), command("pass", None))
    assert kinds(effects) == ["emit_event"]
    assert effects[0].payload["payload"]["thought"].startswith("决定今晚不狩猎")


def test_missing_facts_are_programming_errors() -> None:
    with pytest.raises(ValueError):
        resolve_demon_hunter_action(context(with_fact=False), command())
    with pytest.raises(TypeError):
        resolve_demon_hunter_action(context(camp=None), command())


def test_the_camp_answer_never_reaches_the_model_prompt() -> None:
    registry = demon_hunter_registry()
    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=2,
        players={1: PlayerState(1, HUNTER, "good"), 2: PlayerState(2, WOLF, "werewolf")},
    )
    from app.models.pipeline import IssuedActionRequest

    request = IssuedActionRequest(1, HUNTER, DEMON_HUNT_CONTRACT, 0, 2, "night", "w", "k")
    base = ContextProjector().project(state, request, registry)
    assert "selected_target" not in base.facts
    projected = ContextProjector().project_selected_target(
        state, request, base, command(), registry,
    )
    assert projected.facts["selected_target"] == {"seat": 2, "camp_label": "werewolf"}


def test_the_vial_never_kills_a_poison_immune_seat() -> None:
    from app.core.night_settlement import DELAYABLE_RESOURCE

    damage = ({"target": 2, "amount": 1, "cause": "poison"},)
    deaths, alive, delayed = settle(
        damage, (), {1, 2}, {1: True, 2: True}, round_number=2,
        role_resources={2: {POISON_IMMUNE_RESOURCE: 1}},
    )
    assert deaths == () and delayed == {} and alive[2] is True
    # The knife is not filtered by the mark.
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},), (), {1, 2},
        {1: True, 2: True}, round_number=2,
        role_resources={2: {POISON_IMMUNE_RESOURCE: 1}},
    )
    assert [(item["seat"], item["cause"]) for item in deaths] == [(2, "wolf_kill")]
    # Immunity is judged before the delay mark: a seat holding both takes
    # nothing at all, so nothing is owed to the next day either.
    deaths, alive, delayed = settle(
        damage, (), {1, 2}, {1: True, 2: True}, round_number=2,
        role_resources={2: {POISON_IMMUNE_RESOURCE: 1, DELAYABLE_RESOURCE: 1}},
    )
    assert deaths == () and delayed == {} and alive[2] is True


def test_resolver_adds_the_accept_effect_and_keeps_the_batch() -> None:
    registry = demon_hunter_registry()
    resolved = ActionResolver().resolve_effects(
        context(), registry.require(HUNTER), DEMON_HUNT_CONTRACT, command(),
    )
    assert resolved[0].kind is EffectKind.ACCEPT_ACTION
    assert resolved[1:] == resolve_demon_hunter_action(context(), command())
    assert derive_effect_id("hunt:1", 1) == resolved[1].effect_id


def test_validator_accepts_the_declared_batch() -> None:
    registry = demon_hunter_registry()
    assert ActionValidator().validate(context(), DEMON_HUNT_CONTRACT, command()) == ()
    assert ActionValidator().validate(
        context(target=None), DEMON_HUNT_CONTRACT, command("pass", None),
    ) == ()


def test_registered_on_both_sides() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(HUNTER) is DEMON_HUNTER_SPEC
    assert builtin_registry.require(HUNTER).role_factory is DemonHunter
    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=2,
        players={
            1: PlayerState(1, HUNTER, "good"),
            2: PlayerState(2, VILLAGER, "good"),
            3: PlayerState(3, WOLF, "werewolf"),
        },
    )
    state._pipeline_runtime = _Runtime()
    initialize_role_resources(state, snapshot.specs, "a" * 64)
    assert state._pipeline_runtime.role_resources[1][POISON_IMMUNE_RESOURCE] == 1
    assert EffectApplier() is not None


def test_the_hunt_damage_settles_on_the_next_day(tmp_path) -> None:
    """端到端：第 2 夜命中狼人 → 当夜不死、标记 hunted，次日发言结束后出局。"""
    registry = demon_hunter_registry()
    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=2,
        players={
            1: PlayerState(1, HUNTER, "good"),
            2: PlayerState(2, WOLF, "werewolf"),
        },
    )

    def provider(request, ctx, attempt):
        return ActionCommand(action_type="hunt", target_seat=2, reasoning="砍")

    from app.core.scheduler import Scheduler

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)
    applier = EffectApplier()
    settlement = applier.settle_pending(state, round_number=2)
    assert settlement is not None
    assert state.players[2].is_alive is True
    assert state.death_history == []
    assert state._pipeline_runtime.statuses[2] == {"hunted", "delayed_death"}
    status_events = [
        event for event in settlement.events if event["event_type"] == "STATUS_ADDED"
    ]
    assert len(status_events) == 2

    from app.core.night_settlement import resolve_delayed_deaths

    deaths, cleared = resolve_delayed_deaths(state._pipeline_runtime.statuses, 3)
    assert deaths == ({"seat": 2, "cause": "hunt", "round_number": 3},)
    assert cleared[2] == ("delayed_death", "hunted")
