"""Dancer: the three-seat command, the once-per-game roster and the veil.

The verdict itself is settled in ``test_night_settlement`` (and through the
applier's ``commit_night``) — here the role side is pinned: what the hook may
declare, which seats are legal, and what the pool shield does and does not do.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, EffectRejected, _Runtime
from app.core.night_settlement import (
    DANCE_POOL_STATUS, POISON_IMMUNE_RESOURCE, SILENCED_STATUS, settle_pool,
)
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import Scheduler
from app.models.game import Camp, GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.dancer import (
    DANCE_POOL_CONTRACT, DANCE_VEIL_SOURCE, DANCER_SPEC, POOLED_KEY, Dancer,
    dancer_applicable, resolve_dance_pool, validate_dance_pool,
)
from app.roles.guard import GUARD_SPEC
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC

DANCER = "wolf-killer-dancer"
VILLAGER = "wolf-killer-villager"
WOLF = "wolf-killer-werewolf"
EVENT_ID = "event:" + "9" * 16


def dancer_registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, DANCER_SPEC, GUARD_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def context(
    *, actor: int = 1, alive: bool = True, alive_seats: tuple[int, ...] = (1, 2, 3, 4, 5),
    pooled: tuple[int, ...] = (), round_number: int = 2,
) -> ActionContext:
    contract = DANCE_POOL_CONTRACT
    return ActionContext(
        "g", 9, {"alive_seats": alive_seats, POOLED_KEY: pooled},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=DANCER, actor_alive=alive,
        resources={POISON_IMMUNE_RESOURCE: 1}, action_key="dance:1",
        source_event_id=EVENT_ID,
    )


def command(seats: tuple[int, ...] | None = (2, 3, 4), *, action: str = "pool"):
    return ActionCommand(
        action_type=action, target_seat=None,
        target_seats=None if seats is None else seats, reasoning="共舞",
    )


def kinds(effects) -> list[str]:
    return [effect.kind.value for effect in effects]


def test_spec_declares_three_targets_and_the_vial_immunity() -> None:
    assert DANCER_SPEC.camp_id == "good"
    assert DANCER_SPEC.tags == frozenset({"god"})
    assert DANCER_SPEC.max_count == 1
    assert DANCER_SPEC.initial_resources == {POISON_IMMUNE_RESOURCE: 1}
    assert DANCER_SPEC.initial_private_data == {POOLED_KEY: ()}
    assert DANCE_POOL_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert DANCE_POOL_CONTRACT.order == 20
    assert DANCE_POOL_CONTRACT.multi_target_actions == {"pool": 3}
    assert DANCE_POOL_CONTRACT.actions_requiring_target == frozenset()
    # The fallback has to be a no-target action, or the registry refuses it.
    assert DANCE_POOL_CONTRACT.fallback_action_type == "pass"
    assert GUARD_SPEC.contracts[0].order == 50


def test_the_dance_starts_on_the_second_night_and_needs_three_free_seats() -> None:
    assert dancer_applicable(context(round_number=1)) is False
    assert dancer_applicable(context()) is True
    assert dancer_applicable(context(alive=False)) is False
    # Three living seats left is enough; two is not.
    assert dancer_applicable(context(alive_seats=(1, 2, 3))) is True
    assert dancer_applicable(context(alive_seats=(1, 2))) is False
    # Seats that already danced drop out of the eligible list, so a shrinking
    # table runs out of partners and the skill retires itself.
    assert dancer_applicable(context(alive_seats=(1, 2, 3, 4, 5), pooled=(2, 3))) is True
    assert dancer_applicable(context(alive_seats=(1, 2, 3, 4), pooled=(2, 3))) is False
    assert dancer_applicable(context(alive_seats=(1, 2, 3, 4), pooled=(2, 3, 4))) is False


def test_a_seat_can_only_join_one_pool_per_game() -> None:
    assert validate_dance_pool(context(pooled=(5,)), command((2, 3, 4))) == ()
    violations = validate_dance_pool(context(pooled=(3,)), command((2, 3, 4)))
    assert [item.code for item in violations] == ["pool_repeat_forbidden"]
    assert validate_dance_pool(context(pooled=(3,)), command(None, action="pass")) == ()


def test_the_generic_validator_enforces_the_count_and_liveness() -> None:
    registry = dancer_registry()
    contract = DANCE_POOL_CONTRACT
    assert ActionValidator().validate(context(), contract, command((2, 3, 4))) == ()
    codes = lambda cmd: [  # noqa: E731 - compact table of expected codes
        item.code for item in ActionValidator().validate(context(), contract, cmd)
    ]
    assert "target_count_invalid" in codes(command((2, 3)))
    assert "duplicate_targets" in codes(command((2, 2, 4)))
    assert "target_not_alive" in codes(command((2, 3, 99)))
    assert "target_forbidden" in codes(
        ActionCommand(
            action_type="pool", target_seat=2, target_seats=(2, 3, 4), reasoning="both",
        )
    )
    assert "multi_target_forbidden" in codes(
        ActionCommand(
            action_type="pass", target_seat=None, target_seats=(2, 3, 4), reasoning="bad",
        )
    )


def test_resolve_marks_the_pool_records_it_and_publishes_the_event() -> None:
    effects = resolve_dance_pool(context(pooled=(5,)), command((2, 3, 4)))
    assert kinds(effects) == [
        "add_status", "add_status", "add_status",
        "set_private_data", "emit_event", "emit_event",
    ]
    assert [effect.target_seat for effect in effects[:3]] == [2, 3, 4]
    assert all(
        effect.payload == {"target": effect.target_seat, "status": DANCE_POOL_STATUS}
        for effect in effects[:3]
    )
    # Frozen payloads turn the list into a tuple; the private data keeps it.
    assert effects[3].payload == {
        "target": 1, "key": POOLED_KEY, "value": (5, 2, 3, 4),
    }
    assert dict(effects[4].payload) == {
        "event_type": "DANCE_POOL",
        "payload": {"seat": 1, "pool_seats": (2, 3, 4), "round_number": 2},
    }
    assert effects[5].payload["event_type"] == "DANCER_REASONING"
    # The dancer stayed out of the pool, so nobody is shielded.
    assert "submit_protection" not in kinds(effects)


def test_joining_the_pool_shields_all_three_from_the_knife() -> None:
    effects = resolve_dance_pool(context(), command((1, 2, 3)))
    veils = [
        effect for effect in effects if effect.kind is EffectKind.SUBMIT_PROTECTION
    ]
    assert [effect.target_seat for effect in veils] == [1, 2, 3]
    assert all(
        effect.payload == {"target": effect.target_seat, "amount": 1,
                           "source": DANCE_VEIL_SOURCE}
        for effect in veils
    )
    assert "三人免疫狼刀" in effects[-1].payload["payload"]["thought"]


def test_a_malformed_alive_list_is_a_programming_error() -> None:
    from dataclasses import replace

    broken = replace(context(), facts={"alive_seats": "nope", "pooled_seats": ()})
    with pytest.raises(TypeError):
        dancer_applicable(broken)


def test_the_daytime_dancer_reports_no_skill() -> None:
    assert object.__new__(Dancer).get_skills() == []


def test_passing_only_records_a_reason() -> None:
    effects = resolve_dance_pool(context(), command(None, action="pass"))
    assert kinds(effects) == ["emit_event"]
    assert "不发动起舞" in effects[0].payload["payload"]["thought"]


def test_malformed_private_data_is_a_programming_error() -> None:
    from dataclasses import replace

    broken = replace(context(), facts={"alive_seats": (1, 2, 3), POOLED_KEY: "nope"})
    with pytest.raises(TypeError):
        resolve_dance_pool(broken, command((1, 2, 3)))
    from app.core.night_settlement import settle

    assert settle is not None and SILENCED_STATUS  # keep the import list honest


def test_resolver_and_registry_accept_the_role() -> None:
    registry = dancer_registry()
    resolved = ActionResolver().resolve_effects(
        context(), registry.require(DANCER), DANCE_POOL_CONTRACT, command(),
    )
    assert resolved[0].kind is EffectKind.ACCEPT_ACTION
    assert resolved[1:] == resolve_dance_pool(context(), command())
    snapshot = builtin_registry.freeze()
    assert snapshot.require(DANCER) is DANCER_SPEC
    assert builtin_registry.require(DANCER).role_factory is Dancer


def test_the_scheduler_runs_one_three_target_command_end_to_end() -> None:
    """端到端：真调度器把 3 个目标写进目标座位状态与私有数据，并在当晚结算出局。"""
    registry = dancer_registry()
    state = GameState(
        "g", config=GameConfig(role_counts={DANCER: 1}), phase=GamePhase.NIGHT,
        round_number=2,
        players={
            1: PlayerState(1, DANCER, Camp.GOOD),
            2: PlayerState(2, VILLAGER, Camp.GOOD),
            3: PlayerState(3, VILLAGER, Camp.GOOD),
            4: PlayerState(4, WOLF, Camp.WEREWOLF),
        },
    )
    state._pipeline_runtime = _Runtime()

    def provider(request, context_, attempt):
        return ActionCommand(
            action_type="pool", target_seat=None, target_seats=(2, 3, 4),
            reasoning="两好一狼",
        )

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)

    runtime = state._pipeline_runtime
    assert runtime.private_data[1][POOLED_KEY] == (2, 3, 4)
    assert runtime.statuses[2] == {DANCE_POOL_STATUS}
    assert runtime.statuses[4] == {DANCE_POOL_STATUS}
    # No dancer in the pool, so no veil was submitted.
    assert runtime.pending_protection == ()

    # NIGHT_COMMIT is the point's own work item, so run it the way the engine
    # does: the pool verdict has to surface as a PLAYER_DIED row in the point's
    # events, because that list is what ``_pipeline_night_deaths`` walks to
    # announce the night's deaths.
    commit_result = scheduler.run_point(state, SchedulePoint.NIGHT_COMMIT)
    assert [
        dict(event["payload"]) for event in commit_result.events
        if event["event_type"] == "PLAYER_DIED"
    ] == [{"seat": 4, "cause": "dance", "round_number": 2}]
    assert [(death.player_seat, death.cause) for death in state.death_history] == [
        (4, "dance"),
    ]
    assert state.players[4].is_alive is False
    # The commit installs a *new* runtime, so re-read it before the sweep.
    swept = state._pipeline_runtime
    assert swept.statuses[2] == set()
    assert swept.statuses[4] == set()
    assert swept.pending_damage == ()


def test_a_dancer_in_the_pool_shields_its_members_through_the_write_boundary() -> None:
    registry = dancer_registry()
    state = GameState(
        "g", config=GameConfig(role_counts={DANCER: 1}), phase=GamePhase.NIGHT,
        round_number=2,
        players={
            1: PlayerState(1, DANCER, Camp.GOOD),
            2: PlayerState(2, VILLAGER, Camp.GOOD),
            3: PlayerState(3, WOLF, Camp.WEREWOLF),
        },
    )
    state._pipeline_runtime = _Runtime()
    initialize_role_resources(state, registry.specs, registry.digest)
    assert state._pipeline_runtime.role_resources[1][POISON_IMMUNE_RESOURCE] == 1

    def provider(request, context_, attempt):
        return ActionCommand(
            action_type="pool", target_seat=None, target_seats=(1, 2, 3),
            reasoning="自己参舞",
        )

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)
    runtime = state._pipeline_runtime
    assert runtime.pending_protection == (
        {"target": 1, "amount": 1, "source": DANCE_VEIL_SOURCE},
        {"target": 2, "amount": 1, "source": DANCE_VEIL_SOURCE},
        {"target": 3, "amount": 1, "source": DANCE_VEIL_SOURCE},
    )
    # 1 good + 1 good + 1 (masked-equivalent) wolf: the pool verdict would kill
    # the wolf, and the veil keeps the knife from doing anything on top.
    deaths, cleared = settle_pool(
        runtime.statuses,
        {1: "good", 2: "good", 3: "werewolf"},
        {seat: player.is_alive for seat, player in state.players.items()},
        2,
    )
    assert deaths == ({"seat": 3, "cause": "dance", "round_number": 2},)
    assert sorted(cleared) == [1, 2, 3]
    assert EffectRejected is not None


def mask_registry() -> RegistrySnapshot:
    from app.roles.mask import MASK_SPEC

    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, DANCER_SPEC, MASK_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def mask_command(action_type: str = "mask", target: int | None = 2) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="戴面具")


def mask_context(
    *, target: int | None = 2, in_pool: bool = True, last_masked: object = None,
    checks: tuple = (), round_number: int = 2, with_fact: bool = True,
) -> ActionContext:
    from app.roles.mask import MASK_GRANT_CONTRACT

    facts: dict[str, object] = {
        "alive_seats": (1, 2, 3, 4), "last_masked": last_masked,
        "pool_checks": checks,
    }
    if with_fact and target is not None:
        labels: list[str] = ["in_pool"] if in_pool else []
        facts["selected_target"] = {"seat": target, "status_labels": tuple(labels)}
    return ActionContext(
        "g", 11, facts, config_version="a" * 64,
        contract_id=MASK_GRANT_CONTRACT.contract_id,
        contract_version=MASK_GRANT_CONTRACT.schema_version,
        contract_digest=MASK_GRANT_CONTRACT.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=MASK_GRANT_CONTRACT.schedule_point, actor_seat=1,
        actor_role_id="wolf-killer-mask", actor_alive=True,
        resources={"poison_immune": 1}, action_key="mask:1",
        source_event_id="event:" + "a" * 16,
    )


def test_mask_spec_reads_the_pool_and_runs_after_the_dancer() -> None:
    from app.roles.mask import (
        LAST_MASKED_KEY, MASK_GRANT_CONTRACT, MASK_SPEC, POOL_CHECKS_KEY,
    )
    from app.core.night_settlement import POISON_IMMUNE_RESOURCE

    assert MASK_SPEC.camp_id == "werewolf"
    assert MASK_SPEC.tags == frozenset({"wolf"})
    assert MASK_SPEC.max_count == 1
    assert MASK_SPEC.initial_resources == {POISON_IMMUNE_RESOURCE: 1}
    assert MASK_SPEC.initial_private_data == {LAST_MASKED_KEY: None, POOL_CHECKS_KEY: ()}
    assert MASK_SPEC.contracts[0].to_json() == WEREWOLF_SPEC.contracts[0].to_json()
    assert MASK_GRANT_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert MASK_GRANT_CONTRACT.order == 30
    assert DANCE_POOL_CONTRACT.order == 20 < MASK_GRANT_CONTRACT.order
    assert MASK_GRANT_CONTRACT.selected_target_fact_namespaces == frozenset(
        {"status_labels"}
    )


def test_mask_starts_on_the_second_night_and_forbids_two_nights_in_a_row() -> None:
    from app.roles.mask import mask_applicable, validate_mask_action

    assert mask_applicable(mask_context(round_number=1)) is False
    assert mask_applicable(mask_context(round_number=2)) is True
    assert validate_mask_action(
        mask_context(last_masked=3), mask_command(target=2),
    ) == ()
    violations = validate_mask_action(mask_context(last_masked=2), mask_command(target=2))
    assert [item.code for item in violations] == ["consecutive_mask_forbidden"]
    assert validate_mask_action(
        mask_context(last_masked=2), mask_command("pass", None),
    ) == ()


def test_mask_learns_whether_its_target_is_dancing() -> None:
    from app.roles.mask import MASK_GRANT_CONTRACT, resolve_mask_action

    def masked(command_seats):
        from app.models.pipeline import ActionCommand

        return resolve_mask_action(
            mask_context(),
            ActionCommand(
                action_type="mask", target_seat=command_seats,
                target_seats=None, reasoning="戴面具",
            ),
        )

    inside = masked(2)
    assert [effect.kind.value for effect in inside] == [
        "set_private_data", "set_private_data", "add_status", "emit_event", "emit_event",
    ]
    assert inside[0].payload == {"target": 1, "key": "last_masked", "value": 2}
    # Frozen payloads: the appended record lands as a tuple of proxies.
    assert dict(inside[1].payload) == {
        "target": 1, "key": "pool_checks",
        "value": (MappingProxyType({"target": 2, "in_pool": True}),),
    }
    assert inside[2].payload == {"target": 2, "status": "masked"}
    assert inside[2].preconditions == {
        "status_present": {"status": "masked", "present": False},
    }
    assert dict(inside[3].payload) == {
        "event_type": "MASK_GRANTED",
        "payload": {"seat": 1, "target_seat": 2, "in_pool": True, "round_number": 2},
    }
    assert "正在舞池中" in inside[4].payload["payload"]["thought"]

    outside = resolve_mask_action(
        mask_context(in_pool=False, checks=({"target": 3, "in_pool": True},)),
        __import__("app.models.pipeline", fromlist=["ActionCommand"]).ActionCommand(
            action_type="mask", target_seat=2, reasoning="盲给",
        ),
    )
    assert outside[1].payload["value"] == (
        MappingProxyType({"target": 3, "in_pool": True}),
        MappingProxyType({"target": 2, "in_pool": False}),
    )
    assert dict(outside[3].payload)["payload"]["in_pool"] is False
    assert MASK_GRANT_CONTRACT.allowed_effects


def test_mask_passing_and_malformed_facts() -> None:
    from dataclasses import replace

    from app.models.pipeline import ActionCommand
    from app.roles.mask import resolve_mask_action

    passed = resolve_mask_action(
        mask_context(target=None),
        ActionCommand(action_type="pass", target_seat=None, reasoning="观望"),
    )
    assert [effect.kind.value for effect in passed] == ["emit_event"]
    assert "不发动面具" in passed[0].payload["payload"]["thought"]

    with pytest.raises(ValueError):
        resolve_mask_action(
            mask_context(with_fact=False),
            ActionCommand(action_type="mask", target_seat=2, reasoning="x"),
        )
    broken = replace(
        mask_context(),
        facts={"selected_target": {"seat": 2, "status_labels": 7},
               "pool_checks": (), "last_masked": None},
    )
    with pytest.raises(TypeError):
        resolve_mask_action(
            broken, ActionCommand(action_type="mask", target_seat=2, reasoning="x"),
        )
    broken_checks = replace(
        mask_context(),
        facts={"selected_target": {"seat": 2, "status_labels": ()},
               "pool_checks": 7, "last_masked": None},
    )
    with pytest.raises(TypeError):
        resolve_mask_action(
            broken_checks,
            ActionCommand(action_type="mask", target_seat=2, reasoning="x"),
        )


def test_the_mask_runs_after_the_pool_and_flips_its_verdict() -> None:
    """端到端：舞者(20) 先选池、假面(30) 再读池并戴面具，结算时少数方换人。"""
    from app.core.night_settlement import MASKED_STATUS
    from app.roles.mask import Mask

    registry = mask_registry()
    state = GameState(
        "g", config=GameConfig(role_counts={DANCER: 1}), phase=GamePhase.NIGHT,
        round_number=2,
        players={
            1: PlayerState(1, DANCER, Camp.GOOD),
            2: PlayerState(2, "wolf-killer-mask", Camp.WEREWOLF),
            3: PlayerState(3, VILLAGER, Camp.GOOD),
            4: PlayerState(4, WOLF, Camp.WEREWOLF),
        },
    )
    state._pipeline_runtime = _Runtime()
    calls: list[str] = []

    def provider(request, context_, attempt):
        calls.append(request.contract.contract_id)
        if request.contract.contract_id == "dance_pool":
            return ActionCommand(
                action_type="pool", target_seat=None, target_seats=(1, 2, 3),
                reasoning="两好一狼",
            )
        if request.contract.contract_id == "mask_grant":
            # Mask the wolf inside the pool so the comparison turns single-camp.
            return ActionCommand(
                action_type="mask", target_seat=2, reasoning="罩队友",
            )
        return ActionCommand(action_type="kill", target_seat=1, reasoning="刀")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(state, SchedulePoint.NIGHT_ACTION, slot="r2")

    runtime = state._pipeline_runtime
    assert calls == ["dance_pool", "mask_grant"]
    assert runtime.statuses[2] == {DANCE_POOL_STATUS, MASKED_STATUS}
    assert runtime.private_data[2]["pool_checks"] == (
        MappingProxyType({"target": 2, "in_pool": True}),
    )

    deaths, _ = settle_pool(
        runtime.statuses,
        {seat: (player.camp.value if hasattr(player.camp, "value") else player.camp)
         for seat, player in state.players.items()},
        {seat: player.is_alive for seat, player in state.players.items()},
        2,
    )
    # 1 good + (masked wolf counted good) + 1 good = single camp, nobody dies.
    assert deaths == ()
    assert object.__new__(Mask).get_skills() == ["kill", "mask"]

def test_the_dancer_and_the_mask_declare_their_opening_lines() -> None:
    from app.roles.mask import MASK_GRANT_CONTRACT, MASK_SPEC

    assert DANCER_SPEC.opening_narration == ("舞者请睁眼", "请邀请三名玩家共舞。")
    assert MASK_SPEC.opening_narration == ("假面请睁眼", "请选择今晚要戴面具的玩家。")
    assert DANCE_POOL_CONTRACT.order < MASK_GRANT_CONTRACT.order


