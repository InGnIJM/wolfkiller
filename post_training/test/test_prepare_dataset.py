"""Unit tests for prepare_dataset truncation logic and main()."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import prepare_dataset as pd
from tokenize_report import IM_END, IM_START


class _WordTokenizer:
    """Deterministic fake tokenizer: 1 token per word; decode joins words."""

    def encode(self, text, add_special_tokens=False):
        return type("Enc", (), {"ids": text.split()})()

    def decode(self, ids):
        return " ".join(ids)


def _sample(user: str, *, target="t"):
    return {
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": user},
            {"role": "assistant", "content": target},
        ],
        "meta": {"call_kind": "pipeline", "request_id": "r1"},
    }


# ---------- measurement ----------

def test_sample_total_tokens_counts_everything():
    tok = _WordTokenizer()
    msgs = _sample("u1 u2")["messages"]
    # prompt = system(s) + user(u1 u2) + gen prompt; closing = target + im_end
    n = pd.sample_total_tokens(tok, msgs)
    direct = pd.token_count(
        tok,
        f"{IM_START}system\ns{IM_END}\n{IM_START}user\nu1 u2{IM_END}\n"
        f"{IM_START}assistant\n" + "t" + IM_END + "\n",
    )
    assert n == direct


# ---------- truncate_history ----------

def test_truncate_keeps_history_tail():
    tok = _WordTokenizer()
    user = "HEAD <untrusted_action_history>h1 h2 h3 h4 h5</untrusted_action_history> TAIL"
    out = pd.truncate_history(tok, _sample(user)["messages"], 3)
    assert out is not None
    new_user = out[1]["content"]
    assert "h4 h5" in new_user and "h1 h2 h3" not in new_user
    assert new_user.startswith("HEAD") and new_user.endswith("TAIL")
    assert "<untrusted_action_history>" in new_user


def test_truncate_returns_none_without_block():
    tok = _WordTokenizer()
    assert pd.truncate_history(tok, _sample("no block")["messages"], 5) is None


def test_truncate_returns_none_when_excess_covers_block():
    tok = _WordTokenizer()
    user = "H <untrusted_action_history>a b</untrusted_action_history>"
    assert pd.truncate_history(tok, _sample(user)["messages"], 99) is None


def test_truncate_only_touches_user_role():
    tok = _WordTokenizer()
    block = "<untrusted_action_history>x1 x2 x3</untrusted_action_history>"
    msgs = [
        {"role": "system", "content": "sys " + block},
        {"role": "user", "content": "u " + block},
        {"role": "assistant", "content": "t"},
    ]
    out = pd.truncate_history(tok, msgs, 2)
    assert out is not None
    assert "x1 x2 x3" in out[0]["content"]  # system untouched
    assert "x1" not in out[1]["content"]  # user truncated


# ---------- process_sample ----------

def test_process_keep_within_budget():
    sample = _sample("short")
    out, action = pd.process_sample(_WordTokenizer(), sample, 10**9)
    assert action == "keep" and out is sample


def test_process_truncates_over_budget():
    user = "H " + "<untrusted_action_history>" + " ".join(f"w{i}" for i in range(200)) + "</untrusted_action_history>"
    sample = _sample(user)
    out, action = pd.process_sample(_WordTokenizer(), sample, 30)
    assert action == "truncate" and out is not None
    assert out["meta"]["truncated"] is True
    assert out["meta"]["orig_tokens"] > out["meta"]["final_tokens"]
    # fits now: whole message re-tokenized below budget
    assert pd.sample_total_tokens(_WordTokenizer(), out["messages"]) <= 30


def test_process_drops_when_no_history():
    sample = _sample(" ".join(f"w{i}" for i in range(100)))
    out, action = pd.process_sample(_WordTokenizer(), sample, 10)
    assert action == "drop" and out is None


# ---------- main e2e ----------

def test_main_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "load_tokenizer", lambda _s: _WordTokenizer())
    src = tmp_path / "train.jsonl"
    samples = [
        _sample("tiny"),
        _sample("H <untrusted_action_history>" + " ".join(f"w{i}" for i in range(80)) + "</untrusted_action_history>"),
        _sample(" ".join(f"n{i}" for i in range(100))),  # no history -> drop
    ]
    src.write_text("\n".join(json.dumps(s) for s in samples) + "\n\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    rc = pd.main([
        "--files", str(src), "--budget", "25", "--out-dir", str(out_dir),
    ])
    assert rc == 0
    kept = (out_dir / "train_final.jsonl").read_text(encoding="utf-8").strip().splitlines()
    dropped = (out_dir / "prepare_excluded.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(kept) == 2 and len(dropped) == 1
    report = json.loads((out_dir / "prepare_report.json").read_text(encoding="utf-8"))
    assert report["overall"] == {"kept": 2, "truncated": 1, "dropped": 1}
    trunc_meta = json.loads(kept[1])["meta"]
    assert trunc_meta["truncated"] is True and trunc_meta["final_tokens"] <= 25


def test_load_tokenizer_file_and_hub(tmp_path, monkeypatch):
    import tokenizers
    monkeypatch.setattr(
        tokenizers.Tokenizer, "from_file",
        classmethod(lambda cls, p: _WordTokenizer()),
    )
    tok_file = tmp_path / "t.json"
    tok_file.write_text("{}")
    assert pd.load_tokenizer(str(tok_file)) is not None
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda r, f: "/x")
    assert pd.load_tokenizer("repo/id") is not None
