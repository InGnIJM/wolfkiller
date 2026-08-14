from __future__ import annotations
import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Optional


def _plain(value: object) -> object:
    """Recursively convert frozen mappings/tuples into JSON-serializable values."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


class GameLogger:
    """Writes structured operation logs and conversation logs to disk as JSONL."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir

    def _ensure_dir(self, game_id: str) -> str:
        path = os.path.join(self.data_dir, "games", game_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _write_line(self, game_id: str, filename: str, record: dict) -> None:
        dir_path = self._ensure_dir(game_id)
        filepath = os.path.join(dir_path, filename)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── Game operation log ────────────────────────────────────────

    def log_operation(
        self, game_id: str, operation: str, round_num: int, phase: str,
        data: Optional[dict] = None, seat: Optional[int] = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "round": round_num,
            "phase": phase,
            "operation": operation,
            "seat": seat,
            "data": data or {},
        }
        self._write_line(game_id, "game.log", record)

    def log_phase_change(
        self, game_id: str, phase: str, round_num: int
    ) -> None:
        self.log_operation(game_id, "phase_change", round_num, phase,
                           data={"new_phase": phase})

    def log_deaths(
        self, game_id: str, round_num: int, deaths: list[dict],
    ) -> None:
        self.log_operation(game_id, "night_deaths", round_num, "dawn",
                           data={"deaths": deaths})

    def log_audience_action(
        self, game_id: str, round_num: int, phase: str,
        event_type: str, payload,
    ) -> None:
        """Record one pipeline-emitted PUBLIC audience event (role actions)."""
        self.log_operation(game_id, "audience_action", round_num, phase,
                           data={"event_type": event_type, "payload": _plain(payload)})

    def log_narration(self, game_id: str, round_num: int, phase: str,
                      title: str, text: str) -> None:
        """Record one narrator page shown to the audience during the night."""
        self.log_operation(game_id, "narration", round_num, phase,
                           data={"title": title, "text": text})

    def log_speech(
        self, game_id: str, round_num: int, phase: str, seat: int, text: str,
    ) -> None:
        self.log_operation(game_id, "speak", round_num, phase, seat=seat,
                           data={"text": text})

    def log_vote(
        self, game_id: str, round_num: int, seat: int, target: Optional[int],
    ) -> None:
        self.log_operation(game_id, "vote", round_num, "vote_casting", seat=seat,
                           data={"target": target})

    def log_vote_result(
        self, game_id: str, round_num: int, exiled: Optional[int], tally: dict,
    ) -> None:
        self.log_operation(game_id, "vote_result", round_num, "vote_resolution",
                           data={"exiled": exiled, "tally": tally})

    def log_role_init(
        self, game_id: str, players: dict,
    ) -> None:
        self.log_operation(game_id, "role_init", 0, "role_deal",
                           data={"players": players})

    def log_game_over(
        self, game_id: str, round_num: int, winner: str, reason: str,
    ) -> None:
        self.log_operation(game_id, "game_over", round_num, "game_over",
                           data={"winner": winner, "reason": reason})

    # ── Conversation log ──────────────────────────────────────────

    def log_conversation(
        self, game_id: str, record: dict,
    ) -> None:
        record["timestamp"] = record.get("timestamp") or datetime.now(timezone.utc).isoformat()
        self._write_line(game_id, "conversation.log", record)
