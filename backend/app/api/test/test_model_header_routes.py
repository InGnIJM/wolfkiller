"""Custom request headers: schema validation and route plumbing."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.agents.llm_client import LLMClient
from app.api.model_schemas import ModelConfigRequest, ModelTestRequest
from app.api.routes import model_routes
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig

CAPABILITIES = {
    "tools": True, "strict_tools": False, "json_output": True,
    "reasoning_effort": False, "temperature": True,
}


def _request(**overrides):
    values = {"name": "x", "base_url": "https://example.test/v1", "model_id": "m"}
    values.update(overrides)
    return ModelConfigRequest(**values)


def _patched_client(monkeypatch):
    client = MagicMock(spec=LLMClient)
    client.probe.return_value = CAPABILITIES
    llm_client = MagicMock(return_value=client)
    monkeypatch.setattr(model_routes, "LLMClient", llm_client)
    return llm_client


# ── schema validation ─────────────────────────────────────────

def test_request_defaults_headers_to_empty():
    assert _request().headers == {}


def test_request_accepts_a_custom_header():
    assert _request(headers={"http-referer": "https://a.test"}).headers == {
        "http-referer": "https://a.test",
    }


def test_request_accepts_the_opencode_profiles():
    for profile in ("opencode", "opencode-go"):
        assert _request(provider_profile=profile).provider_profile == profile


def test_request_rejects_an_unknown_opencode_like_profile():
    with pytest.raises(ValidationError):
        _request(provider_profile="opencode-zen")


def test_request_rejects_an_invalid_header_name():
    with pytest.raises(ValidationError, match="RFC 7230 token"):
        _request(headers={"bad name": "v"})


def test_request_rejects_a_reserved_header():
    with pytest.raises(ValidationError, match="managed by the SDK or gateway"):
        _request(headers={"Authorization": "Bearer x"})


def test_request_rejects_header_names_that_differ_only_by_case():
    with pytest.raises(ValidationError, match="duplicate header name"):
        _request(headers={"X-Extra": "1", "x-extra": "2"})


def test_request_rejects_a_header_value_with_a_newline():
    with pytest.raises(ValidationError, match="must not contain CR, LF or NUL"):
        _request(headers={"x-custom": "a\nb"})


def test_request_rejects_too_many_headers():
    with pytest.raises(ValidationError, match="at most 32 custom headers"):
        _request(headers={f"x-h{i}": "v" for i in range(33)})


def test_test_request_defaults_headers_to_none():
    assert ModelTestRequest(base_url="https://a.test/v1", model_id="m").headers is None


def test_test_request_accepts_an_empty_header_set():
    request = ModelTestRequest(
        base_url="https://a.test/v1", model_id="m", headers={},
    )

    assert request.headers == {}


def test_test_request_validates_headers():
    with pytest.raises(ValidationError, match="managed by the SDK or gateway"):
        ModelTestRequest(
            base_url="https://a.test/v1", model_id="m", headers={"host": "evil"},
        )


# ── routes ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_update_persist_and_return_headers(monkeypatch, tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)

    created = await model_routes.create_model(_request(
        provider_profile="opencode", headers={"x-opencode-request": "user-1"},
    ))
    updated = await model_routes.update_model(created.id, _request(
        provider_profile="opencode", headers={"x-opencode-request": "user-2"},
    ))

    assert created.headers == {"x-opencode-request": "user-1"}
    assert updated.headers == {"x-opencode-request": "user-2"}
    assert store.get(created.id).headers == {"x-opencode-request": "user-2"}


@pytest.mark.asyncio
async def test_update_clears_headers_when_an_empty_set_is_sent(monkeypatch, tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)

    created = await model_routes.create_model(_request(headers={"x-a": "1"}))
    updated = await model_routes.update_model(created.id, _request())

    assert updated.headers == {}
    assert store.get(created.id).headers == {}


@pytest.mark.asyncio
async def test_ad_hoc_connection_test_sends_the_supplied_headers(monkeypatch):
    llm_client = _patched_client(monkeypatch)

    await model_routes.test_model(ModelTestRequest(
        base_url="https://opencode.ai/zen/v1", model_id="m",
        headers={"x-opencode-request": "user-1"},
    ))

    assert llm_client.call_args.kwargs["config"].headers == (
        ("x-opencode-request", "user-1"),
    )


@pytest.mark.asyncio
async def test_stored_config_test_falls_back_to_its_saved_headers(monkeypatch, tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    stored = ModelConfig.new(
        "zen", "https://opencode.ai/zen/v1", "m",
        headers={"x-opencode-request": "user-1"},
    )
    store.upsert(stored)
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    llm_client = _patched_client(monkeypatch)

    await model_routes.test_model(ModelTestRequest(config_id=stored.id))

    assert llm_client.call_args.kwargs["config"].headers == (
        ("x-opencode-request", "user-1"),
    )


@pytest.mark.asyncio
async def test_stored_config_test_honours_an_explicit_empty_header_set(
    monkeypatch, tmp_path,
):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    stored = ModelConfig.new(
        "zen", "https://opencode.ai/zen/v1", "m", headers={"x-a": "1"},
    )
    store.upsert(stored)
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    llm_client = _patched_client(monkeypatch)

    await model_routes.test_model(ModelTestRequest(config_id=stored.id, headers={}))

    assert llm_client.call_args.kwargs["config"].headers == ()


@pytest.mark.asyncio
async def test_connection_test_without_headers_sends_none(monkeypatch):
    llm_client = _patched_client(monkeypatch)

    await model_routes.test_model(ModelTestRequest(
        base_url="https://example.test/v1", model_id="m",
    ))

    assert llm_client.call_args.kwargs["config"].headers == ()
