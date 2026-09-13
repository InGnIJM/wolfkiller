# 项目分析报告

> 分析日期：2026-08-31 · 分析基准：HEAD `63fd4b5` · 工作区干净（无未提交改动）

## 一、项目是什么

**Wolf Killer** —— 完全由 LLM 智能体驱动的狼人杀对局系统。狼人、平民、预言家、女巫、猎人、守卫全部由大模型控制，**无真人参与**，观众通过时间轴界面观看直播或回放。

技术栈：Python 3.11+ / FastAPI + LangChain / React 19 + MUI 9 + Zustand 5 + Vite。

## 二、规模

| 维度 | 数值 |
| --- | --- |
| 提交数 | 455（2026-05 有 19 个，2026-08 有 436 个，近期高强度迭代） |
| 后端源码 | 17,073 行（148 个 .py，含内嵌测试目录） |
| 后端测试 | 22,998 行（`backend/tests/`）+ `app/**/test/` 内嵌测试 |
| 前端源码 | 4,879 行（47 个 .ts/.tsx） |
| 前端测试 | 4,067 行 |
| 测试/源码比 | 后端约 1.35 : 1，前端约 0.83 : 1 |
| TODO/FIXME | **0**（源码内无任何遗留标记） |

后端测试密度高于源码，说明测试是被认真维护的一等公民，不是点缀。

## 三、架构评价：这是项目最有价值的部分

核心成果是**「通用角色流水线」**——把角色规则表达为：

```
冻结声明(RoleSpec) + 纯 Hook + 类型化 Effect + 唯一原子写入口(EffectApplier)
```

分层职责清晰，每个模块只有一个职责：

| 模块 | 职责 |
| --- | --- |
| `models/pipeline.py` | 冻结值类型、Effect 代数、ActionContext/Contract/RoleSpec |
| `core/context_projector.py` | 按可见性标签（PUBLIC/ACTOR/CAMP）投影最小上下文 |
| `core/action_validator.py` | 纯校验，零状态读写 |
| `core/action_resolver.py` | 调纯 Hook，产出确定性 Effect 批次 |
| `core/effect_applier.py` | **唯一写入口**：整批校验 + CAS(revision) + 原子应用 + 幂等 |
| `core/scheduler.py` | 调度点、稳定排序、响应窗口、阶段门禁 |
| `core/point_journal.py` | 断点续跑检查点 |

**设计亮点：**

1. **扩展性是设计出来的，不是碰巧的**。`roles/guard.py` 是验收证明：新增角色不动任何核心模块。有「核心模块 blob 不变」测试守着这条约束。
2. **隐私边界有机制保障**。狼人看不到好人信息靠的是 `ContextProjector` 的可见性投影，且有隐私扫描测试禁止私有字段（`check_results`/`has_antidote`/`night_intel` 等）泄漏到公开 DTO 和前端。
3. **容错路径是显式的**。超时/解析失败的席位一律转成**显式技术弃票**再结算，不存在静默缺票——这在 benchmark 的极端测试里被验证有效。
4. **快照版本化**。存档带 `pipeline_version`/`registry_digest`/`state_revision`，缺迁移器或 V2→V1 回滚都抛 `SnapshotVersionError`。

## 四、实测状态（不是看文档，是真跑）

### 后端：2,177 个用例，2,175 通过，**2 个失败**

### 前端：188 个用例，19 个文件，**全部通过**（22 秒）

### 失败用例 1：过期的断言

```
test_llm_client_profiles.py::test_probe_invokes_plain_model_and_returns_capabilities
```
`probe()` 现在多返回一个 `forced_tool_choice: True` 键，测试期望字典没跟上。
来源：`8cba58c fix(providers): bind tools without forced tool_choice on custom gateways`。
性质：测试滞后于实现，改期望值即可。

### 失败用例 2：真实的语义分歧 ⚠️

```
test_output_parser.py::test_schema_validation_exposes_stable_specific_failure_code
  [payload2-action_payload_wrong_type]
```
`parse_action_payload` 在校验**前**调用了 `coerce_payload_to_schema`（`output_parser.py:237`），把 `target_seat: "1"` 静默转成 `1`，于是「类型错误」不再抛异常。

来源：`7ab4c9c` 引入的强制类型转换，本意是处理伪 XML tool call 提取出来的字符串参数——这个需求是合理的。但副作用是**连带放宽了普通 dict 载荷的校验强度**，而测试仍在断言严格拒绝。

这是一个需要人来拍板的取舍，不是单纯改测试：
- 若判定「宽松是特性」→ 更新测试，并确认 `action_payload_wrong_type` 这条失败码还有没有触发路径
- 若判定「严格是契约」→ 把 coercion 收窄到只作用于伪 XML 提取路径

## 五、Benchmark 数据：工具链完整，但样本量严重不足

三次跑批，累计**只有 4 局完整对局**：

| 批次 | 完成/中止 | 平均局长 | fallback 率 | 错误率 | 技术弃票 | 每局 token | 延迟 p50/p95/p99 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mimo-calib | 2 / 0 | 2.5 天 | 0.0% | 0.8% | 0 | 31,932 | 16.5s / 43.3s / 108.4s |
| mimo-accept | 2 / 0 | 4.0 天 | 0.0% | 3.0% | 0 | 34,995 | 13.3s / 37.3s / 61.8s |
| exitprobe | 0 / 1 | n/a | 22.4% | 77.6% | 140 | n/a | 5.0s / 14.3s / 15.2s |

**健康态（mimo-\*）**：零 fallback、错误率 <3%、零技术弃票，每局约 60–66 次调用、3.2–3.5 万 token。p99 延迟到 108 秒偏长，但没击穿 500 秒阶段超时。

**退化态（exitprobe，故意打的失败探针）**：657 次调用、77.6% 错误率、140 次技术弃票、135 条模型错误，对局中止。**好消息是降级路径работает**——p50 只有 5 秒说明是快速失败（连接类错误），系统没有静默丢票，而是全部转成显式弃票后收尾。这正是设计意图。

**决策质量指标**（n 很小，仅看趋势）：

| 指标 | mimo-calib | mimo-accept |
| --- | --- | --- |
| 预言家验中狼率 | 40.0% | 33.3% |
| 放逐命中狼率 | 60.0% | 71.4% |
| 好人投票命中率 | 71.0% | 68.8% |
| 狼刀命中神职率 | 60.0% | 87.5% |
| **狼队投票集中度** | **27.3%** | **26.7%** |

**最值得注意的信号**：狼队投票集中度只有约 27%，说明**狼人之间协同度很低**，基本各投各的。这与「狼队夜间有专门讨论频道」的设计预期不符，是提示词或上下文传递上最值得排查的点。

⚠️ `docs/benchmark.md` 自己写了「胜率类指标建议每配置至少 30–100 局；10–20 局只能看趋势」。当前 4 局，**50/50 胜率在统计上毫无意义**，不能作为平衡性结论。

## 六、发现的问题（按严重度排序）

### P0-1：benchmark 脚本全部未纳入版本控制 🐛

`.gitignore:15` 忽略了 `backend/scripts/`，导致**整条 benchmark 命令行工具链不在仓库里**：

```
git ls-files backend/scripts/  →  0 个文件
```

磁盘上存在但无人能从仓库获取：`run_benchmark.py`、`perf_benchmark.py`、`export_game_summary.py`、`add_model_config.py`、`probe_model_config.py`。

后果：`docs/benchmark.md` 和 `docs/development.md` 教用户执行 `python scripts/run_benchmark.py`，**新克隆的仓库里这个脚本不存在**。
（`app/benchmark/` 这个可复用库**是**纳入版本控制的，也确实在 100% 覆盖门禁内。）

推测成因：目录里混进了一次性调试脚本 `inspect_tasks_42376.py`，于是被一刀切忽略。

**建议**：改用精确忽略（只忽略 `inspect_tasks_*.py`），把五个正式脚本纳入版本控制；删掉或归档那个调试脚本。

### P0-2：完全没有 CI，100% 覆盖门禁形同虚设 🐛

`.github/` 下只有一个来路不明的 `java-upgrade/` 目录（与本栈无关的 Java 脚手架 + PowerShell 钩子），**零个 GitHub Actions 工作流**。

后果很直接：那 2 个失败用例就躺在仓库里没人发现。100% 覆盖门禁只存在于文档里，靠人手动跑。

**建议**：加一条 workflow，跑 `pytest tests app`、`npm test`、`npm run lint`。

### P1-1：遥测数据覆盖不全

45 个对局存档中，只有 **6 个**有 `summary.json`，**12 个**有 `llm_calls.log`。补生成脚本恰好是未纳入版本控制的那批（见 P0-1），历史对局的可分析性因此受限。

### P1-2：文档数字已过期

`docs/development.md` 写「截至 2026-08-29：后端 1919 个用例 / 前端 184 个用例」。实测现在是**后端 2,177 / 前端 188**。

### P2：仓库卫生

- 根目录 `data/` 空（0 文件）、根目录 `node_modules/` 空（1K）——两者都是残留
- `frontend/src/components/layout/` 空目录（文档已注明）
- `.claude/`、`.superpowers/`、`.zcode/`、`.worktrees/` 是各类 AI 工具残留
- 根目录 `shit` 文件（已 gitignore，内容是 LLM 对话样本）

## 七、环境注意事项

1. **vitest 调用方式敏感**：`npm test -- --run` 会挂死（实测 17 分钟无输出）；直接 `npm test` 正常，22 秒跑完。
2. **pytest 临时目录清理被安全层拦截**：pytest 回收 `Temp/pytest-of-*/garbage-*` 时触发批量删除保护（54 个 > 阈值 50），报 `SAFE_DELETE_FAIL_CLOSED`。不影响测试结果，但每次都刷屏。这是环境问题，非项目缺陷。
3. **前端覆盖率门禁已知不达标**：按 `MEMORY.md` 记录，`npm run test:coverage` 在 HEAD 约 96.4%，低于 100% 阈值，基线同样失败。属既有问题，建议以 `npm test` 为准、只比对覆盖率增量。

## 八、总结

**这是一份工程质量明显高于平均水准的代码库。** 角色流水线的抽象层次、隐私边界的机制化保障、显式降级路径、断点续跑与快照版本化，都是经过深思熟虑的设计，且有测试守着。零 TODO、测试密度超过源码，说明维护是认真的。

**但它现在处在「设计已就位、工程护栏还没合上」的状态**：

- 最扎眼的是**没有 CI** —— 两个失败用例就这么躺着；
- **benchmark 工具链文档写了、仓库里没有** —— 这是新手照文档操作会立刻踩到的坑；
- **样本量只有 4 局**，还远没到能下平衡性结论的时候；
- 类型转换放宽测试那一条，是需要人来拍板的语义取舍。

### 建议的下一步（按投入产出比）

1. 补一条 CI workflow —— 成本极低，直接防住当前这类「测试挂了没人知道」
2. 把 `backend/scripts/` 从 gitignore 里放出来，精确忽略调试脚本 —— 半小时的活，修复文档与仓库的断裂
3. 拍板 `action_payload_wrong_type` 的取舍，修掉失败测试，恢复 100% 门禁
4. 排查狼队投票集中度仅 27% 的问题 —— 这是唯一指向真实游戏质量缺陷的信号
