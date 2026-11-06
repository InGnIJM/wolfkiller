"""Build the round-2 targeted dataset: speech-heavy continued-SFT subset.

Runs inside WSL2 (tokenizer reads the local model dir):

    ~/sft/bin/python prepare_round2.py \
        --in  /mnt/e/code/WolfKiller/post_training/data/train_final.jsonl \
        --out /mnt/e/code/WolfKiller/post_training/data/train_round2.jsonl \
        --model /mnt/e/models/Qwen3-4B-Instruct-2507

Differences from ``subset_teacher.py`` (round 1):

* Round 2 targets the weakest surface — speech — so ``--kinds`` defaults to
  ``speech`` and the pool is multi-teacher (3 gemini flavors); deepseek
  outputs are excluded by default because round-1 eval showed their style is
  the furthest outlier (NLL 1.896 vs 1.20 same-teacher).
* Three hygiene filters the round-1 pipeline lacked: normalized-text dedupe,
  a cap on identical opening prefixes (the "去套话" counter — concentrated
  openings are why the student opens every speech with the same template),
  and a degenerate-completion floor.
* The most important one: **leak-pattern drop**.  Round-1 audit showed 45%
  of teacher speeches carry meta-speak ("底牌" register, wolf self-outing,
  night-plan echoes) — the student's "我底牌是深水狼" was *learned*, not
  invented.  Kept speeches must be clean positives; ``--keep-leaks`` opts
  out if a round-3 experiment wants them back.
* Everything else (exact token measurement with the real chat template,
  ``repair_context`` exclusion) matches round 1 so the subsets stay
  comparable.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from typing import Iterable

from speech_quality import LEAK_PATTERNS

DEFAULT_EXCLUDED_TEACHERS = ("deepseek-v4.1-flash", "deepseek-v4-flash-0731")


def iter_records(path: str) -> Iterable[dict]:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def rendered_len(tokenizer, messages: list[dict]) -> int:
    """Token count of the full chat-template rendering (prompt + completion)."""
    ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
    return len(ids)


def completion_text(record: dict) -> str:
    """Final assistant turn as plain text (empty string if absent)."""
    messages = record.get("messages") or []
    if not messages or messages[-1].get("role") != "assistant":
        return ""
    return str(messages[-1].get("content") or "")


_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Dedupe/normalization key: collapse all whitespace."""
    return _WS_RE.sub("", text)


def opening_prefix(text: str, chars: int) -> str:
    """First ``chars`` normalized chars — the template-opening cluster key."""
    return normalize(text)[:chars]


def leak_hits(text: str) -> list[str]:
    """Names of leak patterns (speech_quality.LEAK_PATTERNS) present in text."""
    return [name for name, pattern, _ in LEAK_PATTERNS if re.search(pattern, text)]


def build_subset(
    records: Iterable[dict],
    tokenizer,
    kinds: set[str],
    excluded_teachers: set[str],
    max_tokens: int,
    min_chars: int,
    prefix_chars: int,
    prefix_cap: int,
    drop_leaks: bool,
) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    seen_texts: set[str] = set()
    prefixes: Counter[str] = Counter()
    stats = {
        "seen": 0, "wrong_kind": 0, "excluded_teacher": 0, "repair_context": 0,
        "empty_completion": 0, "too_short": 0, "leaked": 0, "duplicate": 0,
        "prefix_capped": 0, "too_long": 0, "kept": 0,
    }
    for rec in records:
        stats["seen"] += 1
        meta = rec.get("meta") or {}
        if meta.get("call_kind") not in kinds:
            stats["wrong_kind"] += 1
            continue
        if meta.get("teacher") in excluded_teachers:
            stats["excluded_teacher"] += 1
            continue
        if meta.get("repair_context"):
            stats["repair_context"] += 1
            continue
        text = completion_text(rec)
        if not text:
            stats["empty_completion"] += 1
            continue
        if len(text) < min_chars:
            stats["too_short"] += 1
            continue
        if drop_leaks and leak_hits(text):
            stats["leaked"] += 1
            continue
        key = normalize(text)
        if key in seen_texts:
            stats["duplicate"] += 1
            continue
        prefix = opening_prefix(text, prefix_chars)
        if prefixes[prefix] >= prefix_cap:
            stats["prefix_capped"] += 1
            continue
        if rendered_len(tokenizer, rec["messages"]) > max_tokens:
            stats["too_long"] += 1
            continue
        seen_texts.add(key)
        prefixes[prefix] += 1
        kept.append(rec)
        stats["kept"] += 1
    stats["top_prefixes"] = prefixes.most_common(10)
    stats["teacher_dist"] = dict(Counter(
        (r.get("meta") or {}).get("teacher") for r in kept))
    stats["kind_dist"] = dict(Counter(
        (r.get("meta") or {}).get("call_kind") for r in kept))
    return kept, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", dest="dst", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--kinds", nargs="+", default=["speech"])
    parser.add_argument("--exclude-teacher", nargs="*", default=list(DEFAULT_EXCLUDED_TEACHERS))
    parser.add_argument("--min-chars", type=int, default=30)
    parser.add_argument("--prefix-chars", type=int, default=12)
    parser.add_argument("--prefix-cap", type=int, default=120)
    parser.add_argument("--keep-leaks", dest="drop_leaks", action="store_false",
                        help="keep speeches matching LEAK_PATTERNS (default: drop)")
    parser.set_defaults(drop_leaks=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--report", default=None,
                        help="optional path for the stats JSON (default: <out>.report.json)")
    args = parser.parse_args()

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    kept, stats = build_subset(
        iter_records(args.src), tokenizer, set(args.kinds),
        set(args.exclude_teacher), args.max_tokens,
        args.min_chars, args.prefix_chars, args.prefix_cap,
        args.drop_leaks,
    )
    random.Random(args.seed).shuffle(kept)
    with open(args.dst, "w", encoding="utf-8") as handle:
        for rec in kept:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
    report_path = args.report or args.dst.rsplit(".", 1)[0] + ".report.json"
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
