import pytest

from app.core.action_validator import ActionValidator
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext as PipelineActionContext,
    ActionContract as PipelineActionContract,
    RuleViolation,
    SchedulePoint,
)


PIPELINE_HOOK_CALLS: list[tuple[PipelineActionContext, PipelineActionCommand]] = []


def pipeline_valid_hook(
    context: PipelineActionContext, command: PipelineActionCommand
) -> tuple[RuleViolation, ...]:
    PIPELINE_HOOK_CALLS.append((context, command))
    return (
        RuleViolation(code="role_rule", message="role-specific rule failed"),
        RuleViolation(code="role_rule_2", message="another role rule failed"),
    )


def pipeline_bad_list_hook(
    context: PipelineActionContext, command: PipelineActionCommand
) -> object:
    return [RuleViolation(code="bad", message="wrong container")]


def pipeline_bad_item_hook(
    context: PipelineActionContext, command: PipelineActionCommand
) -> object:
    return (object(),)


def pipeline_raising_hook(
    context: PipelineActionContext, command: PipelineActionCommand
) -> tuple[RuleViolation, ...]:
    raise RuntimeError("hook exploded")


def pipeline_context(
    *, contract: PipelineActionContract | None = None, **changes: object
) -> PipelineActionContext:
    if contract is None:
        contract = pipeline_contract()
    values: dict[str, object] = {
        "game_id": "pipeline-game",
        "revision": 3,
        "config_version": "registry-v1",
        "contract_id": "werewolf-kill",
        "contract_version": 1,
        "contract_digest": contract.stable_digest(),
        "round_number": 2,
        "phase": "night",
        "window_id": "night:2",
        "schedule_point": SchedulePoint.NIGHT_ACTION,
        "actor_seat": 1,
        "actor_role_id": "wolf-killer-werewolf",
        "actor_alive": True,
        "resources": {"fang": 1, "permission": True},
        "action_key": "pipeline-game:2:night:1:kill",
        "counters": {"window": 0, "round": 0, "game": 0},
        "facts": {"alive_seats": (1, 2, 3)},
    }
    values.update(changes)
    return PipelineActionContext(**values)


def pipeline_contract(**changes: object) -> PipelineActionContract:
    values: dict[str, object] = {
        "contract_id": "werewolf-kill",
        "schedule_point": SchedulePoint.NIGHT_ACTION,
        "order": 10,
        "action_types": ("kill", "pass"),
        "actions_requiring_target": frozenset({"kill"}),
        "fallback_action_type": "pass",
        "required_resources": {"fang": 1, "permission": 1},
        "per_window_limit": 1,
        "per_round_limit": 2,
        "per_game_limit": 3,
    }
    values.update(changes)
    return PipelineActionContract(**values)


def pipeline_command(
    action_type: str = "kill", target_seat: int | None = 2
) -> PipelineActionCommand:
    return PipelineActionCommand(
        action_type=action_type, target_seat=target_seat, reasoning="reason"
    )


@pytest.fixture
def validator() -> ActionValidator:
    return ActionValidator()


def violation_codes(violations: tuple[RuleViolation, ...]) -> tuple[str, ...]:
    return tuple(item.code for item in violations)


def test_pipeline_validate_is_pure_and_appends_hook_violations(validator):
    PIPELINE_HOOK_CALLS.clear()
    contract = pipeline_contract(validate=pipeline_valid_hook)
    context = pipeline_context(contract=contract)
    command = pipeline_command()
    before = (context.to_json(), contract.to_json(), command.to_json())

    first = validator.validate(context, contract, command)
    second = validator.validate(context, contract, command)

    assert violation_codes(first) == ("role_rule", "role_rule_2")
    assert second == first
    assert PIPELINE_HOOK_CALLS == [(context, command), (context, command)]
    assert (context.to_json(), contract.to_json(), command.to_json()) == before


def test_pipeline_validate_allows_actor_as_target(validator):
    assert validator.validate(
        pipeline_context(actor_seat=1), pipeline_contract(), pipeline_command(target_seat=1)
    ) == ()


@pytest.mark.parametrize(
    ("facts", "target", "expected"),
    [
        ({"alive_seats": (1, 2, 3)}, 99, "target_not_alive"),
        ({"alive_seats": (1, 3)}, 2, "target_not_alive"),
        ({}, 2, "invalid_alive_seats"),
        ({"alive_seats": (1, True)}, 2, "invalid_alive_seats"),
    ],
)
def test_pipeline_validate_rejects_unknown_dead_or_malformed_alive_seats(
    validator, facts, target, expected
):
    assert expected in violation_codes(
        validator.validate(
            pipeline_context(facts=facts), pipeline_contract(), pipeline_command(target_seat=target)
        )
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (pipeline_command("kill", None), "target_required"),
        (pipeline_command("kill", -1), "invalid_target"),
        (pipeline_command("pass", 2), "target_forbidden"),
        (pipeline_command("dance", None), "action_not_allowed"),
    ],
)
def test_pipeline_validate_rejects_command_shape(validator, command, expected):
    assert expected in violation_codes(
        validator.validate(pipeline_context(), pipeline_contract(), command)
    )


@pytest.mark.parametrize(
    ("counter", "value", "expected"),
    [
        ("window", 1, "window_limit_reached"),
        ("round", 2, "round_limit_reached"),
        ("game", 3, "game_limit_reached"),
    ],
)
def test_pipeline_validate_enforces_each_limit_at_boundary(
    validator, counter, value, expected
):
    context = pipeline_context(counters={"window": 0, "round": 0, "game": 0, counter: value})
    assert expected in violation_codes(
        validator.validate(context, pipeline_contract(), pipeline_command())
    )


def test_pipeline_validate_ignores_optional_limits_and_accepts_below_limits(validator):
    contract = pipeline_contract(per_round_limit=None, per_game_limit=None)
    context = pipeline_context(
        contract=contract, counters={"window": 0, "round": 999, "game": 999}
    )
    assert validator.validate(context, contract, pipeline_command()) == ()


@pytest.mark.parametrize(
    ("resources", "expected"),
    [
        ({"fang": 1, "permission": True}, ()),
        ({"fang": 0, "permission": True}, ("resource_insufficient",)),
        ({"permission": True}, ("resource_missing",)),
        ({"fang": True, "permission": True}, ()),
        ({"fang": False, "permission": True}, ("resource_insufficient",)),
        ({"fang": "1", "permission": True}, ("resource_invalid",)),
        ({"fang": 2_147_483_648, "permission": True}, ("resource_invalid",)),
    ],
)
def test_pipeline_validate_checks_required_resource_quantity(
    validator, resources, expected
):
    assert violation_codes(
        validator.validate(
            pipeline_context(resources=resources), pipeline_contract(), pipeline_command()
        )
    ) == expected


@pytest.mark.parametrize(
    ("context_changes", "expected"),
    [
        ({"actor_alive": False}, "actor_not_alive"),
        ({"actor_seat": 0}, "invalid_actor_seat"),
        ({"actor_role_id": ""}, "invalid_actor_role"),
        ({"revision": -1}, "invalid_revision"),
        ({"round_number": -1}, "invalid_round"),
        ({"phase": ""}, "invalid_phase"),
        ({"action_key": ""}, "invalid_action_key"),
        ({"schedule_point": SchedulePoint.DAY_ACTION}, "schedule_point_mismatch"),
    ],
)
def test_pipeline_validate_checks_context_binding(validator, context_changes, expected):
    assert expected in violation_codes(
        validator.validate(
            pipeline_context(**context_changes), pipeline_contract(), pipeline_command()
        )
    )


@pytest.mark.parametrize(
    ("context_changes", "expected"),
    [
        ({"contract_id": "different-contract"}, "contract_id_mismatch"),
        ({"contract_version": 2}, "contract_version_mismatch"),
    ],
)
def test_pipeline_validate_binds_context_to_exact_contract(
    validator, context_changes, expected
):
    assert expected in violation_codes(
        validator.validate(
            pipeline_context(**context_changes), pipeline_contract(), pipeline_command()
        )
    )


def test_pipeline_validate_rejects_same_identity_replacement_contract_without_hook(
    validator,
):
    PIPELINE_HOOK_CALLS.clear()
    original = pipeline_contract()
    replacement = pipeline_contract(
        action_types=("kill", "pass", "reveal"),
        required_resources={},
        per_window_limit=99,
        per_round_limit=99,
        per_game_limit=99,
        validate=pipeline_valid_hook,
    )
    context = pipeline_context(contract_digest=original.stable_digest())

    violations = validator.validate(context, replacement, pipeline_command())

    assert violation_codes(violations) == ("contract_digest_mismatch",)
    assert PIPELINE_HOOK_CALLS == []


def test_pipeline_response_contract_allows_dead_actor_for_reaction_validation(validator):
    contract = pipeline_contract(response_event_types=frozenset({"PLAYER_DIED"}))

    assert validator.validate(
        pipeline_context(contract=contract, actor_alive=False), contract, pipeline_command()
    ) == ()


def test_pipeline_generic_violation_prevents_validate_hook_execution(validator):
    PIPELINE_HOOK_CALLS.clear()
    contract = pipeline_contract(validate=pipeline_valid_hook)
    violations = validator.validate(
        pipeline_context(contract=contract),
        contract,
        pipeline_command("dance", None),
    )

    assert PIPELINE_HOOK_CALLS == []
    assert violation_codes(violations) == ("action_not_allowed",)


@pytest.mark.parametrize(
    ("counters", "expected"),
    [
        ({"window": 0, "surprise": 1}, "invalid_counter"),
    ],
)
def test_pipeline_validate_rejects_malformed_counters(validator, counters, expected):
    assert expected in violation_codes(
        validator.validate(
            pipeline_context(counters=counters), pipeline_contract(), pipeline_command()
        )
    )


def test_pipeline_validate_without_hook_returns_generic_violations_only(validator):
    assert validator.validate(
        pipeline_context(), pipeline_contract(validate=None), pipeline_command()
    ) == ()


@pytest.mark.parametrize("hook", [pipeline_bad_list_hook, pipeline_bad_item_hook])
def test_pipeline_validate_rejects_invalid_hook_return(validator, hook):
    contract = pipeline_contract(validate=hook)
    with pytest.raises(TypeError, match="exact tuple of RuleViolation"):
        validator.validate(
            pipeline_context(contract=contract), contract, pipeline_command()
        )


def test_pipeline_validate_propagates_hook_exception(validator):
    contract = pipeline_contract(validate=pipeline_raising_hook)
    with pytest.raises(RuntimeError, match="hook exploded"):
        validator.validate(
            pipeline_context(contract=contract), contract, pipeline_command()
        )


@pytest.mark.parametrize(
    ("position", "value", "expected"),
    [
        ("context", object(), "context must be an exact ActionContext"),
        ("contract", object(), "contract must be an exact ActionContract"),
        ("command", object(), "command must be an exact ActionCommand"),
    ],
)
def test_pipeline_validate_requires_exact_pipeline_types(
    validator, position, value, expected
):
    arguments = {
        "context": pipeline_context(),
        "contract": pipeline_contract(),
        "command": pipeline_command(),
    }
    arguments[position] = value
    with pytest.raises(TypeError, match=expected):
        validator.validate(**arguments)


