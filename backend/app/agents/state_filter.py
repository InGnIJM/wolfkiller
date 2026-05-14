from __future__ import annotations
from app.models.game import GameState, PlayerState


class StateFilter:
    """Filters game state to produce role-specific views, enforcing information asymmetry."""

    def filter_for_role(self, state: GameState, player_id: int, role_name: str) -> dict:
        view: dict = {
            "alive_players": self._public_alive_players(state),
            "dead_players": self._public_dead_players(state),
            "speeches": self._recent_speeches(state),
            "votes": self._public_votes(state),
            "phase": state.phase.value,
            "round_number": state.round_number,
            "sheriff": state.sheriff,
            "your_seat": player_id,
            "your_role": role_name,
        }

        # Werewolves know their teammates
        if "werewolf" in role_name:
            view["wolf_teammates"] = self._get_wolf_teammates(state, player_id)

        # Seer knows check results
        if "seer" in role_name:
            player = state.players.get(player_id)
            if player:
                view["check_results"] = player.check_results

        # Witch knows night kill victims (before antidote used)
        if "witch" in role_name:
            player = state.players.get(player_id)
            if player:
                view["has_antidote"] = player.has_antidote
                view["has_poison"] = player.has_poison
                view["last_wolf_kill_target"] = state.last_wolf_kill_target

        # Hunter knows gun status
        if "hunter" in role_name:
            player = state.players.get(player_id)
            if player:
                view["has_gun"] = player.has_gun

        return view

    def _public_alive_players(self, state: GameState) -> list[dict]:
        return [
            {"seat": s, "is_sheriff": p.is_sheriff}
            for s, p in state.players.items()
            if p.is_alive
        ]

    def _public_dead_players(self, state: GameState) -> list[dict]:
        return [
            {
                "seat": s,
            }
            for s, p in state.players.items()
            if not p.is_alive
        ]

    def _recent_speeches(self, state: GameState, limit: int = 20) -> list[dict]:
        recent = state.speeches[-limit:] if len(state.speeches) > limit else state.speeches
        return [s.to_dict() if hasattr(s, "to_dict") else s for s in recent]

    def _public_votes(self, state: GameState) -> list[dict]:
        return [v.to_dict() if hasattr(v, "to_dict") else v for v in state.votes]

    def _get_wolf_teammates(self, state: GameState, player_id: int) -> list[int]:
        return [
            s for s, p in state.players.items()
            if "werewolf" in p.role and s != player_id
        ]
