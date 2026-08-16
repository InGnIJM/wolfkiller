# 模型配置后端 实施计划（一期）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付模型配置域（JSON 存储 + key 加密脱敏 + CRUD/测试 API）、角色/预设/约束目录 API，并让游戏创建支持显式模型选择（一期单模型，契约按数组预留二期）。

**Architecture:** 新增 `app/stores/`（存储接口 + JSON 实现 + 机器指纹 Fernet 加密）、`app/catalog.py`（角色元数据/预设/约束）、`/api/models` 与 `/api/catalog` 路由；`LLMClient` 改为显式 `LLMClientConfig` 构造；`GameService.create_game` 通过纯函数 `resolve_model_config` 解析模型分配并物化快照。不改动 5 个核心门禁模块（pipeline/registry/projector/validator/resolver）。

**Tech Stack:** Python 3.11+、FastAPI、pydantic v2、cryptography（新增依赖）、pytest + pytest-asyncio + pytest-cov（100% statement/branch 门禁）。

**规格依据:** `docs/superpowers/specs/2026-08-16-model-config-game-creation-design.md`

**约定（每个 Task 通用）：**
- 测试文件放 `backend/tests/`；运行 `python -m pytest tests/test_xxx.py -q`，全量门禁 `python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q`
- 每个 commit ≤ 3 个文件；message 格式 `<type>(<scope>): <summary>`
- 禁止在日志/异常/响应中出现明文 API key

---

### Task 1: LLMClientConfig 显式配置化

**Files:**
- Modify: `backend/app/agents/llm_client.py`
- Test: `backend/tests/test_llm_client_config.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_llm_client_config.py`：

```python
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.llm_client import (
    LLMClient, LLMClientConfig, env_default_client_config,
)
from app.models.contracts import ActionContract


def _config(**overrides):
    base = dict(
        base_url="https://example.test/v1",
        api_key="sk-test-key",
        model_id="test-model",
        temperature=0.7,
        max_tokens=512,
        strict_base_url="https://example.test/beta",
    )
    base.update(overrides)
    return LLMClientConfig(**base)


def test_env_default_client_config_materializes_app_config(monkeypatch):
    import app.agents.llm_client as mod

    monkeypatch.setattr(mod.app_config.llm, "base_url", "http://env.test/v1")
    monkeypatch.setattr(mod.app_config.llm, "api_key", "env-key")
    monkeypatch.setattr(mod.app_config.llm, "temperature", 1.1)
    monkeypatch.setattr(mod.app_config.llm, "max_tokens", 2048)
    monkeypatch.setattr(mod.app_config.llm, "strict_base_url", "http://env.test/beta")
    monkeypatch.setattr(mod.app_config.llm, "models", property(lambda self: ["env-model"]))

    cfg = env_default_client_config()

    assert cfg == LLMClientConfig(
        base_url="http://env.test/v1", api_key="env-key", model_id="env-model",
        temperature=1.1, max_tokens=2048, strict_base_url="http://env.test/beta",
    )


def test_get_model_uses_explicit_config_kwargs():
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        mock_chat.return_value = SimpleNamespace(__class__=mock_chat)
        client = LLMClient(config=_config())
        client.get_model()
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["api_key"] == "sk-test-key"
    assert kwargs["base_url"] == "https://example.test/v1"
    assert kwargs["temperature"] == 0.7
    assert kwargs["max_tokens"] == 512


def test_model_and_temperature_params_override_config():
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        client = LLMClient(model="custom", temperature=0.1, config=_config())
        client.get_model()
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["model"] == "custom"
    assert kwargs["temperature"] == 0.1
    assert kwargs["max_tokens"] == 512


def test_get_model_with_temperature_overrides_temperature():
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        client = LLMClient(config=_config())
        client.get_model_with_temperature(0.3)
    assert mock_chat.call_args.kwargs["temperature"] == 0.3
    assert mock_chat.call_args.kwargs["base_url"] == "https://example.test/v1"


def test_get_model_with_tools_binds_tools():
    tools = [{"type": "function", "function": {"name": "f"}}]
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        bound = mock_chat.return_value.bind_tools.return_value = SimpleNamespace()
        client = LLMClient(config=_config())
        assert client.get_model_with_tools(tools) is bound
    mock_chat.return_value.bind_tools.assert_called_once_with(tools)


def test_get_model_with_action_tool_uses_strict_base_url():
    contract = ActionContract(
        contract_id="night_check", phase="night",
        action_types=("check", "pass"),
        actions_requiring_target=frozenset({"check"}),
        resolution_priority=1,
    )
    with patch("app.agents.llm_client.ChatOpenAI") as mock_chat:
        client = LLMClient(config=_config())
        client.get_model_with_action_tool(contract)
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["base_url"] == "https://example.test/beta"
    tool = mock_chat.return_value.bind_tools.call_args.args[0][0]
    assert tool["function"]["name"] == "night_check"
    assert mock_chat.return_value.bind_tools.call_args.kwargs["strict"] is True


def test_llm_client_config_is_frozen():
    with pytest.raises(Exception):
        _config().base_url = "x"
```

注意：`ActionContract` 实际字段以 `backend/app/models/contracts.py` 为准；若构造参数不匹配（如 phase 需要 `GamePhase`），运行失败后按源码签名修正测试中的构造参数。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_llm_client_config.py -q`
Expected: FAIL（`ImportError: cannot import name 'LLMClientConfig'`）

- [ ] **Step 3: 实现最小代码**

用以下完整内容替换 `backend/app/agents/llm_client.py`：

```python
from __future__ import annotations

from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from openai import BadRequestError, UnprocessableEntityError

from app.config import config as app_config
from app.agents.output_parser import StrictCapabilityError
from app.models.contracts import ActionContract


@dataclass(frozen=True)
class LLMClientConfig:
    """Explicit per-client model configuration (reads no global state)."""

    base_url: str
    api_key: str
    model_id: str
    temperature: float
    max_tokens: int
    strict_base_url: str


def env_default_client_config() -> LLMClientConfig:
    """Materialize the .env fallback configuration."""
    llm_cfg = app_config.llm
    return LLMClientConfig(
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key,
        model_id=llm_cfg.models[0],
        temperature=llm_cfg.temperature,
        max_tokens=llm_cfg.max_tokens,
        strict_base_url=llm_cfg.strict_base_url,
    )


class LLMClient:
    """Thin wrapper around LangChain ChatModel (OpenAI-compatible).

    Configuration comes from an explicit LLMClientConfig; when omitted the
    .env defaults are used so existing callers keep working.
    """

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        config: LLMClientConfig | None = None,
    ):
        self._config = config if config is not None else env_default_client_config()
        self.model_name = model or self._config.model_id
        self.temperature = (
            temperature if temperature is not None else self._config.temperature
        )
        self.max_tokens = self._config.max_tokens

    def _build(self) -> ChatOpenAI:
        return ChatOpenAI(
            model=self.model_name,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    def get_model(self) -> BaseChatModel:
        return self._build()

    def get_model_with_temperature(self, temperature: float) -> BaseChatModel:
        return ChatOpenAI(
            model=self.model_name,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            temperature=temperature,
            max_tokens=self.max_tokens,
        )

    def get_model_with_tools(self, tools: list[dict]) -> BaseChatModel:
        return self._build().bind_tools(tools)

    @staticmethod
    def map_strict_capability_error(error: Exception) -> Exception:
        """Map only explicit provider strict-schema rejections to a fallback signal."""
        if isinstance(error, StrictCapabilityError):
            return error
        if not isinstance(error, (BadRequestError, UnprocessableEntityError)):
            return error

        response = error.response
        if response.status_code not in (400, 422):
            return error

        body = error.body if isinstance(error.body, dict) else {}
        detail = body.get("error", body)
        if not isinstance(detail, dict):
            detail = {}
        message = str(detail.get("message", error)).lower()
        code = str(detail.get("code", "")).lower()
        parameter = str(detail.get("param", "")).lower()
        strict_or_schema = any(
            marker in " ".join((message, code, parameter))
            for marker in ("strict", "schema", "response_format", "tool")
        )
        unsupported = any(
            marker in " ".join((message, code))
            for marker in (
                "unsupported", "not support", "does not support",
                "not available", "invalid_parameter",
            )
        )
        if strict_or_schema and unsupported:
            return StrictCapabilityError(str(error))
        return error

    def get_model_with_action_tool(self, contract: ActionContract) -> BaseChatModel:
        """Return a strict model bound to the one action tool issued by a contract."""
        model = ChatOpenAI(
            model=self.model_name,
            api_key=self._config.api_key,
            base_url=self._config.strict_base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        tool = {
            "type": "function",
            "function": {
                "name": contract.contract_id,
                "description": "Submit the issued game action.",
                "parameters": contract.json_schema(),
                "strict": True,
            },
        }
        return model.bind_tools(
            [tool], tool_choice=contract.contract_id, strict=True
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_llm_client_config.py tests/test_llm_client.py -q`
Expected: PASS（旧 `test_llm_client.py` 仍全绿——默认行为不变）

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/llm_client.py backend/tests/test_llm_client_config.py
git commit -m "refactor(llm): accept explicit LLMClientConfig instead of global config"
```

---

### Task 2: ModelConfig 模型 + 存储接口 + JSON 实现

**Files:**
- Create: `backend/app/stores/__init__.py`
- Create: `backend/app/stores/model_config_store.py`
- Test: `backend/tests/test_model_config_store.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_model_config_store.py`：

```python
import json

import pytest

from app.stores.model_config_store import (
    JsonModelConfigStore, ModelConfig, get_model_config_store,
)


def _config(name="DeepSeek Pro", **overrides):
    base = dict(
        name=name,
        base_url="https://api.deepseek.com/v1",
        model_id="deepseek-v4-pro",
        api_key_encrypted="gAAAAA...",
        temperature=1.2,
        strict_base_url=None,
    )
    base.update(overrides)
    return ModelConfig.new(**base)


def test_new_generates_id_and_timestamps():
    cfg = _config()
    assert len(cfg.id) == 32
    assert cfg.created_at
    assert cfg.updated_at


def test_store_roundtrip_upsert_list_get_delete(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    cfg = _config()

    store.upsert(cfg)

    assert [c.id for c in store.list_all()] == [cfg.id]
    loaded = store.get(cfg.id)
    assert loaded is not None
    assert loaded.name == "DeepSeek Pro"
    assert loaded.api_key_encrypted == cfg.api_key_encrypted
    assert store.delete(cfg.id) is True
    assert store.list_all() == []


def test_upsert_replaces_existing_with_same_id(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    cfg = _config()
    store.upsert(cfg)
    renamed = _config(name="Renamed")
    renamed.id = cfg.id
    store.upsert(renamed)

    configs = store.list_all()

    assert len(configs) == 1
    assert configs[0].name == "Renamed"


def test_delete_missing_returns_false(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    assert store.delete("nope") is False


def test_missing_file_returns_empty_list(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    assert store.list_all() == []
    assert store.get("x") is None


def test_corrupt_file_returns_empty_list(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = JsonModelConfigStore(str(path))
    assert store.list_all() == []


def test_non_dict_entries_are_skipped(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"version": 1, "configs": ["junk"]}), encoding="utf-8")
    store = JsonModelConfigStore(str(path))
    assert store.list_all() == []


def test_persisted_file_has_version_field_and_no_tmp_left(tmp_path):
    path = tmp_path / "models.json"
    store = JsonModelConfigStore(str(path))
    store.upsert(_config())

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert len(raw["configs"]) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_factory_returns_singleton_for_env_path(tmp_path, monkeypatch):
    import app.stores.model_config_store as mod

    monkeypatch.setattr(mod, "_store", None)
    monkeypatch.setenv("MODEL_CONFIG_PATH", str(tmp_path / "custom.json"))

    first = get_model_config_store()
    second = get_model_config_store()

    assert isinstance(first, JsonModelConfigStore)
    assert first is second
    assert first._path.name == "custom.json"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_model_config_store.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现最小代码**

创建 `backend/app/stores/__init__.py`（空文件）。

创建 `backend/app/stores/model_config_store.py`：

```python
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol


@dataclass
class ModelConfig:
    id: str
    name: str
    base_url: str
    model_id: str
    api_key_encrypted: str = ""
    temperature: Optional[float] = None
    strict_base_url: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(
        cls, name: str, base_url: str, model_id: str,
        api_key_encrypted: str = "", temperature: Optional[float] = None,
        strict_base_url: Optional[str] = None,
    ) -> "ModelConfig":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=uuid.uuid4().hex,
            name=name, base_url=base_url, model_id=model_id,
            api_key_encrypted=api_key_encrypted,
            temperature=temperature, strict_base_url=strict_base_url,
            created_at=now, updated_at=now,
        )


class ModelConfigStore(Protocol):
    def list_all(self) -> list[ModelConfig]: ...
    def get(self, config_id: str) -> Optional[ModelConfig]: ...
    def upsert(self, config: ModelConfig) -> None: ...
    def delete(self, config_id: str) -> bool: ...


class JsonModelConfigStore:
    """JSON-file implementation; writes are atomic (tmp file + os.replace)."""

    VERSION = 1

    def __init__(self, path: str = "data/models.json"):
        self._path = Path(path)
        self._lock = threading.RLock()

    def list_all(self) -> list[ModelConfig]:
        raw = self._read()
        return [
            ModelConfig(**entry)
            for entry in raw.get("configs", [])
            if isinstance(entry, dict)
        ]

    def get(self, config_id: str) -> Optional[ModelConfig]:
        return next(
            (cfg for cfg in self.list_all() if cfg.id == config_id), None,
        )

    def upsert(self, config: ModelConfig) -> None:
        with self._lock:
            raw = self._read()
            configs = [
                entry for entry in raw.get("configs", [])
                if not isinstance(entry, dict) or entry.get("id") != config.id
            ]
            configs.append(asdict(config))
            self._write({"version": self.VERSION, "configs": configs})

    def delete(self, config_id: str) -> bool:
        with self._lock:
            raw = self._read()
            remaining = [
                entry for entry in raw.get("configs", [])
                if not isinstance(entry, dict) or entry.get("id") != config_id
            ]
            if len(remaining) == len(raw.get("configs", [])):
                return False
            self._write({"version": self.VERSION, "configs": remaining})
            return True

    def _read(self) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"version": self.VERSION, "configs": []}
        if not isinstance(raw, dict):
            return {"version": self.VERSION, "configs": []}
        return raw

    def _write(self, payload: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)


_store: Optional[ModelConfigStore] = None


def get_model_config_store() -> ModelConfigStore:
    """Lazy singleton; path overridable via MODEL_CONFIG_PATH env var."""
    global _store
    if _store is None:
        _store = JsonModelConfigStore(
            os.getenv("MODEL_CONFIG_PATH", "data/models.json"),
        )
    return _store
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_model_config_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/stores/__init__.py backend/app/stores/model_config_store.py backend/tests/test_model_config_store.py
git commit -m "feat(stores): add model config store with JSON persistence"
```

---

### Task 3: ModelKeyCrypto 加密与脱敏

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/stores/model_key_crypto.py`
- Test: `backend/tests/test_model_key_crypto.py`

- [ ] **Step 1: 安装依赖**

Run: `pip install "cryptography>=42.0"`
并在 `backend/requirements.txt` 末尾追加一行：`cryptography>=42.0`

- [ ] **Step 2: 写失败测试**

创建 `backend/tests/test_model_key_crypto.py`：

```python
import base64

import pytest
from cryptography.fernet import Fernet

from app.stores.model_key_crypto import (
    KeyDecryptionError, ModelKeyCrypto, _derive_key, mask_api_key,
)


def test_encrypt_decrypt_roundtrip():
    crypto = ModelKeyCrypto()
    token = crypto.encrypt("sk-secret-1234")
    assert token != "sk-secret-1234"
    assert crypto.decrypt(token) == "sk-secret-1234"


def test_encryption_is_deterministic_per_key():
    crypto = ModelKeyCrypto()
    token = crypto.encrypt("sk-secret")
    other = ModelKeyCrypto(key=_derive_key())
    assert other.decrypt(token) == "sk-secret"


def test_decrypt_wrong_key_raises_key_decryption_error():
    crypto = ModelKeyCrypto()
    token = crypto.encrypt("sk-secret")
    forged = Fernet(Fernet.generate_key())
    with pytest.raises(KeyDecryptionError):
        ModelKeyCrypto(key=base64.urlsafe_b64encode(forged._signing_key)).decrypt(token)


def test_decrypt_garbage_raises_key_decryption_error():
    with pytest.raises(KeyDecryptionError):
        ModelKeyCrypto().decrypt("not-a-valid-token")


def test_decrypt_non_utf8_payload_raises_key_decryption_error():
    key = _derive_key()
    token = Fernet(key).encrypt(b"\xff\xfe").decode("ascii")
    with pytest.raises(KeyDecryptionError):
        ModelKeyCrypto(key=key).decrypt(token)


def test_mask_short_key_is_fully_hidden():
    assert mask_api_key("abc1234") == "***"
    assert mask_api_key("") == "***"


def test_mask_long_key_keeps_prefix_and_suffix():
    assert mask_api_key("sk-abcdef1234") == "sk-***1234"
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_model_key_crypto.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 4: 实现最小代码**

创建 `backend/app/stores/model_key_crypto.py`：

```python
from __future__ import annotations

import base64
import hashlib
import platform
import uuid

from cryptography.fernet import Fernet, InvalidToken


def _derive_key() -> bytes:
    """Stable per-machine key from node id + hostname (no separate key file)."""
    material = f"{uuid.getnode()}:{platform.node()}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(material).digest())


class KeyDecryptionError(ValueError):
    """Raised when a stored key cannot be decrypted (e.g. different machine)."""


class ModelKeyCrypto:
    def __init__(self, key: bytes | None = None):
        self._fernet = Fernet(key if key is not None else _derive_key())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as error:
            raise KeyDecryptionError("stored api key cannot be decrypted") from error


def mask_api_key(plaintext: str) -> str:
    """Return a masked display form that never reveals the full key."""
    if len(plaintext) <= 8:
        return "***"
    return f"{plaintext[:3]}***{plaintext[-4:]}"
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_model_key_crypto.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/stores/model_key_crypto.py backend/tests/test_model_key_crypto.py backend/requirements.txt
git commit -m "feat(stores): encrypt api keys with machine-derived Fernet key"
```

---

### Task 4: 模型配置 API（schemas + routes）

**Files:**
- Create: `backend/app/api/model_schemas.py`
- Create: `backend/app/api/routes/model_routes.py`
- Test: `backend/tests/test_model_routes.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_model_routes.py`：

```python
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.model_schemas import (
    ModelAssignment, ModelConfigRequest, ModelConfigResponse,
    ModelListResponse, ModelTestRequest,
)
from app.api.routes import model_routes
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig
from app.stores.model_key_crypto import ModelKeyCrypto


# ── Schema validation ──────────────────────────────────────────

def test_request_rejects_non_http_base_url():
    with pytest.raises(ValidationError, match="base_url"):
        ModelConfigRequest(name="n", base_url="ftp://x", model_id="m")


def test_request_strips_and_rejects_blank_name():
    with pytest.raises(ValidationError, match="name"):
        ModelConfigRequest(name="   ", base_url="https://x", model_id="m")
    assert ModelConfigRequest(name="  a  ", base_url="https://x", model_id="m").name == "a"


def test_request_rejects_temperature_out_of_range():
    with pytest.raises(ValidationError):
        ModelConfigRequest(name="n", base_url="https://x", model_id="m", temperature=3)


def test_model_assignment_count_must_be_positive():
    with pytest.raises(ValidationError):
        ModelAssignment(config_id=None, count=0)


# ── Store-backed handlers ──────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return JsonModelConfigStore(str(tmp_path / "models.json"))


def _stored(store, name="DeepSeek Pro", api_key_encrypted=None):
    cfg = ModelConfig.new(
        name=name, base_url="https://api.deepseek.com/v1",
        model_id="deepseek-v4-pro", api_key_encrypted=api_key_encrypted or "",
    )
    store.upsert(cfg)
    return cfg


@pytest.mark.asyncio
async def test_create_model_returns_masked_key_and_never_leaks(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    req = ModelConfigRequest(
        name="DeepSeek Pro", base_url="https://api.deepseek.com/v1",
        model_id="deepseek-v4-pro", api_key="sk-secret-1234",
    )

    response = await model_routes.create_model(req)

    assert response.api_key_masked == "sk-***1234"
    assert response.has_key is True
    assert response.key_invalid is False
    assert "sk-secret-1234" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_list_models_masks_all_keys(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    _stored(store, api_key_encrypted=crypto.encrypt("sk-very-secret-9999"))

    response = await model_routes.list_models()

    assert len(response.configs) == 1
    assert "sk-very-secret-9999" not in response.model_dump_json()
    assert response.configs[0].api_key_masked == "sk-***9999"


@pytest.mark.asyncio
async def test_create_duplicate_name_conflicts(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    _stored(store)
    req = ModelConfigRequest(
        name="DeepSeek Pro", base_url="https://x", model_id="m",
    )
    with pytest.raises(HTTPException) as exc:
        await model_routes.create_model(req)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_update_keeps_existing_key_when_blank(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-original-1234"))

    response = await model_routes.update_model(
        cfg.id, ModelConfigRequest(
            name="Renamed", base_url="https://x", model_id="m",
        ),
    )

    assert response.name == "Renamed"
    assert response.api_key_masked == "sk-***1234"
    stored = store.get(cfg.id)
    assert crypto.decrypt(stored.api_key_encrypted) == "sk-original-1234"


@pytest.mark.asyncio
async def test_update_replaces_key_when_provided(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-old"))

    response = await model_routes.update_model(
        cfg.id, ModelConfigRequest(
            name="Renamed", base_url="https://x", model_id="m",
            api_key="sk-new-1234",
        ),
    )

    assert response.api_key_masked == "sk-***1234"
    assert crypto.decrypt(store.get(cfg.id).api_key_encrypted) == "sk-new-1234"


@pytest.mark.asyncio
async def test_update_duplicate_name_conflicts(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    other = _stored(store, name="Other")
    target = _stored(store, name="Target")
    with pytest.raises(HTTPException) as exc:
        await model_routes.update_model(
            target.id, ModelConfigRequest(
                name="Other", base_url="https://x", model_id="m",
            ),
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_get_update_delete_missing_returns_404(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with pytest.raises(HTTPException) as exc:
        await model_routes.get_model("nope")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await model_routes.update_model(
            "nope", ModelConfigRequest(name="n", base_url="https://x", model_id="m"),
        )
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await model_routes.delete_model("nope")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_removes_config(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store)
    await model_routes.delete_model(cfg.id)
    assert store.list_all() == []


@pytest.mark.asyncio
async def test_key_invalid_marks_response(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, api_key_encrypted="garbage-not-a-token")

    response = await model_routes.get_model(cfg.id)

    assert response.key_invalid is True
    assert response.api_key_masked is None
    assert response.has_key is True


@pytest.mark.asyncio
async def test_config_without_key_has_no_key(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, api_key_encrypted="")

    response = await model_routes.get_model(cfg.id)

    assert response.has_key is False
    assert response.api_key_masked is None


# ── Connection test endpoint ───────────────────────────────────

@pytest.mark.asyncio
async def test_connection_test_by_id_uses_stored_key(store, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "ChatOpenAI") as mock_chat:
        mock_chat.return_value.invoke.return_value = "ok"
        response = await model_routes.test_model(
            ModelTestRequest(config_id=cfg.id),
        )

    assert response.ok is True
    assert isinstance(response.latency_ms, int)
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["api_key"] == "sk-stored-key"
    assert kwargs["base_url"] == "https://api.deepseek.com/v1"


@pytest.mark.asyncio
async def test_connection_test_by_id_missing_returns_404(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with pytest.raises(HTTPException) as exc:
        await model_routes.test_model(ModelTestRequest(config_id="nope"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_connection_test_by_id_without_stored_key_uses_blank(store, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, api_key_encrypted="")
    with patch.object(model_routes, "ChatOpenAI") as mock_chat:
        await model_routes.test_model(ModelTestRequest(config_id=cfg.id))
    assert mock_chat.call_args.kwargs["api_key"] == ""


@pytest.mark.asyncio
async def test_connection_test_by_id_invalid_key_returns_400(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, api_key_encrypted="garbage")
    with pytest.raises(HTTPException) as exc:
        await model_routes.test_model(ModelTestRequest(config_id=cfg.id))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_connection_test_by_fields_requires_base_url_and_model_id(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with pytest.raises(HTTPException) as exc:
        await model_routes.test_model(ModelTestRequest())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_connection_test_by_fields_uses_given_values(store, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with patch.object(model_routes, "ChatOpenAI") as mock_chat:
        await model_routes.test_model(ModelTestRequest(
            base_url="https://form.test/v1", api_key="sk-form-key",
            model_id="form-model",
        ))
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["base_url"] == "https://form.test/v1"
    assert kwargs["api_key"] == "sk-form-key"
    assert kwargs["model"] == "form-model"


@pytest.mark.asyncio
async def test_connection_test_maps_provider_errors(store, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with patch.object(model_routes, "ChatOpenAI") as mock_chat:
        mock_chat.return_value.invoke.side_effect = TimeoutError("slow")
        response = await model_routes.test_model(ModelTestRequest(
            base_url="https://x", model_id="m",
        ))

    assert response.ok is False
    assert response.error == "TimeoutError"
    assert response.latency_ms is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_model_routes.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 schemas**

创建 `backend/app/api/model_schemas.py`：

```python
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ModelConfigRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    base_url: str
    model_id: str = Field(min_length=1)
    api_key: str = ""
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    strict_base_url: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("base_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value


class ModelConfigResponse(BaseModel):
    id: str
    name: str
    base_url: str
    model_id: str
    has_key: bool
    api_key_masked: Optional[str]
    key_invalid: bool
    temperature: Optional[float]
    strict_base_url: Optional[str]
    created_at: str
    updated_at: str


class ModelListResponse(BaseModel):
    configs: list[ModelConfigResponse]


class ModelTestRequest(BaseModel):
    config_id: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_id: Optional[str] = None


class ModelTestResponse(BaseModel):
    ok: bool
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class ModelAssignment(BaseModel):
    config_id: Optional[str] = None
    count: int = Field(ge=1)
```

- [ ] **Step 4: 实现 routes**

创建 `backend/app/api/routes/model_routes.py`：

```python
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.api.model_schemas import (
    ModelConfigRequest, ModelConfigResponse, ModelListResponse,
    ModelTestRequest, ModelTestResponse,
)
from app.stores.model_config_store import ModelConfig, get_model_config_store
from app.stores.model_key_crypto import (
    KeyDecryptionError, ModelKeyCrypto, mask_api_key,
)

router = APIRouter(prefix="/api/models", tags=["models"])


def _to_response(config: ModelConfig) -> ModelConfigResponse:
    crypto = ModelKeyCrypto()
    masked: str | None = None
    key_invalid = False
    if config.api_key_encrypted:
        try:
            masked = mask_api_key(crypto.decrypt(config.api_key_encrypted))
        except KeyDecryptionError:
            key_invalid = True
    return ModelConfigResponse(
        id=config.id, name=config.name, base_url=config.base_url,
        model_id=config.model_id, has_key=bool(config.api_key_encrypted),
        api_key_masked=masked, key_invalid=key_invalid,
        temperature=config.temperature, strict_base_url=config.strict_base_url,
        created_at=config.created_at, updated_at=config.updated_at,
    )


@router.get("", response_model=ModelListResponse)
async def list_models():
    store = get_model_config_store()
    return ModelListResponse(configs=[_to_response(c) for c in store.list_all()])


@router.post("", response_model=ModelConfigResponse, status_code=201)
async def create_model(req: ModelConfigRequest):
    store = get_model_config_store()
    if any(c.name == req.name for c in store.list_all()):
        raise HTTPException(409, "model config name already exists")
    crypto = ModelKeyCrypto()
    config = ModelConfig.new(
        name=req.name, base_url=req.base_url, model_id=req.model_id,
        api_key_encrypted=crypto.encrypt(req.api_key) if req.api_key else "",
        temperature=req.temperature, strict_base_url=req.strict_base_url,
    )
    store.upsert(config)
    return _to_response(config)


@router.get("/{config_id}", response_model=ModelConfigResponse)
async def get_model(config_id: str):
    config = get_model_config_store().get(config_id)
    if config is None:
        raise HTTPException(404, "model config not found")
    return _to_response(config)


@router.put("/{config_id}", response_model=ModelConfigResponse)
async def update_model(config_id: str, req: ModelConfigRequest):
    store = get_model_config_store()
    existing = store.get(config_id)
    if existing is None:
        raise HTTPException(404, "model config not found")
    if any(c.name == req.name and c.id != config_id for c in store.list_all()):
        raise HTTPException(409, "model config name already exists")
    crypto = ModelKeyCrypto()
    encrypted = existing.api_key_encrypted
    if req.api_key:
        encrypted = crypto.encrypt(req.api_key)
    updated = ModelConfig(
        id=existing.id, name=req.name, base_url=req.base_url,
        model_id=req.model_id, api_key_encrypted=encrypted,
        temperature=req.temperature, strict_base_url=req.strict_base_url,
        created_at=existing.created_at,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    store.upsert(updated)
    return _to_response(updated)


@router.delete("/{config_id}", status_code=204)
async def delete_model(config_id: str):
    if not get_model_config_store().delete(config_id):
        raise HTTPException(404, "model config not found")


@router.post("/test", response_model=ModelTestResponse)
async def test_model(req: ModelTestRequest):
    crypto = ModelKeyCrypto()
    if req.config_id is not None:
        config = get_model_config_store().get(req.config_id)
        if config is None:
            raise HTTPException(404, "model config not found")
        base_url, model_id = config.base_url, config.model_id
        api_key = req.api_key or ""
        if config.api_key_encrypted and not api_key:
            try:
                api_key = crypto.decrypt(config.api_key_encrypted)
            except KeyDecryptionError:
                raise HTTPException(400, "stored api key cannot be decrypted") from None
    else:
        if not req.base_url or not req.model_id:
            raise HTTPException(422, "base_url and model_id are required")
        base_url, model_id = req.base_url, req.model_id
        api_key = req.api_key or ""
    start = time.monotonic()
    try:
        ChatOpenAI(
            model=model_id, api_key=api_key, base_url=base_url,
            temperature=0, max_tokens=1, timeout=10,
        ).invoke([HumanMessage(content="ping")])
    except Exception as error:
        return ModelTestResponse(ok=False, error=type(error).__name__)
    return ModelTestResponse(
        ok=True, latency_ms=int((time.monotonic() - start) * 1000),
    )
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_model_routes.py -q`
Expected: PASS（若 `ChatOpenAI` mock 断言与 langchain 实际签名冲突，按真实签名修正测试中的 kwargs 断言）

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/model_schemas.py backend/app/api/routes/model_routes.py backend/tests/test_model_routes.py
git commit -m "feat(api): add model config CRUD and connection-test endpoints"
```

---

### Task 5: 目录 API（角色/预设/约束）

**Files:**
- Create: `backend/app/catalog.py`
- Create: `backend/app/api/catalog_schemas.py`
- Create: `backend/app/api/routes/catalog_routes.py`
- Test: `backend/tests/test_catalog_routes.py`

- [ ] **Step 1: 写失败测试（catalog 模块 + schemas）**

创建 `backend/tests/test_catalog_routes.py`：

```python
from types import SimpleNamespace

import pytest

from app.catalog import (
    FIELD_CONSTRAINTS, ROLE_METADATA, STANDARD_PRESETS,
    _catalog_item, role_catalog,
)
from app.api.catalog_schemas import (
    ConstraintsResponse, PresetItem, PresetsResponse, RoleCatalogItem,
)
from app.roles.registry import builtin_registry


def test_catalog_item_known_role_uses_metadata():
    spec = SimpleNamespace(
        display_name="Werewolf", camp_id="werewolf", min_count=0,
        max_count=None, dependencies=frozenset(), exclusions=frozenset(),
    )
    item = _catalog_item("wolf-killer-werewolf", spec)

    assert item["name_zh"] == "狼人"
    assert item["icon"] == "wolf"
    assert item["camp"] == "werewolf"
    assert item["description"] == ROLE_METADATA["wolf-killer-werewolf"]["description"]


def test_catalog_item_unknown_role_falls_back_to_spec_display():
    spec = SimpleNamespace(
        display_name="Mystery", camp_id="third_party", min_count=1,
        max_count=3, dependencies=frozenset({"a"}), exclusions=frozenset(),
    )
    item = _catalog_item("x-unknown-role", spec)

    assert item["name_zh"] == "Mystery"
    assert item["icon"] == "unknown"
    assert item["description"] == ""
    assert item["min_count"] == 1
    assert item["max_count"] == 3
    assert item["dependencies"] == ["a"]
    assert item["exclusions"] == []


def test_catalog_item_empty_display_falls_back_to_role_id():
    spec = SimpleNamespace(
        display_name="", camp_id="good", min_count=0, max_count=None,
        dependencies=frozenset(), exclusions=frozenset(),
    )
    item = _catalog_item("x-unknown-role", spec)
    assert item["display_name"] == "x-unknown-role"


def test_role_catalog_covers_all_registered_roles():
    items = role_catalog(builtin_registry.freeze())

    assert len(items) == 6
    ids = {item["role_id"] for item in items}
    assert "wolf-killer-guard" in ids
    by_id = {item["role_id"]: item for item in items}
    assert by_id["wolf-killer-werewolf"]["name_zh"] == "狼人"
    assert by_id["wolf-killer-werewolf"]["camp"] == "werewolf"
    assert by_id["wolf-killer-guard"]["camp"] == "good"


def test_presets_cover_nine_and_ten_player_fields():
    assert len(STANDARD_PRESETS) == 2
    nine = next(p for p in STANDARD_PRESETS if p["id"] == "nine-player-standard")
    ten = next(p for p in STANDARD_PRESETS if p["id"] == "ten-player-standard")
    assert sum(nine["role_counts"].values()) == 9
    assert sum(ten["role_counts"].values()) == 10
    assert ten["role_counts"]["wolf-killer-guard"] == 1


def test_field_constraints_defaults():
    assert FIELD_CONSTRAINTS["min_players"] == 4
    assert FIELD_CONSTRAINTS["max_players"] == 12
    assert FIELD_CONSTRAINTS["min_werewolves"] == 1
    assert FIELD_CONSTRAINTS["min_good"] == 1


def test_schema_models_accept_catalog_shapes():
    item = RoleCatalogItem(**role_catalog(builtin_registry.freeze())[0])
    assert item.role_id
    preset = PresetItem(**STANDARD_PRESETS[0])
    assert preset.id
    constraints = ConstraintsResponse(**FIELD_CONSTRAINTS)
    assert constraints.min_players == 4
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_catalog_routes.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 catalog 模块与 schemas**

创建 `backend/app/catalog.py`：

```python
"""Role display metadata, standard field presets, and custom-field constraints.

Kept OUTSIDE the frozen RoleSpec so adding names/icons/presets never changes
the registry digest and never invalidates persisted game archives.
"""

from __future__ import annotations

ROLE_METADATA: dict[str, dict[str, str]] = {
    "wolf-killer-werewolf": {
        "name_zh": "狼人", "icon": "wolf",
        "description": "夜晚睁眼，与狼队友商议刀人",
    },
    "wolf-killer-villager": {
        "name_zh": "平民", "icon": "villager",
        "description": "白天发言投票，找出狼人",
    },
    "wolf-killer-seer": {
        "name_zh": "预言家", "icon": "seer",
        "description": "每晚查验一名玩家的阵营",
    },
    "wolf-killer-witch": {
        "name_zh": "女巫", "icon": "witch",
        "description": "拥有一瓶解药和一瓶毒药",
    },
    "wolf-killer-hunter": {
        "name_zh": "猎人", "icon": "hunter",
        "description": "死亡时可以开枪带走一名玩家",
    },
    "wolf-killer-guard": {
        "name_zh": "守卫", "icon": "guard",
        "description": "每晚守护一名玩家免于狼刀",
    },
}

STANDARD_PRESETS: list[dict] = [
    {
        "id": "nine-player-standard",
        "name": "九人标准场",
        "description": "3狼 3民 1预言家 1女巫 1猎人",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
        },
    },
    {
        "id": "ten-player-standard",
        "name": "十人标准场",
        "description": "3狼 3民 1预言家 1女巫 1猎人 1守卫",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
    },
]

FIELD_CONSTRAINTS: dict[str, int] = {
    "min_players": 4,
    "max_players": 12,
    "min_werewolves": 1,
    "min_good": 1,
}


def _catalog_item(role_id: str, spec) -> dict:
    meta = ROLE_METADATA.get(role_id)
    if meta is None:
        meta = {}
    display = spec.display_name or role_id
    return {
        "role_id": role_id,
        "display_name": display,
        "name_zh": meta.get("name_zh", display),
        "camp": spec.camp_id,
        "icon": meta.get("icon", "unknown"),
        "description": meta.get("description", ""),
        "min_count": spec.min_count,
        "max_count": spec.max_count,
        "dependencies": sorted(spec.dependencies),
        "exclusions": sorted(spec.exclusions),
    }


def role_catalog(registry_snapshot) -> list[dict]:
    """Project the frozen registry snapshot into frontend-facing role items."""
    return [
        _catalog_item(role_id, spec)
        for role_id, spec in sorted(registry_snapshot.specs.items())
    ]
```

创建 `backend/app/api/catalog_schemas.py`：

```python
from pydantic import BaseModel


class RoleCatalogItem(BaseModel):
    role_id: str
    display_name: str
    name_zh: str
    camp: str
    icon: str
    description: str
    min_count: int
    max_count: int | None
    dependencies: list[str]
    exclusions: list[str]


class RoleCatalogResponse(BaseModel):
    roles: list[RoleCatalogItem]


class PresetItem(BaseModel):
    id: str
    name: str
    description: str
    role_counts: dict[str, int]


class PresetsResponse(BaseModel):
    presets: list[PresetItem]


class ConstraintsResponse(BaseModel):
    min_players: int
    max_players: int
    min_werewolves: int
    min_good: int
```

- [ ] **Step 4: 运行确认通过（模块级）**

Run: `python -m pytest tests/test_catalog_routes.py -q`
Expected: FAIL 只剩 routes 部分缺失——先提交模块与 schema 测试。

```bash
git add backend/app/catalog.py backend/app/api/catalog_schemas.py backend/tests/test_catalog_routes.py
git commit -m "feat(catalog): add role metadata, standard presets, and field constraints"
```

- [ ] **Step 5: 追加 routes 失败测试**

在 `backend/tests/test_catalog_routes.py` 末尾追加：

```python
import pytest


@pytest.mark.asyncio
async def test_list_roles_endpoint_returns_six_roles():
    from app.api.routes import catalog_routes

    response = await catalog_routes.list_roles()

    assert len(response.roles) == 6
    assert response.roles[0].role_id


@pytest.mark.asyncio
async def test_list_presets_endpoint_returns_standards():
    from app.api.routes import catalog_routes

    response = await catalog_routes.list_presets()

    assert len(response.presets) == 2
    assert response.presets[0].name == "九人标准场"


@pytest.mark.asyncio
async def test_get_constraints_endpoint_matches_constants():
    from app.api.routes import catalog_routes

    response = await catalog_routes.get_constraints()

    assert response.min_players == FIELD_CONSTRAINTS["min_players"]
    assert response.max_players == FIELD_CONSTRAINTS["max_players"]
```

- [ ] **Step 6: 运行确认失败**

Run: `python -m pytest tests/test_catalog_routes.py -q`
Expected: FAIL（ModuleNotFoundError: catalog_routes）

- [ ] **Step 7: 实现 routes**

创建 `backend/app/api/routes/catalog_routes.py`：

```python
from fastapi import APIRouter

from app.catalog import FIELD_CONSTRAINTS, STANDARD_PRESETS, role_catalog
from app.api.catalog_schemas import (
    ConstraintsResponse, PresetItem, PresetsResponse, RoleCatalogResponse,
)
from app.roles.registry import builtin_registry

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/roles", response_model=RoleCatalogResponse)
async def list_roles():
    return RoleCatalogResponse(roles=role_catalog(builtin_registry.freeze()))


@router.get("/presets", response_model=PresetsResponse)
async def list_presets():
    return PresetsResponse(presets=[PresetItem(**p) for p in STANDARD_PRESETS])


@router.get("/constraints", response_model=ConstraintsResponse)
async def get_constraints():
    return ConstraintsResponse(**FIELD_CONSTRAINTS)
```

- [ ] **Step 8: 运行确认通过并提交**

Run: `python -m pytest tests/test_catalog_routes.py -q`
Expected: PASS

```bash
git add backend/app/api/routes/catalog_routes.py backend/tests/test_catalog_routes.py
git commit -m "feat(api): expose role catalog, presets, and constraints"
```

---

### Task 6: 创建游戏支持模型分配（一期单模型契约）

**Files:**
- Modify: `backend/app/services/game_service.py`
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/routes/game_routes.py`
- Modify: `backend/app/services/game_manifest.py`
- Test: `backend/tests/test_game_creation_models.py`
- Modify: `backend/tests/test_game_service.py`（适配 3 处 `_command_provider` 调用 + 1 处 LLMClient patch）
- Modify: `backend/tests/test_game_routes.py`（2 处 create_game 断言追加 `model_assignments=None`）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_game_creation_models.py`：

```python
import pytest

from app.agents.llm_client import LLMClientConfig, env_default_client_config
from app.services import game_service
from app.services.game_service import resolve_model_config
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig
from app.stores.model_key_crypto import ModelKeyCrypto


def _store_with(tmp_path, monkeypatch, **fields):
    cfg = ModelConfig.new(
        name="DeepSeek Pro", base_url="https://cfg.test/v1",
        model_id="cfg-model", **fields,
    )
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    store.upsert(cfg)
    monkeypatch.setattr(game_service, "get_model_config_store", lambda: store)
    return cfg, store


def test_none_assignments_returns_env_default_and_empty_snapshot():
    client_config, snapshot = resolve_model_config(None, 9)

    assert isinstance(client_config, LLMClientConfig)
    assert snapshot == []


def test_env_entry_returns_env_config_and_display_snapshot(monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "base_url", "http://env.test/v1")
    monkeypatch.setattr(game_service.app_config.llm, "api_key", "env-key")

    client_config, snapshot = resolve_model_config([{"config_id": None, "count": 9}], 9)

    assert client_config.base_url == "http://env.test/v1"
    assert snapshot == [{
        "config_id": None, "name": "环境默认 (.env)",
        "model_id": client_config.model_id, "base_url": "http://env.test/v1",
    }]


def test_env_entry_count_mismatch_raises():
    with pytest.raises(ValueError, match="count"):
        resolve_model_config([{"config_id": None, "count": 8}], 9)


def test_multiple_entries_raise_in_stage_one():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_model_config([{"config_id": None, "count": 5}] * 2, 9)


def test_non_dict_entry_raises():
    with pytest.raises(ValueError, match="object"):
        resolve_model_config(["junk"], 9)


def test_non_list_assignments_raise():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_model_config({"config_id": None}, 9)


def test_unknown_config_id_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        game_service, "get_model_config_store",
        lambda: JsonModelConfigStore(str(tmp_path / "models.json")),
    )
    with pytest.raises(ValueError, match="unknown"):
        resolve_model_config([{"config_id": "missing", "count": 9}], 9)


def test_configured_model_uses_stored_fields_and_env_fallbacks(tmp_path, monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "max_tokens", 999)
    monkeypatch.setattr(game_service.app_config.llm, "strict_base_url", "http://env.test/beta")
    crypto = ModelKeyCrypto()
    cfg, _ = _store_with(
        tmp_path, monkeypatch,
        api_key_encrypted=crypto.encrypt("sk-cfg-key"),
        temperature=0.5, strict_base_url="http://cfg.test/strict",
    )

    client_config, snapshot = resolve_model_config(
        [{"config_id": cfg.id, "count": 9}], 9,
    )

    assert client_config.base_url == "https://cfg.test/v1"
    assert client_config.model_id == "cfg-model"
    assert client_config.api_key == "sk-cfg-key"
    assert client_config.temperature == 0.5
    assert client_config.max_tokens == 999
    assert client_config.strict_base_url == "http://cfg.test/strict"
    assert snapshot == [{
        "config_id": cfg.id, "name": "DeepSeek Pro",
        "model_id": "cfg-model", "base_url": "https://cfg.test/v1",
    }]
    assert "sk-cfg-key" not in str(snapshot)


def test_config_without_key_uses_env_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "api_key", "env-key")
    cfg, _ = _store_with(tmp_path, monkeypatch, api_key_encrypted="")

    client_config, _ = resolve_model_config([{"config_id": cfg.id, "count": 9}], 9)

    assert client_config.api_key == "env-key"


def test_config_with_invalid_key_raises(tmp_path, monkeypatch):
    cfg, _ = _store_with(tmp_path, monkeypatch, api_key_encrypted="garbage")

    with pytest.raises(ValueError, match="invalid"):
        resolve_model_config([{"config_id": cfg.id, "count": 9}], 9)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_game_creation_models.py -q`
Expected: FAIL（ImportError: resolve_model_config）

- [ ] **Step 3: 实现 game_service 改造**

按以下 6 处精确修改 `backend/app/services/game_service.py`：

① 顶部导入区：删除 `import random`；将

```python
from app.agents.llm_client import LLMClient
```

替换为

```python
from app.agents.llm_client import LLMClient, LLMClientConfig, env_default_client_config
from app.stores.model_config_store import get_model_config_store
from app.stores.model_key_crypto import KeyDecryptionError, ModelKeyCrypto
```

② 在 `PUBLIC_NIGHT_SUBSTEPS = frozenset({...})` 块之后、`class GameService:` 之前插入纯函数：

```python
def resolve_model_config(
    model_assignments: Optional[list[dict]],
    total_players: int,
) -> tuple[LLMClientConfig, list[dict]]:
    """Resolve stage-one model assignment into an explicit client config.

    Stage one accepts exactly one assignment whose count equals the total
    player count. `config_id=None` selects the .env environment default.
    Returns (client_config, display_snapshot); the snapshot never contains
    the api key.
    """
    env_config = env_default_client_config()
    if model_assignments is None:
        return env_config, []
    if not isinstance(model_assignments, list) or len(model_assignments) != 1:
        raise ValueError("model assignments must contain exactly one entry")
    entry = model_assignments[0]
    if not isinstance(entry, dict):
        raise ValueError("model assignment entry must be an object")
    config_id = entry.get("config_id")
    count = entry.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count != total_players:
        raise ValueError("model assignment count must equal total players")
    if config_id is None:
        return env_config, [{
            "config_id": None,
            "name": "环境默认 (.env)",
            "model_id": env_config.model_id,
            "base_url": env_config.base_url,
        }]
    config = get_model_config_store().get(config_id)
    if config is None:
        raise ValueError("unknown model config")
    crypto = ModelKeyCrypto()
    if config.api_key_encrypted:
        try:
            api_key = crypto.decrypt(config.api_key_encrypted)
        except KeyDecryptionError:
            raise ValueError("model config api key is invalid") from None
    else:
        api_key = env_config.api_key
    client_config = LLMClientConfig(
        base_url=config.base_url,
        api_key=api_key,
        model_id=config.model_id,
        temperature=(
            config.temperature
            if config.temperature is not None
            else env_config.temperature
        ),
        max_tokens=env_config.max_tokens,
        strict_base_url=config.strict_base_url or env_config.strict_base_url,
    )
    snapshot = [{
        "config_id": config.id,
        "name": config.name,
        "model_id": config.model_id,
        "base_url": config.base_url,
    }]
    return client_config, snapshot
```

③ `GameService.__init__`：在 `self._tasks: dict[str, asyncio.Task] = {}` 之后新增：

```python
        self._model_snapshots: dict[str, list[dict]] = {}
```

④ `create_game` 签名加参数：将

```python
        num_hunters: Optional[int] = None,
        role_counts: Optional[dict[str, int]] = None,
    ) -> str:
```

替换为

```python
        num_hunters: Optional[int] = None,
        role_counts: Optional[dict[str, int]] = None,
        model_assignments: Optional[list[dict]] = None,
    ) -> str:
```

并将创建流程三处替换。将

```python
        # Create role instances with random model assignment
        models = app_config.llm.models
        prompt_builder = PromptBuilder()
        roles = self._create_roles(config, prompt_builder, models)
```

替换为

```python
        # Resolve the stage-one model assignment into an explicit client config
        client_config, model_snapshot = resolve_model_config(
            model_assignments, config.total_players,
        )
        self._model_snapshots[game_id] = model_snapshot

        def client_provider(seat: int) -> LLMClient:
            return LLMClient(config=client_config)

        prompt_builder = PromptBuilder()
        roles = self._create_roles(config, prompt_builder, client_provider)
```

将

```python
        llm_client = LLMClient(model=random.choice(models))
```

替换为

```python
        llm_client = client_provider(0)
```

将

```python
            self._command_provider(snapshot, renderer, llm_client, director),
```

替换为

```python
            self._command_provider(snapshot, renderer, client_provider, director),
```

将

```python
        self._manifest.add_game(game_id, {
            "role_counts": dict(config.role_counts),
        })
```

替换为

```python
        self._manifest.add_game(
            game_id,
            {"role_counts": dict(config.role_counts)},
            model_snapshot=model_snapshot,
        )
```

⑤ `_create_roles` 整体替换为：

```python
    def _create_roles(
        self, config: GameConfig, prompt_builder: PromptBuilder, client_provider,
    ) -> dict:
        return builtin_registry.create_roles(
            config.role_counts,
            config.total_players,
            prompt_builder,
            llm_client_factory=lambda: client_provider(0),
        )
```

⑥ `_command_provider`：将签名行

```python
    def _command_provider(self, snapshot, renderer: PromptRenderer, llm_client: LLMClient, director):
```

替换为

```python
    def _command_provider(self, snapshot, renderer: PromptRenderer, client_provider, director):
```

并在 provider 内部将

```python
            try:
                response = llm_client.get_model().invoke(messages)
```

替换为

```python
            try:
                llm_client = client_provider(request.actor_seat)
                response = llm_client.get_model().invoke(messages)
```

⑦ 在 `get_game_state` 方法之前新增：

```python
    def get_game_model_snapshot(self, game_id: str) -> list[dict]:
        """Return the persisted model snapshot for a game (display-only)."""
        return self._model_snapshots.get(game_id, [])
```

- [ ] **Step 4: 实现 schemas / manifest / routes**

`backend/app/api/schemas.py`：

① 顶部导入区新增：`from app.api.model_schemas import ModelAssignment`
② `CreateGameRequest` 末尾字段区新增：

```python
    model_assignments: Optional[list[ModelAssignment]] = None
```

③ `CreateGameResponse` 新增字段：

```python
    model_snapshot: list[dict] = []
```

`backend/app/services/game_manifest.py`：

① `add_game` 签名改为：

```python
    def add_game(
        self, game_id: str, config: dict,
        model_snapshot: Optional[list] = None,
    ) -> None:
```

并在 `entry.update({...})` 块之后新增：

```python
        if model_snapshot is not None:
            entry["model_snapshot"] = model_snapshot
```

② `update_game` 签名末尾新增参数 `model_snapshot: Optional[list] = None,`，并在函数体内（`if last_consistent_checkpoint is not None:` 块之后）新增：

```python
        if model_snapshot is not None:
            entry["model_snapshot"] = model_snapshot
```

`backend/app/api/routes/game_routes.py`：将 `create_game` 函数体替换为：

```python
@router.post("", response_model=CreateGameResponse)
async def create_game(req: CreateGameRequest = CreateGameRequest()):
    service = get_service()
    assignments = (
        [item.model_dump() for item in req.model_assignments]
        if req.model_assignments is not None
        else None
    )
    try:
        if req.role_counts is not None:
            game_id = await service.create_game(
                role_counts=req.role_counts, model_assignments=assignments,
            )
        else:
            game_id = await service.create_game(
                num_werewolves=req.num_werewolves,
                num_villagers=req.num_villagers,
                num_seers=req.num_seers,
                num_witches=req.num_witches,
                num_hunters=req.num_hunters,
                model_assignments=assignments,
            )
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    state = service.get_game_state(game_id)
    if state is None:
        raise HTTPException(404, "Game not found after creation")
    return CreateGameResponse(
        game_id=game_id,
        player_count=len(state.players),
        config={
            "role_counts": state.config.role_counts,
            **{
                field: state.config.role_counts.get(role_id, 0)
                for field, role_id in _LEGACY_ROLE_COUNT_FIELDS.items()
            },
        },
        model_snapshot=service.get_game_model_snapshot(game_id),
    )
```

- [ ] **Step 5: 追加路由/服务集成失败测试**

在 `backend/tests/test_game_creation_models.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_create_game_route_passes_assignments_and_returns_snapshot(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.api.routes import game_routes
    from app.api.schemas import CreateGameRequest

    service = MagicMock()
    service.create_game = AsyncMock(return_value="game-123")
    service.get_game_state.return_value = MagicMock(
        players={1: object(), 2: object()},
        config=MagicMock(role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1}),
    )
    service.get_game_model_snapshot.return_value = [{"config_id": None, "name": "环境默认 (.env)"}]
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.create_game(CreateGameRequest(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
        model_assignments=[{"config_id": None, "count": 2}],
    ))

    service.create_game.assert_awaited_once_with(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
        model_assignments=[{"config_id": None, "count": 2}],
    )
    assert response.model_snapshot == [{"config_id": None, "name": "环境默认 (.env)"}]


@pytest.mark.asyncio
async def test_create_game_route_maps_resolution_errors_to_400(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import HTTPException

    from app.api.routes import game_routes
    from app.api.schemas import CreateGameRequest

    service = MagicMock()
    service.create_game = AsyncMock(side_effect=ValueError("unknown model config"))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    with pytest.raises(HTTPException) as exc:
        await game_routes.create_game(CreateGameRequest(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
            model_assignments=[{"config_id": "missing", "count": 2}],
        ))
    assert exc.value.status_code == 400
```

在 `backend/tests/test_game_service.py` 末尾追加：

```python
class TestModelAssignmentIntegration:
    @pytest.mark.asyncio
    async def test_create_game_persists_env_snapshot_and_uses_config_client(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock, patch

        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        with patch.object(service_module, "LLMClient") as mock_client:
            game_id = await service.create_game(
                num_werewolves=3, num_villagers=3,
                num_seers=1, num_witches=1, num_hunters=1,
            )

        assert service.get_game_model_snapshot(game_id) == []
        service._manifest.add_game.assert_called_once()
        kwargs = service._manifest.add_game.call_args.kwargs
        assert kwargs["model_snapshot"] == []
        assert any(
            call.kwargs.get("config") is not None
            for call in mock_client.call_args_list
        )
```

- [ ] **Step 6: 适配存量测试（3 处）**

`backend/tests/test_game_service.py`：

① 第 203 行将

```python
        monkeypatch.setattr(service_module, "LLMClient", lambda model: fake_llm)
```

替换为

```python
        monkeypatch.setattr(
            service_module, "LLMClient",
            lambda model=None, temperature=None, config=None: fake_llm,
        )
```

② 第 1293、1369、1393 行三处 `service._command_provider(snapshot, renderer, llm, director)`（及带 `PromptRenderer()` 的变体）把第三个参数 `llm` 改为 `lambda seat: llm`。

`backend/tests/test_game_routes.py`：两处 `assert_awaited_once_with` 断言追加 `, model_assignments=None`（即 `assert_awaited_once_with(role_counts=counts, model_assignments=None)` 与 legacy 版本同理）。

- [ ] **Step 7: 运行确认通过**

Run: `python -m pytest tests/test_game_creation_models.py tests/test_game_service.py tests/test_game_routes.py tests/test_game_engine.py -q`
Expected: PASS（若其余存量测试因签名变化失败，按相同思路最小修正：`LLMClient` patch 需接受 `config` 关键字）

- [ ] **Step 8: Commit（分两个 commit，每个 ≤3 文件）**

```bash
git add backend/app/services/game_service.py backend/tests/test_game_creation_models.py backend/tests/test_game_service.py
git commit -m "feat(service): resolve explicit model assignment per game"

git add backend/app/api/schemas.py backend/app/api/routes/game_routes.py backend/app/services/game_manifest.py
git commit -m "feat(api): accept model_assignments and return model snapshot"
```

注意：`backend/tests/test_game_routes.py` 的断言适配随第二个 commit 一起提交（`git add` 加进去）。

---

### Task 7: main.py 装配 + 全量门禁

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_main.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_main.py` 中 `test_main_exposes_app_routes_and_services` 的 paths 断言改为：

```python
    paths = {route.path for route in main.app.routes}
    assert "/api/health" in paths
    assert "/api/config" in paths
    assert "/ws/game/{game_id}" in paths
    assert "/api/games" in paths
    assert "/api/models" in paths
    assert "/api/models/{config_id}" in paths
    assert "/api/models/test" in paths
    assert "/api/catalog/roles" in paths
    assert "/api/catalog/presets" in paths
    assert "/api/catalog/constraints" in paths
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_main.py -q`
Expected: FAIL（断言失败：缺少新路径）

- [ ] **Step 3: 实现装配**

`backend/app/main.py`：

① 导入区新增：

```python
from app.api.routes.model_routes import router as model_router
from app.api.routes.catalog_routes import router as catalog_router
```

② 在 `app.include_router(game_router)` 之后新增：

```python
app.include_router(model_router)
app.include_router(catalog_router)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_main.py -q`
Expected: PASS

- [ ] **Step 5: 全量门禁**

Run: `python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q`
Expected: 全绿，覆盖率 100%（含新模块）。若新增分支未覆盖，回到对应 Task 补测试再提交。

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_main.py
git commit -m "feat(api): wire model and catalog routers into the app"
```

---

## Self-Review（已完成）

1. **规格覆盖**：存储接口+JSON（Task 2）✅、加密/脱敏（Task 3）✅、CRUD+测试（Task 4）✅、目录/预设/约束（Task 5）✅、LLMClient 显式配置（Task 1）✅、model_assignments 契约+manifest 快照（Task 6）✅、隐私扫描（Task 4 两个 never-leak 测试）✅、main 装配（Task 7）✅。二期（多模型数量落座、SQLite 实现）明确不在本计划范围。
2. **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码。
3. **类型一致性**：`LLMClientConfig` 字段在 Task 1/6 一致；`resolve_model_config` 返回 `(LLMClientConfig, list[dict])` 在 Task 6 各处一致；`model_snapshot` 键名在 service/manifest/routes 一致；`get_game_model_snapshot` 方法名一致。
