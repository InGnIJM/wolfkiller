# 文档索引

Wolf Killer 的文档库。新手从根目录 [README](../README.md) 开始；想深入再按需往下读。

| 文档 | 内容 | 读者 |
| --- | --- | --- |
| [gameplay.md](gameplay.md) | 游戏规则、角色能力、对局流程、胜负条件 | 玩家 / 想了解产品的人 |
| [architecture.md](architecture.md) | 模块划分、角色流水线、状态机、持久化、前端架构 | 开发者 |
| [development.md](development.md) | 环境变量、本地开发、测试与门禁、数据存储、踩坑记录 | 开发者 |
| [notes/](notes/) | 工作笔记与可视化讲解（HTML） | 按需 |
| [superpowers/plans/](superpowers/plans/) | 历史功能实施计划（按日期归档） | 想了解功能演进的人 |
| [superpowers/specs/](superpowers/specs/) | 历史功能设计稿（按日期归档） | 想了解设计取舍的人 |

## 约定

- REST API 不在本库手工维护：启动后端后访问 `http://localhost:8000/docs`（FastAPI 自动生成的 OpenAPI 文档）。
- AI 编码助手入口：根目录 [`CLAUDE.md`](../CLAUDE.md)（架构速览与门禁规则，与 architecture.md 描述同一套架构）；运行经验与失败模式记录在 [`MEMORY.md`](../MEMORY.md)。
- 修改架构或规则后，请同步更新本库对应文档，避免再次出现文档与代码漂移。
