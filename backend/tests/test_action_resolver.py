from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidationError, ActionValidator
from app.models.actions import DeathReport, NightAction
from app.models.contracts import AcceptedAction, ActionCommand
from app.models.game import Camp, GameConfig, GamePhase, GameState, PlayerState
from app.roles.registry import builtin_registry


def make_player(seat: int, role: str, camp: str, **kwargs) -> PlayerState:
    player = PlayerState(
        seat_number=seat, role=role, camp=camp, is_alive=kwargs.get("is_alive", True)
    )
    if "witch" in role:
        player.has_antidote = kwargs.get("has_antidote", True)
        player.has_poison = kwargs.get("has_poison", True)
    if "hunter" in role:
        player.has_gun = kwargs.get("has_gun", True)
    return player


def make_state(players: list[PlayerState]) -> GameState:
    state = GameState(
        game_id="test", config=GameConfig(), phase=GamePhase.NIGHT, round_number=1
    )
    state.players = {player.seat_number: player for player in players}
    return state


def request_for(state: GameState, seat: int):
    return next(
        request
        for request in builtin_registry.build_requests(
            state, {player_seat: object() for player_seat in state.players}, state.phase
        )
        if request.actor_seat == seat
    )


def accept(state: GameState, seat: int, action_type: str, target_seat: int | None = None):
    return ActionValidator().validate_and_accept(
        state,
        request_for(state, seat),
        {"action_type": action_type, "target_seat": target_seat, "reasoning": "x"},
    )


class TestActionResolver:
    def test_resolver_rejects_unaccepted_night_action(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
        ])

        with pytest.raises(TypeError, match="AcceptedAction"):
            ActionResolver().resolve(
                state, [NightAction(player_seat=1, action_type="kill", target_seat=2)]
            )

    def test_resolves_accepted_wolf_majority_without_random_tie_breaking(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
            make_player(3, "wolf-killer-werewolf", "werewolf"),
            make_player(4, "wolf-killer-villager", "good"),
            make_player(5, "wolf-killer-villager", "good"),
        ])

        deaths = ActionResolver().resolve(state, [
            accept(state, 1, "kill", 4),
            accept(state, 2, "kill", 4),
            accept(state, 3, "kill", 5),
        ])

        assert [(death.player_seat, death.cause) for death in deaths] == [(4, "wolf_kill")]

    def test_resolves_accepted_seer_check(self):
        state = make_state([
            make_player(1, "wolf-killer-seer", "good"),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
        ])

        assert ActionResolver().resolve(state, [accept(state, 1, "check", 2)]) == []
        assert state.players[1].check_results == [
            {"target_seat": 2, "result": "werewolf", "round": 1}
        ]

    def test_resolves_accepted_save_at_the_recorded_wolf_target_without_second_consumption(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good", has_antidote=True),
            make_player(3, "wolf-killer-villager", "good"),
        ])
        kill = accept(state, 1, "kill", 3)
        state.last_wolf_kill_target = 3
        save = accept(state, 2, "save", 3)

        assert ActionResolver().resolve(state, [kill, save]) == []
        assert state.players[2].has_antidote is False

    def test_resolver_does_not_apply_an_accepted_save_for_a_different_target(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good"),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-villager", "good"),
        ])
        kill = accept(state, 1, "kill", 3)
        wrong_save = AcceptedAction(
            request=request_for(state, 2),
            command=ActionCommand(action_type="save", target_seat=4, reasoning="x"),
        )

        deaths = ActionResolver().resolve(state, [kill, wrong_save])

        assert [(death.player_seat, death.cause) for death in deaths] == [(3, "wolf_kill")]

    def test_resolves_accepted_poison_without_second_consumption(self):
        state = make_state([
            make_player(1, "wolf-killer-witch", "good", has_poison=True),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
        ])

        deaths = ActionResolver().resolve(state, [accept(state, 1, "poison", 2)])

        assert [(death.player_seat, death.cause) for death in deaths] == [(2, "poison")]
        assert state.players[1].has_poison is False

    def test_rejects_multiple_witch_potion_actions_before_resolving_them(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good", has_antidote=False, has_poison=True),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-villager", "good"),
        ])
        kill = accept(state, 1, "kill", 3)
        witch_request = request_for(state, 2)
        save = AcceptedAction(
            request=witch_request,
            command=ActionCommand(action_type="save", target_seat=3, reasoning="x"),
        )
        poison = AcceptedAction(
            request=witch_request,
            command=ActionCommand(action_type="poison", target_seat=4, reasoning="x"),
        )

        with pytest.raises(ActionValidationError, match="multiple witch actions"):
            ActionResolver().resolve(state, [kill, save, poison])

        assert state.players[3].is_alive is True
        assert state.players[4].is_alive is True
        assert state.players[2].has_antidote is False
        assert state.players[2].has_poison is True

    @pytest.mark.parametrize(
        ("first_action_type", "second_action_type"),
        [
            ("pass", "pass"),
            ("pass", "poison"),
            ("pass", "save"),
        ],
    )
    def test_rejects_multiple_witch_actions_including_pass_before_state_changes(
        self, first_action_type: str, second_action_type: str
    ):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good"),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-villager", "good"),
        ])
        kill = accept(state, 1, "kill", 3)
        witch_request = request_for(state, 2)

        def witch_action(action_type: str) -> AcceptedAction:
            target_seat = {"save": 3, "poison": 4}.get(action_type)
            return AcceptedAction(
                request=witch_request,
                command=ActionCommand(
                    action_type=action_type, target_seat=target_seat, reasoning="x"
                ),
            )

        with pytest.raises(ActionValidationError, match="multiple witch actions"):
            ActionResolver().resolve(
                state,
                [kill, witch_action(first_action_type), witch_action(second_action_type)],
            )

        assert state.last_wolf_kill_target is None
        assert state.players[3].is_alive is True
        assert state.players[4].is_alive is True

    def test_allows_duplicate_passes_from_non_witch_contracts(self):
        state = make_state([
            make_player(1, "wolf-killer-seer", "good"),
        ])
        seer_request = request_for(state, 1)
        actions = [
            AcceptedAction(
                request=seer_request,
                command=ActionCommand(action_type="pass", target_seat=None, reasoning="x"),
            ),
            AcceptedAction(
                request=seer_request,
                command=ActionCommand(action_type="pass", target_seat=None, reasoning="x"),
            ),
        ]

        assert ActionResolver().resolve(state, actions) == []

    @pytest.mark.parametrize("contract_id", ["werewolf_kill", "future_night_action"])
    def test_does_not_treat_forged_potions_from_non_witch_contract_as_witch_actions(
        self, contract_id: str
    ):
        state = make_state([
            make_player(1, "wolf-killer-witch", "good"),
        ])
        witch_request = request_for(state, 1)
        forged_request = replace(
            witch_request,
            contract=replace(witch_request.contract, contract_id=contract_id),
        )
        actions = [
            AcceptedAction(
                request=forged_request,
                command=ActionCommand(action_type="save", target_seat=2, reasoning="x"),
            ),
            AcceptedAction(
                request=forged_request,
                command=ActionCommand(action_type="poison", target_seat=3, reasoning="x"),
            ),
        ]

        assert ActionResolver().resolve(state, actions) == []

    def test_does_not_treat_witch_contract_from_non_witch_role_as_witch_action(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
        ])
        werewolf_request = request_for(state, 1)
        witch_contract = builtin_registry.require("wolf-killer-witch").contracts[0]
        forged_request = replace(werewolf_request, contract=witch_contract)
        actions = [
            AcceptedAction(
                request=forged_request,
                command=ActionCommand(action_type="save", target_seat=2, reasoning="x"),
            ),
            AcceptedAction(
                request=forged_request,
                command=ActionCommand(action_type="poison", target_seat=3, reasoning="x"),
            ),
        ]

        assert ActionResolver().resolve(state, actions) == []

    def test_resolves_single_witch_pass(self):
        state = make_state([
            make_player(1, "wolf-killer-witch", "good"),
        ])

        assert ActionResolver().resolve(state, [accept(state, 1, "pass")]) == []
        assert state.players[1].has_antidote is True
        assert state.players[1].has_poison is True

    def test_ignores_zero_and_none_targets_even_in_accepted_action_objects(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
        ])
        request = request_for(state, 1)
        malformed = [
            AcceptedAction(
                request=request,
                command=ActionCommand(action_type="kill", target_seat=0, reasoning="x"),
            ),
            AcceptedAction(
                request=request,
                command=ActionCommand(action_type="kill", target_seat=None, reasoning="x"),
            ),
        ]

        assert ActionResolver().resolve(state, malformed) == []
        assert state.last_wolf_kill_target is None

    def test_hunter_shoot_does_not_consume_gun_for_invalid_target(self):
        state = make_state([
            make_player(1, "wolf-killer-hunter", "good", has_gun=True),
            make_player(2, "wolf-killer-villager", "good"),
        ])
        state.phase = GamePhase.DAWN
        accepted = accept(state, 1, "shoot", 2)
        state.players[2].is_alive = False

        with pytest.raises(TypeError, match="AcceptedAction"):
            ActionResolver().resolve_hunter_shoot(
                state, 1, NightAction(player_seat=1, action_type="shoot", target_seat=2)
            )

        assert ActionResolver().resolve_hunter_shoot(
            state, 1, accepted
        ) is None
        assert state.players[1].has_gun is True

    def test_hunter_shoot_rejects_wrong_actor_type_or_target_before_valid_shot(self):
        state = make_state([
            make_player(1, "wolf-killer-hunter", "good", has_gun=True),
            make_player(2, "wolf-killer-villager", "good"),
        ])
        state.phase = GamePhase.DAWN
        shoot = accept(state, 1, "shoot", 2)
        resolver = ActionResolver()

        wrong_actor = AcceptedAction(
            request=replace(shoot.request, actor_seat=2), command=shoot.command
        )
        wrong_type = AcceptedAction(
            request=shoot.request,
            command=ActionCommand(action_type="pass", target_seat=None, reasoning="x"),
        )
        no_target = AcceptedAction(
            request=shoot.request,
            command=ActionCommand(action_type="shoot", target_seat=None, reasoning="x"),
        )

        assert resolver.resolve_hunter_shoot(state, 3, shoot) is None
        assert resolver.resolve_hunter_shoot(state, 1, wrong_actor) is None
        assert resolver.resolve_hunter_shoot(state, 1, wrong_type) is None
        assert resolver.resolve_hunter_shoot(state, 1, no_target) is None
        assert state.players[1].has_gun is True

        death = resolver.resolve_hunter_shoot(state, 1, shoot)

        assert death == DeathReport(player_seat=2, cause="hunter_shot", round_number=1)
        assert state.players[1].has_gun is False
        assert state.players[2].is_alive is False

    def test_identifies_an_armed_hunter_killed_by_non_poison(self):
        state = make_state([
            make_player(1, "wolf-killer-hunter", "good", has_gun=True),
        ])
        resolver = ActionResolver()

        assert resolver.has_hunter_died(
            state, [DeathReport(player_seat=1, cause="poison", round_number=1)]
        ) is None
        assert resolver.has_hunter_died(
            state, [DeathReport(player_seat=1, cause="wolf_kill", round_number=1)]
        ) == 1

    def test_ignores_accepted_seer_checks_without_a_live_target(self):
        state = make_state([
            make_player(1, "wolf-killer-seer", "good"),
        ])
        seer_request = request_for(state, 1)
        no_target = AcceptedAction(
            request=seer_request,
            command=ActionCommand(action_type="check", target_seat=None, reasoning="x"),
        )
        missing_target = AcceptedAction(
            request=seer_request,
            command=ActionCommand(action_type="check", target_seat=2, reasoning="x"),
        )

        assert ActionResolver().resolve(state, [no_target]) == []
        assert ActionResolver().resolve(state, [missing_target]) == []
        assert state.players[1].check_results == []

    def test_ignores_malformed_poison_and_non_check_actions(self):
        state = make_state([
            make_player(1, "wolf-killer-witch", "good"),
            make_player(2, "wolf-killer-seer", "good"),
        ])
        malformed_poison = AcceptedAction(
            request=request_for(state, 1),
            command=ActionCommand(action_type="poison", target_seat=None, reasoning="x"),
        )

        assert ActionResolver().resolve(state, [malformed_poison]) == []
        ActionResolver()._process_seer_checks(
            state, [NightAction(player_seat=2, action_type="pass", target_seat=None)]
        )
        assert state.players[2].check_results == []
