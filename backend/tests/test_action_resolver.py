import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.models.actions import NightAction
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
            make_player(2, "wolf-killer-villager", "good", is_alive=False),
        ])

        assert ActionResolver().resolve_hunter_shoot(
            state, 1, NightAction(player_seat=1, action_type="shoot", target_seat=2)
        ) is None
        assert state.players[1].has_gun is True
