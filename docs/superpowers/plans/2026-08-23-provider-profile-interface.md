# ProviderProfile 模型接口层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改游戏核心、角色和前端的前提下，将 `LLMClient` 改造成 Hermes 风格的 ProviderProfile/Transport 兼容门面，并修复不同厂商模型统一 strict tools 和 768 action token 上限造成的 fallback。

**Architecture:** 新增独立 `app/agents/providers/`，由 `ProviderRegistry` 解析声明式 `ProviderProfile`，由 `OpenAICompatibleTransport` 按调用目的构造 `BaseChatModel`。现有 `LLMClient` 公开接口保持不变，只在内部委托 Profile/Transport；模型配置增加向后兼容的 `provider_profile="auto"`。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic v2、LangChain `BaseChatModel`、`ChatOpenAI`、pytest、pytest-cov。

**Spec:** `docs/superpowers/specs/2026-08-23-provider-profile-interface-design.md`

## Global Constraints

- 不修改 `backend/app/core/**`、`backend/app/roles/**`、`backend/app/models/contracts.py`、`backend/app/models/pipeline.py`、前端和提示词。
- 不安装或运行完整 Hermes Agent；不复制 Hermes 源码。
- `LLMClient` 现有公开方法签名保持不变。
- 旧 `models.json` 缺少新字段时必须继续可读。
- 不得在日志、异常或 API 响应中返回 API Key 或厂商响应正文。
- TDD 顺序固定为 Red → Green → Refactor。
- 测试放在被测模块相邻的 `test/` 子目录。
- 每个 commit 不超过 3 个文件，message 使用 `<type>(<scope>): <description>`。
- 最终运行 `pytest --cov=app --cov-branch --cov-report=term-missing` 并达到 100%。

---

### Task 1: 定义 ProviderProfile 与自动解析注册表

**Files:**
- Create: `backend/app/agents/providers/base.py`
- Create: `backend/app/agents/providers/registry.py`
- Test: `backend/app/agents/providers/test/test_registry.py`

**Interfaces:**
- Produces: `CallPurpose`, `ModelCapabilities`, `ProviderProfile`, `ProviderRegistry.resolve(profile_id, base_url, model_id)`。
- Consumes: 无游戏域类型。

- [ ] **Step 1: 写失败测试**

创建 `backend/app/agents/providers/test/test_registry.py`，覆盖自动识别、保守默认和非法显式 Profile：

```python
import pytest

from app.agents.providers.registry import ProviderRegistry


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.openai.com/v1", "openai"),
        ("https://api.deepseek.com", "deepseek"),
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("http://127.0.0.1:8000/v1", "custom-openai"),
    ],
)
def test_auto_profile_uses_exact_hostname(url, expected):
    assert ProviderRegistry().resolve("auto", url, "model").profile_id == expected


def test_openrouter_is_conservative_about_strict_tools():
    profile = ProviderRegistry().resolve("auto", "https://openrouter.ai/api/v1", "xiaomi/mimo-v2-omni")
    assert profile.capabilities.tools is True
    assert profile.capabilities.strict_tools is False


def test_deepseek_enables_strict_endpoint():
    profile = ProviderRegistry().resolve("auto", "https://api.deepseek.com", "deepseek-chat")
    assert profile.capabilities.strict_tools is True
    assert profile.strict_endpoint is True


def test_deepseek_like_hostname_does_not_match():
    profile = ProviderRegistry().resolve("auto", "https://deepseek.example.com/v1", "model")
    assert profile.profile_id == "custom-openai"


def test_unknown_explicit_profile_is_rejected():
    with pytest.raises(ValueError, match="unknown provider profile"):
        ProviderRegistry().resolve("missing", "https://example.test/v1", "model")
```

- [ ] **Step 2: 运行并确认失败**

Run: `cd backend; python -m pytest app/agents/providers/test/test_registry.py -q`  
Expected: FAIL，`app.agents.providers` 不存在。

- [ ] **Step 3: 实现不可变能力类型和内置注册表**

`base.py` 定义：

```python
from dataclasses import dataclass
from enum import StrEnum


class CallPurpose(StrEnum):
    TEXT = "text"
    ACTION_JSON = "action_json"
    TOOLS = "tools"
    ACTION_STRICT = "action_strict"


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

`registry.py` 使用 `urlparse(base_url).hostname` 精确匹配 `api.openai.com`、`api.deepseek.com`、`openrouter.ai`；其余地址返回 `custom-openai`。内置 Profile 均使用 `api_mode="chat_completions"`，OpenRouter 和 custom 的 `strict_tools=False`，DeepSeek 的 `strict_endpoint=True`。

- [ ] **Step 4: 运行测试并确认通过**

Run: `cd backend; python -m pytest app/agents/providers/test/test_registry.py -q`  
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/providers/base.py backend/app/agents/providers/registry.py backend/app/agents/providers/test/test_registry.py
git commit -m "feat(provider): add declarative provider profiles"
```

---

### Task 2: 实现 OpenAI-compatible Transport 参数策略

**Files:**
- Create: `backend/app/agents/providers/openai_compatible.py`
- Create: `backend/app/agents/providers/test/test_openai_compatible.py`
- Modify: `backend/app/agents/providers/base.py`

**Interfaces:**
- Consumes: `CallPurpose`, `ProviderProfile`，以及具备 `base_url/api_key/model_id/temperature/max_tokens/action_max_tokens/action_timeout_seconds/action_retry_timeout_seconds/strict_base_url` 属性的配置。
- Produces: `OpenAICompatibleTransport.build(...) -> BaseChatModel`。

- [ ] **Step 1: 写失败测试**

测试必须 mock `ChatOpenAI` 并验证：

```python
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.providers.base import CallPurpose
from app.agents.providers.openai_compatible import OpenAICompatibleTransport
from app.agents.providers.registry import ProviderRegistry


def config(**overrides):
    values = dict(
        base_url="https://openrouter.ai/api/v1", strict_base_url="https://unused.test/beta",
        api_key="secret", model_id="xiaomi/mimo-v2-omni", temperature=0.7,
        max_tokens=4096, action_max_tokens=2048,
        action_timeout_seconds=90.0, action_retry_timeout_seconds=120.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_action_budget_is_not_capped_at_768():
    profile = ProviderRegistry().resolve("openrouter", config().base_url, config().model_id)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(config(), profile, CallPurpose.ACTION_JSON)
    assert chat.call_args.kwargs["max_tokens"] == 2048


def test_strict_action_uses_deepseek_strict_endpoint():
    cfg = config(base_url="https://api.deepseek.com", strict_base_url="https://api.deepseek.com/beta")
    profile = ProviderRegistry().resolve("deepseek", cfg.base_url, "deepseek-chat")
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile, CallPurpose.ACTION_STRICT)
    assert chat.call_args.kwargs["base_url"] == "https://api.deepseek.com/beta"


def test_temperature_is_omitted_when_profile_forbids_it():
    profile = ProviderRegistry().resolve("openai", "https://api.openai.com/v1", "model")
    profile = profile.with_capabilities(temperature=False)
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(config(), profile, CallPurpose.TEXT)
    assert "temperature" not in chat.call_args.kwargs
```

- [ ] **Step 2: 运行并确认失败**

Run: `cd backend; python -m pytest app/agents/providers/test/test_openai_compatible.py -q`  
Expected: FAIL，Transport 或 `with_capabilities` 尚不存在。

- [ ] **Step 3: 实现参数构造**

在 `ProviderProfile` 增加不可变 helper：

```python
def with_capabilities(self, **changes: bool) -> "ProviderProfile":
    from dataclasses import replace
    return replace(self, capabilities=replace(self.capabilities, **changes))
```

Transport 先构造公共 kwargs，再按 `CallPurpose` 选择 token、timeout、base_url。只有 Profile 允许 temperature 时才写入该键。`ACTION_STRICT` 且 `strict_endpoint=True` 时使用 `strict_base_url`；所有 action purpose 使用 `action_max_tokens`，不得存在 768 常量。

- [ ] **Step 4: 运行测试并确认通过**

Run: `cd backend; python -m pytest app/agents/providers/test/test_openai_compatible.py -q`  
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/providers/base.py backend/app/agents/providers/openai_compatible.py backend/app/agents/providers/test/test_openai_compatible.py
git commit -m "feat(provider): build profile-aware OpenAI transport"
```

---

### Task 3: 将 LLMClient 改为兼容门面

**Files:**
- Modify: `backend/app/agents/llm_client.py`
- Create: `backend/app/agents/test/test_llm_client_profiles.py`
- Modify: `backend/app/agents/test/test_llm_client.py`

**Interfaces:**
- Consumes: `ProviderRegistry`、`OpenAICompatibleTransport`。
- Produces: 与当前完全一致的 `LLMClient` 公开方法；`LLMClientConfig.provider_profile` 新增默认值 `auto`。

- [ ] **Step 1: 写失败兼容测试**

新增测试覆盖：

```python
def test_openrouter_skips_strict_action_attempt(openrouter_config):
    client = LLMClient(config=openrouter_config)
    assert client.supports_strict_actions is False


def test_deepseek_keeps_strict_action_attempt(deepseek_config):
    client = LLMClient(config=deepseek_config)
    assert client.supports_strict_actions is True


def test_explicit_profile_overrides_hostname(openrouter_config):
    client = LLMClient(config=replace(openrouter_config, provider_profile="custom-openai"))
    assert client.provider_profile.profile_id == "custom-openai"


def test_action_model_uses_full_configured_budget(openrouter_config):
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        LLMClient(config=openrouter_config).get_action_model()
    assert chat.call_args.kwargs["max_tokens"] == openrouter_config.action_max_tokens
```

存量测试继续验证 `get_model_with_action_tool()` 绑定唯一 contract tool、`map_strict_capability_error()` 仅映射 400/422，以及普通模型方法返回 `BaseChatModel` 兼容对象。

- [ ] **Step 2: 运行并确认失败**

Run: `cd backend; python -m pytest app/agents/test/test_llm_client_profiles.py app/agents/test/test_llm_client.py -q`  
Expected: FAIL，配置没有 `provider_profile` 且 OpenRouter 仍报告 strict。

- [ ] **Step 3: 最小改造 LLMClient**

删除 `_ACTION_MAX_TOKENS` 和重复的 `ChatOpenAI(...)` 构造。构造函数解析一次 Profile 并保存 Transport：

```python
self.provider_profile = ProviderRegistry().resolve(
    self._config.provider_profile,
    self._config.base_url,
    self.model_name,
)
self._transport = OpenAICompatibleTransport()
```

各公开方法仅选择 `CallPurpose` 并委托 `build()`；`get_model_with_tools()` 和 `get_model_with_action_tool()` 在 Transport 返回的模型上调用 `bind_tools()`。若调用 `get_model_with_action_tool()` 时 Profile 不支持 strict，抛出 `StrictCapabilityError`，不得发送网络请求。

- [ ] **Step 4: 运行新旧 LLMClient 测试**

Run: `cd backend; python -m pytest app/agents/test/test_llm_client.py app/agents/test/test_llm_client_profiles.py tests/test_llm_client.py tests/test_llm_client_config.py -q`  
Expected: PASS；根据新的 mock 位置，将 `ChatOpenAI` patch 从 `app.agents.llm_client` 调整为 `app.agents.providers.openai_compatible`，不放宽断言。

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/llm_client.py backend/app/agents/test/test_llm_client.py backend/app/agents/test/test_llm_client_profiles.py
git commit -m "refactor(llm): delegate calls through provider profiles"
```

---

### Task 4: 为模型存储增加向后兼容 Profile 字段

**Files:**
- Modify: `backend/app/stores/model_config_store.py`
- Create: `backend/app/stores/test/test_model_config_profile.py`
- Modify: `backend/tests/test_model_config_store.py`

**Interfaces:**
- Produces: `ModelConfig.provider_profile: str = "auto"`；旧 JSON 自动补默认值。
- Consumes: 无 Provider 对象，只保存 profile id 字符串。

- [ ] **Step 1: 写失败测试**

```python
def test_old_json_without_provider_profile_loads_as_auto(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"version": 1, "configs": [{
        "id": "old", "name": "Old", "base_url": "https://openrouter.ai/api/v1",
        "model_id": "model", "created_at": "t", "updated_at": "t",
    }]}), encoding="utf-8")
    assert JsonModelConfigStore(str(path)).get("old").provider_profile == "auto"


def test_profile_round_trip(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    config = ModelConfig.new("OpenRouter", "https://openrouter.ai/api/v1", "model", provider_profile="openrouter")
    store.upsert(config)
    assert store.get(config.id).provider_profile == "openrouter"
```

- [ ] **Step 2: 运行并确认失败**

Run: `cd backend; python -m pytest app/stores/test/test_model_config_profile.py -q`  
Expected: FAIL，字段或构造参数不存在。

- [ ] **Step 3: 实现兼容读取**

在 `ModelConfig` 的已有可选字段末尾增加 `provider_profile: str = "auto"`，`new()` 接受同名可选参数。`JsonModelConfigStore.list_all()` 在构造 dataclass 前复制 entry，并执行 `entry.setdefault("provider_profile", "auto")`；不得修改输入字典原对象。

- [ ] **Step 4: 运行测试并确认通过**

Run: `cd backend; python -m pytest app/stores/test/test_model_config_profile.py tests/test_model_config_store.py -q`  
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/stores/model_config_store.py backend/app/stores/test/test_model_config_profile.py backend/tests/test_model_config_store.py
git commit -m "feat(model-config): persist provider profile compatibly"
```

---

### Task 5: 扩展模型 API Schema 并统一连接探测

**Files:**
- Modify: `backend/app/api/model_schemas.py`
- Modify: `backend/app/api/routes/model_routes.py`
- Create: `backend/app/api/test/test_model_profile_routes.py`

**Interfaces:**
- Produces: request/response 的 `provider_profile`；`ModelTestResponse.capabilities`。
- Consumes: `ProviderRegistry`、`LLMClientConfig`、`LLMClient`。

- [ ] **Step 1: 写失败 API 测试**

测试以下契约：

```python
def test_request_defaults_profile_to_auto():
    request = ModelConfigRequest(name="x", base_url="https://example.test/v1", model_id="m")
    assert request.provider_profile == "auto"


def test_request_rejects_unknown_explicit_profile():
    with pytest.raises(ValidationError):
        ModelConfigRequest(
            name="x", base_url="https://example.test/v1", model_id="m",
            provider_profile="missing",
        )


@pytest.mark.asyncio
async def test_model_test_uses_llm_client_and_reports_capabilities(monkeypatch):
    client = MagicMock()
    client.probe.return_value = {"tools": True, "strict_tools": False, "json_output": True,
                                 "reasoning_effort": False, "temperature": True}
    monkeypatch.setattr(model_routes, "LLMClient", MagicMock(return_value=client))
    response = await model_routes.test_model(ModelTestRequest(
        base_url="https://openrouter.ai/api/v1", model_id="model",
        provider_profile="openrouter",
    ))
    assert response.ok is True
    assert response.capabilities.strict_tools is False
```

- [ ] **Step 2: 运行并确认失败**

Run: `cd backend; python -m pytest app/api/test/test_model_profile_routes.py -q`  
Expected: FAIL，新字段和统一 probe 不存在。

- [ ] **Step 3: 实现 Schema 与探测门面**

Schema 使用字符串枚举 `auto/openai/deepseek/openrouter/custom-openai`。`ModelCapabilitiesResponse` 五个布尔字段均必填。`ModelTestRequest` 增加 `provider_profile="auto"`；`ModelTestResponse` 增加可选 `capabilities`。

`LLMClient.probe()` 执行一次最小文本调用并返回当前 Profile 的能力字典；DeepSeek strict endpoint 继续做额外连通性探测。路由删除对 `ChatOpenAI` 的直接导入，统一构造 `LLMClientConfig` 和 `LLMClient`。捕获异常时只返回 `type(error).__name__`，不得包含 `str(error)`。

- [ ] **Step 4: 运行路由和隐私测试**

Run: `cd backend; python -m pytest app/api/test/test_model_profile_routes.py tests/test_model_routes.py tests/test_model_key_crypto.py -q`  
Expected: PASS；更新旧测试 mock 到 `LLMClient`，继续断言响应不包含明文 key。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/model_schemas.py backend/app/api/routes/model_routes.py backend/app/api/test/test_model_profile_routes.py
git commit -m "feat(model-api): expose profile-aware capability probe"
```

---

### Task 6: 将 Profile 物化到游戏配置快照

**Files:**
- Modify: `backend/app/services/game_service.py`
- Create: `backend/app/services/test/test_game_model_profile.py`
- Modify: `backend/tests/test_game_creation_models.py`

**Interfaces:**
- Consumes: `ModelConfig.provider_profile`。
- Produces: `LLMClientConfig.provider_profile` 和无密钥的 snapshot `provider_profile` 字段。

- [ ] **Step 1: 写失败集成测试**

```python
def test_resolved_config_carries_profile_and_snapshot_has_no_key(stored_model):
    config, snapshot = resolve_model_config(
        [{"config_id": stored_model.id, "count": 9}], 9,
    )
    assert config.provider_profile == "openrouter"
    assert snapshot[0]["provider_profile"] == "openrouter"
    assert "api_key" not in snapshot[0]
    assert "api_key_encrypted" not in snapshot[0]
```

另加回归测试：`GameService.create_game()` 构造角色、NightDirector 和 Scheduler 时仍只提供 `LLMClient`，不向游戏核心传入 `ProviderProfile` 或 Transport。

- [ ] **Step 2: 运行并确认失败**

Run: `cd backend; python -m pytest app/services/test/test_game_model_profile.py -q`  
Expected: FAIL，解析结果未携带 profile。

- [ ] **Step 3: 最小修改 resolve_model_config**

环境默认配置使用 `provider_profile="auto"`；已存配置传递 `config.provider_profile`。显示快照写入解析后的 profile id：

```python
resolved_profile = ProviderRegistry().resolve(
    client_config.provider_profile,
    client_config.base_url,
    client_config.model_id,
).profile_id
snapshot[0]["provider_profile"] = resolved_profile
```

不得修改 `create_game()` 后续引擎、角色、Scheduler、Director 装配逻辑。

- [ ] **Step 4: 运行服务回归测试**

Run: `cd backend; python -m pytest app/services/test/test_game_model_profile.py tests/test_game_creation_models.py tests/test_game_service.py -q`  
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/game_service.py backend/app/services/test/test_game_model_profile.py backend/tests/test_game_creation_models.py
git commit -m "feat(game): snapshot resolved provider profile"
```

---

### Task 7: 全量兼容、覆盖率与架构门禁

**Files:**
- Create: `backend/app/agents/providers/test/test_architecture_boundary.py`
- Modify: `backend/tests/test_guard_extension.py`
- Modify: `backend/requirements.txt` only if test execution proves an existing dependency declaration is missing; otherwise leave unchanged and do not include it in the commit.

**Interfaces:**
- Verifies: 游戏核心不导入 Provider 层，接口层不导入游戏引擎/角色，禁止边界保持成立。

- [ ] **Step 1: 写架构失败测试**

遍历 Python 文件 AST 并断言：

```python
def test_game_core_and_roles_do_not_import_provider_layer():
    forbidden_roots = [APP / "core", APP / "roles"]
    offenders = imports_matching(forbidden_roots, "app.agents.providers")
    assert offenders == []


def test_provider_layer_does_not_import_game_core_or_roles():
    offenders = imports_matching(
        [APP / "agents" / "providers"],
        ("app.core", "app.roles"),
    )
    assert offenders == []
```

`imports_matching` 使用 `ast.walk(ast.parse(path.read_text(encoding="utf-8")))` 同时检查 `Import` 和 `ImportFrom`，返回相对路径和模块名，失败信息可定位。

- [ ] **Step 2: 运行 Provider/LLM/模型 API 相关测试**

Run: `cd backend; python -m pytest app/agents/providers/test app/agents/test app/api/test app/stores/test app/services/test tests/test_llm_client.py tests/test_llm_client_config.py tests/test_model_routes.py tests/test_model_config_store.py tests/test_game_creation_models.py tests/test_game_service.py -q`  
Expected: PASS。

- [ ] **Step 3: 运行全量测试和覆盖率**

Run: `cd backend; python -m pytest tests app --cov=app --cov-branch --cov-report=term-missing -q`  
Expected: PASS，statement 和 branch coverage 均为 100%，没有未覆盖行。

- [ ] **Step 4: 验证无禁止修改和无密钥泄露**

Run: `git diff --name-only`  
Expected: 不包含 `backend/app/core/`、`backend/app/roles/`、`backend/app/models/contracts.py`、`backend/app/models/pipeline.py` 和前端路径。

Run: `rg -n "api_key.*(print|logger)|secret.*(print|logger)" backend/app/agents backend/app/api backend/app/services`  
Expected: 无新增命中。

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/providers/test/test_architecture_boundary.py backend/tests/test_guard_extension.py
git commit -m "test(provider): enforce interface-only architecture"
```

---

## Self-Review

1. **规格覆盖：** Profile 注册表（Task 1）、Transport 参数策略与 token 修复（Task 2）、`LLMClient` 兼容门面（Task 3）、配置迁移（Task 4）、连接探测（Task 5）、游戏快照（Task 6）、架构与覆盖率门禁（Task 7）全部有对应任务。
2. **边界：** 计划没有修改游戏核心、角色、动作契约、提示词或前端；完整 Hermes Agent 不进入依赖。
3. **类型一致性：** 所有任务统一使用 `provider_profile`、`ProviderProfile.profile_id`、`CallPurpose.ACTION_JSON/ACTION_STRICT` 和 `LLMClientConfig.provider_profile`。
4. **回退语义：** OpenRouter/custom 默认不尝试 strict；DeepSeek/OpenAI 保留 strict；只有明确 400/422 映射为能力回退。
5. **数据安全：** 配置快照和 API 响应不包含 key；探测错误只返回异常类型。


