"""Wolf King extension sample.

The Wolf King is the second death-response role (the hunter was the first), so
this file pins the parts that differ from the hunter: a werewolf camp, a shared
``werewolf_kill`` contract that must stay byte-identical to the rest of the pack,
and a reason set that keeps self-destruct, poison, charm and the knight's duel
from firing the claw.
"""

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
from app.roles.registry import RoleRegistry, builtin_registry
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, WEREWOLF_SPEC
from app.roles.wolf_king import (
    WOLF_KING_SHOT_CONTRACT, WOLF_KING_SPEC, WolfKing, resolve_wolf_king_action,
    validate_wolf_king_action, wolf_king_applicable,
)


def _claw_contract() -> ActionContract:
    return next(
        contract for contract in WOLF_KING_SPEC.contracts
        if contract.contract_id == WOLF_KING_SHOT_CONTRACT.contract_id
    )


def context(*, claw: int = 1, alive=(2, 3), reason="wolf_kill") -> ActionContext:
    contract = _claw_contract()
    event_id = "event:" + "1" * 16
    return ActionContext(
        "g", 0, {"alive_seats": alive, "dead_seats": (1,)},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="dawn",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=WOLF_KING_SPEC.role_id, actor_alive=False,
        resources={"wolf_claw": claw}, action_key="a", source_event_id=event_id,
        trigger_event={"event_id": event_id, "type": "PLAYER_DIED",
                       "target_seat": 1, "cause": reason},
        trigger_reason=reason,
    )


def command(action: str, target: int | None = None) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="ok")


def test_spec_reuses_the_shared_kill_contract_verbatim() -> None:
    assert WOLF_KING_SPEC.role_id == "wolf-killer-wolf-king"
    assert WOLF_KING_SPEC.camp_id == "werewolf"
    assert WOLF_KING_SPEC.tags == {"wolf"}
    assert WOLF_KING_SPEC.max_count == 1
    assert WOLF_KING_SPEC.initial_resources == {"wolf_claw": 1}
    shared = [
        contract for contract in WOLF_KING_SPEC.contracts
        if contract.contract_id == "werewolf_kill"
    ]
    # The registry only merges contracts whose declarations are identical, and
    # the scheduler aggregates every holder into one nightly vote.
    assert shared == [WEREWOLF_KILL_CONTRACT]
    assert shared[0].stable_digest() == WEREWOLF_SPEC.contracts[0].stable_digest()


def test_claw_contract_is_a_bounded_death_response() -> None:
    contract = _claw_contract()
    assert contract.schedule_point is SchedulePoint.DAWN_REACTION
    assert contract.order == 40
    assert contract.action_types == ("shoot", "pass")
    assert contract.actions_requiring_target == {"shoot"}
    assert contract.fallback_action_type == "pass"
    assert contract.response_event_types == {"PLAYER_DIED"}
    assert contract.per_window_limit == contract.per_game_limit == 1


def test_claw_never_fires_on_the_forbidden_ways_out() -> None:
    contract = _claw_contract()
    assert contract.response_reasons == {"wolf_kill", "exile", "hunter_shot"}
    # Self-destruct never takes anyone along, a poisoned Wolf King cannot use the
    # claw, a charmed one may not either, and the duel keeps it silent as well.
    for forbidden in ("self_explode", "poison", "charm", "knight_duel"):
        assert forbidden not in contract.response_reasons


def test_instructions_name_every_forbidden_way_out() -> None:
    text = WOLF_KING_SPEC.instructions
    for forbidden in ("自爆", "毒杀", "殉情", "决斗"):
        assert forbidden in text
    assert "pass" in text


def test_applicable_requires_bound_allowed_own_death_and_claw() -> None:
    assert wolf_king_applicable(context()) is True
    assert wolf_king_applicable(context(reason="exile")) is True
    assert wolf_king_applicable(context(reason="hunter_shot")) is True
    for forbidden in ("self_explode", "poison", "charm", "knight_duel"):
        assert wolf_king_applicable(context(reason=forbidden)) is False, forbidden
    assert wolf_king_applicable(context(claw=0)) is False
    assert wolf_king_applicable(
        replace(context(), source_event_id=None, trigger_event=None)
    ) is False
    assert wolf_king_applicable(replace(context(), trigger_event=None)) is False
    assert wolf_king_applicable(replace(context(), trigger_event={
        "event_id": "event:" + "1" * 16, "type": "PLAYER_DIED",
        "target_seat": 2, "cause": "wolf_kill",
    })) is False


def test_validate_and_resolve_shoot_or_pass() -> None:
    assert validate_wolf_king_action(context(), command("shoot", 2)) == ()
    assert validate_wolf_king_action(context(), command("pass")) == ()
    assert [violation.code for violation in
            validate_wolf_king_action(context(claw=0), command("shoot", 2))] == [
        "wolf_claw_unavailable"]

    passed = resolve_wolf_king_action(context(), command("pass"))
    assert [effect.kind for effect in passed] == [EffectKind.EMIT_EVENT]
    assert passed[0].payload == {
        "event_type": "WOLF_KING_REASONING",
        "payload": {
            "seat": 1, "action_type": "pass", "target_seat": None,
            "reasoning": "ok", "degraded": False, "thought": "决定不发动狼王爪：ok",
        },
    }

    effects = resolve_wolf_king_action(context(), command("shoot", 2))
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {"target": 1, "resource": "wolf_claw", "amount": 1}
    assert effects[0].preconditions == {
        "resource_equals": {"resource": "wolf_claw", "value": 1}}
    assert effects[1].payload == {"target": 2, "amount": 1, "cause": "wolf_king_shot"}
    assert effects[2].payload == {
        "event_type": "WOLF_KING_SHOT",
        "payload": {"target_seat": 2, "seat": 1}}
    assert effects[2].visibility == ("PUBLIC",)
    assert effects[3].payload == {
        "event_type": "WOLF_KING_REASONING",
        "payload": {
            "seat": 1, "action_type": "shoot", "target_seat": 2,
            "reasoning": "ok", "degraded": False, "thought": "决定发动狼王爪带走 2 号玩家：ok",
        },
    }
    assert [effect.target_seat for effect in effects] == [1, 2, None, None]
    assert [effect.sort_key for effect in effects] == [(1,), (2,), (3,), (4,)]
    assert all(
        effect.source_event_id == context().source_event_id for effect in effects
    )


def test_generic_validator_allows_dead_responder_but_rejects_dead_target() -> None:
    contract = _claw_contract()
    validator = ActionValidator()
    assert validator.validate(context(), contract, command("shoot", 2)) == ()
    violations = validator.validate(context(), contract, command("shoot", 1))
    assert {item.code for item in violations} == {"target_not_alive"}


def test_registry_registers_both_the_pipeline_and_the_legacy_role() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(WOLF_KING_SPEC.role_id) is WOLF_KING_SPEC
    assert builtin_registry.require(WOLF_KING_SPEC.role_id).role_factory is WolfKing
    # The plain Wolf King and the White Wolf King must stay separate entries.
    assert snapshot.require("wolf-killer-werewolf-king") is not WOLF_KING_SPEC


def _killer_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def _kill_wolf_king(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
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
        # No `per_game_limit` here on purpose: this helper exists to kill a role
        # once in a test, not to model a one-shot card. Declaring a game limit
        # without a resource is what the registry now rejects.
        visibility_namespaces=frozenset({"PUBLIC"}),
        is_applicable=_killer_applicable, resolve=_kill_wolf_king,
    ),), allowed_effects=frozenset({EffectKind.MARK_DEATH}),
    visibility_namespaces=frozenset({"PUBLIC"}),
)


def test_response_queue_filters_the_forbidden_reasons_before_opening() -> None:
    registry = RoleRegistry()
    registry.register_pipeline(WOLF_KING_SPEC)
    snapshot = registry.freeze()
    game = GameState(
        "g", players={1: PlayerState(1, WOLF_KING_SPEC.role_id, "werewolf", False)},
    )
    queue = ResponseQueue(snapshot)
    for allowed in ("wolf_kill", "exile", "hunter_shot"):
        event = DomainEvent("event:" + "2" * 16, "PLAYER_DIED", {"target_seat": 1}, allowed)
        # The Wolf King carries the ordinary claw plus the shared awakened-claw
        # response; the latter is skipped at execution because this seat holds
        # no ``awakened_claw`` resource, but the queue does open its window.
        assert len(queue.open(game, event, depth=0)) == 2, allowed
    for forbidden in ("self_explode", "poison", "charm", "knight_duel", "unknown"):
        event = DomainEvent(
            "event:" + "3" * 16, "PLAYER_DIED", {"target_seat": 1}, forbidden)
        assert queue.open(game, event, depth=0) == (), forbidden


def test_scheduler_dead_wolf_king_shoots_once_and_consumes_the_claw() -> None:
    registry = RoleRegistry()
    registry.register_pipeline(WOLF_KING_SPEC)
    registry.register_pipeline(KILLER_SPEC)
    snapshot = registry.freeze()
    game = GameState(
        "wolf-king-game", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, WOLF_KING_SPEC.role_id, "werewolf"),
            2: PlayerState(2, KILLER_SPEC.role_id, "werewolf"),
        },
    )
    calls = []

    def provider(request, projected, attempt):
        if request.role_id == WOLF_KING_SPEC.role_id:
            calls.append(projected)
            assert projected.actor_alive is False
            return command("shoot", 2)
        return command("kill")

    engine = Scheduler(
        snapshot, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert len(calls) == 1
    assert calls[0].trigger_reason == "wolf_kill"
    assert game.players[1].is_alive is False
    assert role_resource_view(game, 1) == {"wolf_claw": 0}
    assert game._pipeline_runtime.pending_damage == (
        {"target": 2, "amount": 1, "cause": "wolf_king_shot"},
    )

    engine.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_legacy_wolf_king_keeps_kill_and_claw_skills() -> None:
    """The claw needs no daytime method: the pipeline contract drives it."""
    class Builder:
        def build_action_prompt(self, *args): return "prompt"

    class Parser:
        def parse_night_action(self, raw, seat): return NightAction(seat, "kill", 2)

    role = object.__new__(WolfKing)
    role.seat = 1
    role.role_name = WOLF_KING_SPEC.role_id
    role.prompt_builder = Builder()
    role.output_parser = Parser()

    async def invoke(prompt): return "raw"

    role._invoke_llm = invoke
    assert role.get_skills() == ["kill", "shoot"]
    # The inherited nightly kill path still works unchanged.
    assert (await role.kill(GameState("g"), object())).target_seat == 2
