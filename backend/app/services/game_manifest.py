"""Persistent game index — survives backend restarts.

Stores lightweight game metadata in data/games/index.json so the
frontend game list and detail views continue working after a restart.
"""

from __future__ import annotations
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from app.models.game import GamePhase, SnapshotVersionError

logger = logging.getLogger(__name__)

_LEGACY_ROLE_COUNT_KEYS = frozenset({
    "num_werewolves",
    "num_villagers",
    "num_seers",
    "num_witches",
    "num_hunters",
})

_VALID_PIPELINE_VERSIONS = frozenset({"v1", "v2"})
_CURRENT_EFFECT_SCHEMA = 1
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def default_game_name(player_count: int, now: datetime | None = None) -> str:
    moment = datetime.now(_SHANGHAI) if now is None else now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_SHANGHAI)
    else:
        moment = moment.astimezone(_SHANGHAI)
    return f"{player_count}人局 · {moment.month}月{moment.day}日 {moment:%H:%M}"


def restore_snapshot(
    snapshot: Mapping, *, registry, pipeline_mode: str = "v2",
) -> dict:
    """Validate a persisted game snapshot against the running registry.

    Raises SnapshotVersionError for unknown pipeline versions, role specs the
    running registry cannot serve, effect schemas without a migrator, and any
    attempt to downgrade a V2 game with committed effects back to V1.
    """
    if not isinstance(snapshot, Mapping):
        raise SnapshotVersionError("invalid snapshot")
    if pipeline_mode not in _VALID_PIPELINE_VERSIONS:
        raise SnapshotVersionError("unknown pipeline version")
    pipeline = snapshot.get("pipeline_version")
    if pipeline not in (None, "", "v1", "v2"):
        raise SnapshotVersionError("unknown pipeline version")
    state_revision = snapshot.get("state_revision", 0)
    if (
        pipeline_mode == "v1"
        and pipeline == "v2"
        and isinstance(state_revision, int)
        and not isinstance(state_revision, bool)
        and state_revision > 0
    ):
        raise SnapshotVersionError("cannot downgrade v2 game to v1")
    spec_versions = snapshot.get("spec_versions") or {}
    if not isinstance(spec_versions, Mapping):
        raise SnapshotVersionError("invalid spec versions")
    for role_id, version in spec_versions.items():
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1
        ):
            raise SnapshotVersionError("missing role spec")
        try:
            current = registry.require(role_id).schema_version
        except ValueError:
            raise SnapshotVersionError("missing role spec") from None
        if version > current:
            raise SnapshotVersionError("missing role spec")
    effect_schema = snapshot.get("effect_schema_version")
    if effect_schema not in (None, 0, _CURRENT_EFFECT_SCHEMA):
        raise SnapshotVersionError("missing effect schema migrator")
    return dict(snapshot)


def migrate_legacy_entry(entry: dict, *, registry) -> dict:
    """Explicit v1 -> v2 migrator for archives written before pipeline versioning."""
    entry["pipeline_version"] = "v2"
    entry["registry_digest"] = registry.digest
    entry["spec_versions"] = {
        role_id: spec.schema_version for role_id, spec in registry.specs.items()
    }
    entry["effect_schema_version"] = _CURRENT_EFFECT_SCHEMA
    entry["state_revision"] = 0
    entry["last_consistent_checkpoint"] = None
    return entry


def _is_valid_role_counts(role_counts: object) -> bool:
    if not isinstance(role_counts, dict) or not role_counts:
        return False
    if not all(
        isinstance(role_id, str)
        and bool(role_id)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
        for role_id, count in role_counts.items()
    ):
        return False
    return sum(role_counts.values()) > 0


def _has_valid_config(config: object) -> bool:
    if not isinstance(config, dict) or not config:
        return False
    if "role_counts" in config:
        allowed = {"role_counts", "reveal_on_death"}
        return (
            set(config) <= allowed
            and "role_counts" in config
            and _is_valid_role_counts(config["role_counts"])
            and isinstance(config.get("reveal_on_death", False), bool)
        )
    return (
        set(config) == _LEGACY_ROLE_COUNT_KEYS
        and _is_valid_role_counts(config)
    )


class GameManifest:
    """Lightweight on-disk registry of all games (running + completed)."""

    def __init__(self, data_dir: str = "data"):
        self._dir = Path(data_dir) / "games"
        self._path = self._dir / "index.json"
        self._entries: dict[str, dict] = {}

    # ── Public API ─────────────────────────────────────────────────

    def load_or_rebuild(self, registry=None) -> dict[str, dict]:
        """Return {game_id: metadata} for every game on disk.

        Prefer index.json; if missing or corrupt, rebuild by scanning
        game directories and reading their game.log. When a registry is
        supplied, entries are validated against it and legacy archives are
        migrated through the explicit v1 -> v2 migrator; incompatible
        entries are skipped with a warning.
        """
        self._entries = {}
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    for entry in raw:
                        if not isinstance(entry, dict):
                            logger.warning("Skipping malformed game manifest entry")
                            continue
                        gid = entry.get("game_id")
                        if isinstance(gid, str) and gid:
                            self._entries[gid] = entry
                logger.info(f"Game manifest loaded: {len(self._entries)} games")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load game manifest, rebuilding: {e}")

        # Drop manifest entries whose game directory no longer exists on
        # disk (e.g. games deleted while the server was offline).
        for gid in list(self._entries):
            if not (self._dir / gid).is_dir():
                logger.info(
                    "Dropping stale manifest entry without game directory: %s",
                    gid,
                )
                self._entries.pop(gid)

        # Rebuild from disk for any directories not in the index
        if self._dir.exists():
            for child in sorted(self._dir.iterdir()):
                if not child.is_dir():
                    continue
                gid = child.name
                glog = child / "game.log"
                if not glog.exists():
                    continue
                if gid not in self._entries:
                    meta = self._extract_meta(gid, glog)
                    if meta:
                        self._entries[gid] = meta
                        logger.info(f"Recovered game from disk: {gid}")
                elif not _has_valid_config(self._entries[gid].get("config")):
                    meta = self._extract_meta(gid, glog)
                    if meta and meta["config"]:
                        self._entries[gid]["config"] = meta["config"]
                        self._entries[gid]["player_count"] = meta["player_count"]
                        logger.info(f"Recovered role config from disk: {gid}")

        # Version-validate every entry against the running registry and
        # migrate archives written before pipeline versioning.
        if registry is not None:
            for gid in list(self._entries):
                entry = self._entries[gid]
                if entry.get("pipeline_version") in (None, ""):
                    migrate_legacy_entry(entry, registry=registry)
                    logger.info(f"Migrated legacy game archive to v2: {gid}")
                try:
                    restore_snapshot(entry, registry=registry)
                except SnapshotVersionError as error:
                    logger.warning(
                        "Skipping game archive incompatible with the running "
                        "pipeline: %s (%s)", gid, error,
                    )
                    self._entries.pop(gid)

        # Persist rebuilt index
        if self._entries:
            self._persist()
        return dict(self._entries)

    def add_game(
        self, game_id: str, config: dict,
        model_snapshot: Optional[list] = None,
        name: Optional[str] = None,
    ) -> None:
        entry = self._entries.get(game_id, {})
        role_counts = config.get("role_counts")
        player_count = (
            sum(role_counts.values())
            if isinstance(role_counts, dict)
            else sum(config.values())
        )
        entry.update({
            "game_id": game_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": "waiting",
            "round_number": 0,
            "player_count": player_count,
            "config": config,
            "winner": None,
            "finished_at": None,
        })
        if model_snapshot is not None:
            entry["model_snapshot"] = model_snapshot
        if name is not None:
            entry["name"] = name
        self._entries[game_id] = entry
        self._persist()

    def update_game(
        self, game_id: str, *,
        phase: Optional[str] = None,
        round_number: Optional[int] = None,
        player_count: Optional[int] = None,
        alive_count: Optional[int] = None,
        winner: Optional[str] = None,
        pipeline_version: Optional[str] = None,
        registry_digest: Optional[str] = None,
        spec_versions: Optional[dict] = None,
        effect_schema_version: Optional[int] = None,
        state_revision: Optional[int] = None,
        last_consistent_checkpoint: Optional[str] = None,
        model_snapshot: Optional[list] = None,
        name: Optional[str] = None,
    ) -> None:
        entry = self._entries.get(game_id)
        if entry is None:
            return
        if phase is not None:
            entry["phase"] = phase
        if round_number is not None:
            entry["round_number"] = round_number
        if player_count is not None:
            entry["player_count"] = player_count
        if alive_count is not None:
            entry["alive_count"] = alive_count
        if winner is not None:
            entry["winner"] = winner
            entry["finished_at"] = datetime.now(timezone.utc).isoformat()
        if pipeline_version is not None:
            entry["pipeline_version"] = pipeline_version
        if registry_digest is not None:
            entry["registry_digest"] = registry_digest
        if spec_versions is not None:
            entry["spec_versions"] = spec_versions
        if effect_schema_version is not None:
            entry["effect_schema_version"] = effect_schema_version
        if state_revision is not None:
            entry["state_revision"] = state_revision
        if last_consistent_checkpoint is not None:
            entry["last_consistent_checkpoint"] = last_consistent_checkpoint
        if model_snapshot is not None:
            entry["model_snapshot"] = model_snapshot
        if name is not None:
            entry["name"] = name
        self._persist()

    def get_entry(self, game_id: str) -> Optional[dict]:
        return self._entries.get(game_id)

    def remove_game(self, game_id: str) -> None:
        if game_id not in self._entries:
            return
        self._entries.pop(game_id)
        self._persist()

    # ── Internals ──────────────────────────────────────────────────

    def _extract_meta(self, game_id: str, log_path: Path) -> Optional[dict]:
        """Parse a game.log to extract basic metadata for the manifest."""
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Skipping unreadable game log %s: %s", log_path, exc)
            return None

        meta: dict = {
            "game_id": game_id,
            "created_at": None,
            "phase": "waiting",
            "round_number": 0,
            "player_count": 0,
            "config": {},
            "winner": None,
            "finished_at": None,
        }
        players: dict = {}
        reveal_on_death: bool = False

        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(rec, dict):
                logger.warning("Skipping malformed game log record")
                continue

            data = rec.get("data")
            if not isinstance(data, dict):
                logger.warning("Skipping game log record with malformed data")
                continue

            if meta["created_at"] is None:
                meta["created_at"] = rec.get("timestamp")

            op = rec.get("operation")

            if op == "game_config":
                flag = data.get("reveal_on_death")
                if isinstance(flag, bool):
                    reveal_on_death = flag
                continue

            if op == "role_init":
                raw_players = data.get("players")
                if not isinstance(raw_players, Mapping):
                    logger.warning("Skipping malformed role_init players")
                    continue
                players = raw_players
                meta["player_count"] = len(players)
                role_counts: dict[str, int] = {}
                roles_complete = bool(players)
                for player in players.values():
                    role_id = player.get("role") if isinstance(player, dict) else None
                    if not isinstance(role_id, str) or not role_id:
                        roles_complete = False
                        continue
                    role_counts[role_id] = role_counts.get(role_id, 0) + 1
                if roles_complete and sum(role_counts.values()) == meta["player_count"]:
                    meta["config"] = {"role_counts": role_counts}

            # Fallback: extract player count from werewolf votes if role_init missing
            if meta["player_count"] == 0 and op == "werewolf_kill":
                seen = set()
                votes = data.get("votes")
                if not isinstance(votes, list):
                    votes = []
                for v in votes:
                    if not isinstance(v, dict):
                        logger.warning("Skipping malformed werewolf vote")
                        continue
                    s = v.get("player_seat")
                    if isinstance(s, int) and not isinstance(s, bool) and s > 0:
                        seen.add(s)
                if seen:
                    meta["player_count"] = len(seen)

            elif op == "phase_change":
                new_phase = data.get("new_phase")
                if not isinstance(new_phase, str):
                    logger.warning("Skipping phase change with malformed phase")
                    continue
                try:
                    GamePhase(new_phase)
                except ValueError:
                    logger.warning("Skipping phase change with unknown phase: %s", new_phase)
                    continue
                meta["phase"] = new_phase
                meta["round_number"] = rec.get("round", 0)

            elif op == "game_over":
                meta["phase"] = "game_over"
                meta["winner"] = data.get("winner")
                meta["finished_at"] = rec.get("timestamp")

        if isinstance(meta.get("config"), dict) and meta["config"].get("role_counts"):
            meta["config"]["reveal_on_death"] = reveal_on_death
        return meta

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        entries = sorted(
            self._entries.values(),
            key=lambda e: e.get("created_at") if isinstance(e.get("created_at"), str) else "",
        )
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
