import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidationError, ActionValidator
from app.models.contracts import AcceptedAction, ActionCommand, ActionContract, ActionRequest
from app.models.game import Camp, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext as PipelineActionContext,
    ActionContract as PipelineActionContract,
    RuleViolation,
    SchedulePoint,
)
from app.roles.registry import builtin_registry


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


def pipeline_context(**changes: object) -> PipelineActionContext:
    values: dict[str, object] = {
        "game_id": "pipeline-game",
        "revision": 3,
        "config_version": "registry-v1",
        "contract_id": "werewolf-kill",
        "contract_version": 1,
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


def make_state() -> GameState:
    state = GameState(game_id="validator", phase=GamePhase.NIGHT, round_number=1)
    state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", Camp.WEREWOLF.value),
        2: PlayerState(
            2, "wolf-killer-witch", Camp.GOOD.value,
            has_antidote=True, has_poison=True,
        ),
        3: PlayerState(3, "wolf-killer-villager", Camp.GOOD.value),
        4: PlayerState(4, "wolf-killer-villager", Camp.GOOD.value),
    }
    return state


def request_for(state: GameState, seat: int) -> ActionRequest:
    return next(
        request
        for request in builtin_registry.build_requests(
            state, {player_seat: object() for player_seat in state.players}, state.phase
        )
        if request.actor_seat == seat
    )


@pytest.fixture
def state() -> GameState:
    return make_state()


@pytest.fixture
def validator() -> ActionValidator:
    return ActionValidator()


def violation_codes(violations: tuple[RuleViolation, ...]) -> tuple[str, ...]:
    return tuple(item.code for item in violations)


def test_pipeline_validate_is_pure_and_appends_hook_violations(validator):
    PIPELINE_HOOK_CALLS.clear()
    context = pipeline_context()
    contract = pipeline_contract(validate=pipeline_valid_hook)
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
    context = pipeline_context(counters={"window": 0, "round": 999, "game": 999})
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


def test_pipeline_response_contract_allows_dead_actor_for_reaction_validation(validator):
    contract = pipeline_contract(response_event_types=frozenset({"PLAYER_DIED"}))

    assert validator.validate(
        pipeline_context(actor_alive=False), contract, pipeline_command()
    ) == ()


def test_pipeline_generic_violation_prevents_validate_hook_execution(validator):
    PIPELINE_HOOK_CALLS.clear()
    violations = validator.validate(
        pipeline_context(),
        pipeline_contract(validate=pipeline_valid_hook),
        pipeline_command("dance", None),
    )

    assert PIPELINE_HOOK_CALLS == []
    assert violation_codes(violations) == ("action_not_allowed",)


@pytest.mark.parametrize(
    ("counters", "expected"),
    [
        ({"window": -1}, "invalid_counter"),
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
    with pytest.raises(TypeError, match="exact tuple of RuleViolation"):
        validator.validate(
            pipeline_context(), pipeline_contract(validate=hook), pipeline_command()
        )


def test_pipeline_validate_propagates_hook_exception(validator):
    with pytest.raises(RuntimeError, match="hook exploded"):
        validator.validate(
            pipeline_context(), pipeline_contract(validate=pipeline_raising_hook), pipeline_command()
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


def test_validator_accepts_werewolf_self_kill_without_target_whitelist(state, validator):
    accepted = validator.validate_and_accept(
        state, request_for(state, 1),
        {"action_type": "kill", "target_seat": 1, "reasoning": "x"},
    )

    assert accepted.command.target_seat == 1


def test_validator_rejects_save_outside_wolf_target_without_consuming_antidote(state, validator):
    state.last_wolf_kill_target = 3

    with pytest.raises(ActionValidationError, match="wolf target"):
        validator.validate_and_accept(
            state, request_for(state, 2),
            {"action_type": "save", "target_seat": 4, "reasoning": "x"},
        )

    assert state.players[2].has_antidote is True
    assert state.accepted_action_keys == set()


def test_validator_rejects_a_second_witch_request_with_the_same_key(state, validator):
    request = request_for(state, 2)
    validator.validate_and_accept(
        state, request,
        {"action_type": "pass", "target_seat": None, "reasoning": "x"},
    )

    with pytest.raises(ActionValidationError, match="already accepted"):
        validator.validate_and_accept(
            state, request,
            {"action_type": "poison", "target_seat": 3, "reasoning": "x"},
        )

    assert state.players[2].has_poison is True


@pytest.mark.parametrize("target", [0, 99])
def test_validator_rejects_missing_poison_target_without_consuming_poison(state, validator, target):
    with pytest.raises(ActionValidationError, match="target"):
        validator.validate_and_accept(
            state, request_for(state, 2),
            {"action_type": "poison", "target_seat": target, "reasoning": "x"},
        )

    assert state.players[2].has_poison is True
    assert state.accepted_action_keys == set()


def test_validator_rejects_dead_poison_target_without_consuming_poison(state, validator):
    state.players[3].is_alive = False

    with pytest.raises(ActionValidationError, match="alive"):
        validator.validate_and_accept(
            state, request_for(state, 2),
            {"action_type": "poison", "target_seat": 3, "reasoning": "x"},
        )

    assert state.players[2].has_poison is True


def test_validator_rejects_pass_with_target(state, validator):
    with pytest.raises(ActionValidationError, match="no target"):
        validator.validate_and_accept(
            state, request_for(state, 1),
            {"action_type": "pass", "target_seat": 3, "reasoning": "x"},
        )


def test_validator_rejects_invalid_pydantic_payload(state, validator):
    with pytest.raises(ActionValidationError, match="invalid action command"):
        validator.validate_and_accept(
            state, request_for(state, 1),
            {"action_type": "pass", "target_seat": None, "reasoning": "x", "extra": True},
        )


def test_validator_rejects_wrong_request_phase(state, validator):
    request = request_for(state, 1)
    request = ActionRequest(
        actor_seat=request.actor_seat, role_id=request.role_id, contract=request.contract,
        phase=GamePhase.DAWN, round_id=request.round_id,
        idempotency_key=request.idempotency_key,
    )

    with pytest.raises(ActionValidationError, match="phase"):
        validator.validate_and_accept(
            state, request,
            {"action_type": "pass", "target_seat": None, "reasoning": "x"},
        )


def test_validator_rejects_request_whose_contract_has_a_different_phase(state, validator):
    state.phase = GamePhase.VOTE_CASTING
    request = request_for(make_state(), 1)
    request = ActionRequest(
        actor_seat=request.actor_seat, role_id=request.role_id, contract=request.contract,
        phase=GamePhase.VOTE_CASTING, round_id=request.round_id,
        idempotency_key=request.idempotency_key,
    )

    with pytest.raises(ActionValidationError, match="contract phase"):
        validator.validate_and_accept(
            state, request,
            {"action_type": "pass", "target_seat": None, "reasoning": "x"},
        )


def test_validator_rejects_missing_actor(state, validator):
    request = request_for(state, 1)
    request = ActionRequest(
        actor_seat=99, role_id=request.role_id, contract=request.contract,
        phase=request.phase, round_id=request.round_id,
        idempotency_key=request.idempotency_key,
    )

    with pytest.raises(ActionValidationError, match="actor"):
        validator.validate_and_accept(
            state, request,
            {"action_type": "pass", "target_seat": None, "reasoning": "x"},
        )


def test_validator_rejects_actor_with_wrong_role(state, validator):
    request = request_for(state, 1)
    request = ActionRequest(
        actor_seat=request.actor_seat, role_id="wolf-killer-witch", contract=request.contract,
        phase=request.phase, round_id=request.round_id,
        idempotency_key=request.idempotency_key,
    )

    with pytest.raises(ActionValidationError, match="role"):
        validator.validate_and_accept(
            state, request,
            {"action_type": "pass", "target_seat": None, "reasoning": "x"},
        )


def test_validator_rejects_action_outside_contract(state, validator):
    with pytest.raises(ActionValidationError, match="not permitted"):
        validator.validate_and_accept(
            state, request_for(state, 1),
            {"action_type": "poison", "target_seat": 3, "reasoning": "x"},
        )


def test_validator_requires_target_for_targeted_action(state, validator):
    with pytest.raises(ActionValidationError, match="requires a target"):
        validator.validate_and_accept(
            state, request_for(state, 1),
            {"action_type": "kill", "target_seat": None, "reasoning": "x"},
        )


def test_validator_consumes_antidote_only_after_accepting_valid_save(state, validator):
    state.last_wolf_kill_target = 3

    validator.validate_and_accept(
        state, request_for(state, 2),
        {"action_type": "save", "target_seat": 3, "reasoning": "x"},
    )

    assert state.players[2].has_antidote is False


def test_resolver_does_not_consume_an_accepted_save_twice(state, validator):
    kill = validator.validate_and_accept(
        state, request_for(state, 1),
        {"action_type": "kill", "target_seat": 3, "reasoning": "x"},
    )
    state.last_wolf_kill_target = 3
    save = validator.validate_and_accept(
        state, request_for(state, 2),
        {"action_type": "save", "target_seat": 3, "reasoning": "x"},
    )

    assert ActionResolver().resolve(state, [kill, save]) == []
    assert state.players[2].has_antidote is False


def test_validator_rejects_save_without_antidote(state, validator):
    state.last_wolf_kill_target = 3
    state.players[2].has_antidote = False

    with pytest.raises(ActionValidationError, match="antidote"):
        validator.validate_and_accept(
            state, request_for(state, 2),
            {"action_type": "save", "target_seat": 3, "reasoning": "x"},
        )


def test_validator_rejects_poison_without_poison(state, validator):
    state.players[2].has_poison = False

    with pytest.raises(ActionValidationError, match="poison"):
        validator.validate_and_accept(
            state, request_for(state, 2),
            {"action_type": "poison", "target_seat": 3, "reasoning": "x"},
        )


def test_validator_consumes_poison_only_after_accepting_valid_poison(state, validator):
    validator.validate_and_accept(
        state, request_for(state, 2),
        {"action_type": "poison", "target_seat": 3, "reasoning": "x"},
    )

    assert state.players[2].has_poison is False


def test_resolver_ignores_zero_target_without_random_wolf_kill(state):
    request = request_for(state, 1)
    deaths = ActionResolver().resolve(
        state,
        [
            AcceptedAction(
                request=request,
                command=ActionCommand(action_type="kill", target_seat=0, reasoning="x"),
            ),
            AcceptedAction(
                request=request,
                command=ActionCommand(action_type="kill", target_seat=None, reasoning="x"),
            ),
        ],
    )

    assert deaths == []
    assert state.last_wolf_kill_target is None


def test_safe_fallback_accepts_contract_fallback_once(state, validator):
    request = request_for(state, 1)

    accepted = validator.safe_fallback(state, request)

    assert accepted.command.action_type == "pass"
    assert accepted.command.target_seat is None
    with pytest.raises(ActionValidationError, match="already accepted"):
        validator.safe_fallback(state, request)


def test_safe_fallback_uses_abstain_for_vote_contract(state, validator):
    state.phase = GamePhase.VOTE_CASTING
    contract = ActionContract(
        contract_id="vote",
        phase=GamePhase.VOTE_CASTING,
        action_types=("vote", "abstain"),
        actions_requiring_target=frozenset({"vote"}),
        resolution_priority=1,
        fallback_action_type="abstain",
    )
    request = ActionRequest(
        actor_seat=1,
        role_id=state.players[1].role,
        contract=contract,
        phase=GamePhase.VOTE_CASTING,
        round_id=state.round_number,
        idempotency_key="vote-fallback",
    )

    accepted = validator.safe_fallback(state, request)

    assert accepted.command.action_type == "abstain"
    assert accepted.command.target_seat is None
