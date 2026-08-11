from __future__ import annotations

import random
from collections.abc import Callable, Mapping

from app.models.contracts import ActionContract, ActionRequest, RoleSpec
from app.models.game import Camp, GamePhase, GameState
from app.roles.hunter import Hunter
from app.roles.seer import Seer
from app.roles.villager import Villager
from app.roles.werewolf import Werewolf
from app.roles.witch import Witch


class RoleRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, RoleSpec] = {}

    def register(self, spec: RoleSpec) -> None:
        if spec.role_id in self._specs:
            raise ValueError(f"role already registered: {spec.role_id}")
        self._specs[spec.role_id] = spec

    def require(self, role_id: str) -> RoleSpec:
        try:
            return self._specs[role_id]
        except KeyError as error:
            raise ValueError(f"unknown role: {role_id}") from error

    def validate_role_counts(
        self, role_counts: Mapping[str, int], player_count: int
    ) -> None:
        total = 0
        for role_id, count in role_counts.items():
            self.require(role_id)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("role count must be a non-negative integer")
            total += count
        if total != player_count:
            raise ValueError("role count sum must equal player_count")

    def create_roles(
        self,
        role_counts: Mapping[str, int],
        player_count: int,
        prompt_builder: object,
        llm_client_factory: Callable[[], object],
    ) -> dict[int, object]:
        self.validate_role_counts(role_counts, player_count)
        role_ids = [
            role_id
            for role_id, count in role_counts.items()
            for _ in range(count)
        ]
        random.shuffle(role_ids)

        return {
            seat: self.require(role_id).role_factory(
                seat, role_id, prompt_builder, llm_client_factory()
            )
            for seat, role_id in enumerate(role_ids, start=1)
        }

    def build_requests(
        self,
        state: GameState,
        roles: Mapping[int, object],
        phase: GamePhase,
    ) -> list[ActionRequest]:
        if phase != state.phase:
            return []
        accepted_keys = getattr(state, "accepted_action_keys", set())
        requests = []
        for seat, player in state.players.items():
            if not player.is_alive or seat not in roles:
                continue
            role_id = player.role
            for contract in self.require(role_id).contracts:
                if contract.phase != phase:
                    continue
                key = f"{state.round_number}:{phase.value}:{seat}:{contract.contract_id}"
                if key in accepted_keys:
                    continue
                requests.append(
                    ActionRequest(
                        actor_seat=seat,
                        role_id=role_id,
                        contract=contract,
                        phase=phase,
                        round_id=state.round_number,
                        idempotency_key=key,
                    )
                )
        return sorted(requests, key=lambda request: request.contract.resolution_priority)


def _contract(
    contract_id: str,
    phase: GamePhase,
    action_types: tuple[str, ...],
    actions_requiring_target: frozenset[str],
    resolution_priority: int,
    fallback_action_type: str = "pass",
) -> ActionContract:
    return ActionContract(
        contract_id=contract_id,
        phase=phase,
        action_types=action_types,
        actions_requiring_target=actions_requiring_target,
        resolution_priority=resolution_priority,
        fallback_action_type=fallback_action_type,
    )


builtin_registry = RoleRegistry()
builtin_registry.register(
    RoleSpec("wolf-killer-villager", Camp.GOOD, Villager, ())
)
builtin_registry.register(
    RoleSpec(
        "wolf-killer-werewolf",
        Camp.WEREWOLF,
        Werewolf,
        (
            _contract(
                "werewolf_kill",
                GamePhase.NIGHT,
                ("kill", "pass"),
                frozenset({"kill"}),
                resolution_priority=10,
            ),
        ),
    )
)
builtin_registry.register(
    RoleSpec(
        "wolf-killer-seer",
        Camp.GOOD,
        Seer,
        (
            _contract(
                "seer_check",
                GamePhase.NIGHT,
                ("check", "pass"),
                frozenset({"check"}),
                resolution_priority=30,
            ),
        ),
    )
)
builtin_registry.register(
    RoleSpec(
        "wolf-killer-witch",
        Camp.GOOD,
        Witch,
        (
            _contract(
                "witch_action",
                GamePhase.NIGHT,
                ("save", "poison", "pass"),
                frozenset({"save", "poison"}),
                resolution_priority=20,
            ),
        ),
    )
)
builtin_registry.register(
    RoleSpec(
        "wolf-killer-hunter",
        Camp.GOOD,
        Hunter,
        (
            _contract(
                "hunter_shoot",
                GamePhase.DAWN,
                ("shoot", "pass"),
                frozenset({"shoot"}),
                resolution_priority=40,
            ),
        ),
    )
)
