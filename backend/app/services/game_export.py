"""Single-game export: assemble a self-describing bundle and render a
standalone HTML battle-report/replay.

Two data paths, picked automatically:

- ``audience`` — the durable god-view projection (``audience_snapshots`` +
  ``audience_events`` tables) written by the live engine.
- ``legacy`` — archives that predate the projection, projected on the fly from
  ``data/games/<id>/{conversation,game}.log`` by the same
  :mod:`app.services.legacy_replay` the ``/logs`` endpoint uses.

The bundle is embedded into ``app/export/report.html`` as JSON; the page's own
vanilla-JS renderer draws the report offline, no server required.
"""

from __future__ import annotations

import html as _html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.export.labels import export_labels
from app.services.commentary_service import fetch_all_audience_events
from app.services.game_summary import read_game_summary
from app.services.legacy_replay import _public_replay_events, _read_jsonl

logger = logging.getLogger(__name__)

EXPORT_SCHEMA = "wolfkiller.export.v1"
_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "export" / "report.html"
)
#: Config keys safe for a god-view export — mirrors the audience projector's
#: ``_PUBLIC_CONFIG_FIELDS`` (kept as a tuple here so the export stays
#: independent of projector internals).
_PUBLIC_CONFIG_KEYS = (
    "role_counts", "reveal_on_death", "enable_sheriff",
    "witch_self_save", "victory",
)
_REVIEW_FILENAME = "review.json"


class GameExportNotFoundError(Exception):
    """The requested game is unknown to the repository, memory and disk."""


def _camp_label(value: object) -> str | None:
    """Normalize a stored camp value (plain str or str-Enum) into a label."""
    if isinstance(value, str):
        return value
    inner = getattr(value, "value", None)
    return inner if isinstance(inner, str) else None


def _public_config(raw: object) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return {
            key: raw[key] for key in _PUBLIC_CONFIG_KEYS if key in raw
        }
    out: dict[str, Any] = {}
    for key in _PUBLIC_CONFIG_KEYS:
        value = getattr(raw, key, None)
        if value is not None:
            out[key] = value
    return out


def _normalize_player(seat: int, raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "seat": seat,
        "role": raw.get("role") if isinstance(raw.get("role"), str) else None,
        "camp": _camp_label(raw.get("camp")),
        "revealed_role": (
            raw.get("revealed_role")
            if isinstance(raw.get("revealed_role"), str)
            else None
        ),
        "is_alive": bool(raw.get("is_alive", True)),
        "is_sheriff": bool(raw.get("is_sheriff")),
        "model": None,
        "death": None,
    }


def _players_from_snapshot(state: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    players = state.get("players")
    roster: dict[int, dict[str, Any]] = {}
    if not isinstance(players, Mapping):
        return roster
    for key, info in players.items():
        if not isinstance(info, Mapping):
            continue
        seat = info.get("seat_number")
        if type(seat) is not int:
            try:
                seat = int(key)
            except (TypeError, ValueError):
                continue
        roster[seat] = _normalize_player(seat, info)
    return roster


def _players_from_init_event(
    events: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Roster fallback: the GAME_INITIALIZED event carries the same truth."""
    for event in events:
        if event.get("event_type") != "game_initialized":
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping):
            return _players_from_snapshot(
                {"players": payload.get("players")}
            )
    return {}


def _players_from_state(state: object) -> dict[int, dict[str, Any]]:
    """Roster fallback: the in-memory GameState (works for legacy archives)."""
    players = getattr(state, "players", None)
    roster: dict[int, dict[str, Any]] = {}
    if not isinstance(players, Mapping):
        return roster
    for seat, player in players.items():
        if type(seat) is not int:
            continue
        roster[seat] = _normalize_player(
            seat,
            {
                "seat_number": seat,
                "role": getattr(player, "role", None),
                "camp": getattr(player, "camp", None),
                "revealed_role": getattr(player, "revealed_role", None),
                "is_alive": getattr(player, "is_alive", True),
                "is_sheriff": getattr(player, "is_sheriff", False),
            },
        )
    return roster


def _players_from_role_init(
    operations: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Last-resort roster: the ``role_init`` record in ``game.log``."""
    for record in operations:
        if not isinstance(record, Mapping) or record.get("operation") != "role_init":
            continue
        data = record.get("data")
        players = data.get("players") if isinstance(data, Mapping) else None
        if not isinstance(players, Mapping):
            continue
        roster: dict[int, dict[str, Any]] = {}
        for key, info in players.items():
            if not isinstance(info, Mapping):
                continue
            try:
                seat = int(key)
            except (TypeError, ValueError):
                continue
            roster[seat] = _normalize_player(seat, info)
        if roster:
            return roster
    return {}


def _config_from_operations(operations: list[dict[str, Any]]) -> dict[str, Any]:
    for record in operations:
        if not isinstance(record, Mapping) or record.get("operation") != "game_config":
            continue
        data = record.get("data")
        if isinstance(data, Mapping):
            return _public_config(data)
    return {}


def _derive_deaths(events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """seat -> {cause, round} from the exported event stream itself."""
    deaths: dict[int, dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        round_number = payload.get("round_number")
        if event.get("event_type") == "death":
            seat = payload.get("player_seat")
            cause = payload.get("cause")
            if type(seat) is int and isinstance(cause, str):
                deaths.setdefault(seat, {"cause": cause, "round": round_number})
        elif event.get("event_type") == "vote_result":
            seat = payload.get("exiled_seat")
            if type(seat) is int:
                deaths.setdefault(seat, {"cause": "exile", "round": round_number})
    return deaths


def _winner_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event_type") != "winner":
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping) and isinstance(
            payload.get("winning_camp"), str
        ):
            return dict(payload)
    return None


def _normalize_winner(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    camp = raw.get("winning_camp")
    if not isinstance(camp, str) or not camp:
        return None
    return {
        "winning_camp": camp,
        "reason": raw.get("reason") if isinstance(raw.get("reason"), str) else None,
        "winners": (
            [seat for seat in raw.get("winners", []) if type(seat) is int]
            if isinstance(raw.get("winners"), list)
            else []
        ),
    }


def _seat_models(snapshot: object) -> dict[int, str]:
    """``[{model_id, seats: [...]}]`` → ``{seat: model_id}``."""
    out: dict[int, str] = {}
    if not isinstance(snapshot, list):
        return out
    for entry in snapshot:
        if not isinstance(entry, Mapping):
            continue
        model = entry.get("model_id") or entry.get("model")
        seats = entry.get("seats")
        if not isinstance(model, str) or not isinstance(seats, list):
            continue
        for seat in seats:
            if type(seat) is int:
                out.setdefault(seat, model)
    return out


def _read_review(data_dir: str, game_id: str) -> dict[str, Any] | None:
    path = Path(data_dir) / "games" / game_id / _REVIEW_FILENAME
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


class GameExportService:
    """Build the export bundle and render the standalone HTML report."""

    def __init__(
        self,
        repository: Any,
        data_dir: str,
        game_service: Any = None,
    ) -> None:
        self._repository = repository
        self._data_dir = data_dir
        self._game_service = game_service

    # ── public API ────────────────────────────────────────────────

    def build_bundle(self, game_id: str) -> dict[str, Any]:
        repository = self._repository
        record = (
            repository.get_game(game_id) if repository is not None else None
        )
        state = (
            self._game_service.get_game_state(game_id)
            if self._game_service is not None
            else None
        )
        if record is None and state is None:
            raise GameExportNotFoundError(game_id)

        summary = read_game_summary(self._data_dir, game_id)
        snapshot = (
            repository.get_audience_snapshot(game_id)
            if repository is not None
            else None
        )
        audience_events = (
            fetch_all_audience_events(repository, game_id)
            if repository is not None
            else []
        )

        operations: list[dict[str, Any]] = []
        if snapshot is not None or audience_events:
            events = self._normalize_events(audience_events)
            aud_state = snapshot.get("state") if snapshot else None
            roster = (
                _players_from_snapshot(aud_state)
                if isinstance(aud_state, Mapping)
                else {}
            )
            if not roster:
                roster = _players_from_init_event(events)
            if not roster:
                roster = _players_from_state(state)
            meta_state = aud_state if isinstance(aud_state, Mapping) else {}
            source = "audience"
        else:
            events, operations = self._legacy_events(game_id)
            roster = _players_from_state(state) or _players_from_role_init(
                operations
            )
            meta_state = {}
            source = "legacy"

        self._apply_deaths(roster, meta_state, events)
        self._apply_models(roster, game_id, record, summary)

        winner = _normalize_winner(
            (meta_state.get("win_result") if meta_state else None)
            or getattr(state, "win_result", None)
            or _winner_from_events(events)
        )

        phase = meta_state.get("phase")
        if not isinstance(phase, str):
            phase_obj = getattr(state, "phase", None)
            phase = getattr(phase_obj, "value", phase_obj)
            phase = phase if isinstance(phase, str) else None

        round_number = meta_state.get("round_number")
        if type(round_number) is not int:
            round_number = getattr(state, "round_number", None)
            if type(round_number) is not int:
                round_number = max(
                    (
                        e["payload"]["round_number"]
                        for e in events
                        if isinstance(e.get("payload"), Mapping)
                        and type(e["payload"].get("round_number")) is int
                    ),
                    default=0,
                )

        execution_status = None
        if record is not None and isinstance(record.get("execution_status"), str):
            execution_status = record["execution_status"]
        elif isinstance(meta_state.get("execution_status"), str):
            execution_status = meta_state["execution_status"]
        else:
            execution_status = (
                "completed" if phase == "game_over" else "running"
            )

        config = _public_config(record.get("config")) if record else {}
        if not config:
            config = self._init_config(events)
        if not config:
            config = _public_config(getattr(state, "config", None))
        if not config:
            config = _config_from_operations(operations)

        created_at = None
        finished_at = None
        if record is not None:
            created_at = record.get("created_at")
            finished_at = record.get("finished_at")
        if not isinstance(created_at, str) and summary:
            created_at = summary.get("created_at")
        if not isinstance(finished_at, str) and summary:
            finished_at = summary.get("finished_at")
        if winner is None and summary and isinstance(summary.get("winner"), str):
            winner = {
                "winning_camp": summary["winner"],
                "reason": summary.get("reason")
                if isinstance(summary.get("reason"), str)
                else None,
                "winners": [],
            }

        return {
            "schema": EXPORT_SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "game": {
                "game_id": game_id,
                "name": self._display_name(game_id, record),
                "phase": phase,
                "round_number": round_number,
                "execution_status": execution_status,
                "config": config,
                "winner": winner,
                "created_at": created_at if isinstance(created_at, str) else None,
                "finished_at": (
                    finished_at if isinstance(finished_at, str) else None
                ),
            },
            "players": [roster[seat] for seat in sorted(roster)],
            "events": events,
            "review": _read_review(self._data_dir, game_id),
            "stats": self._stats(summary),
            "labels": export_labels(),
        }

    def render_html(self, game_id: str) -> str:
        """Render the standalone report. All model text stays data-only."""
        bundle = self.build_bundle(game_id)
        template = _TEMPLATE_PATH.read_text(encoding="utf-8")
        title = f"{bundle['game']['name']} · WolfKiller"
        # Title first: replacing __DATA__ first would let bundle text spoof
        # the title placeholder.
        rendered = template.replace("__TITLE__", _html.escape(title))
        payload = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
        return rendered.replace("__DATA__", payload)

    # ── internals ─────────────────────────────────────────────────

    @staticmethod
    def _normalize_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            out.append({
                "seq": row.get("seq", index),
                "event_type": row.get("event_type"),
                "timestamp": row.get("timestamp"),
                "payload": (
                    dict(row["payload"])
                    if isinstance(row.get("payload"), Mapping)
                    else {}
                ),
            })
        return out

    def _legacy_events(
        self, game_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        log_dir = Path(self._data_dir) / "games" / game_id
        conversations = _read_jsonl(str(log_dir / "conversation.log"))
        operations = _read_jsonl(str(log_dir / "game.log"))
        events = [
            {
                "seq": index,
                "event_type": event.get("event_type"),
                "timestamp": event.get("timestamp"),
                "payload": (
                    dict(event["payload"])
                    if isinstance(event.get("payload"), Mapping)
                    else {}
                ),
            }
            for index, event in enumerate(
                _public_replay_events(conversations, operations)
            )
        ]
        return events, operations

    @staticmethod
    def _init_config(events: list[dict[str, Any]]) -> dict[str, Any]:
        for event in events:
            if event.get("event_type") != "game_initialized":
                continue
            payload = event.get("payload")
            if isinstance(payload, Mapping):
                return _public_config(payload.get("config"))
        return {}

    @staticmethod
    def _apply_deaths(
        roster: dict[int, dict[str, Any]],
        state: Mapping[str, Any],
        events: list[dict[str, Any]],
    ) -> None:
        deaths = _derive_deaths(events)
        history = state.get("death_history") if state else None
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, Mapping):
                    continue
                seat = item.get("player_seat")
                cause = item.get("cause")
                if type(seat) is int and isinstance(cause, str):
                    deaths.setdefault(seat, {
                        "cause": cause,
                        "round": item.get("round_number"),
                    })
        for seat, entry in roster.items():
            fate = deaths.get(seat)
            if fate is not None:
                entry["death"] = fate
                entry["is_alive"] = False

    def _apply_models(
        self,
        roster: dict[int, dict[str, Any]],
        game_id: str,
        record: Mapping[str, Any] | None,
        summary: Mapping[str, Any] | None,
    ) -> None:
        snapshot = (
            self._game_service.get_game_model_snapshot(game_id)
            if self._game_service is not None
            else None
        )
        models = _seat_models(snapshot)
        if not models and record is not None:
            models = _seat_models(record.get("model_snapshot"))
        if not models and summary is not None:
            models = _seat_models(summary.get("model_snapshot"))
        for seat, entry in roster.items():
            if seat in models:
                entry["model"] = models[seat]

    def _display_name(
        self, game_id: str, record: Mapping[str, Any] | None,
    ) -> str:
        if self._game_service is not None:
            try:
                name = self._game_service.get_display_name(game_id)
                if isinstance(name, str) and name.strip():
                    return name.strip()
            except Exception:  # noqa: BLE001 - display name is best-effort
                logger.debug("get_display_name failed for %s", game_id)
        if record is not None and isinstance(record.get("name"), str):
            return record["name"]
        return game_id[:8]

    @staticmethod
    def _stats(summary: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(summary, Mapping):
            return None
        llm = summary.get("llm")
        return {
            "llm": llm if isinstance(llm, Mapping) else None,
            "model_errors": summary.get("model_errors"),
            "degraded_actions": (
                llm.get("degraded_actions")
                if isinstance(llm, Mapping)
                else None
            ),
            "rounds_played": summary.get("rounds_played"),
        }
