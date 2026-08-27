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

    def log_model_error(
        self, game_id: str, round_num: int, phase: str, seat: int, *,
        contract_id: str, schedule_point: str, attempt: int,
        provider_profile: str, model_id: str, failure_code: str,
        exception_type: str, message: str, status_code: Optional[int] = None,
        error_code: object = None, provider_name: Optional[str] = None,
        provider_raw: Optional[str] = None,
        cause_chain: Optional[list[dict[str, object]]] = None,
        stack: Optional[list[dict[str, object]]] = None,
    ) -> None:
        """Persist sanitized model diagnostics without prompts or credentials."""
        data = {
            "contract_id": contract_id,
            "schedule_point": schedule_point,
            "attempt": attempt,
            "provider_profile": provider_profile,
            "model_id": model_id,
            "failure_code": failure_code,
            "exception_type": exception_type,
        }
        if status_code is not None:
            data["status_code"] = status_code
        if error_code is not None:
            data["error_code"] = error_code
        data["message"] = message
        if provider_name is not None:
            data["provider_name"] = provider_name
        if provider_raw is not None:
            data["provider_raw"] = provider_raw
        if cause_chain:
            data["cause_chain"] = _plain(cause_chain)
        self.log_operation(
            game_id, "model_error", round_num, phase, seat=seat, data=data,
        )
        error_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "round": round_num,
            "phase": phase,
            "operation": "model_error",
            "seat": seat,
            "data": {**data, "stack": _plain(stack or [])},
        }
        self._write_line(game_id, "model_errors.log", error_record)

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
        self, game_id: str, round_num: int, seat: int, target: Optional[int], *,
        vote_round: int,
    ) -> None:
        self.log_operation(game_id, "vote", round_num, "vote_casting", seat=seat,
                           data={"target": target, "vote_round": vote_round})

    def log_vote_telemetry(
        self, game_id: str, round_num: int, seat: int, *, transport: str,
        attempt: int, prompt_chars: int, elapsed_ms: int, retried: bool,
        parse_result: str, failure_code: Optional[str] = None,
        timeout_type: Optional[str] = None,
        window_id: Optional[str] = None,
    ) -> None:
        data = {
            "transport": transport, "attempt": attempt,
            "prompt_chars": prompt_chars, "elapsed_ms": elapsed_ms,
            "retried": retried, "parse_result": parse_result,
        }
        if failure_code is not None:
            data["failure_code"] = failure_code
        if timeout_type is not None:
            data["timeout_type"] = timeout_type
        if window_id is not None:
            data["window_id"] = window_id
        self.log_operation(
            game_id, "vote_telemetry", round_num, "vote_casting", seat=seat,
            data=data,
        )

    def log_vote_queue_telemetry(
        self, game_id: str, round_num: int, seat: int, *,
        queue_wait_ms: int, worker_limit: int, vote_round: int,
    ) -> None:
        self.log_operation(
            game_id, "vote_queue_telemetry", round_num, "vote_casting", seat=seat,
            data={
                "queue_wait_ms": queue_wait_ms, "worker_limit": worker_limit,
                "vote_round": vote_round,
            },
        )

    def log_vote_phase_timeout(
        self, game_id: str, round_num: int, *, timeout_seconds: float,
        completed_seats: list[int], missing_seats: list[int], vote_round: int,
    ) -> None:
        self.log_operation(
            game_id, "vote_phase_timeout", round_num, "vote_casting",
            data={
                "timeout_seconds": timeout_seconds,
                "completed_seats": completed_seats,
                "missing_seats": missing_seats,
                "vote_round": vote_round,
            },
        )

    def log_vote_window_opened(
        self, game_id: str, round_num: int, *, window_id: str,
        vote_round: int, eligible_voters: list[int], timeout_seconds: float,
    ) -> None:
        self.log_operation(
            game_id, "vote_window_opened", round_num, "vote_casting",
            data={
                "window_id": window_id, "vote_round": vote_round,
                "eligible_voters": eligible_voters,
                "timeout_seconds": timeout_seconds,
            },
        )

    def log_vote_receipt(
        self, game_id: str, round_num: int, seat: int, *, window_id: str,
        action_key: str, vote_round: int, status: str, target: Optional[int],
        command_digest: str, replayed: bool,
        failure_code: Optional[str] = None,
        timeout_type: Optional[str] = None,
    ) -> None:
        data = {
            "window_id": window_id, "action_key": action_key,
            "vote_round": vote_round, "status": status, "target": target,
            "command_digest": command_digest, "replayed": replayed,
        }
        if failure_code is not None:
            data["failure_code"] = failure_code
        if timeout_type is not None:
            data["timeout_type"] = timeout_type
        self.log_operation(
            game_id, "vote_receipt", round_num, "vote_casting",
            seat=seat, data=data,
        )

    def log_vote_window_closed(
        self, game_id: str, round_num: int, *, window_id: str,
        vote_round: int, accepted_votes: int, voluntary_abstains: int,
        technical_abstains: int, missing_voters: int,
    ) -> None:
        self.log_operation(
            game_id, "vote_window_closed", round_num, "vote_casting",
            data={
                "window_id": window_id, "vote_round": vote_round,
                "accepted_votes": accepted_votes,
                "voluntary_abstains": voluntary_abstains,
                "technical_abstains": technical_abstains,
                "missing_voters": missing_voters,
            },
        )

    def log_vote_technical_abstain(
        self, game_id: str, round_num: int, seat: int, *, failure_code: str,
        timeout_type: Optional[str] = None, window_id: Optional[str] = None,
        vote_round: Optional[int] = None,
    ) -> None:
        data = {"failure_code": failure_code}
        if timeout_type is not None:
            data["timeout_type"] = timeout_type
        if window_id is not None:
            data["window_id"] = window_id
        if vote_round is not None:
            data["vote_round"] = vote_round
        self.log_operation(
            game_id, "vote_technical_abstain", round_num, "vote_casting", seat=seat,
            data=data,
        )

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
