"""Measure the exported SFT dataset with the real student tokenizer.

Answers the questions that decide the training window:

* How long is each sample in *tokens* (Qwen3 chat-template wrapped)?
* Which training windows (4k/8k/12k/16k/24k/32k) fit what fraction of data?
* How much of each prompt is the tail-truncatable history block
  (``<untrusted_action_history>``), i.e. how much can tail-truncation reclaim
  without touching the contract head?
* How long are completions (inference ``max_new_tokens`` planning)?

The ``tokenizers`` library alone is used (no torch/transformers import: the
local torch install is broken and unneeded). The Qwen3 chat template is
replicated literally: ``<|im_start|>{role}\\n{content}<|im_end|>\\n`` plus a
``<|im_start|>assistant\\n`` generation prompt before the target.

Usage::

    python tokenize_report.py                      # defaults: data/*.jsonl
    python tokenize_report.py --files data/train.jsonl
    python tokenize_report.py --tokenizer <path-or-repo>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

IM_START = "<|im_start|>"
IM_END = "<|im_end|>"
GEN_PROMPT = f"{IM_START}assistant\n"
_HISTORY_OPEN = "<untrusted_action_history>"
_HISTORY_CLOSE = "</untrusted_action_history>"
_DEFAULT_REPO = "Qwen/Qwen3-4B-Instruct-2507"
_WINDOWS = (4096, 8192, 12288, 16384, 24576, 32768)


def wrap_chat(messages: list[dict], *, add_generation_prompt: bool = False) -> str:
    """Serialise messages exactly like the Qwen3 instruct chat template."""
    parts = [
        f"{IM_START}{m['role']}\n{m.get('content', '')}{IM_END}\n" for m in messages
    ]
    if add_generation_prompt:
        parts.append(GEN_PROMPT)
    return "".join(parts)


def history_span(content: str) -> tuple[int, int] | None:
    """Char span of the history payload inside a pipeline-style prompt."""
    start = content.find(_HISTORY_OPEN)
    if start < 0:
        return None
    inner_start = start + len(_HISTORY_OPEN)
    end = content.find(_HISTORY_CLOSE, inner_start)
    if end < 0:
        return None
    return inner_start, end


def bucket_of(tokens: int) -> str:
    for win in _WINDOWS:
        if tokens < win:
            return f"<{win // 1024}k"
    return f">={_WINDOWS[-1] // 1024}k"


def percentile(sorted_values: list[int], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = (len(sorted_values) - 1) * q
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return float(sorted_values[lo])
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (idx - lo)


def measure_sample(
    sample: dict, encode
) -> dict:
    """Token metrics for one exported sample.

    ``encode(text) -> int`` is injected (token count), keeping the function
    pure and unit-testable without a real tokenizer.
    """
    messages = sample["messages"]
    prompt_msgs, target = messages[:-1], messages[-1]
    prompt_text = wrap_chat(prompt_msgs, add_generation_prompt=True)
    full_text = prompt_text + f"{target.get('content', '')}{IM_END}\n"

    prompt_tokens = encode(prompt_text)
    total_tokens = encode(full_text)
    completion_tokens = encode(target.get("content", ""))

    history_tokens = 0
    user_text = "".join(
        m.get("content", "") for m in prompt_msgs if m["role"] == "user"
    )
    span = history_span(user_text)
    if span is not None:
        history_tokens = encode(user_text[span[0] : span[1]])

    return {
        "call_kind": sample["meta"].get("call_kind"),
        "contract_id": sample["meta"].get("contract_id"),
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "history_tokens": history_tokens,
        "truncatable_overhead": total_tokens - history_tokens,
    }


def summarise(rows: list[dict]) -> dict:
    """Aggregate per-sample metrics into the report block for one group."""
    totals = sorted(r["total_tokens"] for r in rows)
    completions = sorted(r["completion_tokens"] for r in rows)
    histories = sorted(r["history_tokens"] for r in rows)
    fits = {}
    for win in _WINDOWS:
        n_fit = sum(1 for t in totals if t <= win)
        # Samples that fit after cutting history to the bone: only the
        # non-history overhead must fit the window.
        n_fit_trunc = sum(
            1 for r in rows if r["truncatable_overhead"] <= win
        )
        fits[f"{win // 1024}k"] = {
            "samples_fit": n_fit,
            "pct": round(100.0 * n_fit / len(rows), 2) if rows else 0.0,
            "samples_fit_tail_trunc": n_fit_trunc,
            "pct_tail_trunc": (
                round(100.0 * n_fit_trunc / len(rows), 2) if rows else 0.0
            ),
        }
    qs = (0.5, 0.75, 0.9, 0.95, 0.99)
    return {
        "samples": len(rows),
        "total_tokens": {
            **{f"p{int(q * 100)}": percentile(totals, q) for q in qs},
            "max": totals[-1] if totals else 0,
            "mean": round(sum(totals) / len(totals), 1) if totals else 0,
        },
        "completion_tokens": {
            **{f"p{int(q * 100)}": percentile(completions, q) for q in qs},
            "max": completions[-1] if completions else 0,
        },
        "history_tokens": {
            "p50": percentile(histories, 0.5),
            "p90": percentile(histories, 0.9),
            "with_history": sum(1 for h in histories if h > 0),
        },
        "fit_by_window": fits,
        "buckets": dict(Counter(bucket_of(t) for t in totals)),
    }


def load_encode(source: str | None):
    """Return a ``text -> token_count`` callable for the student tokenizer."""
    from tokenizers import Tokenizer

    if source and Path(source).is_file():
        tokenizer = Tokenizer.from_file(source)
    else:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(source or _DEFAULT_REPO, "tokenizer.json")
        tokenizer = Tokenizer.from_file(path)

    def encode(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    return encode


def _iter_samples(paths: list[str]):
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield path, json.loads(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    here = Path(__file__).resolve().parent
    parser.add_argument(
        "--files",
        nargs="+",
        default=[str(here / "data" / "train.jsonl"), str(here / "data" / "eval.jsonl")],
    )
    parser.add_argument(
        "--tokenizer",
        default=None,
        help="tokenizer.json path or HF repo id (default: %(default)s)",
    )
    parser.add_argument("--out", default=str(here / "data" / "tokenize_report.json"))
    args = parser.parse_args(argv)

    encode = load_encode(args.tokenizer)

    per_file: dict[str, list[dict]] = defaultdict(list)
    per_kind: dict[str, list[dict]] = defaultdict(list)
    all_rows: list[dict] = []
    for path, sample in _iter_samples(args.files):
        row = measure_sample(sample, encode)
        per_file[Path(path).name].append(row)
        per_kind[row["call_kind"] or "(none)"].append(row)
        all_rows.append(row)

    report = {
        "tokenizer": args.tokenizer or _DEFAULT_REPO,
        "windows_tokens": list(_WINDOWS),
        "overall": summarise(all_rows),
        "by_file": {name: summarise(rows) for name, rows in per_file.items()},
        "by_call_kind": {
            name: summarise(rows) for name, rows in sorted(per_kind.items())
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    overall = report["overall"]
    print(f"samples={overall['samples']}  total p50={overall['total_tokens']['p50']:.0f} "
          f"p90={overall['total_tokens']['p90']:.0f} p99={overall['total_tokens']['p99']:.0f} "
          f"max={overall['total_tokens']['max']}")
    print("window | fit% | fit% with history tail-trunc")
    for win, fit in overall["fit_by_window"].items():
        print(f"  {win:>5} | {fit['pct']:5.2f} | {fit['pct_tail_trunc']:5.2f}")
    print("by call_kind samples:", {k: v["samples"] for k, v in report["by_call_kind"].items()})
    print(f"report -> {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
