"""Filter exported SFT samples to a single teacher and a token budget.

Runs inside WSL2 (tokenizer reads the local model dir):

    ~/sft/bin/python subset_teacher.py \
        --in  /mnt/e/code/WolfKiller/post_training/data/train_final.jsonl \
        --out /mnt/e/code/WolfKiller/post_training/data/train_strong.jsonl \
        --teacher gemini-3.8-flash-high \
        --model /mnt/e/models/Qwen3-4B-Instruct-2507 \
        --max-tokens 8192

Two filters, both measured exactly:

* ``meta.teacher`` equality — single-teacher distillation kills the
  multi-teacher style noise that dilutes a 4B student's gradient.
* total rendered token length <= ``--max-tokens`` — TRL right-truncates at
  ``max_length``, which would silently clip the assistant completion at the
  tail and turn the sample into a prompt-only row. Measuring with the real
  chat template keeps every kept sample intact.
"""

from __future__ import annotations

import argparse
import json
from typing import Iterable


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


def build_subset(records: Iterable[dict], tokenizer, teacher: str, max_tokens: int) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    stats = {"seen": 0, "wrong_teacher": 0, "too_long": 0, "kept": 0}
    for rec in records:
        stats["seen"] += 1
        meta = rec.get("meta") or {}
        if meta.get("teacher") != teacher:
            stats["wrong_teacher"] += 1
            continue
        if rendered_len(tokenizer, rec["messages"]) > max_tokens:
            stats["too_long"] += 1
            continue
        kept.append(rec)
        stats["kept"] += 1
    return kept, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", dest="dst", required=True)
    parser.add_argument("--teacher", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-tokens", type=int, default=8192)
    args = parser.parse_args()

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    kept, stats = build_subset(iter_records(args.src), tokenizer, args.teacher, args.max_tokens)
    with open(args.dst, "w", encoding="utf-8") as handle:
        for rec in kept:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
