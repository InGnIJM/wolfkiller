from unittest.mock import patch

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


_CAPABILITIES = {
    "tools": True,
    "strict_tools": True,
    "json_output": True,
    "reasoning_effort": True,
    "temperature": True,
}


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
            name="Renamed", base_url=cfg.base_url, model_id="m",
        ),
    )

    assert response.name == "Renamed"
    assert response.api_key_masked == "sk-***1234"
    stored = store.get(cfg.id)
    assert crypto.decrypt(stored.api_key_encrypted) == "sk-original-1234"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"base_url": "https://untrusted.invalid/v1"},
    {"strict_base_url": "https://untrusted.invalid/beta"},
])
async def test_changing_endpoint_requires_a_new_key(store, monkeypatch, changes):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, api_key_encrypted=ModelKeyCrypto().encrypt("fake-only-key"))
    fields = {"name": cfg.name, "base_url": cfg.base_url, "model_id": cfg.model_id, **changes}
    with pytest.raises(HTTPException) as error:
        await model_routes.update_model(cfg.id, ModelConfigRequest(**fields))
    assert error.value.status_code == 400
    assert store.get(cfg.id) == cfg


@pytest.mark.asyncio
async def test_probe_does_not_block_event_loop_and_always_closes_client(monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock, MagicMock

    released = threading.Event()
    saw_release = []
    client = MagicMock()
    client.aclose = AsyncMock()
    def probe():
        saw_release.append(released.wait(timeout=1))
        return _CAPABILITIES
    client.probe.side_effect = probe
    monkeypatch.setattr(model_routes, "LLMClient", lambda **kwargs: client)
    task = asyncio.create_task(model_routes.test_model(ModelTestRequest(
        base_url="https://example.invalid/v1", model_id="fake", provider_profile="custom-openai",
    )))
    asyncio.get_running_loop().call_soon(released.set)
    response = await task
    assert response.ok
    assert saw_release == [True]
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_probe_closes_client(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.probe.side_effect = TimeoutError("fake timeout")
    client.aclose = AsyncMock()
    monkeypatch.setattr(model_routes, "LLMClient", lambda **kwargs: client)
    response = await model_routes.test_model(ModelTestRequest(
        base_url="https://example.invalid/v1", model_id="fake", provider_profile="custom-openai",
    ))
    assert response.error == "TimeoutError"
    client.aclose.assert_awaited_once()


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
    _stored(store, name="Other")
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
    monkeypatch.setattr(model_routes, "get_repository", lambda: None)
    cfg = _stored(store)
    await model_routes.delete_model(cfg.id)
    assert store.list_all() == []


@pytest.mark.asyncio
async def test_delete_rejects_config_referenced_by_unfinished_work(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store)
    repository = type("Repository", (), {
        "model_config_references": lambda self, config_id: {
            "game_ids": ["game-1"], "benchmark_run_ids": ["run-1"],
        },
    })()
    monkeypatch.setattr(model_routes, "get_repository", lambda: repository)

    with pytest.raises(HTTPException) as caught:
        await model_routes.delete_model(cfg.id)

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "code": "model_config_referenced",
        "game_ids": ["game-1"],
        "benchmark_run_ids": ["run-1"],
    }
    assert store.get(cfg.id) is not None


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
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        response = await model_routes.test_model(
            ModelTestRequest(config_id=cfg.id),
        )

    assert response.ok is True
    assert isinstance(response.latency_ms, int)
    calls = mock_client.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["config"].base_url == "https://api.deepseek.com/v1"
    assert calls[1].kwargs["config"].base_url == "https://api.deepseek.com/beta"
    assert calls[0].kwargs["config"].api_key == "sk-stored-key"
    assert calls[1].kwargs["config"].api_key == "sk-stored-key"


@pytest.mark.asyncio
async def test_connection_test_by_id_with_provided_key_overrides_stored_key(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        await model_routes.test_model(ModelTestRequest(
            config_id=cfg.id, api_key="sk-provided-key",
        ))
    calls = mock_client.call_args_list
    assert calls[0].kwargs["config"].api_key == "sk-provided-key"
    assert calls[1].kwargs["config"].api_key == "sk-provided-key"


@pytest.mark.asyncio
async def test_connection_test_by_id_missing_returns_404(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with pytest.raises(HTTPException) as exc:
        await model_routes.test_model(ModelTestRequest(config_id="nope"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_connection_test_by_id_without_stored_key_uses_blank(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, api_key_encrypted="")
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        await model_routes.test_model(ModelTestRequest(config_id=cfg.id))
    assert mock_client.call_args_list[0].kwargs["config"].api_key == ""


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
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        await model_routes.test_model(ModelTestRequest(
            base_url="https://form.test/v1", api_key="sk-form-key",
            model_id="form-model",
        ))
    config = mock_client.call_args.kwargs["config"]
    assert config.base_url == "https://form.test/v1"
    assert config.api_key == "sk-form-key"
    assert config.model_id == "form-model"


@pytest.mark.asyncio
async def test_connection_test_maps_provider_errors(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.side_effect = TimeoutError("slow")
        response = await model_routes.test_model(ModelTestRequest(
            base_url="https://x", model_id="m",
        ))

    assert response.ok is False
    assert response.error == "TimeoutError"
    assert response.latency_ms is None


@pytest.mark.asyncio
async def test_connection_test_by_id_reports_strict_endpoint_failure(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.side_effect = [
            _CAPABILITIES, TimeoutError("slow"),
        ]
        response = await model_routes.test_model(ModelTestRequest(config_id=cfg.id))

    assert response.ok is False
    assert response.error == "strict:TimeoutError"
    assert response.latency_ms is None


@pytest.mark.asyncio
async def test_connection_test_by_id_non_deepseek_config_uses_single_endpoint(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = ModelConfig.new(
        name="MiMo", base_url="https://api.xiaomimimo.com/v1",
        model_id="mimo-v2.5", api_key_encrypted=crypto.encrypt("sk-x"),
    )
    store.upsert(cfg)
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        await model_routes.test_model(ModelTestRequest(config_id=cfg.id))

    calls = mock_client.call_args_list
    assert len(calls) == 1
    assert calls[0].kwargs["config"].base_url == "https://api.xiaomimimo.com/v1"


@pytest.mark.asyncio
async def test_update_to_own_name_does_not_conflict(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, name="Keep")

    response = await model_routes.update_model(
        cfg.id, ModelConfigRequest(name="Keep", base_url="https://x", model_id="m"),
    )

    assert response.name == "Keep"


@pytest.mark.asyncio
async def test_connection_test_by_fields_requires_model_id(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with pytest.raises(HTTPException) as exc:
        await model_routes.test_model(ModelTestRequest(base_url="https://x"))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_update_preserves_created_at_and_refreshes_updated_at(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = ModelConfig(
        id="fixed-id-1", name="Old", base_url="https://x", model_id="m",
        api_key_encrypted="", temperature=None, strict_base_url=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    store.upsert(cfg)

    response = await model_routes.update_model(
        "fixed-id-1", ModelConfigRequest(name="New", base_url="https://x", model_id="m"),
    )

    assert response.created_at == "2026-01-01T00:00:00+00:00"
    assert response.updated_at != "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_cancelled_probe_closes_only_after_worker_finishes(monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock, MagicMock

    started, released, closed = threading.Event(), threading.Event(), threading.Event()
    client = MagicMock()
    client.aclose = AsyncMock(side_effect=closed.set)
    def probe():
        started.set()
        released.wait(timeout=3)
        return _CAPABILITIES
    client.probe.side_effect = probe
    monkeypatch.setattr(model_routes, "LLMClient", lambda **kwargs: client)
    task = asyncio.create_task(model_routes.test_model(ModelTestRequest(
        base_url="https://example.invalid/v1", model_id="fake", provider_profile="custom-openai",
    )))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not closed.is_set()
    finally:
        released.set()
        await asyncio.gather(task, return_exceptions=True)
    assert await asyncio.to_thread(closed.wait, 2)
    client.aclose.assert_awaited_once()



@pytest.mark.asyncio
async def test_unreferenced_model_can_be_deleted_with_repository_attached(store, monkeypatch):
    from unittest.mock import Mock

    cfg = _stored(store)
    repository = Mock()
    repository.model_config_references.return_value = {"game_ids": [], "benchmark_run_ids": []}
    monkeypatch.setattr(model_routes, "get_repository", lambda: repository)
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    await model_routes.delete_model(cfg.id)
    assert store.get(cfg.id) is None
