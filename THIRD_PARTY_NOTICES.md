# 第三方开源软件与资产声明 (Third-Party Notices)

本项目（Wolf Killer）在其开发、构建与运行过程中使用了以下第三方开源软件库、网络字体及部分开发资产。特在此说明其开源许可证及权利归属。

---

## 1. 项目核心许可证 (Project License)

Wolf Killer 源码基于 **Mozilla Public License Version 2.0 (MPL-2.0)** 发布。完整协议文本参见根目录 [LICENSE](LICENSE)。

---

## 2. 开源依赖库 (Open Source Dependencies)

本项目的依赖项均已审查，未引入任何强传染性 GPL / AGPL 协议依赖。以下为主要依赖组件及其许可证类型：

### 2.1 后端依赖 (Python)
| 软件包 | 主要用途 | 许可证 |
| --- | --- | --- |
| **FastAPI** | REST API 框架 | MIT |
| **Uvicorn** | ASGI 服务器 | BSD-3-Clause |
| **Pydantic** | 数据校验与模型序列化 | MIT |
| **LangChain** 生态 (`langchain`, `langchain-core`, `langchain-openai`, `langchain-anthropic`) | 大语言模型客户端与协议封装 | MIT |
| **HTTPX** | 异步 HTTP 客户端 | BSD-3-Clause |
| **WebSockets** | 实时事件通信 | BSD-3-Clause |
| **Cryptography** | API Key 加密与安全存储 | Apache-2.0 / BSD-3-Clause |
| **PyYAML** | 配置文件解析 | MIT |
| **python-dotenv** | 环境变量加载 | BSD-3-Clause |
| **pytest** / **pytest-cov** / **pytest-asyncio** | 自动化测试与覆盖率门禁 | MIT / Apache-2.0 |

### 2.2 前端依赖 (Node.js / npm)
| 软件包 | 主要用途 | 许可证 |
| --- | --- | --- |
| **React** / **React-DOM** | 用户界面核心库 | MIT |
| **Material UI (@mui/material, @mui/icons-material)** | UI 组件库与图标 | MIT |
| **Emotion (@emotion/react, @emotion/styled)** | CSS-in-JS 样式引擎 | MIT |
| **Zustand** | 轻量级状态管理 | MIT |
| **React Router** | 单页应用客户端路由 | MIT |
| **Vite** | 前端构建工具与开发服务器 | MIT |
| **lightningcss** (及各平台二进制分发包) | 高性能 CSS 转换工具 | **MPL-2.0** |
| **TypeScript** | 静态类型系统 | Apache-2.0 |
| **ESLint** | 代码规范检查 | MIT |
| **Vitest** | 前端单元测试框架 | MIT |
| **Playwright** | 端到端浏览器验收测试 | Apache-2.0 |

> **注**：本项目前端构建链依赖的 `lightningcss` 原生遵循 MPL-2.0 协议，与本项目主许可证高度自洽。

---

## 3. 字体资产 (Fonts)

本项目前端页面（`frontend/index.html`）通过 Google Fonts CDN 引用了以下两款开源衬线字体，仅通过网络字体形式加载：
- **Cinzel** (Designed by Natanael Gama) — 遵循 [SIL Open Font License 1.1 (OFL-1.1)](https://openfontlicense.org/)
- **Noto Serif SC** (Designed by Google Fonts) — 遵循 [SIL Open Font License 1.1 (OFL-1.1)](https://openfontlicense.org/)

本项目 Git 仓库未内置或直接分发上述字体的二进制文件（`.woff2` / `.ttf`），无二次分发字体文件的法律义务。

---

## 4. 美术与多媒体资产说明 (Media & Artwork)

### 4.1 自制角色立绘与卡背素材
- 路径：`frontend/src/assets/roles/role-*.webp`（40 张角色立绘）、`frontend/src/assets/roles/card-back.webp`、`frontend/src/assets/roles-original/*.webp`（10 张旧版备份）。
- 来源：由本项目作者（金铭）通过 AI 辅助生成，并进行手工筛选、裁切、调色及细节修正。
- 权利说明：版权由 金铭 所有，随本项目 MPL-2.0 开源分发。

### 4.2 游戏大厅与回放界面背景与截图
- 路径：`frontend/src/assets/bg/*.webp`、`frontend/src/assets/hero.png`、`docs/assets/*.png`。
- 来源：本项目自研系统运行截图与原创主题设计背景图。

### 4.3 网易官方立绘（已退出仓库）
- 2026-10-21 起，活动目录 `frontend/src/assets/roles/` 只保留自制立绘与卡背；网易官方立绘不再随仓库分发。
- `scripts/fetch-official-role-art.py` 为遗留参考脚本，正常路径会跳过已有文件；`--force` 会用网易官方占位图覆盖自制立绘，禁止在发布分支运行。
- 官方美术资产的权利归网易公司所有；本项目不再分发该批素材。

### 4.4 初始化模板残留图标
- 路径：`frontend/src/assets/react.svg`、`frontend/src/assets/vite.svg`、`frontend/public/icons.svg`、`frontend/public/favicon.svg`。
- 来源：前端项目初始化时由脚手架模板生成，仅作为基础演示与图标占位符。
