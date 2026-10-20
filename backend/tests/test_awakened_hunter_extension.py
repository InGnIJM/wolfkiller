"""Awakened Hunter: a directional shot that can only land on a werewolf.

What this file pins is the part that makes the role different from the ordinary
hunter: *which* seat dies is derived from a direction by the rules, the direction
is the only thing the model chooses, and the two roles disagree about which ways
out open the shot (this one: all of them, the poison included).
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import (
    ContextProjector, _DERIVED_FACT_BUILDERS,
)
from app.core.effect_applier import CommitResult, EffectApplier, _Runtime
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, IssuedActionRequest,
    PROJECTED_FACT_NAMES, SchedulePoint,
)
from app.agents.prompt_renderer import PromptRenderer
from app.core.point_journal import (
    PendingEvent, PointCheckpoint, PointKey, WorkCursor, point_journal,
)
from app.core.registry_identity import frozen_registry_digest
from app.roles.awakened_hunter import (
    AWAKENED_HUNT_CONTRACT, AWAKENED_HUNTER_SPEC, AwakenedHunter,
    awakened_hunter_applicable, resolve_awakened_hunter_action,
    validate_awakened_hunter_action,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC

HUNTER = "wolf-killer-awakened-hunter"
WOLF = "wolf-killer-werewolf"
VILLAGER = "wolf-killer-villager"
EVENT_ID = "event:" + "7" * 16


def hunter_registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, AWAKENED_HUNTER_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def context(
    *, actor: int = 1, alive: bool = False, gun: int = 1,
    forward: int | None = 4, backward: int | None = 3,
    trigger_seat: int | None = 1, cause: str | None = "poison",
    phase: str = "night", source_event: str | None = EVENT_ID,
    facts: bool = True,
) -> ActionContext:
    contract = AWAKENED_HUNT_CONTRACT
    projected: dict[str, object] = {"alive_seats": (1, 2, 3, 4)}
    if facts:
        projected["directional_wolves"] = {"forward": forward, "backward": backward}
    return ActionContext(
        "g", 7, projected, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=2, phase=phase,
        window_id="w", schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=HUNTER, actor_alive=alive, resources={"gun": gun},
        action_key="hunt:1", source_event_id=source_event,
        trigger_event=None if trigger_seat is None else {
            "event_id": EVENT_ID, "type": "PLAYER_DIED",
            "target_seat": trigger_seat, "cause": cause,
            "round_number": 2, "phase": phase,
        },
        trigger_reason=cause,
    )


def command(action_type: str = "hunt_forward", target: int | None = None) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="ok")


def kinds(effects) -> list[str]:
    return [effect.kind.value for effect in effects]


def test_every_projected_fact_name_has_a_builder() -> None:
    assert PROJECTED_FACT_NAMES == set(_DERIVED_FACT_BUILDERS)


def test_spec_declares_a_direction_only_contract() -> None:
    assert AWAKENED_HUNTER_SPEC.camp_id == "good"
    assert AWAKENED_HUNTER_SPEC.tags == frozenset({"god"})
    assert AWAKENED_HUNTER_SPEC.max_count == 1
    assert AWAKENED_HUNTER_SPEC.contracts == (AWAKENED_HUNT_CONTRACT,)
    assert AWAKENED_HUNTER_SPEC.initial_resources == {"gun": 1}
    assert AWAKENED_HUNTER_SPEC.projected_facts == frozenset({"directional_wolves"})
    assert AWAKENED_HUNT_CONTRACT.schedule_point is SchedulePoint.DAWN_REACTION
    assert AWAKENED_HUNT_CONTRACT.order == 45
    assert AWAKENED_HUNT_CONTRACT.action_types == ("hunt_forward", "hunt_backward", "pass")
    # No target field: the model picks a direction, never a seat.
    assert AWAKENED_HUNT_CONTRACT.actions_requiring_target == frozenset()
    assert AWAKENED_HUNT_CONTRACT.fallback_action_type == "pass"
    assert AWAKENED_HUNT_CONTRACT.per_window_limit == 1
    assert AWAKENED_HUNT_CONTRACT.per_game_limit == 1


def test_every_death_cause_opens_the_shot_the_poison_included() -> None:
    from app.models.values import DeathCause

    assert AWAKENED_HUNT_CONTRACT.response_reasons == {
        cause.value for cause in DeathCause
    }
    assert "poison" in AWAKENED_HUNT_CONTRACT.response_reasons


def test_applicable_needs_an_own_death_and_a_reachable_wolf() -> None:
    assert awakened_hunter_applicable(context()) is True
    # Alive, or still holding the gun, or a death that is not its own: shut.
    assert awakened_hunter_applicable(context(alive=True)) is False
    assert awakened_hunter_applicable(context(gun=0)) is False
    assert awakened_hunter_applicable(context(trigger_seat=2)) is False
    assert awakened_hunter_applicable(context(trigger_seat=None)) is False
    assert awakened_hunter_applicable(context(source_event=None)) is False
    # No wolf in either direction: nothing to shoot at.
    assert awakened_hunter_applicable(context(forward=None, backward=None)) is False
    # One empty direction is enough to still open the window.
    assert awakened_hunter_applicable(context(forward=None)) is True
    # Without the projected fact the contract stays shut rather than guessing.
    assert awakened_hunter_applicable(context(facts=False)) is False


def test_validate_rejects_an_empty_direction_and_a_spent_gun() -> None:
    assert validate_awakened_hunter_action(context(), command()) == ()
    assert validate_awakened_hunter_action(context(), command("pass")) == ()
    empty = validate_awakened_hunter_action(context(forward=None), command())
    assert [item.code for item in empty] == ["hunt_direction_empty"]
    spent = validate_awakened_hunter_action(context(gun=0), command())
    assert [item.code for item in spent] == ["gun_unavailable"]
    # A spent gun outranks the direction check: both are reported in order.
    both = validate_awakened_hunter_action(
        context(gun=0, forward=None), command("hunt_backward"),
    )
    assert [item.code for item in both] == ["gun_unavailable"]


def test_forward_hunts_the_next_wolf_and_spends_the_gun() -> None:
    effects = resolve_awakened_hunter_action(context(forward=4), command())
    assert kinds(effects) == [
        "consume_resource", "submit_damage", "emit_event", "emit_event",
    ]
    assert effects[0].payload == {"target": 1, "resource": "gun", "amount": 1}
    # The default context is a night death, so this shot says it may not be
    # replaced (the rule the Awakened Wolf Beauty's illusion has to respect).
    assert effects[1].payload == {
        "target": 4, "amount": 1, "cause": "awakened_hunt", "unreplaceable": True,
    }
    assert effects[1].target_seat == 4
    assert effects[2].payload == {
        "event_type": "AWAKENED_HUNT", "payload": {"target_seat": 4, "seat": 1},
    }
    assert effects[3].payload["event_type"] == "AWAKENED_HUNTER_REASONING"
    assert effects[3].payload["payload"] == {
        "seat": 1, "action_type": "hunt_forward", "target_seat": 4,
        "reasoning": "ok", "degraded": False,
        "thought": "决定正序发动巡猎，带走 4 号玩家：ok",
    }


def test_a_daytime_shot_stays_replaceable() -> None:
    """Only the night shot ignores the wolves' protective skills."""
    effects = resolve_awakened_hunter_action(
        context(phase="vote_resolution"), command(),
    )
    assert effects[1].payload == {"target": 4, "amount": 1, "cause": "awakened_hunt"}


def test_backward_hunts_the_other_side() -> None:
    effects = resolve_awakened_hunter_action(
        context(backward=3), command("hunt_backward"),
    )
    assert effects[1].payload["target"] == 3
    assert "逆序" in effects[3].payload["payload"]["thought"]


def test_passing_records_only_a_reason() -> None:
    effects = resolve_awakened_hunter_action(context(), command("pass"))
    assert kinds(effects) == ["emit_event"]
    assert effects[0].payload["payload"]["target_seat"] is None
    assert effects[0].payload["payload"]["thought"].startswith("决定不发动巡猎")


def test_a_degraded_command_reports_the_engine_line() -> None:
    from app.models.pipeline import DEGRADED_THOUGHT

    degraded = ActionCommand(
        action_type="pass", target_seat=None, reasoning="本轮未提交决策", degraded=True,
    )
    effects = resolve_awakened_hunter_action(context(), degraded)
    assert effects[0].payload["payload"]["thought"] == DEGRADED_THOUGHT


def test_resolver_adds_the_accept_effect_and_keeps_the_batch() -> None:
    registry = hunter_registry()
    resolved = ActionResolver().resolve_effects(
        context(), registry.require(HUNTER), AWAKENED_HUNT_CONTRACT, command(),
    )
    assert resolved[0].kind is EffectKind.ACCEPT_ACTION
    assert resolved[1:] == resolve_awakened_hunter_action(context(), command())


def test_validator_accepts_the_declared_batch() -> None:
    assert ActionValidator().validate(context(), AWAKENED_HUNT_CONTRACT, command()) == ()
    assert ActionValidator().validate(
        context(), AWAKENED_HUNT_CONTRACT, command("pass"),
    ) == ()


def test_registered_on_both_sides() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(HUNTER) is AWAKENED_HUNTER_SPEC
    assert builtin_registry.require(HUNTER).role_factory is AwakenedHunter
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
    assert state._pipeline_runtime.role_resources[1]["gun"] == 1


def test_the_derived_fact_reaches_the_hook_but_never_the_prompt() -> None:
    registry = hunter_registry()
    state = GameState(
        "g", phase=GamePhase.DAWN, round_number=2,
        players={
            1: PlayerState(1, HUNTER, "good"),
            2: PlayerState(2, VILLAGER, "good"),
            3: PlayerState(3, WOLF, "werewolf"),
            4: PlayerState(4, WOLF, "werewolf"),
        },
    )
    projector = ContextProjector()
    request = IssuedActionRequest(
        1, HUNTER, AWAKENED_HUNT_CONTRACT, 0, 2, "dawn", "w", "k",
    )
    projected = projector.project(state, request, registry)
    assert projected.facts["directional_wolves"] == {"forward": 3, "backward": 4}
    prompt = PromptRenderer().render(
        registry.require(HUNTER), AWAKENED_HUNT_CONTRACT, projected, "",
    )
    assert "directional_wolves" not in prompt
    assert '"forward"' not in prompt


def test_the_direction_wraps_around_the_seat_order() -> None:
    registry = hunter_registry()
    state = GameState(
        "g", phase=GamePhase.DAWN, round_number=2,
        players={
            1: PlayerState(1, HUNTER, "good"),
            2: PlayerState(2, VILLAGER, "good"),
            3: PlayerState(3, VILLAGER, "good"),
            4: PlayerState(4, WOLF, "werewolf"),
        },
    )
    request = IssuedActionRequest(
        1, HUNTER, AWAKENED_HUNT_CONTRACT, 0, 2, "dawn", "w", "k",
    )
    facts = ContextProjector().project(state, request, registry).facts
    # Forward wraps 4 -> 1 -> 2 -> 3 and finds the wolf behind the hunter; the
    # backward sweep reaches the same seat in the other direction.
    assert facts["directional_wolves"] == {"forward": 4, "backward": 4}


def test_dead_seats_are_skipped_in_both_directions() -> None:
    registry = hunter_registry()
    state = GameState(
        "g", phase=GamePhase.DAWN, round_number=2,
        players={
            1: PlayerState(1, HUNTER, "good"),
            2: PlayerState(2, WOLF, "werewolf", is_alive=False),
            3: PlayerState(3, WOLF, "werewolf"),
        },
    )
    request = IssuedActionRequest(
        1, HUNTER, AWAKENED_HUNT_CONTRACT, 0, 2, "dawn", "w", "k",
    )
    facts = ContextProjector().project(state, request, registry).facts
    assert facts["directional_wolves"] == {"forward": 3, "backward": 3}


def test_no_living_wolf_leaves_both_directions_empty() -> None:
    """A board with no living wolf left leaves nothing to resolve a direction to."""
    registry = hunter_registry()
    state = GameState(
        "g", phase=GamePhase.DAWN, round_number=2,
        players={
            1: PlayerState(1, HUNTER, "good"),
            2: PlayerState(2, VILLAGER, "good"),
            3: PlayerState(3, WOLF, "werewolf", is_alive=False),
        },
    )
    request = IssuedActionRequest(
        1, HUNTER, AWAKENED_HUNT_CONTRACT, 0, 2, "dawn", "w", "k",
    )
    facts = ContextProjector().project(state, request, registry).facts
    assert facts["directional_wolves"] == {"forward": None, "backward": None}


def _seed_death(state: GameState, registry: RegistrySnapshot, seat: int, cause: str) -> None:
    """Seed DAWN_REACTION with the engine's synthetic death commit.

    Mirrors ``GameEngine._run_response_point``: the point is given a commit that
    carries the PLAYER_DIED event, and the response queue opens the windows the
    dead seat's own contracts declare. Resuming straight into the response phase
    skips the issuance sweep that normally seeds role resources, so they are
    established here the same way the engine does.
    """
    identity = frozen_registry_digest(state, registry)
    initialize_role_resources(state, registry.specs, identity)
    events = ({"event_type": "PLAYER_DIED", "payload": {"seat": seat, "cause": cause}},)
    commit = CommitResult("death:seed", (), 0, events, "d")
    key = PointKey(
        state.game_id, state.round_number, Scheduler.point_phase(state, ""),
        SchedulePoint.DAWN_REACTION, identity,
    )
    point_journal(state).put(key, PointCheckpoint(
        (), (), (commit,), events, (), (PendingEvent(0, 0, 0),),
        WorkCursor("response", 0, 0), work_count=0,
    ))


def _state() -> GameState:
    return GameState(
        "hunt-window", phase=GamePhase.DAWN, round_number=2,
        players={
            1: PlayerState(1, HUNTER, "good", is_alive=False),
            2: PlayerState(2, VILLAGER, "good"),
            3: PlayerState(3, WOLF, "werewolf"),
            4: PlayerState(4, WOLF, "werewolf"),
        },
    )


def test_the_reaction_window_fires_the_hunt_through_the_pipeline() -> None:
    registry = hunter_registry()
    state = _state()
    _seed_death(state, registry, 1, "poison")

    def provider(request, projected, attempt):
        assert projected.facts["directional_wolves"] == {"forward": 3, "backward": 4}
        return command("hunt_backward")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    result = scheduler.run_point(state, SchedulePoint.DAWN_REACTION)

    # The point result carries the events the audience cares about; the accept
    # and the consumed gun stay in the commit itself.
    assert [event["event_type"] for event in result.events] == [
        "PLAYER_DIED", "AWAKENED_HUNT", "AWAKENED_HUNTER_REASONING",
    ]
    assert state._pipeline_runtime.pending_damage == (
        {"target": 4, "amount": 1, "cause": "awakened_hunt"},
    )
    assert state._pipeline_runtime.role_resources[1]["gun"] == 0


def test_a_hunter_who_has_already_shot_cannot_shoot_again() -> None:
    """A spent gun closes the window before the model is asked.

    Model-driven response windows consult ``is_applicable``. With the gun
    already gone that hook is false, so the death stays the only event and
    nobody is asked to choose a direction.
    """
    registry = hunter_registry()
    state = _state()
    _seed_death(state, registry, 1, "wolf_kill")
    state._pipeline_runtime.role_resources[1]["gun"] = 0
    asked: list[int] = []

    def provider(request, projected, attempt):
        asked.append(request.actor_seat)
        return command("hunt_forward")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    result = scheduler.run_point(state, SchedulePoint.DAWN_REACTION)
    assert asked == []
    assert [event["event_type"] for event in result.events] == ["PLAYER_DIED"]
    assert state._pipeline_runtime.pending_damage == ()
    assert state._pipeline_runtime.role_resources[1]["gun"] == 0


def test_the_hunt_damage_kills_the_wolf_it_resolved_to() -> None:
    registry = hunter_registry()
    state = _state()
    _seed_death(state, registry, 1, "wolf_kill")
    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), lambda request, projected, attempt: command("hunt_forward"),
    )
    scheduler.run_point(state, SchedulePoint.DAWN_REACTION)
    settlement = EffectApplier().settle_pending(state, round_number=2)
    assert settlement is not None
    deaths = [
        (event["payload"]["seat"], event["payload"]["cause"])
        for event in settlement.events if event["event_type"] == "PLAYER_DIED"
    ]
    assert deaths == [(3, "awakened_hunt")]
    assert state.players[3].is_alive is False


@pytest.mark.parametrize("cause", ["poison", "exile", "hunter_shot", "knight_duel"])
def test_every_death_cause_actually_opens_the_window(cause: str) -> None:
    """The whitelist is the whole directory, so no cause silently closes it."""
    registry = hunter_registry()
    state = _state()
    _seed_death(state, registry, 1, cause)
    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), lambda request, projected, attempt: command("hunt_forward"),
    )
    result = scheduler.run_point(state, SchedulePoint.DAWN_REACTION)
    assert "AWAKENED_HUNT" in [event["event_type"] for event in result.events]
