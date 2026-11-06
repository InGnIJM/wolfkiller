"""Offline held-out evaluation for the post-training SFT student.

Runs inside WSL2 (needs torch / transformers / peft / bitsandbytes):

    ~/sft/bin/python eval_student.py --limit 8 --mode both   # smoke
    ~/sft/bin/python eval_student.py --mode both             # full 910 rows
    ~/sft/bin/python eval_student.py --mode loss             # loss only

Two measurements per row:

* **completion NLL** — teacher-forced loss over the assistant completion only
  (the prompt is masked), i.e. the number the SFT training log also reports.
* **structured replay** — greedy generation from the same prompt, then compare
  against the gold assistant turn.  Action contracts are scored field by field
  (``action_type`` / ``target_seat`` / ``target_seats`` / ``side`` / ``speak`` /
  ``preferred_target``); free text (speech, ``text`` / ``day_plan`` fields) is
  scored with char-bigram F1.

Rows whose full conversation exceeds ``--max-seq-len`` are reported as skipped
(the student was trained on <=8192-token samples, so longer prompts are out of
distribution and would also blow the 8GB budget).

The pure helpers (:func:`parse_json_object`, :func:`compare_predictions`,
:func:`summarize`) are unit-tested; the model loop is exercised by running the
script on a small ``--limit``.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import sys
import time
from typing import Any, Callable, Iterable

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


# ---------------------------------------------------------------- pure helpers


def iter_records(path: str) -> Iterable[dict]:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def normalise_text(text: Any) -> str:
    """Drop all whitespace so CJK/emoji comparisons are not whitespace-sensitive."""
    return re.sub(r"\s+", "", str(text or ""))


def _json_candidates(text: str) -> list[str]:
    text = (text or "").strip()
    candidates = [text]
    for match in _FENCE.finditer(text):
        candidates.insert(0, match.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    return candidates


def parse_json_object(text: str) -> dict | None:
    """First JSON object inside ``text`` (tolerates ```json fences), else None."""
    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def classify_gold(text: str) -> tuple[str, Any]:
    """('json', dict) when the gold assistant turn is a JSON object, else ('text', str)."""
    obj = parse_json_object(text)
    if obj is not None:
        return "json", obj
    return "text", str(text or "")


def _as_targets(obj: dict) -> tuple[int, ...] | None:
    """Normalised target tuple: multi-target ``target_seats`` wins over ``target_seat``."""
    if "target_seats" in obj and obj["target_seats"] is not None:
        value = obj["target_seats"]
        if not isinstance(value, (list, tuple)):
            return None
        try:
            return tuple(sorted(int(item) for item in value))
        except (TypeError, ValueError):
            return None
    if "target_seat" in obj and obj["target_seat"] is not None:
        try:
            return (int(obj["target_seat"]),)
        except (TypeError, ValueError):
            return None
    return None


def char_ngram_f1(pred: str, gold: str, n: int = 2) -> float:
    """Whitespace-insensitive char-bigram F1 (0.0 when either side is empty)."""
    pred_text, gold_text = normalise_text(pred), normalise_text(gold)
    if not pred_text or not gold_text:
        return 0.0

    def grams(text: str) -> collections.Counter:
        if len(text) < n:
            return collections.Counter([text])
        return collections.Counter(text[i : i + n] for i in range(len(text) - n + 1))

    pred_grams, gold_grams = grams(pred_text), grams(gold_text)
    overlap = sum((pred_grams & gold_grams).values())
    if overlap == 0:
        return 0.0
    precision = overlap / sum(pred_grams.values())
    recall = overlap / sum(gold_grams.values())
    return 2 * precision * recall / (precision + recall)


def compare_predictions(gold_text: str, pred_text: str) -> dict:
    """Field-level comparison of one generated answer against the gold turn."""
    kind, gold = classify_gold(gold_text)
    metrics: dict[str, Any] = {"gold_kind": kind}
    if kind == "text":
        metrics["text_f1"] = char_ngram_f1(pred_text, gold)
        metrics["pred_json"] = parse_json_object(pred_text) is not None
        metrics["length_ratio"] = _ratio(len(normalise_text(pred_text)), len(normalise_text(gold)))
        return metrics

    pred = parse_json_object(pred_text)
    metrics["pred_json_ok"] = pred is not None
    if pred is None:
        return metrics

    if "action_type" in gold:
        metrics["action_type_ok"] = pred.get("action_type") == gold["action_type"]
    gold_targets = _as_targets(gold)
    if gold_targets is not None:
        metrics["target_ok"] = _as_targets(pred) == gold_targets
    if "action_type_ok" in metrics and "target_ok" in metrics:
        metrics["exact_ok"] = metrics["action_type_ok"] and metrics["target_ok"]
    if "side" in gold:
        metrics["side_ok"] = pred.get("side") == gold["side"]
    if "speak" in gold:
        metrics["speak_ok"] = pred.get("speak") == gold["speak"]
    if "preferred_target" in gold:
        metrics["preferred_target_ok"] = pred.get("preferred_target") == gold["preferred_target"]
    if isinstance(gold.get("text"), str):
        metrics["text_f1"] = char_ngram_f1(pred.get("text", ""), gold["text"])
    if isinstance(gold.get("day_plan"), str):
        metrics["day_plan_f1"] = char_ngram_f1(pred.get("day_plan", ""), gold["day_plan"])
    return metrics


def _ratio(value: int, reference: int) -> float:
    if not reference:
        return 0.0
    return round(value / reference, 4)


def select_generation_subset(
    records: list[dict], limit: int, eligible: set[int] | None = None
) -> set[int] | None:
    """Deterministic call_kind-stratified row indices for generation.

    ``limit <= 0`` (or >= population) means "every row" -> None.  Otherwise each
    kind gets a proportional share (largest-remainder apportionment) and rows are
    picked evenly spaced inside the kind, so the subset is reproducible.
    ``eligible`` (row indices) filters the population first, e.g. to prompts that
    fit the generation memory budget.
    """
    if limit <= 0 or limit >= len(records):
        return None
    buckets: dict[str, list[int]] = collections.defaultdict(list)
    for index, record in enumerate(records):
        if eligible is not None and index not in eligible:
            continue
        buckets[(record.get("meta") or {}).get("call_kind") or "unknown"].append(index)

    total = sum(len(indices) for indices in buckets.values()) or 1
    shares = {kind: limit * len(indices) / total for kind, indices in buckets.items()}
    taken = {kind: int(share) for kind, share in shares.items()}
    remainder = limit - sum(taken.values())
    for kind in sorted(shares, key=lambda k: (shares[k] - taken[k], -len(buckets[k])), reverse=True)[:remainder]:
        taken[kind] += 1

    picked: list[int] = []
    for kind, indices in buckets.items():
        share = min(taken[kind], len(indices))
        if share <= 0:
            continue
        for slot in range(share):
            picked.append(indices[int((slot + 0.5) * len(indices) / share)])
    return set(sorted(picked)[:limit])


# ------------------------------------------------------------- aggregation


def _mean(values: Iterable[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def _rate(rows: list[dict], key: str) -> dict | None:
    scored = [row["metrics"][key] for row in rows if key in row["metrics"]]
    if not scored:
        return None
    hits = sum(1 for value in scored if value)
    return {"n": len(scored), "rate": round(hits / len(scored), 4)}


def _bucket(rows: list[dict]) -> dict:
    losses = [row["loss"] for row in rows if row.get("loss") is not None]
    tokens = sum(row.get("completion_tokens") or 0 for row in rows)
    bucket: dict[str, Any] = {
        "n": len(rows),
        "n_generated": sum(1 for row in rows if row.get("prediction") is not None),
        "completion_nll": _mean(losses),
        "completion_tokens": tokens,
        "json_parse": _rate(rows, "pred_json_ok"),
        "action_type": _rate(rows, "action_type_ok"),
        "target": _rate(rows, "target_ok"),
        "exact": _rate(rows, "exact_ok"),
        "side": _rate(rows, "side_ok"),
        "speak": _rate(rows, "speak_ok"),
        "preferred_target": _rate(rows, "preferred_target_ok"),
        "text_f1": _mean(row["metrics"].get("text_f1") for row in rows),
        "day_plan_f1": _mean(row["metrics"].get("day_plan_f1") for row in rows),
    }
    return {key: value for key, value in bucket.items() if value is not None}


def summarize(rows: list[dict], skipped: list[dict]) -> dict:
    """Aggregate per-row results, overall and split by call_kind / contract / teacher."""
    buckets: dict[str, dict] = {}
    for field in ("call_kind", "contract_id", "teacher"):
        grouped: dict[str, list[dict]] = collections.defaultdict(list)
        for row in rows:
            grouped[row.get(field) or "unknown"].append(row)
        buckets[field] = {name: _bucket(group) for name, group in sorted(grouped.items())}

    losses = [row["loss"] for row in rows if row.get("loss") is not None]
    overall = _bucket(rows)
    if overall.get("completion_nll") is not None:
        overall["ppl"] = round(math.exp(overall["completion_nll"]), 3)
    overall["n_skipped"] = len(skipped)
    overall["skipped_reasons"] = dict(collections.Counter(item.get("reason", "unknown") for item in skipped))
    return {"overall": overall, **buckets}


def format_markdown(report: dict, args: argparse.Namespace) -> str:
    """Human-readable summary card for the eval run."""
    overall = report["overall"]
    lines = [
        "# Student eval — held-out split",
        "",
        f"- adapter: `{args.adapter}`",
        f"- rows evaluated: {overall['n']} (loss) / {overall.get('n_generated', 0)} (generation), "
        f"skipped {overall['n_skipped']} (over max-seq-len)",
        f"- completion NLL: **{overall.get('completion_nll')}**  (ppl {overall.get('ppl')})",
        f"- JSON parse rate: {_pct(overall.get('json_parse'))}",
        f"- action_type acc: {_pct(overall.get('action_type'))}  |  target acc: {_pct(overall.get('target'))}",
        f"- speech char-F1: {overall.get('text_f1')}  |  day_plan char-F1: {overall.get('day_plan_f1')}",
        "",
        "| contract | n | NLL | JSON | action | target |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, bucket in report["contract_id"].items():
        lines.append(
            f"| {name} | {bucket['n']} | {bucket.get('completion_nll')} | "
            f"{_pct(bucket.get('json_parse'))} | {_pct(bucket.get('action_type'))} | {_pct(bucket.get('target'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _pct(slot: dict | None) -> str:
    if not slot or slot.get("rate") is None:
        return "—"
    return f"{slot['rate'] * 100:.1f}% ({slot['n']})"


# --------------------------------------------------------------- model loop


def build_chat_ids(tokenizer, messages: list[dict], add_generation_prompt: bool) -> list[int]:
    out = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=add_generation_prompt,
        return_dict=False,
    )
    if isinstance(out, dict):
        out = out["input_ids"]
    return list(out)


def load_student(base: str, adapter: str, max_seq_len: int, loader: str = "unsloth"):
    """4-bit base + LoRA adapter; ``unsloth`` is ~2x faster at decode on the 4060."""
    import torch
    from peft import PeftModel

    if loader == "unsloth":
        from unsloth import FastLanguageModel

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=base,
            max_seq_length=max_seq_len,
            load_in_4bit=True,
            dtype=None,
        )
        model = PeftModel.from_pretrained(model, adapter)
        FastLanguageModel.for_inference(model)
        model.eval()
        return model, tokenizer

    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    tokenizer = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(
        base,
        quantization_config=quant,
        device_map={"": torch.cuda.current_device()},
        torch_dtype=torch.bfloat16,
    )
    model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    return model, tokenizer


def _backbone(model):
    """(transformer, lm_head) of a causal LM, unwrapping PEFT/Unsloth wrappers."""
    node = model
    for _ in range(5):
        base = getattr(node, "base_model", None)
        if base is None or base is node or not hasattr(base, "model"):
            break
        node = base
    inner = getattr(node, "model", node)
    return inner.model, inner.lm_head


def make_loss_fn(model, tokenizer) -> Callable[[list[dict], int], tuple[float | None, int, str]]:
    import torch
    import torch.nn.functional as F

    def loss_fn(messages: list[dict], max_seq_len: int):
        full = build_chat_ids(tokenizer, messages, add_generation_prompt=False)
        prompt = build_chat_ids(tokenizer, messages[:-1], add_generation_prompt=True)
        if len(full) > max_seq_len:
            return None, 0, f"too_long:{len(full)}>{max_seq_len}"
        if full[: len(prompt)] != prompt:
            return None, 0, "prefix_mismatch"
        completion = len(full) - len(prompt)
        if completion <= 0:
            return None, 0, "empty_completion"
        input_ids = torch.tensor([full], device=model.device)
        labels = torch.tensor([full], device=model.device)
        try:
            # Compute logits only over completion positions in small chunks.  A
            # full [L, 151936] logits tensor is ~2.5 GB at L=8192 and pushes the
            # 8 GB card into WDDM shared-memory thrash (the 2026-11-02 run hung
            # for 55 min on the first ~7k-token row).
            transformer, head = _backbone(model)
            with torch.no_grad():
                hidden = transformer(input_ids=input_ids, use_cache=False).last_hidden_state
            total, count = 0.0, 0
            for start in range(len(prompt), len(full), 256):
                end = min(start + 256, len(full))
                logits = head(hidden[:, start - 1 : end - 1]).float()
                target = labels[:, start:end]
                total += float(
                    F.cross_entropy(
                        logits.reshape(-1, logits.shape[-1]), target.reshape(-1), reduction="sum"
                    )
                )
                count += target.numel()
                del logits, target
            torch.cuda.empty_cache()
            return total / count, completion, "ok"
        except Exception:
            labels = input_ids.clone()
            labels[:, : len(prompt)] = -100
            with torch.no_grad():
                output = model(input_ids=input_ids, labels=labels)
            torch.cuda.empty_cache()
            return float(output.loss), completion, "ok"

    return loss_fn


def make_gen_fn(model, tokenizer, max_new_tokens: int) -> Callable[[list[dict], int], str]:
    import torch
    from transformers import StoppingCriteria, StoppingCriteriaList

    class _TokenCap(StoppingCriteria):
        """Hard cap on generated tokens.

        Unsloth's patched ``generate`` silently ignored ``max_new_tokens`` on
        some long prompts (the 2026-11-02 eval wedged for 55 min on one row),
        so the cap is enforced independently of the generation config.
        """

        def __init__(self, prompt_len: int, limit: int):
            self.prompt_len = prompt_len
            self.limit = limit

        def __call__(self, input_ids, scores, **kwargs) -> bool:
            return bool(input_ids.shape[-1] - self.prompt_len >= self.limit)

    def gen_fn(messages: list[dict], _max_new_tokens: int) -> str:
        prompt = build_chat_ids(tokenizer, messages[:-1], add_generation_prompt=True)
        input_ids = torch.tensor([prompt], device=model.device)
        cap = _TokenCap(len(prompt), _max_new_tokens)
        with torch.no_grad():
            output = model.generate(
                input_ids=input_ids,
                max_new_tokens=_max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                stopping_criteria=StoppingCriteriaList([cap]),
            )
        return tokenizer.decode(output[0][len(prompt) :], skip_special_tokens=True)

    return gen_fn


def run_eval(
    records: list[dict],
    loss_fn,
    gen_fn,
    *,
    max_seq_len: int,
    max_new_tokens: int,
    mode: str,
    progress_every: int = 25,
    gen_indices: set[int] | None = None,
    skip_ids: set[str] | None = None,
    skip_indices: set[int] | None = None,
    on_row=None,
) -> tuple[list[dict], list[dict]]:
    """Score every record; returns (rows, skipped).

    ``gen_indices`` restricts the (expensive) greedy replay to a subset while
    loss is still measured on every row; ``None`` means generate for all rows.
    """
    rows: list[dict] = []
    skipped: list[dict] = []
    started = time.time()
    for index, record in enumerate(records, 1):
        messages = record["messages"]
        meta = record.get("meta") or {}
        row_index = index - 1
        if skip_indices and row_index in skip_indices:
            continue
        if skip_ids and meta.get("request_id") in skip_ids:
            continue
        row: dict[str, Any] = {
            "row_index": row_index,
            "request_id": meta.get("request_id"),
            "call_kind": meta.get("call_kind"),
            "contract_id": meta.get("contract_id"),
            "teacher": meta.get("teacher"),
            "loss": None,
            "completion_tokens": 0,
        }
        if mode in ("loss", "both"):
            loss, tokens, status = loss_fn(messages, max_seq_len)
            if status != "ok":
                skipped.append({"request_id": meta.get("request_id"), "reason": status})
                continue
            row["loss"], row["completion_tokens"] = loss, tokens
        do_generate = mode in ("gen", "both") and (gen_indices is None or index - 1 in gen_indices)
        if do_generate:
            row["prediction"] = gen_fn(messages, max_new_tokens).strip()
            row["metrics"] = compare_predictions(messages[-1]["content"], row["prediction"])
        else:
            row["metrics"] = {}
        rows.append(row)
        if on_row is not None:
            on_row(row)
        if progress_every and index % progress_every == 0:
            elapsed = time.time() - started
            print(
                f"[{index}/{len(records)}] {elapsed:.0f}s ({elapsed / index:.2f}s/row) "
                f"loss={row.get('loss')} gen={'prediction' in row} "
                f"kind={row.get('call_kind')} contract={row.get('contract_id')}",
                flush=True,
            )
    return rows, skipped


# ------------------------------------------------------------------- driver


def partial_rows_path(out: str) -> str:
    """Sidecar JSONL with one finished row per line, used by --resume."""
    return out.rsplit(".", 1)[0] + ".rows.jsonl"


def _row_key(row: dict):
    """Stable row identity: dataset position, falling back to request_id.

    request_id alone is not unique in the capture set (different games reuse ids
    like ``speech:1:sheriff_campaign:1``), so collapsing on it silently drops
    real rows.
    """
    if row.get("row_index") is not None:
        return ("row", row["row_index"])
    return ("req", row.get("request_id"))


def merge_rows(done: dict[str, dict], new_rows: list[dict]) -> list[dict]:
    """Merge freshly computed rows into the sidecar rows (gen run over loss run)."""
    merged = {_row_key(value): dict(value) for value in done.values()}
    for row in new_rows:
        key = _row_key(row)
        if key not in merged:
            merged[key] = row
            continue
        base = merged[key]
        for field, value in row.items():
            if field == "metrics":
                if value:
                    base["metrics"] = value
            elif value is not None:
                base[field] = value
        base.setdefault("metrics", {})
    return list(merged.values())


def load_partial_rows(path: str) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("request_id"):
                done[row["request_id"]] = row
    return done


def evaluate(args: argparse.Namespace) -> dict:
    records = list(iter_records(args.data))
    if args.limit:
        records = records[: args.limit]
    model, tokenizer = load_student(args.base, args.adapter, args.max_seq_len, args.loader)
    loss_fn = make_loss_fn(model, tokenizer)
    gen_fn = make_gen_fn(model, tokenizer, args.max_new_tokens)
    eligible = None
    if args.gen_sample and args.max_gen_prompt:
        eligible = {
            index
            for index, record in enumerate(records)
            if len(build_chat_ids(tokenizer, record["messages"][:-1], add_generation_prompt=True))
            <= args.max_gen_prompt
        }
        print(f"generation eligibility: {len(eligible)}/{len(records)} rows <= {args.max_gen_prompt} prompt tokens", flush=True)
    gen_indices = select_generation_subset(records, args.gen_sample, eligible)
    partial_path = partial_rows_path(args.out)
    done = load_partial_rows(partial_path) if args.resume else {}
    skip_indices = {row["row_index"] for row in done.values() if row.get("row_index") is not None}
    legacy_skip_ids = {
        row["request_id"] for row in done.values() if row.get("row_index") is None and row.get("request_id")
    }
    if args.resume and done:
        print(f"resume: {len(done)} rows already done -> {partial_path}", flush=True)
    handle = open(partial_path, "a" if args.resume else "w", encoding="utf-8")

    def _on_row(row: dict) -> None:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()

    try:
        rows, skipped = run_eval(
            records,
            loss_fn,
            gen_fn,
            max_seq_len=args.max_seq_len,
            max_new_tokens=args.max_new_tokens,
            mode=args.mode,
            progress_every=args.progress_every,
            gen_indices=gen_indices,
            skip_ids=legacy_skip_ids or None,
            skip_indices=skip_indices or None,
            on_row=_on_row,
        )
    finally:
        handle.close()
    rows = merge_rows(done, rows)
    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "args": {key: str(value) for key, value in vars(args).items()},
        "summary": summarize(rows, skipped),
        "rows": rows,
        "skipped": skipped,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    md_path = args.out.rsplit(".", 1)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(format_markdown(report["summary"], args))
    print(json.dumps(report["summary"]["overall"], ensure_ascii=False, indent=2))
    print(f"report -> {args.out}\nmarkdown -> {md_path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/eval_final.jsonl")
    parser.add_argument("--base", default="/mnt/e/models/Qwen3-4B-Instruct-2507")
    parser.add_argument("--adapter", default="ckpt/wolfkiller-sft-strong/lora_final")
    parser.add_argument("--out", default="data/eval_report.json")
    parser.add_argument("--limit", type=int, default=0, help="only the first N rows (smoke)")
    parser.add_argument("--mode", choices=("loss", "gen", "both"), default="both")
    parser.add_argument("--max-seq-len", type=int, default=8192)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--resume", action="store_true",
                        help="skip rows already present in the .rows.jsonl sidecar")
    parser.add_argument("--loader", choices=("unsloth", "transformers"), default="unsloth")
    parser.add_argument(
        "--gen-sample", type=int, default=0,
        help="generate for a stratified subset of N rows (0 = all rows)",
    )
    parser.add_argument(
        "--max-gen-prompt", type=int, default=4500,
        help="skip generation for prompts above N tokens (VRAM safety; 0 = no cap)",
    )
    evaluate(parser.parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
