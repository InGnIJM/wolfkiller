"""Build per-game benchmark summaries from persisted game logs.

A summary condenses one game's ``game.log`` / ``llm_calls.log`` / manifest
entry into a single ``summary.json`` that the benchmark metrics layer can
consume without re-parsing JSONL archives. Summaries contain metadata only —
never prompts, model outputs or private context.
"""

from __future__ import annotations

import json
import math
import os
from typing import Optional

from app.services.game_manifest import _has_known_model_assignment

_SUMMARY_FILENAME = "summary.json"

# Audience events that carry decision information usable by benchmarks.
_TRACKED_EVENTS = frozenset({
    "WEREWOLF_KILL", "SEER_CHECK", "WITCH_SAVE", "WITCH_POISON",
    "HUNTER_SHOT", "WOLF_KING_SHOT", "GUARD_PROTECT",
})


def _read_jsonl(path: str) -> list[dict]:
    """Read a JSONL file into dicts, skipping malformed or missing files."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _load_manifest_entry(data_dir: str, game_id: str) -> Optional[dict]:
    """Return the game's manifest entry from index.json, or None."""
    path = os.path.join(data_dir, "games", "index.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("game_id") == game_id:
            return entry
    return None


def _percentile(values: list[int], pct: int) -> Optional[int]:
    """Nearest-rank percentile of non-negative ints, or None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = min(max(math.ceil(pct / 100 * len(ordered)), 1), len(ordered))
    return ordered[rank - 1]


def _round_bucket(rounds: dict, rnd: object) -> dict:
    key = rnd if type(rnd) is int else 0
    bucket = rounds.get(key)
    if bucket is None:
        bucket = {
            "speeches": [], "votes": [], "deaths": [], "night_actions": [],
            "technical_abstains": 0, "rejected_actions": 0,
            "exiled": None, "tally": {},
        }
        rounds[key] = bucket
    return bucket


def _death_key(death: object) -> tuple:
    if not isinstance(death, dict):
        return ()
    return (death.get("player_seat"), death.get("cause"), death.get("round_number"))


def _merge_deaths(bucket: dict, deaths: object) -> None:
    """Append one round's death batch, skipping seats it already recorded.

    ``log_deaths`` fires once per batch — the night's settlement, a day
    interruption, a delayed death — and several batches can land in one round,
    so a round's deaths are the union of its batches in time order. Assignment
    instead of merging dropped everything but the last batch (13 of ~200 games
    lost a seat that way, including the night kill of a round whose day a
    Crimson Envoy went on to interrupt). The key check makes a replayed batch
    idempotent: a crash between ``log_deaths`` and the stage advance re-runs
    stage 0 over the same seats, and a summary must not count a seat twice.
    """
    seen = {_death_key(existing) for existing in bucket["deaths"]}
    for death in (deaths if isinstance(deaths, list) else []):
        key = _death_key(death)
        if not key or key in seen:
            continue
        seen.add(key)
        bucket["deaths"].append(death)


def _int_or_zero(value: object) -> int:
    return value if type(value) is int else 0


def _summarize(
    game_id: str,
    records: list[dict],
    llm_records: list[dict],
    manifest_entry: Optional[dict],
) -> dict:
    roles: dict[str, dict] = {}
    rounds: dict[int, dict] = {}
    phase_ms: dict[str, int] = {}
    slow_rules: list[dict] = []
    outcome: dict[str, Optional[object]] = {"winner": None, "reason": None}
    log_created_at: Optional[str] = None
    log_finished_at: Optional[str] = None
    model_errors = 0
    final_round: Optional[int] = None

    for record in records:
        op = record.get("operation")
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        rnd = record.get("round")

        if op == "role_init":
            log_created_at = record.get("timestamp")
            players = data.get("players")
            if isinstance(players, dict):
                for seat, info in players.items():
                    if isinstance(info, dict) and "role" in info:
                        roles[str(seat)] = {
                            "role": info.get("role"), "camp": info.get("camp"),
                        }
        elif op == "game_over":
            log_finished_at = record.get("timestamp")
            outcome["winner"] = data.get("winner")
            outcome["reason"] = data.get("reason")
            if type(rnd) is int:
                final_round = rnd
        elif op == "night_deaths":
            _merge_deaths(_round_bucket(rounds, rnd), data.get("deaths"))
        elif op == "speak":
            text = data.get("text")
            _round_bucket(rounds, rnd)["speeches"].append({
                "seat": record.get("seat"),
                "chars": len(text) if isinstance(text, str) else 0,
            })
        elif op == "vote":
            _round_bucket(rounds, rnd)["votes"].append({
                "seat": record.get("seat"), "target": data.get("target"),
            })
        elif op == "vote_result":
            bucket = _round_bucket(rounds, rnd)
            bucket["exiled"] = data.get("exiled")
            tally = data.get("tally")
            bucket["tally"] = tally if isinstance(tally, dict) else {}
        elif op == "vote_technical_abstain":
            _round_bucket(rounds, rnd)["technical_abstains"] += 1
        elif op == "audience_action":
            event_type = data.get("event_type")
            if event_type in _TRACKED_EVENTS:
                payload = (
                    data.get("payload")
                    if isinstance(data.get("payload"), dict) else {}
                )
                _round_bucket(rounds, rnd)["night_actions"].append({
                    "event": event_type,
                    "seat": payload.get("seat"),
                    "target_seat": payload.get("target_seat"),
                    "result": payload.get("result"),
                })
        elif op == "model_error":
            model_errors += 1
        elif op == "stage_telemetry":
            scope = data.get("scope")
            if scope == "phase":
                stage = data.get("stage")
                if isinstance(stage, str):
                    phase_ms[stage] = (
                        phase_ms.get(stage, 0) + _int_or_zero(data.get("elapsed_ms"))
                    )
            elif scope == "hook":
                if data.get("code") == "action_rejected":
                    # The model answered something the rules forbid and the
                    # engine stood in with the contract's fallback. Counted per
                    # round so a report can tell a giving-up model from an
                    # outage; the slow-rule bucket keeps only real timings.
                    _round_bucket(rounds, rnd)["rejected_actions"] += 1
                    continue
                slow_rules.append({
                    "stage": data.get("stage"),
                    "label": data.get("label"),
                    "elapsed_ms": data.get("elapsed_ms"),
                })

    manifest = manifest_entry if isinstance(manifest_entry, dict) else {}
    winner = outcome["winner"]
    if winner is None:
        winner = manifest.get("winner")

    return {
        "game_id": game_id,
        "created_at": manifest.get("created_at") or log_created_at,
        "finished_at": manifest.get("finished_at") or log_finished_at,
        "winner": winner,
        "reason": outcome["reason"],
        "rounds_played": (
            final_round if final_round is not None else (max(rounds) if rounds else 0)
        ),
        "roles": roles,
        "rounds": {str(rnd): rounds[rnd] for rnd in sorted(rounds)},
        "llm": _llm_aggregate(llm_records),
        "engine": {"phase_ms": phase_ms, "slow_rules": slow_rules},
        "model_errors": model_errors,
        "model_snapshot": manifest.get("model_snapshot"),
        "model_snapshot_version": manifest.get("model_snapshot_version"),
        "model_assignment_known": _has_known_model_assignment(manifest),
        "pipeline_version": manifest.get("pipeline_version"),
    }


def _llm_aggregate(records: list[dict]) -> dict:
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    elapsed: list[int] = []
    by_kind: dict[str, int] = {}
    errors = 0
    fallbacks = 0
    retried = 0

    for record in records:
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        kind = data.get("call_kind")
        if isinstance(kind, str):
            by_kind[kind] = by_kind.get(kind, 0) + 1
        for field, target in (
            ("prompt_tokens", "prompt"),
            ("completion_tokens", "completion"),
            ("total_tokens", "total"),
        ):
            value = data.get(field)
            if type(value) is int:
                tokens[target] += value
        ms = data.get("elapsed_ms")
        if type(ms) is int and ms >= 0:
            elapsed.append(ms)
        # The inner ladder's own count: one llm_calls row per logical call, so
        # this is the only truthful source for a retry rate. `model_attempts`
        # cannot express it (one row per dispatch, retries happen inside it).
        if data.get("retried") is True:
            retried += 1
        parse_result = data.get("parse_result")
        if parse_result == "error" or data.get("failure_code") is not None:
            errors += 1
        elif parse_result in ("fallback", "parse_failed"):
            fallbacks += 1

    return {
        "calls": len(records),
        "errors": errors,
        "fallbacks": fallbacks,
        "retried": retried,
        "tokens": tokens,
        "elapsed_ms": {
            "p50": _percentile(elapsed, 50),
            "p95": _percentile(elapsed, 95),
            "p99": _percentile(elapsed, 99),
        },
        "calls_by_kind": by_kind,
    }


def build_game_summary(data_dir: str, game_id: str) -> dict:
    """Build one game's benchmark summary from its archived logs."""
    game_dir = os.path.join(data_dir, "games", game_id)
    records = _read_jsonl(os.path.join(game_dir, "game.log"))
    if not records:
        raise FileNotFoundError(
            f"no game.log found for game {game_id!r} under {data_dir!r}",
        )
    llm_records = _read_jsonl(os.path.join(game_dir, "llm_calls.log"))
    manifest_entry = _load_manifest_entry(data_dir, game_id)
    return _summarize(game_id, records, llm_records, manifest_entry)


def write_game_summary(data_dir: str, game_id: str, summary: dict) -> str:
    """Persist a summary next to the game's logs and return the path."""
    path = os.path.join(data_dir, "games", game_id, _SUMMARY_FILENAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return path


def build_and_write_summary(data_dir: str, game_id: str) -> str:
    """Build and persist one summary; returns the written path."""
    return write_game_summary(
        data_dir, game_id, build_game_summary(data_dir, game_id),
    )


def read_game_summary(data_dir: str, game_id: str) -> Optional[dict]:
    """Best-effort read of a game's ``summary.json``.

    ``None`` when the file is absent, unreadable, or not a JSON object: a game
    that predates summaries (or one whose file was hand-edited) must not fail a
    whole benchmark report. The report counts these as ``summaries_missing``.
    """
    path = os.path.join(data_dir, "games", game_id, _SUMMARY_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
