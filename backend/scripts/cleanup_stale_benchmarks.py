"""Remove the two dead benchmark runs and their orphaned games.

The 2026-10-07 evaluation review left three runs behind:

* ``0bf6d7e3-0256-4b3e-96eb-0673e86df546`` — paused, 87 games, four of them
  ``recovery_blocked`` (``checkpoint_corrupt``). The *run* is a documented data
  snapshot (``docs/notes/2026-09-16-mimo-mixed-arena.md``), so only the four
  broken games are released here.
* ``a5d787e5-b359-4a5b-903f-7b8dd5858ee8`` — interrupted, two interrupted games,
  referenced nowhere.
* ``10c2974e-e680-492f-9c5e-1d6781fb29d6`` — interrupted, one interrupted game,
  recorded as "不可比" in ``docs/notes/2026-10-07-eval-review.md``.

Deleting a run cancels it, deletes its bound games (soft delete — the JSONL logs
under ``data/games/<id>/`` stay on disk) and then removes the run row together
with its items and cached reports. Games are released through the same service
methods the HTTP API uses, so ``benchmark_items`` is unbound before the game row
is marked deleted.

Usage (run from backend/, **with the server stopped** — the script takes the
data-directory lock and refuses while the backend holds it):

    python scripts/cleanup_stale_benchmarks.py
    python scripts/cleanup_stale_benchmarks.py --apply

The default is a dry run: it prints exactly what would be removed. Take a
SQLite ``.backup`` first (``scripts/`` does not do it for you, because copying
the main file is not a safe backup while WAL is active).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.websocket.ws_handler import WSManager  # noqa: E402
from app.core.event_bus import EventBus  # noqa: E402
from app.persistence.process_lock import ProcessLock, ProcessLockError  # noqa: E402
from app.persistence.repository import GameRepository  # noqa: E402
from app.services.benchmark_service import BenchmarkService  # noqa: E402
from app.services.game_service import GameService  # noqa: E402

#: Run kept on purpose: it is the snapshot the mixed-arena note is built on.
KEPT_RUN = "0bf6d7e3-0256-4b3e-96eb-0673e86df546"
#: The broken games inside ``KEPT_RUN`` (``checkpoint_corrupt``, unrecoverable).
BROKEN_GAMES = (
    "2aae0766-420f-5625-9a01-84fe0d559da4",
    "415df31c-30f5-51b7-9cce-9e5dfb4dc7db",
    "55df3572-8885-5402-9aa9-3b5f85c30ec4",
    "67ae8872-1f9e-5c3a-9b27-9d73db7084e3",
)
#: Runs with no documented value left in them.
DEAD_RUNS = (
    "a5d787e5-b359-4a5b-903f-7b8dd5858ee8",
    "10c2974e-e680-492f-9c5e-1d6781fb29d6",
)


async def _run(data_dir: str, apply: bool) -> int:
    repository = GameRepository(data_dir)
    service = GameService(
        ws_manager=WSManager(), event_bus=EventBus(),
        data_dir=data_dir, repository=repository,
    )
    benchmark = BenchmarkService(
        repository,
        owned_game_deleter=service.delete_benchmark_owned_game,
        data_dir=data_dir,
    )
    try:
        print(f"data dir: {data_dir}")
        for run_id in DEAD_RUNS:
            run = repository.get_benchmark_run(run_id)
            if run is None:
                print(f"  run {run_id}: already gone")
                continue
            games = repository.list_benchmark_items(run_id)
            print(
                f"  run {run_id}: status={run['status']} "
                f"games={len(games)} name={run.get('name')!r}"
            )
            if not apply:
                continue
            await benchmark.delete_run(run_id)
            print("    deleted")

        if apply:
            released = await benchmark.delete_owned_games(KEPT_RUN, list(BROKEN_GAMES))
            print(f"  kept run {KEPT_RUN}: released {released}")
        else:
            for game_id in BROKEN_GAMES:
                game = repository.get_game(game_id)
                status = None if game is None else game.get("execution_status")
                print(f"  game {game_id}: status={status}")
        return 0
    finally:
        await benchmark.aclose()
        await service.aclose()
        repository.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.getenv("WOLFKILLER_DATA_DIR", "data"))
    parser.add_argument(
        "--apply", action="store_true",
        help="actually delete; without it the script only reports",
    )
    args = parser.parse_args()

    lock = ProcessLock(args.data_dir)
    try:
        lock.acquire()
    except ProcessLockError:
        print("backend is running (data-directory lock is held); stop it first")
        return 2
    try:
        return asyncio.run(_run(args.data_dir, args.apply))
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
