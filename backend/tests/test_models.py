import pytest
from app.models.game import (
    GameState, GameConfig, PlayerState, GamePhase, Camp
)
from app.models.actions import (
    NightAction, VoteAction, SpeechRecord, DeathReport, WinResult
)


class TestGameConfig:
    def test_default_config(self):
        config = GameConfig()
        assert config.role_counts == {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
        }
        assert config.total_players == 9
        assert config.reveal_on_death is False

    def test_role_distribution(self):
        config = GameConfig()
        assert config.role_distribution() == [
            "wolf-killer-werewolf",
            "wolf-killer-werewolf",
            "wolf-killer-werewolf",
            "wolf-killer-villager",
            "wolf-killer-villager",
            "wolf-killer-villager",
            "wolf-killer-seer",
            "wolf-killer-witch",
            "wolf-killer-hunter",
        ]

    def test_custom_role_counts_define_total_and_distribution(self):
        config = GameConfig(role_counts={"werewolf": 1, "villager": 3})
        assert config.total_players == 4
        assert config.role_distribution() == ["werewolf", "villager", "villager", "villager"]

    def test_empty_role_counts_has_no_players_or_roles(self):
        config = GameConfig(role_counts={})
        assert config.total_players == 0
        assert config.role_distribution() == []

    def test_legacy_role_counts_are_converted_to_role_counts(self):
        config = GameConfig(
            num_werewolves=1,
            num_villagers=3,
            num_seers=0,
            num_witches=0,
            num_hunters=0,
        )
        assert config.role_counts == {
            "wolf-killer-werewolf": 1,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 0,
            "wolf-killer-witch": 0,
            "wolf-killer-hunter": 0,
        }
        assert not hasattr(config, "num_werewolves")

    def test_legacy_config_calculates_total_and_distribution(self):
        config = GameConfig(
            num_werewolves=1,
            num_villagers=3,
            num_seers=0,
            num_witches=0,
            num_hunters=0,
        )
        assert config.total_players == 4
        assert config.role_distribution() == [
            "wolf-killer-werewolf",
            "wolf-killer-villager",
            "wolf-killer-villager",
            "wolf-killer-villager",
        ]

    @pytest.mark.parametrize(
        "legacy_parameter",
        [
            "num_werewolves",
            "num_villagers",
            "num_seers",
            "num_witches",
            "num_hunters",
        ],
    )
    def test_role_counts_cannot_be_combined_with_legacy_counts(self, legacy_parameter):
        with pytest.raises(ValueError, match="role_counts"):
            GameConfig(role_counts={"wolf-killer-werewolf": 1}, **{legacy_parameter: 1})


class TestPlayerState:
    def test_default_is_alive(self):
        p = PlayerState(seat_number=1, role="wolf-killer-villager", camp="good")
        assert p.is_alive is True
        assert p.revealed_role is None

    def test_witch_has_potions(self):
        p = PlayerState(seat_number=1, role="wolf-killer-witch", camp="good")
        p.has_antidote = True
        p.has_poison = True
        assert p.has_antidote is True
        assert p.has_poison is True

    def test_hunter_has_gun(self):
        p = PlayerState(seat_number=1, role="wolf-killer-hunter", camp="good")
        p.has_gun = True
        assert p.has_gun is True

    def test_mark_dead(self):
        p = PlayerState(seat_number=1, role="wolf-killer-werewolf", camp="werewolf")
        p.mark_dead("exile")
        assert p.is_alive is False
        # revealed_role is never set — identity is never publicly revealed

    def test_mark_dead_night(self):
        p = PlayerState(seat_number=1, role="wolf-killer-villager", camp="good")
        p.mark_dead("wolf_kill")
        assert p.is_alive is False

    def test_reset_alive(self):
        p = PlayerState(seat_number=1, role="wolf-killer-werewolf", camp="werewolf")
        p.mark_dead("exile")
        p.reset_alive()
        assert p.is_alive is True


class TestGameState:
    def test_default_tiebreak_state(self):
        state = GameState(game_id="test")
        assert state.vote_round == 1
        assert state.is_tiebreak is False
        assert state.tiebreak_candidates == set()
        assert state.supplemental_speakers == set()
        assert state.voted_seats == set()
        assert state.accepted_action_keys == set()

    def test_tiebreak_and_vote_sets_are_independent_between_states(self):
        first = GameState(game_id="first")
        second = GameState(game_id="second")
        additions = {
            "tiebreak_candidates": 1,
            "supplemental_speakers": 2,
            "voted_seats": 3,
            "accepted_action_keys": "night:1:kill",
        }

        for attribute, value in additions.items():
            getattr(first, attribute).add(value)
            assert getattr(second, attribute) == set()

    def test_alive_players(self):
        state = GameState(game_id="test")
        state.players = {
            1: PlayerState(1, "villager", "good"),
            2: PlayerState(2, "villager", "good"),
        }
        state.players[2].is_alive = False
        alive = state.alive_players()
        assert len(alive) == 1
        assert 1 in alive

    def test_dead_players(self):
        state = GameState(game_id="test")
        state.players = {
            1: PlayerState(1, "villager", "good"),
            2: PlayerState(2, "villager", "good"),
        }
        state.players[2].is_alive = False
        dead = state.dead_players()
        assert len(dead) == 1
        assert 2 in dead

    def test_players_by_camp(self):
        state = GameState(game_id="test")
        state.players = {
            1: PlayerState(1, "villager", "good"),
            2: PlayerState(2, "werewolf", "werewolf"),
        }
        goods = state.players_by_camp("good")
        wolves = state.players_by_camp("werewolf")
        assert len(goods) == 1
        assert len(wolves) == 1

    def test_alive_by_camp(self):
        state = GameState(game_id="test")
        p1 = PlayerState(1, "villager", "good")
        p2 = PlayerState(2, "werewolf", "werewolf", is_alive=False)
        state.players = {1: p1, 2: p2}
        alive_good = state.alive_by_camp("good")
        alive_wolf = state.alive_by_camp("werewolf")
        assert len(alive_good) == 1
        assert len(alive_wolf) == 0

    def test_get_public_state(self):
        state = GameState(game_id="test")
        state.players = {1: PlayerState(1, "villager", "good")}
        public = state.get_public_state()
        assert public["game_id"] == "test"
        assert 1 in public["players"]

    def test_public_state_exposes_viewer_roles_and_camps(self):
        state = GameState(
            game_id="g",
            players={1: PlayerState(1, "wolf-killer-werewolf", "werewolf")},
        )
        public = state.get_public_state()
        assert public["players"][1]["role"] == "wolf-killer-werewolf"
        assert public["players"][1]["camp"] == "werewolf"

    def test_phase_enum(self):
        assert GamePhase.NIGHT.value == "night"
        assert GamePhase.SPEECH.value == "speech"


class TestActionModels:
    def test_night_action_to_dict(self):
        a = NightAction(player_seat=1, action_type="kill", target_seat=5, reasoning="test")
        d = a.to_dict()
        assert d["player_seat"] == 1
        assert d["action_type"] == "kill"
        assert d["target_seat"] == 5

    def test_vote_action_to_dict(self):
        v = VoteAction(voter_seat=3, target_seat=7, reasoning="suspicious")
        d = v.to_dict()
        assert d["voter_seat"] == 3
        assert d["target_seat"] == 7

    def test_vote_action_abstain(self):
        v = VoteAction(voter_seat=3, target_seat=None, reasoning="not sure")
        d = v.to_dict()
        assert d["target_seat"] is None

    def test_speech_record_to_dict(self):
        s = SpeechRecord(player_seat=2, text="hello", round_number=1)
        d = s.to_dict()
        assert d["text"] == "hello"

    def test_death_report_to_dict(self):
        d = DeathReport(player_seat=5, cause="wolf_kill", round_number=1)
        dd = d.to_dict()
        assert dd["cause"] == "wolf_kill"

    def test_win_result_to_dict(self):
        w = WinResult(winning_camp="good", reason="all_wolves_dead")
        d = w.to_dict()
        assert d["winning_camp"] == "good"
