# 模型可视化配置 与 新建游戏配置向导 设计

日期：2026-08-16
状态：已确认（分节确认完毕，待用户审阅书面规格）

## 1. 背景与目标

Wolf Killer 目前所有 LLM 调用都走 `backend/.env` 里的全局单一配置（base_url / api_key / 模型列表），创建游戏只有一个硬编码 5 角色数量的弹窗。本次要交付两个功能：

1. **前端页面可视化配置模型**：配置模型 URL、模型 ID、API Key，持久化存储，供游戏创建时选用。
2. **新建游戏两步配置向导**：第一步人数身份配置（九人标准场 / 十人标准场 / 自定义场，支持角色加减），第二步 Agent 模型配置（从已配置模型中选择；可当场新建模型配置）。

核心诉求：**可扩展**——后续会加入更多角色与标准场。

## 2. 需求澄清结论（决策记录）

| # | 问题 | 决策 |
|---|---|---|
| 1 | 模型配置存储 | JSON 文件起步（`backend/data/models.json`）+ 存储接口抽象，预留 SQLite |
| 2 | API Key 处理 | 本地密钥（机器指纹派生）可逆加密存储；API 返回永远脱敏；编辑留空=保留原 key |
| 3 | 模型配置字段 | 三字段 url / 模型 id / apikey + 可选高级项（temperature、严格模式地址，留空=按厂商推导：DeepSeek 官方 → /beta，其它厂商 → base_url 本身；2026-08-17 由根因修复修订，原"沿用 .env"规则会跨厂商误调 DeepSeek） |
| 4 | 角色/预设数据来源 | 后端 API 驱动（角色目录 + 标准场预设 + 硬约束都从后端下发） |
| 5 | 模型分配方式 | 按数量随机落座（二期）；一期整局一个模型 |
| 6 | 前端导航 | 引入 react-router |
| 7 | 无配置兜底 | 保留 .env 环境默认选项，创建向导里永远可选 |
| 8 | 向导形态 | 独立页面 `/create`（两步 Stepper），不用弹窗 |
| 9 | 自定义场约束 | 后端硬约束（至少 1 狼、好人营至少 1 人、总人数区间），数值后端可调、随接口下发 |
| 10 | 实施方案 | 方案 B 分两期（先单模型可用，二期做多模型数量分配） |

## 3. 分期交付

### 第一期（本次）
- 后端模型配置域：存储接口 + JSON 实现、key 加密/脱敏、CRUD API、连接测试
- 后端目录域：角色目录 / 标准场预设 / 硬约束 API
- 引擎：`LLMClient` 显式配置化；`create_game` 接受 `model_assignments`（**契约按数组设计**），一期恰好 1 条且 count=总人数
- 前端：react-router 四路由；模型管理页；两步创建向导

### 第二期（后续）
- 第二步改为每个模型配数量（多条 `model_assignments`，count 之和=总人数）
- 后端按数量随机物化到座位；夜晚管道 / NightDirector 按座位取各自模型
- 存档记录每座位模型快照
- **一期已把"单一 client"写成 client 提供器（provider）结构，二期只换提供器实现，游戏域零改动**

## 4. 架构总览

```
后端
├── app/stores/model_config_store.py     # ModelConfigStore 接口 + JsonModelConfigStore（原子写）
├── app/stores/model_key_crypto.py       # 机器指纹派生密钥 + Fernet 可逆加密 + 脱敏
├── app/catalog.py                       # 角色中文名/图标/描述元数据表 + 标准场预设 + 硬约束常量
├── app/api/routes/model_routes.py       # /api/models CRUD + test
├── app/api/routes/catalog_routes.py     # /api/catalog/*（角色/预设/约束）
├── app/agents/llm_client.py             # LLMClientConfig 显式配置化改造
└── app/services/game_service.py         # model_assignments 解析 → client 提供器；manifest 模型快照

前端
├── src/App.tsx                          # react-router：/ /models /create /game/:gameId
├── src/api/client.ts                    # 新增 models / catalog API 调用
├── src/store/modelConfigStore.ts        # Zustand：模型配置 CRUD 状态
├── src/components/models/ModelConfigPage.tsx
├── src/components/models/ModelConfigDialog.tsx
├── src/components/create/CreateGameWizard.tsx
├── src/components/create/RoleStep.tsx
└── src/components/create/ModelStep.tsx
```

边界原则：模型配置域与游戏域解耦——游戏创建时把模型选择**物化**为引擎内配置快照，此后删除/修改模型配置不影响运行中的游戏。

## 5. 数据模型与存储

### ModelConfig（内部模型）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | str | uuid4 hex |
| name | str | 显示名，非空、唯一、≤50 字符 |
| base_url | str | 必须 http/https |
| model_id | str | 非空 |
| api_key_encrypted | str | Fernet token（密文落盘，非明文） |
| temperature | float \| None | 可选，0~2 |
| strict_base_url | str \| None | 可选，留空=按厂商推导（DeepSeek 官方 → /beta，其它厂商 → base_url 本身；见风险 1） |
| created_at / updated_at | str | ISO8601 UTC |

### 存储文件
`backend/data/models.json`：`{"version": 1, "configs": [...]}`。`backend/.gitignore` 已有 `data/*`，无需新增忽略规则（`models.json` 含密文，确认不提交）。

### 存储接口
```python
class ModelConfigStore(Protocol):
    def list_all(self) -> list[ModelConfig]: ...
    def get(self, config_id: str) -> ModelConfig | None: ...
    def upsert(self, config: ModelConfig) -> None: ...
    def delete(self, config_id: str) -> bool: ...
```
- 第一个实现 `JsonModelConfigStore`：临时文件写入 + `os.replace` 原子替换；进程内 `threading.RLock` 保护
- 未来 `SqliteModelConfigStore` 实现同一协议，通过工厂函数按环境变量切换（一期不实现）
- 单 uvicorn 进程部署下无跨进程并发写；多 worker 场景靠 SQLite 实现解决（见风险 3）

### Key 加密
- 密钥 = SHA-256(`uuid.getnode()` + 主机名) → base64 → Fernet key；无独立密钥文件（不存在密钥文件丢失问题）
- 解密失败（换机器/改主机名）→ 该配置在 API 中标记 `key_invalid=true`，前端提示重输 key，不影响其他配置
- 安全级别诚实声明：**防顺手翻本地文件**；防不了本机管理员与有代码访问权的人（与 .env 现状一致）
- 脱敏规则：`sk-***` + 末 4 位（<8 位则整体 `***`）；日志、异常、错误信息严禁打印 key

## 6. API 设计

### 6.1 模型配置 `/api/models`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/models` | 列表（key 永远脱敏） |
| POST | `/api/models` | 新建；校验 URL 协议、name/model_id 非空；name 重复 → 409 |
| GET | `/api/models/{id}` | 详情（脱敏） |
| PUT | `/api/models/{id}` | 更新；`api_key` 缺省/空 = 保留原 key |
| DELETE | `/api/models/{id}` | 删除；不影响运行中/历史游戏（快照已物化） |
| POST | `/api/models/test` | 连接测试：探测游戏真实调用的两个端点（普通端点 + 按厂商推导的 strict 端点，DeepSeek 官方二者不同），`max_tokens=1` + 10s 超时；入参二选一：`{id}`（key 用已存密文）或 `{base_url, api_key, model_id}`（未保存的表单试连）；strict 失败时 error 带 `strict:` 前缀；返回 `{ok, latency_ms, error}` |

`ModelConfigResponse`：`id, name, base_url, model_id, has_key, api_key_masked, key_invalid, temperature, strict_base_url, created_at, updated_at`。**响应绝不包含明文 key**（隐私扫描测试强制）。

### 6.2 目录 `/api/catalog`
- `GET /api/roles`：角色目录，来自 `builtin_registry.freeze()` 快照（`role_id, display_name, camp_id, min_count, max_count, dependencies, exclusions`）+ `app/catalog.py` 元数据表（中文名、图标 key、描述）。**不改动冻结 RoleSpec**（改 spec 会变 registry digest，破坏旧存档校验）；元数据表缺项时回退到 spec 的 `display_name`
- `GET /api/presets`：标准场预设列表 `{id, name, description, role_counts}`——九人标准场（3狼3民1预言家1女巫1猎人）、十人标准场（+1守卫）；加预设只改 `app/catalog.py`
- `GET /api/constraints`：`{min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1}`，常量集中定义、可调

### 6.3 创建游戏 `POST /api/games` 扩展
```python
class ModelAssignment(BaseModel):
    config_id: str | None   # None = .env 环境默认
    count: int              # 一期：恰好 1 条且 count == 总人数；二期：多条、count 之和 == 总人数

class CreateGameRequest(BaseModel):
    # ...现有字段不变
    model_assignments: list[ModelAssignment] | None = None
```
- 校验（后端兜底）：一期 `len == 1` 且 `count == total_players`；`config_id` 非 None 时必须存在且 `key_invalid == False`，否则 400；不传 `model_assignments` = 环境默认（向后兼容）
- 响应新增 `model_snapshot: [{config_id, name, model_id, base_url}]`（无 key）
- 旧档无该字段 → 前端显示"未知"；manifest 持久化模型快照

## 7. 引擎改造（一期）

1. 新增 `LLMClientConfig`（frozen dataclass）：`base_url / api_key / model_id / temperature / max_tokens / strict_base_url`
2. `LLMClient(config: LLMClientConfig | None = None)`：None 时从 `.env` 构造（`env_default_client_config()` 工厂）；`get_model / get_model_with_temperature / get_model_with_tools / get_model_with_action_tool` 全部改用实例配置，不再读全局 `app_config.llm`
3. `GameService.create_game(..., model_assignments=None)`：解析为一个 `LLMClientConfig`；构造 `client_provider: Callable[[int], LLMClient]`（一期返回同一实例，二期按座位路由）
4. `_create_roles` 的 `llm_client_factory` 改为 `lambda seat: client_provider(seat)`；Scheduler 的 `_command_provider` 接受 provider，按 `request.actor_seat` 取 client；NightDirector 的 `_night_invoke` 使用 provider(0)（一期单模型语义无差异）
5. manifest 记录 `model_snapshot`；`llm_client.py` / `game_service.py` 不在 5 个核心 blob 门禁（`test_guard_extension.py`）内，改造不触犯门禁，但必须全量跑测试确认

## 8. 前端设计

### 路由（react-router）
- `/` 大厅（GameList，迁移现有逻辑）；`/models` 模型管理页；`/create` 创建向导；`/game/:gameId` 对局（GameBoard 用 `useParams`）
- AppBar：标题（回大厅）+「模型管理」入口；原 `#gameId` 芯片由路由参数取代

### 模型管理页 `/models`
- 配置卡片列表：名称、model_id、base_url、key 脱敏状态（`sk-***abcd` / 未设置 / 密钥失效）、更新时间；操作：测试 / 编辑 / 删除
- 空状态说明：「环境默认 (.env)」始终可在创建游戏时选用
- 新建/编辑对话框 `ModelConfigDialog`：名称 / Base URL / 模型 ID / API Key（password，占位提示"留空=保留原 key"）/ 高级选项折叠（temperature、严格模式地址）；按钮：测试连接 / 取消 / 保存；连接测试失败不阻止保存
- **保存强制（2026-08-17 修订）**：「保存」在连接测试通过前禁用（提示"保存前需通过连接测试"）；测试通过后若改动 Base URL / 模型 ID / API Key / 严格模式地址任一连通性字段，需重新测试才能保存

### 创建向导 `/create`（两步 Stepper）
- **第 1 步 RoleStep**：三张预设卡片（九人场/十人场/自定义场，来自 `/api/presets`）→ 角色加减列表（来自 `/api/roles`：图标、中文名、阵营 chip、`− n +`，按角色 min/max 与全局约束禁用按钮）；**选标准场后再加减角色 = 自动切换为"自定义场"**；底部显示"共 N 人"+「下一步」
- **第 2 步 ModelStep**：「环境默认 (.env)」卡片置顶（永远可选）→ 已存配置单选卡片（key_invalid 的置灰）→ 虚线「+ 当场新建模型配置」（弹 ModelConfigDialog，保存后刷新列表并自动选中）；「上一步 / 创建游戏」→ `POST /api/games` 携带 `model_assignments: [{config_id|null, count: 总人数}]` → 跳转 `/game/:id`
- 一期单选；二期同位置换成每模型数量 stepper，布局与接口不变

### 组件复用与规范
- 复用 `RoleIcon`（按目录下发的 icon key 扩展守卫图标）、现有 MUI 主题与设计 token
- 角色图标与全局"Material Symbols 禁 emoji"规范存在冲突（现状用 emoji），实施时确认处理方式（见风险 9）

## 9. 校验与错误处理
- 前后端双校验；前端即时提示，后端 400 兜底
- key 解密失败 → `key_invalid` 标记，UI 提示重输，不影响其他配置
- 连接测试失败不阻止保存，结构化错误（超时/4xx/解析失败）展示
- 删除模型配置不影响任何已有游戏（创建时物化）
- `models.json` 原子写（临时文件 + `os.replace`），崩溃不损坏

## 10. 测试策略

### 后端（新增，100% statement/branch 门禁）
- `test_model_config_store.py`：CRUD、原子写、version 字段、损坏文件容错
- `test_model_key_crypto.py`：加解密往返、解密失败、脱敏边界（短 key）
- `test_model_routes.py`：CRUD、name 重复 409、**响应 JSON 全程无明文 key 的隐私扫描**
- `test_catalog_routes.py`：角色目录与注册表一致性、预设/约束内容
- `test_game_creation_validation.py`：model_assignments 一期校验（数量/存在性/key_invalid）、响应快照无 key
- `test_llm_client_config.py`：显式配置构造、env 兜底、四个 get_model* 用实例配置
- 存量 1331 个测试全绿；`--cov-fail-under=100`

### 前端（Vitest）
- `modelConfigStore` 状态流转与错误处理
- `ModelConfigDialog` 表单校验（URL 协议、必填、留空保留 key 语义）
- 向导：预设切换、自定义场加减边界、自动切换自定义场、模型单选
- `client.ts` 新接口的 fetch mock
- 存量 57 个测试全绿；现有组件测试因路由引入需 MemoryRouter 包裹

## 11. 风险与潜在问题

1. **strict 地址缺省回退（2026-08-17 已修）**：原规则"留空沿用 .env strict 地址"会在非 DeepSeek 厂商配置上把请求误发到 DeepSeek beta（实测 401）→ 改为按厂商推导（DeepSeek 官方 → /beta，其它 → base_url 本身），连接测试同步探测 strict 端点；引擎仍有 StrictCapabilityError → JSON 降级兜底 🟢
2. **推理型模型需更大输出预算（2026-08-17 实测）**：小米 MiMo 等推理模型思考消耗大量 token，`LLM_MAX_TOKENS`=1024 时真实讨论提示词下实测 5/5 `finish_reason=length`（输出空或截断 → JSONDecodeError，引擎降级为狼跳过/弃权）；调到 4096 后 3/3 正常。`.env` 已设 `LLM_MAX_TOKENS=4096`；配置推理型模型时必须留足思考预算 🟢
3. **key 加密机器绑定**：换机器/改主机名后 key 解不开需重输 🟡（文档写明）
4. **多进程并发写 models.json**：单 uvicorn 进程无碍；多 worker 时靠预留的 SQLite 实现 🟢
5. **泄露面**：GET 脱敏 + 日志禁打 key + 错误不回显 key 🟢
6. **二期兼容**：`model_assignments` 一期就是数组结构，二期放宽为多条；manifest 模型快照同为数组——无迁移 🟢
7. **路由引入冲击现有测试**：GameList 等组件用 Link/useNavigate 后单测需 Router 包裹，可控工作量 🟡
7. **核心模块门禁**：`llm_client.py` / `game_service.py` 不在 5 个核心 blob 门禁内，改造不触犯 `test_guard_extension.py`；实施时跑全量门禁确认 🟢
8. **旧存档兼容**：不动 registry / effect schema，旧档继续可回放；旧档缺模型快照字段显示"未知" 🟢
9. **图标规范冲突**：全局规范要求 Material Symbols 禁 emoji，现有 `RoleIcon` 用 emoji；实施时二选一（迁移图标库或维持现状豁免），不阻塞本设计 🟡
10. **`.env` 已在仓库**：现有 key 已暴露过，建议另行轮换（不在本次范围） 🟡

## 12. 明确不做（YAGNI）
- SQLite 存储实现（仅预留接口）
- 二期多模型数量落座与按座位快照
- 标准场预设的 UI 编辑
- 模型按角色绑定 / 指定到座位
- 多用户、鉴权、key 轮换与审计
- 模型配置导入导出
