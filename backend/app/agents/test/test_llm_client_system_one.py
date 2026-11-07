"""LLMClient surface for the System One (Jev) dialect.

A system-one client cannot build chat models: every chat accessor must fail
loudly, while ``ainvoke_system_one`` delegates to the transport's ``decide``.
"""

import pytest

from app.agents.llm_client import LLMClient, LLMClientConfig


@pytest.fixture
def jev_config():
    return LLMClientConfig(
        base_url="https://api.typesafe.ai",
        api_key="key",
        model_id="jev-latest",
        temperature=0.7,
        max_tokens=512,
        strict_base_url="",
        provider_profile="jev",
    )


def test_system_one_client_is_flagged(jev_config):
    client = LLMClient(config=jev_config)

    assert client.is_system_one is True


def test_chat_clients_are_not_flagged(openrouter_like):
    client = LLMClient(config=openrouter_like)

    assert client.is_system_one is False


@pytest.fixture
def openrouter_like():
    return LLMClientConfig(
        base_url="https://openrouter.ai/api/v1",
        api_key="k",
        model_id="m",
        temperature=0.7,
        max_tokens=512,
        strict_base_url="",
    )


def test_get_model_fails_loudly_for_system_one(jev_config):
    client = LLMClient(config=jev_config)

    with pytest.raises(NotImplementedError, match="system_one"):
        client.get_model()


@pytest.mark.asyncio
async def test_ainvoke_system_one_delegates_to_the_transport(
    jev_config, monkeypatch,
):
    client = LLMClient(config=jev_config)
    calls = []

    async def fake_decide(config, profile, state, questions, **kwargs):
        calls.append((config, profile, state, questions))
        return "decision"

    monkeypatch.setattr(client._transport, "decide", fake_decide)

    result = await client.ainvoke_system_one("state text", {"q": {}})

    assert result == "decision"
    assert calls[0][0] is client._config
    assert calls[0][1] is client.provider_profile
    assert calls[0][2] == "state text"
    assert calls[0][3] == {"q": {}}


@pytest.mark.asyncio
async def test_ainvoke_system_one_rejects_non_system_one_profiles(
    openrouter_like,
):
    client = LLMClient(config=openrouter_like)

    with pytest.raises(NotImplementedError, match="system_one"):
        await client.ainvoke_system_one("s", {})


@pytest.mark.asyncio
async def test_aclose_releases_the_transports_http_client(
    jev_config, monkeypatch,
):
    client = LLMClient(config=jev_config)
    closed = []

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(client._transport, "aclose", fake_close)

    await client.aclose()

    assert closed == [True]


def test_probe_uses_a_minimal_decision_call(jev_config, monkeypatch):
    client = LLMClient(config=jev_config)
    calls = []

    async def fake_decide(config, profile, state, questions, **kwargs):
        calls.append((state, questions))
        from app.agents.providers.system_one import SystemOneResponse
        return SystemOneResponse(answers={"ping": {}}, usage=None)

    monkeypatch.setattr(client._transport, "decide", fake_decide)

    capabilities = client.probe()

    assert calls, "probe must exercise the real endpoint"
    assert capabilities["tools"] is False
    assert capabilities["json_output"] is False
