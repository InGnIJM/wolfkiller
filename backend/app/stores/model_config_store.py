from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


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
    provider_profile: str = "auto"

    @classmethod
    def new(
        cls, name: str, base_url: str, model_id: str,
        api_key_encrypted: str = "", temperature: Optional[float] = None,
        strict_base_url: Optional[str] = None,
        provider_profile: str = "auto",
    ) -> "ModelConfig":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=uuid.uuid4().hex,
            name=name, base_url=base_url, model_id=model_id,
            api_key_encrypted=api_key_encrypted,
            temperature=temperature, strict_base_url=strict_base_url,
            provider_profile=provider_profile,
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
        with self._lock:
            raw = self._read()
            configs = []
            for entry in raw.get("configs", []):
                if not isinstance(entry, dict):
                    continue
                entry = entry.copy()
                entry.setdefault("provider_profile", "auto")
                configs.append(ModelConfig(**entry))
            return configs

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
        except OSError as error:
            logger.warning(
                "Failed to load model config store, starting empty: %s", error,
            )
            return {"version": self.VERSION, "configs": []}
        except json.JSONDecodeError as error:
            logger.warning(
                "Failed to load model config store, starting empty: %s", error,
            )
            return {"version": self.VERSION, "configs": []}
        if not isinstance(raw, dict):
            logger.warning("Model config store root is not an object, starting empty")
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
        default_path = Path(os.getenv("WOLFKILLER_DATA_DIR", "data")).expanduser() / "models.json"
        _store = JsonModelConfigStore(
            os.getenv("MODEL_CONFIG_PATH", str(default_path)),
        )
    return _store
