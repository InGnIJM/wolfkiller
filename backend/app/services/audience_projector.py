"""Whitelist projection from internal domain events to the audience stream."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.models.game import GameState


_EVENTS: dict[str, tuple[str, frozenset[str]]] = {
    "GAME_INITIALIZED": ("game_initialized", frozenset({"players", "config"})),
    "PLAYER_REVEALED": ("player_revealed", frozenset({"seat_number", "role", "camp"})),
    "EXECUTION_STATE": ("execution_state", frozenset({"execution_status", "recoverable", "recovery_block_code"})),
    "PHASE_CHANGED": ("phase", frozenset({"phase", "round_number"})),
    "SPEECH_MADE": ("speech", frozenset({"player_seat", "text", "round_number", "phase"})),
    "PLAYER_DIED": ("death", frozenset({"player_seat", "seat", "cause", "round_number"})),
    "VOTE_CAST": ("vote", frozenset({"voter_seat", "target_seat", "round_number", "status"})),
    "VOTE_RESULT": ("vote_result", frozenset({"round_number", "exiled_seat", "counts"})),
    "GAME_OVER": ("winner", frozenset({"winning_camp", "reason"})),
    "NIGHT_ACTION": ("night_action", frozenset({"action_type", "target_seat", "round_number", "vote_counts", "result"})),
    "TECHNICAL_ABSTAIN": ("technical_abstain", frozenset({"player_seat", "round_number", "phase", "failure_code"})),
}

_PUBLIC_ROLE_ACTIONS = {
    "WEREWOLF_KILL": "werewolf_kill",
    "WITCH_SAVE": "witch_save",
    "WITCH_POISON": "witch_poison",
    "SEER_CHECK": "seer_check",
    "HUNTER_SHOT": "hunter_shot",
    "GUARD_PROTECT": "guard_protect",
}
_PUBLIC_REASONING_EVENTS = {
    "HUNTER_REASONING": "hunter_reasoning",
    "WITCH_REASONING": "witch_reasoning",
    "SEER_REASONING": "seer_reasoning",
    "GUARD_REASONING": "guard_reasoning",
}
_PUBLIC_PLAYER_FIELDS = frozenset({
    "seat_number", "is_alive", "is_sheriff", "role", "camp", "revealed_role",
})
_PUBLIC_CONFIG_FIELDS = frozenset({"role_counts", "reveal_on_death"})
for _domain_type in _PUBLIC_ROLE_ACTIONS:
    _EVENTS[_domain_type] = (
        "night_action",
        frozenset({"target_seat", "round_number", "vote_counts", "result"}),
    )
_EVENTS["WOLF_CHAT_MESSAGE"] = (
    "wolf_chat_message", frozenset({"seat", "text", "round_number"}),
)
_EVENTS["WOLF_VOTE"] = (
    "wolf_vote", frozenset({"seat", "target_seat", "reasoning", "round_number"}),
)
for _domain_type in _PUBLIC_REASONING_EVENTS:
    _EVENTS[_domain_type] = (
        "night_thought",
        frozenset({"seat", "action_type", "target_seat", "reasoning", "round_number"}),
    )


class AudienceProjector:
    projection_version = 1

    def project_events(
        self, game_id: str, events: Iterable[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        projected: list[dict[str, object]] = []
        for event in events:
            event_type = event.get("event_type")
            definition = _EVENTS.get(event_type) if isinstance(event_type, str) else None
            visibility = event.get("visibility", ())
            if definition is None or not isinstance(visibility, (list, tuple, set, frozenset)) or "PUBLIC" not in visibility:
                continue
            event_id = event.get("event_id")
            payload = event.get("payload")
            if not isinstance(event_id, str) or not event_id or not isinstance(payload, Mapping):
                raise ValueError("public domain event is missing event_id or payload")
            public_type, allowed = definition
            public_payload: dict[str, object] = {
                key: value for key, value in payload.items() if key in allowed
            }
            if event_type == "GAME_INITIALIZED":
                players = payload.get("players")
                if isinstance(players, Mapping):
                    public_payload["players"] = {
                        str(seat): {
                            key: value for key, value in player.items()
                            if key in _PUBLIC_PLAYER_FIELDS
                        }
                        for seat, player in players.items()
                        if isinstance(player, Mapping)
                    }
                elif isinstance(players, list):
                    public_payload["players"] = [{
                        key: value for key, value in player.items()
                        if key in _PUBLIC_PLAYER_FIELDS
                    } for player in players if isinstance(player, Mapping)]
                config = payload.get("config")
                if isinstance(config, Mapping):
                    public_payload["config"] = {
                        key: value for key, value in config.items()
                        if key in _PUBLIC_CONFIG_FIELDS
                    }
            action_type = _PUBLIC_ROLE_ACTIONS.get(event_type)
            if action_type is not None:
                public_payload = {"action_type": action_type, **public_payload}
            reasoning_type = _PUBLIC_REASONING_EVENTS.get(event_type)
            if reasoning_type is not None:
                public_payload["action_type"] = reasoning_type
            row: dict[str, object] = {
                "event_id": f"{event_id}:audience",
                "event_type": public_type,
                "schema_version": 1,
                "payload": public_payload,
            }
            timestamp = event.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                row["timestamp"] = timestamp
            projected.append(row)
        return projected

    def snapshot(self, state: GameState, *, execution_status: str,
                 recovery_block_code: str | None = None) -> dict[str, object]:
        return {
            "game_id": state.game_id,
            "phase": state.phase.value,
            "round_number": state.round_number,
            "execution_status": execution_status,
            "recoverable": execution_status in {"paused", "interrupted"} and recovery_block_code is None,
            "recovery_block_code": recovery_block_code,
            "players": {
                str(seat): {
                    "seat_number": player.seat_number,
                    "is_alive": player.is_alive,
                    "is_sheriff": player.is_sheriff,
                    "role": player.role,
                    "camp": player.camp,
                    "revealed_role": player.revealed_role,
                }
                for seat, player in sorted(state.players.items())
            },
            "sheriff": state.sheriff,
            "speeches": [item.to_dict() for item in state.speeches],
            "votes": [item.to_dict() for item in state.votes],
            "death_history": [item.to_dict() for item in state.death_history],
            "win_result": state.win_result,
        }
