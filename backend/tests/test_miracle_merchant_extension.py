"""Miracle Merchant: lends a one-shot skill, and pays if the lucky one is a wolf.

The role is the first user of *granted contracts* — contracts that live on the
Merchant's spec but are issued to whichever seat holds the marker — so this file
runs the real ``Scheduler`` end to end rather than only calling the hooks.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.night_settlement import resolve_delayed_deaths
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.guard import GUARD_SPEC
from app.roles.miracle_merchant import (
    GRANTED_SKILLS_KEY, LUCKY_CHECK_CONTRACT, LUCKY_GUARD_CONTRACT,
    LUCKY_POISON_CONTRACT, MIRACLE_GRANT_CONTRACT, MIRACLE_MERCHANT_SPEC,
    MiracleMerchant, lucky_applicable, merchant_applicable, resolve_lucky_check,
    resolve_lucky_guard, resolve_lucky_poison, resolve_merchant_action,
    validate_lucky_check, validate_lucky_pass_through, validate_merchant_action,
)
from app.roles.registry import RoleRegistry, builtin_registry
from app.roles.seer import SEER_SPEC
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC
from app.roles.witch import WITCH_SPEC

MERCHANT = "wolf-killer-miracle-merchant"
# seats: 1 wolf, 2 merchant, 3 villager, 4 seer
WOLF, MERCH, VILL, SEER = 1, 2, 3, 4


def context(contract, *, facts=None, actor=MERCH, alive=True, degraded=False,
            role=MERCHANT, resources=None) -> ActionContext:
    return ActionContext(
        "g", 3, {"alive_seats": (1, 2, 3, 4), **(facts or {})}, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=role, actor_alive=alive, resources=resources or {},
        action_key="k:1", degraded=degraded,
    )


def cmd(action: str, target: int | None = None, *, degraded: bool = False) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="理由", degraded=degraded)


def selected(seat: int, camp: str, labels=()) -> dict:
    return {"seat": seat, "camp_label": camp, "resource_labels": tuple(labels)}


# ── declarations ───────────────────────────────────────────────────────────


def test_spec_declares_three_granted_skills_gated_on_holder_markers() -> None:
    assert MIRACLE_MERCHANT_SPEC.camp_id == "good"
    assert MIRACLE_MERCHANT_SPEC.tags == frozenset({"god"})
    assert MIRACLE_GRANT_CONTRACT.order == 1, "the merchant acts before every other seat"
    assert MIRACLE_GRANT_CONTRACT.per_game_limit is None
    assert {c.contract_id: dict(c.required_resources)
            for c in MIRACLE_MERCHANT_SPEC.granted_contracts} == {
        "lucky_seer_check": {"lucky_seer": 1},
        "lucky_poison": {"lucky_poison": 1},
        "lucky_guard": {"lucky_guard": 1},
    }
    # No lent skill may be a one-shot by quota: declining must not spend it.
    for granted in MIRACLE_MERCHANT_SPEC.granted_contracts:
        assert granted.per_game_limit is None
    assert issubclass(MiracleMerchant, BaseRole)
    assert builtin_registry.require(MERCHANT).role_factory is MiracleMerchant


def test_skills_sit_after_the_merchant_so_a_same_night_grant_is_usable() -> None:
    assert MIRACLE_GRANT_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert LUCKY_CHECK_CONTRACT.schedule_point is SchedulePoint.NIGHT_SEER_ACTION
    assert LUCKY_POISON_CONTRACT.schedule_point is SchedulePoint.NIGHT_WITCH_ACTION
    assert LUCKY_GUARD_CONTRACT.schedule_point is SchedulePoint.NIGHT_WITCH_ACTION


def test_the_registry_can_look_up_the_lent_contracts_for_any_seat() -> None:
    frozen = builtin_registry.freeze()
    ids = {c.contract_id for c in frozen.contracts_for("wolf-killer-villager")}
    assert {"lucky_seer_check", "lucky_poison", "lucky_guard"} <= ids
    acting = frozen.acting_spec("wolf-killer-villager", LUCKY_CHECK_CONTRACT)
    assert acting.role_id == "wolf-killer-villager"
    assert LUCKY_CHECK_CONTRACT in acting.contracts
    assert "LUCKY SKILL" in acting.instructions
    # A role's own contract keeps its own spec untouched.
    own = frozen.acting_spec("wolf-killer-seer", SEER_SPEC.contracts[0])
    assert own is frozen.require("wolf-killer-seer")


# ── the Merchant's own choice ──────────────────────────────────────────────


def test_applicability_ends_once_every_skill_is_lent() -> None:
    assert merchant_applicable(context(MIRACLE_GRANT_CONTRACT)) is True
    assert merchant_applicable(context(MIRACLE_GRANT_CONTRACT, alive=False)) is False
    lent = ("grant_seer", "grant_poison", "grant_guard")
    assert merchant_applicable(
        context(MIRACLE_GRANT_CONTRACT, facts={GRANTED_SKILLS_KEY: lent[:2]}),
    ) is True
    assert merchant_applicable(
        context(MIRACLE_GRANT_CONTRACT, facts={GRANTED_SKILLS_KEY: lent}),
    ) is False


def test_validation_rejects_self_and_repeats_only() -> None:
    ctx = context(MIRACLE_GRANT_CONTRACT, facts={GRANTED_SKILLS_KEY: ("grant_seer",)})
    assert validate_merchant_action(ctx, cmd("pass")) == ()
    assert validate_merchant_action(ctx, cmd("grant_poison", VILL)) == ()
    assert [v.code for v in validate_merchant_action(ctx, cmd("grant_poison", MERCH))] == ["self_target"]
    assert [v.code for v in validate_merchant_action(ctx, cmd("grant_seer", VILL))] == [
        "skill_already_granted",
    ]
    # No target at all is the generic target check's business, not this hook's.
    assert validate_merchant_action(ctx, cmd("grant_seer", None)) == ()


def test_granting_a_good_player_lends_the_skill_and_marks_it_spent() -> None:
    ctx = context(MIRACLE_GRANT_CONTRACT, facts={"selected_target": selected(VILL, "good")})
    effects = resolve_merchant_action(ctx, cmd("grant_seer", VILL))
    assert [e.kind for e in effects] == [
        EffectKind.SET_PRIVATE_DATA, EffectKind.SET_RESOURCE, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload["value"] == ("grant_seer",)
    assert effects[1].payload == {"target": VILL, "resource": "lucky_seer", "value": 1}
    thought = effects[2].payload["payload"]["thought"]
    assert "3 号" in thought and "预言家的查验" in thought and "出局" not in thought


def test_granting_a_wolf_gives_nothing_and_costs_the_merchant() -> None:
    ctx = context(
        MIRACLE_GRANT_CONTRACT,
        facts={"selected_target": selected(WOLF, "werewolf"), GRANTED_SKILLS_KEY: ("grant_seer",)},
    )
    effects = resolve_merchant_action(ctx, cmd("grant_guard", WOLF))
    assert [e.kind for e in effects] == [
        EffectKind.SET_PRIVATE_DATA, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    ]
    # The skill is spent either way, so it cannot be tried on the next seat.
    assert effects[0].payload["value"] == ("grant_seer", "grant_guard")
    assert effects[1].target_seat == MERCH
    assert effects[1].payload == {"target": MERCH, "amount": 1, "cause": "miracle_backlash"}
    assert all(e.kind is not EffectKind.SET_RESOURCE for e in effects)
    assert "次日商人出局" in effects[2].payload["payload"]["thought"]


def test_passing_and_degrading_only_leave_a_line() -> None:
    ctx = context(MIRACLE_GRANT_CONTRACT)
    passed = resolve_merchant_action(ctx, cmd("pass"))
    assert [e.kind for e in passed] == [EffectKind.EMIT_EVENT]
    assert "不赠予" in passed[0].payload["payload"]["thought"]
    degraded = resolve_merchant_action(
        context(MIRACLE_GRANT_CONTRACT, degraded=True), cmd("pass", degraded=True),
    )
    assert degraded[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    # A target-less grant is treated as a pass rather than a crash.
    assert len(resolve_merchant_action(ctx, cmd("grant_seer", None))) == 1


def test_a_missing_or_mismatched_target_fact_is_refused() -> None:
    with pytest.raises(ValueError):
        resolve_merchant_action(context(MIRACLE_GRANT_CONTRACT), cmd("grant_seer", VILL))
    with pytest.raises(ValueError):
        resolve_merchant_action(
            context(MIRACLE_GRANT_CONTRACT, facts={"selected_target": selected(9, "good")}),
            cmd("grant_seer", VILL),
        )
    with pytest.raises(TypeError):
        resolve_merchant_action(
            context(MIRACLE_GRANT_CONTRACT, facts={"selected_target": {"seat": VILL, "camp_label": 3}}),
            cmd("grant_seer", VILL),
        )


# ── the three lent skills (hooks) ──────────────────────────────────────────


def test_lucky_applicability_and_validation() -> None:
    ctx = context(LUCKY_CHECK_CONTRACT, actor=VILL, role="wolf-killer-villager")
    assert lucky_applicable(ctx) is True
    assert lucky_applicable(context(LUCKY_CHECK_CONTRACT, actor=VILL, alive=False)) is False
    assert validate_lucky_check(ctx, cmd("check", SEER)) == ()
    assert [v.code for v in validate_lucky_check(ctx, cmd("check", VILL))] == ["self_check_forbidden"]
    assert validate_lucky_check(ctx, cmd("pass")) == ()
    assert validate_lucky_pass_through(ctx, cmd("poison", SEER)) == ()


def test_lucky_check_records_the_camp_and_spends_the_marker() -> None:
    ctx = context(
        LUCKY_CHECK_CONTRACT, actor=VILL, role="wolf-killer-villager",
        facts={"selected_target": selected(WOLF, "werewolf")},
    )
    effects = resolve_lucky_check(ctx, cmd("check", WOLF))
    assert [e.kind for e in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {"target": VILL, "resource": "lucky_seer", "amount": 1}
    assert effects[0].preconditions == {"resource_equals": {"resource": "lucky_seer", "value": 1}}
    assert effects[1].payload["namespace"] == "private_checks"
    assert effects[1].payload["fact"] == {"target": WOLF, "camp": "werewolf"}
    assert "狼人" in effects[2].payload["payload"]["thought"]


def test_lucky_check_bounces_off_a_reflecting_target() -> None:
    ctx = context(
        LUCKY_CHECK_CONTRACT, actor=VILL, role="wolf-killer-villager",
        facts={"selected_target": selected(WOLF, "werewolf", ("reflect_check",))},
    )
    effects = resolve_lucky_check(ctx, cmd("check", WOLF))
    assert effects[-1].payload["cause"] == "reflect"
    assert effects[-1].target_seat == VILL


def test_lucky_check_passing_and_bad_facts() -> None:
    ctx = context(LUCKY_CHECK_CONTRACT, actor=VILL, role="wolf-killer-villager")
    passed = resolve_lucky_check(ctx, cmd("pass"))
    assert [e.kind for e in passed] == [EffectKind.EMIT_EVENT]
    assert "不使用技能" in passed[0].payload["payload"]["thought"]
    degraded = resolve_lucky_check(
        context(LUCKY_CHECK_CONTRACT, actor=VILL, degraded=True), cmd("pass", degraded=True),
    )
    assert degraded[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    with pytest.raises(ValueError):
        resolve_lucky_check(ctx, cmd("check", WOLF))
    with pytest.raises(TypeError):
        resolve_lucky_check(
            context(LUCKY_CHECK_CONTRACT, actor=VILL,
                    facts={"selected_target": {"seat": WOLF, "camp_label": None}}),
            cmd("check", WOLF),
        )


def test_lucky_poison_and_guard_spend_their_marker_and_apply_the_effect() -> None:
    poison = resolve_lucky_poison(
        context(LUCKY_POISON_CONTRACT, actor=VILL,
                facts={"selected_target": selected(WOLF, "werewolf")}),
        cmd("poison", WOLF),
    )
    assert [e.kind for e in poison] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT,
    ]
    assert poison[0].payload["resource"] == "lucky_poison"
    assert poison[1].payload == {"target": WOLF, "amount": 1, "cause": "poison"}

    bounced = resolve_lucky_poison(
        context(LUCKY_POISON_CONTRACT, actor=VILL,
                facts={"selected_target": selected(WOLF, "werewolf", ("reflect_poison",))}),
        cmd("poison", WOLF),
    )
    assert bounced[-1].payload["cause"] == "reflect"
    # With no selected-target projection at all the poison still lands.
    assert len(resolve_lucky_poison(
        context(LUCKY_POISON_CONTRACT, actor=VILL), cmd("poison", WOLF),
    )) == 3
    assert [e.kind for e in resolve_lucky_poison(
        context(LUCKY_POISON_CONTRACT, actor=VILL), cmd("pass"),
    )] == [EffectKind.EMIT_EVENT]

    guard = resolve_lucky_guard(context(LUCKY_GUARD_CONTRACT, actor=VILL), cmd("guard", SEER))
    assert [e.kind for e in guard] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_PROTECTION, EffectKind.EMIT_EVENT,
    ]
    assert guard[1].payload == {"target": SEER, "amount": 1, "source": "guard"}
    assert [e.kind for e in resolve_lucky_guard(
        context(LUCKY_GUARD_CONTRACT, actor=VILL), cmd("pass"),
    )] == [EffectKind.EMIT_EVENT]


# ── end to end through the real scheduler ──────────────────────────────────


def build_registry() -> RoleRegistry:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, SEER_SPEC, WITCH_SPEC, GUARD_SPEC,
                 MIRACLE_MERCHANT_SPEC):
        registry.register_pipeline(spec)
    return registry


def build_state(game_id: str) -> GameState:
    return GameState(
        game_id, phase=GamePhase.NIGHT, round_number=1,
        players={
            WOLF: PlayerState(WOLF, "wolf-killer-werewolf", "werewolf"),
            MERCH: PlayerState(MERCH, MERCHANT, "good"),
            VILL: PlayerState(VILL, "wolf-killer-villager", "good"),
            SEER: PlayerState(SEER, "wolf-killer-seer", "good"),
        },
    )


def scheduler(provider) -> Scheduler:
    return Scheduler(
        build_registry().freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )


def test_a_lucky_villager_checks_a_player_on_the_same_night() -> None:
    state = build_state("lucky-seer")
    seen: list[tuple[int, str, str]] = []

    def provider(request, projected, attempt):
        seen.append((request.actor_seat, request.role_id, request.contract.contract_id))
        if request.contract.contract_id == "miracle_grant":
            return cmd("grant_seer", VILL)
        if request.contract.contract_id == "lucky_seer_check":
            return cmd("check", WOLF)
        return cmd("pass")

    run = scheduler(provider)
    run.run_point(state, SchedulePoint.NIGHT_ACTION)
    # Before the skill is lent nobody but the merchant is asked anything.
    assert seen == [(MERCH, MERCHANT, "miracle_grant")]
    assert state._pipeline_runtime.role_resources[VILL]["lucky_seer"] == 1

    run.run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    # The lucky seat is asked as a villager, with the lent contract.
    assert (VILL, "wolf-killer-villager", "lucky_seer_check") in seen
    assert (SEER, "wolf-killer-seer", "seer_check") in seen
    assert state._pipeline_runtime.role_resources[VILL]["lucky_seer"] == 0
    from app.core.role_runtime import role_private_facts_view

    checks = role_private_facts_view(state, VILL, "private_checks")
    assert [dict(item) for item in checks] == [{"target": WOLF, "camp": "werewolf"}]
    # The answer is read back for the lucky seat's own later prompts too.
    view = ContextProjector().project_view(state, build_registry().freeze(), VILL)
    assert view.facts["private_checks"][-1]["target"] == WOLF
    assert state._pipeline_runtime.private_data[MERCH][GRANTED_SKILLS_KEY] == ("grant_seer",)
    # The seat is still a plain villager for every camp-based rule.
    assert state.players[VILL].role == "wolf-killer-villager"

    # Spent: the next night the check is no longer issued to anyone.
    seen.clear()
    state.round_number = 2
    run.run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    assert all(contract != "lucky_seer_check" for _, _, contract in seen)


def test_declining_a_lent_skill_keeps_it_for_a_later_night() -> None:
    state = build_state("lucky-keep")
    calls: list[str] = []

    def provider(request, projected, attempt):
        calls.append(request.contract.contract_id)
        if request.contract.contract_id == "miracle_grant":
            return cmd("grant_guard", VILL)
        return cmd("pass")

    run = scheduler(provider)
    run.run_point(state, SchedulePoint.NIGHT_ACTION)
    run.run_point(state, SchedulePoint.NIGHT_WITCH_ACTION)
    assert "lucky_guard" in calls
    assert state._pipeline_runtime.role_resources[VILL]["lucky_guard"] == 1, "a pass is not a use"


def test_a_lucky_guard_protects_through_the_write_gate() -> None:
    state = build_state("lucky-guard")

    def provider(request, projected, attempt):
        contract = request.contract.contract_id
        if contract == "miracle_grant":
            return cmd("grant_guard", VILL)
        if contract == "lucky_guard":
            return cmd("guard", SEER)
        return cmd("pass")

    run = scheduler(provider)
    run.run_point(state, SchedulePoint.NIGHT_ACTION)
    run.run_point(state, SchedulePoint.NIGHT_WITCH_ACTION)
    assert state._pipeline_runtime.role_resources[VILL]["lucky_guard"] == 0
    assert state._pipeline_runtime.pending_protection


def test_lending_to_a_wolf_kills_the_merchant_after_the_next_days_speeches() -> None:
    state = build_state("backlash")

    def provider(request, projected, attempt):
        if request.contract.contract_id == "miracle_grant":
            return cmd("grant_poison", WOLF)
        return cmd("pass")

    run = scheduler(provider)
    run.run_point(state, SchedulePoint.NIGHT_ACTION)
    # The wolf got nothing, and the skill is recorded as lent.
    assert "lucky_poison" not in state._pipeline_runtime.role_resources.get(WOLF, {})
    assert state._pipeline_runtime.private_data[MERCH][GRANTED_SKILLS_KEY] == ("grant_poison",)

    settlement = EffectApplier().settle_pending(state, round_number=1)
    assert settlement is not None
    # Not dead tonight: the backlash waits for the day, like a hunt.
    assert state.players[MERCH].is_alive is True
    assert state.death_history == []
    assert state._pipeline_runtime.statuses[MERCH] == {"miracle_marked", "delayed_death"}

    deaths, cleared = resolve_delayed_deaths(state._pipeline_runtime.statuses, 2)
    assert deaths == ({"seat": MERCH, "cause": "miracle_backlash", "round_number": 2},)
    assert set(cleared[MERCH]) == {"miracle_marked", "delayed_death"}


# ── registry validation and scheduler gates for granted contracts ──────────


def _registered(spec) -> RoleRegistry:
    registry = RoleRegistry()
    registry.register_pipeline(spec)
    registry.freeze()
    return registry


def test_a_stray_contract_gets_the_seats_own_spec_back() -> None:
    frozen = builtin_registry.freeze()
    # Neither owned nor lent: the resolver's identity check is the one to refuse it.
    assert frozen.acting_spec("wolf-killer-seer", GUARD_SPEC.contracts[0]) is frozen.require(
        "wolf-killer-seer",
    )


def test_granted_notes_must_match_the_granted_contracts() -> None:
    with pytest.raises(ValueError, match="granted notes must match"):
        _registered(replace(MIRACLE_MERCHANT_SPEC, granted_notes={"lucky_seer_check": "x"}))


def test_a_contract_cannot_be_owned_and_granted() -> None:
    both = replace(
        MIRACLE_MERCHANT_SPEC, granted_contracts=(MIRACLE_GRANT_CONTRACT,),
        granted_notes={"miracle_grant": "x"},
    )
    with pytest.raises(ValueError, match="both owned and granted"):
        _registered(both)


def test_a_granted_contract_needs_a_holder_resource() -> None:
    bare = replace(LUCKY_CHECK_CONTRACT, required_resources={})
    spec = replace(
        MIRACLE_MERCHANT_SPEC, granted_contracts=(bare,),
        granted_notes={"lucky_seer_check": "x"},
    )
    with pytest.raises(ValueError, match="holder resource"):
        _registered(spec)


def test_a_granted_contract_must_be_a_plain_per_seat_command() -> None:
    from app.roles.idiot import IDIOT_SPEC
    from app.roles.werewolf import WEREWOLF_KILL_CONTRACT

    reaction = IDIOT_SPEC.contracts[0]
    variants = (
        {"aggregate": WEREWOLF_KILL_CONTRACT.aggregate},
        {
            "react": reaction.react, "response_event_types": reaction.response_event_types,
            "response_reasons": reaction.response_reasons,
        },
    )
    for change in variants:
        odd = replace(
            LUCKY_CHECK_CONTRACT, resolve=None, selected_target_fact_namespaces=frozenset(),
            **change,
        )
        spec = replace(
            MIRACLE_MERCHANT_SPEC, granted_contracts=(odd,),
            granted_notes={"lucky_seer_check": "x"},
        )
        with pytest.raises(ValueError, match="plain per-seat command"):
            _registered(spec)

def never_applicable(context: ActionContext) -> bool:
    return False


def sloppy_applicable(context: ActionContext) -> bool:
    return "yes"


def _lend(registry: RoleRegistry, game_id: str) -> GameState:
    """Run the merchant's night so the villager really holds the seer skill."""
    state = build_state(game_id)

    def provider(request, projected, attempt):
        return cmd("grant_seer", VILL) if request.contract.contract_id == "miracle_grant" else cmd("pass")

    Scheduler(
        registry.freeze(), ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    ).run_point(state, SchedulePoint.NIGHT_ACTION)
    assert state._pipeline_runtime.role_resources[VILL]["lucky_seer"] == 1
    return state


def _with_lent(*lent) -> RoleRegistry:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, SEER_SPEC, replace(
        MIRACLE_MERCHANT_SPEC, granted_contracts=lent,
    )):
        registry.register_pipeline(spec)
    return registry


def test_a_lent_skill_that_is_not_applicable_is_not_issued() -> None:
    closed = replace(LUCKY_CHECK_CONTRACT, is_applicable=never_applicable)
    registry = _with_lent(closed, LUCKY_POISON_CONTRACT, LUCKY_GUARD_CONTRACT)
    state = _lend(registry, "closed")
    asked: list[str] = []

    def provider(request, projected, attempt):
        asked.append(request.contract.contract_id)
        return cmd("pass")

    Scheduler(
        registry.freeze(), ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    ).run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    assert "lucky_seer_check" not in asked


def test_a_non_boolean_applicability_answer_pauses_the_pipeline() -> None:
    from app.core.scheduler import PipelinePaused

    good = _with_lent(LUCKY_CHECK_CONTRACT, LUCKY_POISON_CONTRACT, LUCKY_GUARD_CONTRACT)
    state = _lend(good, "broken")
    broken = replace(LUCKY_CHECK_CONTRACT, is_applicable=sloppy_applicable)
    registry = _with_lent(broken, LUCKY_POISON_CONTRACT, LUCKY_GUARD_CONTRACT)
    with pytest.raises(PipelinePaused, match="applicability"):
        Scheduler(
            registry.freeze(), ContextProjector(), ActionValidator(), ActionResolver(),
            EffectApplier(), lambda *args: cmd("pass"),
        ).run_point(state, SchedulePoint.NIGHT_SEER_ACTION)


def test_a_lent_skill_already_answered_this_round_is_not_asked_twice() -> None:
    registry = _with_lent(LUCKY_CHECK_CONTRACT, LUCKY_POISON_CONTRACT, LUCKY_GUARD_CONTRACT)
    state = _lend(registry, "asked")
    asked: list[str] = []

    def provider(request, projected, attempt):
        asked.append(request.contract.contract_id)
        return cmd("pass")

    run = Scheduler(
        registry.freeze(), ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    run.run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    assert asked.count("lucky_seer_check") == 1
    run.run_point(state, SchedulePoint.NIGHT_SEER_ACTION, slot="again")
    assert asked.count("lucky_seer_check") == 1, "the round limit closes the second window"

def test_a_dead_holder_is_not_asked_to_use_the_skill() -> None:
    registry = _with_lent(LUCKY_CHECK_CONTRACT, LUCKY_POISON_CONTRACT, LUCKY_GUARD_CONTRACT)
    state = _lend(registry, "dead-holder")
    state.players[VILL].is_alive = False
    asked: list[str] = []

    def provider(request, projected, attempt):
        asked.append(request.contract.contract_id)
        return cmd("pass")

    Scheduler(
        registry.freeze(), ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    ).run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    assert "lucky_seer_check" not in asked