"""寻香识命: the pair check and the bond that drags the other player along.

Two roles, two very different shapes of "somebody else's seat matters":

* the **Awakened Seer** reads two seats at once and records one boolean — the
  multi-target command plus the plural selected-target projection;
* the **Fragrance Phantom** answers for a death that is not its own — the
  declared ``bond_slot`` is what makes the response queue wake her, which is the
  only way a react contract can fire for somebody else's elimination.
"""

from __future__ import annotations

import pytest

from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import (
    EffectApplier, EffectPermission, EffectRejected, _Runtime, derive_effect_id,
)
from app.core.night_settlement import settle
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import DomainEvent, ResponseQueue, Scheduler
from app.core.action_resolver import ActionResolver
from app.models.game import GamePhase, GameState, PlayerState
from app.models.values import DeathCause
from app.models.pipeline import (
    DEGRADED_REASONING, DEGRADED_THOUGHT, ActionCommand, ActionContext,
    ActionContract, EffectKind, GameEffect, SchedulePoint,
)
from app.roles.awakened_seer import (
    AWAKENED_SEER_SPEC, PAIR_CHECK_NAMESPACE, resolve_awakened_seer_action,
    validate_awakened_seer_action,
)
from app.roles.fragrance_phantom import (
    BOND_CAUSE, BOND_MARKER, BOND_SLOT, FRAGRANCE_PHANTOM_SPEC,
    react_fragrance_chain, resolve_fragrance_bond, validate_fragrance_bond,
)
from app.roles.gargoyle import GARGOYLE_SPEC
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC

SEER = "wolf-killer-awakened-seer"
PHANTOM = "wolf-killer-fragrance-phantom"
WOLF = "wolf-killer-werewolf"
VILLAGER = "wolf-killer-villager"


def pairs_registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, GARGOYLE_SPEC, AWAKENED_SEER_SPEC,
                 FRAGRANCE_PHANTOM_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def seer_context(
    *, actor: int = 4, alive: bool = True, rows: object = "auto",
    round_number: int = 1, phase: str = "night",
) -> ActionContext:
    contract = AWAKENED_SEER_SPEC.contracts[0]
    seats = (1, 2)
    if rows == "auto":
        rows = (
            {"seat": 1, "camp_label": "werewolf"},
            {"seat": 2, "camp_label": "good"},
        )
    facts: dict[str, object] = {"alive_seats": (1, 2, 3, 4)}
    if rows is not None:
        facts["selected_targets"] = rows
    return ActionContext(
        "g", 5, facts, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=round_number,
        phase=phase, window_id="w", schedule_point=contract.schedule_point,
        actor_seat=actor, actor_role_id=SEER, actor_alive=alive,
        action_key="pair:1",
    )


def seer_command(action: str = "check", seats: tuple[int, ...] | None = (1, 2)) -> ActionCommand:
    return ActionCommand(action_type=action, target_seats=seats, reasoning="看看")


def phantom_context(
    *, actor: int = 1, alive: bool = True, marker: int = 1,
    pair: object = (2, 3), trigger: int | None = None, cause: str = "wolf_kill",
    alive_seats: tuple[int, ...] = (1, 3),
) -> ActionContext:
    contract = next(
        item for item in FRAGRANCE_PHANTOM_SPEC.contracts
        if item.contract_id == "fragrance_chain"
    )
    facts: dict[str, object] = {
        "alive_seats": alive_seats,
        BOND_SLOT: pair,
        "actor_identity": {"seat": actor, "role_id": PHANTOM, "camp_id": "werewolf"},
    }
    return ActionContext(
        "g", 5, facts, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=2,
        phase="dawn", window_id="w", schedule_point=contract.schedule_point,
        actor_seat=actor, actor_role_id=PHANTOM, actor_alive=alive,
        resources={BOND_MARKER: marker}, action_key="chain:1",
        source_event_id="event:" + "1" * 16 if trigger is not None else None,
        trigger_event=None if trigger is None else {
            "event_id": "event:" + "1" * 16, "type": "PLAYER_DIED",
            "target_seat": trigger, "cause": cause, "round_number": 2, "phase": "dawn",
        },
        trigger_reason=cause,
    )


# ── 觉醒预言家 ────────────────────────────────────────────────────────────


def test_awakened_seer_declares_a_two_target_check() -> None:
    contract = AWAKENED_SEER_SPEC.contracts[0]
    assert AWAKENED_SEER_SPEC.camp_id == "good"
    assert AWAKENED_SEER_SPEC.tags == frozenset({"god"})
    assert AWAKENED_SEER_SPEC.max_count == 1
    assert contract.schedule_point is SchedulePoint.NIGHT_SEER_ACTION
    assert contract.multi_target_actions == {"check": 2}
    assert contract.selected_target_fact_namespaces == {"camp_label"}
    assert AWAKENED_SEER_SPEC.initial_private_data == {PAIR_CHECK_NAMESPACE: ()}
    assert AWAKENED_SEER_SPEC.opening_narration == (
        "觉醒预言家请睁眼", "请选择两名玩家，判断其中是否有狼人。",
    )


def test_the_pair_check_returns_only_that_there_is_a_wolf() -> None:
    effects = resolve_awakened_seer_action(seer_context(), seer_command())
    assert [effect.kind.value for effect in effects] == [
        "record_private_fact", "emit_event",
    ]
    fact = effects[0].payload["fact"]
    # 载荷在 GameEffect 构造时就被冻结成元组（JSON 数组 → tuple）。
    assert fact == {"seats": (1, 2), "has_wolf": True}
    assert effects[0].payload["namespace"] == PAIR_CHECK_NAMESPACE
    assert effects[1].payload["payload"]["has_wolf"] is True
    assert effects[1].payload["payload"]["target_seats"] == (1, 2)
    assert "窥视 1、2 号" in effects[1].payload["payload"]["thought"]


def test_a_pair_of_good_seats_answers_no() -> None:
    rows = (
        {"seat": 1, "camp_label": "good"},
        {"seat": 2, "camp_label": "good"},
    )
    effects = resolve_awakened_seer_action(seer_context(rows=rows), seer_command())
    assert effects[0].payload["fact"] == {"seats": (1, 2), "has_wolf": False}
    assert effects[1].payload["payload"]["has_wolf"] is False


def test_passing_and_degrading_still_record_a_line() -> None:
    passed = resolve_awakened_seer_action(seer_context(), seer_command("pass", None))
    assert [effect.kind.value for effect in passed] == ["emit_event"]
    assert passed[0].payload["payload"]["has_wolf"] is None
    assert passed[0].payload["payload"]["thought"].startswith("决定今晚不窥视")
    degraded = resolve_awakened_seer_action(
        seer_context(),
        ActionCommand(action_type="check", target_seats=(1, 2),
                      reasoning=DEGRADED_REASONING, degraded=True),
    )
    assert degraded[1].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert degraded[1].payload["payload"]["degraded"] is True


def test_the_seer_cannot_look_at_itself() -> None:
    itself = validate_awakened_seer_action(
        seer_context(actor=3), seer_command(seats=(3, 2)),
    )
    assert [item.code for item in itself] == ["self_check_forbidden"]
    assert validate_awakened_seer_action(seer_context(), seer_command()) == ()
    assert validate_awakened_seer_action(seer_context(), seer_command("pass", None)) == ()


@pytest.mark.parametrize(("rows", "error"), [
    (None, ValueError),                                    # 投影缺失
    (({"seat": 1, "camp_label": "good"},), ValueError),     # 只有一行
    (({"seat": 9, "camp_label": "good"},
      {"seat": 2, "camp_label": "good"}), ValueError),      # 行与座位不匹配
    (("不是对象", {"seat": 2, "camp_label": "good"}), ValueError),
    (({"seat": 1, "camp_label": 7},
      {"seat": 2, "camp_label": "good"}), TypeError),       # camp 不是字符串
])
def test_a_mismatched_projection_is_a_programming_error(rows, error) -> None:
    with pytest.raises(error):
        resolve_awakened_seer_action(seer_context(rows=rows), seer_command())


def test_the_plural_projection_feeds_the_pair() -> None:
    """走真实投影：多目标命令拿到两行 selected_targets，单目标仍是单行。"""
    registry = pairs_registry()
    state = _pairs_state()
    contract = registry.require(SEER).contracts[0]
    request = _request(state, registry, SEER, contract, SchedulePoint.NIGHT_SEER_ACTION)
    single = ContractProjectionCase(registry, state, request)
    context = single.context
    pair = single.project(seer_command())
    assert [row["seat"] for row in pair.facts["selected_targets"]] == [1, 2]
    assert [row["camp_label"] for row in pair.facts["selected_targets"]] == [
        "werewolf", "good",
    ]
    assert "selected_target" not in pair.facts


class ContractProjectionCase:
    """Small helper: one request plus the contexts it projects."""

    def __init__(self, registry, state, request) -> None:
        self.registry, self.state, self.request = registry, state, request
        self.context = ContextProjector().project(state, request, registry)

    def project(self, command: ActionCommand) -> ActionContext:
        return ContextProjector().project_selected_target(
            self.state, self.request, self.context, command, self.registry,
        )


def test_the_check_answer_reaches_its_own_prompt() -> None:
    """`pair_checks` 必须回读，否则查验等于白验（石像鬼当年的老缺口）。"""
    registry = pairs_registry()
    state = _pairs_state()
    contract = registry.require(SEER).contracts[0]
    request = _request(state, registry, SEER, contract, SchedulePoint.NIGHT_SEER_ACTION)
    projector = ContextProjector()
    context = projector.project(state, request, registry)
    command = seer_command()
    projected = projector.project_selected_target(
        state, request, context, command, registry,
    )
    for effect in ActionResolver().resolve_effects(
        projected, registry.require(SEER), contract, command,
    ):
        if effect.kind is not EffectKind.ACCEPT_ACTION:
            _apply_pair_effects(state, [effect])
    view = projector.project_view(state, registry, 4)
    assert view.facts["pair_checks"] == (
        {"seats": (1, 2), "has_wolf": True},
    )


def test_a_malformed_pair_record_is_dropped_not_raised() -> None:
    registry = pairs_registry()
    state = _pairs_state()
    for bad in (
        {"seats": [1], "has_wolf": True},
        {"seats": [1, 0], "has_wolf": True},
        {"seats": [1, 2], "has_wolf": "yes"},
        {"seats": "12", "has_wolf": True},
        {"seats": [1, 2]},
    ):
        _apply_pair_effects(state, [GameEffect(
            derive_effect_id(f"bad:{len(str(bad))}", 1), EffectKind.RECORD_PRIVATE_FACT,
            f"bad:{len(str(bad))}", target_seat=4,
            payload={"target": 4, "namespace": PAIR_CHECK_NAMESPACE, "fact": bad},
            expected_revision=state._pipeline_runtime.revision, sort_key=(1,),
        )])
    view = ContextProjector().project_view(state, registry, 4)
    assert view.facts["pair_checks"] == ()


def test_the_declared_pair_passes_the_validator() -> None:
    registry = pairs_registry()
    contract = registry.require(SEER).contracts[0]
    context = ContextProjector().project(
        _pairs_state(), _request(_pairs_state(), registry, SEER, contract,
                                 SchedulePoint.NIGHT_SEER_ACTION), registry,
    )
    assert ActionValidator().validate(context, contract, seer_command()) == ()
    wrong_count = ActionValidator().validate(
        context, contract, seer_command(seats=(1,)),
    )
    assert [item.code for item in wrong_count] == ["target_count_invalid"]


# ── 寻香魅影 ──────────────────────────────────────────────────────────────


def test_the_phantom_is_a_hidden_wolf_with_a_declared_bond_slot() -> None:
    assert FRAGRANCE_PHANTOM_SPEC.camp_id == "werewolf"
    assert FRAGRANCE_PHANTOM_SPEC.tags == frozenset({"wolf"})
    assert [c.contract_id for c in FRAGRANCE_PHANTOM_SPEC.contracts] == [
        "hidden_wolf_kill", "fragrance_bond", "fragrance_chain",
    ]
    assert FRAGRANCE_PHANTOM_SPEC.initial_resources == {
        "no_wolf_pack": 1, BOND_MARKER: 1,
    }
    assert FRAGRANCE_PHANTOM_SPEC.initial_private_data == {BOND_SLOT: ()}
    assert FRAGRANCE_PHANTOM_SPEC.bond_slot == BOND_SLOT
    assert FRAGRANCE_PHANTOM_SPEC.projected_facts == {"wolf_hint_seat"}
    bond = FRAGRANCE_PHANTOM_SPEC.contracts[1]
    assert bond.schedule_point is SchedulePoint.NIGHT_ACTION
    assert bond.multi_target_actions == {"bond": 2}
    chain = FRAGRANCE_PHANTOM_SPEC.contracts[2]
    assert chain.schedule_point is SchedulePoint.DAWN_REACTION
    assert chain.response_event_types == {"PLAYER_DIED"}
    # 任何死因都带走另一个人：空集只会匹配「没有死因」的事件，所以整份目录都要列。
    assert chain.response_reasons == frozenset(cause.value for cause in DeathCause)
    assert chain.per_game_limit == 1


def test_the_bond_keeps_one_pair_and_replaces_it() -> None:
    effects = resolve_fragrance_bond(
        _bond_context(), ActionCommand(action_type="bond", target_seats=(2, 3), reasoning="绑"),
    )
    assert [effect.kind.value for effect in effects] == [
        "set_private_data", "emit_event", "emit_event",
    ]
    assert effects[0].payload["key"] == BOND_SLOT
    assert effects[0].payload["value"] == (2, 3)
    assert effects[1].payload["event_type"] == "FRAGRANCE_BOND"
    assert effects[1].payload["payload"]["target_seats"] == (2, 3)
    assert "绑定 2、3 号" in effects[2].payload["payload"]["thought"]


def test_a_degraded_bond_reasoning_reports_the_engine_line() -> None:
    effects = resolve_fragrance_bond(
        _bond_context(),
        ActionCommand(action_type="pass", target_seats=None,
                      reasoning=DEGRADED_REASONING, degraded=True),
    )
    payload = effects[0].payload["payload"]
    assert payload["thought"] == DEGRADED_THOUGHT
    assert payload["degraded"] is True


def test_passing_the_bond_keeps_the_previous_pair() -> None:
    effects = resolve_fragrance_bond(
        _bond_context(), ActionCommand(action_type="pass", target_seats=None, reasoning="不绑"),
    )
    assert [effect.kind.value for effect in effects] == ["emit_event"]
    assert effects[0].payload["payload"]["thought"].startswith("决定今晚不绑定")
    assert validate_fragrance_bond(_bond_context(), ActionCommand(
        action_type="bond", target_seats=(1, 1), reasoning="自己",
    )) == ()


def test_the_chain_drags_the_other_member() -> None:
    effects = react_fragrance_chain(phantom_context(pair=(2, 3), trigger=2))
    assert [effect.kind.value for effect in effects] == [
        "consume_resource", "mark_death", "emit_event",
    ]
    assert effects[0].payload == {"target": 1, "resource": BOND_MARKER, "amount": 1}
    assert effects[1].payload == {"target": 3, "cause": BOND_CAUSE}
    assert effects[1].target_seat == 3
    assert effects[2].payload["event_type"] == "FRAGRANCE_BOND"
    assert effects[2].payload["payload"]["target_seat"] == 3


def test_a_bond_that_names_its_owner_kills_her_when_the_other_dies() -> None:
    effects = react_fragrance_chain(phantom_context(pair=(1, 3), trigger=3))
    assert effects[1].payload == {"target": 1, "cause": BOND_CAUSE}


def test_her_own_death_drags_her_partner() -> None:
    """窗口是「死者自己的角色」那条路开的：她死了也要响应。"""
    effects = react_fragrance_chain(
        phantom_context(pair=(1, 3), trigger=1, alive_seats=(3, 4)),
    )
    assert effects[1].target_seat == 3


@pytest.mark.parametrize("case", [
    {"marker": 0, "trigger": 2},                    # 技能已经用掉
    {"pair": (), "trigger": 2},                     # 还没绑过人
    {"pair": "不是列表", "trigger": 2},               # 槽里的存档读不出来
    {"pair": (2, 3), "trigger": 9},                 # 死的不是被绑的人
    {"pair": (2, 3), "trigger": 2, "alive_seats": ()},   # 另一人已经出局
    {"pair": (2, 3, 4), "trigger": 2, "alive_seats": (1, 3, 4)},   # 槽里有三个人：不是一对
    {"pair": (2, 3), "trigger": 2, "alive_seats": "1,3"},  # 存活名单坏了
    {"pair": (2, 3)},                               # 没有触发事件
])
def test_the_chain_stays_silent_where_it_does_not_apply(case: dict) -> None:
    context = phantom_context(
        marker=case.get("marker", 1), pair=case.get("pair", (2, 3)),
        trigger=case.get("trigger"), alive_seats=case.get("alive_seats", (1, 4)),
    )
    assert react_fragrance_chain(context) == ()


def test_a_bound_response_without_a_trigger_event_stays_silent() -> None:
    """窗口的 source_event_id 在、trigger_event 缺失：钩子必须当作「不是我的事」。"""
    context = phantom_context(pair=(2, 3), trigger=None)
    context = ActionContext.from_mapping({
        **{field: getattr(context, field) for field in context.__dataclass_fields__},
        "source_event_id": "event:" + "2" * 16,
    })
    assert react_fragrance_chain(context) == ()


def test_the_seer_window_closes_when_it_is_dead() -> None:
    from app.roles.awakened_seer import awakened_seer_applicable

    assert awakened_seer_applicable(seer_context()) is True
    assert awakened_seer_applicable(seer_context(alive=False)) is False


def test_a_bond_slot_that_is_not_a_token_is_rejected_at_build_time() -> None:
    from app.models.pipeline import RoleSpec

    with pytest.raises(ValueError, match="token"):
        RoleSpec(
            role_id="wolf-killer-bad-token", display_name="Bad", camp_id="good",
            initial_private_data={"bad slot": ()}, bond_slot="bad slot",
        )


def test_the_bond_reaction_dies_once() -> None:
    """真实写入口：第二个人落地为 bond_death，标记在同一批里花掉。"""
    state = _pairs_state()
    initialize_role_resources(
        state, pairs_registry().specs, "a" * 64,
    )
    state._pipeline_runtime.role_resources.setdefault(1, {})[BOND_MARKER] = 1
    state._pipeline_runtime.private_data.setdefault(1, {})[BOND_SLOT] = [2, 3]
    context = phantom_context(pair=(2, 3), trigger=2)
    context = ActionContext.from_mapping({
        **{field: getattr(context, field) for field in context.__dataclass_fields__},
        "revision": state._pipeline_runtime.revision,
        "resources": {BOND_MARKER: 1},
        "facts": dict(context.facts) | {BOND_SLOT: (2, 3), "alive_seats": (1, 3)},
    })
    # 走真实批次（resolver 会在最前面补 ACCEPT_ACTION），而不是手工拼三步。
    spec = pairs_registry().require(PHANTOM)
    contract = next(c for c in spec.contracts if c.contract_id == "fragrance_chain")
    result = EffectApplier().apply(
        state, ActionResolver().react_effects(context, spec, contract),
        EffectPermission(
        actor_seat=1, role_effects=frozenset(EffectKind),
        contract_effects=frozenset(EffectKind),
        allowed_targets=frozenset({1, 2, 3}),
            allowed_visibility=frozenset({"PUBLIC", "ACTOR"}),
        ),
    )
    assert state.players[3].is_alive is False
    # 写入口只负责「标记死 + 发 PLAYER_DIED」；death_history 是引擎的账
    # （`log_deaths`），这里断言事件本身。
    assert [(event["payload"]["seat"], event["payload"]["cause"])
            for event in result.events if event["event_type"] == "PLAYER_DIED"] == [
        (3, BOND_CAUSE),
    ]
    assert state._pipeline_runtime.role_resources[1][BOND_MARKER] == 0


def test_a_second_chain_is_rejected_at_the_write_entry() -> None:
    """标记花掉之后引擎再跑一次这个窗口：写入口以 precondition 拒绝。

    纯 react 没有 fallback，一次致命拒绝会打死整局，所以钩子自己必须闭嘴
    （上一条用例断言它返回空批次）—— 这条用例钉住「闭嘴是必要的」。
    """
    state = _pairs_state()
    initialize_role_resources(state, pairs_registry().specs, "a" * 64)
    state._pipeline_runtime.role_resources.setdefault(1, {})[BOND_MARKER] = 0
    state._pipeline_runtime.private_data.setdefault(1, {})[BOND_SLOT] = (2, 3)
    fresh = phantom_context(pair=(2, 3), trigger=2, marker=1)
    fresh = ActionContext.from_mapping({
        **{field: getattr(fresh, field) for field in fresh.__dataclass_fields__},
        "revision": state._pipeline_runtime.revision,
        "resources": {BOND_MARKER: 1},
        "facts": dict(fresh.facts) | {BOND_SLOT: (2, 3), "alive_seats": (1, 3)},
    })
    spec = pairs_registry().require(PHANTOM)
    contract = next(c for c in spec.contracts if c.contract_id == "fragrance_chain")
    with pytest.raises(EffectRejected):
        EffectApplier().apply(
            state, ActionResolver().react_effects(fresh, spec, contract),
            EffectPermission(
                actor_seat=1, role_effects=frozenset(EffectKind),
                contract_effects=frozenset(EffectKind),
                allowed_targets=frozenset({1, 2, 3}),
                allowed_visibility=frozenset({"PUBLIC", "ACTOR"}),
            ),
        )
    assert state.players[3].is_alive is True


def test_the_queue_wakes_her_for_a_bonded_seat_only() -> None:
    registry = pairs_registry()
    state = _pairs_state()
    initialize_role_resources(state, registry.specs, "a" * 64)
    state._pipeline_runtime.private_data.setdefault(1, {})[BOND_SLOT] = [2, 3]
    queue = ResponseQueue(registry)

    def windows(target: int):
        return queue.open(state, _death_event(target), depth=1)

    bonded = windows(2)
    assert [(window.contract_id, window.actor_seat) for window in bonded] == [
        ("fragrance_chain", 1),
    ]
    # 没被绑的人死了：她不该被叫醒。
    stranger = ResponseQueue(registry)
    assert stranger.open(state, _death_event(4), depth=1) == ()
    # 她自己死了：走「死者自己的角色」那条既有路，不重复开窗。
    own = ResponseQueue(registry)
    opened = own.open(state, _death_event(1), depth=1)
    assert [(window.contract_id, window.actor_seat) for window in opened] == [
        ("fragrance_chain", 1),
    ]


def test_the_queue_ignores_a_malformed_slot_and_a_dead_watcher() -> None:
    registry = pairs_registry()
    state = _pairs_state()
    initialize_role_resources(state, registry.specs, "a" * 64)
    state._pipeline_runtime.private_data.setdefault(1, {})[BOND_SLOT] = "2"
    assert ResponseQueue(registry).open(state, _death_event(2), depth=1) == ()
    state._pipeline_runtime.private_data[1][BOND_SLOT] = [2, "3"]
    assert ResponseQueue(registry).open(state, _death_event(2), depth=1) == ()
    state._pipeline_runtime.private_data[1][BOND_SLOT] = [2, 3]
    state.players[1].is_alive = False
    assert ResponseQueue(registry).open(state, _death_event(2), depth=1) == ()


def test_the_wolf_hint_is_one_seat_stable_and_never_herself() -> None:
    registry = pairs_registry()
    state = _pairs_state()
    projector, snapshot = ContextProjector(), pairs_registry()
    hint = projector.project_view(state, snapshot, 1).facts["wolf_hint_seat"]
    assert hint in {3, 6}, "只在小队里挑，绝不给自己"
    # 同一局同一个答案：没有 RNG，续跑重算也一致。
    assert projector.project_view(state, snapshot, 1).facts["wolf_hint_seat"] == hint
    state.players[6 if hint == 3 else 3].is_alive = False
    assert projector.project_view(state, snapshot, 1).facts["wolf_hint_seat"] == hint
    state.players[hint].is_alive = False
    assert projector.project_view(state, snapshot, 1).facts["wolf_hint_seat"] is None


def test_the_bond_lands_through_the_scheduler() -> None:
    registry = pairs_registry()

    def provider(request, projected, attempt):
        return ActionCommand(action_type="bond", target_seats=(2, 6), reasoning="绑")

    state = _pairs_state()
    Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    ).run_point(state, SchedulePoint.NIGHT_ACTION)

    # SET_PRIVATE_DATA 的 JSON 数组在 GameEffect 构造时冻结成元组。
    assert state._pipeline_runtime.private_data[1][BOND_SLOT] == (2, 6)
    assert state._pipeline_runtime.role_resources[1][BOND_MARKER] == 1


def test_a_night_knife_on_a_bonded_seat_still_settles_normally() -> None:
    """夜间这条路是「先死一个，再由图外的窗口带走另一个」，结算本身不受影响。"""
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},), (), {1, 2, 3, 6},
        {1: True, 2: True, 3: True, 6: True}, round_number=2,
    )
    assert [(item["seat"], item["cause"]) for item in deaths] == [(2, "wolf_kill")]
    assert alive[3] is True


def test_both_roles_are_registered_and_are_their_own_factories() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(SEER) is AWAKENED_SEER_SPEC
    assert snapshot.require(PHANTOM) is FRAGRANCE_PHANTOM_SPEC
    assert builtin_registry.require(SEER).role_factory.__name__ == "AwakenedSeer"
    assert builtin_registry.require(PHANTOM).role_factory.__name__ == "FragrancePhantom"


def test_a_bond_slot_nobody_declared_is_rejected() -> None:
    from app.models.pipeline import RoleSpec

    registry = RoleRegistry()
    with pytest.raises(ValueError, match="bond slot"):
        registry.register_pipeline(RoleSpec(
            role_id="wolf-killer-bad-slot", display_name="Bad", camp_id="good",
            contracts=(ActionContract(
                contract_id="bad_slot", schedule_point=SchedulePoint.DAWN_REACTION,
                order=10, action_types=("x",), actions_requiring_target=frozenset(),
                fallback_action_type="x",
            ),),
            bond_slot="not_declared",
        ))
        registry.freeze()


# ── helpers ───────────────────────────────────────────────────────────────


def _pairs_state() -> GameState:
    players = {
        1: PlayerState(1, PHANTOM, "werewolf"),
        2: PlayerState(2, VILLAGER, "good"),
        3: PlayerState(3, WOLF, "werewolf"),
        4: PlayerState(4, SEER, "good"),
        6: PlayerState(6, WOLF, "werewolf"),
    }
    state = GameState("pairs", phase=GamePhase.NIGHT, round_number=1, players=players)
    state._pipeline_runtime = _Runtime()
    return state


def _request(state: GameState, registry: RegistrySnapshot, role_id: str,
             contract: ActionContract, point: SchedulePoint):
    from app.models.pipeline import IssuedActionRequest

    return IssuedActionRequest(
        actor_seat=4 if role_id == SEER else 1, role_id=role_id, contract=contract,
        context_revision=state.state_revision, round_number=state.round_number,
        phase=state.phase.value, window_id="w", action_key="pair:1",
    )


def _death_event(seat: int) -> DomainEvent:
    return DomainEvent(
        event_id=f"event:{seat:016d}", event_type="PLAYER_DIED",
        payload={"target_seat": seat, "cause": "wolf_kill", "round_number": 2},
        reason="wolf_kill",
    )


def _apply_pair_effects(state: GameState, effects: tuple[GameEffect, ...]) -> None:
    key = effects[0].source_action_key
    revision = state._pipeline_runtime.revision
    accept = GameEffect(
        derive_effect_id(key, 0), EffectKind.ACCEPT_ACTION, key,
        payload={}, expected_revision=revision, sort_key=(0,),
    )
    EffectApplier().apply(state, (accept, *effects), EffectPermission(
        actor_seat=4, role_effects=frozenset(EffectKind),
        contract_effects=frozenset(EffectKind), allowed_targets=frozenset({4}),
        allowed_visibility=frozenset({"ACTOR"}),
    ))


def _bond_context() -> ActionContext:
    contract = FRAGRANCE_PHANTOM_SPEC.contracts[1]
    return ActionContext(
        "g", 5, {BOND_SLOT: (), "alive_seats": (1, 2, 3, 6)}, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=PHANTOM, actor_alive=True, resources={BOND_MARKER: 1},
        action_key="bond:1",
    )
