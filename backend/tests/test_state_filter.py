import pytest

from app.agents.state_filter import StateFilter, _plain
from app.models.game import GameState, GamePhase, PlayerState
from app.roles.registry import builtin_registry


def make_state(role_assignments: dict[int, str]) -> GameState:
    state = GameState(game_id="view", phase=GamePhase.SPEECH, round_number=1)
    specs = builtin_registry.freeze().specs
    for seat, role in role_assignments.items():
        state.players[seat] = PlayerState(
            seat_number=seat, role=role, camp=specs[role].camp_id
        )
    return state


def wolf_state() -> GameState:
    return make_state({
        1: "wolf-killer-werewolf",
        2: "wolf-killer-werewolf",
        3: "wolf-killer-villager",
        4: "wolf-killer-witch",
    })


class TestStateFilter:
    def test_view_contains_public_facts_and_actor_identity(self):
        view = StateFilter().filter_for_role(wolf_state(), 1, "wolf-killer-werewolf")

        assert view["game_id"] == "view"
        assert view["phase"] == "speech"
        assert view["round_number"] == 1
        assert view["actor_seat"] == 1
        assert view["actor_alive"] is True
        assert view["actor_role_id"] == "wolf-killer-werewolf"
        assert tuple(view["facts"]["alive_seats"]) == (1, 2, 3, 4)
        assert view["facts"]["actor_identity"] == {
            "seat": 1, "role_id": "wolf-killer-werewolf", "camp_id": "werewolf",
        }

    def test_camp_members_visible_only_for_camp_roles(self):
        filter_ = StateFilter()
        wolf_view = filter_.filter_for_role(wolf_state(), 1, "wolf-killer-werewolf")
        villager_view = filter_.filter_for_role(wolf_state(), 3, "wolf-killer-villager")

        assert wolf_view["facts"]["camp_members"] == [1, 2]
        assert "camp_members" not in villager_view["facts"]

    @pytest.mark.parametrize("antidote,poison", [(True, True), (True, False), (False, True), (False, False)])
    @pytest.mark.parametrize("target", [3, None])
    def test_witch_day_view_keeps_target_regardless_of_potions(self, antidote, poison, target):
        state = wolf_state()
        state.players[4].has_antidote = antidote
        state.players[4].has_poison = poison
        state.last_wolf_kill_target = target
        view = StateFilter().filter_for_role(state, 4, "wolf-killer-witch")

        assert view["resources"] == {"antidote": antidote, "poison": poison}
        assert view["facts"]["wolf_kill_target"] == target
        for seat, role in ((1, "wolf-killer-werewolf"), (3, "wolf-killer-villager")):
            other = StateFilter().filter_for_role(state, seat, role)
            assert "wolf_kill_target" not in other["facts"]

    def test_view_is_a_plain_independent_copy(self):
        filter_ = StateFilter()
        state = wolf_state()
        first = filter_.filter_for_role(state, 1, "wolf-killer-werewolf")
        first["facts"]["alive_seats"].append(99)
        first["resources"]["tampered"] = True
        second = filter_.filter_for_role(state, 1, "wolf-killer-werewolf")
        assert second["facts"]["alive_seats"] == [1, 2, 3, 4]
        assert "tampered" not in second["resources"]

    def test_view_requires_existing_player_and_matching_role(self):
        filter_ = StateFilter()
        state = wolf_state()
        with pytest.raises(ValueError, match="does not exist"):
            filter_.filter_for_role(state, 99, "wolf-killer-werewolf")
        with pytest.raises(ValueError, match="does not match"):
            filter_.filter_for_role(state, 1, "wolf-killer-villager")

    def test_view_rejects_unknown_role(self):
        state = wolf_state()
        state.players[3].role = "wolf-killer-unknown"
        with pytest.raises(ValueError, match="unknown role"):
            StateFilter().filter_for_role(state, 3, "wolf-killer-unknown")


class TestPlainConverter:
    def test_rejects_unsupported_values(self):
        with pytest.raises(TypeError, match="unsupported"):
            _plain(object())

    def test_rejects_nested_depth(self):
        nested: object = 0
        for _ in range(70):
            nested = [nested]
        with pytest.raises(ValueError, match="depth"):
            _plain(nested)

    def test_rejects_cycles(self):
        cycle: list = []
        cycle.append(cycle)
        with pytest.raises(ValueError, match="cycle"):
            _plain(cycle)

    def test_plain_passthrough_and_mappings(self):
        assert _plain(1) == 1
        assert _plain("text") == "text"
        assert _plain(None) is None
        assert _plain({"a": [1, (2,)]}) == {"a": [1, [2]]}
