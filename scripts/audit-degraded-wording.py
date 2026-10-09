#!/usr/bin/env python3
"""Count the engine's old fallback wording anywhere it could have leaked.

Background: every model fallback used to carry ``reasoning="系统异常，本轮未行动"``
(and two timeout twins). With no ``degraded`` flag on the command, four consumers
read that placeholder as the seat's own decision — the god view published it as
``night_thought`` / ``wolf_vote``, ``_log_audience_pairs`` wrote it into the
seat's private thought history, and the next prompt fed it back as the model's
own memory ("决定今晚不使用药水：系统异常，本轮未行动").

The fix stopped new games from producing it. This script exists to separate two
things a plain grep cannot: **archives that still contain the historical text**
(expected, and deliberately not rewritten) from **new games that are still
producing it** (a regression). Run it after a batch:

    python scripts/audit-degraded-wording.py                # every game
    python scripts/audit-degraded-wording.py --since 2026-10-08
    python scripts/audit-degraded-wording.py --game 09982d7c

Read-only: nothing is written, and no game data is touched. If the SQLite file
lives on a Windows mount, copy it first — opening it in place raises
``disk I/O error`` (same reason the benchmark tooling copies it).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

#: The engine's own failure text. A hit means the placeholder reached a surface
#: that a reader (human or model) interprets as a player's reasoning.
#:
#: Deliberately **only** the night placeholder. The two day-vote twins
#: (``系统异常，代投弃权`` / ``系统超时，代投弃权``) look similar but are a
#: different thing and must not be needles here:
#:
#: * they say out loud that the ballot was cast by the system, so they do not
#:   impersonate a decision — and the audience already gets a dedicated
#:   ``technical_abstain`` row ("系统代投") next to them;
#: * they never reach ``conversation.log`` or a prompt:
#:   ``BaseRole._log_vote_technical_abstain`` records only
#:   ``failure_code`` / ``timeout_type`` / ``window_id``, and the vote reasoning
#:   is published as a public ballot, not as the seat's own thought.
#:
#: Keeping them listed made the tool permanently red for the day path, which is
#: exactly how a regression detector stops being read.
NEEDLES = ("系统异常，本轮未行动",)

#: Surfaces to scan, as (label, path pattern). `conversation.log` is the one that
#: decides whether the model saw it: only records with a thought line land there.
SURFACES = (
    ("game.log", "game.log"),
    ("conversation.log", "conversation.log"),
    ("llm_prompts.jsonl", "llm_prompts.jsonl"),
)


def hits_in_file(path: Path) -> int:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if any(needle in line for needle in NEEDLES))


def scan_games(root: Path, since: datetime | None, only: str | None) -> int:
    total = 0
    reported = 0
    for game_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if only is not None and not game_dir.name.startswith(only):
            continue
        if since is not None:
            newest = max(
                (path.stat().st_mtime for path in game_dir.glob("*") if path.is_file()),
                default=0.0,
            )
            if newest and datetime.fromtimestamp(newest, tz=timezone.utc) < since:
                continue
        counts = []
        for label, name in SURFACES:
            path = game_dir / name
            counts.append((label, hits_in_file(path) if path.exists() else 0))
        if not any(count for _, count in counts):
            continue
        reported += 1
        total += sum(count for _, count in counts)
        detail = "  ".join(f"{label}={count}" for label, count in counts if count)
        print(f"  {game_dir.name[:8]}  {detail}")
    print(f"\n日志文件：{reported} 局命中，共 {total} 行")
    return total


def scan_database(path: Path, since: datetime | None, only: str | None) -> int:
    if not path.exists():
        print(f"（跳过 SQLite：{path} 不存在）")
        return 0
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError as error:
        print(f"（跳过 SQLite：{error}；Windows 挂载上请先复制该文件）", file=sys.stderr)
        return 0
    try:
        return _scan_database_rows(connection, since, only)
    except sqlite3.OperationalError as error:
        # On a Windows mount the failure surfaces on the first read, not on
        # connect: report it and keep the log-file result usable.
        print(f"（跳过 SQLite：{error}；Windows 挂载上请先复制该文件）", file=sys.stderr)
        return 0
    finally:
        connection.close()


def _scan_database_rows(
    connection: sqlite3.Connection, since: datetime | None, only: str | None,
) -> int:
    rows = connection.execute(
        "SELECT game_id, event_type, payload_json FROM audience_events "
        "WHERE payload_json LIKE ?", (f"%{NEEDLES[0]}%",),
    ).fetchall()
    created = {
        game_id: created_at
        for game_id, created_at in connection.execute(
            "SELECT game_id, created_at FROM games"
        ).fetchall()
    }
    if only is not None:
        rows = [row for row in rows if row[0].startswith(only)]
    if since is not None:
        kept = []
        for game_id, event_type, payload in rows:
            stamp = created.get(game_id) or ""
            try:
                if stamp and datetime.fromisoformat(stamp) < since:
                    continue
            except ValueError:
                pass
            kept.append((game_id, event_type, payload))
        rows = kept
    by_type: dict[str, int] = {}
    games: set[str] = set()
    for game_id, event_type, _payload in rows:
        by_type[event_type] = by_type.get(event_type, 0) + 1
        games.add(game_id)
    print(f"\n观众流：{len(games)} 局命中，共 {len(rows)} 条")
    for event_type, count in sorted(by_type.items(), key=lambda item: -item[1]):
        print(f"  {event_type}: {count}")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "backend" / "data",
        help="backend/data 目录（默认按仓库位置推断）",
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="只看该日期之后仍在写新数据的对局，例如 2026-10-08（UTC）",
    )
    parser.add_argument("--game", type=str, default=None, help="只看某个 game_id 前缀")
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

    games_root = args.data_dir / "games"
    print(f"数据目录：{args.data_dir}")
    log_total = scan_games(games_root, since, args.game) if games_root.is_dir() else 0
    db_total = scan_database(args.data_dir / "wolfkiller.sqlite3", since, args.game)

    if log_total or db_total:
        print(
            "\n命中说明这些对局里仍有引擎文案出现在日志/观众流。"
            "\n本轮修复之前产生的历史对局属于既成事实（不追溯改写）；"
            "\n只有 --since 之后新建的对局仍命中才算回归。"
        )
        return 1
    print("\n干净：没有发现引擎文案。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
