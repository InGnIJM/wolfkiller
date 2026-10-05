"""Pure White: a good god that learns one exact identity a night.

From the second night on, a check that lands on a werewolf kills that werewolf
in the same night (``verify_kill``). Everything is a frozen declaration plus
pure hooks; the identity answer rides the ``role_checks`` private-fact channel
that ``ContextProjector`` reads back for the role that recorded it.
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, _Runtime, derive_effect_id
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, IssuedActionRequest, SchedulePoint,
)
from app.roles.pure_white import (
    PURE_WHITE_CHECK_CONTRACT, PURE_WHITE_SPEC, ROLE_CHECK_NAMESPACE,
    PureWhite, resolve_pure_white_action, validate_pure_white_action,
    pure_white_applicable,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC

WHITE = "wolf-killer-pure-white"
WOLF = "wolf-killer-werewolf"
VILLAGER = "wolf-killer-villager"
EVENT_ID = "event:" + "4" * 16


def pure_white_registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, PURE_WHITE_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def context(
    *, alive: bool = True, target: int | None = 3, camp: str | None = "werewolf",
    role: str | None = VILLAGER, round_number: int = 2, with_fact: bool = True,
    actor: int = 1, action_type: str = "check",
) -> ActionContext:
    contract = PURE_WHITE_CHECK_CONTRACT
    facts: dict[str, object] = {"alive_seats": (1, 2, 3)}
    if with_fact and target is not None:
        selected: dict[str, object] = {"seat": target}
        if camp is not None:
            selected["camp_label"] = camp
        if role is not None:
            selected["role_label"] = role
        facts["selected_target"] = selected
    return ActionContext(
        "g", 5, facts, config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=WHITE, actor_alive=alive, action_key="white:1",
        source_event_id=EVENT_ID,
    )


def command(action_type: str = "check", target: int | None = 3) -> ActionCommand:
    return ActionCommand(action_type=action_type, target_seat=target, reasoning="ok")


def kinds(effects) -> list[str]:
    return [effect.kind.value for effect in effects]


def test_spec_is_a_good_god_with_one_nightly_check() -> None:
    assert PURE_WHITE_SPEC.role_id == WHITE
    assert PURE_WHITE_SPEC.camp_id == "good"
    assert PURE_WHITE_SPEC.tags == frozenset({"god"})
    assert PURE_WHITE_SPEC.max_count == 1
    assert PURE_WHITE_SPEC.contracts == (PURE_WHITE_CHECK_CONTRACT,)
    assert PURE_WHITE_CHECK_CONTRACT.schedule_point is SchedulePoint.NIGHT_SEER_ACTION
    assert PURE_WHITE_CHECK_CONTRACT.action_types == ("check", "pass")
    assert PURE_WHITE_CHECK_CONTRACT.selected_target_fact_namespaces == frozenset(
        {"camp_label", "role_label"}
    )
    assert PURE_WHITE_SPEC.initial_resources == {}
    assert ROLE_CHECK_NAMESPACE == "role_checks"


def test_applicable_is_just_liveness() -> None:
    assert pure_white_applicable(context()) is True
    assert pure_white_applicable(context(alive=False)) is False
    assert pure_white_applicable(context(round_number=1)) is True


def test_validate_rejects_a_self_check_only() -> None:
    assert validate_pure_white_action(context(target=2), command(target=2)) == ()
    violations = validate_pure_white_action(context(), command(target=1))
    assert [item.code for item in violations] == ["self_check_forbidden"]
    assert validate_pure_white_action(context(), command("pass", None)) == ()


def test_a_check_records_camp_and_identity_privately() -> None:
    effects = resolve_pure_white_action(context(role=VILLAGER, camp="good"),
                                        command())
    assert kinds(effects) == ["record_private_fact", "record_private_fact", "emit_event"]
    assert effects[0].payload == {
        "target": 1, "namespace": "private_checks",
        "fact": {"target": 3, "camp": "good"},
    }
    assert effects[0].visibility == ("ACTOR",)
    assert effects[1].payload == {
        "target": 1, "namespace": "role_checks",
        "fact": {"target": 3, "role": VILLAGER},
    }
    assert effects[1].visibility == ("ACTOR",)
    assert effects[2].payload["event_type"] == "PURE_WHITE_REASONING"
    assert effects[2].visibility == ("PUBLIC",)


def test_the_kill_starts_on_the_second_night_only() -> None:
    first_night = resolve_pure_white_action(context(round_number=1), command())
    assert "submit_damage" not in kinds(first_night)
    second_night = resolve_pure_white_action(context(round_number=2), command())
    damage = [effect for effect in second_night if effect.kind is EffectKind.SUBMIT_DAMAGE]
    assert len(damage) == 1
    assert damage[0].payload == {"target": 3, "amount": 1, "cause": "verify_kill"}
    assert damage[0].target_seat == 3


def test_a_good_target_is_never_killed() -> None:
    effects = resolve_pure_white_action(
        context(camp="good", role="wolf-killer-seer"), command(),
    )
    assert "submit_damage" not in kinds(effects)


def test_passing_only_records_a_reason() -> None:
    effects = resolve_pure_white_action(context(target=None), command("pass", None))
    assert kinds(effects) == ["emit_event"]
    assert effects[0].payload["payload"]["action_type"] == "pass"
    assert effects[0].payload["payload"]["thought"].startswith("决定今晚不查验")


def test_a_missing_selected_target_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        resolve_pure_white_action(context(with_fact=False), command())
    with pytest.raises(TypeError):
        resolve_pure_white_action(context(camp=None), command())
    with pytest.raises(TypeError):
        resolve_pure_white_action(context(role=None), command())


def test_resolver_accepts_the_whole_batch() -> None:
    registry = builtin_registry.freeze()
    resolved = ActionResolver().resolve_effects(
        context(), registry.require(WHITE), PURE_WHITE_CHECK_CONTRACT, command(),
    )
    # The resolver prepends the canonical accept effect; the role's own batch
    # follows unchanged and keeps its canonical ordinal ids.
    assert resolved[0].kind is EffectKind.ACCEPT_ACTION
    assert resolved[1:] == resolve_pure_white_action(context(), command())
    assert derive_effect_id("white:1", 1) == resolved[1].effect_id


def test_pure_white_is_registered_in_both_places() -> None:
    assert builtin_registry.freeze().require(WHITE) is PURE_WHITE_SPEC
    from app.roles.pure_white import PureWhite

    assert builtin_registry.require(WHITE).role_factory is PureWhite


def test_the_identity_answer_reaches_the_pure_white_prompt() -> None:
    registry = pure_white_registry()
    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=2,
        players={
            1: PlayerState(1, WHITE, "good"),
            3: PlayerState(3, VILLAGER, "good"),
        },
    )
    state._pipeline_runtime = _Runtime()
    state._pipeline_runtime.private_facts[1] = [
        {"namespace": "role_checks", "fact": {"target": 3, "role": VILLAGER}},
    ]
    request = IssuedActionRequest(1, WHITE, PURE_WHITE_CHECK_CONTRACT, 0, 2, "night", "w", "k")
    projected = ContextProjector().project(state, request, registry)
    assert projected.facts["role_checks"] == ({"target": 3, "role": VILLAGER},)


def test_scheduler_runs_the_check_and_applies_the_kill(tmp_path) -> None:
    """端到端：第 2 夜查中狼人 → 当夜 verify_kill 伤害进 pending，同夜结算出局。"""
    registry = pure_white_registry()
    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=2,
        players={
            1: PlayerState(1, WHITE, "good"),
            2: PlayerState(2, WOLF, "werewolf"),
        },
    )

    def provider(request, ctx, attempt):
        return ActionCommand(action_type="check", target_seat=2, reasoning="验狼")

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    result = scheduler.run_point(state, SchedulePoint.NIGHT_SEER_ACTION)
    runtime = state._pipeline_runtime
    assert runtime.pending_damage == ({"target": 2, "amount": 1, "cause": "verify_kill"},)
    assert any(
        event["event_type"] == "PURE_WHITE_REASONING" for event in result.events
    )
    # The identity answer is private: it never lands in the point's events.
    assert [event["event_type"] for event in result.events] == ["PURE_WHITE_REASONING"]
    settlement = EffectApplier().settle_pending(state, round_number=2)
    assert settlement is not None
    assert state.players[2].is_alive is False
    assert [(death.player_seat, death.cause) for death in state.death_history] == [
        (2, "verify_kill"),
    ]
