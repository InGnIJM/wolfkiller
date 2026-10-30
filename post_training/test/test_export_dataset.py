"""Unit tests for export_dataset pure functions and the end-to-end main."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import export_dataset as ex


# ---------- helpers ----------

STATIC_FULL = {
    "role_id": "wolf-killer-seer",
    "display_name": "Seer",
    "camp_id": "good",
    "instructions": "check one player each night",
    "contract_id": "seer_check",
    "contract_version": 1,
    "schedule_point": "night_seer_action",
    "fallback_action_type": "pass",
    "action_types": ["check", "pass"],
    "actions_requiring_target": ["check"],
    "multi_target_actions": {},
}

STATIC_OLD = {k: v for k, v in STATIC_FULL.items() if k not in ex._NEW_CONTRACT_KEYS}


def _prompt(contract: dict, projected: dict | None = None) -> str:
    projected = projected if projected is not None else {"actor_seat": 1}
    return (
        "ROLE_CONTRACT=" + ex.canonical_json(contract) + "\n"
        "PROJECTED_CONTEXT=" + ex.canonical_json(projected) + "\n"
        "OUTPUT_ACTION_COMMAND_SCHEMA={}\n"
    )


def _record(
    *,
    kind="pipeline",
    contract_id="seer_check",
    teacher="gemini-3.8-flash-high",
    messages=None,
    request_id="req-1",
):
    if messages is None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _prompt(STATIC_OLD)},
        ]
    return {
        "timestamp": "2026-10-05T00:00:00+00:00",
        "round": 1,
        "phase": "night",
        "seat": 3,
        "operation": "llm_prompt",
        "data": {
            "call_kind": kind,
            "request_id": request_id,
            "contract_id": contract_id,
            "model_id": teacher,
            "prompt_chars": 100,
            "messages": messages,
        },
    }


def _result(payload, *, envelope=True, status="succeeded"):
    if envelope:
        return {"payload": payload, "call": {"status": status}}
    return {"payload": payload, "call": {}}


# ---------- canonical_json / embedded decode ----------

def test_canonical_json_sorts_and_compacts():
    assert ex.canonical_json({"b": 1, "a": [2]}) == '{"a":[2],"b":1}'


def test_decode_and_splice_round_trip():
    content = _prompt(STATIC_OLD)
    found = ex._decode_embedded(content, ex._ROLE_CONTRACT_PREFIX)
    assert found is not None
    obj, start, end = found
    assert obj == STATIC_OLD
    out = ex.splice_embedded(content, ex._ROLE_CONTRACT_PREFIX, ex.canonical_json(STATIC_FULL))
    assert "ROLE_CONTRACT=" + ex.canonical_json(STATIC_FULL) in out
    assert ex._decode_embedded(content, "MISSING=") is None


def test_decode_bad_json_returns_none():
    assert ex._decode_embedded("x ROLE_CONTRACT={oops", ex._ROLE_CONTRACT_PREFIX) is None


# ---------- upgrade ----------

def _fake_static(role_id, contract_id):
    if contract_id == "seer_check":
        return dict(STATIC_FULL)
    return None


def test_upgrade_splices_new_keys():
    content = _prompt(STATIC_OLD, {"actor_seat": 1, "degraded": True})
    outcome = ex.upgrade_prompt_text(content, _fake_static)
    assert outcome.upgraded and not outcome.conflict
    new_contract = ex._decode_embedded(outcome.content, ex._ROLE_CONTRACT_PREFIX)[0]
    assert new_contract == STATIC_FULL
    # degraded already present -> left untouched
    proj = ex._decode_embedded(outcome.content, ex._PROJECTED_PREFIX)[0]
    assert proj["degraded"] is True


def test_upgrade_injects_degraded_false():
    outcome = ex.upgrade_prompt_text(_prompt(STATIC_FULL), _fake_static)
    proj = ex._decode_embedded(outcome.content, ex._PROJECTED_PREFIX)[0]
    assert proj["degraded"] is False
    assert outcome.upgraded is True


def test_upgrade_conflict_on_drift():
    drifted = dict(STATIC_OLD, instructions="OLD wording")
    outcome = ex.upgrade_prompt_text(_prompt(drifted), _fake_static)
    assert outcome.conflict
    assert "instructions" in outcome.conflict_detail


def test_upgrade_conflict_unknown_contract():
    bad = dict(STATIC_OLD, contract_id="ghost")
    outcome = ex.upgrade_prompt_text(_prompt(bad), _fake_static)
    assert outcome.conflict and outcome.conflict_detail == "contract_not_in_registry"


def test_upgrade_noop_on_new_shape():
    content = _prompt(STATIC_FULL, {"degraded": False})
    outcome = ex.upgrade_prompt_text(content, _fake_static)
    assert not outcome.upgraded and not outcome.conflict


def test_upgrade_skips_non_contract_prompt():
    outcome = ex.upgrade_prompt_text("plain text prompt", _fake_static)
    assert not outcome.upgraded and not outcome.conflict


# ---------- unwrap / completion ----------

def test_unwrap_result_envelope_and_bare():
    payload, call = ex.unwrap_result(
        '{"payload":{"a":1},"call":{"status":"degraded"}}'
    )
    assert payload == {"a": 1} and call["status"] == "degraded"
    payload, call = ex.unwrap_result('{"a":1}')
    assert payload == {"a": 1} and call == {}
    assert ex.unwrap_result("not json") == (None, {})
    assert ex.unwrap_result("[1,2]") == (None, {})


def test_build_completion_speech():
    assert ex.build_completion("speech", {"text": "hello", "is_none": False}) == "hello"
    assert ex.build_completion("speech", {"text": "", "is_none": False}) is None
    assert ex.build_completion("speech", {"text": "x", "is_none": True}) is None


def test_build_completion_action_unwraps_command():
    payload = {
        "command": {"action_type": "vote", "target_seat": 8, "reasoning": "r"},
        "technical_failure_code": None,
        "timeout_type": None,
    }
    out = json.loads(ex.build_completion("action", payload))
    assert out == {"action_type": "vote", "target_seat": 8, "reasoning": "r"}


def test_build_completion_drops_ledger_meta():
    payload = {"action_type": "check", "target_seat": 5, "reasoning": "r",
               "schema_version": 1, "_": None, "degraded": False}
    out = json.loads(ex.build_completion("night", payload))
    # schema_version is a required emitted field -> kept; junk keys dropped
    assert "_" not in out and "degraded" not in out
    assert out["schema_version"] == 1 and out["action_type"] == "check"


def test_build_completion_pipeline_keeps_schema_version():
    payload = {"action_type": "check", "target_seat": 5, "reasoning": "r",
               "schema_version": 1}
    out = json.loads(ex.build_completion("pipeline", payload))
    assert out["schema_version"] == 1


def test_build_completion_empty_returns_none():
    assert ex.build_completion("night", {"_": None, "degraded": False}) is None


def test_is_repair_context():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert not ex.is_repair_context(msgs)
    assert ex.is_repair_context(msgs + [{"role": "user", "content": "again"}])
    hint = [{"role": "user", "content": "The previous action was invalid."}]
    assert ex.is_repair_context(hint)


# ---------- process_record ----------

def _row(payload, status="succeeded"):
    return {"payload": payload, "call": {"status": status}}


ACTION_PAYLOAD = {"action_type": "check", "target_seat": 5, "reasoning": "r",
                  "schema_version": 1}


def test_process_record_happy_path_upgrades():
    rec = _record()
    sample, reason = ex.process_record(
        rec, _row(dict(ACTION_PAYLOAD)), render_static=_fake_static
    )
    assert reason is None and sample is not None
    assert sample["meta"]["upgraded"] is True
    assert sample["meta"]["teacher"] == "gemini-3.8-flash-high"
    assert sample["messages"][-1] == {
        "role": "assistant",
        "content": ex.canonical_json(ACTION_PAYLOAD),
    }
    contract = ex._decode_embedded(
        sample["messages"][-2]["content"], ex._ROLE_CONTRACT_PREFIX
    )[0]
    assert "action_types" in contract


def test_process_record_filters():
    rec = _record()
    assert ex.process_record(rec, None, render_static=_fake_static)[1] == "no_result"
    assert ex.process_record(
        rec, _row({}, "degraded"), render_static=_fake_static
    )[1] == "degraded"
    assert ex.process_record(
        rec,
        _row({"command": {}, "technical_failure_code": "request_timeout"}),
        render_static=_fake_static,
    )[1] == "engine_fallback"
    weak = _record(teacher="glm-5.3-flash")
    assert ex.process_record(weak, _row(dict(ACTION_PAYLOAD)))[1] == "weak_teacher"
    sample, _ = ex.process_record(
        weak, _row(dict(ACTION_PAYLOAD)),
        render_static=_fake_static, include_weak=True,
    )
    assert sample is not None


def test_process_record_upgrade_conflict_excluded():
    drifted = dict(STATIC_OLD, instructions="OLD wording")
    rec = _record(messages=[
        {"role": "user", "content": _prompt(drifted)},
    ])
    sample, reason = ex.process_record(
        rec, _row(dict(ACTION_PAYLOAD)), render_static=_fake_static
    )
    assert sample is None and reason.startswith("upgrade_conflict")


def test_process_record_speech():
    rec = _record(kind="speech", contract_id="day_speech", messages=[
        {"role": "system", "content": "s"}, {"role": "user", "content": "u"},
    ])
    sample, _ = ex.process_record(
        rec, _row({"text": "我发言", "is_none": False}), render_static=_fake_static
    )
    assert sample["messages"][-1]["content"] == "我发言"


def test_process_record_no_upgrade_flag():
    rec = _record()
    sample, _ = ex.process_record(
        rec, _row(dict(ACTION_PAYLOAD)),
        render_static=_fake_static, upgrade=False,
    )
    assert sample["meta"]["upgraded"] is False


# ---------- split ----------

def test_split_games_no_leakage_and_deterministic():
    samples = [(f"g{i}", {"meta": {"i": i}}) for i in range(20) for _ in range(5)]
    t1, e1 = ex.split_games(samples, 0.2, 7)
    t2, e2 = ex.split_games(samples, 0.2, 7)
    assert [s["meta"]["i"] for s in t1] == [s["meta"]["i"] for s in t2]
    eval_meta = {s["meta"]["i"] for s in e1}
    assert eval_meta and eval_meta.isdisjoint({s["meta"]["i"] for s in t1})


# ---------- end-to-end ----------

def _write_game(tmp: Path, game_id: str, records: list[dict]):
    gdir = tmp / "games" / game_id
    gdir.mkdir(parents=True)
    with open(gdir / "llm_prompts.jsonl", "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _write_db(tmp: Path, rows: list[tuple[str, str, str]]):
    db = sqlite3.connect(tmp / "wolfkiller.sqlite3")
    db.execute(
        "CREATE TABLE model_requests (game_id TEXT, request_id TEXT, "
        "status TEXT, normalized_result_json TEXT)"
    )
    db.executemany(
        "INSERT INTO model_requests VALUES (?,?,?,?)",
        [(g, r, "consumed", res) for g, r, res in rows],
    )
    db.commit()
    db.close()


def test_main_end_to_end(tmp_path):
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    records = [
        _record(request_id="r1"),
        _record(request_id="r2", teacher="glm-5.3-flash"),
        _record(request_id="r3", kind="speech", contract_id="day_speech",
                messages=[{"role": "user", "content": "speak prompt"}]),
    ]
    _write_game(data_dir, "game-a", records)
    _write_game(data_dir, "game-b", [_record(request_id="r4")])
    _write_db(data_dir, [
        ("game-a", "r1", json.dumps(_result(dict(ACTION_PAYLOAD)))),
        ("game-a", "r2", json.dumps(_result(dict(ACTION_PAYLOAD)))),
        ("game-a", "r3", json.dumps(_result({"text": "ok", "is_none": False}))),
        ("game-b", "r4", json.dumps(
            {"command": {"action_type": "vote", "target_seat": 1,
                          "reasoning": "r"},
             "technical_failure_code": "request_timeout"})),
    ])
    rc = ex.main([
        "--data-dir", str(data_dir), "--out", str(out_dir),
        "--eval-fraction", "0.5", "--seed", "1", "--no-upgrade",
    ])
    assert rc == 0
    train = (out_dir / "train.jsonl").read_text(encoding="utf-8").strip()
    excluded = (out_dir / "excluded.jsonl").read_text(encoding="utf-8").strip()
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["total_records"] == 4
    assert report["kept"] == 2  # weak teacher + engine fallback excluded
    reasons = {json.loads(line)["reason"].split(":")[0] for line in excluded.splitlines()}
    assert reasons == {"weak_teacher", "engine_fallback"}
    n_train = len([l for l in train.splitlines() if l.strip()])
    n_eval = len((out_dir / "eval.jsonl").read_text(encoding="utf-8").strip().splitlines())
    assert n_train + n_eval == 2


def test_main_no_records(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "games").mkdir(parents=True)
    _write_db(data_dir, [])
    rc = ex.main(["--data-dir", str(data_dir), "--out", str(tmp_path / "out")])
    assert rc == 0


# ---------- remaining edge coverage ----------

def test_splice_embedded_missing_prefix_raises():
    with pytest.raises(ValueError):
        ex.splice_embedded("nothing here", ex._ROLE_CONTRACT_PREFIX, "{}")


def test_size_bucket_edges():
    assert ex._size_bucket(0) == "<4k"
    assert ex._size_bucket(5000) == "4k-8k"
    assert ex._size_bucket(10000) == "8k-16k"
    assert ex._size_bucket(20000) == "16k-32k"
    assert ex._size_bucket(40000) == ">=32k"


def test_process_record_empty_completion_excluded():
    rec = _record(kind="night", contract_id="werewolf_kill", messages=[
        {"role": "user", "content": "night prompt"},
    ])
    sample, reason = ex.process_record(
        rec, _row({"_": None, "degraded": False}), render_static=_fake_static
    )
    assert sample is None and reason == "empty_completion"


def test_iter_prompt_records_skips_junk(tmp_path):
    gdir = tmp_path / "games" / "g1"
    gdir.mkdir(parents=True)
    good = {"operation": "llm_prompt", "data": {"request_id": "x"}}
    with open(gdir / "llm_prompts.jsonl", "w", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("not-json\n")
        fh.write(json.dumps({"operation": "other"}) + "\n")
        fh.write(json.dumps(good) + "\n")
    rows = list(ex.iter_prompt_records(str(tmp_path)))
    assert rows == [("g1", good)]


def test_main_db_override(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "games").mkdir(parents=True)
    other_db = tmp_path / "other.sqlite3"
    db = sqlite3.connect(other_db)
    db.execute(
        "CREATE TABLE model_requests (game_id TEXT, request_id TEXT, "
        "status TEXT, normalized_result_json TEXT)"
    )
    db.commit()
    db.close()
    rc = ex.main([
        "--data-dir", str(data_dir), "--db", str(other_db),
        "--out", str(tmp_path / "out"),
    ])
    assert rc == 0


def test_main_with_upgrade_and_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "build_contract_lookup", lambda: _fake_static)
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    repair_msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "user", "content": "The previous action was invalid."},
    ]
    _write_game(data_dir, "game-a", [
        _record(request_id="r1"),
        _record(request_id="r2", kind="speech", contract_id="day_speech",
                messages=repair_msgs),
    ])
    _write_db(data_dir, [
        ("game-a", "r1", json.dumps(_result(dict(ACTION_PAYLOAD)))),
        ("game-a", "r2", json.dumps(_result({"text": "ok", "is_none": False}))),
    ])
    rc = ex.main([
        "--data-dir", str(data_dir), "--out", str(out_dir),
        "--eval-fraction", "0.0",
    ])
    assert rc == 0
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["kept"] == 2
    assert report["upgraded"] == 1
    assert report["repair_context"] == 1


def test_module_main_guard(tmp_path):
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(Path(ex.__file__)), "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "data-dir" in proc.stdout


def test_build_contract_lookup_real_registry():
    render = ex.build_contract_lookup()
    static = render("wolf-killer-seer", "seer_check")
    assert static is not None
    assert static["contract_id"] == "seer_check"
    assert "action_types" in static
    assert render("ghost-role", "seer_check") is None
    assert render("wolf-killer-seer", "ghost_contract") is None
