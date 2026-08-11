from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.models.game import Camp, GamePhase, GameState, PlayerState
from app.roles.registry import RoleRegistry, builtin_registry


@dataclass
class StubRole:
    seat: int
    role_name: str


class TestActionContracts:
    def test_contract_schema_requires_action_type_target_and_reasoning(self):
        contract = builtin_registry.require("wolf-killer-werewolf").contracts[0]

        schema = contract.json_schema()

        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["action_type", "target_seat", "reasoning"]
        assert schema["properties"]["action_type"]["enum"] == ["kill", "pass"]
        assert schema["properties"]["target_seat"]["type"] == ["integer", "null"]
        assert schema["properties"]["reasoning"]["maxLength"] == 500


class TestRoleRegistry:
    def test_rejects_unknown_role(self):
        with pytest.raises(ValueError, match="unknown role"):
            builtin_registry.validate_role_counts({"unknown": 1}, player_count=1)

    def test_rejects_duplicate_registration(self):
        registry = RoleRegistry()
        spec = builtin_registry.require("wolf-killer-villager")
        registry.register(spec)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(spec)

    def test_rejects_count_sum_mismatch(self):
        with pytest.raises(ValueError, match="sum"):
            builtin_registry.validate_role_counts(
                {"wolf-killer-villager": 1}, player_count=2
            )

    @pytest.mark.parametrize("count", [-1, 1.5, True])
    def test_rejects_negative_or_non_integer_count(self, count):
        with pytest.raises(ValueError, match="non-negative integer"):
            builtin_registry.validate_role_counts(
                {"wolf-killer-villager": count}, player_count=0
            )

    def test_create_roles_validates_then_assigns_registered_factories(self):
        registry = RoleRegistry()
        spec = builtin_registry.require("wolf-killer-villager")
        registry.register(spec)

        roles = registry.create_roles(
            {"wolf-killer-villager": 2},
            player_count=2,
            prompt_builder=object(),
            llm_client_factory=lambda: object(),
        )

        assert sorted(roles) == [1, 2]
        assert all(role.role_name == "wolf-killer-villager" for role in roles.values())

    def test_build_requests_only_for_alive_roles_with_current_phase_contracts(self):
        state = GameState(game_id="contracts", phase=GamePhase.NIGHT, round_number=4)
        state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", Camp.WEREWOLF.value),
            2: PlayerState(2, "wolf-killer-seer", Camp.GOOD.value),
            3: PlayerState(3, "wolf-killer-villager", Camp.GOOD.value),
            4: PlayerState(4, "wolf-killer-witch", Camp.GOOD.value, is_alive=False),
        }
        roles = {
            seat: StubRole(seat, player.role)
            for seat, player in state.players.items()
        }

        requests = builtin_registry.build_requests(state, roles, GamePhase.NIGHT)

        assert [(request.actor_seat, request.contract.contract_id) for request in requests] == [
            (1, "werewolf_kill"),
            (2, "seer_check"),
        ]
        assert all(request.round_id == 4 for request in requests)

    def test_build_requests_skips_previously_accepted_idempotency_key(self):
        state = GameState(game_id="contracts", phase=GamePhase.NIGHT, round_number=2)
        state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", Camp.WEREWOLF.value),
        }
        roles = {1: StubRole(1, "wolf-killer-werewolf")}
        state.accepted_action_keys = {"2:night:1:werewolf_kill"}

        assert builtin_registry.build_requests(state, roles, GamePhase.NIGHT) == []

    def test_build_requests_skips_contracts_from_other_phases(self):
        state = GameState(game_id="contracts", phase=GamePhase.NIGHT, round_number=2)
        state.players = {
            1: PlayerState(1, "wolf-killer-hunter", Camp.GOOD.value),
        }

        assert builtin_registry.build_requests(
            state, {1: StubRole(1, "wolf-killer-hunter")}, GamePhase.NIGHT
        ) == []

    def test_witch_has_one_combined_action_contract(self):
        contracts = builtin_registry.require("wolf-killer-witch").contracts

        assert len(contracts) == 1
        assert contracts[0].contract_id == "witch_action"
        assert contracts[0].action_types == ("save", "poison", "pass")
