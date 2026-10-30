"""Unit tests for tokenize_report pure functions and main()."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tokenize_report as tr


def _encode_words(text: str) -> int:
    """Deterministic fake tokenizer: one token per whitespace-separated word."""
    return len(text.split())


def _sample(content_user: str, *, kind="pipeline", target="out"):
    return {
        "messages": [
            {"role": "system", "content": "sys words"},
            {"role": "user", "content": content_user},
            {"role": "assistant", "content": target},
        ],
        "meta": {"call_kind": kind, "contract_id": "seer_check"},
    }


# ---------- wrap_chat ----------

def test_wrap_chat_template_shape():
    out = tr.wrap_chat([
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ])
    assert out == "<|im_start|>system\nS<|im_end|>\n<|im_start|>user\nU<|im_end|>\n"
    assert tr.wrap_chat([], add_generation_prompt=True) == "<|im_start|>assistant\n"


# ---------- history_span ----------

def test_history_span_finds_inner_block():
    content = "AAA<untrusted_action_history>XYZ</untrusted_action_history>ZZZ"
    span = tr.history_span(content)
    assert span is not None
    assert content[span[0]:span[1]] == "XYZ"


def test_history_span_missing_or_unclosed():
    assert tr.history_span("no history here") is None
    assert tr.history_span("<untrusted_action_history>unclosed") is None


# ---------- bucket / percentile ----------

def test_bucket_of():
    assert tr.bucket_of(100) == "<4k"
    assert tr.bucket_of(5000) == "<8k"
    assert tr.bucket_of(9000) == "<12k"
    assert tr.bucket_of(33000) == ">=32k"


def test_percentile():
    vals = list(range(1, 101))
    assert tr.percentile(vals, 0.5) == 50.5
    assert tr.percentile(vals, 1.0) == 100
    assert tr.percentile([], 0.9) == 0.0


# ---------- measure_sample ----------

def test_measure_sample_counts_and_history():
    user = "head " + "<untrusted_action_history>h1 h2 h3 h4</untrusted_action_history>"
    row = tr.measure_sample(_sample(user), _encode_words)
    # prompt_text = template(sys+user) + gen prompt; total adds target+im_end
    assert row["completion_tokens"] == 1
    assert row["history_tokens"] == 4
    assert row["total_tokens"] > row["prompt_tokens"]
    assert row["truncatable_overhead"] == row["total_tokens"] - 4


def test_measure_sample_no_history():
    row = tr.measure_sample(_sample("plain user text"), _encode_words)
    assert row["history_tokens"] == 0
    assert row["truncatable_overhead"] == row["total_tokens"]


# ---------- summarise ----------

def test_summarise_windows():
    rows = [
        {"total_tokens": t, "completion_tokens": 1,
         "history_tokens": h, "truncatable_overhead": t - h,
         "call_kind": "x", "contract_id": "c"}
        for t, h in [(1000, 0), (5000, 3000), (9000, 8000), (20000, 19000)]
    ]
    rep = tr.summarise(rows)
    assert rep["samples"] == 4
    assert rep["fit_by_window"]["4k"]["samples_fit"] == 1
    # With tail-trunc, the 9k/20k samples shrink to 1k overhead -> all but 5k-3k=2k fit 4k?
    # overheads: 1000, 2000, 1000, 1000 -> all <= 4096
    assert rep["fit_by_window"]["4k"]["samples_fit_tail_trunc"] == 4
    assert rep["total_tokens"]["max"] == 20000
    assert rep["history_tokens"]["with_history"] == 3


# ---------- load_encode ----------

class _FakeTok:
    def encode(self, text, add_special_tokens=False):
        return type("Enc", (), {"ids": text.split()})()


def test_load_encode_from_file(tmp_path, monkeypatch):
    tok_file = tmp_path / "tokenizer.json"
    tok_file.write_text("{}")
    import tokenizers
    monkeypatch.setattr(
        tokenizers.Tokenizer, "from_file",
        classmethod(lambda cls, p: _FakeTok()),
    )
    encode = tr.load_encode(str(tok_file))
    assert encode("a b c") == 3


def test_load_encode_from_hub(monkeypatch):
    import huggingface_hub
    import tokenizers
    monkeypatch.setattr(
        huggingface_hub, "hf_hub_download", lambda repo, f: "/fake/tok.json"
    )
    monkeypatch.setattr(
        tokenizers.Tokenizer, "from_file",
        classmethod(lambda cls, p: _FakeTok()),
    )
    encode = tr.load_encode("some/repo")
    assert encode("x") == 1


# ---------- main end-to-end ----------

def test_main_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "load_encode", lambda _src: _encode_words)
    data = tmp_path / "train.jsonl"
    rows = [_sample("u one two"), _sample("u a b c d e f", kind="night")]
    data.write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )
    out = tmp_path / "report.json"
    rc = tr.main(["--files", str(data), "--out", str(out)])
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["overall"]["samples"] == 2
    assert set(report["by_call_kind"]) == {"pipeline", "night"}
    assert "train.jsonl" in report["by_file"]
