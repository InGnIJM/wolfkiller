from pathlib import Path

import pytest

from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect,
    RoleSpec, RuleViolation, SchedulePoint,
)
from app.roles.guard import (
    GUARD_SPEC, guard_applicable, resolve_guard_action, validate_guard_action,
)
from app.roles.registry import builtin_registry


def guard_contract() -> ActionContract:
    return next(contract for contract in GUARD_SPEC.contracts
                if contract.contract_id == "guard_action")


def guard_context(*, last_guarded=None, actor_alive=True, action_key="guard:1") -> ActionContext:
    contract = guard_contract()
    return ActionContext(
        game_id="guard-game",
        revision=0,
        config_version="guard-registry",
        contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(),
        round_number=2,
        phase="night",
        window_id="guard:window",
        schedule_point=contract.schedule_point,
        actor_seat=1,
        actor_role_id=GUARD_SPEC.role_id,
        actor_alive=actor_alive,
        action_key=action_key,
        facts={"alive_seats": (1, 2, 3), "last_guarded": last_guarded},
    )


def guard_command(target: int | None) -> ActionCommand:
    return ActionCommand(
        action_type="pass" if target is None else "guard",
        target_seat=target,
        reasoning="protect a suspicious player",
    )


def test_guard_rejects_same_target_on_consecutive_nights() -> None:
    violations = validate_guard_action(
        guard_context(last_guarded=2), guard_command(2),
    )
    assert [item.code for item in violations] == ["consecutive_guard"]


def test_guard_allows_new_target_and_pass() -> None:
    assert validate_guard_action(guard_context(last_guarded=2), guard_command(3)) == ()
    assert validate_guard_action(guard_context(last_guarded=2), guard_command(None)) == ()
    assert validate_guard_action(guard_context(last_guarded=None), guard_command(2)) == ()


def test_guard_uses_only_existing_effects() -> None:
    context = guard_context(last_guarded=2)
    effects = resolve_guard_action(context, guard_command(3))
    assert tuple(effect.kind for effect in effects) == (
        EffectKind.SUBMIT_PROTECTION, EffectKind.SET_PRIVATE_DATA,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    )
    assert effects[0].target_seat == 3
    assert effects[0].payload == {"target": 3, "amount": 1, "source": "guard"}
    assert effects[1].target_seat == 1
    assert effects[1].payload == {"target": 1, "key": "last_guarded", "value": 3}
    assert effects[0].effect_id == derive_effect_id(context.action_key, 1)
    assert effects[1].effect_id == derive_effect_id(context.action_key, 2)


def test_guard_action_emits_reasoning_and_protect_audience_events() -> None:
    context = guard_context(last_guarded=2)
    effects = resolve_guard_action(context, guard_command(3))
    reasoning, protect = effects[2], effects[3]
    assert reasoning.kind is EffectKind.EMIT_EVENT
    assert reasoning.payload["event_type"] == "GUARD_REASONING"
    assert reasoning.payload["payload"]["seat"] == 1
    assert reasoning.payload["payload"]["action_type"] == "guard"
    assert reasoning.payload["payload"]["target_seat"] == 3
    assert reasoning.payload["payload"]["reasoning"] == "protect a suspicious player"
    assert "守护 3 号" in reasoning.payload["payload"]["thought"]
    assert reasoning.visibility == ("PUBLIC",)
    assert protect.kind is EffectKind.EMIT_EVENT
    assert protect.payload == {"event_type": "GUARD_PROTECT", "payload": {"target_seat": 3}}
    assert protect.visibility == ("PUBLIC",)


def test_guard_pass_emits_reasoning_event_only() -> None:
    effects = resolve_guard_action(guard_context(), guard_command(None))
    assert tuple(effect.kind for effect in effects) == (EffectKind.EMIT_EVENT,)
    reasoning = effects[0]
    assert reasoning.payload["event_type"] == "GUARD_REASONING"
    assert reasoning.payload["payload"]["action_type"] == "pass"
    assert reasoning.payload["payload"]["target_seat"] is None
    assert "不守护" in reasoning.payload["payload"]["thought"]


def test_guard_contract_and_spec_allow_emit_event() -> None:
    assert EffectKind.EMIT_EVENT in guard_contract().allowed_effects
    assert EffectKind.EMIT_EVENT in GUARD_SPEC.allowed_effects


def test_guard_is_applicable_only_while_alive() -> None:
    assert guard_applicable(guard_context(actor_alive=True)) is True
    assert guard_applicable(guard_context(actor_alive=False)) is False


def test_guard_spec_is_registered_with_pipeline() -> None:
    spec = builtin_registry.freeze().require("wolf-killer-guard")
    assert spec is GUARD_SPEC or spec == GUARD_SPEC
    assert spec.display_name == "Guard"
    assert spec.camp_id == "good"


def test_ten_player_standard_board_creates_roles_via_legacy_registry() -> None:
    role_counts = {
        "wolf-killer-werewolf": 3,
        "wolf-killer-villager": 3,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-hunter": 1,
        "wolf-killer-guard": 1,
    }
    roles = builtin_registry.create_roles(
        role_counts, 10, object(), lambda: object(),
    )
    assert sorted(roles) == list(range(1, 11))
    assert sorted(role.role_name for role in roles.values()) == [
        "wolf-killer-guard",
        "wolf-killer-hunter",
        "wolf-killer-seer",
        "wolf-killer-villager",
        "wolf-killer-villager",
        "wolf-killer-villager",
        "wolf-killer-werewolf",
        "wolf-killer-werewolf",
        "wolf-killer-werewolf",
        "wolf-killer-witch",
    ]


def test_guard_night_action_point_issues_request_and_submits_protection() -> None:
    from app.core.action_resolver import ActionResolver
    from app.core.action_validator import ActionValidator
    from app.core.context_projector import ContextProjector
    from app.core.effect_applier import EffectApplier
    from app.core.role_runtime import role_private_data_view
    from app.core.scheduler import Scheduler
    from app.models.game import GameConfig, GamePhase, GameState, PlayerState
    from app.models.pipeline import ActionCommand as PipelineActionCommand

    snapshot = builtin_registry.freeze()
    state = GameState(game_id="guard-night", config=GameConfig(role_counts={
        "wolf-killer-guard": 1,
        "wolf-killer-villager": 2,
    }))
    specs = snapshot.specs
    state.players[1] = PlayerState(1, "wolf-killer-guard", "good")
    state.players[2] = PlayerState(2, "wolf-killer-villager", "good")
    state.players[3] = PlayerState(3, "wolf-killer-villager", "good")
    state.phase = GamePhase.NIGHT

    requests = []

    def provider(request, context, attempt):
        requests.append(request)
        return PipelineActionCommand(
            action_type="guard", target_seat=2, reasoning="protect the talkative one",
        )

    scheduler = Scheduler(
        snapshot, ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )
    result = scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)

    assert [request.contract.contract_id for request in requests] == ["guard_action"]
    assert len(result.commits) == 1
    commit = result.commits[0]
    action_key = requests[0].action_key
    assert derive_effect_id(action_key, 1) in commit.effect_ids  # SUBMIT_PROTECTION
    assert derive_effect_id(action_key, 2) in commit.effect_ids  # SET_PRIVATE_DATA
    assert derive_effect_id(action_key, 3) in commit.effect_ids  # GUARD_REASONING
    assert derive_effect_id(action_key, 4) in commit.effect_ids  # GUARD_PROTECT
    assert role_private_data_view(state, 1)["last_guarded"] == 2
    event_types = [event["event_type"] for event in commit.events]
    assert event_types == ["GUARD_REASONING", "GUARD_PROTECT"]
