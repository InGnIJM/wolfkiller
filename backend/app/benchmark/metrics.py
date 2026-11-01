"""Benchmark metrics computed from per-game summaries.

All functions are pure: they take summary dicts (see
``app.services.game_summary``) and return plain data. Rates are pooled
across games (sum of hits divided by sum of opportunities) so small
samples do not average misleadingly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from app.roles.registry import builtin_registry

_WEREWOLF_CAMP = "werewolf"
#: The one seat the Wolf Witch's check is aimed at (role ids are stable).
PURE_WHITE_ROLE = "wolf-killer-pure-white"
#: Roles whose nightly action learns another seat's hidden identity or camp —
#: the "information" seats a board's decision metrics are actually about. A
#: run whose boards carry none of these cannot answer the check questions at
#: all, so coverage names which ones were present instead of asking only
#: whether a literal ``wolf-killer-seer`` was dealt.
INFO_ROLES = frozenset({
    "wolf-killer-seer",
    "wolf-killer-awakened-seer",
    "wolf-killer-fool-seer",
    "wolf-killer-pure-white",
    "wolf-killer-mirror-girl",
    "wolf-killer-wolf-witch",
    "wolf-killer-gargoyle",
    "wolf-killer-gravekeeper",
    "wolf-killer-awakened-hidden-wolf",
    "wolf-killer-demon-hunter",
})
_GOD_TAG = "god"
_WOLF_TAG = "wolf"
_ROUNDS_KEY = "rounds"


@lru_cache(maxsize=None)
def _role_tags(role: str) -> frozenset[str]:
    """``RoleSpec.tags`` for a role id, empty for anything the registry lacks.

    The classifier used to be two hand-written string sets
    (``_GOD_ROLE_TOKENS`` / ``_WEREWOLF_ROLES``), which is exactly the kind of
    copy that drifts: it knew 2 wolf ids out of 10 and 5 god tokens out of 11,
    so every 纯白夜影 / 假面舞会 / 狼美人 board was scored with the wrong camp
    and the wrong god/villager split (放逐命中狼 38.4% against the true 54.6%).
    ``RoleSpec.tags`` is the registry's own authority — the same one
    ``rule_engine`` uses for 屠边 — and role ids are never deleted, so archived
    summaries stay classifiable.
    """
    try:
        return builtin_registry.freeze().require(role).tags
    except ValueError:
        return frozenset()


def _is_god(role: Optional[str]) -> bool:
    return isinstance(role, str) and _GOD_TAG in _role_tags(role)


def _is_wolf_role(role: Optional[str]) -> bool:
    return isinstance(role, str) and _WOLF_TAG in _role_tags(role)


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


def _decision_pool(summaries: list[dict]) -> "_DecisionPool":
    """Walk every summary once and accumulate the decision counters."""
    pool = _DecisionPool()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        roles = _roles_by_seat(summary)
        buckets = _rounds(summary)
        if buckets:
            # 有明细的局数：分母为 0 的比率是 None，页面要靠这个数解释「无样本」。
            pool._add("_summaries")
        for bucket in buckets:
            for action in bucket.get("night_actions") or []:
                pool.add_night_action(action, roles)
            for vote in bucket.get("votes") or []:
                pool.add_vote(vote, roles)
            exiled = bucket.get("exiled")
            pool.add_exile(exiled, roles)
    return pool


def compute_decision_metrics(summaries: list[dict]) -> dict:
    """Pooled decision-quality rates across all games.

    The seer, witch and hunter metrics do not need the actor's identity:
    only whether the targeted seat actually was a werewolf (or, for wolf
    kills, a god role). The same rule scores the roles that act silently —
    the Pure White, the Wolf Witch, the Gargoyle, the Demon Hunter and the
    Dancer read their target's true camp from ``roles``.

    Not scored here, deliberately: the Gravekeeper has no model-facing action
    at all (its information is engine-derived), and the Dream Weaver, the
    Nightmare and the Wolf Beauty have no single "hit" their decision aims at.
    """
    return _decision_pool(summaries).to_metrics()


def compute_decision_samples(summaries: list[dict]) -> dict:
    """The denominators behind :func:`compute_decision_metrics`.

    A ``None`` rate means "no opportunity", which is not the same as ``0.0``
    ("tried and missed"), so the interface shows both the rate and its sample.
    """
    counters = _decision_pool(summaries).counters
    return {
        "games": counters.get("_summaries", 0),
        "seer_checks": counters.get("seer_checks", 0),
        "witch_poisons": counters.get("witch_poisons", 0),
        "hunter_shots": counters.get("hunter_shots", 0),
        "wolf_kills": counters.get("wolf_kills", 0),
        "good_votes": counters.get("good_votes", 0),
        "exiles": counters.get("exiles", 0),
        "pure_white_checks": counters.get("pure_white_checks", 0),
        "wolf_witch_checks": counters.get("wolf_witch_checks", 0),
        "gargoyle_checks": counters.get("gargoyle_checks", 0),
        "demon_hunter_hunts": counters.get("demon_hunter_hunts", 0),
        "dance_pools": counters.get("dance_pools", 0),
        "mask_grants": counters.get("mask_grants", 0),
    }


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
        elif event == "PURE_WHITE_REASONING":
            # These four roles publish no outcome event of their own: the
            # reasoning row *is* the decision, and its ``target_seat`` is absent
            # when the model passed (a decline is not an opportunity).
            if target is not None:
                self._add("pure_white_checks")
                if _is_wolf_role(target_role):
                    self._add("pure_white_check_hits")
        elif event == "WOLF_WITCH_REASONING":
            if target is not None:
                self._add("wolf_witch_checks")
                # A role-id comparison, not a tag: the Pure White is a god like
                # any other, and role ids are never deleted, so archived
                # summaries stay classifiable.
                if target_role == PURE_WHITE_ROLE:
                    self._add("wolf_witch_check_hits")
        elif event == "GARGOYLE_REASONING":
            if target is not None:
                self._add("gargoyle_checks")
                if _is_wolf_role(target_role):
                    self._add("gargoyle_check_hits")
        elif event == "DEMON_HUNTER_REASONING":
            if target is not None:
                self._add("demon_hunter_hunts")
                if _is_wolf_role(target_role):
                    self._add("demon_hunter_hunt_hits")
        elif event == "DANCE_POOL":
            self._add("dance_pools")
            pooled = action.get("pool_seats")
            if isinstance(pooled, (list, tuple)) and any(
                _is_wolf_role(roles.get(str(seat))) for seat in pooled
            ):
                self._add("dance_pool_wolf_hits")
        elif event == "MASK_GRANTED":
            self._add("mask_grants")
            if action.get("in_pool") is True:
                self._add("mask_in_pool_hits")

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
            "pure_white_check_hit_rate": _rate(
                counters.get("pure_white_check_hits", 0),
                counters.get("pure_white_checks", 0),
            ),
            "wolf_witch_check_hit_rate": _rate(
                counters.get("wolf_witch_check_hits", 0),
                counters.get("wolf_witch_checks", 0),
            ),
            "gargoyle_check_hit_rate": _rate(
                counters.get("gargoyle_check_hits", 0),
                counters.get("gargoyle_checks", 0),
            ),
            "demon_hunter_hunt_hit_rate": _rate(
                counters.get("demon_hunter_hunt_hits", 0),
                counters.get("demon_hunter_hunts", 0),
            ),
            "dance_pool_wolf_rate": _rate(
                counters.get("dance_pool_wolf_hits", 0),
                counters.get("dance_pools", 0),
            ),
            "mask_in_pool_rate": _rate(
                counters.get("mask_in_pool_hits", 0),
                counters.get("mask_grants", 0),
            ),
            "wolf_vote_concentration": (
                _rate(_max_target_votes(wolf_targets), wolf_votes)
                if wolf_votes else None
            ),
            "wolf_vote_distinct_targets": distinct if wolf_votes else 0,
        }


def _max_target_votes(targets: dict[str, int]) -> int:
    return max(targets.values()) if targets else 0


def compute_board_coverage(summaries: list[dict]) -> dict:
    """Which boards a run actually played, and whether a seer was in any of them.

    A run's scenario pins one board for every item, so "this run is a single
    composition, and it has no seer" used to be invisible: readers saw pooled
    rates without knowing what produced them (eval review §2.12). Derived from
    each summary's own ``roles``, so an archived run keeps answering without the
    scenario having to be re-read.
    """
    boards: dict[tuple[tuple[str, int], ...], int] = {}
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        roles = summary.get("roles")
        if not isinstance(roles, dict):
            continue
        counts: dict[str, int] = {}
        for info in roles.values():
            role = info.get("role") if isinstance(info, dict) else None
            if isinstance(role, str) and role:
                counts[role] = counts.get(role, 0) + 1
        if not counts:
            continue
        key = tuple(sorted(counts.items()))
        boards[key] = boards.get(key, 0) + 1
    dealt = {role for key in boards for role in dict(key)}
    return {
        "boards": [
            {"role_counts": dict(key), "games": games}
            for key, games in sorted(
                boards.items(), key=lambda item: (-item[1], item[0]),
            )
        ],
        # The seer is the one role the metric review called out by name: a run
        # without it cannot answer the seer questions at all.
        "seer_present": "wolf-killer-seer" in dealt,
        # The generalised version: which information roles (any of the nightly
        # check seats) this run's boards actually carried. An empty list means
        # the pooled check hit-rates are all "no opportunity" by construction.
        "info_roles_present": sorted(dealt & INFO_ROLES),
    }
