"""Time Wolf: lock a seat so the good camp's seer / poison / guard land on their caster.

Hook-level checks for the declarations and the lock itself, then the whole card
through the real ``Scheduler`` — the redirection lives in three *other* roles, so
only an end-to-end run proves the pieces fit.
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector, _time_lock_spent
from app.core.effect_applier import EffectApplier
from app.core.night_settlement import NIGHT_SCOPED_STATUSES, TIME_LOCKED_STATUS
from app.core.role_runtime import role_private_facts_view
from app.core.scheduler import Scheduler
from app.core.time_lock import (
    SELF_CHECK_CAMP, TIME_LOCK_HIT_RESOURCE, is_locked, lock_hit_effect,
)
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.registry import builtin_registry
from app.roles.time_wolf import (
    LOCKED_KEY, TIME_LOCK_CONTRACT, TIME_WOLF_SPEC, TimeWolf, resolve_time_wolf_action,
    time_wolf_applicable,
)

TIME_WOLF_ROLE = "wolf-killer-time-wolf"
# seats: 1 wolf, 2 time wolf, 3 seer, 4 witch, 5 guard, 6 villager, 7 order prince
WOLF, TWOLF, SEER, WITCH, GUARD, VILL, PRINCE = range(1, 8)
ROLES = {
    WOLF: ("wolf-killer-werewolf", "werewolf"), TWOLF: (TIME_WOLF_ROLE, "werewolf"),
    SEER: ("wolf-killer-seer", "good"), WITCH: ("wolf-killer-witch", "good"),
    GUARD: ("wolf-killer-guard", "good"), VILL: ("wolf-killer-villager", "good"),
    PRINCE: ("wolf-killer-order-prince", "good"),
}


def cmd(action: str, target: int | None = None, *, degraded: bool = False) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="理由", degraded=degraded)


def context(*, facts=None, alive=True, round_number=1) -> ActionContext:
    contract = TIME_LOCK_CONTRACT
    return ActionContext(
        game_id="g", revision=3, facts={"alive_seats": (1, 2, 3), **(facts or {})},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=TWOLF,
        actor_role_id=TIME_WOLF_ROLE, actor_alive=alive, action_key="k:1",
    )


# ── declarations ───────────────────────────────────────────────────────────


def test_the_card_is_a_pack_wolf_that_acts_before_every_good_skill() -> None:
    from app.roles.guard import GUARD_SPEC
    from app.roles.werewolf import WEREWOLF_KILL_CONTRACT

    assert TIME_WOLF_SPEC.camp_id == "werewolf" and TIME_WOLF_SPEC.tags == frozenset({"wolf"})
    # A pack wolf: it shares the byte-identical kill contract and has no hidden-wolf marker.
    assert TIME_WOLF_SPEC.contracts[0].to_json() == WEREWOLF_KILL_CONTRACT.to_json()
    assert "no_wolf_pack" not in TIME_WOLF_SPEC.initial_resources
    assert TIME_LOCK_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert TIME_LOCK_CONTRACT.order < GUARD_SPEC.contracts[0].order
    assert TIME_LOCK_CONTRACT.fallback_action_type == "pass"
    assert TIME_WOLF_SPEC.projected_facts == frozenset({"time_lock_spent"})
    assert TIME_WOLF_SPEC.opening_narration[0] == "蚀时狼妃请睁眼"
    assert builtin_registry.require(TIME_WOLF_ROLE).role_factory is TimeWolf
    assert TIME_LOCKED_STATUS in NIGHT_SCOPED_STATUSES


def test_the_lock_is_open_while_alive_and_unspent() -> None:
    assert time_wolf_applicable(context()) is True
    assert time_wolf_applicable(context(alive=False)) is False
    assert time_wolf_applicable(context(facts={"time_lock_spent": True})) is False
    assert time_wolf_applicable(context(facts={"time_lock_spent": False})) is True


# ── the lock hook ──────────────────────────────────────────────────────────


def test_locking_a_seat_marks_it_for_the_night_and_remembers_it() -> None:
    effects = resolve_time_wolf_action(context(), cmd("lock", VILL))
    assert [e.kind for e in effects] == [
        EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT,
        EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload["key"] == LOCKED_KEY
    assert effects[0].payload["value"] == {"seat": VILL, "round": 1}
    assert effects[1].payload == {"target": VILL, "status": TIME_LOCKED_STATUS}
    assert effects[2].payload["event_type"] == "STATUS_ADDED"
    assert effects[2].payload["payload"] == {
        "seat": VILL, "status": TIME_LOCKED_STATUS, "round_number": 1,
    }
    assert effects[3].payload["event_type"] == "TIME_WOLF_REASONING"
    assert "封锁 6 号" in effects[3].payload["payload"]["thought"]


def test_passing_or_a_targetless_lock_only_reports_the_thought() -> None:
    for command in (cmd("pass"), cmd("lock", None)):
        effects = resolve_time_wolf_action(context(), command)
        assert [e.kind for e in effects] == [EffectKind.EMIT_EVENT]
        assert effects[0].payload["payload"]["thought"].startswith("决定今晚不封锁任何人")


def test_a_degraded_turn_is_labelled_as_the_systems() -> None:
    effects = resolve_time_wolf_action(context(), cmd("pass", degraded=True))
    assert effects[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert effects[0].payload["payload"]["degraded"] is True


def test_the_wolf_may_lock_herself_a_teammate_or_the_same_seat_twice() -> None:
    """The card has no restrictions: there is no validate hook at all."""
    assert TIME_LOCK_CONTRACT.validate is None


# ── the redirect helpers ───────────────────────────────────────────────────


def test_is_locked_reads_only_the_status_labels() -> None:
    assert is_locked({"status_labels": (TIME_LOCKED_STATUS,)}) is True
    assert is_locked({"status_labels": ("silenced",)}) is False
    assert is_locked({"status_labels": None}) is False
    assert is_locked({}) is False
    assert is_locked(None) is False
    assert is_locked("nope") is False


def test_the_hit_marker_needs_a_real_seat() -> None:
    effect = lock_hit_effect(context(), {"seat": VILL}, 5)
    assert effect.kind is EffectKind.SET_RESOURCE
    assert effect.payload == {"target": VILL, "resource": TIME_LOCK_HIT_RESOURCE, "value": 1}
    assert effect.target_seat == VILL and effect.preconditions == {}
    for bad in ({}, {"seat": 0}, {"seat": "6"}):
        with pytest.raises(TypeError):
            lock_hit_effect(context(), bad, 5)
    assert SELF_CHECK_CAMP == "good"


def test_the_spent_fact_reads_the_marker_on_the_last_locked_seat() -> None:
    wolf = PlayerState(TWOLF, TIME_WOLF_ROLE, "werewolf")
    state = _state("spent-fact")
    # No runtime yet: nothing can have been locked.
    assert _time_lock_spent(state, wolf) is False

    table = Table(state, {"time_lock": cmd("lock", VILL)})
    table.night(1, SchedulePoint.NIGHT_ACTION)
    assert _time_lock_spent(state, wolf) is False
    table.runtime.role_resources.setdefault(VILL, {})[TIME_LOCK_HIT_RESOURCE] = 1
    assert _time_lock_spent(state, wolf) is True
    # A malformed memory is read as "never locked", not as a crash.
    table.runtime.private_data[TWOLF][LOCKED_KEY] = 6
    assert _time_lock_spent(state, wolf) is False


# ── end to end through the real scheduler ──────────────────────────────────


def _state(game_id: str) -> GameState:
    return GameState(
        game_id, phase=GamePhase.NIGHT, round_number=1,
        players={seat: PlayerState(seat, role, camp) for seat, (role, camp) in ROLES.items()},
    )


class Table:
    """A scripted night: answers by contract id and records who was asked."""

    def __init__(self, state: GameState, answers: dict[str, ActionCommand]) -> None:
        self.state = state
        self.answers = answers
        self.asked: list[tuple[int, str]] = []
        self.scheduler = Scheduler(
            builtin_registry.freeze(), ContextProjector(), ActionValidator(),
            ActionResolver(), EffectApplier(), self._provider,
        )

    def _provider(self, request, projected, attempt):
        self.asked.append((request.actor_seat, request.contract.contract_id))
        return self.answers.get(request.contract.contract_id, cmd("pass"))

    def night(self, round_number: int, *points: SchedulePoint) -> None:
        self.state.round_number = round_number
        self.asked.clear()
        for point in points:
            self.scheduler.run_point(self.state, point)

    @property
    def runtime(self):
        return self.state._pipeline_runtime


_NIGHT = (
    SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_SEER_ACTION,
    SchedulePoint.NIGHT_WITCH_ACTION,
)


def test_a_seer_check_on_the_locked_seat_lands_on_the_seer() -> None:
    state = _state("lock-seer")
    table = Table(state, {
        "time_lock": cmd("lock", VILL), "seer_check": cmd("check", VILL),
    })
    table.night(1, *_NIGHT)
    checks = role_private_facts_view(state, SEER, "private_checks")
    assert [dict(item) for item in checks] == [{"target": SEER, "camp": "good"}]
    assert table.runtime.role_resources[VILL][TIME_LOCK_HIT_RESOURCE] == 1
    assert table.runtime.statuses[VILL] == {TIME_LOCKED_STATUS}

    # The lock took effect, so the Wolf loses the skill from the next night on.
    EffectApplier().commit_night(state, round_number=1)
    assert TIME_LOCKED_STATUS not in table.runtime.statuses.get(VILL, set())
    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert (TWOLF, "time_lock") not in table.asked
    table.night(3, SchedulePoint.NIGHT_ACTION)
    assert (TWOLF, "time_lock") not in table.asked, "the loss is permanent"


def test_a_check_on_an_unlocked_seat_is_untouched() -> None:
    state = _state("lock-miss")
    table = Table(state, {"time_lock": cmd("lock", VILL), "seer_check": cmd("check", WOLF)})
    table.night(1, *_NIGHT)
    checks = role_private_facts_view(state, SEER, "private_checks")
    assert [dict(item) for item in checks] == [{"target": WOLF, "camp": "werewolf"}]
    assert TIME_LOCK_HIT_RESOURCE not in table.runtime.role_resources.get(VILL, {})


def test_a_lock_nobody_cast_into_does_not_cost_the_wolf_her_skill() -> None:
    state = _state("lock-unused")
    table = Table(state, {"time_lock": cmd("lock", WOLF)})
    table.night(1, SchedulePoint.NIGHT_ACTION)
    EffectApplier().commit_night(state, round_number=1)
    assert TIME_LOCKED_STATUS not in table.runtime.statuses.get(WOLF, set())
    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert (TWOLF, "time_lock") in table.asked
    # Locking the same seat again on consecutive nights is allowed.
    EffectApplier().commit_night(state, round_number=2)
    table.night(3, SchedulePoint.NIGHT_ACTION)
    assert table.runtime.private_data[TWOLF][LOCKED_KEY] == {"seat": WOLF, "round": 3}


def test_a_poison_on_the_locked_seat_lands_on_the_witch() -> None:
    state = _state("lock-witch")
    table = Table(state, {"time_lock": cmd("lock", VILL), "witch_action": cmd("poison", VILL)})
    table.night(1, *_NIGHT)
    assert {"target": WITCH, "amount": 1, "cause": "poison"} in table.runtime.pending_damage
    assert not [d for d in table.runtime.pending_damage if d["target"] == VILL]
    assert table.runtime.role_resources[VILL][TIME_LOCK_HIT_RESOURCE] == 1


def test_a_poison_on_an_unlocked_seat_is_untouched() -> None:
    state = _state("lock-witch-miss")
    table = Table(state, {"time_lock": cmd("lock", GUARD), "witch_action": cmd("poison", VILL)})
    table.night(1, *_NIGHT)
    assert {"target": VILL, "amount": 1, "cause": "poison"} in table.runtime.pending_damage


def test_a_guard_on_the_locked_seat_guards_the_guard() -> None:
    state = _state("lock-guard")
    table = Table(state, {"time_lock": cmd("lock", VILL), "guard_action": cmd("guard", VILL)})
    table.night(1, SchedulePoint.NIGHT_ACTION)
    assert table.runtime.pending_protection == (
        {"target": GUARD, "amount": 1, "source": "guard"},
    )
    assert table.runtime.role_resources[VILL][TIME_LOCK_HIT_RESOURCE] == 1


def _locked_target(seat: int, **extra: object) -> dict[str, object]:
    return {"seat": seat, "status_labels": (TIME_LOCKED_STATUS,), **extra}


def test_a_redirected_guard_names_the_original_target_and_still_remembers_itself() -> None:
    from app.roles.guard import GUARD_SPEC, resolve_guard_action

    contract = GUARD_SPEC.contracts[0]
    ctx = ActionContext(
        game_id="g", revision=3,
        facts={"alive_seats": (3, 4, 5, 6), "selected_target": _locked_target(VILL)},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=GUARD,
        actor_role_id="wolf-killer-guard", actor_alive=True, action_key="k:g",
    )
    effects = resolve_guard_action(ctx, cmd("guard", VILL))
    protect = next(item for item in effects if item.payload.get("event_type") == "GUARD_PROTECT")
    assert protect.payload["payload"] == {
        "target_seat": GUARD, "seat": GUARD, "intended_seat": VILL,
    }
    memory = next(item for item in effects if item.kind is EffectKind.SET_PRIVATE_DATA)
    assert memory.payload["value"] == {"seat": GUARD, "round": 1}


def test_an_unredirected_guard_does_not_name_an_intended_seat() -> None:
    from app.roles.guard import GUARD_SPEC, resolve_guard_action

    contract = GUARD_SPEC.contracts[0]
    ctx = ActionContext(
        game_id="g", revision=3,
        facts={
            "alive_seats": (3, 4, 5, 6),
            "selected_target": {"seat": VILL, "status_labels": ()},
        },
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=GUARD,
        actor_role_id="wolf-killer-guard", actor_alive=True, action_key="k:g",
    )
    effects = resolve_guard_action(ctx, cmd("guard", VILL))
    protect = next(item for item in effects if item.payload.get("event_type") == "GUARD_PROTECT")
    assert protect.payload["payload"] == {"target_seat": VILL, "seat": GUARD}


def test_a_redirected_seer_check_names_the_original_target() -> None:
    from app.roles.seer import SEER_SPEC, resolve_seer_action

    contract = SEER_SPEC.contracts[0]
    ctx = ActionContext(
        game_id="g", revision=3,
        facts={
            "alive_seats": (3, 4, 6),
            "selected_target": _locked_target(VILL, camp_label="werewolf"),
        },
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=SEER,
        actor_role_id="wolf-killer-seer", actor_alive=True, action_key="k:s",
    )
    effects = resolve_seer_action(ctx, cmd("check", VILL))
    check = next(item for item in effects if item.payload.get("event_type") == "SEER_CHECK")
    assert check.payload["payload"] == {
        "target_seat": SEER, "result": SELF_CHECK_CAMP, "seat": SEER,
        "intended_seat": VILL,
    }


def test_a_redirected_poison_names_the_original_target() -> None:
    from app.roles.witch import resolve_witch_action

    witch_contract = builtin_registry.freeze().require("wolf-killer-witch").contracts[0]
    ctx = ActionContext(
        game_id="g", revision=3,
        facts={
            "alive_seats": (3, 4, 6),
            "selected_target": _locked_target(VILL, resource_labels=()),
        },
        config_version="a" * 64, contract_id=witch_contract.contract_id,
        contract_version=witch_contract.schema_version,
        contract_digest=witch_contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=witch_contract.schedule_point, actor_seat=WITCH,
        actor_role_id="wolf-killer-witch", actor_alive=True,
        resources={"antidote": 1, "poison": 1}, action_key="k:p",
    )
    effects = resolve_witch_action(ctx, cmd("poison", VILL))
    poison = next(item for item in effects if item.payload.get("event_type") == "WITCH_POISON")
    assert poison.payload["payload"] == {
        "target_seat": WITCH, "seat": WITCH, "intended_seat": VILL,
    }


def test_the_antidote_is_never_redirected() -> None:
    from app.roles.witch import resolve_witch_action

    witch_contract = builtin_registry.freeze().require("wolf-killer-witch").contracts[0]
    ctx = ActionContext(
        game_id="g", revision=3,
        facts={
            "alive_seats": (3, 4, 6),
            "selected_target": {
                "seat": VILL, "resource_labels": (), "status_labels": (TIME_LOCKED_STATUS,),
            },
        },
        config_version="a" * 64, contract_id=witch_contract.contract_id,
        contract_version=witch_contract.schema_version,
        contract_digest=witch_contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=witch_contract.schedule_point, actor_seat=WITCH,
        actor_role_id="wolf-killer-witch", actor_alive=True,
        resources={"antidote": 1, "poison": 1}, action_key="k:w",
    )
    effects = resolve_witch_action(ctx, cmd("save", VILL))
    assert all(e.payload.get("resource") != TIME_LOCK_HIT_RESOURCE for e in effects)
    assert any(e.payload.get("target") == VILL for e in effects)
    save = next(item for item in effects if item.payload.get("event_type") == "WITCH_SAVE")
    assert save.payload["payload"] == {"target_seat": VILL, "seat": WITCH}
