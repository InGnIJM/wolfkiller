"""Report prompt size and duplicate-fact leakage from captured prompts.

`app.core.prompt_capture` writes one `llm_prompts.jsonl` per game holding the
exact messages a model received. This tool reads those files and prints, per
call kind, the prompt-length distribution plus a section breakdown, and counts
how many records still ship the same fact twice.

Usage (run from backend/):
    python scripts/prompt_size_report.py
    python scripts/prompt_size_report.py --data-dir data --limit 5
    python scripts/prompt_size_report.py --json
    python scripts/prompt_size_report.py --prefix   # prefix-cache reuse ceiling
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import os
import sys

PROMPT_CAPTURE_FILENAME = "llm_prompts.jsonl"

# Section markers. Each section has its own closing marker (single-line
# ``KEY=`` sections end at the newline), so the spans do not depend on the
# order the renderers emit them in — that order is the prefix-cache layout and
# is free to change.
_CONTRACT_SECTIONS = (
    ("ROLE_CONTRACT", "ROLE_CONTRACT=", "\n"),
    ("PROJECTED_CONTEXT", "PROJECTED_CONTEXT=", "\n"),
    ("SCHEMA", "OUTPUT_ACTION_COMMAND_SCHEMA=", "\n"),
    ("TARGET_SELECTION_RULE", "TARGET_SELECTION_RULE=", "\n"),
    ("public_role_rules", "<public_role_rules>", "</public_role_rules>"),
    ("history", "<untrusted_action_history>", "</untrusted_action_history>"),
)
_DAY_SECTIONS = (
    ("authoritative_state", "<authoritative_state>", "</authoritative_state>"),
    ("private_facts", "## 你的私有事实", "</authoritative_state>"),
    ("public_role_rules", "<public_role_rules>", "</public_role_rules>"),
    ("public_history", "<untrusted_public_history>", "</untrusted_public_history>"),
    ("private_history", "<untrusted_private_history>", "</untrusted_private_history>"),
    ("wolf_channel", "<untrusted_wolf_channel>", "</untrusted_wolf_channel>"),
    ("self_history", "<untrusted_self_history>", "</untrusted_self_history>"),
)


def _span(text: str, start: str, end: str) -> int:
    """Total size of every ``start``..``end`` span (end marker included)."""
    total = 0
    begin = text.find(start)
    while begin >= 0:
        finish = text.find(end, begin + len(start))
        stop = finish + len(end) if finish >= 0 else len(text)
        total += stop - begin
        begin = text.find(start, stop)
    return total


def _common_prefix(left: str, right: str) -> int:
    """Length of the shared prefix, by binary search over C-level slice compares."""
    low, high = 0, min(len(left), len(right))
    while low < high:
        middle = (low + high + 1) // 2
        if left[:middle] == right[:middle]:
            low = middle
        else:
            high = middle - 1
    return low


def prefix_reuse(records: list[dict]) -> dict[str, object]:
    """Upper bound of the provider prefix cache over captured prompts.

    For each prompt (in capture order) find the longest prefix it shares with
    any earlier prompt sent to the same model; that span is what an automatic
    prefix cache could serve. In lexicographic order the best earlier match is
    always one of the two neighbours, so a sorted pool keeps this near-linear.
    Tool definitions, minimum cacheable lengths and cache expiry are ignored:
    this is a ceiling for comparing prompt layouts, not a billing estimate.
    """
    pools: dict[str, list[str]] = {}
    by_kind: dict[str, list[int]] = {}
    for record in records:
        data = record.get("data") or {}
        text = "".join(
            f"<{message.get('role')}>{message.get('content') or ''}"
            for message in data.get("messages") or []
        )
        pool = pools.setdefault(str(data.get("model_id") or "unknown"), [])
        index = bisect.bisect_left(pool, text)
        reused = max(
            (_common_prefix(text, pool[i]) for i in (index - 1, index) if 0 <= i < len(pool)),
            default=0,
        )
        pool.insert(index, text)
        bucket = by_kind.setdefault(str(data.get("call_kind") or "unknown"), [0, 0, 0])
        bucket[0] += 1
        bucket[1] += len(text)
        bucket[2] += reused

    def entry(count: int, chars: int, reused: int) -> dict[str, object]:
        return {
            "records": count, "prompt_chars": chars, "reusable_chars": reused,
            "reuse_rate": round(reused / chars, 4) if chars else None,
        }

    totals = [sum(bucket[i] for bucket in by_kind.values()) for i in range(3)]
    return {
        **entry(*totals),
        "kinds": {kind: entry(*bucket) for kind, bucket in sorted(by_kind.items())},
    }


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
    reuse = summary.get("prefix_reuse")
    if isinstance(reuse, dict):
        lines.append(f"\n前缀可复用上限（同模型历史请求的最长公共前缀）: {_rate_text(reuse['reuse_rate'])}")
        for kind, stats in reuse["kinds"].items():
            lines.append(
                f"    {kind:22s} n={stats['records']:5d} "
                f"reuse={_rate_text(stats['reuse_rate'])}"
            )
    return "\n".join(lines)


def _rate_text(value: object) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default="data", help="backend data directory")
    parser.add_argument("--limit", type=int, default=None, help="only the first N capture files")
    parser.add_argument("--json", action="store_true", help="emit the raw summary as JSON")
    parser.add_argument(
        "--prefix", action="store_true",
        help="also report the prefix-cache reuse ceiling per call kind",
    )
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
    if args.prefix:
        summary["prefix_reuse"] = prefix_reuse(records)
    print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json else render_report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
