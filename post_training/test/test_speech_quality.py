import json
import sys
import types

import pytest

import speech_quality


def test_extract_text_prediction_filters_non_speech():
    row = {"call_kind": "speech", "prediction": "我是预言家"}
    assert speech_quality.extract_text(row, "prediction") == "我是预言家"
    assert speech_quality.extract_text({"call_kind": "night", "prediction": "x"}, "prediction") is None


def test_extract_text_assistant():
    rec = {"meta": {"call_kind": "speech"},
           "messages": [{"role": "user", "content": "u"},
                        {"role": "assistant", "content": "发言"}]}
    assert speech_quality.extract_text(rec, "assistant") == "发言"
    assert speech_quality.extract_text({"meta": {"call_kind": "action"},
                                        "messages": [{"role": "assistant", "content": "{}"}]},
                                       "assistant") is None
    rec_no_last = {"meta": {"call_kind": "speech"},
                   "messages": [{"role": "user", "content": "u"}]}
    assert speech_quality.extract_text(rec_no_last, "assistant") is None


def test_extract_text_generic_field():
    assert speech_quality.extract_text({"note": "hi"}, "note") == "hi"
    assert speech_quality.extract_text({}, "note") is None


def test_score_texts_clean_and_empty():
    result = speech_quality.score_texts(["我是预言家，昨晚查验3号是狼人", None, ""])
    assert result["speeches"] == 1
    assert result["leaked"] == 0 and result["leak_rate"] == 0.0
    assert all(v == 0 for v in result["pattern_hits"].values())


def test_score_texts_leak_patterns():
    texts = [
        "我底牌是深水狼，首日发言保持低调",   # meta_hand + wolf_self
        "今晚我们计划刀9号",                  # night_plan
        "我的狼队友是3号",                    # teammate + wolf_self
        "从策略上来说我们应该归票",            # coaching
        "正常发言，没有泄露",                  # clean
    ]
    result = speech_quality.score_texts(texts)
    assert result["speeches"] == 5
    assert result["leaked"] == 4
    assert result["leak_rate"] == 0.8
    assert result["pattern_hits"]["meta_hand"] == 1
    assert result["pattern_hits"]["wolf_self"] == 1
    assert result["pattern_hits"]["night_plan"] == 1
    assert result["pattern_hits"]["teammate"] == 1
    assert result["pattern_hits"]["coaching"] == 1
    assert result["top_openings"][0][1] >= 1


def test_score_texts_empty_input():
    result = speech_quality.score_texts([])
    assert result["speeches"] == 0 and result["leak_rate"] == 0.0
    assert result["len_p50"] == 0 and result["len_max"] == 0


def test_render_markdown_sections():
    md = speech_quality.render_markdown(speech_quality.score_texts(["底牌是狼"]), "src")
    assert "leak rate: 100.0%" in md
    assert "| meta_hand | 1 |" in md
    assert "## top openings" in md


def test_main_writes_json_and_md(tmp_path, monkeypatch):
    rows = tmp_path / "rows.jsonl"
    rows.write_text(
        json.dumps({"call_kind": "speech", "prediction": "我底牌是深水狼"},
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["speech_quality.py", "--rows", str(rows)])
    assert speech_quality.main() == 0
    out = tmp_path / "rows.speech_quality.json"
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["leak_rate"] == 1.0
    md = tmp_path / "rows.speech_quality.md"
    assert md.exists() and "meta_hand" in md.read_text(encoding="utf-8")
