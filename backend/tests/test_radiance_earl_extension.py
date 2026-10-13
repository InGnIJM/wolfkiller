"""Radiance Earl: a once-a-night shelter that stops the knife *and* the vial.

The shelter rides the any-damage tier (``source="radiance"``), so these tests pin it
the same way the Dream Weaver's was pinned — hook level for the card's rules, then the
real ``Scheduler`` + write gate + settlement for the part that unit tests miss: the
protection source has to be accepted by the write gate, and the one-night mark that
lets the Eclipse Maid recognise a sheltered seat has to be swept when the night closes.
"""

from __future__ import annotations

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.night_settlement import PROTECTION_SOURCES, settle
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.radiance_earl import (
    LAST_SHIELDED_KEY, RADIANCE_EARL_SPEC, RADIANCE_SHIELD_CONTRACT, RadianceEarl,
    radiance_applicable, resolve_radiance_action, validate_radiance_action,
)
from app.roles.registry import builtin_registry

EARL = "wolf-killer-radiance-earl"
# seats: 1 wolf, 2 earl, 3 villager, 4 witch
WOLF, EARL_SEAT, VILL, WITCH = 1, 2, 3, 4


def cmd(action: str, target: int | None = None, *, degraded: bool = False) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="理由", degraded=degraded)


def context(*, memory=None, alive=True, round_number=2, degraded=False) -> ActionContext:
    contract = RADIANCE_SHIELD_CONTRACT
    return ActionContext(
        game_id="g", revision=3,
        facts={"alive_seats": (1, 2, 3, 4), LAST_SHIELDED_KEY: memory},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=EARL_SEAT, actor_role_id=EARL,
        actor_alive=alive, action_key="k:1", degraded=degraded,
    )


# ── declarations ───────────────────────────────────────────────────────────


def test_the_card_is_a_good_god_with_one_optional_shelter() -> None:
    spec = RADIANCE_EARL_SPEC
    assert spec.camp_id == "good"
    assert spec.tags == frozenset({"god"})
    assert spec.max_count == 1
    assert RADIANCE_SHIELD_CONTRACT.action_types == ("shield", "pass")
    assert RADIANCE_SHIELD_CONTRACT.fallback_action_type == "pass"
    assert RADIANCE_SHIELD_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    # Ahead of the Eclipse Maid (3): the shelter mark must exist before she looks.
    assert RADIANCE_SHIELD_CONTRACT.order < 3
    assert "radiance" in PROTECTION_SOURCES
    assert issubclass(RadianceEarl, BaseRole)
    assert builtin_registry.require(EARL).role_factory is RadianceEarl
    for rule in ("第二晚", "其他", "毒药", "连续两晚"):
        assert rule in spec.instructions


def test_the_shelter_opens_from_the_second_night_while_alive() -> None:
    assert radiance_applicable(context()) is True
    assert radiance_applicable(context(round_number=1)) is False
    assert radiance_applicable(context(alive=False)) is False


# ── the card's restrictions ────────────────────────────────────────────────


def test_the_earl_cannot_shelter_itself() -> None:
    assert [v.code for v in validate_radiance_action(context(), cmd("shield", EARL_SEAT))] == [
        "self_shield_forbidden",
    ]


def test_the_same_seat_cannot_be_sheltered_on_two_consecutive_nights() -> None:
    last_night = {"seat": VILL, "round": 1}
    assert [v.code for v in validate_radiance_action(
        context(memory=last_night), cmd("shield", VILL),
    )] == ["consecutive_shield_forbidden"]
    # A skipped night breaks the chain: the memory says round 1, tonight is round 3.
    assert validate_radiance_action(
        context(memory=last_night, round_number=3), cmd("shield", VILL),
    ) == ()
    assert validate_radiance_action(context(memory=last_night), cmd("shield", WITCH)) == ()
    assert validate_radiance_action(context(memory=last_night), cmd("pass")) == ()
    assert validate_radiance_action(context(), cmd("shield", None)) == ()


# ── the hook ───────────────────────────────────────────────────────────────


def test_a_shelter_protects_marks_and_remembers() -> None:
    effects = resolve_radiance_action(context(), cmd("shield", VILL))
    assert [e.kind for e in effects] == [
        EffectKind.SUBMIT_PROTECTION, EffectKind.ADD_STATUS,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {"target": VILL, "amount": 1, "source": "radiance"}
    assert effects[1].payload == {"target": VILL, "status": "radiance_shielded"}
    assert effects[2].payload["key"] == LAST_SHIELDED_KEY
    assert effects[2].payload["value"] == {"seat": VILL, "round": 2}
    event = effects[-1].payload
    assert event["event_type"] == "RADIANCE_EARL_REASONING"
    assert "庇护 3 号玩家" in event["payload"]["thought"]


def test_passing_degrading_and_a_targetless_shield_only_leave_a_line() -> None:
    passed = resolve_radiance_action(context(), cmd("pass"))
    assert [e.kind for e in passed] == [EffectKind.EMIT_EVENT]
    assert "不庇护" in passed[0].payload["payload"]["thought"]
    degraded = resolve_radiance_action(context(degraded=True), cmd("pass", degraded=True))
    assert degraded[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert degraded[0].payload["payload"]["degraded"] is True
    assert len(resolve_radiance_action(context(), cmd("shield", None))) == 1


# ── settlement: the shelter outranks a plain guard ─────────────────────────


def _settle(cause: str, source: str):
    return settle(
        ({"target": VILL, "amount": 1, "cause": cause},),
        ({"target": VILL, "amount": 1, "source": source},),
        {1, 2, 3, 4}, {1: True, 2: True, 3: True, 4: True}, 2,
    )


def test_the_shelter_stops_both_the_knife_and_the_vial() -> None:
    for cause in ("wolf_kill", "poison"):
        deaths, alive, _ = _settle(cause, "radiance")
        assert deaths == () and alive[VILL] is True, cause


def test_a_plain_guard_still_cannot_stop_the_vial() -> None:
    assert _settle("poison", "guard")[0] != ()


# ── end to end: scheduler, write gate, settlement ──────────────────────────


def _state(game_id: str) -> GameState:
    roles = {
        WOLF: ("wolf-killer-werewolf", "werewolf"),
        EARL_SEAT: (EARL, "good"),
        VILL: ("wolf-killer-villager", "good"),
        WITCH: ("wolf-killer-witch", "good"),
    }
    return GameState(
        game_id, phase=GamePhase.NIGHT, round_number=1,
        players={seat: PlayerState(seat, role, camp) for seat, (role, camp) in roles.items()},
    )


def _scheduler(answer, asked):
    def provider(request, projected, attempt):
        asked.append((request.actor_seat, request.contract.contract_id))
        return answer(request.contract.contract_id)

    return Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )


def test_the_first_night_never_asks_the_earl() -> None:
    state, asked = _state("earl-first"), []
    _scheduler(lambda _: cmd("shield", VILL), asked).run_point(state, SchedulePoint.NIGHT_ACTION)
    assert (EARL_SEAT, "radiance_shield") not in asked


def test_a_shelter_passes_the_write_gate_and_saves_a_poisoned_seat() -> None:
    state, asked = _state("earl-save"), []
    state.round_number = 2
    scheduler = _scheduler(lambda _: cmd("shield", VILL), asked)
    scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)
    runtime = state._pipeline_runtime
    assert (EARL_SEAT, "radiance_shield") in asked
    assert runtime.pending_protection == (
        {"target": VILL, "amount": 1, "source": "radiance"},
    )
    assert runtime.statuses[VILL] == {"radiance_shielded"}
    assert runtime.private_data[EARL_SEAT][LAST_SHIELDED_KEY] == {"seat": VILL, "round": 2}

    runtime.pending_damage += ({"target": VILL, "amount": 1, "cause": "poison"},)
    result = EffectApplier().commit_night(state, round_number=2)
    assert result is not None
    assert state.players[VILL].is_alive is True
    assert state.death_history == []
    # The mark lived one night only: it must not outlast the settlement.
    assert "radiance_shielded" not in state._pipeline_runtime.statuses.get(VILL, set())


def test_without_a_shelter_the_same_poison_kills() -> None:
    state = _state("earl-control")
    state.round_number = 2
    scheduler = _scheduler(lambda _: cmd("pass"), [])
    scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)
    state._pipeline_runtime.pending_damage += (
        {"target": VILL, "amount": 1, "cause": "poison"},
    )
    EffectApplier().commit_night(state, round_number=2)
    assert state.players[VILL].is_alive is False


def test_a_declined_night_leaves_no_memory_so_the_next_night_is_free() -> None:
    state = _state("earl-skip")
    state.round_number = 2
    _scheduler(lambda _: cmd("pass"), []).run_point(state, SchedulePoint.NIGHT_ACTION)
    assert LAST_SHIELDED_KEY not in state._pipeline_runtime.private_data.get(EARL_SEAT, {})
    assert not state._pipeline_runtime.pending_protection
