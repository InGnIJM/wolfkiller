# ProviderProfile 模型接口层设计

日期：2026-08-23  
状态：用户已确认

## 1. 结论

WolfKiller 保留现有游戏引擎、角色、动作契约、调度器、提示词和前端，仅改造模型接口层。实现借鉴 Hermes Agent 的 `ProviderProfile + Transport` 结构，但不安装、不启动、不嵌入完整 Hermes Agent。

`LLMClient` 继续作为兼容门面，现有调用方仍使用：

```python
get_model()
get_action_model()
get_model_with_temperature(temperature)
get_model_with_tools(tools)
get_model_with_action_tool(contract)
map_strict_capability_error(error)
```

因此 `backend/app/core/**`、`backend/app/roles/**`、动作契约和游戏流程无需重写。

## 2. 目标

1. 不再假定所有模型都支持 OpenAI strict tool calling。
2. 将厂商识别、请求参数、能力声明、端点选择和错误映射集中在模型接口层。
3. 让 OpenAI、DeepSeek、OpenRouter 及自定义 OpenAI-compatible 服务使用声明式 Profile。
4. 为未来 Anthropic Messages、Responses API 等 Transport 留出稳定扩展点。
5. 保持旧模型配置和运行中游戏快照向后兼容。
6. 修复推理模型因统一 768 action token 上限而出现的空响应或截断问题。

## 3. 明确不做

- 不接入 Hermes Agent 的 Agent Loop、memory、skills 或 tools。
- 不修改游戏规则、角色行为、提示词和动作 Schema。
- 不做跨厂商自动切换；一次游戏继续使用创建时物化的模型配置。
- 不修改前端；新增字段通过后端默认值和自动识别工作。
- 本期不实现 Anthropic 原生 Transport，只定义可扩展接口并实现当前所需的 OpenAI-compatible Transport。

## 4. 架构

```text
GameService / BaseRole（保持现状）
              │
              ▼
      LLMClient 兼容门面
              │
              ├── ProviderRegistry：识别 profile
              ├── ProviderProfile：声明能力和参数策略
              └── ModelTransport：构造厂商请求
                         │
                         ▼
              OpenAICompatibleTransport
                         │
                         ▼
          OpenAI / DeepSeek / OpenRouter / Custom
```

### 4.1 ProviderProfile

```python
@dataclass(frozen=True)
class ModelCapabilities:
    tools: bool
    strict_tools: bool
    json_output: bool
    reasoning_effort: bool
    temperature: bool


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    api_mode: str
    capabilities: ModelCapabilities
    default_action_max_tokens: int
    strict_endpoint: bool = False
```

Profile 只描述能力和策略，不保存 API Key，不引用游戏类型。

### 4.2 内置 Profile

| Profile | 自动识别 | strict tools | temperature | action token 策略 |
|---|---|---:|---:|---|
| `openai` | `api.openai.com` | 是 | 是 | 使用配置预算 |
| `deepseek` | `api.deepseek.com` | 是，动作走 `/beta` | 是 | 使用配置预算 |
| `openrouter` | `openrouter.ai` | 默认否 | 由请求保留 | 不设 768 硬上限 |
| `custom-openai` | 其他 HTTP(S) 地址 | 默认否 | 是 | 使用配置预算 |

未知模型采用保守能力：可以文本调用，但不主动发送 strict tool 参数。这样 Ox Alpha 等模型会直接进入现有 JSON action 路径，而不是先必然失败一次。

### 4.3 Transport

```python
class ModelTransport(Protocol):
    def build(
        self,
        config: LLMClientConfig,
        profile: ProviderProfile,
        purpose: CallPurpose,
        *,
        temperature: float | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        strict: bool = False,
    ) -> BaseChatModel: ...
```

本期 `OpenAICompatibleTransport` 继续使用 `ChatOpenAI`，但只发送 Profile 允许的参数：

- `temperature=False` 时完全省略 temperature，而不是发送 `None`。
- action token 使用 `LLMClientConfig.action_max_tokens`，不再经过全局 768 上限。
- strict action 只在 `profile.capabilities.strict_tools=True` 时构造。
- DeepSeek 官方 strict action 才使用派生的 `/beta` 端点。
- 普通文本、JSON action、tools action 使用各自明确的 timeout/token policy。

## 5. 配置兼容

`ModelConfig`、`ModelConfigRequest/Response` 和 `LLMClientConfig` 增加：

```python
provider_profile: str = "auto"
```

兼容规则：

1. 旧 `models.json` 没有该字段时，由 dataclass 默认值补为 `auto`。
2. `auto` 根据规范化后的 `base_url` hostname 解析。
3. 旧 `strict_base_url` 保留；只有支持 strict endpoint 的 Profile 使用。
4. 游戏模型快照增加解析后的 `provider_profile`，但不保存 API Key。
5. 未识别的显式 Profile 在保存/更新时返回 422，不静默退回其他厂商。

## 6. 调用策略

### 文本调用

`get_model()` 和 `get_model_with_temperature()` 由 Transport 构造普通文本模型；不改变调用方返回类型。

### 动作调用

1. `supports_strict_actions` 直接读取 Profile 能力。
2. 支持 strict 的 Profile：沿用现有 strict tool action。
3. 不支持 strict 的 Profile：现有 `BaseRole` 直接使用 JSON action model。
4. strict 请求收到明确 400/422 能力拒绝：继续映射为 `StrictCapabilityError`，让既有逻辑执行一次 JSON fallback。
5. 认证、限流、超时和服务器错误不得误判成能力不支持。

### Token 与推理模型

- 删除 `_ACTION_MAX_TOKENS = 768`。
- action 输出预算以配置值为准，缺省继续为 2048。
- Profile 可以设置缺省值，但不得把用户显式配置无声压低。
- 本期不自行猜测或改写 reasoning effort；接口预留能力字段，后续按模型 Profile 增加映射。

## 7. 模型连接测试

`POST /api/models/test` 不再直接构造 `ChatOpenAI`，统一经 `LLMClient`：

1. 文本探测：验证 endpoint、认证、model id 和基础响应。
2. 能力报告：返回 Profile 声明的 `tools/strict_tools/json_output/reasoning_effort/temperature`。
3. 若 Profile 声明 strict endpoint，额外探测该端点。
4. 探测不修改或持久化 Profile，不把临时网络故障写成永久能力结论。
5. 错误响应只返回分类名，不回显 API Key、请求体或厂商原始响应正文。

## 8. 错误与回退边界

```text
strict 400/422 capability rejection -> StrictCapabilityError -> JSON action
timeout / 429 / 5xx / auth error       -> 原错误 -> 现有安全动作 fallback
invalid JSON / contract violation      -> 现有 parser/validator -> 安全动作 fallback
```

本期不引入跨 Provider fallback，避免一局中途切换模型导致行为不可复现。

## 9. 修改边界

允许修改：

- `backend/app/agents/llm_client.py`
- `backend/app/agents/providers/**`
- `backend/app/stores/model_config_store.py`
- `backend/app/api/model_schemas.py`
- `backend/app/api/routes/model_routes.py`
- `backend/app/services/game_service.py` 中 `resolve_model_config()` 和模型快照字段
- 与上述文件对应的测试

禁止修改：

- `backend/app/core/**`
- `backend/app/roles/**`
- `backend/app/models/contracts.py`
- `backend/app/models/pipeline.py`
- 所有角色提示词、规则数据和前端文件

## 10. 验收标准

1. 旧模型配置无需迁移脚本即可读取。
2. Ox Alpha/OpenRouter 不再尝试 strict tool，动作直接使用 JSON 路径。
3. DeepSeek 官方模型仍使用 `/beta` strict action。
4. action token 不再被硬截为 768。
5. `LLMClient` 所有现有公开方法签名不变。
6. 游戏核心、角色和前端无修改。
7. 新增与受影响代码具备 happy path、边界和异常路径测试。
8. `pytest --cov=app --cov-branch --cov-report=term-missing` 达到 100%。


