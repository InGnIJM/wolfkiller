"""Wolf Witch: the mirrored half of the 纯白夜影 board.

Its two defining properties are structural rather than behavioural: it shares
the pack's kill contract byte for byte (so the whole camp still votes through
one aggregate), and its own check is a plain identity lookup whose only sentence
is the Pure White. The pair also pins the mutual check: both damages land in the
same night's settlement, so the two checkers kill each other.
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, _Runtime
from app.core.role_runtime import initialize_role_resources
from app.core.wolf_pack import in_wolf_pack
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.pure_white import PURE_WHITE_SPEC
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, WEREWOLF_SPEC
from app.roles.witch import WITCH_SPEC
from app.roles.wolf_witch import (
    PURE_WHITE_ROLE_ID, WOLF_WITCH_CHECK_CONTRACT, WOLF_WITCH_SPEC, WolfWitch,
    resolve_wolf_witch_action, validate_wolf_witch_action, wolf_witch_applicable,
)

WOOD = "wolf-killer-wolf-witch"
WOLF = "wolf-killer-werewolf"
VILLAGER = "wolf-killer-villager"
EVENT_ID = "event:" + "5" * 16


def wolf_witch_registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, WOLF_WITCH_SPEC, VILLAGER_SPEC, PURE_WHITE_SPEC, WITCH_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def context(
    *, target: int | None = 3, identity: str | None = PURE_WHITE_ROLE_ID,
    members: tuple[int, ...] = (1, 2), actor: int = 1, round_number: int = 2,
    with_fact: bool = True, action_type: str = "check",
) -> ActionContext:
    contract = WOLF_WITCH_CHECK_CONTRACT
    facts: dict[str, object] = {
        "alive_seats": (1, 2, 3, 4), "camp_members": members,
    }
    if with_fact and target is not None:
        selected: dict[str, object] = {"seat": target}
        if identity is not None:
            selected["role_label"] = identity
        facts["selected_target"] = selected
    return ActionContext(
        "g", 4, facts, config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=WOOD, actor_alive=True, action_key="witch:1",
        source_event_id=EVENT_ID,
    )


def command(action_type: str = "check", target: int | None = 3) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="ok")


def kinds(effects) -> list[str]:
    return [effect.kind.value for effect in effects]


def test_spec_shares_the_kill_contract_and_stays_in_the_pack() -> None:
    assert WOLF_WITCH_SPEC.camp_id == "werewolf"
    assert WOLF_WITCH_SPEC.tags == frozenset({"wolf"})
    assert WOLF_WITCH_SPEC.max_count == 1
    assert WOLF_WITCH_SPEC.contracts == (
        WEREWOLF_KILL_CONTRACT, WOLF_WITCH_CHECK_CONTRACT,
    )
    # Byte-identical, or the registry would reject the duplicate contract id.
    assert WOLF_WITCH_SPEC.contracts[0].to_json() == WEREWOLF_SPEC.contracts[0].to_json()
    assert WOLF_WITCH_CHECK_CONTRACT.schedule_point is SchedulePoint.NIGHT_SEER_ACTION
    assert WOLF_WITCH_CHECK_CONTRACT.order == 40
    assert WOLF_WITCH_CHECK_CONTRACT.visibility_namespaces == frozenset(
        {"PUBLIC", "ACTOR", "CAMP"}
    )
    assert PURE_WHITE_ROLE_ID == PURE_WHITE_SPEC.role_id

    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, WOOD, "werewolf"),
            2: PlayerState(2, WOLF, "werewolf"),
            3: PlayerState(3, VILLAGER, "good"),
        },
    )
    state._pipeline_runtime = _Runtime()
    initialize_role_resources(state, wolf_witch_registry().specs, "a" * 64)
    assert in_wolf_pack(state, 1) is True
    assert in_wolf_pack(state, 2) is True


def test_applicable_is_just_liveness() -> None:
    assert wolf_witch_applicable(context()) is True


def test_validate_forbids_self_and_teammates() -> None:
    assert validate_wolf_witch_action(context(target=3), command(target=3)) == ()
    assert [item.code for item in validate_wolf_witch_action(context(), command(target=1))] == [
        "self_check_forbidden",
    ]
    assert [
        item.code for item in validate_wolf_witch_action(context(), command(target=2))
    ] == ["teammate_check_forbidden"]
    # A context without the roster only loses the teammate rule, never the call.
    assert validate_wolf_witch_action(
        context(members=(), target=2), command(target=2),
    ) == ()
    assert validate_wolf_witch_action(context(), command("pass", None)) == ()


def test_checking_excludes_teammates_but_keeps_non_wolves() -> None:
    """「除狼人阵营外」只能靠 CAMP 名册判定：名册里的座位一律拒绝。"""
    members = (1, 2)
    for seat in (3, 4):
        assert validate_wolf_witch_action(context(members=members), command(target=seat)) == ()
    for seat in members:
        assert validate_wolf_witch_action(
            context(members=members), command(target=seat),
        ) != ()


def test_a_check_records_the_exact_identity_privately() -> None:
    effects = resolve_wolf_witch_action(context(), command())
    assert kinds(effects) == ["record_private_fact", "submit_damage", "emit_event"]
    assert effects[0].payload == {
        "target": 1, "namespace": "role_checks",
        "fact": {"target": 3, "role": PURE_WHITE_ROLE_ID},
    }
    assert effects[0].visibility == ("ACTOR",)
    assert effects[1].payload == {"target": 3, "amount": 1, "cause": "verify_kill"}
    assert effects[2].payload["event_type"] == "WOLF_WITCH_REASONING"
    assert effects[2].visibility == ("PUBLIC",)


def test_only_the_pure_white_is_sentenced_and_only_from_the_second_night() -> None:
    first_night = resolve_wolf_witch_action(context(round_number=1), command())
    assert "submit_damage" not in kinds(first_night)
    other = resolve_wolf_witch_action(context(identity=VILLAGER), command())
    assert "submit_damage" not in kinds(other)
    innocent = resolve_wolf_witch_action(
        context(identity="wolf-killer-seer"), command(),
    )
    assert "submit_damage" not in kinds(innocent)


def test_passing_only_records_a_reason() -> None:
    effects = resolve_wolf_witch_action(context(target=None), command("pass", None))
    assert kinds(effects) == ["emit_event"]
    assert "不查验" in effects[0].payload["payload"]["thought"]


def test_missing_facts_are_programming_errors() -> None:
    with pytest.raises(ValueError):
        resolve_wolf_witch_action(context(with_fact=False), command())
    with pytest.raises(TypeError):
        resolve_wolf_witch_action(context(identity=None), command())


def test_mutual_checks_kill_both_sides_in_one_settlement() -> None:
    """同夜互验：两笔 verify_kill 同批结算，纯白之女与狼巫同归于尽。"""
    from app.core.scheduler import Scheduler

    registry = wolf_witch_registry()
    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=2,
        players={
            1: PlayerState(1, WOOD, "werewolf"),
            2: PlayerState(2, PURE_WHITE_ROLE_ID, "good"),
        },
    )

    def provider(request, ctx, attempt):
        target = 2 if request.actor_seat == 1 else 1
        return ActionCommand(action_type="check", target_seat=target, reasoning="互验")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    assert sorted(
        (item["target"], item["cause"]) for item in state._pipeline_runtime.pending_damage
    ) == [(1, "verify_kill"), (2, "verify_kill")]
    settlement = EffectApplier().settle_pending(state, round_number=2)
    assert settlement is not None
    assert sorted(
        (death.player_seat, death.cause) for death in state.death_history
    ) == [(1, "verify_kill"), (2, "verify_kill")]
    assert state.players[1].is_alive is False
    assert state.players[2].is_alive is False


def test_resolver_accepts_the_whole_batch() -> None:
    registry = wolf_witch_registry()
    resolved = ActionResolver().resolve_effects(
        context(), registry.require(WOOD), WOLF_WITCH_CHECK_CONTRACT, command(),
    )
    assert resolved[0].kind is EffectKind.ACCEPT_ACTION
    assert resolved[1:] == resolve_wolf_witch_action(context(), command())


def test_registered_on_both_sides() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(WOOD) is WOLF_WITCH_SPEC
    assert builtin_registry.require(WOOD).role_factory is WolfWitch
    assert snapshot.require(WOLF) is WEREWOLF_SPEC


def test_the_scheduler_issues_the_check_and_respects_the_roster() -> None:
    """真调度器：登记请求、跑契约、把 identity 写进 role_checks。"""
    from app.core.scheduler import Scheduler

    registry = wolf_witch_registry()
    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=2,
        players={
            1: PlayerState(1, WOOD, "werewolf"),
            2: PlayerState(2, WOLF, "werewolf"),
            3: PlayerState(3, PURE_WHITE_ROLE_ID, "good"),
        },
    )
    picks: list[int | None] = []

    def provider(request, ctx, attempt):
        if request.contract.contract_id != "wolf_witch_check":
            return ActionCommand(action_type="pass", target_seat=None, reasoning="wolf kill")
        picks.append(3)
        return ActionCommand(action_type="check", target_seat=3, reasoning="验纯白")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    runtime = state._pipeline_runtime
    assert picks == [3]
    assert runtime.pending_damage == ({"target": 3, "amount": 1, "cause": "verify_kill"},)
    assert [
        fact["fact"] for fact in runtime.private_facts[1]
    ] == [{"target": 3, "role": PURE_WHITE_ROLE_ID}]
    settlement = EffectApplier().settle_pending(state, round_number=2)
    assert settlement is not None
    assert state.players[3].is_alive is False
