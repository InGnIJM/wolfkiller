"""The Responses dialect must survive schema validation, routes and snapshots."""

from unittest.mock import MagicMock

import pytest

from app.agents.llm_client import LLMClient
from app.api.model_schemas import ModelConfigRequest, ModelTestRequest, public_model_snapshot
from app.api.routes import model_routes
from app.agents.providers.base import API_MODE_OPENAI_RESPONSES
from app.stores.model_config_store import JsonModelConfigStore

CAPABILITIES = {
    "tools": True, "strict_tools": False, "json_output": True,
    "reasoning_effort": False, "temperature": True,
}
ZEN_RESPONSES = "https://opencode.ai/zen/v1/responses"
MUSE = "muse-spark-1.3-contributor-free"


def _patched_client(monkeypatch):
    client = MagicMock(spec=LLMClient)
    client.probe.return_value = CAPABILITIES
    llm_client = MagicMock(return_value=client)
    monkeypatch.setattr(model_routes, "LLMClient", llm_client)
    return llm_client


def test_request_accepts_the_explicit_responses_profile():
    request = ModelConfigRequest(
        name="x", base_url="https://api.openai.com/v1", model_id="m",
        provider_profile="openai-responses",
    )

    assert request.provider_profile == "openai-responses"


def test_snapshot_publishes_the_responses_profile_id():
    published = public_model_snapshot([{
        "config_id": "c", "name": "OpenAI", "model_id": "m",
        "base_url": "https://api.openai.com/v1",
        "provider_profile": "openai-responses", "count": 1, "seats": [1],
    }])

    assert published and published[0]["provider_profile"] == "openai-responses"


@pytest.mark.asyncio
async def test_connection_test_uses_the_responses_dialect_for_a_responses_url(monkeypatch):
    llm_client = _patched_client(monkeypatch)

    response = await model_routes.test_model(ModelTestRequest(
        base_url=ZEN_RESPONSES, model_id=MUSE,
    ))

    assert response.ok is True
    config = llm_client.call_args.kwargs["config"]
    assert config.base_url == ZEN_RESPONSES
    assert llm_client.call_args.kwargs["config"].provider_profile == "opencode"
    assert llm_client.call_args.kwargs["config"].headers == ()
    # The resolved profile must be the Responses dialect, not chat completions.
    from app.agents.providers.registry import ProviderRegistry
    resolved = ProviderRegistry().resolve("opencode", ZEN_RESPONSES, MUSE)
    assert resolved.api_mode == API_MODE_OPENAI_RESPONSES


@pytest.mark.asyncio
async def test_a_saved_responses_config_round_trips(monkeypatch, tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)

    created = await model_routes.create_model(ModelConfigRequest(
        name="Muse", base_url=ZEN_RESPONSES, model_id=MUSE,
        provider_profile="opencode",
    ))

    assert created.base_url == ZEN_RESPONSES
    assert store.get(created.id).base_url == ZEN_RESPONSES
