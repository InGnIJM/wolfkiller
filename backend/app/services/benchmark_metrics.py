"""Deterministic metrics over frozen native benchmark facts.

``BenchmarkMetrics.compute`` accepts four sequences of plain dictionaries and
never reads a database, clock, network, or model:

* ``games``: ``game_id``, winner (``winner`` or ``winner_camp``), rounds
  (``rounds`` or ``rounds_played``), duration (``duration_ms`` or
  ``active_elapsed_ms``), and optional ``players``/``roles``.  A player has
  ``seat``, ``role_id``/``role`` and ``camp_id``/``camp``.
* ``items``: ``game_id`` and, for paired regression, ``pair_id``,
  ``variant`` (``baseline`` or ``candidate``), plus either numeric ``score``
  or ``evaluation_camp``.  These fields may instead be inside ``assignment``
  or JSON-encoded ``assignment_json``.
* ``model_requests``: terminal ``status`` and optional ``normalized_result``.
  A resolved ``pass``/``abstain`` result is counted as an abstention.
* ``model_attempts``: ``elapsed_ms``, ``usage_known`` and token columns.

The returned digest hashes canonical JSON with each fact table sorted, so
database row order and dictionary insertion order cannot change a report.
Percentiles use linear interpolation.  The paired statistic is candidate
score minus baseline score, bootstrapped over complete pairs with a private,
fixed-seed PRNG.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


METRIC_VERSION = "v2"
_BOOTSTRAP_SEED = 1729
_BOOTSTRAP_SAMPLES = 2000
_CONFIDENCE = 0.95

_SUCCESS_STATUSES = frozenset({
    "accepted", "completed", "consumed", "ok", "resolved", "succeeded", "success",
})
_ABSTAIN_STATUSES = frozenset({"abstain", "abstained", "skipped"})
_ABSTAIN_ACTIONS = frozenset({"abstain", "no_vote", "pass", "skip"})
_PENDING_STATUSES = frozenset({"in_flight", "prepared", "unknown"})
_CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})
_BASELINE_VARIANTS = frozenset({"base", "baseline", "control"})
_CANDIDATE_VARIANTS = frozenset({"candidate", "treatment"})


class BenchmarkMetrics:
    """Compute a versioned report from an immutable snapshot of fact rows."""

    @classmethod
    def compute(
        cls,
        *,
        games: Sequence[Mapping[str, Any]],
        items: Sequence[Mapping[str, Any]],
        model_requests: Sequence[Mapping[str, Any]],
        model_attempts: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Return ``metric_version``, canonical ``input_digest`` and summary."""
        facts = {
            "games": _canonical_rows(games, "games"),
            "items": _canonical_rows(items, "items"),
            "model_requests": _canonical_rows(model_requests, "model_requests"),
            "model_attempts": _canonical_rows(model_attempts, "model_attempts"),
        }
        payload = _canonical_json(facts)
        return {
            "metric_version": METRIC_VERSION,
            "input_digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "summary": {
                "games": _game_metrics(facts["games"]),
                "requests": _request_metrics(facts["model_requests"]),
                "attempts": _attempt_metrics(facts["model_attempts"]),
                "model_performance": _model_performance(
                    facts["games"], facts["items"],
                ),
                "mixed_uncertainty": _mixed_uncertainty(
                    facts["games"], facts["items"],
                ),
                "paired_regression": _paired_regression(
                    facts["games"], facts["items"],
                ),
                "paired_latency_ms": _paired_latency(
                    facts["games"], facts["items"], facts["model_attempts"],
                ),
            },
        }


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"benchmark facts are not canonical JSON: {error}") from error


def _canonical_rows(
    rows: Sequence[Mapping[str, Any]], name: str,
) -> list[dict[str, Any]]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise TypeError(f"{name} must be a sequence of dictionaries")
    copied: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError(f"{name} must contain only dictionaries")
        plain = dict(row)
        _canonical_json(plain)
        copied.append(plain)
    return sorted(copied, key=_canonical_json)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _nonnegative_number(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number >= 0 else None


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return _rounded(sum(values) / len(values))


def _rounded(value: float) -> float:
    return round(value, 6)


def _rate(wins: int, games: int) -> float | None:
    return None if games == 0 else _rounded(wins / games)


def _winner(game: Mapping[str, Any]) -> str | None:
    winner = game.get("winner", game.get("winner_camp"))
    return winner if isinstance(winner, str) and winner else None


def _players(game: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = game.get("players", game.get("roles", game.get("seat_roles", ())))
    if isinstance(raw, Mapping):
        values: list[dict[str, Any]] = []
        for seat, info in raw.items():
            if isinstance(info, Mapping):
                values.append({"seat": seat, **dict(info)})
            elif isinstance(info, str):
                values.append({"seat": seat, "role_id": info})
        return values
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [dict(player) for player in raw if isinstance(player, Mapping)]
    return []


def _role_and_camp(player: Mapping[str, Any]) -> tuple[str | None, str | None]:
    role = player.get("role_id", player.get("role"))
    camp = player.get("camp_id", player.get("camp"))
    role = role if isinstance(role, str) and role else None
    camp = camp if isinstance(camp, str) and camp else None
    return role, camp


def _add_stratum(
    pool: dict[str, list[int]], key: object, won: bool,
) -> None:
    if isinstance(key, bool) or not isinstance(key, (str, int)):
        return
    label = str(key)
    if not label:
        return
    counts = pool.setdefault(label, [0, 0])
    counts[0] += 1
    counts[1] += int(won)


def _strata(pool: Mapping[str, list[int]]) -> dict[str, dict[str, Any]]:
    return {
        key: {"games": counts[0], "wins": counts[1],
              "win_rate": _rate(counts[1], counts[0])}
        for key, counts in sorted(pool.items())
    }


def _game_metrics(games: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    camp_wins: dict[str, int] = {}
    by_seat: dict[str, list[int]] = {}
    by_role: dict[str, list[int]] = {}
    rounds: list[float] = []
    durations: list[float] = []
    completed = 0
    interrupted_games = 0
    for game in games:
        interruptions = game.get("interruption_count", 0)
        if type(interruptions) is int and interruptions > 0:
            interrupted_games += 1
        winner = _winner(game)
        if winner is None:
            continue
        completed += 1
        camp_wins[winner] = camp_wins.get(winner, 0) + 1
        round_count = _nonnegative_number(
            game.get("rounds", game.get("rounds_played")),
        )
        duration = _nonnegative_number(
            game.get("duration_ms", game.get("active_elapsed_ms")),
        )
        if round_count is not None:
            rounds.append(round_count)
        if duration is not None:
            durations.append(duration)
        for player in _players(game):
            role, camp = _role_and_camp(player)
            if camp is None:
                continue
            won = camp == winner
            _add_stratum(by_seat, player.get("seat"), won)
            _add_stratum(by_role, role, won)
    return {
        "total": len(games),
        "completed": completed,
        "interrupted_games": interrupted_games,
        "interruption_rate": _rate(interrupted_games, len(games)),
        "win_rate_by_camp": {
            camp: _rate(wins, completed)
            for camp, wins in sorted(camp_wins.items())
        },
        "win_rate_by_seat": _strata(by_seat),
        "win_rate_by_role": _strata(by_role),
        "average_rounds": _mean(rounds),
        "average_duration_ms": _mean(durations),
    }


def _result(request: Mapping[str, Any]) -> Mapping[str, Any]:
    result = request.get("normalized_result")
    if isinstance(result, Mapping):
        return result
    if isinstance(result, str):
        try:
            decoded = json.loads(result)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, Mapping) else {}
    return {}


def _request_class(request: Mapping[str, Any]) -> str:
    status = request.get("status")
    status = status.lower() if isinstance(status, str) else ""
    result = _result(request)
    action = result.get("action_type")
    action = action.lower() if isinstance(action, str) else ""
    if status in _ABSTAIN_STATUSES or action in _ABSTAIN_ACTIONS:
        return "abstained"
    if status in _SUCCESS_STATUSES:
        return "successful"
    if status in _PENDING_STATUSES:
        return "pending"
    if status in _CANCELLED_STATUSES:
        return "cancelled"
    return "failed"


def _logical_latency_ms(request: Mapping[str, Any]) -> float | None:
    explicit = _nonnegative_number(request.get("logical_elapsed_ms"))
    if explicit is not None:
        return explicit
    started, finished = request.get("created_at"), request.get("updated_at")
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    try:
        elapsed = (
            datetime.fromisoformat(finished.replace("Z", "+00:00"))
            - datetime.fromisoformat(started.replace("Z", "+00:00"))
        ).total_seconds() * 1000
    except ValueError:
        return None
    return _rounded(elapsed) if elapsed >= 0 else None


def _is_vote(request: Mapping[str, Any]) -> bool:
    position = request.get("action_position")
    return isinstance(position, str) and "vote" in position.lower()


def _is_fallback(request: Mapping[str, Any], category: str) -> bool:
    result = _result(request)
    marker = result.get("fallback", result.get("system_fallback"))
    source = result.get("source")
    action = result.get("action_type")
    return (
        category == "failed"
        or marker is True
        or source == "system_fallback"
        or action == "technical_abstain"
    )


def _request_metrics(requests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {
        "successful": 0, "abstained": 0, "failed": 0,
        "pending": 0, "cancelled": 0,
    }
    technical_abstained = 0
    terminal_votes = 0
    fallback = 0
    latencies: list[float] = []
    for request in requests:
        category = _request_class(request)
        counts[category] += 1
        action = _result(request).get("action_type")
        eligible = category in {"successful", "abstained", "failed"}
        is_vote = _is_vote(request)
        if eligible and is_vote:
            terminal_votes += 1
        if eligible and (action == "technical_abstain" or (category == "failed" and is_vote)):
            technical_abstained += 1
        if eligible and _is_fallback(request, category):
            fallback += 1
        latency = _logical_latency_ms(request) if eligible else None
        if latency is not None:
            latencies.append(latency)
    total = len(requests)
    valid = counts["successful"] + counts["abstained"]
    eligible_terminal = valid + counts["failed"]
    unknown = sum(
        isinstance(request.get("status"), str)
        and str(request["status"]).lower() == "unknown"
        for request in requests
    )
    return {
        "total": total,
        **counts,
        "eligible_terminal": eligible_terminal,
        "unknown": unknown,
        "valid": valid,
        "technical_abstained": technical_abstained,
        "terminal_votes": terminal_votes,
        "fallback": fallback,
        "valid_rate": _rate(valid, eligible_terminal),
        "success_rate": _rate(counts["successful"], eligible_terminal),
        "abstain_rate": _rate(counts["abstained"], eligible_terminal),
        "failure_rate": _rate(counts["failed"], eligible_terminal),
        "fallback_rate": _rate(fallback, eligible_terminal),
        "technical_abstain_rate": _rate(technical_abstained, terminal_votes),
        "logical_latency_ms": {
            "count": len(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
    }


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return _rounded(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _token_value(attempt: Mapping[str, Any], key: str) -> int:
    value = attempt.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _attempt_metrics(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    elapsed = [
        value for attempt in attempts
        if (value := _nonnegative_number(attempt.get("elapsed_ms"))) is not None
    ]
    known = [attempt for attempt in attempts if attempt.get("usage_known") is True]
    attempts_per_request: dict[tuple[object, object], int] = {}
    for attempt in attempts:
        key = (attempt.get("game_id"), attempt.get("request_id"))
        attempts_per_request[key] = attempts_per_request.get(key, 0) + 1
    executed_requests = len(attempts_per_request)
    retried_requests = sum(count > 1 for count in attempts_per_request.values())
    prompt_tokens = sum(_token_value(row, "prompt_tokens") for row in known)
    completion_tokens = sum(
        _token_value(row, "completion_tokens") for row in known
    )
    total_tokens = sum(_token_value(row, "total_tokens") for row in known)
    unknown_count = len(attempts) - len(known)
    completeness_rate = _rate(len(known), len(attempts))
    return {
        "total": len(attempts),
        "latency_ms": {
            "count": len(elapsed),
            "p50": _percentile(elapsed, 0.50),
            "p95": _percentile(elapsed, 0.95),
            "p99": _percentile(elapsed, 0.99),
        },
        "known_tokens": {
            "usage_count": len(known),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "token_usage": {
            "known_attempts": len(known),
            "unknown_attempts": unknown_count,
            "known_prompt_tokens": prompt_tokens,
            "known_completion_tokens": completion_tokens,
            "known_total_tokens": total_tokens,
            "completeness_rate": completeness_rate,
        },
        "unknown_usage_count": unknown_count,
        "usage_completeness_rate": completeness_rate,
        "executed_requests": executed_requests,
        "retried_requests": retried_requests,
        "retry_rate": _rate(retried_requests, executed_requests),
    }


def _assignment(item: Mapping[str, Any]) -> dict[str, Any]:
    assignment = item.get("assignment")
    if isinstance(assignment, Mapping):
        return dict(assignment)
    encoded = item.get("assignment_json")
    if isinstance(encoded, Mapping):
        return dict(encoded)
    if isinstance(encoded, str):
        try:
            decoded = json.loads(encoded)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _field(item: Mapping[str, Any], assignment: Mapping[str, Any], key: str) -> Any:
    return item[key] if key in item else assignment.get(key)


def _model_name(model: object) -> str:
    if not isinstance(model, Mapping):
        return "default"
    for key in ("model_config_id", "config_id", "model_id", "name"):
        value = model.get(key)
        if isinstance(value, str) and value:
            return value
    return "default"


def _seat_number(value: object) -> int | None:
    if type(value) is int:
        seat = value
    elif isinstance(value, str):
        try:
            seat = int(value)
        except ValueError:
            return None
    else:
        return None
    return seat if seat > 0 else None


def _assigned_models(assignment: Mapping[str, Any]) -> list[tuple[int, str]]:
    seat_models = assignment.get("seat_models")
    if isinstance(seat_models, Mapping):
        rows = []
        for raw_seat, model in seat_models.items():
            seat = _seat_number(raw_seat)
            if seat is not None:
                rows.append((seat, _model_name(model)))
        return rows
    seats = assignment.get("seats")
    if not isinstance(seats, Sequence) or isinstance(seats, (str, bytes)):
        return []
    model = _model_name(assignment.get("model"))
    return [
        (seat, model)
        for raw_seat in seats
        if (seat := _seat_number(raw_seat)) is not None
    ]


def _model_performance(
    games: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    games_by_id = {
        game["game_id"]: game for game in games
        if isinstance(game.get("game_id"), str) and _winner(game) is not None
    }
    counts: dict[tuple[str, str, str], list[int]] = {}
    for item in items:
        game_id = item.get("game_id")
        game = games_by_id.get(game_id) if isinstance(game_id, str) else None
        if game is None:
            continue
        winner = _winner(game)
        players = {
            seat: player
            for player in _players(game)
            if (seat := _seat_number(player.get("seat"))) is not None
        }
        for seat, model in _assigned_models(_assignment(item)):
            player = players.get(seat)
            if player is None:
                continue
            role, camp = _role_and_camp(player)
            if camp is None:
                continue
            won = camp == winner
            dimensions = [("camp", camp)]
            if role is not None:
                dimensions.append(("role", role))
            for dimension, group in dimensions:
                bucket = counts.setdefault((model, dimension, group), [0, 0])
                bucket[0] += 1
                bucket[1] += int(won)

    rows = []
    for (model, dimension, group), (samples, wins) in sorted(counts.items()):
        row = {
            "id": f"{model}:{dimension}:{group}",
            "model": model,
            "samples": samples,
            "wins": wins,
            "win_rate": _rate(wins, samples),
        }
        row[dimension] = group
        rows.append(row)
    return rows


def _mixed_observations(
    games: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    games_by_id = {
        game["game_id"]: game for game in games
        if isinstance(game.get("game_id"), str) and _winner(game) is not None
    }
    rows: list[dict[str, Any]] = []
    for item in items:
        assignment = _assignment(item)
        if _field(item, assignment, "variant") != "mixed":
            continue
        block_index = item.get("block_index")
        if type(block_index) is not int or block_index < 0:
            continue
        game_id = item.get("game_id")
        game = games_by_id.get(game_id) if isinstance(game_id, str) else None
        if game is None:
            continue
        winner = _winner(game)
        players = {
            seat: player
            for player in _players(game)
            if (seat := _seat_number(player.get("seat"))) is not None
        }
        for seat, model in _assigned_models(assignment):
            player = players.get(seat)
            if player is None:
                continue
            role, camp = _role_and_camp(player)
            if camp is None:
                continue
            won = camp == winner
            dimensions = [("camp", camp)]
            if role is not None:
                dimensions.append(("role", role))
            for dimension, group in dimensions:
                rows.append({
                    "game_id": game_id, "block_index": block_index,
                    "model": model, "dimension": dimension,
                    "group": group, "won": won,
                })
    return rows


def _bootstrap_block_ratio(
    blocks: Mapping[int, tuple[int, int]],
) -> tuple[float | None, float | None]:
    if len(blocks) < 2:
        return None, None
    ordered = [blocks[key] for key in sorted(blocks)]
    generator = random.Random(_BOOTSTRAP_SEED)
    values: list[float] = []
    for _ in range(_BOOTSTRAP_SAMPLES):
        sampled = [generator.choice(ordered) for _ in range(len(ordered))]
        denominator = sum(value[0] for value in sampled)
        if denominator:
            values.append(sum(value[1] for value in sampled) / denominator)
    tail = (1.0 - _CONFIDENCE) / 2.0
    return _percentile(values, tail), _percentile(values, 1.0 - tail)


def _mixed_uncertainty(
    games: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    observations = _mixed_observations(games, items)
    grouped: dict[
        tuple[str, str, str], dict[str, Any]
    ] = {}
    for row in observations:
        key = (row["model"], row["dimension"], row["group"])
        bucket = grouped.setdefault(key, {"blocks": {}, "games": set()})
        block = bucket["blocks"].setdefault(row["block_index"], [0, 0])
        block[0] += 1
        block[1] += int(row["won"])
        bucket["games"].add(row["game_id"])

    result: list[dict[str, Any]] = []
    for (model, dimension, group), bucket in sorted(grouped.items()):
        blocks = {
            index: (counts[0], counts[1])
            for index, counts in bucket["blocks"].items()
        }
        low, high = _bootstrap_block_ratio(blocks)
        valid_seats = sum(counts[0] for counts in blocks.values())
        wins = sum(counts[1] for counts in blocks.values())
        row = {
            "id": f"{model}:{dimension}:{group}", "model": model,
            "games": len(bucket["games"]), "valid_seats": valid_seats,
            "wins": wins, "win_rate": _rate(wins, valid_seats),
            "block_count": len(blocks), "confidence": _CONFIDENCE,
            "samples": _BOOTSTRAP_SAMPLES, "seed": _BOOTSTRAP_SEED,
            "low": low, "high": high,
        }
        row[dimension] = group
        result.append(row)
    return result


def _variant(item: Mapping[str, Any], assignment: Mapping[str, Any]) -> str | None:
    value = _field(item, assignment, "variant")
    if value is None:
        value = _field(item, assignment, "arm")
    value = value.lower() if isinstance(value, str) else ""
    if value in _BASELINE_VARIANTS:
        return "baseline"
    if value in _CANDIDATE_VARIANTS:
        return "candidate"
    return None


def _score(
    item: Mapping[str, Any], assignment: Mapping[str, Any],
    game: Mapping[str, Any] | None,
) -> float | None:
    score = _number(_field(item, assignment, "score"))
    if score is not None:
        return score
    won = _field(item, assignment, "won")
    if isinstance(won, bool):
        return float(won)
    evaluated_camp = _field(item, assignment, "evaluation_camp")
    if evaluated_camp is None:
        evaluated_camp = _field(item, assignment, "target_camp")
    if not isinstance(evaluated_camp, str) or game is None:
        return None
    winner = _winner(game)
    return None if winner is None else float(winner == evaluated_camp)


def _bootstrap(differences: Sequence[float]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "seed": _BOOTSTRAP_SEED,
        "samples": _BOOTSTRAP_SAMPLES,
        "confidence": _CONFIDENCE,
        "low": None,
        "high": None,
    }
    if not differences:
        return result
    generator = random.Random(_BOOTSTRAP_SEED)
    count = len(differences)
    means = [
        sum(generator.choice(differences) for _ in range(count)) / count
        for _ in range(_BOOTSTRAP_SAMPLES)
    ]
    tail = (1.0 - _CONFIDENCE) / 2.0
    result["low"] = _percentile(means, tail)
    result["high"] = _percentile(means, 1.0 - tail)
    return result


def _paired_regression(
    games: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    games_by_id = {
        game["game_id"]: game for game in games
        if isinstance(game.get("game_id"), str)
    }
    pairs: dict[str, dict[str, list[float]]] = {}
    for item in items:
        pair_id = item.get("pair_id")
        if not isinstance(pair_id, str) or not pair_id:
            continue
        assignment = _assignment(item)
        variant = _variant(item, assignment)
        game_id = item.get("game_id")
        game = games_by_id.get(game_id) if isinstance(game_id, str) else None
        score = _score(item, assignment, game)
        if variant is None or score is None:
            continue
        pair = pairs.setdefault(pair_id, {"baseline": [], "candidate": []})
        pair[variant].append(score)

    rows: list[dict[str, Any]] = []
    for pair_id, values in sorted(pairs.items()):
        baseline = _mean(values["baseline"])
        candidate = _mean(values["candidate"])
        if baseline is None or candidate is None:
            continue
        rows.append({
            "pair_id": pair_id,
            "baseline": baseline,
            "candidate": candidate,
            "difference": _rounded(candidate - baseline),
        })
    differences = [row["difference"] for row in rows]
    return {
        "pair_count": len(rows),
        "mean_difference": _mean(differences),
        "differences": rows,
        "bootstrap": _bootstrap(differences),
    }


def _paired_latency(
    games: Sequence[Mapping[str, Any]],
    items: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    games_by_id = {
        game["game_id"]: game for game in games
        if isinstance(game.get("game_id"), str)
    }
    elapsed_by_game: dict[str, list[float]] = {}
    for attempt in attempts:
        game_id = attempt.get("game_id")
        elapsed = _nonnegative_number(attempt.get("elapsed_ms"))
        if isinstance(game_id, str) and elapsed is not None:
            elapsed_by_game.setdefault(game_id, []).append(elapsed)

    pairs: dict[str, dict[str, list[str]]] = {}
    for item in items:
        pair_id = item.get("pair_id")
        if not isinstance(pair_id, str) or not pair_id:
            continue
        assignment = _assignment(item)
        variant = _variant(item, assignment)
        game_id = item.get("game_id")
        if variant is None or not isinstance(game_id, str):
            continue
        pair = pairs.setdefault(pair_id, {"baseline": [], "candidate": []})
        pair[variant].append(game_id)

    rows: list[dict[str, Any]] = []
    excluded_interrupted = 0
    excluded_incomplete = 0
    for pair_id, variants in sorted(pairs.items()):
        game_ids = variants["baseline"] + variants["candidate"]
        interrupted = any(
            type(games_by_id.get(game_id, {}).get("interruption_count")) is int
            and int(games_by_id[game_id]["interruption_count"]) > 0
            for game_id in game_ids
        )
        if interrupted:
            excluded_interrupted += 1
            continue
        baseline = _mean([
            value
            for game_id in variants["baseline"]
            if (value := _mean(elapsed_by_game.get(game_id, ()))) is not None
        ])
        candidate = _mean([
            value
            for game_id in variants["candidate"]
            if (value := _mean(elapsed_by_game.get(game_id, ()))) is not None
        ])
        if baseline is None or candidate is None:
            excluded_incomplete += 1
            continue
        rows.append({
            "pair_id": pair_id, "baseline": baseline, "candidate": candidate,
            "difference": _rounded(candidate - baseline),
        })
    differences = [row["difference"] for row in rows]
    return {
        "pair_count": len(rows),
        "excluded_interrupted_pairs": excluded_interrupted,
        "excluded_incomplete_pairs": excluded_incomplete,
        "mean_difference": _mean(differences),
        "differences": rows,
        "bootstrap": _bootstrap(differences),
    }
