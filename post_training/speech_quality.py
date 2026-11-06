"""Speech-quality metrics for round-2 comparison: leak / template / length.

Pure-CPU, works on any JSONL that carries generated speech text:

    # student predictions from eval_student.py generation rows
    python speech_quality.py --rows data/eval_report.rows.jsonl \
        --field prediction --out data/speech_quality.json

    # teacher gold side (same metric as a sanity baseline: should be ~0%)
    python speech_quality.py --rows data/eval_final.jsonl \
        --field assistant --out data/speech_quality_gold.json

Why these metrics: round 1's failure signature was meta-speak leaking into
public speeches ("我底牌是深水狼"), which char-F1 cannot see.  The patterns
below are the exact phrases observed in the 11-03 benchmark plus the
equivalent wolf-side variants.  Scoring the *gold* texts with the same
patterns guards against a metric that fires on legitimate play (a seer
publicly claiming 预言家 is normal; "底牌" meta-language is not).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from typing import Iterable

# (name, pattern, rationale).  All are meta-language / private-fact markers;
# none should appear in a well-formed public speech regardless of role.
LEAK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("meta_hand", r"底牌", "meta-speak 'my card is X' — the 11-03 signature"),
    ("wolf_self", r"深水狼|底牌是[^。，,]{0,8}狼|我们?狼队|我.{0,3}是狼人",
     "wolf identity stated in public"),
    ("night_plan", r"(今夜|今晚|夜间|夜里|昨晚).{0,12}(刀|杀|下刀|计划|安排)",
     "wolf night-kill plan echoed into day speech"),
    ("teammate", r"(狼)?队友", "private team info leaked"),
    ("coaching", r"(策略|战术)(上|方面)?(来说|而言|看)|内部(思考|计划|笔记)",
     "strategy-memo register instead of public speech"),
)

_OPEN_CHARS = 8


def iter_records(path: str) -> Iterable[dict]:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def extract_text(record: dict, field: str) -> str | None:
    """Pull the speech text out of a row, by field convention.

    ``prediction`` — eval_student.py generation rows (call_kind speech only).
    ``assistant``   — SFT JSONL records (final assistant message content).
    """
    if field == "prediction":
        if record.get("call_kind") != "speech":
            return None
        return record.get("prediction")
    if field == "assistant":
        meta = record.get("meta") or {}
        if meta.get("call_kind") != "speech":
            return None
        messages = record.get("messages") or []
        if messages and messages[-1].get("role") == "assistant":
            return str(messages[-1].get("content") or "")
        return None
    return record.get(field)


def score_texts(texts: Iterable[str], open_chars: int = _OPEN_CHARS) -> dict:
    """Aggregate leak / template / length metrics over speech texts."""
    total = 0
    hits = Counter()
    leaked = 0
    openings = Counter()
    lengths = []
    for text in texts:
        if not text:
            continue
        total += 1
        lengths.append(len(text))
        openings[text.strip()[:open_chars]] += 1
        flagged = False
        for name, pattern, _ in LEAK_PATTERNS:
            if re.search(pattern, text):
                hits[name] += 1
                flagged = True
        leaked += flagged
    return {
        "speeches": total,
        "leaked": leaked,
        "leak_rate": round(leaked / total, 4) if total else 0.0,
        "pattern_hits": {name: hits.get(name, 0) for name, _, _ in LEAK_PATTERNS},
        "top_openings": openings.most_common(10),
        "len_p50": statistics.median(lengths) if lengths else 0,
        "len_max": max(lengths) if lengths else 0,
    }


def render_markdown(result: dict, source: str) -> str:
    lines = [
        f"# Speech quality — {source}",
        "",
        f"- speeches scored: {result['speeches']}",
        f"- leak rate: {result['leak_rate']:.1%} ({result['leaked']})",
        f"- length p50 / max: {result['len_p50']} / {result['len_max']} chars",
        "",
        "| pattern | hits | meaning |",
        "| --- | --- | --- |",
    ]
    for name, _, why in LEAK_PATTERNS:
        lines.append(f"| {name} | {result['pattern_hits'][name]} | {why} |")
    lines += ["", "## top openings", "", "| prefix | n |", "| --- | --- |"]
    for prefix, n in result["top_openings"]:
        lines.append(f"| {prefix} | {n} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", required=True, help="JSONL with generated/gold speeches")
    parser.add_argument("--field", default="prediction",
                        choices=["prediction", "assistant"])
    parser.add_argument("--out", default=None, help="stats JSON path")
    parser.add_argument("--md", default=None, help="markdown summary path")
    args = parser.parse_args()

    texts = [t for t in (extract_text(r, args.field) for r in iter_records(args.rows)) if t]
    result = score_texts(texts)
    result["source"] = f"{args.rows}:{args.field}"

    out = args.out or args.rows.rsplit(".", 1)[0] + ".speech_quality.json"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    md = args.md or out.rsplit(".", 1)[0] + ".md"
    with open(md, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(result, args.rows))
    print(render_markdown(result, args.rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
