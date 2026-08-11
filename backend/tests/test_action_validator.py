import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidationError, ActionValidator
from app.models.actions import NightAction
from app.models.contracts import ActionContract, ActionRequest
from app.models.game import Camp, GamePhase, GameState, PlayerState
from app.roles.registry import builtin_registry


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
    deaths = ActionResolver().resolve(
        state,
        [
            NightAction(player_seat=1, action_type="kill", target_seat=0),
            NightAction(player_seat=1, action_type="kill", target_seat=None),
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
