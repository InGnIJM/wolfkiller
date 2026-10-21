# 文档索引

Wolf Killer 的文档库。新手从根目录 [README](../README.md) 开始；想深入再按需往下读。

| 文档 | 内容 | 读者 |
| --- | --- | --- |
| [gameplay.md](gameplay.md) | 游戏规则、角色能力、对局流程、胜负条件 | 玩家 / 想了解产品的人 |
| [architecture.md](architecture.md) | 模块划分、角色流水线、状态机、持久化、前端架构 | 开发者 |
| [development.md](development.md) | 环境变量、本地开发、测试与门禁、数据存储、踩坑记录 | 开发者 |
| [benchmark.md](benchmark.md) | 耐久 benchmark API/CLI、指标口径与导出 | 跑评测 / 看报告的人 |
| [plans/](plans/) | **现行实施计划**（头部写进度与残留）：网易板型扩板（阶段 0–3.6 完成；**阶段 4 进行中：孤独少女、寻香识命与奇迹商人已落地**；**阶段 4.6 自曝愚言家（愚言家 + 铁好人）与阶段 4.7 丘比特奇缘（乌鸦 + 丘比特 + 暗恋者，屠城与第三方）已落地**；**阶段 5 永序之轮与阶段 6 镜隐迷踪（觉醒隐狼 + 魔镜少女）、猎日逐光（蚀日侍女 + 流光伯爵）已落地**；阶段 4.5/6.5/6.6 未开始）、创建向导重设计（已完成）、prompt 原文采集（已完成）、[统一模型调用](plans/2026-10-07-unified-model-invocation.md)（P0/P1 已落地；P2 未开始，P3 成果已落梯子未迁移，P4 决策点已接，P5 的票种口径与保留窗口已落、警长票失败可见性未落） | 想知道还剩什么没做的人 |
| [notes/](notes/) | 工作笔记；含评测快照与可视化讲解 | 按需 |
| [notes/2026-09-16-mimo-mixed-arena.md](notes/2026-09-16-mimo-mixed-arena.md) | 2026-09-16 本机混座评测快照（61 局完成） | 看历史评测结论 |
| [notes/2026-10-06-rule-execution-audit.md](notes/2026-10-06-rule-execution-audit.md) | 2026-10-06 最近 6 局规则执行漏洞审计 | 查规则实现缺陷 |
| [notes/2026-10-07-eval-review.md](notes/2026-10-07-eval-review.md) | **2026-10-07 最近两次评测复盘与逻辑/流程问题清单**（现行） | 看现行评测结论与指标口径陷阱 |
| [notes/2026-10-10-known-unfixed-and-metric-leftovers.md](notes/2026-10-10-known-unfixed-and-metric-leftovers.md) | 2026-10-10 盘点后修掉的十项：竞选自爆落 `night_deaths`、狼人节拍、竞选窗口快照、资源镜像、不可放弃强制；票种口径、新角色命中率、板型覆盖等 | 查这十项的症状/根因/门禁 |
| [notes/2026-10-20-role-portrait-inventory.md](notes/2026-10-20-role-portrait-inventory.md) | 2026-10-20 角色立绘盘点与替换记录（2026-10-21 已补齐 40/40） | 查立绘替换过程与历史缺口 |
| [project-analysis.md](project-analysis.md) | **2026-08-31 的历史快照**，不是当前状态 | 只作当时记录 |
| [superpowers/plans/](superpowers/plans/) | 历史功能实施计划（按日期归档） | 想了解功能演进的人 |
| [superpowers/specs/](superpowers/specs/) | 历史功能设计稿（按日期归档） | 想了解设计取舍的人 |

## 约定

- REST API 不在本库手工维护：启动后端后访问 `http://localhost:8000/docs`（FastAPI 自动生成的 OpenAPI 文档）。
- AI 编码助手入口：根目录 [`AGENTS.md`](../AGENTS.md)（Cursor / Codex / Gemini）与 [`CLAUDE.md`](../CLAUDE.md)（Claude Code）；两者描述同一套命令、架构与门禁，并与 architecture.md 对齐。运行经验与失败模式记录在 [`MEMORY.md`](../MEMORY.md)。
- `docs/superpowers/` 与 `project-analysis.md` 是按日期归档的设计稿/分析，不要当现行说明书改代码。**现行评测结论**以 [notes/2026-10-07-eval-review.md](notes/2026-10-07-eval-review.md)（最近两次运行的复盘与指标口径陷阱）与 [notes/2026-09-16-mimo-mixed-arena.md](notes/2026-09-16-mimo-mixed-arena.md)（混座方法论）为准。
- **现行实施计划**放 `docs/plans/`，每份文档头部写「已完成 / 未完成 / 实现偏差」；做完或收尾时同步头部状态，别让状态行留在开工前。`docs/superpowers/plans/` 只收历史归档。
- 修改架构或规则后，请同步更新本库对应文档以及 `AGENTS.md` / `CLAUDE.md`，避免再次出现文档与代码漂移。
