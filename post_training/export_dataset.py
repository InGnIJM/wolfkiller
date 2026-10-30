"""Export captured (prompt, completion) pairs into SFT chat JSONL.

Joins ``backend/data/games/*/llm_prompts.jsonl`` (raw prompts written by
``PromptCapture``) with ``model_requests.normalized_result_json`` (the accepted
teacher output) and emits one training sample per record.

Filters (each exclusion is written to ``excluded.jsonl`` with a reason):

* ``engine_fallback``  – ``technical_failure_code`` records are engine-supplied
  ``abstain`` placeholders, not model output.
* ``degraded``         – envelope says the stored result was degraded.
* ``empty_speech``     – speech payload has ``is_none`` or blank text.
* ``weak_teacher``     – teacher model on the exclusion list (default on; pass
  ``--include-weak-teachers`` to keep them as tagged samples instead).
* ``upgrade_conflict`` – old-shape ROLE_CONTRACT whose surviving fields no
  longer match the frozen registry (the prompt would silently teach stale
  semantics; upgrading is unsafe, so the record is excluded).
* ``no_result``        – no consumed/resolved ledger row.

Format upgrade (default on, ``--no-upgrade`` to disable):

* Pipeline prompts rendered before 2026-10-26 lack ``action_types`` /
  ``actions_requiring_target`` / ``multi_target_actions`` in ROLE_CONTRACT.
  The frozen registry is the single source of truth, so the current values are
  spliced back in after verifying every surviving field is unchanged.
* PROJECTED_CONTEXT rendered before 2026-10-09 lacks ``degraded``; ``false``
  is injected (first asks are overwhelmingly non-degraded; the flag is
  recorded in ``meta.context_upgraded`` for auditing).

Completion targets:

* ``speech``   -> raw speech text (parses through ``parse_speech``).
* ``action``   -> canonical JSON of the inner ``command`` object.
* ``night``    -> canonical JSON of the payload minus junk keys.
* ``pipeline`` -> canonical JSON of the payload verbatim (``schema_version``
  is a required field of the emitted contract schema, so it stays).

Split: by ``game_id`` (no same-game leakage between train/eval), deterministic
seed. Meta carries audit fields; training frameworks read ``messages``.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

WEAK_TEACHERS = frozenset({"glm-5.3-flash", "mimo-v2.6-flash"})
#: Meta keys the ledger adds around the model-visible payload. They are never
#: part of what the model emitted, so completions drop them. ``schema_version``
#: is deliberately absent: pipeline contracts declare it a *required* output
#: field, so the stored value is model-emitted and must survive.
_LEDGER_META_KEYS = frozenset(
    {"technical_failure_code", "timeout_type", "degraded", "_"}
)
_REPAIR_HINT = "previous action was invalid"
_ROLE_CONTRACT_PREFIX = "ROLE_CONTRACT="
_PROJECTED_PREFIX = "PROJECTED_CONTEXT="


def canonical_json(value: object) -> str:
    """Byte-identical serialisation to ``prompt_renderer._json``."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _decode_embedded(content: str, prefix: str) -> tuple[dict, int, int] | None:
    """Return (parsed_object, start, end) of the JSON after ``prefix``."""
    idx = content.find(prefix)
    if idx < 0:
        return None
    start = idx + len(prefix)
    try:
        obj, end = json.JSONDecoder().raw_decode(content[start:])
    except json.JSONDecodeError:
        return None
    return obj, start, start + end


def splice_embedded(content: str, prefix: str, new_json: str) -> str:
    """Re-serialise the JSON after ``prefix`` with ``new_json`` text."""
    found = _decode_embedded(content, prefix)
    if found is None:
        raise ValueError(f"{prefix} block not found")
    _, start, end = found
    return content[:start] + new_json + content[end:]


def build_contract_lookup():
    """Resolve (role_id, contract_id) -> current static ROLE_CONTRACT dict.

    Imports the frozen registry lazily so pure functions stay testable without
    the backend dependency tree loaded.
    """
    from app.roles.registry import builtin_registry

    snapshot = builtin_registry.freeze()

    def render_static(role_id: str, contract_id: str) -> dict | None:
        spec = snapshot.specs.get(role_id)
        if spec is None:
            return None
        contract = next(
            (c for c in spec.contracts if c.contract_id == contract_id), None
        )
        if contract is None:
            contract = next(
                (
                    c
                    for c in spec.granted_contracts
                    if c.contract_id == contract_id
                ),
                None,
            )
        if contract is None:
            return None
        return {
            "role_id": spec.role_id,
            "display_name": spec.display_name,
            "camp_id": spec.camp_id,
            "instructions": spec.instructions,
            "contract_id": contract.contract_id,
            "contract_version": contract.schema_version,
            "schedule_point": contract.schedule_point.value,
            "fallback_action_type": contract.fallback_action_type,
            "action_types": list(contract.action_types),
            "actions_requiring_target": sorted(
                contract.actions_requiring_target
            ),
            "multi_target_actions": dict(
                sorted(contract.multi_target_actions.items())
            ),
        }

    return render_static


_NEW_CONTRACT_KEYS = (
    "action_types",
    "actions_requiring_target",
    "multi_target_actions",
)


@dataclass
class UpgradeResult:
    content: str
    upgraded: bool = False
    conflict: bool = False
    conflict_detail: str = ""


def upgrade_prompt_text(
    content: str, render_static, *, inject_degraded: bool = True
) -> UpgradeResult:
    """Bring a pre-2026-10-26 prompt up to the current renderer output.

    ``render_static(role_id, contract_id) -> dict | None`` abstracts the
    registry so tests can inject a fake contract source.
    """
    result = UpgradeResult(content=content)

    found = _decode_embedded(content, _ROLE_CONTRACT_PREFIX)
    if found is None:
        contract = None
    else:
        contract, _, _ = found
    if contract is not None and not all(k in contract for k in _NEW_CONTRACT_KEYS):
        current = render_static(contract["role_id"], contract["contract_id"])
        if current is None:
            result.conflict = True
            result.conflict_detail = "contract_not_in_registry"
            return result
        drift = [
            key
            for key, value in contract.items()
            if key not in _NEW_CONTRACT_KEYS and current.get(key) != value
        ]
        if drift:
            result.conflict = True
            result.conflict_detail = "static_drift:" + ",".join(sorted(drift))
            return result
        content = splice_embedded(
            content, _ROLE_CONTRACT_PREFIX, canonical_json(current)
        )
        result.upgraded = True

    if inject_degraded:
        found = _decode_embedded(content, _PROJECTED_PREFIX)
        if found is not None:
            projected, _, _ = found
            if "degraded" not in projected:
                projected["degraded"] = False
                content = splice_embedded(
                    content, _PROJECTED_PREFIX, canonical_json(projected)
                )
                result.upgraded = True

    result.content = content
    return result


def unwrap_result(raw_json: str) -> tuple[dict | None, dict]:
    """Split a ``normalized_result_json`` into (payload, call_meta)."""
    try:
        row = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return None, {}
    if not isinstance(row, dict):
        return None, {}
    call = row.get("call")
    payload = row.get("payload")
    if isinstance(payload, dict):
        return payload, call if isinstance(call, dict) else {}
    return row, call if isinstance(call, dict) else {}


def build_completion(call_kind: str, payload: dict) -> str | None:
    """Serialise the accepted result into the student-facing target."""
    if call_kind == "speech":
        text = payload.get("text")
        if payload.get("is_none") or not isinstance(text, str) or not text.strip():
            return None
        return text
    if call_kind == "action":
        inner = payload.get("command")
        if isinstance(inner, dict):
            payload = inner
    cleaned = {
        key: value
        for key, value in payload.items()
        if key not in _LEDGER_META_KEYS
    }
    if not cleaned:
        return None
    return canonical_json(cleaned)


def is_repair_context(messages: list[dict]) -> bool:
    if len(messages) > 2:
        return True
    return any(
        isinstance(m.get("content"), str) and _REPAIR_HINT in m["content"]
        for m in messages
    )


@dataclass
class Counters:
    total: int = 0
    kept: int = 0
    excluded: Counter = field(default_factory=Counter)
    upgraded: int = 0
    upgrade_conflicts: int = 0
    repair_context: int = 0
    by_kind: Counter = field(default_factory=Counter)
    by_contract: Counter = field(default_factory=Counter)
    by_teacher: Counter = field(default_factory=Counter)
    size_buckets: Counter = field(default_factory=Counter)


def _size_bucket(chars: int) -> str:
    if chars < 4000:
        return "<4k"
    if chars < 8000:
        return "4k-8k"
    if chars < 16000:
        return "8k-16k"
    if chars < 32000:
        return "16k-32k"
    return ">=32k"


def process_record(
    record: dict,
    result_row: dict | None,
    *,
    render_static=None,
    upgrade: bool = True,
    include_weak: bool = False,
) -> tuple[dict | None, str | None]:
    """Return (sample, None) to keep or (None, reason) to exclude."""
    data = record["data"]
    kind = data["call_kind"]
    teacher = data.get("model_id")

    if not include_weak and teacher in WEAK_TEACHERS:
        return None, "weak_teacher"
    if result_row is None:
        return None, "no_result"

    payload, call_meta = result_row["payload"], result_row["call"]
    if call_meta.get("status") == "degraded":
        return None, "degraded"
    if payload.get("technical_failure_code"):
        return None, "engine_fallback"

    completion = build_completion(kind, payload)
    if completion is None:
        return None, "empty_completion"

    messages = [dict(m) for m in data["messages"]]
    upgraded = False
    if upgrade and messages:
        outcome = upgrade_prompt_text(messages[-1]["content"], render_static)
        if outcome.conflict:
            return None, "upgrade_conflict:" + outcome.conflict_detail
        if outcome.upgraded:
            messages[-1]["content"] = outcome.content
            upgraded = True

    messages.append({"role": "assistant", "content": completion})
    sample = {
        "messages": messages,
        "meta": {
            "request_id": data["request_id"],
            "call_kind": kind,
            "contract_id": data.get("contract_id"),
            "seat": record.get("seat"),
            "round": record.get("round"),
            "phase": record.get("phase"),
            "teacher": teacher,
            "prompt_chars": data.get("prompt_chars", 0),
            "repair_context": is_repair_context(messages[:-1]),
            "upgraded": upgraded,
            "timestamp": record.get("timestamp"),
        },
    }
    return sample, None


def load_results(db_path: str) -> dict[tuple[str, str], dict]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT game_id, request_id, normalized_result_json "
            "FROM model_requests "
            "WHERE status IN ('consumed','resolved')"
        ).fetchall()
    finally:
        db.close()
    results = {}
    for game_id, request_id, raw in rows:
        payload, call = unwrap_result(raw)
        if payload is not None:
            results[(game_id, request_id)] = {"payload": payload, "call": call}
    return results


def iter_prompt_records(data_dir: str):
    pattern = os.path.join(data_dir, "games", "*", "llm_prompts.jsonl")
    for path in sorted(glob.glob(pattern)):
        game_id = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("operation") != "llm_prompt":
                    continue
                yield game_id, record


def split_games(
    samples: list[tuple[str, dict]], eval_fraction: float, seed: int
) -> tuple[list[dict], list[dict]]:
    """Split by game_id so no game leaks into both sets."""
    game_ids = sorted({game_id for game_id, _ in samples})
    rng = random.Random(seed)
    rng.shuffle(game_ids)
    eval_games: set[str] = set()
    target = len(samples) * eval_fraction
    count = 0
    for game_id in game_ids:
        if count >= target:
            break
        eval_games.add(game_id)
        count += sum(1 for gid, _ in samples if gid == game_id)
    train = [s for gid, s in samples if gid not in eval_games]
    eval_ = [s for gid, s in samples if gid in eval_games]
    return train, eval_


def _write_jsonl(path: Path, rows: list[dict]) -> int:
    size = 0
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            payload = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            size += len(payload)
            handle.buffer.write(payload)
    return size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "backend" / "data"))
    parser.add_argument(
        "--db",
        default=None,
        help="sqlite path (default: <data-dir>/wolfkiller.sqlite3)",
    )
    parser.add_argument(
        "--out", default=str(Path(__file__).resolve().parent / "data")
    )
    parser.add_argument("--eval-fraction", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--include-weak-teachers", action="store_true")
    parser.add_argument("--no-upgrade", action="store_true")
    args = parser.parse_args(argv)

    db_path = args.db or os.path.join(args.data_dir, "wolfkiller.sqlite3")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    render_static = None
    if not args.no_upgrade:
        render_static = build_contract_lookup()

    results = load_results(db_path)
    counters = Counters()
    kept: list[tuple[str, dict]] = []
    excluded: list[dict] = []

    for game_id, record in iter_prompt_records(args.data_dir):
        counters.total += 1
        data = record["data"]
        row = results.get((game_id, data["request_id"]))
        sample, reason = process_record(
            record,
            row,
            render_static=render_static,
            upgrade=not args.no_upgrade,
            include_weak=args.include_weak_teachers,
        )
        if sample is None:
            base = reason.split(":")[0] if reason else "unknown"
            counters.excluded[base] += 1
            excluded.append(
                {
                    "game_id": game_id,
                    "request_id": data["request_id"],
                    "call_kind": data["call_kind"],
                    "teacher": data.get("model_id"),
                    "reason": reason,
                }
            )
            continue
        meta = sample["meta"]
        counters.kept += 1
        if meta["upgraded"]:
            counters.upgraded += 1
        if meta["repair_context"]:
            counters.repair_context += 1
        counters.by_kind[meta["call_kind"]] += 1
        counters.by_contract[meta["contract_id"] or "(none)"] += 1
        counters.by_teacher[meta["teacher"] or "(unknown)"] += 1
        counters.size_buckets[_size_bucket(meta["prompt_chars"])] += 1
        kept.append((game_id, sample))

    train, eval_ = split_games(kept, args.eval_fraction, args.seed)

    sizes = {
        "train.jsonl": _write_jsonl(out_dir / "train.jsonl", train),
        "eval.jsonl": _write_jsonl(out_dir / "eval.jsonl", eval_),
        "excluded.jsonl": _write_jsonl(out_dir / "excluded.jsonl", excluded),
    }
    report = {
        "total_records": counters.total,
        "kept": counters.kept,
        "excluded": dict(counters.excluded),
        "upgraded": counters.upgraded,
        "repair_context": counters.repair_context,
        "by_kind": dict(counters.by_kind),
        "by_contract": dict(counters.by_contract.most_common()),
        "by_teacher": dict(counters.by_teacher),
        "size_buckets_chars": dict(counters.size_buckets),
        "train_samples": len(train),
        "eval_samples": len(eval_),
        "file_bytes": sizes,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess test
    raise SystemExit(main())
