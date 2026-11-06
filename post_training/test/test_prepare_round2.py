import json
import sys
import types

import pytest

import prepare_round2


def rec(kind="speech", teacher="gemini-3.8-flash-high", text="x" * 50, repair=False):
    return {
        "messages": [
            {"role": "user", "content": "prompt"},
            {"role": "assistant", "content": text},
        ],
        "meta": {"call_kind": kind, "teacher": teacher, "repair_context": repair},
    }


class FakeTokenizer:
    """apply_chat_template returns token ids ≈ 1 token per 4 chars."""

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False):
        total = sum(len(str(m.get("content") or "")) for m in messages)
        return [0] * (total // 4)


def build(records, **kw):
    args = dict(
        tokenizer=FakeTokenizer(), kinds={"speech"}, excluded_teachers=set(),
        max_tokens=8192, min_chars=30, prefix_chars=12, prefix_cap=2,
        drop_leaks=True,
    )
    args.update(kw)
    return prepare_round2.build_subset(records, **args)


def test_normalize_and_opening_prefix():
    assert prepare_round2.normalize("  我 是\n预言家 ") == "我是预言家"
    assert prepare_round2.opening_prefix("  我 是\n预言家，昨晚", 4) == "我是预言"


def test_completion_text_edges():
    assert prepare_round2.completion_text(rec()) == "x" * 50
    assert prepare_round2.completion_text({}) == ""
    assert prepare_round2.completion_text({"messages": [{"role": "user", "content": "u"}]}) == ""


def test_kind_and_teacher_filters():
    rows = [rec(), rec(kind="action"), rec(teacher="deepseek-v4.1-flash")]
    kept, stats = build(rows, excluded_teachers={"deepseek-v4.1-flash"})
    assert [stats["kept"], stats["wrong_kind"], stats["excluded_teacher"]] == [1, 1, 1]
    assert stats["kind_dist"] == {"speech": 1}
    assert stats["teacher_dist"] == {"gemini-3.8-flash-high": 1}


def test_repair_empty_short_and_long():
    rows = [
        rec(),
        rec(repair=True),
        {**rec(), "messages": [{"role": "user", "content": "u"}]},  # empty
        rec(text="tiny"),
        {**rec(), "messages": [
            {"role": "user", "content": "p" * 40000},
            {"role": "assistant", "content": "y" * 50}]},
    ]
    kept, stats = build(rows)
    assert stats["kept"] == 1
    for key in ("repair_context", "empty_completion", "too_short", "too_long"):
        assert stats[key] == 1, key


def test_leak_drop_and_keep():
    leaked = rec(text="我底牌是深水狼，首日发言保持低调" + "x" * 50)
    clean = rec(text="我是预言家，昨晚查验3号是狼人" + "y" * 50)
    kept, stats = build([leaked, clean])
    assert stats["leaked"] == 1 and stats["kept"] == 1
    kept2, stats2 = build([leaked, clean], drop_leaks=False)
    assert stats2["leaked"] == 0 and stats2["kept"] == 2


def test_leak_hits_names():
    assert prepare_round2.leak_hits("我底牌是深水狼") == ["meta_hand", "wolf_self"]
    assert prepare_round2.leak_hits("正常发言") == []


def test_duplicate_and_prefix_cap():
    dup = rec(text="重复发言" + "x" * 50)
    rows = [rec(text="相同的开头发言示例" + "y" * 50 + str(i)) for i in range(4)] + [dup, dup]
    kept, stats = build(rows)
    assert stats["duplicate"] == 1
    assert stats["prefix_capped"] == 2  # cap=2: 4 same-opening rows -> 2 dropped
    assert stats["kept"] == 3
    assert stats["top_prefixes"]


def test_main_writes_output_and_report(tmp_path, monkeypatch):
    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps(rec(), ensure_ascii=False) + "\n" + json.dumps(rec(kind="night")) + "\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.jsonl"
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(
        AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda p: FakeTokenizer())))
    monkeypatch.setattr(sys, "argv", [
        "prepare_round2.py", "--in", str(src), "--out", str(dst),
        "--model", "m", "--seed", "1",
    ])
    assert prepare_round2.main() == 0
    lines = dst.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    report = json.loads((tmp_path / "out.report.json").read_text(encoding="utf-8"))
    assert report["kept"] == 1 and report["wrong_kind"] == 1
