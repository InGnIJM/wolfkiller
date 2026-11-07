import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.providers.registry import ProviderRegistry
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


def test_request_rejects_unknown_reasoning_effort():
    with pytest.raises(ValidationError, match="reasoning_effort"):
        ModelConfigRequest(
            name="n", base_url="https://x", model_id="m", reasoning_effort="ultra",
        )
    assert ModelConfigRequest(
        name="n", base_url="https://x", model_id="m", reasoning_effort="none",
    ).reasoning_effort == "none"


def test_test_request_rejects_unknown_reasoning_effort():
    with pytest.raises(ValidationError, match="reasoning_effort"):
        ModelTestRequest(
            base_url="https://x", model_id="m", reasoning_effort="ultra",
        )


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
async def test_list_models_attaches_recorded_latency(store, monkeypatch):
    """The benchmark wizard budgets a run from this, so it has to be the
    resolved endpoint identity, not the raw ``auto`` profile."""
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    config = _stored(store)
    profile = ProviderRegistry().resolve(
        config.provider_profile, config.base_url, config.model_id,
    ).profile_id
    seen: list[dict[str, object]] = []

    def _stats(**kwargs):
        seen.append(kwargs)
        return {(config.model_id, profile): {
            "samples": 30, "avg_ms": 12000, "p50_ms": 9000, "p90_ms": 30000,
        }}

    monkeypatch.setattr(
        model_routes, "get_repository", lambda: SimpleNamespace(model_latency_stats=_stats),
    )

    response = await model_routes.list_models()

    latency = response.configs[0].latency
    assert latency is not None
    assert (latency.samples, latency.avg_ms, latency.p50_ms, latency.p90_ms) == (
        30, 12000, 9000, 30000,
    )
    assert list(seen[0]) == ["since"]
    assert str(seen[0]["since"]).startswith("20")
    # The wizard reads these snake_case keys straight off the wire.
    assert set(response.model_dump()["configs"][0]["latency"]) == {
        "samples", "avg_ms", "p50_ms", "p90_ms",
    }


@pytest.mark.asyncio
async def test_list_models_without_repository_reports_no_latency(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    _stored(store)
    monkeypatch.setattr(model_routes, "get_repository", lambda: None)

    response = await model_routes.list_models()

    assert response.configs[0].latency is None


@pytest.mark.asyncio
async def test_list_models_ignores_history_from_another_endpoint(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    config = _stored(store)
    monkeypatch.setattr(
        model_routes, "get_repository",
        lambda: SimpleNamespace(model_latency_stats=lambda **_: {
            (config.model_id, "some-other-profile"): {
                "samples": 30, "avg_ms": 1, "p50_ms": 1, "p90_ms": 1,
            },
        }),
    )

    response = await model_routes.list_models()

    assert response.configs[0].latency is None


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
async def test_create_model_round_trips_reasoning_effort(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)

    response = await model_routes.create_model(ModelConfigRequest(
        name="Vote DeepSeek", base_url="https://x", model_id="m",
        reasoning_effort="none",
    ))

    assert response.reasoning_effort == "none"
    stored = store.list_all()
    assert len(stored) == 1 and stored[0].reasoning_effort == "none"


@pytest.mark.asyncio
async def test_update_model_changes_and_clears_reasoning_effort(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store)

    updated = await model_routes.update_model(cfg.id, ModelConfigRequest(
        name=cfg.name, base_url=cfg.base_url, model_id=cfg.model_id,
        reasoning_effort="low",
    ))
    assert updated.reasoning_effort == "low"

    cleared = await model_routes.update_model(cfg.id, ModelConfigRequest(
        name=cfg.name, base_url=cfg.base_url, model_id=cfg.model_id,
    ))
    assert cleared.reasoning_effort is None
    assert store.get(cfg.id).reasoning_effort is None


@pytest.mark.asyncio
async def test_connection_test_forwards_reasoning_effort(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    seen = []
    client = MagicMock()
    client.probe.return_value = _CAPABILITIES
    client.aclose = AsyncMock()

    def build(**kwargs):
        seen.append(kwargs["config"])
        return client

    monkeypatch.setattr(model_routes, "LLMClient", build)
    response = await model_routes.test_model(ModelTestRequest(
        base_url="https://example.invalid/v1", model_id="fake",
        provider_profile="custom-openai", reasoning_effort="none",
    ))

    assert response.ok
    assert seen[0].reasoning_effort == "none"


@pytest.mark.asyncio
async def test_connection_test_empty_effort_clears_the_stored_value(
    store, monkeypatch,
):
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store)
    store.upsert(ModelConfig(
        id=cfg.id, name=cfg.name, base_url=cfg.base_url, model_id=cfg.model_id,
        reasoning_effort="high",
        created_at=cfg.created_at, updated_at=cfg.updated_at,
    ))
    seen = []
    client = MagicMock()
    client.probe.return_value = _CAPABILITIES
    client.aclose = AsyncMock()
    monkeypatch.setattr(
        model_routes, "LLMClient",
        lambda **kwargs: (seen.append(kwargs["config"]), client)[1],
    )

    response = await model_routes.test_model(ModelTestRequest(
        config_id=cfg.id, reasoning_effort="",
    ))

    assert response.ok
    assert seen[0].reasoning_effort is None


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
async def test_update_delete_missing_returns_404(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
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
    _stored(store, api_key_encrypted="garbage-not-a-token")

    response = (await model_routes.list_models()).configs[0]

    assert response.key_invalid is True
    assert response.api_key_masked is None
    assert response.has_key is True


@pytest.mark.asyncio
async def test_config_without_key_has_no_key(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    _stored(store, api_key_encrypted="")

    response = (await model_routes.list_models()).configs[0]

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
async def test_connection_test_refuses_an_edited_base_url_with_the_stored_key(store, monkeypatch):
    """A stored key must never travel to an endpoint the request chose.

    ``PUT`` already refuses to move an endpoint without a re-entered key; the
    probe skipped that rule, so one unauthenticated local request carrying a
    ``config_id`` and an attacker's ``base_url`` turned the probe into a
    credential forwarder.
    """
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        with pytest.raises(HTTPException) as exc:
            await model_routes.test_model(ModelTestRequest(
                config_id=cfg.id, base_url="https://attacker.test/v1",
            ))

    assert exc.value.status_code == 400
    assert "sk-stored-key" not in str(exc.value.detail)
    # The key was never even decrypted, let alone sent anywhere.
    assert mock_client.call_args_list == []


@pytest.mark.asyncio
async def test_connection_test_refuses_an_edited_strict_base_url_with_the_stored_key(
    store, monkeypatch,
):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        with pytest.raises(HTTPException) as exc:
            await model_routes.test_model(ModelTestRequest(
                config_id=cfg.id, strict_base_url="https://attacker.test/beta",
            ))

    assert exc.value.status_code == 400
    assert mock_client.call_args_list == []


@pytest.mark.asyncio
async def test_connection_test_refuses_an_edited_profile_with_the_stored_key(store, monkeypatch):
    """A profile can reroute the strict call, so it is an endpoint change too."""
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        with pytest.raises(HTTPException) as exc:
            await model_routes.test_model(ModelTestRequest(
                config_id=cfg.id, provider_profile="anthropic",
            ))

    assert exc.value.status_code == 400
    assert mock_client.call_args_list == []


@pytest.mark.asyncio
async def test_connection_test_treats_a_trailing_slash_as_the_same_endpoint(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        response = await model_routes.test_model(ModelTestRequest(
            config_id=cfg.id, base_url="https://api.deepseek.com/v1/",
        ))

    assert response.ok is True
    assert mock_client.call_args_list[0].kwargs["config"].api_key == "sk-stored-key"


@pytest.mark.asyncio
async def test_connection_test_allows_an_edited_endpoint_with_a_provided_key(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    crypto = ModelKeyCrypto()
    cfg = _stored(store, api_key_encrypted=crypto.encrypt("sk-stored-key"))
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        response = await model_routes.test_model(ModelTestRequest(
            config_id=cfg.id, base_url="https://other.test/v1", api_key="sk-new-key",
        ))

    assert response.ok is True
    assert mock_client.call_args_list[0].kwargs["config"].api_key == "sk-new-key"


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
async def test_connection_test_by_id_prefers_edited_base_url_and_model_id(store, monkeypatch):
    """The dialog gates saving on a test, so edited fields must beat stored ones."""
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, api_key_encrypted="")
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        await model_routes.test_model(ModelTestRequest(
            config_id=cfg.id, base_url="https://edited.test/v1", model_id="edited-model",
        ))

    config = mock_client.call_args_list[0].kwargs["config"]
    assert config.base_url == "https://edited.test/v1"
    assert config.model_id == "edited-model"


@pytest.mark.asyncio
async def test_connection_test_by_id_prefers_edited_strict_base_url(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = _stored(store, api_key_encrypted="")
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        await model_routes.test_model(ModelTestRequest(
            config_id=cfg.id, strict_base_url="https://edited-strict.test/beta",
        ))

    calls = mock_client.call_args_list
    assert calls[0].kwargs["config"].strict_base_url == "https://edited-strict.test/beta"
    assert calls[1].kwargs["config"].base_url == "https://edited-strict.test/beta"


@pytest.mark.asyncio
async def test_connection_test_by_id_blank_strict_base_url_clears_stored_value(store, monkeypatch):
    """Clearing the strict address must not silently fall back to the stored one."""
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = ModelConfig.new(
        name="DeepSeek Strict", base_url="https://api.deepseek.com/v1",
        model_id="deepseek-v4-pro", strict_base_url="https://stored-strict.test/beta",
    )
    store.upsert(cfg)
    with patch.object(model_routes, "LLMClient", autospec=True) as mock_client:
        mock_client.return_value.probe.return_value = _CAPABILITIES
        await model_routes.test_model(ModelTestRequest(config_id=cfg.id, strict_base_url=""))

    calls = mock_client.call_args_list
    assert calls[0].kwargs["config"].strict_base_url == "https://api.deepseek.com/beta"


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


# ── Latency aggregate caching ─────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_latency_cache():
    model_routes._latency_cache = None
    yield
    model_routes._latency_cache = None


@pytest.mark.asyncio
async def test_list_models_caches_the_latency_aggregate(store, monkeypatch):
    calls: list[str] = []

    def _stats(*, since):
        calls.append(since)
        return {}

    repository = SimpleNamespace(model_latency_stats=_stats)
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    monkeypatch.setattr(model_routes, "get_repository", lambda: repository)

    await model_routes.list_models()
    await model_routes.list_models()

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_the_latency_cache_expires_after_the_ttl(store, monkeypatch):
    calls: list[str] = []
    clock = [1000.0]

    def _stats(*, since):
        calls.append(since)
        return {}

    repository = SimpleNamespace(model_latency_stats=_stats)
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    monkeypatch.setattr(model_routes.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(model_routes, "get_repository", lambda: repository)

    await model_routes.list_models()
    clock[0] += model_routes.LATENCY_CACHE_TTL_SECONDS - 1
    await model_routes.list_models()
    assert len(calls) == 1

    clock[0] += 2
    await model_routes.list_models()
    assert len(calls) == 2


def test_the_latency_cache_is_scoped_to_one_repository(monkeypatch):
    counters = {"a": 0, "b": 0}

    def _repository(name):
        def _stats(*, since):
            counters[name] += 1
            return {}
        return SimpleNamespace(model_latency_stats=_stats)

    current = {"repository": _repository("a")}
    monkeypatch.setattr(model_routes, "get_repository", lambda: current["repository"])

    model_routes._latency_stats()
    model_routes._latency_stats()
    current["repository"] = _repository("b")
    model_routes._latency_stats()

    assert counters == {"a": 1, "b": 1}


def test_warm_latency_cache_fills_the_cache(monkeypatch):
    calls: list[str] = []

    def _stats(*, since):
        calls.append(since)
        return {}

    monkeypatch.setattr(
        model_routes, "get_repository",
        lambda: SimpleNamespace(model_latency_stats=_stats),
    )

    model_routes.warm_latency_cache()

    assert len(calls) == 1
    assert model_routes._latency_cache is not None


def test_warm_latency_cache_logs_and_swallows_failures(monkeypatch, caplog):
    def _boom(*, since):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        model_routes, "get_repository",
        lambda: SimpleNamespace(model_latency_stats=_boom),
    )

    with caplog.at_level(logging.WARNING, logger=model_routes.__name__):
        model_routes.warm_latency_cache()  # must not raise

    assert any("warmup failed" in record.getMessage() for record in caplog.records)


def test_to_response_builds_its_own_crypto_when_not_supplied(store):
    config = _stored(store)

    response = model_routes._to_response(config)

    assert response.id == config.id
    assert response.latency is None


# ── Model catalog ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_catalog_lists_upstream_model_ids(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = ModelConfig.new(
        name="Go", base_url="https://opencode.ai/zen/go/v1",
        model_id="deepseek-v4.1-flash",
    )
    store.upsert(cfg)
    calls = {}

    async def fake_fetch(base_url, api_key, headers, api_mode):
        calls.update(
            base_url=base_url, api_key=api_key,
            headers=headers, api_mode=api_mode,
        )
        return ["deepseek-v4.1-flash", "mimo-v2.6-flash"]

    monkeypatch.setattr(model_routes, "_fetch_model_ids", fake_fetch)
    response = await model_routes.model_catalog(cfg.id)

    assert response.model_ids == ["deepseek-v4.1-flash", "mimo-v2.6-flash"]
    assert response.error is None
    assert calls["base_url"] == "https://opencode.ai/zen/go/v1"


@pytest.mark.asyncio
async def test_catalog_decrypts_the_stored_key(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = ModelConfig.new(
        name="Keyed", base_url="https://x/v1", model_id="m",
        api_key_encrypted=ModelKeyCrypto().encrypt("sk-live"),
    )
    store.upsert(cfg)
    calls = {}

    async def fake_fetch(base_url, api_key, headers, api_mode):
        calls["api_key"] = api_key
        return []

    monkeypatch.setattr(model_routes, "_fetch_model_ids", fake_fetch)
    response = await model_routes.model_catalog(cfg.id)

    assert calls["api_key"] == "sk-live"
    assert response.error is None


@pytest.mark.asyncio
async def test_catalog_unknown_config_returns_404(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    with pytest.raises(HTTPException) as exc:
        await model_routes.model_catalog("missing")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_catalog_undecryptable_key_returns_400(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = ModelConfig.new(
        name="Bad", base_url="https://x/v1", model_id="m",
        api_key_encrypted="not-fernet",
    )
    store.upsert(cfg)
    with pytest.raises(HTTPException) as exc:
        await model_routes.model_catalog(cfg.id)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_catalog_system_one_reports_no_listing(store, monkeypatch):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = ModelConfig.new(
        name="Jev", base_url="https://api.typesafe.ai",
        model_id="jev-latest", provider_profile="jev",
    )
    store.upsert(cfg)
    response = await model_routes.model_catalog(cfg.id)

    assert response.model_ids == []
    assert response.error


@pytest.mark.asyncio
async def test_catalog_upstream_failure_is_reported_not_raised(
    store, monkeypatch,
):
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    cfg = ModelConfig.new(name="Down", base_url="https://x/v1", model_id="m")
    store.upsert(cfg)

    async def boom(*args):
        raise TimeoutError("connect timeout")

    monkeypatch.setattr(model_routes, "_fetch_model_ids", boom)
    response = await model_routes.model_catalog(cfg.id)

    assert response.model_ids == []
    assert "TimeoutError" in response.error


@pytest.mark.asyncio
async def test_fetch_model_ids_sends_bearer_and_parses_data(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}, "junk", {}]}

    class _Client:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return _Response()

    monkeypatch.setattr(model_routes.httpx, "AsyncClient", _Client)
    ids = await model_routes._fetch_model_ids(
        "https://x/v1/", "sk", {"X-Trace": "1"}, "chat_completions",
    )

    assert ids == ["a", "b"]
    assert captured["url"] == "https://x/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sk"
    assert captured["headers"]["X-Trace"] == "1"


@pytest.mark.asyncio
async def test_fetch_model_ids_uses_anthropic_auth(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "claude-1"}]}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            captured["headers"] = headers
            return _Response()

    monkeypatch.setattr(model_routes.httpx, "AsyncClient", _Client)
    ids = await model_routes._fetch_model_ids(
        "https://api.anthropic.com/v1", "ak", {}, "anthropic_messages",
    )

    assert ids == ["claude-1"]
    assert captured["headers"]["x-api-key"] == "ak"
    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_fetch_model_ids_rejects_an_unexpected_payload(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"unexpected": True}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            return _Response()

    monkeypatch.setattr(model_routes.httpx, "AsyncClient", _Client)
    with pytest.raises(ValueError, match="payload"):
        await model_routes._fetch_model_ids(
            "https://x/v1", "", {}, "chat_completions",
        )
