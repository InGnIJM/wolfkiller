import json

import pytest

from subset_teacher import build_subset, iter_records, rendered_len


class FakeTokenizer:
    """apply_chat_template stand-in: one id per character of joined content."""

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False):
        assert tokenize is True
        assert add_generation_prompt is False
        return list(range(sum(len(m["content"]) for m in messages)))


def _rec(teacher="gemini-3.8-flash-high", prompt="p" * 10, completion="c" * 5):
    return {
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": completion},
        ],
        "meta": {"teacher": teacher},
    }


def test_iter_records_skips_blank_lines(tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text(json.dumps(_rec()) + "\n\n   \n" + json.dumps(_rec()) + "\n", encoding="utf-8")
    assert len(list(iter_records(str(src)))) == 2


def test_rendered_len_uses_chat_template():
    tok = FakeTokenizer()
    assert rendered_len(tok, _rec()["messages"]) == 15


def test_build_subset_filters_teacher_and_length():
    records = [
        _rec(),                                        # keep
        _rec(teacher="glm-5.3-flash"),                 # wrong teacher
        _rec(prompt="p" * 9000),                       # too long
        _rec(),                                        # keep
        {"messages": _rec()["messages"]},              # no meta -> wrong teacher
    ]
    kept, stats = build_subset(records, FakeTokenizer(), "gemini-3.8-flash-high", 8192)
    assert len(kept) == 2
    assert stats == {"seen": 5, "wrong_teacher": 2, "too_long": 1, "kept": 2}


def test_build_subset_boundary_exact_max_tokens():
    records = [_rec(prompt="p" * 8187, completion="c" * 5)]  # total 8192 == max
    kept, stats = build_subset(records, FakeTokenizer(), "gemini-3.8-flash-high", 8192)
    assert len(kept) == 1 and stats["too_long"] == 0


def test_main_writes_output(tmp_path, monkeypatch, capsys):
    src = tmp_path / "in.jsonl"
    dst = tmp_path / "out.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in [_rec(), _rec(teacher="x")]), encoding="utf-8")

    import sys
    import types

    import subset_teacher

    fake = types.SimpleNamespace(
        AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda _path: FakeTokenizer()))
    monkeypatch.setitem(sys.modules, "transformers", fake)
    monkeypatch.setattr(sys, "argv", ["subset_teacher.py", "--in", str(src), "--out", str(dst),
                                      "--teacher", "gemini-3.8-flash-high", "--model", "m"])
    assert subset_teacher.main() == 0
    lines = dst.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["meta"]["teacher"] == "gemini-3.8-flash-high"
    assert '"kept": 1' in capsys.readouterr().out
