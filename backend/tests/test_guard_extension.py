import hashlib
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

FIVE_CORE_PATHS = (
    "app/core/game_engine.py",
    "app/core/action_validator.py",
    "app/core/action_resolver.py",
    "app/agents/prompt_builder.py",
    "app/agents/state_filter.py",
)

CORE_BLOBS_BEFORE_GUARD = (
    "7833a968835b6c99ae8f26a792d3407ca467edb1",
    "2420b709c39c38958b21518033baabd08fe4bf9d",
    "7e85ba15f8277a9d5b7d3424c53e3e3dca9f9422",
    "6a06a6ad0ffc56dd6ad96422deb0f8c56b573429",
    "bc509cfd7d80c12ea2a341fb08080a312e687d1e",
)


def _git_blob_sha(path: str) -> str:
    content = Path(path).read_bytes()
    return hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\0" + content
    ).hexdigest()


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
    )
    assert effects[0].target_seat == 3
    assert effects[0].payload == {"target": 3, "amount": 1}
    assert effects[1].target_seat == 1
    assert effects[1].payload == {"target": 1, "key": "last_guarded", "value": 3}
    assert effects[0].effect_id == derive_effect_id(context.action_key, 1)
    assert effects[1].effect_id == derive_effect_id(context.action_key, 2)
    assert resolve_guard_action(context, guard_command(None)) == ()


def test_guard_is_applicable_only_while_alive() -> None:
    assert guard_applicable(guard_context(actor_alive=True)) is True
    assert guard_applicable(guard_context(actor_alive=False)) is False


def test_guard_spec_is_registered_with_pipeline() -> None:
    spec = builtin_registry.freeze().require("wolf-killer-guard")
    assert spec is GUARD_SPEC or spec == GUARD_SPEC
    assert spec.display_name == "Guard"
    assert spec.camp_id == "good"


def test_guard_addition_did_not_modify_five_core_modules() -> None:
    current = tuple(_git_blob_sha(path) for path in FIVE_CORE_PATHS)
    assert current == CORE_BLOBS_BEFORE_GUARD
