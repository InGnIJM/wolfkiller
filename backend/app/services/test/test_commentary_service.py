from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from app.persistence.repository import GameRepository
from app.services.commentary_service import (
    CommentaryService,
    _default_client_factory,
    extract_roster_and_roles,
    fetch_all_audience_events,
    resolve_commentary_client_config,
)
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig
from app.stores.model_key_crypto import ModelKeyCrypto


@dataclass
class _FakeStructured:
    payload: dict[str, Any]


class _FakeLLMClient:
    def __init__(self, text: str = "精彩解说", fail: Exception | None = None, model_name: str = "fake-model"):
        self.text = text
        self.fail = fail
        self.model_name = model_name
        self.closed = False
        self.calls: list[Any] = []

    async def ainvoke_json(self, messages, *, tool_name: str, schema: dict[str, Any]):
        self.calls.append((messages, tool_name, schema))
        if self.fail is not None:
            raise self.fail
        return _FakeStructured({"text": self.text})

    async def aclose(self) -> None:
        self.closed = True


class _FakeWSManager:
    def __init__(self):
        self.wakes: list[str] = []

    def wake_v2(self, game_id: str) -> None:
        self.wakes.append(game_id)


def _seed_game(
    repository: GameRepository,
    game_id: str = "g-1",
    *,
    enabled: bool = True,
    config_id: str | None = None,
) -> None:
    repository.create_game(
        game_id=game_id,
        name="g",
        config={
            "role_counts": {"wolf-killer-werewolf": 1, "wolf-killer-seer": 1},
            "commentary": {"enabled": enabled, "commentary_config_id": config_id},
        },
        execution_status="running",
        source="native",
        model_snapshot=[],
    )
    repository.commit_step(
        game_id=game_id,
        expected_storage_revision=0,
        execution_generation=1,
        step_key="init",
        input_digest="i",
        result_digest="r",
        checkpoint={"checkpoint_version": 1},
        domain_events=[],
        audience_events=[
            {
                "event_id": "e-1",
                "event_type": "wolf_chat_message",
                "payload": {"round_number": 1, "seat": 2, "text": "刀1"},
                "schema_version": 1,
            },
            {
                "event_id": "e-2",
                "event_type": "speech",
                "payload": {"round_number": 1, "player_seat": 1, "text": "我是预言家", "phase": "speech"},
                "schema_version": 1,
            },
        ],
        audience_state={
            "players": {
                "1": {"seat_number": 1, "role": "wolf-killer-seer", "camp": "good", "is_alive": True},
                "2": {"seat_number": 2, "role": "wolf-killer-werewolf", "camp": "werewolf", "is_alive": True},
            },
        },
    )


def test_resolve_commentary_client_config_env_and_saved(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "models.json"
    store = JsonModelConfigStore(str(store_path))
    monkeypatch.setattr("app.services.commentary_service.get_model_config_store", lambda: store)

    default_cfg = resolve_commentary_client_config(None)
    assert default_cfg.model_id

    plain_model = ModelConfig.new(
        name="Plain", base_url="https://api.example.com/v1", model_id="m-plain",
        api_key_encrypted="", temperature=0.7,
    )
    store.upsert(plain_model)
    cfg = resolve_commentary_client_config(plain_model.id)
    assert cfg.model_id == "m-plain"
    assert cfg.temperature == 0.7

    crypto = ModelKeyCrypto()
    enc_model = ModelConfig.new(
        name="Enc", base_url="https://api.example.com/v1", model_id="m-enc",
        api_key_encrypted=crypto.encrypt("sk-secret"), temperature=None,
    )
    store.upsert(enc_model)
    cfg_enc = resolve_commentary_client_config(enc_model.id)
    assert cfg_enc.api_key == "sk-secret"

    bad_model = ModelConfig.new(
        name="Bad", base_url="https://api.example.com/v1", model_id="m-bad",
        api_key_encrypted="not-valid-ciphertext",
    )
    store.upsert(bad_model)
    with pytest.raises(ValueError, match="api key is invalid"):
        resolve_commentary_client_config(bad_model.id)

    with pytest.raises(ValueError, match="unknown model config"):
        resolve_commentary_client_config("missing-id")


@pytest.mark.asyncio
async def test_commentary_service_phase_triggers_dedup_and_wake(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    ws = _FakeWSManager()
    clients: list[_FakeLLMClient] = []

    def factory(_config_id: str | None) -> _FakeLLMClient:
        c = _FakeLLMClient(text="本轮解说正文")
        clients.append(c)
        return c

    service = CommentaryService(
        repository=repository,
        ws_manager=ws,
        data_dir=str(tmp_path),
        client_factory=factory,
        settle_delay_seconds=0.0,
    )
    try:
        _seed_game(repository, "g-1", enabled=True)

        # Unrelated phase or missing args are ignored
        await service.on_phase_changed(game_id="g-1", phase="speech", round_number=1)
        await service.on_phase_changed(game_id=None, phase="dawn", round_number=1)
        await service.on_phase_changed(game_id="g-1", phase="night", round_number=0)
        await service.wait_pending()
        assert len(clients) == 0

        # Dawn triggers round 1 night commentary
        await service.on_phase_changed(game_id="g-1", phase="dawn", round_number=1)
        await service.wait_pending()
        assert len(clients) == 1
        assert clients[0].closed is True
        assert ws.wakes == ["g-1"]

        # Duplicate trigger for the same (round=1, beat="night") is skipped
        await service.on_phase_changed(game_id="g-1", phase="sheriff_election", round_number=1)
        await service.wait_pending()
        assert len(clients) == 1

        # Append a new audience event and trigger day commentary on phase='night'
        repository.append_audience_events(
            "g-1",
            [{
                "event_id": "e-3",
                "event_type": "vote_result",
                "payload": {"round_number": 1, "exiled_seat": 2, "counts": {"2": 1}},
            }],
            step_key="step-vote",
        )
        await service.on_phase_changed(game_id="g-1", phase="night", round_number=1)
        await service.wait_pending()
        assert len(clients) == 2

        # Trigger game_over -> final commentary
        repository.append_audience_events(
            "g-1",
            [{
                "event_id": "e-4",
                "event_type": "winner",
                "payload": {"winning_camp": "good", "reason": "all_wolves_dead"},
            }],
            step_key="step-win",
        )
        await service.on_game_over(game_id="g-1")
        await service.wait_pending()
        assert len(clients) == 3

        page = repository.get_audience_events("g-1", after_seq=0, limit=20)
        comm_events = [e for e in page["events"] if e["event_type"] == "commentary"]
        assert [e["payload"]["beat"] for e in comm_events] == ["night", "day", "final"]
        assert all(e["payload"]["status"] == "ok" for e in comm_events)
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_commentary_service_disabled_empty_slice_and_failure_isolation(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    ws = _FakeWSManager()
    try:
        # 1. Disabled commentary game does nothing
        _seed_game(repository, "g-disabled", enabled=False)
        service_disabled = CommentaryService(
            repository=repository,
            ws_manager=ws,
            data_dir=str(tmp_path),
            client_factory=lambda _: _FakeLLMClient(),
            settle_delay_seconds=0.0,
        )
        await service_disabled.on_phase_changed(game_id="g-disabled", phase="dawn", round_number=1)
        await service_disabled. on_game_over(game_id="missing-game")
        await service_disabled.wait_pending()

        # 2. Failing LLM writes status='failed' commentary event without crashing
        _seed_game(repository, "g-fail", enabled=True)
        fail_client = _FakeLLMClient(fail=RuntimeError("boom"))
        service_fail = CommentaryService(
            repository=repository,
            ws_manager=ws,
            data_dir=str(tmp_path),
            client_factory=lambda _: fail_client,
            settle_delay_seconds=0.0,
        )
        await service_fail.on_phase_changed(game_id="g-fail", phase="dawn", round_number=1)
        await service_fail.wait_pending()
        assert fail_client.closed is True
        page = repository.get_audience_events("g-fail", after_seq=0, limit=20)
        comm = [e for e in page["events"] if e["event_type"] == "commentary"]
        assert len(comm) == 1
        assert comm[0]["payload"]["status"] == "failed"
        assert comm[0]["payload"]["failure_code"] is not None

        # 3. Empty slice (no new non-skipped events since cursor) skips calling LLM
        called = 0

        def count_factory(_cfg):  # pragma: no cover - asserted not called
            nonlocal called
            called += 1
            return _FakeLLMClient()

        service_empty = CommentaryService(
            repository=repository,
            ws_manager=ws,
            data_dir=str(tmp_path),
            client_factory=count_factory,
            settle_delay_seconds=0.0,
        )
        await service_empty.on_phase_changed(game_id="g-fail", phase="night", round_number=1)
        await service_empty.wait_pending()
        assert called == 0
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_commentary_service_aclose_cancels_in_flight_tasks(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    ws = _FakeWSManager()
    started = asyncio.Event()

    class _SlowClient(_FakeLLMClient):
        async def ainvoke_json(self, messages, *, tool_name: str, schema: dict[str, Any]):
            started.set()
            await asyncio.sleep(10)  # pragma: no cover - cancelled before wake
            return _FakeStructured({"text": "never"})  # pragma: no cover

    service = CommentaryService(
        repository=repository,
        ws_manager=ws,
        data_dir=str(tmp_path),
        client_factory=lambda _: _SlowClient(),
        settle_delay_seconds=0.0,
    )
    try:
        _seed_game(repository, "g-slow", enabled=True)
        await service.on_phase_changed(game_id="g-slow", phase="dawn", round_number=1)
        await asyncio.wait_for(started.wait(), timeout=2.0)
        await service.aclose()
        # Spawning after aclose is a clean no-op
        await service.on_phase_changed(game_id="g-slow", phase="dawn", round_number=1)
        await service.on_game_over(game_id=None)
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_helpers_and_edge_branches(tmp_path, monkeypatch) -> None:
    repository = GameRepository(tmp_path)
    try:
        _seed_game(repository, "g-page", enabled=True)
        # Multi-page fetch (page_limit=1)
        events = fetch_all_audience_events(repository, "g-page", page_limit=1)
        assert len(events) == 2

        # Snapshot missing or malformed player entries
        repository.create_game(
            game_id="g-no-snap",
            name="ns",
            config={"commentary": {"enabled": True}},
            execution_status="running",
            source="native",
            model_snapshot=[],
        )
        assert extract_roster_and_roles(repository, "g-no-snap") == ({}, {})

        repository.commit_step(
            game_id="g-no-snap",
            expected_storage_revision=0,
            execution_generation=1,
            step_key="s1",
            input_digest="i",
            result_digest="r",
            checkpoint={"checkpoint_version": 1},
            domain_events=[],
            audience_events=[{
                "event_id": "e1",
                "event_type": "speech",
                "payload": {"round_number": 1, "player_seat": 1, "text": "hi", "phase": "speech"},
                "schema_version": 1,
            }],
            audience_state={"players": {"bad": "not-dict", "2": {"seat_number": "nan"}}},
        )
        players, roles = extract_roster_and_roles(repository, "g-no-snap")
        assert roles == {}

        # Settle delay > 0, ws_manager=None, and factory raising before client created
        def broken_factory(_cfg):
            raise RuntimeError("cannot build client")

        service = CommentaryService(
            repository=repository,
            ws_manager=None,
            data_dir=str(tmp_path),
            client_factory=broken_factory,
            settle_delay_seconds=0.001,
        )
        await service.on_phase_changed(game_id="g-no-snap", phase="dawn", round_number=1)
        await service.wait_pending()
        await service.aclose()

        # Default client factory builds and closes cleanly
        client = _default_client_factory(None)
        await client.aclose()
    finally:
        repository.close()


def test_default_client_factory_owns_a_dedicated_http_pool():
    """One-shot commentary/review clients must not share the game seats' pool."""
    client = _default_client_factory(None)
    try:
        assert client._dedicated_http_clients is True
    finally:
        asyncio.run(client.aclose())
