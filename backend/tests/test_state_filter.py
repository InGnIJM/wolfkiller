import pytest
from app.agents.state_filter import StateFilter
from app.models.game import GameState, GameConfig, PlayerState
from app.models.actions import SpeechRecord, DeathReport


def make_player(seat, role, camp, alive=True):
    p = PlayerState(seat_number=seat, role=role, camp=camp, is_alive=alive)
    if "witch" in role:
        p.has_antidote = True
        p.has_poison = True
    if "hunter" in role:
        p.has_gun = True
    return p


class TestStateFilter:
    def test_werewolf_sees_teammates(self):
        sf = StateFilter()
        state = GameState(game_id="test", config=GameConfig())
        state.players = {
            1: make_player(1, "wolf-killer-werewolf", "werewolf"),
            2: make_player(2, "wolf-killer-werewolf", "werewolf"),
            3: make_player(3, "wolf-killer-werewolf", "werewolf"),
            4: make_player(4, "wolf-killer-villager", "good"),
        }

        view = sf.filter_for_role(state, 1, "wolf-killer-werewolf")
        assert view["your_seat"] == 1
        assert set(view["wolf_teammates"]) == {2, 3}

    def test_villager_sees_only_public(self):
        sf = StateFilter()
        state = GameState(game_id="test", config=GameConfig())
        state.players = {
            1: make_player(1, "wolf-killer-villager", "good"),
            2: make_player(2, "wolf-killer-werewolf", "werewolf"),
        }

        view = sf.filter_for_role(state, 1, "wolf-killer-villager")
        assert "wolf_teammates" not in view
        assert "check_results" not in view
        assert len(view["alive_players"]) == 2

    def test_seer_sees_check_results(self):
        sf = StateFilter()
        state = GameState(game_id="test", config=GameConfig())
        p1 = make_player(1, "wolf-killer-seer", "good")
        p1.check_results = [{"target_seat": 2, "result": "werewolf", "round": 1}]
        state.players = {1: p1, 2: make_player(2, "wolf-killer-villager", "good")}

        view = sf.filter_for_role(state, 1, "wolf-killer-seer")
        assert len(view["check_results"]) == 1
        assert view["check_results"][0]["result"] == "werewolf"

    def test_witch_sees_potion_status(self):
        sf = StateFilter()
        state = GameState(game_id="test", config=GameConfig())
        state.players = {
            1: make_player(1, "wolf-killer-witch", "good"),
            2: make_player(2, "wolf-killer-werewolf", "werewolf"),
        }
        state.last_wolf_kill_target = 2

        view = sf.filter_for_role(state, 1, "wolf-killer-witch")
        assert view["has_antidote"] is True
        assert view["has_poison"] is True
        assert view["last_wolf_kill_target"] == 2

    def test_hunter_sees_gun_status(self):
        sf = StateFilter()
        state = GameState(game_id="test", config=GameConfig())
        state.players = {
            1: make_player(1, "wolf-killer-hunter", "good"),
            2: make_player(2, "wolf-killer-villager", "good"),
        }

        view = sf.filter_for_role(state, 1, "wolf-killer-hunter")
        assert view["has_gun"] is True

    def test_dead_players_listed(self):
        sf = StateFilter()
        state = GameState(game_id="test", config=GameConfig())
        p1 = make_player(1, "wolf-killer-villager", "good", alive=False)
        state.players = {
            1: p1,
            2: make_player(2, "wolf-killer-werewolf", "werewolf"),
        }
        state.death_history = [DeathReport(player_seat=1, cause="wolf_kill", round_number=1)]

        view = sf.filter_for_role(state, 2, "wolf-killer-werewolf")
        assert len(view["dead_players"]) == 1

    def test_dead_player_no_history(self):
        sf = StateFilter()
        state = GameState(game_id="test", config=GameConfig())
        p1 = make_player(1, "wolf-killer-villager", "good", alive=False)
        state.players = {1: p1, 2: make_player(2, "wolf-killer-werewolf", "werewolf")}

        view = sf.filter_for_role(state, 2, "wolf-killer-werewolf")
        assert view["dead_players"][0]["seat"] == 1

    def test_speeches_truncated(self):
        sf = StateFilter()
        state = GameState(game_id="test", config=GameConfig())
        state.players = {1: make_player(1, "wolf-killer-villager", "good")}

        # Add >20 speeches to test limit
        for i in range(25):
            state.speeches.append(SpeechRecord(player_seat=1, text=f"speech {i}", round_number=1))

        view = sf.filter_for_role(state, 1, "wolf-killer-villager")
        assert len(view["speeches"]) == 20
