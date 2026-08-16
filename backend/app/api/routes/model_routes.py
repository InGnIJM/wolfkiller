from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.agents.llm_client import derive_strict_base_url
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
        strict_url = derive_strict_base_url(config.base_url, config.strict_base_url)
    else:
        if not req.base_url or not req.model_id:
            raise HTTPException(422, "base_url and model_id are required")
        base_url, model_id = req.base_url, req.model_id
        api_key = req.api_key or ""
        strict_url = derive_strict_base_url(base_url)
    # Probe the same endpoints the game will call: the regular endpoint and
    # the derived strict-mode endpoint (they differ for official DeepSeek).
    targets = (
        [(base_url, ""), (strict_url, "strict:")]
        if strict_url != base_url
        else [(base_url, "")]
    )
    start = time.monotonic()
    for endpoint, prefix in targets:
        try:
            ChatOpenAI(
                model=model_id, api_key=api_key, base_url=endpoint,
                temperature=0, max_tokens=1, timeout=10,
            ).invoke([HumanMessage(content="ping")])
        except Exception as error:
            return ModelTestResponse(ok=False, error=f"{prefix}{type(error).__name__}")
    return ModelTestResponse(
        ok=True, latency_ms=int((time.monotonic() - start) * 1000),
    )
