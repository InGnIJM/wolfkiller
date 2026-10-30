# post_training

狼人杀小模型后训练工作区。目标：在 8GB 显存（RTX 4060 Laptop）上把
Qwen3-4B 级别的小模型蒸馏/后训练到接近 teacher 大模型的局内表现。

## 目录

```
post_training/
  export_dataset.py   # llm_prompts.jsonl x model_requests -> SFT JSONL 导出器
  test/               # pytest 单测（--cov=export_dataset 100%）
  data/               # 导出产物（已 gitignore）
    train.jsonl       # 训练集（按 game_id 切分，防同局泄漏）
    eval.jsonl        # 评估集
    excluded.jsonl    # 被剔除样本及原因（留作 KTO 负样本候选）
    report.json       # 分布统计
```

## 用法

```bash
cd post_training
python export_dataset.py                 # 默认读 ../backend/data，全部开关开
python export_dataset.py --include-weak-teachers   # 保留 glm/mimo 样本（打标不丢）
python export_dataset.py --no-upgrade    # 不做旧格式升级
python -m pytest test/ -q                # 测试
python -m pytest test/ --cov=export_dataset --cov-report=term-missing
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
