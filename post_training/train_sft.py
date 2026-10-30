"""QLoRA SFT for the werewolf student model (8GB VRAM path).

Runs inside WSL2:

    ~/sft/bin/python train_sft.py --smoke            # VRAM/throughput check only
    ~/sft/bin/python train_sft.py                  # full run

Design pins (all load-bearing at 8GB):

* ``max_seq_length=12288`` — the measured window: 99.76% of samples fit
  natively, the rest were tail-truncated by ``prepare_dataset.py``.
* ``completion_only_loss=True`` — loss on assistant tokens only. Without it
  the 15w-vocab logits over a 12k sequence alone exceed VRAM; with it, logits
  are computed only over the few hundred target tokens.
* ``paged_adamw_8bit`` + ``gradient_checkpointing="unsloth"`` + batch 1 with
  gradient accumulation — the only combination that fits.
* Dataset is converted to TRL conversational ``prompt``/``completion`` pairs
  (our ``messages`` list minus/plus the final assistant turn) so TRL can mask
  the prompt side for completion-only loss.
"""

from __future__ import annotations

import argparse
import json
import os


def load_pairs(path: str):
    """JSONL samples -> TRL conversational prompt/completion rows."""
    from datasets import Dataset

    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            messages = json.loads(line)["messages"]
            rows.append({"prompt": messages[:-1], "completion": messages[-1:]})
    return Dataset.from_list(rows)


def smoke(args) -> None:
    """Load 4bit weights and run one fwd/bwd step at full seq length."""
    import torch
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.model,
        max_seq_length=args.seq_len,
        load_in_4bit=True,
        dtype=None,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_dropout=0,
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )
    model.train()
    ids = tokenizer("smoke " * 64, return_tensors="pt").input_ids.cuda()
    # Pad to the full training window — VRAM at seq_len is what we are probing.
    seq = ids.repeat(1, args.seq_len // ids.shape[1] + 1)[:, : args.seq_len]
    out = model(input_ids=seq, labels=seq)
    out.loss.backward()
    peak = torch.cuda.max_memory_allocated() / 1024**3
    print(f"loss={out.loss.item():.3f}  peak_vram={peak:.2f} GiB")


def train(args) -> None:
    from unsloth import FastLanguageModel
    from trl import SFTConfig, SFTTrainer

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.model,
        max_seq_length=args.seq_len,
        load_in_4bit=True,
        dtype=None,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_dropout=0,
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )
    dataset = load_pairs(args.data)
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=SFTConfig(
            per_device_train_batch_size=1,
            gradient_accumulation_steps=args.grad_accum,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            lr_scheduler_type="cosine",
            warmup_ratio=0.05,
            logging_steps=10,
            save_strategy="epoch",
            output_dir=args.out,
            bf16=True,
            max_length=args.seq_len,
            completion_only_loss=True,
            packing=False,
            dataset_num_proc=4,
            report_to=[],
            seed=42,
        ),
    )
    trainer.train()
    model.save_pretrained(os.path.join(args.out, "lora_final"))
    tokenizer.save_pretrained(os.path.join(args.out, "lora_final"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model", default="/mnt/e/models/Qwen3-4B-Instruct-2507"
    )
    parser.add_argument(
        "--data",
        default="/mnt/e/code/WolfKiller/post_training/data/train_final.jsonl",
    )
    parser.add_argument("--seq-len", type=int, default=12288)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--grad-accum", type=int, default=16)
    parser.add_argument("--out", default="./ckpt/wolfkiller-sft")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        smoke(args)
    else:
        train(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
