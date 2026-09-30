"""Purge old terminal games to reclaim space and quiet startup.

Old terminal games only bloat ``wolfkiller.sqlite3``: failed/cancelled/completed
games are never resumed, and a resume no longer depends on the role registry
matching the one the game was frozen under (see
``app/persistence/recovery_compat.py``), so a registry change is not a reason to
delete anything. This offline script lists (default) or deletes the selected
games through the repository's own soft-delete path (payload rows are purged,
benchmark-owned games are always skipped).

Usage (run from backend/):
    python scripts/purge_old_games.py --data-dir data
    python scripts/purge_old_games.py --data-dir data --apply --keep-recent 10

Stop the backend first: the script takes the data-directory lock and
refuses to run while the server holds it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.persistence.process_lock import ProcessLock, ProcessLockError  # noqa: E402
from app.persistence.repository import GameRepository  # noqa: E402

DEFAULT_STATUSES = ("completed", "failed", "cancelled", "recovery_blocked")


def _parse_updated_at(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def select_candidates(
    games: Iterable[Mapping[str, object]],
    statuses: Iterable[str],
    keep_recent: int = 0,
    older_than_days: float | None = None,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split games into (to_delete, retained), oldest first in to_delete."""
    wanted = set(statuses)
    eligible = [
        dict(game) for game in games
        if game.get("deleted_at") is None
        and game.get("benchmark_run_id") is None
        and str(game.get("execution_status")) in wanted
    ]
    if older_than_days is not None:
        reference = now if now is not None else datetime.now(timezone.utc)
        cutoff = reference - timedelta(days=older_than_days)
        eligible = [
            game for game in eligible
            if _parse_updated_at(game.get("updated_at")) is None
            or _parse_updated_at(game.get("updated_at")) < cutoff
        ]
    ordered = sorted(
        eligible,
        key=lambda game: (
            str(game.get("updated_at") or ""), str(game.get("game_id")),
        ),
    )
    keep = max(0, keep_recent)
    to_delete = ordered[: max(0, len(ordered) - keep)]
    retained = ordered[len(to_delete):]
    return to_delete, retained


def purge_data_dir(
    data_dir: str | os.PathLike[str],
    statuses: Iterable[str] = DEFAULT_STATUSES,
    keep_recent: int = 10,
    older_than_days: float | None = None,
    dry_run: bool = True,
    backup: bool = True,
    vacuum: bool = True,
) -> dict[str, object]:
    """Delete selected games; dry-run only reports. Returns a summary dict."""
    # Take the data-directory lock before the repository exists: constructing it
    # initializes the schema (and therefore writes a WAL) on a directory another
    # process may still own.
    lock = ProcessLock(data_dir).acquire()
    try:
        repository = GameRepository(data_dir)
    except BaseException:
        lock.release()
        raise
    try:
        games = repository.list_games()
        to_delete, retained = select_candidates(
            games, statuses, keep_recent, older_than_days,
        )
        db_path = os.fspath(repository.database_path)
        size_before = _logical_size(db_path)
        backup_path: str | None = None
        if not dry_run and backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            backup_dir = os.path.join(os.fspath(data_dir), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            backup_path = os.path.join(backup_dir, f"wolfkiller-{stamp}.sqlite3")
            _backup_database(db_path, backup_path)
        deleted: list[str] = []
        swept: list[str] = []
        if not dry_run:
            for target in to_delete:
                repository.mark_game_deleted(str(target["game_id"]))
                deleted.append(str(target["game_id"]))
            swept = _sweep_deleted_archives(data_dir, repository)
        if not dry_run and vacuum:
            with sqlite3.connect(db_path, timeout=30.0) as connection:
                connection.execute("VACUUM")
        size_after = _logical_size(db_path)
        return {
            "data_dir": os.fspath(data_dir),
            "dry_run": dry_run,
            "to_delete": [str(game["game_id"]) for game in to_delete],
            "retained": [str(game["game_id"]) for game in retained],
            "deleted": deleted,
            "swept": swept,
            "backup_path": backup_path,
            "size_before": size_before,
            "size_after": size_after,
        }
    finally:
        lock.release()
        repository.close()


def _logical_size(db_path: str) -> int:
    """Size of the database's own pages, independent of the WAL file.

    ``os.path.getsize`` describes whichever part of the database happens to have
    been checkpointed, so the same database reports two different sizes before
    and after an unrelated open.
    """
    with sqlite3.connect(db_path, timeout=30.0) as connection:
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    return int(page_count) * int(page_size)


def _backup_database(db_path: str, backup_path: str) -> None:
    """Write a consistent single-file copy, WAL included.

    ``shutil.copy2`` copies only the main file, so every commit still in
    ``-wal`` is missing — the "backup" can be an empty database while the purge
    deletes the games it was supposed to protect. SQLite's own online backup
    API reads through the WAL and needs no checkpoint.
    """
    source = sqlite3.connect(db_path, timeout=30.0)
    try:
        target = sqlite3.connect(backup_path, timeout=30.0)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _sweep_deleted_archives(
    data_dir: str | os.PathLike[str], repository: GameRepository,
) -> list[str]:
    """Drop on-disk archives + manifest rows for every soft-deleted game.

    The lobby is rebuilt from ``data/games/<id>`` and ``games/index.json``, so
    soft-deleting the repository row alone leaves purged games visible. This
    mirrors ``GameService.delete_game`` for offline games and is idempotent,
    which also cleans up leftovers from earlier soft-deletes.
    """
    from app.services.game_manifest import GameManifest

    soft_deleted = [
        str(game["game_id"])
        for game in repository.list_games(include_deleted=True)
        if game.get("deleted_at") is not None and not game.get("benchmark_run_id")
    ]
    if not soft_deleted:
        return []
    games_root = os.path.join(os.fspath(data_dir), "games")
    for game_id in soft_deleted:
        target = os.path.join(games_root, game_id)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
    manifest = GameManifest(os.fspath(data_dir))
    manifest.load_or_rebuild()
    for game_id in soft_deleted:
        manifest.remove_game(game_id)
    return soft_deleted


def _parse_statuses(values: list[str] | None) -> list[str]:
    if not values:
        return list(DEFAULT_STATUSES)
    selected: list[str] = []
    for value in values:
        selected.extend(
            part.strip() for part in str(value).split(",") if part.strip()
        )
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", help="backend data directory")
    parser.add_argument(
        "--status", action="append", default=None,
        help="execution status to purge (repeatable, comma-separated)",
    )
    parser.add_argument(
        "--keep-recent", type=int, default=10,
        help="newest matching games to keep (default 10)",
    )
    parser.add_argument(
        "--older-than-days", type=float, default=None,
        help="only purge games not updated for this many days",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="delete for real (default is dry-run)",
    )
    parser.add_argument(
        "--no-backup", action="store_true", help="skip sqlite backup on --apply",
    )
    parser.add_argument(
        "--no-vacuum", action="store_true", help="skip VACUUM on --apply",
    )
    args = parser.parse_args(argv)
    try:
        summary = purge_data_dir(
            args.data_dir,
            statuses=_parse_statuses(args.status),
            keep_recent=args.keep_recent,
            older_than_days=args.older_than_days,
            dry_run=not args.apply,
            backup=not args.no_backup,
            vacuum=not args.no_vacuum,
        )
    except ProcessLockError as error:
        print(f"refusing to purge: {error}", file=sys.stderr)
        return 2
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"{mode}: {len(summary['to_delete'])} game(s) selected, "
        f"{len(summary['retained'])} retained "
        f"({summary['size_before']} -> {summary['size_after']} bytes)"
    )
    for game_id in summary["to_delete"]:
        print(f"  {mode} {game_id}")
    print(f"swept archived dirs/manifest rows: {len(summary['swept'])}")
    if summary["backup_path"]:
        print(f"backup: {summary['backup_path']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
