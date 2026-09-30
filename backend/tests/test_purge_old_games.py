"""Tests for the offline old-game purge script (dry-run by default)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.persistence.process_lock import ProcessLock, ProcessLockError
from app.persistence.repository import GameRepository
from scripts.purge_old_games import (
    DEFAULT_STATUSES,
    main,
    purge_data_dir,
    select_candidates,
)


def _game(
    game_id: str,
    status: str,
    updated_at: str = "2026-01-01T00:00:00+00:00",
    benchmark_run_id=None,
    deleted_at=None,
) -> dict:
    return {
        "game_id": game_id,
        "execution_status": status,
        "updated_at": updated_at,
        "benchmark_run_id": benchmark_run_id,
        "deleted_at": deleted_at,
    }


def test_select_filters_status_benchmark_and_deleted() -> None:
    games = [
        _game("keep-running", "running"),
        _game("keep-bench", "failed", benchmark_run_id="run-1"),
        _game("keep-deleted", "failed", deleted_at="2026-02-01T00:00:00+00:00"),
        _game("del-1", "failed"),
        _game("del-2", "completed"),
    ]
    to_delete, retained = select_candidates(
        games, set(DEFAULT_STATUSES), keep_recent=0
    )
    assert sorted(g["game_id"] for g in to_delete) == ["del-1", "del-2"]
    assert retained == []


def test_select_keep_recent_protects_newest_by_updated_at() -> None:
    games = [
        _game("old-1", "failed", "2026-01-01T00:00:00+00:00"),
        _game("old-2", "failed", "2026-01-02T00:00:00+00:00"),
        _game("new-1", "failed", "2026-01-03T00:00:00+00:00"),
    ]
    to_delete, retained = select_candidates(
        games, set(DEFAULT_STATUSES), keep_recent=1
    )
    assert sorted(g["game_id"] for g in to_delete) == ["old-1", "old-2"]
    assert [g["game_id"] for g in retained] == ["new-1"]


def test_select_older_than_days_and_unparseable_date() -> None:
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    games = [
        _game("ancient", "failed", "2026-01-01T00:00:00"),
        _game(
            "fresh", "failed",
            (now - timedelta(days=1)).isoformat(),
        ),
        _game("no-date", "failed", "not-a-date"),
    ]
    to_delete, _ = select_candidates(
        games, set(DEFAULT_STATUSES), keep_recent=0,
        older_than_days=30.0, now=now,
    )
    assert sorted(g["game_id"] for g in to_delete) == ["ancient", "no-date"]
    to_delete_default_now, _ = select_candidates(
        games, set(DEFAULT_STATUSES), keep_recent=0, older_than_days=-1.0,
    )
    assert sorted(g["game_id"] for g in to_delete_default_now) == [
        "ancient", "fresh", "no-date",
    ]


def _seed_repo(tmp_path) -> GameRepository:
    repository = GameRepository(tmp_path)
    for game_id, status in [
        ("old-failed", "failed"),
        ("old-completed", "completed"),
        ("fresh-failed", "failed"),
    ]:
        repository.create_game(
            game_id=game_id, name=game_id, config={},
            execution_status=status, source="native", model_snapshot=[],
        )
    repository.create_game(
        game_id="bench-failed", name="bench", config={},
        execution_status="failed", source="native", model_snapshot=[],
        benchmark_run_id="run-1",
    )
    _seed_archive(
        tmp_path, "old-failed", "old-completed", "fresh-failed", "bench-failed",
    )
    return repository


def _seed_archive(tmp_path, *game_ids: str) -> None:
    """Create the legacy manifest layout the lobby rebuilds from."""
    import json

    entries = []
    for game_id in game_ids:
        game_dir = os.path.join(str(tmp_path), "games", game_id)
        os.makedirs(game_dir, exist_ok=True)
        entries.append({
            "game_id": game_id, "created_at": "2026-01-01T00:00:00+00:00",
            "phase": "game_over", "round_number": 1, "player_count": 1,
            "config": {}, "winner": "good", "finished_at": None,
        })
    index = os.path.join(str(tmp_path), "games", "index.json")
    with open(index, "w", encoding="utf-8") as handle:
        json.dump(entries, handle)


def _manifest_ids(tmp_path) -> set[str]:
    import json

    index = os.path.join(str(tmp_path), "games", "index.json")
    if not os.path.isfile(index):
        return set()
    with open(index, "r", encoding="utf-8") as handle:
        return {entry["game_id"] for entry in json.load(handle)}


def _seed_wal_only_repo(tmp_path) -> None:
    """Commit games in a child that exits without closing any connection.

    Closing the last connection checkpoints the WAL back into the main file, so
    the only way to reproduce "committed rows live in ``-wal``" deterministically
    is to let the process die: ``os._exit`` skips every cleanup hook.
    """
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = (
        "import os, sys\n"
        f"sys.path.insert(0, {backend_root!r})\n"
        "from app.persistence.repository import GameRepository\n"
        f"repo = GameRepository({str(tmp_path)!r})\n"
        "for game_id, status in (('wal-failed', 'failed'), ('wal-completed', 'completed')):\n"
        "    repo.create_game(game_id=game_id, name=game_id, config={},\n"
        "        execution_status=status, source='native', model_snapshot=[])\n"
        "os._exit(0)\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = backend_root
    subprocess.run(
        [sys.executable, "-c", script], check=True, env=environment, timeout=60,
    )


def _has_dir(tmp_path, game_id: str) -> bool:
    return os.path.isdir(os.path.join(str(tmp_path), "games", game_id))


def test_purge_dry_run_deletes_nothing(tmp_path) -> None:
    repository = _seed_repo(tmp_path)
    repository.close()
    summary = purge_data_dir(
        str(tmp_path), statuses=set(DEFAULT_STATUSES), keep_recent=1,
        dry_run=True,
    )
    assert summary["dry_run"] is True
    assert len(summary["to_delete"]) == 2  # bench skipped, newest kept
    check = GameRepository(tmp_path)
    try:
        assert check.get_game("old-failed")["deleted_at"] is None
    finally:
        check.close()
    assert _has_dir(tmp_path, "old-failed")
    assert "old-failed" in _manifest_ids(tmp_path)


def test_purge_apply_removes_archive_dir_and_manifest_entry(tmp_path) -> None:
    repository = _seed_repo(tmp_path)
    repository.close()
    purge_data_dir(
        str(tmp_path), statuses=set(DEFAULT_STATUSES), keep_recent=1,
        dry_run=False,
    )
    # lobby source of truth: no on-disk archive, no manifest entry
    assert not _has_dir(tmp_path, "old-failed")
    assert not _has_dir(tmp_path, "old-completed")
    remaining = _manifest_ids(tmp_path)
    assert "old-failed" not in remaining
    assert "old-completed" not in remaining
    # retained + benchmark archives survive
    assert _has_dir(tmp_path, "fresh-failed")
    assert _has_dir(tmp_path, "bench-failed")
    assert {"fresh-failed", "bench-failed"} <= remaining


def test_purge_cleans_leftovers_from_previous_soft_delete(tmp_path) -> None:
    """A game already soft-deleted offline must still lose dir + manifest."""
    repository = _seed_repo(tmp_path)
    repository.mark_game_deleted("old-failed")
    repository.close()
    assert _has_dir(tmp_path, "old-failed")
    summary = purge_data_dir(
        str(tmp_path), statuses={"completed"}, keep_recent=0,
        dry_run=False, backup=False, vacuum=False,
    )
    # not re-selected (already deleted) but leftover runtime is swept
    assert summary["deleted"] == ["old-completed"]
    assert not _has_dir(tmp_path, "old-failed")
    assert "old-failed" not in _manifest_ids(tmp_path)


def test_purge_sweeps_soft_deleted_game_without_directory(tmp_path) -> None:
    repository = _seed_repo(tmp_path)
    repository.mark_game_deleted("old-completed")
    shutil.rmtree(os.path.join(str(tmp_path), "games", "old-completed"))
    repository.close()
    summary = purge_data_dir(
        str(tmp_path), statuses={"failed"}, keep_recent=0,
        dry_run=False, backup=False, vacuum=False,
    )
    assert sorted(summary["deleted"]) == ["fresh-failed", "old-failed"]
    assert "old-completed" in summary["swept"]
    assert "old-completed" not in _manifest_ids(tmp_path)


def test_purge_apply_with_no_selection_sweeps_nothing(tmp_path) -> None:
    repository = _seed_repo(tmp_path)
    repository.close()
    summary = purge_data_dir(
        str(tmp_path), statuses={"running"}, keep_recent=0,
        dry_run=False, backup=False, vacuum=False,
    )
    assert summary["deleted"] == []
    assert summary["swept"] == []


def test_purge_apply_deletes_and_backs_up(tmp_path) -> None:
    repository = _seed_repo(tmp_path)
    repository.close()
    summary = purge_data_dir(
        str(tmp_path), statuses=set(DEFAULT_STATUSES), keep_recent=1,
        dry_run=False,
    )
    assert sorted(summary["deleted"]) == ["old-completed", "old-failed"]
    assert summary["backup_path"] is not None
    # The backup is a usable database holding the pre-purge state — not a byte
    # copy of a main file whose commits were still sitting in the WAL.
    with sqlite3.connect(summary["backup_path"]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        backed_up = {row[0] for row in backup.execute("SELECT game_id FROM games")}
    assert backed_up == {"old-failed", "old-completed", "fresh-failed", "bench-failed"}
    # Both sizes describe the same thing now, so deleting games can only shrink it.
    assert summary["size_after"] <= summary["size_before"]
    check = GameRepository(tmp_path)
    try:
        assert check.get_game("old-failed", include_deleted=True)["deleted_at"] is not None
        # benchmark-owned game is never touched
        assert check.get_game("bench-failed")["deleted_at"] is None
    finally:
        check.close()


def test_purge_takes_the_lock_before_touching_the_database(tmp_path) -> None:
    """A directory owned by a running server must not even be initialized.

    The repository constructor creates the schema, so building it before taking
    the lock writes a WAL into a data directory another process still owns.
    """
    lock = ProcessLock(tmp_path).acquire()
    try:
        with pytest.raises(ProcessLockError):
            purge_data_dir(str(tmp_path), dry_run=True)
    finally:
        lock.release()

    assert not os.path.exists(os.path.join(str(tmp_path), "wolfkiller.sqlite3"))


def test_purge_backup_contains_rows_that_only_live_in_the_wal(tmp_path) -> None:
    """The backup must be a usable database, WAL included.

    A byte copy of the main file alone loses every commit still sitting in
    ``-wal``. The purge then permanently deletes those games while the "safety
    backup" cannot even be opened — the copy has no tables at all.
    """
    _seed_wal_only_repo(tmp_path)
    wal_path = os.path.join(str(tmp_path), "wolfkiller.sqlite3-wal")
    assert os.path.getsize(wal_path) > 0

    summary = purge_data_dir(
        str(tmp_path), statuses=set(DEFAULT_STATUSES), keep_recent=0,
        dry_run=False, backup=True, vacuum=False,
    )

    assert summary["backup_path"] is not None
    with sqlite3.connect(summary["backup_path"]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        ids = {row[0] for row in backup.execute("SELECT game_id FROM games")}
    assert {"wal-failed", "wal-completed"} <= ids


def test_purge_apply_without_backup_or_vacuum(tmp_path) -> None:
    repository = _seed_repo(tmp_path)
    repository.close()
    summary = purge_data_dir(
        str(tmp_path), statuses={"failed"}, keep_recent=0,
        dry_run=False, backup=False, vacuum=False,
    )
    assert summary["backup_path"] is None
    assert sorted(summary["deleted"]) == ["fresh-failed", "old-failed"]
    assert not _has_dir(tmp_path, "old-failed")
    assert _has_dir(tmp_path, "old-completed")


def test_purge_refuses_when_backend_holds_lock(tmp_path) -> None:
    repository = _seed_repo(tmp_path)
    lock = ProcessLock(tmp_path).acquire()
    try:
        with pytest.raises(Exception, match="[Ll]ock"):
            purge_data_dir(str(tmp_path), dry_run=False)
    finally:
        lock.release()
        repository.close()


def test_main_dry_run_and_apply(tmp_path, capsys) -> None:
    repository = _seed_repo(tmp_path)
    repository.close()
    assert main(["--data-dir", str(tmp_path), "--keep-recent", "1"]) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert (
        main(["--data-dir", str(tmp_path), "--keep-recent", "1", "--apply"]) == 0
    )
    check = GameRepository(tmp_path)
    try:
        assert (
            check.get_game("old-failed", include_deleted=True)["deleted_at"]
            is not None
        )
    finally:
        check.close()


def test_main_locked_returns_distinct_exit_code(tmp_path) -> None:
    repository = _seed_repo(tmp_path)
    lock = ProcessLock(tmp_path).acquire()
    try:
        assert main(["--data-dir", str(tmp_path), "--apply"]) == 2
    finally:
        lock.release()
        repository.close()


def test_main_accepts_comma_separated_statuses(tmp_path, capsys) -> None:
    repository = _seed_repo(tmp_path)
    repository.close()
    assert (
        main(["--data-dir", str(tmp_path), "--status", "failed, completed,"]) == 0
    )
    assert "DRY-RUN" in capsys.readouterr().out
