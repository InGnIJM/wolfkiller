"""Nightmare: the fear window, its ordering guarantee and the pack-wide seal.

Three things need proving, and none of them is a hook fact:

1. the contract really runs inside ``NIGHT_ACTION`` ahead of the Guard, because
   the scheduler executes one point's members in ``order``;
2. a feared seat is therefore refused *in the same point run* it was feared in;
3. a feared wolf seals the whole pack's attack (that half lives in the engine,
   and the engine's own test drives it from a durable status).
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, _Runtime
from app.core.night_settlement import SILENCED_STATUS
from app.core.scheduler import Scheduler
from app.models.game import Camp, GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.guard import GUARD_SPEC
from app.roles.nightmare import (
    FEARED_KEY, NIGHTMARE_FEAR_CONTRACT, NIGHTMARE_SPEC, Nightmare,
    nightmare_applicable, resolve_nightmare_action, validate_nightmare_action,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.seer import SEER_SPEC
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, WEREWOLF_SPEC

NIGHTMARE = "wolf-killer-nightmare"
WOLF = "wolf-killer-werewolf"
GUARD = "wolf-killer-guard"
VILLAGER = "wolf-killer-villager"
EVENT_ID = "event:" + "8" * 16


def nightmare_registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, NIGHTMARE_SPEC, VILLAGER_SPEC, GUARD_SPEC, SEER_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def last_night_memory(
    value: object, round_number: int, memory_round: int | None = None,
) -> object:
    """Wrap a bare seat into the ``{seat, round}`` memory the roles now write.

    ``round_number`` is the night being played, so the memory defaults to the
    night before it. ``memory_round`` models a *skipped* night: the same seat
    remembered from further back. A non-int pass-through keeps malformed-input
    cases testable.
    """
    if value is None or type(value) is not int:
        return value
    return {
        "seat": value,
        "round": round_number - 1 if memory_round is None else memory_round,
    }


def context(
    *, target: int | None = 3, last_feared: object = None, alive: bool = True,
    actor: int = 1, round_number: int = 2, memory_round: int | None = None,
) -> ActionContext:
    contract = NIGHTMARE_FEAR_CONTRACT
    return ActionContext(
        "g", 8, {"alive_seats": (1, 2, 3, 4),
                 FEARED_KEY: last_night_memory(
                     last_feared, round_number, memory_round)},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=NIGHTMARE, actor_alive=alive, action_key="fear:1",
        source_event_id=EVENT_ID,
    )


def command(action_type: str = "fear", target: int | None = 3) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="怕了你")


def kinds(effects) -> list[str]:
    return [effect.kind.value for effect in effects]


def test_spec_is_a_werewolf_in_the_pack_with_a_first_move_window() -> None:
    assert NIGHTMARE_SPEC.camp_id == "werewolf"
    assert NIGHTMARE_SPEC.tags == frozenset({"wolf"})
    assert NIGHTMARE_SPEC.max_count == 1
    assert NIGHTMARE_SPEC.contracts[0].to_json() == WEREWOLF_SPEC.contracts[0].to_json()
    assert NIGHTMARE_SPEC.initial_private_data == {FEARED_KEY: None, "awakened_claw_gift": None}
    assert NIGHTMARE_FEAR_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert NIGHTMARE_FEAR_CONTRACT.order == 10
    # The Guard is the next night action on this board.
    assert GUARD_SPEC.contracts[0].order == 50


def test_applicable_is_just_liveness() -> None:
    assert nightmare_applicable(context()) is True
    assert nightmare_applicable(context(alive=False)) is False


def test_the_same_seat_cannot_be_feared_twice_in_a_row() -> None:
    assert validate_nightmare_action(context(last_feared=4), command(target=3)) == ()
    violations = validate_nightmare_action(context(last_feared=3), command(target=3))
    assert [item.code for item in violations] == ["consecutive_fear_forbidden"]
    assert validate_nightmare_action(context(last_feared=3), command("pass", None)) == ()
    assert validate_nightmare_action(context(last_feared=None), command(target=3)) == ()


def test_fearing_myself_or_a_teammate_is_allowed() -> None:
    """官方卡牌要把狼人算进「恐惧对象」，所以自指与狼队友都不能被拒。"""
    assert validate_nightmare_action(context(target=1, last_feared=4), command(target=1)) == ()
    assert validate_nightmare_action(context(target=2, last_feared=4), command(target=2)) == ()


def test_resolve_records_the_target_and_seals_it() -> None:
    effects = resolve_nightmare_action(context(), command(target=3))
    assert kinds(effects) == [
        "set_private_data", "add_status", "emit_event", "emit_event",
    ]
    assert dict(effects[0].payload) == {
        "target": 1, "key": FEARED_KEY, "value": {"seat": 3, "round": 2},
    }
    assert effects[1].payload == {"target": 3, "status": SILENCED_STATUS}
    assert effects[1].target_seat == 3
    assert effects[1].preconditions == {
        "status_present": {"status": SILENCED_STATUS, "present": False},
    }
    assert effects[2].payload == {
        "event_type": "STATUS_ADDED",
        "payload": {"seat": 3, "status": SILENCED_STATUS, "round_number": 2},
    }
    assert effects[3].payload["event_type"] == "NIGHTMARE_REASONING"
    assert effects[3].visibility == ("PUBLIC",)


def test_passing_only_records_a_reason() -> None:
    effects = resolve_nightmare_action(context(target=None), command("pass", None))
    assert kinds(effects) == ["emit_event"]
    assert "不恐惧" in effects[0].payload["payload"]["thought"]


def test_resolver_and_validator_accept_the_batch() -> None:
    registry = nightmare_registry()
    resolved = ActionResolver().resolve_effects(
        context(), registry.require(NIGHTMARE), NIGHTMARE_FEAR_CONTRACT, command(),
    )
    assert resolved[0].kind is EffectKind.ACCEPT_ACTION
    assert resolved[1:] == resolve_nightmare_action(context(), command())
    assert ActionValidator().validate(
        context(), NIGHTMARE_FEAR_CONTRACT, command(),
    ) == ()
    assert [
        item.code for item in ActionValidator().validate(
            context(last_feared=3), NIGHTMARE_FEAR_CONTRACT, command(),
        )
    ] == ["consecutive_fear_forbidden"]


def test_registered_on_both_sides() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(NIGHTMARE) is NIGHTMARE_SPEC
    assert builtin_registry.require(NIGHTMARE).role_factory is Nightmare
    assert WEREWOLF_KILL_CONTRACT.to_json() == NIGHTMARE_SPEC.contracts[0].to_json()




def test_the_fear_is_issued_first_and_silences_the_guard_in_the_same_point() -> None:
    """顺序即规则：同一次 NIGHT_ACTION 里先手落状态，后手当场就动不了。"""
    registry = nightmare_registry()
    state = GameState(
        "g", config=GameConfig(role_counts={NIGHTMARE: 1, GUARD: 1}),
        phase=GamePhase.NIGHT, round_number=2,
        players={
            1: PlayerState(1, NIGHTMARE, Camp.WEREWOLF),
            3: PlayerState(3, GUARD, Camp.GOOD),
            4: PlayerState(4, VILLAGER, Camp.GOOD),
        },
    )
    requested: list[tuple[str, int]] = []

    def provider(request, ctx, attempt):
        requested.append((request.contract.contract_id, request.actor_seat))
        if request.contract.contract_id == "nightmare_fear":
            return ActionCommand(action_type="fear", target_seat=3, reasoning="封守卫")
        # The guard should never be asked at all.
        return ActionCommand(action_type="guard", target_seat=4, reasoning="守人")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    result = scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)

    assert requested == [("nightmare_fear", 1)]
    assert state._pipeline_runtime.statuses[3] == {SILENCED_STATUS}
    assert dict(state._pipeline_runtime.private_data[1][FEARED_KEY]) == {
        "seat": 3, "round": state.round_number,
    }
    assert [fault["code"] for fault in result.faults] == ["actor_silenced"]
    assert result.faults[0]["seat"] == 3
    types = [event["event_type"] for event in result.events]
    assert "STATUS_ADDED" in types and "NIGHTMARE_REASONING" in types


def test_fearing_a_wolf_seals_the_whole_pack_through_the_engine_gate() -> None:
    """恐惧到狼人 → 整个狼人阵营当夜无法袭击（引擎门禁读同一份状态）。"""
    registry = nightmare_registry()
    state = GameState(
        "g", config=GameConfig(role_counts={NIGHTMARE: 1}), phase=GamePhase.NIGHT,
        round_number=2,
        players={
            1: PlayerState(1, NIGHTMARE, Camp.WEREWOLF),
            2: PlayerState(2, WOLF, Camp.WEREWOLF),
            3: PlayerState(3, VILLAGER, Camp.GOOD),
        },
    )
    state._pipeline_runtime = _Runtime()

    from app.core.game_engine import GameEngine

    engine = GameEngine.__new__(GameEngine)
    engine.state = state
    assert engine._pack_sealed([1, 2]) is False
    state._pipeline_runtime.statuses[2] = {SILENCED_STATUS}
    assert engine._pack_sealed([1, 2]) is True
    assert engine._pack_sealed([1]) is False
    del state._pipeline_runtime
    assert engine._pack_sealed([1, 2]) is False

def test_the_nightmare_declares_its_opening_line() -> None:
    """它排在所有夜间角色之前，所以这一句是当晚的第一句开眼旁白。"""
    assert NIGHTMARE_SPEC.opening_narration == ("噩梦之影请睁眼", "请选择今晚要恐惧的玩家。")
    assert NIGHTMARE_FEAR_CONTRACT.order == 10



def test_the_fear_forgets_a_night_it_spent_passing() -> None:
    """「不能连续两晚」means consecutive nights, not consecutive choices.

    A night the Nightmare passed (or whose call failed, or on which it was
    silenced) breaks the chain: the same seat afterwards is a legal fear, not a
    violation that would burn a second night on the blind retry.
    """
    consecutive = context(last_feared=3, round_number=4, memory_round=3)
    assert [item.code for item in validate_nightmare_action(
        consecutive, command(target=3),
    )] == ["consecutive_fear_forbidden"]

    after_a_skipped_night = context(last_feared=3, round_number=4, memory_round=2)
    assert validate_nightmare_action(after_a_skipped_night, command(target=3)) == ()
