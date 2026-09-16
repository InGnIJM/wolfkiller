import pytest
from app.core.rule_engine import RuleEngine
from app.models.game import GameState, GameConfig, PlayerState, Camp


def make_player(seat: int, role: str, camp: str, alive: bool = True) -> PlayerState:
    p = PlayerState(seat_number=seat, role=role, camp=camp, is_alive=alive)
    if "witch" in role:
        p.has_antidote = True
        p.has_poison = True
    if "hunter" in role:
        p.has_gun = True
    return p


def make_state(players: list[PlayerState]) -> GameState:
    state = GameState(game_id="test", config=GameConfig())
    for p in players:
        state.players[p.seat_number] = p
    return state


class TestRuleEngine:
    def test_all_wolves_dead_good_wins(self):
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf", alive=False),
            make_player(2, "wolf-killer-villager", "good"),
            make_player(3, "wolf-killer-seer", "good"),
        ]
        state = make_state(players)
        result = engine.check_win(state)
        assert result is not None
        assert result.winning_camp == "good"

    def test_all_gods_dead_wolves_win(self):
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
            make_player(3, "wolf-killer-seer", "good", alive=False),
            make_player(4, "wolf-killer-witch", "good", alive=False),
            make_player(5, "wolf-killer-hunter", "good", alive=False),
        ]
        state = make_state(players)
        result = engine.check_win(state)
        assert result is not None
        assert result.winning_camp == "werewolf"
        assert result.reason == "all_gods_dead"

    def test_all_villagers_dead_wolves_win(self):
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good", alive=False),
            make_player(3, "wolf-killer-villager", "good", alive=False),
            make_player(4, "wolf-killer-seer", "good"),
        ]
        state = make_state(players)
        result = engine.check_win(state)
        assert result is not None
        assert result.winning_camp == "werewolf"
        assert result.reason == "all_villagers_dead"

    def test_game_not_over(self):
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
            make_player(3, "wolf-killer-seer", "good"),
        ]
        state = make_state(players)
        result = engine.check_win(state)
        assert result is None

    def test_is_game_over(self):
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf", alive=False),
            make_player(2, "wolf-killer-villager", "good"),
        ]
        state = make_state(players)
        assert engine.is_game_over(state) is True

    def test_edge_both_conditions_same_time(self):
        """If the last wolf and last villager die simultaneously, wolves already met their condition first."""
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf", alive=False),
            make_player(2, "wolf-killer-villager", "good", alive=False),
            make_player(3, "wolf-killer-seer", "good"),
        ]
        state = make_state(players)
        result = engine.check_win(state)
        # all_villagers_dead should trigger werewolf win (checked before all_wolves_dead in order)
        assert result is not None
        assert result.winning_camp == "werewolf"

    def test_multiple_wolves_one_dead(self):
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-werewolf", "werewolf", alive=False),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-seer", "good"),
        ]
        state = make_state(players)
        result = engine.check_win(state)
        assert result is None  # game continues

    def test_wolves_win_when_alive_wolves_outnumber_alive_good_players(self):
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
            make_player(3, "wolf-killer-werewolf", "werewolf"),
            make_player(4, "wolf-killer-villager", "good"),
            make_player(5, "wolf-killer-seer", "good"),
        ]

        result = RuleEngine().check_win(make_state(players))

        assert result is not None
        assert result.winning_camp == "werewolf"
        assert result.reason == "wolves_outnumber_good"

    def test_only_hunter_god(self):
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
            make_player(3, "wolf-killer-hunter", "good"),
        ]
        state = make_state(players)
        result = engine.check_win(state)
        assert result is None  # gods still alive (hunter)

    def test_hunter_dead_gods_dead(self):
        engine = RuleEngine()
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
            make_player(3, "wolf-killer-hunter", "good", alive=False),
        ]
        state = make_state(players)
        result = engine.check_win(state)
        # Only hunter was god, now dead → all gods dead
        assert result is not None
        assert result.winning_camp == "werewolf"

    def test_living_guard_prevents_all_gods_dead_win(self):
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
            make_player(3, "wolf-killer-guard", "good"),
        ]

        assert RuleEngine().check_win(make_state(players)) is None

    def test_living_idiot_counts_as_a_god(self):
        players = [
            make_player(1, "wolf-killer-werewolf-king", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
            make_player(3, "wolf-killer-idiot", "good"),
        ]

        assert RuleEngine().check_win(make_state(players)) is None
        players[2].is_alive = False
        result = RuleEngine().check_win(make_state(players))
        assert result is not None and result.winning_camp == "werewolf"

    def test_lone_werewolf_king_keeps_the_wolf_camp_alive(self):
        players = [
            make_player(1, "wolf-killer-werewolf", "werewolf", alive=False),
            make_player(2, "wolf-killer-werewolf-king", "werewolf"),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-seer", "good"),
        ]

        assert RuleEngine().check_win(make_state(players)) is None
