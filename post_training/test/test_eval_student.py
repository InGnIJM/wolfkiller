import collections
import json
import sys
import types

import pytest

import eval_student


# ---------------------------------------------------------------- fixtures


def _record(kind, gold, meta=None):
    return {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
            {"role": "assistant", "content": gold},
        ],
        "meta": {"call_kind": kind, "contract_id": kind, **(meta or {})},
    }


# ---------------------------------------------------------------- pure text


def test_iter_records_skips_blank_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"a": 1}\n\n   \n{"a": 2}\n', encoding="utf-8")
    assert list(eval_student.iter_records(str(path))) == [{"a": 1}, {"a": 2}]


def test_normalise_text_strips_all_whitespace():
    assert eval_student.normalise_text(" 你 好\n世界\t ") == "你好世界"
    assert eval_student.normalise_text(None) == ""


def test_parse_json_object_accepts_plain_fenced_and_embedded():
    assert eval_student.parse_json_object('{"a": 1}') == {"a": 1}
    assert eval_student.parse_json_object('```json\n{"a": 2}\n```') == {"a": 2}
    assert eval_student.parse_json_object('好的：{"action_type": "pass"} 以上') == {"action_type": "pass"}
    assert eval_student.parse_json_object("") is None
    assert eval_student.parse_json_object("not json") is None
    assert eval_student.parse_json_object("[1, 2]") is None  # list is not an object
    assert eval_student.parse_json_object('{"broken": ') is None


def test_classify_gold_distinguishes_json_and_text():
    assert eval_student.classify_gold('{"action_type": "vote"}')[0] == "json"
    assert eval_student.classify_gold("今天先听发言")[0] == "text"
    assert eval_student.classify_gold("") == ("text", "")


def test_char_ngram_f1_identical_disjoint_and_short():
    assert eval_student.char_ngram_f1("我建议刀九号", "我建议刀九号") == 1.0
    assert eval_student.char_ngram_f1("aaaa", "bbbb") == 0.0
    assert eval_student.char_ngram_f1("", "abc") == 0.0
    assert eval_student.char_ngram_f1("a", "a") == 1.0  # shorter than n
    assert 0.0 < eval_student.char_ngram_f1("我建议刀九号", "我建议刀十号") < 1.0


def test_as_targets_normalises_single_multi_and_invalid():
    assert eval_student._as_targets({"target_seat": 3}) == (3,)
    assert eval_student._as_targets({"target_seats": [7, 3]}) == (3, 7)
    # multi-target list wins over the scalar copy
    assert eval_student._as_targets({"target_seat": 3, "target_seats": [5, 4]}) == (4, 5)
    assert eval_student._as_targets({"target_seat": None}) is None
    assert eval_student._as_targets({}) is None
    assert eval_student._as_targets({"target_seats": "3"}) is None
    assert eval_student._as_targets({"target_seats": [3, "x"]}) is None


# ------------------------------------------------------------ comparisons


def test_compare_text_gold_scores_f1_and_json_flag():
    metrics = eval_student.compare_predictions("我是三号，先听警上", "我是三号，先听警上")
    assert metrics["gold_kind"] == "text"
    assert metrics["text_f1"] == 1.0
    assert metrics["pred_json"] is False
    assert metrics["length_ratio"] == 1.0


def test_compare_text_gold_counts_json_prediction():
    metrics = eval_student.compare_predictions("发言", '{"text": "发言"}')
    assert metrics["pred_json"] is True
    assert metrics["text_f1"] < 1.0


def test_compare_action_gold_scores_fields():
    gold = '{"action_type": "vote", "reasoning": "r", "target_seat": 12}'
    good = eval_student.compare_predictions(gold, '{"action_type": "vote", "target_seat": 12}')
    assert good["pred_json_ok"] is True
    assert good["action_type_ok"] is True
    assert good["target_ok"] is True
    assert good["exact_ok"] is True
    assert "text_f1" not in good

    wrong = eval_student.compare_predictions(gold, '{"action_type": "pass", "target_seat": 7}')
    assert wrong["action_type_ok"] is False
    assert wrong["target_ok"] is False
    assert wrong["exact_ok"] is False

    unparsable = eval_student.compare_predictions(gold, "我想投12号")
    assert unparsable["pred_json_ok"] is False
    assert "action_type_ok" not in unparsable


def test_compare_multi_target_and_special_fields():
    multi = eval_student.compare_predictions(
        '{"action_type": "check", "target_seats": [3, 7]}',
        '{"action_type": "check", "target_seats": [7, 3]}',
    )
    assert multi["target_ok"] is True

    rich = eval_student.compare_predictions(
        '{"day_plan": "分工", "preferred_target": 9, "speak": true, "text": "我建议刀9号"}',
        '{"day_plan": "分工", "preferred_target": 9, "speak": true, "text": "建议刀9号"}',
    )
    assert rich["speak_ok"] is True
    assert rich["preferred_target_ok"] is True
    assert rich["day_plan_f1"] == 1.0
    assert 0.0 < rich["text_f1"] < 1.0

    side = eval_student.compare_predictions(
        '{"action_type": "choose_side", "side": "left"}',
        '{"action_type": "choose_side", "side": "right"}',
    )
    assert side["side_ok"] is False


# --------------------------------------------------------------- summaries


def _row(loss, kind, metrics, contract=None):
    return {
        "loss": loss,
        "completion_tokens": 10,
        "call_kind": kind,
        "contract_id": contract or kind,
        "teacher": "gemini-3.8-flash-high",
        "metrics": metrics,
    }


def test_summarize_aggregates_overall_and_splits():
    rows = [
        _row(1.0, "action", eval_student.compare_predictions(
            '{"action_type": "vote", "target_seat": 3}', '{"action_type": "vote", "target_seat": 3}')),
        _row(3.0, "action", eval_student.compare_predictions(
            '{"action_type": "vote", "target_seat": 3}', '{"action_type": "pass", "target_seat": 1}')),
        _row(2.0, "speech", eval_student.compare_predictions("发言内容", "发言内容")),
    ]
    summary = eval_student.summarize(rows, [{"reason": "too_long:9000>8192"}])
    assert summary["overall"]["n"] == 3
    assert summary["overall"]["completion_nll"] == 2.0
    assert summary["overall"]["ppl"] == round(eval_student.math.exp(2.0), 3)
    assert summary["overall"]["n_skipped"] == 1
    assert summary["overall"]["skipped_reasons"] == {"too_long:9000>8192": 1}
    assert summary["call_kind"]["action"]["n"] == 2
    assert summary["call_kind"]["action"]["action_type"]["rate"] == 0.5
    assert summary["call_kind"]["speech"]["text_f1"] == 1.0
    assert summary["contract_id"]["action"]["n"] == 2
    assert summary["teacher"]["gemini-3.8-flash-high"]["n"] == 3


def test_summarize_handles_missing_loss_and_rates():
    rows = [_row(None, "speech", {"gold_kind": "text", "text_f1": 1.0})]
    summary = eval_student.summarize(rows, [])
    assert summary["overall"]["n"] == 1
    assert "completion_nll" not in summary["overall"]
    assert "ppl" not in summary["overall"]
    assert "json_parse" not in summary["overall"]


def test_format_markdown_contains_metrics_and_tolerates_missing():
    rows = [_row(1.5, "action", eval_student.compare_predictions(
        '{"action_type": "vote", "target_seat": 3}', '{"action_type": "vote", "target_seat": 3}'))]
    report = eval_student.summarize(rows, [])
    args = types.SimpleNamespace(adapter="ckpt/x")
    text = eval_student.format_markdown(report, args)
    assert "completion NLL" in text and "1.5" in text
    assert "100.0%" in text
    assert "exile_vote" in text or "action" in text

    empty = eval_student.summarize([], [])
    assert "—" in eval_student.format_markdown({"overall": empty["overall"], "contract_id": {}}, args)


def test_pct_handles_none():
    assert eval_student._pct(None) == "—"
    assert eval_student._pct({"n": 0, "rate": None}) == "—"
    assert eval_student._pct({"n": 2, "rate": 0.5}) == "50.0% (2)"


# ------------------------------------------------------------- model loop


class _FakeTokenizer:
    def __init__(self, lengths=None):
        self.lengths = lengths or {}

    def apply_chat_template(self, messages, tokenize, add_generation_prompt, return_dict=False):
        size = self.lengths.get(add_generation_prompt, 5)
        ids = list(range(size))
        return {"input_ids": ids} if not return_dict else ids


def test_build_chat_ids_unwraps_batch_encoding_and_list():
    tok = _FakeTokenizer({False: 7, True: 4})
    assert eval_student.build_chat_ids(tok, [], add_generation_prompt=False) == list(range(7))
    assert eval_student.build_chat_ids(tok, [], add_generation_prompt=True) == list(range(4))


def test_run_eval_both_modes_and_skip():
    records = [
        _record("action", '{"action_type": "vote", "target_seat": 3}'),
        _record("speech", "发言"),
    ]

    def loss_fn(messages, max_seq_len):
        return 1.25, 9, "ok"

    def gen_fn(messages, max_new_tokens):
        return ' {"action_type": "vote", "target_seat": 3} '

    rows, skipped = eval_student.run_eval(
        records, loss_fn, gen_fn, max_seq_len=8192, max_new_tokens=8, mode="both", progress_every=1
    )
    assert len(rows) == 2 and skipped == []
    assert rows[0]["loss"] == 1.25 and rows[0]["completion_tokens"] == 9
    assert rows[0]["metrics"]["exact_ok"] is True
    assert rows[1]["metrics"]["gold_kind"] == "text"


def test_run_eval_skips_too_long_rows():
    records = [_record("action", '{"a": 1}'), _record("action", '{"a": 2}')]
    calls = {"count": 0}

    def loss_fn(messages, max_seq_len):
        calls["count"] += 1
        if calls["count"] == 1:
            return None, 0, "too_long:9000>8192"
        return 0.5, 3, "ok"

    rows, skipped = eval_student.run_eval(
        records, loss_fn, lambda m, n: "", max_seq_len=8192, max_new_tokens=4, mode="loss", progress_every=0
    )
    assert len(rows) == 1 and len(skipped) == 1
    assert skipped[0]["reason"] == "too_long:9000>8192"
    assert rows[0]["metrics"] == {}  # loss-only mode reports no format metrics


def test_run_eval_gen_mode_skips_loss():
    records = [_record("speech", "发言")]

    def loss_fn(messages, max_seq_len):
        raise AssertionError("loss_fn must not be called in gen mode")

    rows, _ = eval_student.run_eval(
        records, loss_fn, lambda m, n: "发言", max_seq_len=8192, max_new_tokens=4, mode="gen"
    )
    assert rows[0]["loss"] is None
    assert rows[0]["metrics"]["text_f1"] == 1.0


# ----------------------------------------------------------------- driver


def test_evaluate_writes_report_and_markdown(tmp_path, monkeypatch):
    src = tmp_path / "eval.jsonl"
    records = [
        _record("action", '{"action_type": "vote", "target_seat": 3}'),
        _record("speech", "发言"),
    ]
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")

    monkeypatch.setattr(eval_student, "load_student", lambda *a, **k: ("MODEL", "TOKENIZER"))
    monkeypatch.setattr(eval_student, "make_loss_fn", lambda model, tokenizer: (lambda m, n: (1.0, 5, "ok")))
    monkeypatch.setattr(eval_student, "make_gen_fn", lambda model, tokenizer, n: (lambda m, _n: "发言"))

    out = tmp_path / "report.json"
    args = types.SimpleNamespace(
        data=str(src), base="b", adapter="a", out=str(out), limit=0, mode="both",
        max_seq_len=8192, max_new_tokens=8, progress_every=1, loader="unsloth", gen_sample=0,
        resume=False, max_gen_prompt=4500,
    )
    report = eval_student.evaluate(args)
    assert report["summary"]["overall"]["n"] == 2
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["overall"]["n"] == 2
    assert (tmp_path / "report.md").exists()


def test_main_parses_args_and_returns_zero(monkeypatch):
    seen = {}

    def fake_evaluate(args):
        seen["args"] = args
        return {"overall": {}}

    monkeypatch.setattr(eval_student, "evaluate", fake_evaluate)
    assert eval_student.main([]) == 0
    assert seen["args"].mode == "both" and seen["args"].max_seq_len == 8192
    assert seen["args"].adapter.endswith("lora_final")
    assert seen["args"].gen_sample == 0 and seen["args"].loader == "unsloth"
    assert seen["args"].resume is False
    assert seen["args"].max_gen_prompt == 4500


def test_select_generation_subset_stratifies_and_is_deterministic():
    records = [{"meta": {"call_kind": kind}} for kind in ["action"] * 40 + ["night"] * 40 + ["speech"] * 20]
    indices = eval_student.select_generation_subset(records, 20)
    assert len(indices) == 20
    counts = collections.Counter(records[i]["meta"]["call_kind"] for i in indices)
    assert counts == {"action": 8, "night": 8, "speech": 4}
    assert eval_student.select_generation_subset(records, 20) == indices
    assert eval_student.select_generation_subset(records, 0) is None
    assert eval_student.select_generation_subset(records, 100) is None


def test_select_generation_subset_handles_unknown_kind_and_tiny_limit():
    records = [{"meta": None}, {"meta": {"call_kind": "action"}}, {"meta": {"call_kind": "action"}}, {}]
    indices = eval_student.select_generation_subset(records, 2)
    assert len(indices) == 2  # never exceeds the requested limit


def test_run_eval_restricts_generation_to_indices():
    records = [_record("action", '{"a": 1}'), _record("speech", "发言")]
    calls = {"gen": 0}

    def gen_fn(messages, n):
        calls["gen"] += 1
        return "发言"

    rows, _ = eval_student.run_eval(
        records, lambda m, s: (1.0, 3, "ok"), gen_fn,
        max_seq_len=8192, max_new_tokens=4, mode="both", gen_indices={1},
    )
    assert calls["gen"] == 1
    assert rows[0]["metrics"] == {} and rows[0]["loss"] == 1.0
    assert rows[1]["prediction"] == "发言"


def test_partial_rows_helpers(tmp_path):
    import json as _json
    out = str(tmp_path / "report.json")
    path = eval_student.partial_rows_path(out)
    assert path.endswith("report.rows.jsonl")
    assert eval_student.load_partial_rows(path) == {}
    from pathlib import Path
    Path(path).write_text(
        _json.dumps({"request_id": "a", "loss": 1.0}) + "\n\n" + _json.dumps({"request_id": "b"}) + "\n",
        encoding="utf-8",
    )
    done = eval_student.load_partial_rows(path)
    assert set(done) == {"a", "b"}


def test_run_eval_skips_finished_ids():
    records = [
        _record("action", '{"a": 1}', meta={"request_id": "r1"}),
        _record("action", '{"a": 2}', meta={"request_id": "r2"}),
    ]
    seen = []

    def loss_fn(messages, max_seq_len):
        seen.append(messages[-1]["content"])
        return 1.0, 3, "ok"

    rows, _ = eval_student.run_eval(
        records, loss_fn, lambda m, n: "", max_seq_len=8192, max_new_tokens=4,
        mode="loss", progress_every=0, skip_ids={"r1"},
    )
    assert len(rows) == 1 and seen == ['{"a": 2}']
    assert rows[0]["request_id"] == "r2"


def test_evaluate_resume_reuses_partial_rows(tmp_path, monkeypatch):
    src = tmp_path / "eval.jsonl"
    records = [
        _record("action", '{"action_type": "vote", "target_seat": 3}', meta={"request_id": "r1"}),
        _record("action", '{"action_type": "vote", "target_seat": 4}', meta={"request_id": "r2"}),
    ]
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")

    out = tmp_path / "report.json"
    partial = tmp_path / "report.rows.jsonl"
    partial.write_text(json.dumps({"request_id": "r1", "loss": 0.9, "metrics": {}, "call_kind": "action",
                                   "contract_id": "action", "teacher": "t"}) + "\n", encoding="utf-8")

    calls = {"loss": 0}

    def fake_loss(messages, max_seq_len):
        calls["loss"] += 1
        return 1.0, 5, "ok"

    monkeypatch.setattr(eval_student, "load_student", lambda *a, **k: ("M", "T"))
    monkeypatch.setattr(eval_student, "make_loss_fn", lambda model, tok: fake_loss)
    monkeypatch.setattr(eval_student, "make_gen_fn", lambda model, tok, n: (lambda m, _n: "x"))
    args = types.SimpleNamespace(
        data=str(src), base="b", adapter="a", out=str(out), limit=0, mode="loss",
        max_seq_len=8192, max_new_tokens=8, progress_every=1, loader="unsloth", gen_sample=0,
        resume=True, max_gen_prompt=4500,
    )
    report = eval_student.evaluate(args)
    assert calls["loss"] == 1  # only r2 recomputed
    assert report["summary"]["overall"]["n"] == 2
    lines = [line for line in partial.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2


def test_select_generation_subset_respects_eligibility():
    records = [{"meta": {"call_kind": "speech"}} for _ in range(10)]
    indices = eval_student.select_generation_subset(records, 4, eligible={2, 4, 6, 8, 9})
    assert indices is not None and len(indices) == 4
    assert indices <= {2, 4, 6, 8, 9}


def test_merge_rows_prefers_new_values_but_keeps_old_loss():
    done = {"r1": {"request_id": "r1", "loss": 1.2, "metrics": {}, "call_kind": "action"}}
    new = [
        {"request_id": "r1", "loss": None, "metrics": {"pred_json_ok": True}, "call_kind": "action"},
        {"request_id": "r2", "loss": 0.9, "metrics": {}, "call_kind": "speech"},
    ]
    rows = eval_student.merge_rows(done, new)
    by_id = {row["request_id"]: row for row in rows}
    assert by_id["r1"]["loss"] == 1.2          # not overwritten by None
    assert by_id["r1"]["metrics"] == {"pred_json_ok": True}
    assert by_id["r2"]["loss"] == 0.9
    assert len(rows) == 2


def test_evaluate_gen_sample_filters_by_prompt_length(tmp_path, monkeypatch):
    src = tmp_path / "eval.jsonl"
    records = [
        _record("speech", "发言", meta={"request_id": "r1"}),
        _record("speech", "发言", meta={"request_id": "r2"}),
    ]
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    lengths = iter([100, 9000])

    def fake_build(tokenizer, messages, add_generation_prompt):
        return [0] * next(lengths)

    monkeypatch.setattr(eval_student, "load_student", lambda *a, **k: ("M", "T"))
    monkeypatch.setattr(eval_student, "make_loss_fn", lambda model, tok: (lambda m, n: (1.0, 5, "ok")))
    monkeypatch.setattr(eval_student, "make_gen_fn", lambda model, tok, n: (lambda m, _n: "发言"))
    monkeypatch.setattr(eval_student, "build_chat_ids", fake_build)
    captured = {}
    original = eval_student.select_generation_subset

    def spy(records_arg, limit, eligible=None):
        captured["eligible"] = eligible
        return original(records_arg, limit, eligible)

    monkeypatch.setattr(eval_student, "select_generation_subset", spy)
    args = types.SimpleNamespace(
        data=str(src), base="b", adapter="a", out=str(tmp_path / "report.json"), limit=0, mode="both",
        max_seq_len=8192, max_new_tokens=8, progress_every=1, loader="unsloth", gen_sample=1,
        resume=False, max_gen_prompt=4500,
    )
    eval_student.evaluate(args)
    assert captured["eligible"] == {0}


def test_run_eval_assigns_row_index_and_skips_by_index():
    records = [
        _record("action", '{"a": 1}', meta={"request_id": "same"}),
        _record("action", '{"a": 2}', meta={"request_id": "same"}),
    ]
    rows, _ = eval_student.run_eval(
        records, lambda m, s: (1.0, 3, "ok"), lambda m, n: "", max_seq_len=8192,
        max_new_tokens=4, mode="loss", progress_every=0,
    )
    assert [row["row_index"] for row in rows] == [0, 1]

    rows, _ = eval_student.run_eval(
        records, lambda m, s: (1.0, 3, "ok"), lambda m, n: "", max_seq_len=8192,
        max_new_tokens=4, mode="loss", progress_every=0, skip_indices={0},
    )
    assert [row["row_index"] for row in rows] == [1]


def test_merge_rows_keeps_duplicate_request_ids_separate():
    done = {"x": {"row_index": 0, "request_id": "dup", "loss": 1.0, "metrics": {}}}
    new = [
        {"row_index": 1, "request_id": "dup", "loss": 2.0, "metrics": {}},
        {"row_index": 0, "request_id": "dup", "loss": None, "metrics": {"pred_json_ok": True}},
    ]
    rows = eval_student.merge_rows(done, new)
    assert len(rows) == 2
    by_index = {row["row_index"]: row for row in rows}
    assert by_index[0]["loss"] == 1.0
    assert by_index[0]["metrics"] == {"pred_json_ok": True}
    assert by_index[1]["loss"] == 2.0
