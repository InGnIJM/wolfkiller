from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.api.model_schemas import ModelConfigRequest, ModelTestRequest
from app.api.routes import model_routes
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig


def test_request_defaults_profile_to_auto():
    request = ModelConfigRequest(
        name="x", base_url="https://example.test/v1", model_id="m",
    )

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
    client.probe.return_value = {
        "tools": True,
        "strict_tools": False,
        "json_output": True,
        "reasoning_effort": False,
        "temperature": True,
    }
    llm_client = MagicMock(return_value=client)
    monkeypatch.setattr(model_routes, "LLMClient", llm_client)

    response = await model_routes.test_model(ModelTestRequest(
        base_url="https://openrouter.ai/api/v1", model_id="model",
        provider_profile="openrouter",
    ))

    assert response.ok is True
    assert response.capabilities.strict_tools is False
    assert llm_client.call_args.kwargs["config"].provider_profile == "openrouter"


@pytest.mark.asyncio
async def test_config_probe_uses_stored_profile(monkeypatch, tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    stored = ModelConfig.new(
        name="router", base_url="https://openrouter.ai/api/v1", model_id="m",
        provider_profile="openrouter",
    )
    store.upsert(stored)
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    client = MagicMock()
    client.probe.return_value = {
        "tools": True,
        "strict_tools": False,
        "json_output": True,
        "reasoning_effort": True,
        "temperature": True,
    }
    llm_client = MagicMock(return_value=client)
    monkeypatch.setattr(model_routes, "LLMClient", llm_client)

    response = await model_routes.test_model(ModelTestRequest(
        config_id=stored.id, provider_profile="deepseek",
    ))

    assert response.ok is True
    assert llm_client.call_args.kwargs["config"].provider_profile == "openrouter"


@pytest.mark.asyncio
async def test_model_test_does_not_expose_provider_error_details(monkeypatch):
    client = MagicMock()
    client.probe.side_effect = RuntimeError("key=secret body=private")
    monkeypatch.setattr(model_routes, "LLMClient", MagicMock(return_value=client))

    response = await model_routes.test_model(ModelTestRequest(
        base_url="https://example.test/v1", model_id="model",
    ))

    assert response.ok is False
    assert response.error == "RuntimeError"


@pytest.mark.asyncio
async def test_create_and_update_persist_and_return_provider_profile(monkeypatch, tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)

    created = await model_routes.create_model(ModelConfigRequest(
        name="x", base_url="https://example.test/v1", model_id="m",
        provider_profile="openrouter",
    ))
    updated = await model_routes.update_model(created.id, ModelConfigRequest(
        name="x", base_url="https://example.test/v1", model_id="m",
        provider_profile="custom-openai",
    ))

    assert created.provider_profile == "openrouter"
    assert updated.provider_profile == "custom-openai"
    assert store.get(created.id).provider_profile == "custom-openai"
