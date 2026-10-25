"""Awakened Wolf Beauty: the charm that dies in her place.

Three paths have to agree about one skill: the night settlement redirects a
lethal hit onto the stand-in, the exile verdict cancels the exile before the
engine can mark her dead, and an ``unreplaceable`` hit from a night-dead
Awakened Hunter ignores the illusion entirely. What this file pins is that all
three spend the same marker and that the effect is once per game.
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, _Runtime
from app.core.night_settlement import (
    STAND_IN_CAUSE, STAND_IN_RESOURCE, STAND_IN_USED_RESOURCE, settle,
)
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.core.sheriff_flow import NO_SELF_EXPLODE_RESOURCE, SheriffDirector
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.awakened_wolf_beauty import (
    AWAKENED_BEAUTY_CHARM_CONTRACT, AWAKENED_BEAUTY_STAND_IN_CONTRACT,
    AWAKENED_WOLF_BEAUTY_SPEC, AwakenedWolfBeauty, LAST_ILLUSION_KEY,
    awakened_beauty_applicable, awakened_beauty_stand_in_applicable,
    react_awakened_beauty_stand_in, resolve_awakened_beauty_action,
    validate_awakened_beauty_action,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC

BEAUTY = "wolf-killer-awakened-wolf-beauty"
WOLF = "wolf-killer-werewolf"
VILLAGER = "wolf-killer-villager"
EVENT_ID = "event:" + "8" * 16


def beauty_registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, AWAKENED_WOLF_BEAUTY_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def context(
    *, actor: int = 1, alive: bool = True, stand_in: int = 0, used: int = 0,
    last_round: int | None = None, round_number: int = 2,
    trigger_seat: int | None = None, cause: str | None = None,
    alive_seats: tuple[int, ...] = (1, 2, 3), memory: object = "auto",
) -> ActionContext:
    contract = AWAKENED_BEAUTY_CHARM_CONTRACT
    facts: dict[str, object] = {
        "alive_seats": alive_seats,
        "actor_identity": {"seat": actor, "role_id": BEAUTY, "camp_id": "werewolf"},
    }
    if memory == "auto":
        if last_round is not None:
            facts[LAST_ILLUSION_KEY] = {"seat": 2, "round": last_round}
    elif memory is not None:
        facts[LAST_ILLUSION_KEY] = memory
    return ActionContext(
        "g", 7, facts, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=round_number,
        phase="night", window_id="w", schedule_point=contract.schedule_point,
        actor_seat=actor, actor_role_id=BEAUTY, actor_alive=alive,
        resources={STAND_IN_RESOURCE: stand_in, STAND_IN_USED_RESOURCE: used},
        action_key="illusion:1",
        source_event_id=EVENT_ID if cause is not None else None,
        trigger_event=None if cause is None else {
            "event_id": EVENT_ID, "type": "EXILE_PENDING",
            "target_seat": actor if trigger_seat is None else trigger_seat,
            "cause": cause, "round_number": round_number, "phase": "vote_resolution",
        },
        trigger_reason=cause,
    )


def command(action_type: str = "charm", target: int | None = 2) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="ok")


def kinds(effects) -> list[str]:
    return [effect.kind.value for effect in effects]


def test_spec_declares_the_kill_the_illusion_and_the_replacement() -> None:
    assert AWAKENED_WOLF_BEAUTY_SPEC.camp_id == "werewolf"
    assert AWAKENED_WOLF_BEAUTY_SPEC.tags == frozenset({"wolf"})
    assert AWAKENED_WOLF_BEAUTY_SPEC.max_count == 1
    assert [c.contract_id for c in AWAKENED_WOLF_BEAUTY_SPEC.contracts] == [
        "werewolf_kill", "awakened_beauty_charm", "awakened_beauty_stand_in",
        "awakened_wolf_claw_gift",
    ]
    assert AWAKENED_WOLF_BEAUTY_SPEC.initial_resources == {
        STAND_IN_RESOURCE: 0, STAND_IN_USED_RESOURCE: 0,
        "self_kill_forbidden": 1, NO_SELF_EXPLODE_RESOURCE: 1,
    }
    assert AWAKENED_BEAUTY_CHARM_CONTRACT.schedule_point is SchedulePoint.NIGHT_WITCH_ACTION
    assert AWAKENED_BEAUTY_CHARM_CONTRACT.order == 55
    assert AWAKENED_BEAUTY_STAND_IN_CONTRACT.schedule_point is SchedulePoint.EXILE_VERDICT
    assert AWAKENED_BEAUTY_STAND_IN_CONTRACT.response_event_types == {"EXILE_PENDING"}
    assert AWAKENED_BEAUTY_STAND_IN_CONTRACT.response_reasons == {"exile"}
    assert AWAKENED_BEAUTY_STAND_IN_CONTRACT.react is react_awakened_beauty_stand_in
    assert AWAKENED_WOLF_BEAUTY_SPEC.opening_narration == (
        "觉醒狼美人请睁眼", "请魅惑一名玩家作为替身（每隔一晚一次）。",
    )


def test_the_illusion_is_offered_every_other_night_until_it_fires() -> None:
    assert awakened_beauty_applicable(context(round_number=1)) is True
    # A charm last night closes tonight; the night after opens again.
    assert awakened_beauty_applicable(context(last_round=1, round_number=2)) is False
    assert awakened_beauty_applicable(context(last_round=1, round_number=3)) is True
    # Once the replacement happened the skill is gone for good.
    assert awakened_beauty_applicable(context(last_round=1, round_number=3, used=1)) is False
    assert awakened_beauty_applicable(context(alive=False)) is False


def test_a_memory_the_hook_cannot_date_keeps_the_window_shut() -> None:
    """An undated record reads as "last night", the same reading the shared
    target memory gives a pre-round archive — never as "no charm ever"."""
    assert awakened_beauty_applicable(context(memory={"seat": 2})) is False
    assert awakened_beauty_applicable(context(memory=2)) is False
    assert awakened_beauty_applicable(context(memory={"seat": 2, "round": "x"})) is False
    assert awakened_beauty_applicable(context(memory=None)) is True


def test_validate_forbids_charming_yourself() -> None:
    assert validate_awakened_beauty_action(context(), command()) == ()
    assert validate_awakened_beauty_action(context(), command("pass", None)) == ()
    missing = validate_awakened_beauty_action(context(), command("charm", None))
    assert [item.code for item in missing] == ["charm_target_required"]
    itself = validate_awakened_beauty_action(context(), command("charm", 1))
    assert [item.code for item in itself] == ["self_target"]


def test_the_charm_records_the_stand_in_and_spends_nothing_yet() -> None:
    effects = resolve_awakened_beauty_action(context(), command())
    assert kinds(effects) == [
        "set_resource", "set_private_data", "emit_event", "emit_event",
    ]
    assert effects[0].payload == {
        "target": 1, "resource": STAND_IN_RESOURCE, "value": 2,
    }
    assert effects[0].target_seat == 1
    assert dict(effects[1].payload)["value"] == {"seat": 2, "round": 2}
    assert effects[2].payload == {
        "event_type": "WRAITH_ILLUSION",
        "payload": {"seat": 1, "target_seat": 2, "round_number": 2},
    }
    assert effects[3].payload["event_type"] == "AWAKENED_BEAUTY_REASONING"
    assert "替身" in effects[3].payload["payload"]["thought"]


def test_passing_records_only_a_reason() -> None:
    effects = resolve_awakened_beauty_action(context(), command("pass", None))
    assert kinds(effects) == ["emit_event"]
    assert effects[0].payload["payload"]["thought"].startswith("决定本晚不发动挽歌幻象")


def test_a_degarded_command_reports_the_engine_line() -> None:
    from app.models.pipeline import DEGRADED_THOUGHT

    degraded = ActionCommand(
        action_type="pass", target_seat=None, reasoning="本轮未提交决策", degraded=True,
    )
    effects = resolve_awakened_beauty_action(context(), degraded)
    assert effects[0].payload["payload"]["thought"] == DEGRADED_THOUGHT


def test_resolver_and_validator_accept_the_declared_batch() -> None:
    registry = beauty_registry()
    resolved = ActionResolver().resolve_effects(
        context(), registry.require(BEAUTY), AWAKENED_BEAUTY_CHARM_CONTRACT, command(),
    )
    assert resolved[0].kind is EffectKind.ACCEPT_ACTION
    assert ActionValidator().validate(
        context(), AWAKENED_BEAUTY_CHARM_CONTRACT, command(),
    ) == ()


def test_the_exile_react_sends_the_stand_in_and_flips_the_card() -> None:
    exiled = context(stand_in=3, trigger_seat=1, cause="exile", alive_seats=(1, 3))
    assert awakened_beauty_stand_in_applicable(exiled) is True
    effects = react_awakened_beauty_stand_in(exiled)
    assert kinds(effects) == [
        "mark_death", "set_resource", "set_resource", "emit_event", "emit_event",
    ]
    assert effects[0].payload == {"target": 3, "cause": STAND_IN_CAUSE}
    assert effects[1].payload == {"target": 1, "resource": STAND_IN_RESOURCE, "value": 0}
    assert effects[2].payload == {
        "target": 1, "resource": STAND_IN_USED_RESOURCE, "value": 1,
    }
    assert effects[3].payload["event_type"] == "PLAYER_REVEALED"
    assert effects[3].payload["payload"] == {
        "seat_number": 1, "role": BEAUTY, "camp": "werewolf",
    }
    assert effects[4].payload["event_type"] == "EXILE_CANCELLED"


@pytest.mark.parametrize(
    "case",
    [
        {"stand_in": 0},                       # never charmed
        {"stand_in": 3, "used": 1},            # the skill is already spent
        {"stand_in": 3, "cause": "wolf_kill"},  # not the exile window
        {"stand_in": 3, "cause": "exile", "trigger_seat": 2},
        {"stand_in": 3, "cause": "exile", "alive": False},
        {"stand_in": 3, "cause": "exile", "alive_seats": (1, 2)},  # stand-in died
    ],
)
def test_the_exile_react_stays_silent_where_the_skill_does_not_apply(case: dict) -> None:
    exiled = context(
        stand_in=case.get("stand_in", 3), used=case.get("used", 0),
        trigger_seat=case.get("trigger_seat", 1), cause=case.get("cause", "exile"),
        alive=case.get("alive", True), alive_seats=case.get("alive_seats", (1, 3)),
    )
    assert awakened_beauty_stand_in_applicable(exiled) is False
    assert react_awakened_beauty_stand_in(exiled) == ()


def test_the_night_settlement_sends_the_stand_in_instead() -> None:
    substitutions: list[dict[str, int]] = []
    deaths, alive, delayed = settle(
        ({"target": 1, "amount": 1, "cause": "wolf_kill"},), (), {1, 2, 3},
        {1: True, 2: True, 3: True}, round_number=2,
        role_resources={1: {STAND_IN_RESOURCE: 3}},
        substitutions=substitutions,
    )
    assert [(item["seat"], item["cause"]) for item in deaths] == [(3, STAND_IN_CAUSE)]
    assert substitutions == [{"victim": 1, "substitute": 3}]
    assert alive[1] is True and alive[3] is False
    assert delayed == {}


def test_a_guard_on_the_stand_in_does_not_save_it() -> None:
    """The replacement is a direct death: shields never enter the comparison."""
    deaths, alive, _ = settle(
        ({"target": 1, "amount": 1, "cause": "wolf_kill"},),
        ({"target": 3, "amount": 1, "source": "guard"},), {1, 2, 3},
        {1: True, 2: True, 3: True}, round_number=2,
        role_resources={1: {STAND_IN_RESOURCE: 3}},
    )
    assert [(item["seat"], item["cause"]) for item in deaths] == [(3, STAND_IN_CAUSE)]
    assert alive[1] is True and alive[3] is False


def test_an_unreplaceable_hit_ignores_the_illusion() -> None:
    """The Awakened Hunter's night shot is the one hit the skill cannot absorb."""
    substitutions: list[dict[str, int]] = []
    deaths, alive, _ = settle(
        ({"target": 1, "amount": 1, "cause": "awakened_hunt", "unreplaceable": True},),
        (), {1, 2, 3}, {1: True, 2: True, 3: True}, round_number=2,
        role_resources={1: {STAND_IN_RESOURCE: 3}},
        substitutions=substitutions,
    )
    assert [(item["seat"], item["cause"]) for item in deaths] == [(1, "awakened_hunt")]
    assert substitutions == []
    assert alive[1] is False and alive[3] is True


def test_a_dead_stand_in_replaces_nobody() -> None:
    deaths, alive, _ = settle(
        ({"target": 1, "amount": 1, "cause": "poison"},), (), {1, 2, 3},
        {1: True, 2: True, 3: False}, round_number=2,
        role_resources={1: {STAND_IN_RESOURCE: 3}},
    )
    assert [(item["seat"], item["cause"]) for item in deaths] == [(1, "poison")]
    assert alive[1] is False


def test_a_stand_in_who_is_also_knifed_dies_once_with_the_replacement_cause() -> None:
    deaths, alive, _ = settle(
        (
            {"target": 1, "amount": 1, "cause": "wolf_kill"},
            {"target": 3, "amount": 1, "cause": "wolf_kill"},
        ),
        (), {1, 2, 3}, {1: True, 2: True, 3: True}, round_number=2,
        role_resources={1: {STAND_IN_RESOURCE: 3}},
    )
    assert [(item["seat"], item["cause"]) for item in deaths] == [(3, STAND_IN_CAUSE)]
    assert alive[1] is True and alive[3] is False


def test_a_malformed_stand_in_marker_never_redirects() -> None:
    for marker in (0, -1, "3", None, 1):
        deaths, alive, _ = settle(
            ({"target": 1, "amount": 1, "cause": "wolf_kill"},), (), {1, 2, 3},
            {1: True, 2: True, 3: True}, round_number=2,
            role_resources={1: {STAND_IN_RESOURCE: marker}},
        )
        assert [(item["seat"], item["cause"]) for item in deaths] == [(1, "wolf_kill")], marker
        assert alive[1] is False


def test_a_rejected_unreplaceable_mark_is_fatal() -> None:
    with pytest.raises(ValueError, match="unreplaceable"):
        settle(
            ({"target": 1, "amount": 1, "cause": "wolf_kill", "unreplaceable": "yes"},),
            (), {1, 2, 3}, {1: True, 2: True, 3: True}, round_number=2,
        )


def test_registered_on_both_sides_and_a_werewolf_by_factory() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(BEAUTY) is AWAKENED_WOLF_BEAUTY_SPEC
    assert builtin_registry.require(BEAUTY).role_factory is AwakenedWolfBeauty
    assert issubclass(AwakenedWolfBeauty, builtin_registry.require(WOLF).role_factory)
    assert object.__new__(AwakenedWolfBeauty).get_skills() == ["kill", "charm"]


def test_the_badge_swallowing_move_is_not_offered_to_her() -> None:
    state = GameState(
        "election", phase=GamePhase.SHERIFF_ELECTION, round_number=1,
        players={
            1: PlayerState(1, BEAUTY, "werewolf"),
            2: PlayerState(2, WOLF, "werewolf"),
        },
    )
    state._pipeline_runtime = _Runtime()
    initialize_role_resources(state, builtin_registry.freeze().specs, "a" * 64)
    assert SheriffDirector._may_explode(state, 1) is False
    assert SheriffDirector._may_explode(state, 2) is True


def _night_state() -> GameState:
    return GameState(
        "illusion", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, BEAUTY, "werewolf"),
            2: PlayerState(2, WOLF, "werewolf"),
            3: PlayerState(3, VILLAGER, "good"),
        },
    )


def test_the_whole_redirect_lands_in_one_settlement_commit() -> None:
    """Charm → the pack kills her → the stand-in dies and the skill is spent."""
    registry = beauty_registry()

    def provider(request, projected, attempt):
        if request.contract.contract_id == "werewolf_kill":
            return ActionCommand(action_type="kill", target_seat=1, reasoning="刀")
        return command("charm", 3)

    state = _night_state()
    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(state, SchedulePoint.NIGHT_WOLF_VOTE)
    scheduler.run_point(state, SchedulePoint.NIGHT_WITCH_ACTION)
    assert state._pipeline_runtime.role_resources[1][STAND_IN_RESOURCE] == 3
    assert state._pipeline_runtime.private_data[1][LAST_ILLUSION_KEY] == {
        "seat": 3, "round": 1,
    }

    settlement = EffectApplier().settle_pending(state, round_number=1, night_end=True)
    assert settlement is not None
    assert state.players[1].is_alive is True
    assert state.players[3].is_alive is False
    assert [(item.player_seat, item.cause) for item in state.death_history] == [
        (3, STAND_IN_CAUSE),
    ]
    assert state._pipeline_runtime.role_resources[1][STAND_IN_RESOURCE] == 0
    assert state._pipeline_runtime.role_resources[1][STAND_IN_USED_RESOURCE] == 1

    # The second attempt on her life lands: there is no stand-in left to send.
    assert awakened_beauty_applicable(
        context(last_round=1, round_number=3, used=1),
    ) is False
