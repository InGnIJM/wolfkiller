"""Dedicated vote-model client: creation, persistence, recovery, shutdown.

A game created with ``vote_model_config_id`` builds one LLMClient shared by
every seat's ``exile_vote`` contract. The choice is frozen into the repository
config doc so a resumed game rebuilds the same client; omitting the option
must leave every existing behaviour untouched.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import app.services.game_service as service_module
from app.agents.cache_route import shared_session_id
from app.agents.providers.base import (
    API_MODE_CHAT_COMPLETIONS,
    CacheMode,
    ModelCapabilities,
    ProviderProfile,
)
from app.api.websocket.ws_handler import WSManager
from app.agents.llm_client import env_default_client_config
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.models.game import GameConfig, GameState, PlayerState
from app.persistence.repository import GameRepository
from app.services.game_service import (
    GameService,
    _model_parameters,
    _parameter_digest,
)
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig


def _store_with(tmp_path, monkeypatch, **fields):
    values = dict(
        name="Vote Model", base_url="https://vote.test/v1", model_id="vote-model",
    )
    values.update(fields)
    cfg = ModelConfig.new(**values)
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    store.upsert(cfg)
    monkeypatch.setattr(service_module, "get_model_config_store", lambda: store)
    return cfg, store


class _FakeClient:
    instances = []

    def __init__(self, *, config, dedicated_http_clients: bool = False):
        self.config = config
        self.model_name = config.model_id
        self.provider_profile = SimpleNamespace(profile_id="custom-openai")
        self.closed = 0
        type(self).instances.append(self)

    async def aclose(self):
        self.closed += 1


async def _noop_start(self):
    return None


@pytest.fixture
def fake_clients(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(service_module, "LLMClient", _FakeClient)
    monkeypatch.setattr(GameEngine, "start", _noop_start)
    return _FakeClient


@pytest.mark.asyncio
async def test_create_game_injects_one_shared_vote_client(
    tmp_path, monkeypatch, fake_clients,
):
    cfg, _ = _store_with(tmp_path, monkeypatch)
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
            vote_model_config_id=cfg.id,
        )
        await service._tasks[game_id]

        vote_client = fake_clients.instances[-1]
        assert len(fake_clients.instances) == 5
        assert vote_client.config.model_id == "vote-model"
        roles = service._engines[game_id].roles
        assert all(
            role.vote_llm_client is vote_client for role in roles.values()
        )
        assert service._vote_llm_clients[game_id] is vote_client

        doc = repository.get_game(game_id)["config"]["vote_model"]
        assert doc["config_id"] == cfg.id
        assert doc["parameters"]["model_id"] == "vote-model"
        assert doc["parameters_digest"] == _parameter_digest(doc["parameters"])
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_create_game_without_vote_model_changes_nothing(
    tmp_path, monkeypatch, fake_clients,
):
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
        )
        await service._tasks[game_id]

        assert len(fake_clients.instances) == 4
        roles = service._engines[game_id].roles
        assert all(role.vote_llm_client is None for role in roles.values())
        assert game_id not in service._vote_llm_clients
        assert "vote_model" not in repository.get_game(game_id)["config"]
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_create_game_rejects_an_unknown_vote_config(
    tmp_path, monkeypatch, fake_clients,
):
    monkeypatch.setattr(
        service_module, "get_model_config_store",
        lambda: JsonModelConfigStore(str(tmp_path / "models.json")),
    )
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        with pytest.raises(ValueError, match="unknown model config"):
            await service.create_game(vote_model_config_id="missing")
        assert not fake_clients.instances
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_close_game_clients_closes_the_vote_client_once(tmp_path):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    seat = _FakeClient(config=env_default_client_config())
    vote = _FakeClient(config=env_default_client_config())
    service._llm_clients["g"] = {1: seat, 2: seat}
    service._vote_llm_clients["g"] = vote

    await service._close_game_clients("g")

    assert vote.closed == 1
    assert seat.closed == 1
    assert "g" not in service._vote_llm_clients


def _state_with_players() -> GameState:
    state = GameState(
        game_id="game",
        config=GameConfig(role_counts={"wolf-killer-villager": 2}),
    )
    state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", "good")
        for seat in (1, 2)
    }
    return state


def _vote_model_record(tmp_path, monkeypatch, *, digest: str | None = None):
    cfg, _ = _store_with(tmp_path, monkeypatch)
    env = env_default_client_config()
    vote_config, _ = service_module._saved_client_config(cfg.id, env)
    parameters = _model_parameters(vote_config)
    return {
        "config": {
            "vote_model": {
                "config_id": cfg.id,
                "parameters": parameters,
                "parameters_digest": (
                    digest if digest is not None else _parameter_digest(parameters)
                ),
            },
        },
    }


def _engine_stub(service, monkeypatch):
    class Engine:
        def __init__(self, **kwargs):
            self.roles = kwargs["roles"]
            self._rng = service_module.random.Random(7)
            self.game_logger = SimpleNamespace(
                log_llm_call=lambda *args, **kwargs: None,
            )

        def load_restored_state(self, state, orchestration, codec):
            self.loaded = (state, orchestration, codec)

    monkeypatch.setattr(service_module, "GameEngine", Engine)


def test_recovered_engine_rebuilds_the_shared_vote_client(
    tmp_path, monkeypatch, fake_clients,
):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    env = env_default_client_config()
    monkeypatch.setattr(
        service, "_resolve_recovery_model_configs",
        lambda *_args: {1: env, 2: env},
    )
    _engine_stub(service, monkeypatch)
    record = _vote_model_record(tmp_path, monkeypatch)
    try:
        engine, clients = service._build_recovered_engine(state, {}, record)

        vote_client = fake_clients.instances[-1]
        assert len(fake_clients.instances) == 3
        assert vote_client.config.model_id == "vote-model"
        assert all(
            role.vote_llm_client is vote_client
            for role in engine.roles.values()
        )
        assert service._vote_llm_clients[state.game_id] is vote_client
    finally:
        asyncio.run(service.aclose())


def test_recovered_engine_rejects_a_tampered_vote_digest(
    tmp_path, monkeypatch, fake_clients,
):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    env = env_default_client_config()
    monkeypatch.setattr(
        service, "_resolve_recovery_model_configs",
        lambda *_args: {1: env, 2: env},
    )
    _engine_stub(service, monkeypatch)
    record = _vote_model_record(tmp_path, monkeypatch, digest="bogus")
    try:
        with pytest.raises(ValueError, match="checkpoint_corrupt"):
            service._build_recovered_engine(state, {}, record)
    finally:
        asyncio.run(service.aclose())


def test_recovered_engine_without_vote_model_keeps_seat_client(
    tmp_path, monkeypatch, fake_clients,
):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    env = env_default_client_config()
    monkeypatch.setattr(
        service, "_resolve_recovery_model_configs",
        lambda *_args: {1: env, 2: env},
    )
    _engine_stub(service, monkeypatch)
    try:
        engine, clients = service._build_recovered_engine(state, {}, {"config": {}})

        assert len(fake_clients.instances) == 2
        assert all(
            role.vote_llm_client is None for role in engine.roles.values()
        )
        assert state.game_id not in service._vote_llm_clients
    finally:
        asyncio.run(service.aclose())


def test_recovery_vote_model_rejects_malformed_entries(
    tmp_path, monkeypatch, fake_clients,
):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    try:
        assert service._resolve_recovery_vote_model(state, {"config": {}}) is None
        assert service._resolve_recovery_vote_model(state, {}) is None
        for entry in ("junk", {"config_id": 3}, {"config_id": "x"}):
            with pytest.raises(ValueError, match="model_config_missing"):
                service._resolve_recovery_vote_model(
                    state, {"config": {"vote_model": entry}},
                )
    finally:
        asyncio.run(service.aclose())


def test_recovery_vote_model_maps_store_failures(
    tmp_path, monkeypatch, fake_clients,
):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    parameters = _model_parameters(env_default_client_config())
    try:
        record = _vote_model_record(tmp_path, monkeypatch)
        record["config"]["vote_model"]["config_id"] = "missing"
        with pytest.raises(ValueError, match="model_config_missing"):
            service._resolve_recovery_vote_model(state, record)

        cfg, store = _store_with(
            tmp_path, monkeypatch, api_key_encrypted="garbage",
        )
        record["config"]["vote_model"]["config_id"] = cfg.id
        with pytest.raises(ValueError, match="model_key_unavailable"):
            service._resolve_recovery_vote_model(state, record)
    finally:
        asyncio.run(service.aclose())


def test_recovery_vote_model_accepts_env_default_and_records_drift(
    tmp_path, monkeypatch, fake_clients,
):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    env = env_default_client_config()
    parameters = _model_parameters(env)
    try:
        record = {"config": {"vote_model": {
            "config_id": None,
            "parameters": parameters,
            "parameters_digest": _parameter_digest(parameters),
        }}}
        resolved = service._resolve_recovery_vote_model(state, record)
        assert resolved.model_id == env.model_id
        assert state.game_id not in service._recovery_warnings

        drifted = dict(parameters, temperature=parameters["temperature"] + 0.5)
        record["config"]["vote_model"]["parameters"] = drifted
        record["config"]["vote_model"]["parameters_digest"] = _parameter_digest(
            drifted,
        )
        resolved = service._resolve_recovery_vote_model(state, record)
        assert resolved.temperature == drifted["temperature"]
        assert service._recovery_warnings[state.game_id] == ["model_drift"]

        broken = dict(parameters)
        del broken["model_id"]
        record["config"]["vote_model"]["parameters"] = broken
        record["config"]["vote_model"]["parameters_digest"] = _parameter_digest(
            broken,
        )
        with pytest.raises(ValueError, match="checkpoint_corrupt"):
            service._resolve_recovery_vote_model(state, record)
    finally:
        asyncio.run(service.aclose())


@pytest.mark.asyncio
async def test_recover_game_merges_the_vote_client_into_the_shared_session(
    tmp_path, monkeypatch, fake_clients,
):
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    repository.create_game(
        game_id="game", name="game", config={},
        execution_status="interrupted", source="native", model_snapshot=[],
    )
    monkeypatch.setattr(
        repository, "load_checkpoint",
        lambda _game_id: {"checkpoint": {}, "storage_revision": 1},
    )
    state = _state_with_players()
    state.registry_digest = service._registry_snapshot.digest
    monkeypatch.setattr(
        service._checkpoint_codec, "decode", lambda _doc: (state, {}),
    )
    seat_client = _FakeClient(config=env_default_client_config())
    vote_client = _FakeClient(config=env_default_client_config())

    class Engine:
        def __init__(self):
            self.game_logger = SimpleNamespace(
                log_operation=lambda *args, **kwargs: None,
            )

        async def restore(self, state, orchestration, codec):
            return None

    def fake_build(state_arg, orchestration, record):
        service._vote_llm_clients[state_arg.game_id] = vote_client
        return Engine(), {1: seat_client, 2: seat_client}

    shared: dict[str, object] = {}

    async def fake_share(game_id, clients, mode):
        shared["clients"] = clients

    monkeypatch.setattr(service, "_build_recovered_engine", fake_build)
    monkeypatch.setattr(service, "_share_cache_session", fake_share)
    try:
        await service.recover_game("game")
        assert shared["clients"][0] is vote_client
        assert shared["clients"][1] is seat_client
    finally:
        await service.aclose()
        repository.close()


def _routing_profile() -> ProviderProfile:
    return ProviderProfile(
        profile_id="opencode-go",
        api_mode=API_MODE_CHAT_COMPLETIONS,
        capabilities=ModelCapabilities(True, True, True, True, True),
        default_action_max_tokens=2048,
        session_header="x-opencode-session",
        cache_mode=CacheMode.ROUTING,
    )


@pytest.mark.asyncio
async def test_create_game_shares_the_cache_session_with_the_vote_client(
    tmp_path, monkeypatch,
):
    cfg, _ = _store_with(tmp_path, monkeypatch)
    sessions = []

    class RoutingClient:
        def __init__(self, *, config, dedicated_http_clients: bool = False):
            self.config = config
            self.provider_profile = _routing_profile()
            self.session_id = None
            sessions.append(self)

        async def set_session_id(self, session_id: str):
            self.session_id = session_id

        async def aclose(self):
            pass

    monkeypatch.setattr(service_module, "LLMClient", RoutingClient)
    monkeypatch.setattr(GameEngine, "start", _noop_start)
    monkeypatch.setenv("WOLFKILLER_SHARED_CACHE_SESSION", "on")

    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
            vote_model_config_id=cfg.id,
        )
        expected = shared_session_id(game_id, "x-opencode-session")
        assert [client.session_id for client in sessions] == [expected] * 5
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_create_game_applies_vote_model_overrides(
    tmp_path, monkeypatch, fake_clients,
):
    cfg, _ = _store_with(tmp_path, monkeypatch, reasoning_effort="high")
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
            vote_model_config_id=cfg.id,
            vote_model_id="mimo-v2.6-flash",
            vote_reasoning_effort="none",
        )
        await service._tasks[game_id]

        vote_client = fake_clients.instances[-1]
        assert vote_client.config.model_id == "mimo-v2.6-flash"
        assert vote_client.config.reasoning_effort == "none"
        assert vote_client.config.base_url == "https://vote.test/v1"

        doc = repository.get_game(game_id)["config"]["vote_model"]
        assert doc["parameters"]["model_id"] == "mimo-v2.6-flash"
        assert doc["parameters"]["reasoning_effort"] == "none"
        assert doc["parameters_digest"] == _parameter_digest(doc["parameters"])
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_vote_overrides_without_a_config_are_rejected(
    tmp_path, monkeypatch, fake_clients,
):
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        with pytest.raises(ValueError, match="vote_model_id"):
            await service.create_game(
                role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
                vote_model_id="mimo-v2.6-flash",
            )
        with pytest.raises(ValueError, match="vote_reasoning_effort"):
            await service.create_game(
                role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
                vote_reasoning_effort="low",
            )
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_create_game_rejects_invalid_vote_overrides(
    tmp_path, monkeypatch, fake_clients,
):
    cfg, _ = _store_with(tmp_path, monkeypatch)
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        with pytest.raises(ValueError, match="vote_model_id"):
            await service.create_game(
                role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
                vote_model_config_id=cfg.id, vote_model_id="  ",
            )
        with pytest.raises(ValueError, match="reasoning_effort"):
            await service.create_game(
                role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
                vote_model_config_id=cfg.id, vote_reasoning_effort="ultra",
            )
    finally:
        await service.aclose()
        repository.close()
