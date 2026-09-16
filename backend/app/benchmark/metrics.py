"""Benchmark metrics computed from per-game summaries.

All functions are pure: they take summary dicts (see
``app.services.game_summary``) and return plain data. Rates are pooled
across games (sum of hits divided by sum of opportunities) so small
samples do not average misleadingly.
"""

from __future__ import annotations

from typing import Optional

_WEREWOLF_ROLE = "wolf-killer-werewolf"
_WEREWOLF_ROLES = frozenset({_WEREWOLF_ROLE, "wolf-killer-werewolf-king"})
_WEREWOLF_CAMP = "werewolf"
_GOD_ROLE_TOKENS = ("seer", "witch", "hunter", "guard", "idiot")
_ROUNDS_KEY = "rounds"


def _is_god(role: Optional[str]) -> bool:
    if not isinstance(role, str):
        return False
    return any(token in role for token in _GOD_ROLE_TOKENS)


def _is_wolf_role(role: Optional[str]) -> bool:
    return role in _WEREWOLF_ROLES


def _roles_by_seat(summary: dict) -> dict[str, str]:
    roles = summary.get("roles")
    if not isinstance(roles, dict):
        return {}
    return {
        str(seat): info.get("role")
        for seat, info in roles.items()
        if isinstance(info, dict)
    }


def _rounds(summary: dict) -> list[dict]:
    rounds = summary.get(_ROUNDS_KEY)
    if not isinstance(rounds, dict):
        return []
    return [bucket for bucket in rounds.values() if isinstance(bucket, dict)]


def _rate(hits: int, opportunities: int) -> Optional[float]:
    if opportunities <= 0:
        return None
    return round(hits / opportunities, 4)


def compute_outcome_metrics(summaries: list[dict]) -> dict:
    """Win rates by camp and role, plus game length statistics."""
    finished = 0
    wolf_wins = 0
    good_wins = 0
    total_rounds = 0
    role_stats: dict[str, dict[str, int]] = {}

    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        winner = summary.get("winner")
        if winner is None:
            continue
        finished += 1
        total_rounds += _rounds_value(summary)
        if winner == _WEREWOLF_CAMP:
            wolf_wins += 1
        else:
            good_wins += 1
        for _, role in _roles_by_seat(summary).items():
            if not isinstance(role, str):
                continue
            stats = role_stats.setdefault(role, {"games": 0, "wins": 0})
            stats["games"] += 1
            camp = (
                _WEREWOLF_CAMP if _is_wolf_role(role) else "good"
            )
            if winner == camp:
                stats["wins"] += 1

    games = len(summaries)
    return {
        "games": games,
        "finished": finished,
        "aborted": games - finished,
        "win_rate_by_camp": {
            "werewolf": _rate(wolf_wins, finished),
            "good": _rate(good_wins, finished),
        },
        "win_rate_by_role": {
            role: _rate(stats["wins"], stats["games"])
            for role, stats in sorted(role_stats.items())
        },
        "games_by_role": {
            role: stats["games"] for role, stats in sorted(role_stats.items())
        },
        "avg_rounds": _rate(total_rounds, finished),
    }


def _rounds_value(summary: dict) -> int:
    value = summary.get("rounds_played")
    return value if type(value) is int else 0


def compute_decision_metrics(summaries: list[dict]) -> dict:
    """Pooled decision-quality rates across all games.

    The seer, witch and hunter metrics do not need the actor's identity:
    only whether the targeted seat actually was a werewolf (or, for wolf
    kills, a god role).
    """
    pool = _DecisionPool()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        roles = _roles_by_seat(summary)
        for bucket in _rounds(summary):
            for action in bucket.get("night_actions") or []:
                pool.add_night_action(action, roles)
            for vote in bucket.get("votes") or []:
                pool.add_vote(vote, roles)
            exiled = bucket.get("exiled")
            pool.add_exile(exiled, roles)
    return pool.to_metrics()


class _DecisionPool:
    """Accumulates decision hit/miss counts across rounds and games."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    def _add(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def add_night_action(self, action: object, roles: dict[str, str]) -> None:
        if not isinstance(action, dict):
            return
        event = action.get("event")
        target = action.get("target_seat")
        target_role = roles.get(str(target)) if target is not None else None
        if event == "SEER_CHECK":
            self._add("seer_checks")
            if _is_wolf_role(target_role):
                self._add("seer_check_hits")
        elif event == "WITCH_SAVE":
            self._add("witch_saves")
        elif event == "WITCH_POISON":
            self._add("witch_poisons")
            if _is_wolf_role(target_role):
                self._add("witch_poison_hits")
        elif event == "HUNTER_SHOT":
            self._add("hunter_shots")
            if _is_wolf_role(target_role):
                self._add("hunter_shot_hits")
        elif event == "WEREWOLF_KILL":
            self._add("wolf_kills")
            if _is_god(target_role):
                self._add("wolf_kill_god_hits")

    def add_vote(self, vote: object, roles: dict[str, str]) -> None:
        if not isinstance(vote, dict):
            return
        seat = vote.get("seat")
        voter_role = roles.get(str(seat))
        target = vote.get("target")
        if _is_wolf_role(voter_role):
            self._add("wolf_votes")
            self._add_wolf_vote_target(target)
            return
        if not isinstance(voter_role, str):
            return
        self._add("good_votes")
        if target is None:
            self._add("good_vote_abstains")
        elif _is_wolf_role(roles.get(str(target))):
            self._add("good_vote_hits")

    def _add_wolf_vote_target(self, target: object) -> None:
        if target is None:
            return
        key = str(target)
        targets = self.counters.setdefault("_wolf_vote_targets", {})
        targets[key] = targets.get(key, 0) + 1

    def add_exile(self, exiled: object, roles: dict[str, str]) -> None:
        if exiled is None:
            return
        self._add("exiles")
        exiled_role = roles.get(str(exiled))
        if _is_wolf_role(exiled_role):
            self._add("exile_wolf_hits")
        elif _is_god(exiled_role):
            self._add("exile_god_misfires")

    def to_metrics(self) -> dict:
        counters = self.counters
        wolf_votes = counters.get("wolf_votes", 0)
        wolf_targets = counters.get("_wolf_vote_targets", {})
        distinct = len(wolf_targets)
        return {
            "seer_check_hit_rate": _rate(
                counters.get("seer_check_hits", 0), counters.get("seer_checks", 0),
            ),
            "witch_save_uses": counters.get("witch_saves", 0),
            "witch_poison_uses": counters.get("witch_poisons", 0),
            "witch_poison_hit_rate": _rate(
                counters.get("witch_poison_hits", 0),
                counters.get("witch_poisons", 0),
            ),
            "hunter_shot_hit_rate": _rate(
                counters.get("hunter_shot_hits", 0),
                counters.get("hunter_shots", 0),
            ),
            "wolf_kill_god_rate": _rate(
                counters.get("wolf_kill_god_hits", 0),
                counters.get("wolf_kills", 0),
            ),
            "good_vote_hit_rate": _rate(
                counters.get("good_vote_hits", 0), counters.get("good_votes", 0),
            ),
            "good_vote_abstain_rate": _rate(
                counters.get("good_vote_abstains", 0),
                counters.get("good_votes", 0),
            ),
            "exile_wolf_rate": _rate(
                counters.get("exile_wolf_hits", 0), counters.get("exiles", 0),
            ),
            "exile_god_misfire_rate": _rate(
                counters.get("exile_god_misfires", 0), counters.get("exiles", 0),
            ),
            "wolf_vote_concentration": (
                _rate(_max_target_votes(wolf_targets), wolf_votes)
                if wolf_votes else None
            ),
            "wolf_vote_distinct_targets": distinct if wolf_votes else 0,
        }


def _max_target_votes(targets: dict[str, int]) -> int:
    return max(targets.values()) if targets else 0


def compute_compliance_metrics(summaries: list[dict]) -> dict:
    llm_calls = 0
    fallbacks = 0
    errors = 0
    technical_abstains = 0
    model_errors = 0
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        model_errors += _int(summary.get("model_errors"))
        llm = summary.get("llm")
        if isinstance(llm, dict):
            total_calls = _int(llm.get("calls"))
            llm_calls += total_calls
            errors += _int(llm.get("errors"))
            fallbacks += _int(llm.get("fallbacks"))
        rounds = summary.get(_ROUNDS_KEY)
        if isinstance(rounds, dict):
            for bucket in rounds.values():
                if isinstance(bucket, dict):
                    technical_abstains += _int(bucket.get("technical_abstains"))
    return {
        "llm_calls": llm_calls,
        "llm_fallback_rate": _rate(fallbacks, llm_calls),
        "llm_error_rate": _rate(errors, llm_calls),
        "technical_abstains": technical_abstains,
        "model_errors": model_errors,
    }


def _int(value: object) -> int:
    return value if type(value) is int else 0


_LLM_EMPTY = {
    "tokens_per_game": {"prompt": None, "completion": None, "total": None},
    "calls_per_game": None,
    "elapsed_ms_p50": None,
    "elapsed_ms_p95": None,
    "elapsed_ms_p99": None,
}


def compute_llm_metrics(summaries: list[dict]) -> dict:
    """Average per-game token and latency statistics over games with LLM data."""
    prompt = completion = total = 0
    calls = 0
    p50 = p95 = p99 = 0
    valid = 0
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        llm = summary.get("llm")
        if not isinstance(llm, dict):
            continue
        valid += 1
        tokens = llm.get("tokens")
        if isinstance(tokens, dict):
            prompt += _int(tokens.get("prompt"))
            completion += _int(tokens.get("completion"))
            total += _int(tokens.get("total"))
        calls += _int(llm.get("calls"))
        elapsed = llm.get("elapsed_ms")
        if isinstance(elapsed, dict):
            p50 += _int(elapsed.get("p50"))
            p95 += _int(elapsed.get("p95"))
            p99 += _int(elapsed.get("p99"))
    if valid == 0:
        return dict(_LLM_EMPTY)
    return {
        "tokens_per_game": {
            "prompt": round(prompt / valid, 1),
            "completion": round(completion / valid, 1),
            "total": round(total / valid, 1),
        },
        "calls_per_game": round(calls / valid, 1),
        "elapsed_ms_p50": round(p50 / valid, 1),
        "elapsed_ms_p95": round(p95 / valid, 1),
        "elapsed_ms_p99": round(p99 / valid, 1),
    }


def aggregate(summaries: list[dict]) -> dict:
    """Compute the full benchmark aggregate for a list of game summaries."""
    return {
        "games": len(summaries),
        "outcome": compute_outcome_metrics(summaries),
        "decision": compute_decision_metrics(summaries),
        "compliance": compute_compliance_metrics(summaries),
        "llm": compute_llm_metrics(summaries),
    }
