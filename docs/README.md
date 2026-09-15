# 文档索引

Wolf Killer 的文档库。新手从根目录 [README](../README.md) 开始；想深入再按需往下读。

| 文档 | 内容 | 读者 |
| --- | --- | --- |
| [gameplay.md](gameplay.md) | 游戏规则、角色能力、对局流程、胜负条件 | 玩家 / 想了解产品的人 |
| [architecture.md](architecture.md) | 模块划分、角色流水线、状态机、持久化、前端架构 | 开发者 |
| [development.md](development.md) | 环境变量、本地开发、测试与门禁、数据存储、踩坑记录 | 开发者 |
| [benchmark.md](benchmark.md) | 耐久 benchmark API/CLI、指标口径与导出 | 跑评测 / 看报告的人 |
| [notes/](notes/) | 工作笔记与可视化讲解（HTML） | 按需 |
| [project-analysis.md](project-analysis.md) | **2026-08-31 的历史快照**，不是当前状态 | 只作当时记录 |
| [superpowers/plans/](superpowers/plans/) | 历史功能实施计划（按日期归档） | 想了解功能演进的人 |
| [superpowers/specs/](superpowers/specs/) | 历史功能设计稿（按日期归档） | 想了解设计取舍的人 |

## 约定

- REST API 不在本库手工维护：启动后端后访问 `http://localhost:8000/docs`（FastAPI 自动生成的 OpenAPI 文档）。
- AI 编码助手入口：根目录 [`AGENTS.md`](../AGENTS.md)（Cursor / Codex / Gemini）与 [`CLAUDE.md`](../CLAUDE.md)（Claude Code）；两者描述同一套命令、架构与门禁，并与 architecture.md 对齐。运行经验与失败模式记录在 [`MEMORY.md`](../MEMORY.md)。
- `docs/superpowers/` 与 `project-analysis.md` 是按日期归档的设计稿/分析，不要当现行说明书改代码。
- 修改架构或规则后，请同步更新本库对应文档以及 `AGENTS.md` / `CLAUDE.md`，避免再次出现文档与代码漂移。
