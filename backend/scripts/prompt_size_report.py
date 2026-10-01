"""Report prompt size and duplicate-fact leakage from captured prompts.

`app.core.prompt_capture` writes one `llm_prompts.jsonl` per game holding the
exact messages a model received. This tool reads those files and prints, per
call kind, the prompt-length distribution plus a section breakdown, and counts
how many records still ship the same fact twice.

Usage (run from backend/):
    python scripts/prompt_size_report.py
    python scripts/prompt_size_report.py --data-dir data --limit 5
    python scripts/prompt_size_report.py --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

PROMPT_CAPTURE_FILENAME = "llm_prompts.jsonl"

# Section markers, in the order the renderers emit them.
_CONTRACT_SECTIONS = (
    ("ROLE_CONTRACT", "ROLE_CONTRACT=", "\nPROJECTED_CONTEXT="),
    ("PROJECTED_CONTEXT", "PROJECTED_CONTEXT=", "\nOUTPUT_ACTION_COMMAND_SCHEMA="),
    ("SCHEMA", "OUTPUT_ACTION_COMMAND_SCHEMA=", "\nTARGET_SELECTION_RULE="),
    ("TARGET_SELECTION_RULE", "TARGET_SELECTION_RULE=", "\n<public_role_rules>"),
    ("public_role_rules", "<public_role_rules>", "</public_role_rules>"),
    ("history", "<untrusted_action_history>", None),
)
_DAY_SECTIONS = (
    ("authoritative_state", "<authoritative_state>", "</authoritative_state>"),
    ("private_facts", "## 你的私有事实", "</authoritative_state>"),
    ("public_role_rules", "<public_role_rules>", "</public_role_rules>"),
    ("history", "## 历史与对话", None),
)


def _span(text: str, start: str, end: str | None) -> int:
    begin = text.find(start)
    if begin < 0:
        return 0
    if end is None:
        return len(text) - begin
    finish = text.find(end, begin + len(start))
    return (finish - begin) if finish >= 0 else (len(text) - begin)


def classify(record: dict) -> dict[str, object]:
    """Split one capture record into path kind, section sizes and duplicate flags."""
    data = record.get("data") or {}
    messages = data.get("messages") or []
    system_chars = sum(len(m.get("content") or "") for m in messages if m.get("role") == "system")
    user = "".join(m.get("content") or "" for m in messages if m.get("role") != "system")
    projected = user[
        user.find("PROJECTED_CONTEXT="):user.find("\nOUTPUT_ACTION_COMMAND_SCHEMA=")
    ] if "PROJECTED_CONTEXT=" in user else ""

    if "PROJECTED_CONTEXT=" in user:
        path, markers = "contract", _CONTRACT_SECTIONS
    elif "<authoritative_state>" in user:
        path, markers = "day", _DAY_SECTIONS
    else:
        path, markers = "other", ()

    sections = {name: _span(user, start, end) for name, start, end in markers}
    return {
        "call_kind": str(data.get("call_kind") or "unknown"),
        "path": path,
        "system_chars": system_chars,
        "user_chars": len(user),
        "prompt_chars": system_chars + len(user),
        "sections": sections,
        # Facts that a dedicated block already renders must not be serialised a
        # second time. Any non-zero count here is a regression.
        "dup_role_rules_dump": "- public_role_rules：" in user,
        "dup_speeches_dump": "- speeches：" in user,
        "dup_role_rules_projected": '"public_role_rules"' in projected,
        "dup_speeches_projected": '"speeches"' in projected,
    }


def load_records(data_dir: str, limit: int | None = None) -> list[dict]:
    """Read every capture record under `<data_dir>/games/*/llm_prompts.jsonl`."""
    patterns = sorted(glob.glob(os.path.join(data_dir, "games", "*", PROMPT_CAPTURE_FILENAME)))
    if limit is not None:
        patterns = patterns[:limit]
    records: list[dict] = []
    for path in patterns:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _percentile(values: list[int], pct: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = min(max(-(-pct * len(ordered) // 100), 1), len(ordered))
    return ordered[rank - 1]


def summarize(records: list[dict]) -> dict[str, object]:
    """Aggregate per call kind: length distribution, sections, duplicate counts."""
    classified = [classify(record) for record in records]
    by_kind: dict[str, list[dict]] = {}
    for item in classified:
        by_kind.setdefault(str(item["call_kind"]), []).append(item)

    kinds: dict[str, object] = {}
    for kind, items in sorted(by_kind.items()):
        lengths = [int(item["prompt_chars"]) for item in items]
        section_names = sorted({name for item in items for name in item["sections"]})
        kinds[kind] = {
            "records": len(items),
            "paths": sorted({str(item["path"]) for item in items}),
            "prompt_chars": {
                "mean": round(sum(lengths) / len(lengths)),
                "p50": _percentile(lengths, 50),
                "p90": _percentile(lengths, 90),
                "max": max(lengths),
            },
            "sections": {
                name: round(
                    sum(int(item["sections"].get(name, 0)) for item in items) / len(items)
                )
                for name in section_names
            },
        }

    duplicates = {
        "role_rules_dumped_again": sum(1 for i in classified if i["dup_role_rules_dump"]),
        "speeches_dumped_again": sum(1 for i in classified if i["dup_speeches_dump"]),
        "role_rules_in_projected": sum(1 for i in classified if i["dup_role_rules_projected"]),
        "speeches_in_projected": sum(1 for i in classified if i["dup_speeches_projected"]),
    }
    return {"files_scanned": len(records), "kinds": kinds, "duplicates": duplicates}


def render_report(summary: dict[str, object]) -> str:
    lines = [f"捕获记录数: {summary['files_scanned']}"]
    for kind, stats in (summary["kinds"] or {}).items():  # type: ignore[union-attr]
        chars = stats["prompt_chars"]
        lines.append(
            f"\n[{kind}] n={stats['records']} 路径={','.join(stats['paths'])}  "
            f"prompt_chars mean={chars['mean']} p50={chars['p50']} "
            f"p90={chars['p90']} max={chars['max']}"
        )
        for name, size in sorted(stats["sections"].items(), key=lambda kv: -kv[1]):
            if size:
                lines.append(f"    {name:22s} {size:7d}")
    duplicates = summary["duplicates"]
    lines.append("\n重复事实检测（应为 0）:")
    for name, count in duplicates.items():  # type: ignore[union-attr]
        lines.append(f"    {name:26s} {count}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default="data", help="backend data directory")
    parser.add_argument("--limit", type=int, default=None, help="only the first N capture files")
    parser.add_argument("--json", action="store_true", help="emit the raw summary as JSON")
    args = parser.parse_args(argv)

    records = load_records(args.data_dir, args.limit)
    if not records:
        print(
            f"未找到 {PROMPT_CAPTURE_FILENAME}（data-dir={args.data_dir}）。"
            "捕获默认开启，跑一局新对局后再试。",
            file=sys.stderr,
        )
        return 2
    summary = summarize(records)
    print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json else render_report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
