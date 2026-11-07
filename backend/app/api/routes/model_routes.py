from __future__ import annotations

import asyncio
import logging
import time
import sys
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException
from app.agents.llm_client import LLMClient, LLMClientConfig, derive_strict_base_url
from app.agents.providers.base import API_MODE_ANTHROPIC_MESSAGES, API_MODE_SYSTEM_ONE
from app.agents.providers.registry import ProviderRegistry
from app.api.model_schemas import (
    ModelCatalogRequest, ModelCatalogResponse, ModelConfigRequest,
    ModelConfigResponse,
    ModelListResponse, ModelCapabilitiesResponse, ModelLatencyStats,
    ModelTestRequest, ModelTestResponse,
)
from app.stores.model_config_store import ModelConfig, get_model_config_store
from app.stores.model_key_crypto import (
    KeyDecryptionError, ModelKeyCrypto, mask_api_key,
)

router = APIRouter(prefix="/api/models", tags=["models"])
logger = logging.getLogger(__name__)

# Latency history older than this says little about the endpoint as it is now.
LATENCY_WINDOW_DAYS = 30

#: The aggregate scans the attempts table for the whole window. A page refresh
#: (or a remounting wizard) must not pay for it again: 60 s is short enough that
#: an operator watching a fresh run sees it warm up, long enough that repeated
#: page opens are free. Keyed by repository identity so tests and a replaced
#: application instance never share a cache entry.
LATENCY_CACHE_TTL_SECONDS = 60.0
_latency_cache: tuple[object, float, dict[tuple[str, str], dict[str, int]]] | None = None


def get_repository():
    """Use the repository already owned by the running application."""
    main = sys.modules.get("app.main")
    return None if main is None else getattr(main, "repository", None)


def _latency_stats() -> dict[tuple[str, str], dict[str, int]]:
    """Per (model_id, provider_profile) call latency, or empty when unknown."""
    global _latency_cache
    repository = get_repository()
    if repository is None:
        return {}
    now = time.monotonic()
    cached = _latency_cache
    if cached is not None and cached[0] is repository and now < cached[1]:
        return cached[2]
    since = (
        datetime.now(timezone.utc) - timedelta(days=LATENCY_WINDOW_DAYS)
    ).isoformat()
    stats = repository.model_latency_stats(since=since)
    _latency_cache = (repository, now + LATENCY_CACHE_TTL_SECONDS, stats)
    return stats


def warm_latency_cache() -> None:
    """Populate the cache during startup; a failure must not break the boot."""
    try:
        _latency_stats()
    except Exception:
        logger.warning("Model latency cache warmup failed", exc_info=True)


def _to_response(
    config: ModelConfig, latency: ModelLatencyStats | None = None,
    crypto: ModelKeyCrypto | None = None,
) -> ModelConfigResponse:
    crypto = crypto or ModelKeyCrypto()
    masked: str | None = None
    key_invalid = False
    if config.api_key_encrypted:
        try:
            masked = mask_api_key(crypto.decrypt(config.api_key_encrypted))
        except KeyDecryptionError:
            key_invalid = True
    return ModelConfigResponse(
        id=config.id, name=config.name, base_url=config.base_url,
        model_id=config.model_id,
        model_ids=list(config.model_ids) or [config.model_id],
        has_key=bool(config.api_key_encrypted),
        api_key_masked=masked, key_invalid=key_invalid,
        temperature=config.temperature, strict_base_url=config.strict_base_url,
        provider_profile=config.provider_profile,
        reasoning_effort=config.reasoning_effort,
        headers=dict(config.headers),
        created_at=config.created_at, updated_at=config.updated_at,
        latency=latency,
    )


def _latency_for(
    config: ModelConfig, stats: dict[tuple[str, str], dict[str, int]],
) -> ModelLatencyStats | None:
    """Match stored history to this config's resolved endpoint identity.

    Recorded attempts carry the profile the transport actually used, so an
    ``auto`` config has to be resolved the same way before it can be matched.
    """
    resolved = ProviderRegistry().resolve(
        config.provider_profile, config.base_url, config.model_id,
    ).profile_id
    row = stats.get((config.model_id, resolved))
    return None if row is None else ModelLatencyStats(**row)


@router.get("", response_model=ModelListResponse)
async def list_models():
    store = get_model_config_store()
    # The stats query scans the attempts table; keep it off the event loop.
    stats = await asyncio.to_thread(_latency_stats)
    crypto = ModelKeyCrypto()
    return ModelListResponse(
        configs=[
            _to_response(c, _latency_for(c, stats), crypto)
            for c in store.list_all()
        ],
    )


@router.post("", response_model=ModelConfigResponse, status_code=201)
async def create_model(req: ModelConfigRequest):
    store = get_model_config_store()
    if any(c.name == req.name for c in store.list_all()):
        raise HTTPException(409, "model config name already exists")
    crypto = ModelKeyCrypto()
    model_ids = req.model_ids or [req.model_id]
    config = ModelConfig.new(
        name=req.name, base_url=req.base_url, model_id=model_ids[0],
        model_ids=model_ids,
        api_key_encrypted=crypto.encrypt(req.api_key) if req.api_key else "",
        temperature=req.temperature, strict_base_url=req.strict_base_url,
        provider_profile=req.provider_profile,
        reasoning_effort=req.reasoning_effort,
        headers=req.headers,
    )
    store.upsert(config)
    return _to_response(config)


def _endpoints_changed(
    existing: ModelConfig, *, base_url: str, strict_base_url: str | None,
) -> bool:
    """Whether a request moves the host a stored key would be sent to.

    Both the plain and the strict address count: the strict one is derived from
    the base URL and is where a profile declaring ``strict_endpoint`` sends the
    action calls. This is the single source of truth for "the endpoint moved",
    shared by saving a config and by probing one.
    """
    return (
        base_url.rstrip("/") != existing.base_url.rstrip("/")
        or derive_strict_base_url(base_url, strict_base_url).rstrip("/")
        != derive_strict_base_url(existing.base_url, existing.strict_base_url).rstrip("/")
    )


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
    endpoints_changed = _endpoints_changed(
        existing, base_url=req.base_url, strict_base_url=req.strict_base_url,
    )
    if encrypted and not req.api_key and endpoints_changed:
        raise HTTPException(400, "Changing model endpoints requires re-entering the API key")
    if req.api_key:
        encrypted = crypto.encrypt(req.api_key)
    model_ids = req.model_ids or [req.model_id]
    updated = ModelConfig(
        id=existing.id, name=req.name, base_url=req.base_url,
        model_id=model_ids[0], model_ids=model_ids,
        api_key_encrypted=encrypted,
        temperature=req.temperature, strict_base_url=req.strict_base_url,
        provider_profile=req.provider_profile,
        reasoning_effort=req.reasoning_effort,
        headers=req.headers,
        created_at=existing.created_at,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    store.upsert(updated)
    return _to_response(updated)


@router.delete("/{config_id}", status_code=204)
async def delete_model(config_id: str):
    repository = get_repository()
    if repository is not None:
        references = repository.model_config_references(config_id)
        if references["game_ids"] or references["benchmark_run_ids"]:
            raise HTTPException(409, {
                "code": "model_config_referenced",
                **references,
            })
    if not get_model_config_store().delete(config_id):
        raise HTTPException(404, "model config not found")


async def _fetch_model_ids(
    base_url: str,
    api_key: str,
    headers: dict[str, str],
    api_mode: str,
    *,
    timeout: float = 10.0,
) -> list[str]:
    """List the provider's ``GET /models`` catalog, if it exposes one.

    OpenAI-compatible and Anthropic dialects both answer with a
    ``{"data": [{"id": ...}]}`` envelope; only the auth header differs.
    """
    url = f"{base_url.rstrip('/')}/models"
    request_headers = dict(headers)
    if api_mode == API_MODE_ANTHROPIC_MESSAGES:
        request_headers.setdefault("x-api-key", api_key)
        request_headers.setdefault("anthropic-version", "2023-06-01")
    elif api_key:
        request_headers.setdefault("Authorization", f"Bearer {api_key}")
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=request_headers)
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("unexpected /models payload")
    return sorted({
        item["id"]
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    })


@router.post("/catalog", response_model=ModelCatalogResponse)
async def model_catalog(req: ModelCatalogRequest):
    """List the models a provider can serve.

    Works for a stored config (``config_id``) or an unsaved dialog (inline
    ``base_url``/``api_key``); the override and endpoint-change rules mirror
    ``/test`` so a stored key never travels to an endpoint it was not saved
    for. Failures come back in ``error`` so the dialog can keep manual entry.
    """
    crypto = ModelKeyCrypto()
    if req.config_id is not None:
        config = get_model_config_store().get(req.config_id)
        if config is None:
            raise HTTPException(404, "model config not found")
        base_url = req.base_url or config.base_url
        api_key = req.api_key or ""
        explicit_strict_base_url = (
            config.strict_base_url if req.strict_base_url is None else req.strict_base_url
        )
        provider_profile = req.provider_profile or config.provider_profile
        if config.api_key_encrypted and not req.api_key and (
            provider_profile != config.provider_profile
            or _endpoints_changed(
                config, base_url=base_url, strict_base_url=explicit_strict_base_url,
            )
        ):
            raise HTTPException(
                400, "Changing model endpoints requires re-entering the API key",
            )
        if config.api_key_encrypted and not api_key:
            try:
                api_key = crypto.decrypt(config.api_key_encrypted)
            except KeyDecryptionError:
                raise HTTPException(
                    400, "stored api key cannot be decrypted",
                ) from None
        headers = dict(config.headers) if req.headers is None else req.headers
        model_id = config.model_id
    else:
        if not req.base_url:
            raise HTTPException(422, "base_url is required")
        base_url = req.base_url
        api_key = req.api_key or ""
        provider_profile = req.provider_profile or "auto"
        headers = req.headers or {}
        model_id = ""
    profile = ProviderRegistry().resolve(provider_profile, base_url, model_id)
    if profile.api_mode == API_MODE_SYSTEM_ONE:
        return ModelCatalogResponse(
            model_ids=[], error="provider does not expose a model listing",
        )
    try:
        model_ids = await _fetch_model_ids(
            base_url, api_key, headers, profile.api_mode,
        )
    except Exception as error:
        return ModelCatalogResponse(
            model_ids=[], error=type(error).__name__,
        )
    return ModelCatalogResponse(model_ids=model_ids)


def _probe_model(config: LLMClientConfig) -> dict[str, bool]:
    """Own the client's complete lifetime in the worker, even on cancellation."""
    client = LLMClient(config=config, dedicated_http_clients=True)
    try:
        return client.probe()
    finally:
        asyncio.run(client.aclose())


@router.post("/test", response_model=ModelTestResponse)
async def test_model(req: ModelTestRequest):
    crypto = ModelKeyCrypto()
    if req.config_id is not None:
        config = get_model_config_store().get(req.config_id)
        if config is None:
            raise HTTPException(404, "model config not found")
        # Saving is gated on a passing test, so every editable field follows
        # one rule: an explicit request value wins over the stored config and
        # ``None`` means "not sent, use the stored value". An empty strict
        # address is an edited value (it clears the stored one), not an
        # omission.
        base_url = req.base_url or config.base_url
        model_id = req.model_id or config.model_id
        api_key = req.api_key or ""
        explicit_strict_base_url = (
            config.strict_base_url if req.strict_base_url is None else req.strict_base_url
        )
        provider_profile = req.provider_profile or config.provider_profile
        # A stored key may only ever travel to the endpoint it was stored for.
        # Saving a config already enforces that; probing it must too, or one
        # local request with a ``config_id`` and an attacker's ``base_url``
        # forwards the key. A profile counts as an endpoint change because it
        # decides whether the strict address is used. The refusal happens before
        # any decryption, so a rejected probe cannot leak even by accident.
        if config.api_key_encrypted and not req.api_key and (
            provider_profile != config.provider_profile
            or _endpoints_changed(
                config, base_url=base_url, strict_base_url=explicit_strict_base_url,
            )
        ):
            raise HTTPException(
                400, "Changing model endpoints requires re-entering the API key",
            )
        if config.api_key_encrypted and not api_key:
            try:
                api_key = crypto.decrypt(config.api_key_encrypted)
            except KeyDecryptionError:
                raise HTTPException(400, "stored api key cannot be decrypted") from None
        # ``None`` keeps the stored headers so the dialog can test a profile
        # change without losing them; an explicit dict (even empty) wins.
        headers = dict(config.headers) if req.headers is None else req.headers
        reasoning_effort = (
            config.reasoning_effort
            if req.reasoning_effort is None
            else req.reasoning_effort or None
        )
    else:
        if not req.base_url or not req.model_id:
            raise HTTPException(422, "base_url and model_id are required")
        base_url, model_id = req.base_url, req.model_id
        api_key = req.api_key or ""
        explicit_strict_base_url = req.strict_base_url
        provider_profile = req.provider_profile or "auto"
        headers = req.headers or {}
        reasoning_effort = req.reasoning_effort or None
    resolved_profile = ProviderRegistry().resolve(
        provider_profile, base_url, model_id,
    )
    strict_url = base_url
    targets = [(base_url, "")]
    if resolved_profile.strict_endpoint:
        strict_url = derive_strict_base_url(base_url, explicit_strict_base_url)
        targets.append((strict_url, "strict:"))
    start = time.monotonic()
    capabilities: dict[str, bool] | None = None
    for endpoint, prefix in targets:
        try:
            result = await asyncio.to_thread(_probe_model, LLMClientConfig(
                base_url=endpoint,
                api_key=api_key,
                model_id=model_id,
                temperature=0,
                max_tokens=1,
                strict_base_url=strict_url,
                action_timeout_seconds=10,
                action_retry_timeout_seconds=10,
                provider_profile=resolved_profile.profile_id,
                reasoning_effort=reasoning_effort,
                headers=tuple(headers.items()),
            ))
            if not prefix:
                capabilities = result
        except Exception as error:
            return ModelTestResponse(ok=False, error=f"{prefix}{type(error).__name__}")
    return ModelTestResponse(
        ok=True, latency_ms=int((time.monotonic() - start) * 1000),
        capabilities=ModelCapabilitiesResponse(**(capabilities or {})),
    )
