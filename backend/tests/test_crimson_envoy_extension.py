"""Crimson Envoy: self-destruct the day, seal the good camp's night.

Two cards in one role. The self-destruct is the White Wolf King's window
without a passenger, and the seal it applies is the generic ``silenced`` status
— which is why this file proves the *engine* behaviour too: the feared/sealed
seats lose their model calls, and the second half of the card (surviving its own
exile as the last wolf) reuses the exile-verdict window the Idiot introduced.
"""

from __future__ import annotations

import tempfile

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, _Runtime
from app.core.night_settlement import (
    DELAYED_DEATH_STATUS, LINGERING_EXILE_STATUS, SILENCED_STATUS,
    resolve_delayed_deaths,
)
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.models.game import Camp, GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, GameEffect, SchedulePoint,
)
from app.roles.crimson_envoy import (
    CRIMSON_EXPLODE_CONTRACT, CRIMSON_LINGER_CONTRACT, CRIMSON_ENVOY_SPEC,
    EXPLODE_RESOURCE, CrimsonEnvoy, LINGER_RESOURCE, crimson_envoy_applicable,
    crimson_linger_applicable, react_crimson_linger, resolve_crimson_envoy_action,
    validate_crimson_envoy_action,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, WEREWOLF_SPEC

ENVOY = "wolf-killer-crimson-envoy"
WOLF = "wolf-killer-werewolf"
VILLAGER = "wolf-killer-villager"
SEER = "wolf-killer-seer"
EVENT_ID = "event:" + "7" * 16


def envoy_registry() -> RegistrySnapshot:
    from app.roles.guard import GUARD_SPEC
    from app.roles.seer import SEER_SPEC

    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, CRIMSON_ENVOY_SPEC, VILLAGER_SPEC,
                 SEER_SPEC, GUARD_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def context(
    *, actor: int = 1, alive: bool = True, alive_seats: tuple[int, ...] = (1, 2, 3, 4),
    members: tuple[int, ...] = (1, 2), resources: dict[str, int] | None = None,
    trigger_seat: int | None = None, trigger_reason: str | None = None,
    with_source: bool = False, round_number: int = 1,
) -> ActionContext:
    contract = (
        CRIMSON_LINGER_CONTRACT if trigger_seat is not None else CRIMSON_EXPLODE_CONTRACT
    )
    facts: dict[str, object] = {
        "alive_seats": alive_seats, "camp_members": members,
    }
    trigger = None
    if trigger_seat is not None:
        trigger = {
            "event_id": EVENT_ID, "type": "EXILE_PENDING", "target_seat": trigger_seat,
        }
    return ActionContext(
        "g", 3, facts, config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(),
        round_number=round_number, phase="speech", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=ENVOY, actor_alive=alive,
        resources=(
            resources if resources is not None
            else {EXPLODE_RESOURCE: 1, LINGER_RESOURCE: 1}
        ),
        action_key="envoy:1",
        source_event_id=EVENT_ID if with_source else None,
        trigger_event=trigger, trigger_reason=trigger_reason,
    )


def command(action_type: str = "explode", target: int | None = None) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="炸")


def kinds(effects) -> list[str]:
    return [effect.kind.value for effect in effects]


def test_spec_shares_the_kill_contract_and_declares_both_cards() -> None:
    assert CRIMSON_ENVOY_SPEC.camp_id == "werewolf"
    assert CRIMSON_ENVOY_SPEC.tags == frozenset({"wolf"})
    assert CRIMSON_ENVOY_SPEC.max_count == 1
    assert CRIMSON_ENVOY_SPEC.contracts[0].to_json() == WEREWOLF_SPEC.contracts[0].to_json()
    assert CRIMSON_ENVOY_SPEC.initial_resources == {
        EXPLODE_RESOURCE: 1, LINGER_RESOURCE: 1,
    }
    assert CRIMSON_EXPLODE_CONTRACT.schedule_point is SchedulePoint.DAY_ACTION
    assert CRIMSON_EXPLODE_CONTRACT.actions_requiring_target == frozenset()
    assert CRIMSON_LINGER_CONTRACT.schedule_point is SchedulePoint.EXILE_VERDICT
    assert CRIMSON_LINGER_CONTRACT.response_reasons == frozenset({"exile"})


def test_the_explosion_is_offered_once_and_only_while_alive() -> None:
    assert crimson_envoy_applicable(context()) is True
    assert crimson_envoy_applicable(context(alive=False)) is False
    assert crimson_envoy_applicable(
        context(resources={EXPLODE_RESOURCE: 0})
    ) is False
    assert [
        item.code for item in validate_crimson_envoy_action(
            context(resources={EXPLODE_RESOURCE: 0}), command(),
        )
    ] == ["explode_unavailable"]
    assert validate_crimson_envoy_action(context(), command("pass", None)) == ()


def test_exploding_takes_nobody_along_and_seals_every_living_good_seat() -> None:
    effects = resolve_crimson_envoy_action(context(), command())
    assert kinds(effects) == [
        "consume_resource", "submit_damage",
        "add_status", "add_status",
        "emit_event", "emit_event",
        "emit_event", "emit_event",
        "emit_event",
    ]
    assert effects[0].payload == {
        "target": 1, "resource": EXPLODE_RESOURCE, "amount": 1,
    }
    assert effects[1].payload == {"target": 1, "amount": 1, "cause": "self_explode"}
    sealed = [effect for effect in effects if effect.kind is EffectKind.ADD_STATUS]
    assert [effect.target_seat for effect in sealed] == [3, 4]
    assert all(
        effect.payload == {"target": effect.target_seat, "status": SILENCED_STATUS}
        for effect in sealed
    )
    assert all(
        effect.preconditions == {
            "status_present": {"status": SILENCED_STATUS, "present": False},
        }
        for effect in sealed
    )
    status_events = [
        effect for effect in effects
        if effect.payload.get("event_type") == "STATUS_ADDED"
    ]
    assert [effect.payload["payload"]["seat"] for effect in status_events] == [3, 4]
    explode = next(
        effect for effect in effects
        if effect.payload.get("event_type") == "CRIMSON_EXPLODE"
    )
    # Frozen payloads turn the list into a tuple; the wire projection keeps it
    # as an array either way.
    assert explode.payload["payload"] == {
        "seat": 1, "silenced_seats": (3, 4), "round_number": 1,
    }
    interrupted = next(
        effect for effect in effects
        if effect.payload.get("event_type") == "DAY_INTERRUPTED"
    )
    assert interrupted.payload["payload"] == {
        "seat": 1, "cause": "self_explode", "round_number": 1,
    }


def test_a_dead_teammate_is_not_sealed_and_a_living_one_is_not_either() -> None:
    """名册过滤的是阵营，存活名单过滤的是生死：队友一律不封印。"""
    effects = resolve_crimson_envoy_action(
        context(alive_seats=(1, 2, 3), members=(1, 2, 5)), command(),
    )
    assert [effect.target_seat for effect in effects
            if effect.kind is EffectKind.ADD_STATUS] == [3]


def test_passing_only_records_a_reason() -> None:
    effects = resolve_crimson_envoy_action(context(), command("pass", None))
    assert kinds(effects) == ["emit_event"]
    assert effects[0].payload["event_type"] == "CRIMSON_ENVOY_REASONING"
    assert "暂不自爆" in effects[0].payload["payload"]["thought"]


def test_malformed_rosters_are_programming_errors() -> None:
    from dataclasses import replace as _replace

    broken = context()
    broken = _replace(broken, facts={**broken.facts, "camp_members": "not a roster"})
    with pytest.raises(TypeError):
        resolve_crimson_envoy_action(broken, command())
    # A missing roster (rather than a malformed one) seals nobody instead of
    # raising: the projection is allowed to omit it.
    empty = _replace(context(), facts={})
    effects = resolve_crimson_envoy_action(empty, command())
    assert [effect for effect in effects if effect.kind is EffectKind.ADD_STATUS] == []


def test_the_linger_only_fires_for_the_last_wolf_exiled_tonight() -> None:
    alone = context(
        trigger_seat=1, trigger_reason="exile", with_source=True,
        members=(1, 2), alive_seats=(1, 3, 4),
    )
    assert crimson_linger_applicable(alone) is True
    # A living teammate keeps the card silent.
    not_last = context(
        trigger_seat=1, trigger_reason="exile", with_source=True,
        members=(1, 2), alive_seats=(1, 2, 3),
    )
    assert crimson_linger_applicable(not_last) is False
    # Someone else's exile, an unrelated reason, a spent card and a missing
    # source event each keep it silent too.
    assert crimson_linger_applicable(
        context(trigger_seat=3, trigger_reason="exile", with_source=True,
                members=(1, 2), alive_seats=(1, 3, 4))
    ) is False
    assert crimson_linger_applicable(
        context(trigger_seat=1, trigger_reason="wolf_kill", with_source=True,
                members=(1,), alive_seats=(1,))
    ) is False
    assert crimson_linger_applicable(
        context(trigger_seat=1, trigger_reason="exile", with_source=False,
                members=(1,), alive_seats=(1,))
    ) is False
    assert crimson_linger_applicable(
        context(trigger_seat=1, trigger_reason="exile", with_source=True,
                members=(1,), alive_seats=(1,),
                resources={LINGER_RESOURCE: 0})
    ) is False


def test_a_malformed_roster_keeps_the_linger_silent() -> None:
    """名册不是序列时只当作「不是最后一个狼」，绝不能抛给响应窗口。"""
    from dataclasses import replace

    broken = replace(
        context(trigger_seat=1, trigger_reason="exile", with_source=True,
                members=(1,), alive_seats=(1,)),
        facts={"camp_members": "nope", "alive_seats": (1,)},
    )
    assert crimson_linger_applicable(broken) is False
    assert react_crimson_linger(broken) == ()




def test_the_linger_survives_the_exile_and_schedules_the_death() -> None:
    effects = react_crimson_linger(context(
        trigger_seat=1, trigger_reason="exile", with_source=True,
        members=(1,), alive_seats=(1, 3, 4),
    ))
    assert kinds(effects) == ["consume_resource", "add_status", "add_status", "emit_event"]
    assert effects[0].payload == {"target": 1, "resource": LINGER_RESOURCE, "amount": 1}
    assert effects[1].payload == {"target": 1, "status": DELAYED_DEATH_STATUS}
    assert effects[2].payload == {"target": 1, "status": LINGERING_EXILE_STATUS}
    assert effects[3].payload["event_type"] == "EXILE_CANCELLED"
    assert effects[3].payload["payload"] == {"target_seat": 1, "round_number": 1}
    # A spent card stays silent; response windows do not consult applicability.
    assert react_crimson_linger(context(
        trigger_seat=1, trigger_reason="exile", with_source=True,
        members=(1,), alive_seats=(1, 3, 4), resources={LINGER_RESOURCE: 0},
    )) == ()


def test_a_lingering_exile_dies_of_exile_after_the_next_dawn() -> None:
    deaths, cleared = resolve_delayed_deaths(
        {1: {DELAYED_DEATH_STATUS, LINGERING_EXILE_STATUS}}, 3,
    )
    assert deaths == ({"seat": 1, "cause": "exile", "round_number": 3},)
    assert cleared == {1: (DELAYED_DEATH_STATUS, LINGERING_EXILE_STATUS)}


def test_registered_on_both_sides() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(ENVOY) is CRIMSON_ENVOY_SPEC
    assert builtin_registry.require(ENVOY).role_factory is CrimsonEnvoy
    assert snapshot.require("wolf-killer-werewolf-king") is not CRIMSON_ENVOY_SPEC
    assert WEREWOLF_KILL_CONTRACT.to_json() == CRIMSON_ENVOY_SPEC.contracts[0].to_json()


def test_the_explosion_lands_through_the_scheduler_and_seals_the_night() -> None:
    """端到端：自爆当批消耗资源 + 自伤 + 封印全部存活好人 + 中断当日。"""
    registry = envoy_registry()
    state = GameState(
        "g", config=GameConfig(role_counts={ENVOY: 1}), phase=GamePhase.SPEECH,
        round_number=1,
        players={
            1: PlayerState(1, ENVOY, Camp.WEREWOLF),
            2: PlayerState(2, WOLF, Camp.WEREWOLF),
            3: PlayerState(3, SEER, Camp.GOOD),
            4: PlayerState(4, VILLAGER, Camp.GOOD),
        },
    )
    state._pipeline_runtime = _Runtime()
    initialize_role_resources(state, registry.specs, registry.digest)

    def provider(request, ctx, attempt):
        return ActionCommand(action_type="explode", target_seat=None, reasoning="炸")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    result = scheduler.run_point(state, SchedulePoint.DAY_ACTION, slot="r1-s1")
    runtime = state._pipeline_runtime
    assert runtime.role_resources[1][EXPLODE_RESOURCE] == 0
    assert runtime.pending_damage == ({"target": 1, "amount": 1, "cause": "self_explode"},)
    assert runtime.statuses[3] == {SILENCED_STATUS}
    assert runtime.statuses[4] == {SILENCED_STATUS}
    assert runtime.statuses.get(2, set()) == set()
    types = [event["event_type"] for event in result.events]
    assert types.count("STATUS_ADDED") == 2
    assert "CRIMSON_EXPLODE" in types
    assert "DAY_INTERRUPTED" in types

    # A second window offers nothing: the resource is spent.
    assert crimson_envoy_applicable(
        ContextProjector().project(
            state,
            _request(registry, "crimson_explode"),
            registry,
        )
    ) is False


def _request(registry: RegistrySnapshot, contract_id: str):
    from app.models.pipeline import IssuedActionRequest

    contract = next(
        item for item in registry.require(ENVOY).contracts
        if item.contract_id == contract_id
    )
    return IssuedActionRequest(1, ENVOY, contract, 0, 1, "speech", "w", "k")


def test_the_lingering_exile_dies_and_is_announced_after_the_next_dawn() -> None:
    """引擎侧：延迟出局在次日发言结束后落地，死因仍是 exile。

    赤月使徒是最后一个狼时，被放逐当夜由 EXILE_VERDICT 上的 react 契约发
    EXILE_CANCELLED（白痴的通用窗口），只落下两个状态；真正的死亡在这里。
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.core.game_engine import GameEngine

    with tempfile.TemporaryDirectory() as data_dir:
        engine = GameEngine(
            "envoy", pipeline_scheduler=None, director=MagicMock(), data_dir=data_dir,
        )
        engine.state.players = {
            1: PlayerState(1, ENVOY, Camp.WEREWOLF),
            3: PlayerState(3, SEER, Camp.GOOD),
            4: PlayerState(4, VILLAGER, Camp.GOOD),
        }
        engine.memory_service = MagicMock()
        engine._publish_deaths = AsyncMock()
        state = engine.state
        state._pipeline_runtime = _Runtime()
        state._pipeline_runtime.statuses[1] = {
            DELAYED_DEATH_STATUS, LINGERING_EXILE_STATUS,
        }
        reports = asyncio.run(engine._resolve_delayed_deaths())

    assert [(item.player_seat, item.cause) for item in reports] == [(1, "exile")]
    assert engine.state.players[1].is_alive is False
    assert fig_events_published(engine) is True


def fig_events_published(engine) -> bool:
    """The delayed death reached the bus through ``_publish_deaths``."""
    return engine._publish_deaths.await_count == 1


def test_the_seal_survives_into_the_night_and_blocks_the_good_seats() -> None:
    """封印的完整链路：自爆落状态 → 当晚好人契约不再被发放命令。"""
    from app.roles.guard import GUARD_SPEC

    registry = envoy_registry()
    state = GameState(
        "g", config=GameConfig(role_counts={ENVOY: 1}), phase=GamePhase.NIGHT,
        round_number=2,
        players={
            1: PlayerState(1, ENVOY, Camp.WEREWOLF),
            3: PlayerState(3, SEER, Camp.GOOD),
            5: PlayerState(5, "wolf-killer-guard", Camp.GOOD),
        },
    )
    applier = EffectApplier()
    state._pipeline_runtime = _Runtime()
    initialize_role_resources(state, registry.specs, registry.digest)
    state._pipeline_runtime.statuses[3] = {SILENCED_STATUS}
    state._pipeline_runtime.statuses[5] = {SILENCED_STATUS}
    calls: list[str] = []

    def provider(request, ctx, attempt):
        calls.append(request.contract.contract_id)
        return ActionCommand(action_type="check", target_seat=1, reasoning="验")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        applier, provider,
    )
    scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)
    assert calls == []
    scheduler.run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    assert calls == []
    assert GUARD_SPEC.role_id == "wolf-killer-guard"
    assert state._pipeline_runtime.statuses[3] == {SILENCED_STATUS}
