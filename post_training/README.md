# post_training

狼人杀小模型后训练工作区。目标：在 8GB 显存（RTX 4060 Laptop）上把
Qwen3-4B 级别的小模型蒸馏/后训练到接近 teacher 大模型的局内表现。

## 目录

```
post_training/
  export_dataset.py   # llm_prompts.jsonl x model_requests -> SFT JSONL 导出器
  tokenize_report.py  # Qwen3 tokenizer 实测长度分布 -> tokenize_report.json
  prepare_dataset.py  # 12k 窗口应用：history 块保尾截头 -> *_final.jsonl
  train_sft.py        # WSL 内跑的 Unsloth QLoRA 训练入口（含 --smoke）
  eval_student.py     # 留出集评估：completion NLL + 结构化生成比对
  serve_student.py    # OpenAI 兼容推理服务（引擎 benchmark 用，含 speech tool_calls）
  REPORT.md           # 后训练完整报告（数据/训练/离线评估/真实对局）
  test/               # pytest 单测（各模块 --cov 100%）
  data/               # 导出产物（已 gitignore）
    train.jsonl       # 训练集（按 game_id 切分，防同局泄漏）
    eval.jsonl        # 评估集
    train_final.jsonl # 12k 窗口后的最终训练集
    eval_final.jsonl
    excluded.jsonl    # 被剔除样本及原因（留作 KTO 负样本候选）
    report.json       # 分布统计
    tokenize_report.json
    prepare_report.json
    eval_report.json  # eval_student.py 产出（逐行指标 + 汇总）
    eval_report.md    # 同一份结果的速读卡
```

## 用法

```bash
cd post_training
python export_dataset.py                 # 默认读 ../backend/data，全部开关开
python tokenize_report.py                # 实测 token 分布
python prepare_dataset.py                # 应用 12k 窗口（保尾截头）
python -m pytest test/ -q                # 全部测试

# 留出集评估（GPU；loss 全量 + 生成抽样，避免 8GB 机器跑一整夜）：
~/sft/bin/python eval_student.py --mode both --gen-sample 150
~/sft/bin/python eval_student.py --mode loss          # 只测完成度 loss
~/sft/bin/python eval_student.py --mode both --gen-sample 150 --resume   # 中断后接着跑
```

# WSL 内（venv 在 ~/sft，模型在 /mnt/e/models/）：
~/sft/bin/python train_sft.py --smoke    # 显存冒烟
~/sft/bin/python train_sft.py            # 正式训练
```

## 样本格式

每行一个 JSON：`messages`（system/user/assistant 三段）+ `meta`（审计字段，
训练框架忽略）。

- 结构化动作（action/night/pipeline）→ assistant = 契约 JSON
  `{"action_type":...,"target_seat":...,"reasoning":...}`（pipeline 含
  `schema_version`；多目标契约含 `target_seats`）。引擎 JSON 解析路径可直接
  消费，学生模型无需工具调用支持。
- speech → assistant = 纯文本发言（`parse_speech` 路径）。

`meta` 关键字段：`call_kind` / `contract_id` / `teacher` / `prompt_chars` /
`repair_context`（含修复提醒轮）/ `upgraded`（旧格式已升级到现行渲染）。

## 过滤规则

| 原因 | 含义 |
| --- | --- |
| `engine_fallback` | `technical_failure_code` 非空 = 引擎代答的 abstain 兜底 |
| `degraded` | 账本标记的降级结果 |
| `weak_teacher` | glm-5.3-flash / mimo-v2.6-flash（默认剔除，KTO 阶段可当负样本） |
| `upgrade_conflict` | 旧 prompt 的契约静态字段与现行 registry 不一致（instructions 改过），升级不安全 |
| `empty_completion` / `no_result` | 无有效输出或无账本记录 |

## 格式升级

2026-10-26 前渲染的 pipeline prompt 缺 `action_types` /
`actions_requiring_target` / `multi_target_actions` 三键；10-09 前缺
`degraded`。脚本从冻结 registry 确定性补回（逐字段校验其余部分不变才升级，
不一致进 `upgrade_conflict` 剔除）。

## 已知事项

- 约 38% 样本 prompt 在 16k-32k 字符（估 ~12-24k token），超过 8k 训练窗口
  需在 tokenize 阶段截尾或丢弃——见 `meta.prompt_chars`。
- SFT 天花板 = teacher（gemini-3.8/3.7-flash、deepseek-v4.1）局内水平；
  超越 teacher 需第二阶段 KTO/自博弈（excluded.jsonl + 学生自产对局）。
