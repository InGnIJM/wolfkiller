import pytest
from app.core.action_resolver import ActionResolver
from app.models.game import GameState, GameConfig, PlayerState
from app.models.actions import NightAction


def make_player(seat: int, role: str, camp: str, **kwargs) -> PlayerState:
    p = PlayerState(seat_number=seat, role=role, camp=camp)
    if "witch" in role:
        p.has_antidote = kwargs.get("has_antidote", True)
        p.has_poison = kwargs.get("has_poison", True)
    if "hunter" in role:
        p.has_gun = kwargs.get("has_gun", True)
    return p


def make_state(players: list[PlayerState], round_number: int = 1) -> GameState:
    state = GameState(game_id="test", config=GameConfig(), round_number=round_number)
    for p in players:
        state.players[p.seat_number] = p
    return state


class TestActionResolver:
    def test_seer_check_good_player(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-seer", "good"),
            make_player(2, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="check", target_seat=2)]

        deaths = resolver.resolve(state, actions)

        assert len(deaths) == 0
        assert len(state.players[1].check_results) == 1
        assert state.players[1].check_results[0]["result"] == "good"

    def test_seer_check_werewolf(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-seer", "good"),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="check", target_seat=2)]

        resolver.resolve(state, actions)

        assert state.players[1].check_results[0]["result"] == "werewolf"

    def test_wolf_kill_majority(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
            make_player(3, "wolf-killer-werewolf", "werewolf"),
            make_player(4, "wolf-killer-villager", "good"),
            make_player(5, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [
            NightAction(player_seat=1, action_type="kill", target_seat=4),
            NightAction(player_seat=2, action_type="kill", target_seat=4),
            NightAction(player_seat=3, action_type="kill", target_seat=5),
        ]

        deaths = resolver.resolve(state, actions)

        assert len(deaths) == 1
        assert deaths[0].player_seat == 4
        assert deaths[0].cause == "wolf_kill"

    def test_witch_save_prevents_death(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good", has_antidote=True),
            make_player(3, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [
            NightAction(player_seat=1, action_type="kill", target_seat=3),
            NightAction(player_seat=2, action_type="save"),
        ]

        deaths = resolver.resolve(state, actions)

        assert len(deaths) == 0
        assert state.players[3].is_alive is True
        assert state.players[2].has_antidote is False

    def test_witch_poison_kills(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-witch", "good", has_poison=True),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="poison", target_seat=2)]

        deaths = resolver.resolve(state, actions)

        assert len(deaths) == 1
        assert deaths[0].cause == "poison"
        assert state.players[1].has_poison is False

    def test_witch_cant_save_without_antidote(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good", has_antidote=False, has_poison=True),
            make_player(3, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [
            NightAction(player_seat=1, action_type="kill", target_seat=3),
            NightAction(player_seat=2, action_type="save"),
        ]

        deaths = resolver.resolve(state, actions)

        assert len(deaths) == 1
        assert deaths[0].cause == "wolf_kill"

    def test_wolf_vote_tie_random(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [
            NightAction(player_seat=1, action_type="kill", target_seat=3),
            NightAction(player_seat=2, action_type="kill", target_seat=4),
        ]

        deaths = resolver.resolve(state, actions)

        assert len(deaths) == 1
        assert deaths[0].player_seat in (3, 4)

    def test_wolf_no_targets(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="kill", target_seat=None)]

        deaths = resolver.resolve(state, actions)
        assert len(deaths) == 0

    def test_wolf_kill_already_dead(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good", is_alive=False),
        ]
        state = make_state(players)
        state.players[2].is_alive = False
        actions = [NightAction(player_seat=1, action_type="kill", target_seat=2)]

        deaths = resolver.resolve(state, actions)
        assert len(deaths) == 0

    def test_witch_poison_no_target(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-witch", "good", has_poison=True),
            make_player(2, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="poison", target_seat=None)]

        deaths = resolver.resolve(state, actions)
        assert len(deaths) == 0

    def test_seer_check_dead_target(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-seer", "good"),
            make_player(2, "wolf-killer-villager", "good", is_alive=False),
        ]
        state = make_state(players)
        state.players[2].is_alive = False
        actions = [NightAction(player_seat=1, action_type="check", target_seat=2)]

        deaths = resolver.resolve(state, actions)
        assert len(deaths) == 0
        assert len(state.players[1].check_results) == 0

    def test_hunter_death_by_wolf_kill(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-hunter", "good", has_gun=True),
            make_player(3, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="kill", target_seat=2)]

        deaths = resolver.resolve(state, actions)
        # hunter should die from wolf kill
        assert any(d.player_seat == 2 and d.cause == "wolf_kill" for d in deaths)

    def test_witch_used_poison_then_cant_poison_again(self):
        resolver = ActionResolver()
        p1 = make_player(1, "wolf-killer-witch", "good", has_antidote=False, has_poison=True)
        state = make_state([p1, make_player(2, "wolf-killer-villager", "good")])
        actions = [NightAction(player_seat=1, action_type="poison", target_seat=2)]
        deaths = resolver.resolve(state, actions)
        assert not state.players[1].has_poison

    def test_seer_check_none_target(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-seer", "good"),
            make_player(2, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="check", target_seat=None)]

        deaths = resolver.resolve(state, actions)
        assert len(deaths) == 0
        assert len(state.players[1].check_results) == 0

    def test_witch_action_from_nonexistent_player(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=99, action_type="save")]

        deaths = resolver.resolve(state, actions)
        assert len(deaths) == 0

    def test_hunter_dies_from_poison_no_gun(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-witch", "good", has_poison=True),
            make_player(2, "wolf-killer-hunter", "good", has_gun=True),
            make_player(3, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="poison", target_seat=2)]

        deaths = resolver.resolve(state, actions)
        assert any(d.player_seat == 2 and d.cause == "poison" for d in deaths)

    def test_wolf_kill_nonexistent_target(self):
        resolver = ActionResolver()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        actions = [NightAction(player_seat=1, action_type="kill", target_seat=99)]

        deaths = resolver.resolve(state, actions)
        assert len(deaths) == 0
