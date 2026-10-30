"""Apply the 12k training window to the exported SFT dataset.

Samples are measured with the real student tokenizer (same Qwen3 chat-template
wrapping as ``tokenize_report``). Anything over the budget is handled by the
least-lossy path:

* prompts carrying ``<untrusted_action_history>`` get the block's *head*
  dropped and its *tail* kept — identical policy to the production renderer
  (``_history_text`` keeps ``value[-MAX:]``, the newest part of the record);
* prompts without a truncatable block are dropped into
  ``prepare_excluded.jsonl``.

Truncation works on token ids: the history text is encoded, the first N ids
are cut, and the rest decoded back — a raw mid-token cut, no marker, matching
what the engine itself does when history overflows.

Outputs ``train_final.jsonl`` / ``eval_final.jsonl`` plus
``prepare_report.json``; every truncated sample keeps ``meta.truncated = true``
and the before/after token counts so the eval side can isolate the effect.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from tokenize_report import history_span, wrap_chat

_HERE = Path(__file__).resolve().parent
_DEFAULT_REPO = "Qwen/Qwen3-4B-Instruct-2507"


def load_tokenizer(source: str | None):
    """Return a ``tokenizers.Tokenizer`` for the student model."""
    from tokenizers import Tokenizer

    if source and Path(source).is_file():
        return Tokenizer.from_file(source)
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(source or _DEFAULT_REPO, "tokenizer.json")
    return Tokenizer.from_file(path)


def token_count(tokenizer, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False).ids)


def sample_total_tokens(tokenizer, messages: list[dict]) -> int:
    """Full training sequence: templated prompt + target + close tag."""
    from tokenize_report import IM_END

    prompt = wrap_chat(messages[:-1], add_generation_prompt=True)
    closing = messages[-1].get("content", "") + IM_END + "\n"
    return token_count(tokenizer, prompt + closing)


def truncate_history(tokenizer, messages: list[dict], excess: int) -> list[dict] | None:
    """Drop the head of the history block so the sample fits.

    Keeps the tail (newest events) like the production renderer. Returns new
    messages, or ``None`` when no truncatable block exists or the whole block
    is shorter than the excess.
    """
    new_messages = [dict(m) for m in messages]
    for message in new_messages:
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        span = history_span(content)
        if span is None:
            continue
        inner = content[span[0]:span[1]]
        ids = tokenizer.encode(inner, add_special_tokens=False).ids
        if len(ids) <= excess:
            return None
        message["content"] = (
            content[: span[0]]
            + tokenizer.decode(ids[excess:])
            + content[span[1]:]
        )
        return new_messages
    return None


def process_sample(
    tokenizer, sample: dict, budget: int, safety: int = 8
) -> tuple[dict | None, str]:
    """Return ``(sample, action)`` where action is keep/truncate/drop.

    ``safety`` shaves extra tokens because a mid-token cut decodes into text
    that can re-encode one or two tokens longer than the raw arithmetic.
    """
    total = sample_total_tokens(tokenizer, sample["messages"])
    if total <= budget:
        return sample, "keep"
    truncated = truncate_history(
        tokenizer, sample["messages"], total - budget + safety
    )
    if truncated is None:
        return None, "drop"
    final = sample_total_tokens(tokenizer, truncated)
    out = {**sample, "messages": truncated}
    out["meta"] = {
        **sample["meta"],
        "truncated": True,
        "orig_tokens": total,
        "final_tokens": final,
    }
    return out, "truncate"


def _process_file(tokenizer, src: Path, budget: int):
    kept, dropped = [], []
    with open(src, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            sample = json.loads(line)
            out, action = process_sample(tokenizer, sample, budget)
            if out is None:
                dropped.append(
                    {
                        "request_id": sample["meta"].get("request_id"),
                        "call_kind": sample["meta"].get("call_kind"),
                        "reason": "over_budget_no_history",
                    }
                )
            else:
                kept.append((out, action))
    return kept, dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--files",
        nargs="+",
        default=[str(_HERE / "data" / "train.jsonl"), str(_HERE / "data" / "eval.jsonl")],
    )
    parser.add_argument("--budget", type=int, default=12288)
    parser.add_argument("--tokenizer", default=None)
    parser.add_argument("--out-dir", default=str(_HERE / "data"))
    args = parser.parse_args(argv)

    tokenizer = load_tokenizer(args.tokenizer)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, object] = {"budget": args.budget, "files": {}}
    all_dropped: list[dict] = []
    totals = Counter()
    final_max = 0
    for src_str in args.files:
        src = Path(src_str)
        kept, dropped = _process_file(tokenizer, src, args.budget)
        all_dropped.extend(dropped)
        n_trunc = sum(1 for _, action in kept if action == "truncate")
        for sample, _ in kept:
            final_max = max(final_max, sample["meta"].get("final_tokens") or 0)
        dst = out_dir / (src.stem + "_final.jsonl")
        with open(dst, "w", encoding="utf-8") as handle:
            for sample, _ in kept:
                handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
        report["files"][src.name] = {
            "total": len(kept) + len(dropped),
            "kept": len(kept),
            "truncated": n_trunc,
            "dropped": len(dropped),
            "out": str(dst),
        }
        totals["kept"] += len(kept)
        totals["truncated"] += n_trunc
        totals["dropped"] += len(dropped)

    with open(out_dir / "prepare_excluded.jsonl", "w", encoding="utf-8") as handle:
        for row in all_dropped:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    report["overall"] = dict(totals)
    report["final_max_tokens"] = final_max
    (out_dir / "prepare_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())