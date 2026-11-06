# WolfKiller 后训练报告（2026-11-03）

> 目标：在 8GB 显存（RTX 4060 Laptop / WSL2）上，把 Qwen3-4B 级别的小模型蒸馏到接近 teacher
> 大模型的局内表现。本报告覆盖数据管线、SFT 训练、离线留出集评估与首次真实对局验证的全部结果，
> 并给出问题根因与下一步优先级。

---

## 0. 结论摘要

| 维度 | 结论 |
| --- | --- |
| SFT 训练 | ✅ 完成 1 epoch（383 步 / 6,122 条单 teacher 数据），产物 `lora_final` |
| 输出格式 | ✅ JSON 100% 可解析，`action_type` 94.6% 一致 |
| 选座判断 | ⚠️ 75.5% 一致（离线），真实对局里放逐狼率仅 16.7% |
| 自由发言 | ❌ 最弱：NLL 1.85，真实对局 35/45 条发言自报底牌，狼人公开发言"我底牌是深水狼" |
| 长上下文 | ❌ 训练窗口 8k，真实对局后期 prompt 达 8~11k token，出现 360s 超时 |
| 端到端链路 | ✅ 已打通：本地 OpenAI 兼容服务 + 引擎 benchmark；分布内 12 人板对照实验见 §5.6 |

**一句话**：动作层已可用、格式完全过关；语言/策略层（尤其发言）还达不到"能打"的水平；
当前模型适合继续迭代，不适合作为最终产物。

---

## 1. 背景与约束

- 硬件：RTX 4060 Laptop，8GB 显存；系统内存 13GB（WSL2）
- 底座：`Qwen3-4B-Instruct-2507`（本地 `/mnt/e/models/`）
- 训练方式：4-bit QLoRA + Unsloth，completion-only loss
- 数据来源：引擎训练语料采集（`backend/data/games/<id>/llm_prompts.jsonl`），
  按对局切分训练/留出集，避免同局泄漏
- 目标 teacher：gemini-3.8-flash-high（SFT 阶段单一 teacher）

---

## 2. 数据管线

| 阶段 | 结果 |
| --- | --- |
| 原始采集 | 17,548 条记录 |
| 导出过滤后 | **15,825 条**（train 14,915 / eval 910） |
| 剔除 | 1,723 条：weak_teacher 1,025 / upgrade_conflict 360 / engine_fallback 338 |
| 旧格式升级 | 1,058 条（契约静态字段回填） |
| 修复上下文样本 | 36 条 |
| 分布（全量） | pipeline 1,118 / night 6,584 / speech 4,598 / action 3,525 |
| teacher 分布（全量） | gemini-3.8-high 6,186 / 3.7-high 4,373 / 3.8-tiered 3,905 / deepseek-v4.1 1,279 / deepseek-0731 82 |

**窗口分析（Qwen3 tokenizer 实测）**

| 窗口 | 样本可完整容纳 |
| --- | --- |
| 8k | 85.6% |
| 12k | 99.76% |
| 16k | 99.98% |

- token 长度：p50 6,095 / p90 8,548 / p95 9,003 / p99 10,147 / max 19,175
- 12,288 预算下：0 条丢弃、38 条截尾

**SFT 强子集（实际训练数据）**

- 6,122 条，**单一 teacher**：gemini-3.8-flash-high
- 按类型：night 2,400 / speech 1,697 / action 1,503 / pipeline 522
- 过滤条件：单 teacher + 完整会话 ≤ 8,192 token（completion 不被截断）
- 发言样本长度：p10 157 / p50 175 / p90 187 / max 200 字（受引擎 prompt 的 5–200 字约束）

---

## 3. SFT 训练

### 3.1 配置

| 项 | 值 |
| --- | --- |
| 底座 | Qwen3-4B-Instruct-2507（4-bit） |
| LoRA | r=16，alpha=32，7 个投影模块；可训练参数 33M（0.81%） |
| 序列 / batch | max_seq_len 8192；batch 1 × grad_accum 16（有效 batch 16） |
| 学习率 | 1e-4，cosine，warmup 5% |
| epoch | 1（6,122 样本 → 383 步） |
| 精度 | bf16 + paged_adamw_8bit + gradient checkpointing |

### 3.2 时间线与稳定性

- 10-30 起多次尝试，早期因 12k 长样本/显存与 checkpoint 损坏多次中断（早期速度低至 500~1100s/步）
- 11-01 16:09 起改用 `batch=1 × accum=16`、save-steps=1，稳定在 ~150–200s/步
- 11-02 08:23 从 checkpoint-262 续训，**13:37 完成 383/383 步**
- 最终这段 121 步耗时 5h14m

### 3.3 Loss 曲线（每个 logging step）

| step | 10 | 40 | 80 | 120 | 160 | 200 | 240 | 280 | 320 | 360 | 380 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| loss | 3.29 | 1.67 | 1.60 | 1.53 | 1.45 | ~1.40 | 1.39 | 1.40 | 1.40 | 1.34 | 1.40 |

- 收敛到 **~1.35–1.45** 平台；lr 末端衰减到 ~3e-8
- 注意：日志里的 `train_loss: 0.4435` 是**续训口径伪值**（只累计 262→383 的 121 步却按 383 步平均），
  真实逐步 loss 见上表

### 3.4 产物

```
post_training/ckpt/wolfkiller-sft-strong/
├── lora_final/              # 最终 LoRA（137MB）
│   ├── adapter_model.safetensors
│   ├── adapter_config.json
│   └── tokenizer* / chat_template.jinja
└── checkpoint-381/382/383   # 最近 3 个断点（383 含 optimizer）
```

---

## 4. 离线留出集评估

数据：`data/eval_final.jsonl`（910 条，按 game_id 与训练集隔离）。
评估口径：loss 全量 786 条可评（124 条 >8k token 跳过）；生成抽样 120 条（prompt ≤4500 池，
按 call_kind 分层）。

### 4.1 总体

| 指标 | 结果 |
| --- | --- |
| completion NLL / ppl | **1.328 / 3.78** |
| JSON 合法解析率 | **100%**（112/112） |
| action_type 一致率 | **94.6%**（93） |
| target（选座）一致率 | **75.5%**（49） |
| 动作+目标完全一致 | **75.5%**（49） |
| 狼队讨论 speak / preferred_target | 63.2% / 57.9%（各 19） |
| 发言 char-F1 / day_plan F1 | 0.195 / 0.314 |

### 4.2 分类型

| 类型 | n | NLL | action 一致 | target 一致 |
| --- | --- | --- | --- | --- |
| action（放逐投票） | 178 | **1.078** | 100%（12） | 75.0%（12） |
| night（夜间） | 352 | **1.118** | 93.4%（76） | 71.9%（32） |
| pipeline（狼队讨论） | 49 | 1.554 | 100%（5） | 100%（5） |
| speech（发言/遗言） | 207 | **1.848** | — | — |

- 最易：`werewolf_kill` NLL 0.84（狼刀目标 10/10 全中）
- 最难：`last_words` 2.26、`crimson_explode` 1.93、`day_speech` 1.80

### 4.3 按 teacher（训练只见过 gemini-3.8-flash-high）

| teacher | n | NLL | action | target |
| --- | --- | --- | --- | --- |
| gemini-3.8-flash-high（同分布） | 47 | 1.202 | 100%（5） | 100%（3） |
| gemini-3.7-flash-high | 293 | 1.211 | 93.9%（33） | 78.6%（14） |
| gemini-3.8-flash-tiered | 348 | 1.285 | 92.1%（38） | 66.7%（21） |
| deepseek-v4.1-flash | 98 | 1.896 | 100%（17） | 81.8%（11） |

跨 teacher 风格泛化正常；deepseek 风格差异最大。

### 4.4 错误分析

- 12 条选座错误里 **9 条是警长票**（12/21 正确）——本质是"信哪个预言家"的判断分歧；
  学生的推理链完整，只是和 teacher 站边不同
- 输出无空串、无乱码，长度合规（p50 138 字）
- 局限：
  - 生成抽样来自 ≤4500 token 池，存在长度偏置
  - target 样本仅 49 条（95% 置信区间约 ±12%）
  - char-F1 对同义改写惩罚重，只能作弱代理
  - 124 条 >8k token 样本未评（真实长局覆盖不足）

---

## 5. 端到端真实对局验证（Option 2）

### 5.1 架构

```
WolfKiller 后端（Windows:8000）
  └─ 模型配置 "WolfKiller SFT Qwen3-4B" (id ab82a769…)
       └─ http://127.0.0.1:8100/v1        ← WSL 本地服务
            └─ post_training/serve_student.py
                 ├─ Qwen3-4B-Instruct-2507 (4-bit)
                 └─ LoRA: ckpt/wolfkiller-sft-strong/lora_final
```

服务实现要点：

- OpenAI 兼容 `/v1/chat/completions`（支持 **SSE 流式** 与普通响应）
- 发言路径必须返回**原生 tool_calls**（引擎的 speech 解析只认 tool call）：
  服务端把纯文本发言包装成 `speak` / `last_words` 调用
- 动作/夜间契约继续返回 JSON content，走引擎的 JSON 兜底解析
- JSON 括号停止条件 + 512 token 硬上限；prompt 超长时头尾截断（头 1k + 尾 7k）
- 12k 上下文；每次调用后 `empty_cache`

### 5.2 对局结果

| 项 | 值 |
| --- | --- |
| 板子 | 10 人标准板（3 狼 / 3 民 / 预·女·猎·守），无警长 |
| 模式 | `mixed_arena`，10 个座位全部同一个学生模型 |
| 轮次 / 胜负 | **6 轮，狼人胜**（`all_villagers_dead`，屠民边） |
| 时长 | **77.7 分钟**（09:30:37 → 10:48:19） |
| LLM 调用 | 132 次（130 个逻辑请求，重试 7 次） |
| Token | prompt 844,975 / completion 21,068 / 合计 866,043 |
| 延迟 | p50 26.3s / p95 163.6s / p99 360.1s |
| 请求质量 | 成功 96.9%，失败 2.3%，fallback 2.3%，合法 97.7% |
| 规则拒绝 | 11 次（8.5%）：`werewolf_kill target_not_alive` ×10、`guard consecutive` ×1 |

### 5.3 决策质量（v7 指标，单局样本）

| 指标 | 值 | 样本 |
| --- | --- | --- |
| 放逐狼率 | **16.7%** | 6 次放逐 |
| 好人投票命中狼 | **29.6%** | 27 票 |
| 放逐神职误伤率 | **50%** | 6 次放逐 |
| 预言家查验命中 | 66.7% | 3 次 |
| 狼刀神职率 | 33.3% | 3 刀 |
| 狼票集中度 | 0.25（7 个不同目标） | — |

### 5.4 行为观察（本局最重要的产出）

1. **发言泄露身份（35/45 条发言自报底牌）**
   - 狼 1/3/8 号在白天公开发言："**我底牌是深水狼**，首日发言保持低调……"——直接复读了
     狼队夜间计划里的"深水位（1号）"
   - 女巫、猎人、预言家、守卫也全部公开报底牌
   - 猎人反复说"我底牌是猎人，昨晚开枪"（实际上从未开枪）
2. **发言模板化 / 策略备忘录化**
   - 大量发言以"我底牌是 X，坚决站边好人。我反驳 N 号的发言……"开头，像内部策略笔记，
     不是公开口语；违反 prompt 的"不要复述私有思考"
3. **状态跟踪差**
   - 狼人 10 次狼刀目标选了**已死玩家**，被引擎规则拒绝（`target_not_alive`）
4. **从众投票**
   - 第 1 轮 10 票里 8 票投给 5 号（女巫）、第 2 轮几乎全票投 2 号（村民）——
     好人跟风、狼人混票，最终 6 轮内屠民边
5. **速度随上下文增长退化**
   - 第 1 轮 8.7 分钟 → 第 2 轮 17.8 分钟 → 第 3 轮 31.6 分钟
   - 后期 prompt 从 13k 字符涨到 21.9k 字符（≈8–11k token），
     超过训练窗口 8k；出现 7 次 `request_timeout`，单次最长 360s 后兜底模板

### 5.5 典型片段

> 狼 1 号（公开白天发言）：
> "首日平安夜，好人阵营信息量充足。**我底牌是深水狼**，首日发言保持低调，不主动带节奏……"

> 狼 3 号：
> "**我底牌是深水狼**，首日发言保持低调。我反驳 2 号的发言……"

> 预言家 9 号（正常）：
> "我底牌是预言家，昨晚查验 3 号是狼人，3 号出局……"

---


### 5.6 12 人板对照实验（2026-11-03 下午）

为了验证「质量差是不是板子/prompt 分布外造成的」，用同一个 `lora_final` 跑了训练分布内的
12 人板 **猎日逐光（twelve-player-sun-hunt，训练集里 774 条，是出现最多的板子）**，
其余条件不变（全学生座位、单局）。

| 指标 | 10 人标准板（分布外） | 12 人猎日逐光（分布内） |
| --- | --- | --- |
| 胜负 / 轮次 | 狼胜（屠民边）/ 6 轮 | 狼胜（屠神边）/ 4 轮 |
| 时长 | 77.7 分钟 | **53.7 分钟** |
| LLM 调用 | 132 | 130 |
| 延迟 p50 / p95 / p99 | 26.3 / 163.6 / 360.1s | **22.0 / 81.1 / 253.1s** |
| 重试率 | 5.3%（7 次） | **0.8%（1 次）** |
| 规则拒绝 | 11 次（8.5%） | **1 次（0.8%）** |
| 发言条数 | 45 | 43 |
| 发言提及底牌 | 35（77.8%） | 27（62.8%） |
| **狼人公开自曝** | **3 只（"深水狼"）** | **0** |
| 放逐狼率 | 16.7%（1/6） | 0%（0/4） |
| 好人投票命中狼 | 29.6%（8/27） | 11.5%（3/26） |
| 放逐神职误伤率 | 50%（3/6） | **100%（4/4）** |
| 预言家查验命中 | 66.7%（2/3） | 100%（2/2） |

**结论：**

1. **分布外确实是行为崩坏的主要原因**：换回训练板后，
   狼人自曝消失（3 → 0）、规则拒绝从 11 次降到 1 次、重试率从 5.3% 降到 0.8%、
   延迟 p99 从 360s 降到 253s、整局时长缩短 24 分钟。说明 10 人板 +「你的风格」段
   + 后期长上下文把模型推到了它没见过的分布上。
2. **但分布对齐救不了策略水平**：12 人板局里好人 4 次放逐**全部投死神职**
   （放逐神职误伤率 100%），最终 4 轮被屠神边。模型能"说人话"了，但仍然分不清真假预言家、
   无法组织有效归票——这是**模仿学习的天花板**，需要胜负信号（RL/自博弈）或强 teacher 混座来纠偏。
3. 两个板子都是单局、全学生自博弈，胜率类指标噪声大；但"行为形态"的改善非常明确。

## 6. 根因分析

| 问题 | 直接原因 | 深层原因 |
| --- | --- | --- |
| 发言泄露身份 | speech prompt 里包含私有事实/狼队夜间计划，小模型直接复读 | 训练数据没有"公开发言不得泄露私有事实"的负样本；单 teacher、单 epoch |
| 发言像策略备忘录 | 模型把"思考"与"发言"混为一谈 | speech 仅占训练集 27.7%（1,697/6,122），且长度集中在 175 字左右、开头模板集中 |
| 状态跟踪差（打死者） | 长上下文下对"存活名单"注意不足 | 训练 prompt 与真实对局 prompt 分布不同（训练以 12 人板为主 17.9k 字符，本局 10 人板 13–22k 字符） |
| 后期超时 | 8k 训练窗口 + 8GB 显存，长 prompt 推理极慢 | 训练数据只用 ≤8192 token；服务端 12k 上下文在 8GB 上处于交换边缘 |
| 整体策略弱 | 4B 容量 + 单 teacher 单 epoch | 需要更大模型或偏好优化（DPO/KTO）与更多高质量数据 |

---

## 7. 改进路线（按优先级）

1. **先建度量（半天）**
   - 用 gemini-3.8-flash-high 做 LLM judge，对 200 条发言按
     「事实一致性 / 切题 / 策略合理 / 自然度」打分，替代 char-F1
2. **扩 speech 数据 + 负样本（1~2 天）**
   - 从全量 4,598 条 speech 中筛 3.7/3.8 高质量样本（去 deepseek 风格），目标 4k+
   - 去模板：按开头聚类、去套话；按阶段（警上/警下/遗言/PK）分层
   - **新增负样本**：泄露私有事实、复述思考、编造查验/票型 → 用于 DPO/KTO
3. **speech 上采样 / 二阶段续训（1 天）**
   - 在 `lora_final` 上继续训练 speech 子集（lr 2e-5，1 epoch，约 1.5h）
   - 需要给 `train_sft.py` 加 `--adapter` 入口（`PeftModel.from_pretrained(..., is_trainable=True)`）
   - 或 speech 权重 2~3x 重训混合数据
4. **12k 窗口 + 长上下文策略**
   - 用 `train_final.jsonl`（12k 产物）训练，或至少把 strong 子集放宽到 12k
   - 服务端固定 8k 截断策略（头 1k + 尾 7k），避免 360s 超时
5. **偏好优化（2~3 天）**
   - 用 excluded.jsonl + 学生低分生成构造负样本，teacher 高质量发言做正样本，DPO
6. **推理/服务优化**
   - 4-bit 4B 在 4060 上 ~10 tok/s；考虑 llama.cpp/vLLM（AWQ/GPTQ）提速
   - 长局 benchmark 建议放到更快机器，或限制单局 prompt 预算
7. **Benchmark 规模化**
   - 先修 speech 再跑 paired（teacher vs student），≥20 局统计
   - teacher 的 Gemini 本地代理 `127.0.0.1:8045` 当前未启动，需要先恢复

---

## 8. 附录

### 8.1 关键文件

| 文件 | 说明 |
| --- | --- |
| `post_training/export_dataset.py` | 从 `llm_prompts.jsonl` 导出 SFT JSONL |
| `post_training/prepare_dataset.py` | 12k 窗口裁剪（保尾截头） |
| `post_training/subset_teacher.py` | 单 teacher + 8k 过滤（生成 train_strong） |
| `post_training/train_sft.py` | Unsloth QLoRA 训练入口（支持 --smoke/--auto-resume） |
| `post_training/eval_student.py` | 离线留出集评估（loss + 结构化生成比对，支持 --resume） |
| `post_training/serve_student.py` | OpenAI 兼容推理服务（流式 + tool_calls） |
| `post_training/data/eval_report.{json,md}` | 留出集评估结果 |
| `post_training/data/benchmark_report.{json,md}` | 首次真实对局 benchmark 报告 |
| `post_training/ckpt/wolfkiller-sft-strong/lora_final/` | 最终 LoRA |

### 8.2 复现命令

```bash
cd post_training
python export_dataset.py
python tokenize_report.py
python prepare_dataset.py
~/sft/bin/python subset_teacher.py --in data/train_final.jsonl \
  --out data/train_strong.jsonl --teacher gemini-3.8-flash-high \
  --model /mnt/e/models/Qwen3-4B-Instruct-2507 --max-tokens 8192
~/sft/bin/python train_sft.py --data data/train_strong.jsonl \
  --batch-size 1 --grad-accum 16 --save-steps 1 --auto-resume

# 离线评估
~/sft/bin/python eval_student.py --mode both --gen-sample 120 --resume

# 起本地服务
~/sft/bin/python serve_student.py --port 8100 --max-seq-len 12288
```

### 8.3 指标定义

- **completion NLL**：只对 assistant 完成部分（prompt 掩码）的 teacher-forced 交叉熵
- **JSON 解析率**：模型输出能被解析为 JSON 对象的比例（仅在生成抽样上）
- **action_type / target**：与 teacher 答案逐字段一致的比例
- **text F1**：去空白后的字符 bigram F1
- **放逐狼率 / 好人投票命中狼**：benchmark v7 的决策质量指标，按样本量谨慎解读
- **规则拒绝**：引擎规则校验拒绝的请求（如刀已死玩家），不计入 provider 失败

### 8.4 时间线

| 日期 | 事件 |
| --- | --- |
| 10-30 ~ 11-01 | 数据导出、窗口裁剪、单 teacher 子集；早期训练多次中断 |
| 11-02 08:23 | 从 checkpoint-262 续训 |
| 11-02 13:37 | SFT 完成（383/383） |
| 11-02 19:14 | 离线留出集评估完成 |
| 11-03 09:30 | 首次真实对局（benchmark）开始 |
| 11-03 10:48 | 对局结束：狼人胜（6 轮，78 分钟） |
