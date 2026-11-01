import json
import os
import sys
import types

import pytest

import train_sft


def test_latest_checkpoint_picks_highest_step(tmp_path):
    out = tmp_path / "ckpt"
    out.mkdir()
    for step in (50, 150, 100):
        (out / f"checkpoint-{step}").mkdir()
    (out / "lora_final").mkdir()  # non-checkpoint dirs ignored
    (out / "checkpoint-x").mkdir()
    assert train_sft.latest_checkpoint(str(out)) == str(out / "checkpoint-150")


def test_latest_checkpoint_empty_and_missing(tmp_path):
    assert train_sft.latest_checkpoint(str(tmp_path / "nope")) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert train_sft.latest_checkpoint(str(empty)) is None


def test_load_pairs_splits_prompt_completion(tmp_path, monkeypatch):
    captured = {}

    class FakeDataset:
        @staticmethod
        def from_list(rows):
            captured["rows"] = rows
            return "DS"

    monkeypatch.setitem(sys.modules, "datasets", types.SimpleNamespace(Dataset=FakeDataset))
    src = tmp_path / "d.jsonl"
    msgs = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    src.write_text(json.dumps({"messages": msgs}) + "\n\n" + json.dumps({"messages": msgs}), encoding="utf-8")
    assert train_sft.load_pairs(str(src)) == "DS"
    assert captured["rows"][0] == {"prompt": msgs[:-1], "completion": msgs[-1:]}
    assert len(captured["rows"]) == 2


def test_main_defaults_and_flags(monkeypatch):
    seen = {}

    def fake_train(args):
        seen["args"] = args

    monkeypatch.setattr(train_sft, "train", fake_train)
    monkeypatch.setattr(train_sft, "smoke", lambda args: seen.setdefault("smoke", args))
    monkeypatch.setattr(sys, "argv", ["train_sft.py"])
    assert train_sft.main() == 0
    args = seen["args"]
    assert args.seq_len == 8192 and args.epochs == 1.0
    assert args.batch_size == 2 and args.grad_accum == 8
    assert args.save_steps == 50 and args.resume is None and not args.auto_resume

    seen.clear()
    monkeypatch.setattr(sys, "argv", ["train_sft.py", "--smoke"])
    assert train_sft.main() == 0
    assert "smoke" in seen


def test_train_resume_resolution(monkeypatch, tmp_path):
    """--resume path wins; --auto-resume falls back to newest checkpoint dir."""
    calls = {}

    saved = types.SimpleNamespace(save_pretrained=lambda path: None)
    monkeypatch.setitem(sys.modules, "unsloth", types.SimpleNamespace(
        FastLanguageModel=types.SimpleNamespace(
            from_pretrained=lambda **kw: (saved, saved),
            get_peft_model=lambda m, **kw: m,
        )))
    monkeypatch.setitem(sys.modules, "trl", types.SimpleNamespace(
        SFTConfig=lambda **kw: kw,
        SFTTrainer=lambda **kw: types.SimpleNamespace(
            train=lambda resume_from_checkpoint=None: calls.__setitem__("resume", resume_from_checkpoint)),
    ))
    monkeypatch.setattr(train_sft, "load_pairs", lambda p: "DS")
    monkeypatch.setattr(os.path, "join", os.path.join)

    args = types.SimpleNamespace(
        model="m", seq_len=8192, lora_r=16, lora_alpha=32,
        data="d", out=str(tmp_path), batch_size=2, grad_accum=8, epochs=1.0,
        lr=1e-4, save_steps=50, resume=None, auto_resume=False, smoke=False,
    )
    train_sft.train(args)
    assert calls["resume"] is None

    (tmp_path / "checkpoint-50").mkdir()
    args.auto_resume = True
    train_sft.train(args)
    assert calls["resume"].endswith("checkpoint-50")

    args.resume = "/explicit/path"
    train_sft.train(args)
    assert calls["resume"] == "/explicit/path"
