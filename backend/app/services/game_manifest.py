"""Persistent game index — survives backend restarts.

Stores lightweight game metadata in data/games/index.json so the
frontend game list and detail views continue working after a restart.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GameManifest:
    """Lightweight on-disk registry of all games (running + completed)."""

    def __init__(self, data_dir: str = "data"):
        self._dir = Path(data_dir) / "games"
        self._path = self._dir / "index.json"
        self._entries: dict[str, dict] = {}

    # ── Public API ─────────────────────────────────────────────────

    def load_or_rebuild(self) -> dict[str, dict]:
        """Return {game_id: metadata} for every game on disk.

        Prefer index.json; if missing or corrupt, rebuild by scanning
        game directories and reading their game.log.
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
                elif self._entries[gid].get("config") in (None, {}):
                    meta = self._extract_meta(gid, glog)
                    if meta and meta["config"]:
                        self._entries[gid]["config"] = meta["config"]
                        self._entries[gid]["player_count"] = meta["player_count"]
                        logger.info(f"Recovered role config from disk: {gid}")

        # Persist rebuilt index
        if self._entries:
            self._persist()
        return dict(self._entries)

    def add_game(self, game_id: str, config: dict) -> None:
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
        self._entries[game_id] = entry
        self._persist()

    def update_game(
        self, game_id: str, *,
        phase: Optional[str] = None,
        round_number: Optional[int] = None,
        player_count: Optional[int] = None,
        alive_count: Optional[int] = None,
        winner: Optional[str] = None,
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

            if op == "role_init":
                raw_players = data.get("players", {})
                players = raw_players if isinstance(raw_players, dict) else {}
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
                    if s:
                        seen.add(s)
                if seen:
                    meta["player_count"] = len(seen)

            elif op == "phase_change":
                meta["phase"] = data.get("new_phase", rec.get("phase", ""))
                meta["round_number"] = rec.get("round", 0)

            elif op == "game_over":
                meta["phase"] = "game_over"
                meta["winner"] = data.get("winner")
                meta["finished_at"] = rec.get("timestamp")

        return meta

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        entries = sorted(
            self._entries.values(),
            key=lambda e: e.get("created_at") if isinstance(e.get("created_at"), str) else "",
        )
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
