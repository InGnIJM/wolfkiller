"""Dedicated vote-model client: creation, persistence, recovery, shutdown.

A game created with ``vote_model_config_id`` builds one LLMClient shared by
every seat's ``exile_vote`` contract. The choice is frozen into the repository
config doc so a resumed game rebuilds the same client; omitting the option
must leave every existing behaviour untouched.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

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


# ── sheriff_vote routing ──────────────────────────────────────────────
#
# The shared vote client also owns ``sheriff_vote``: a chat model answers the
# usual night_json tool call, a System One decision model gets the ballot
# rewritten as a ``choice`` question, and the seat client keeps the seat-model
# fallback whenever no ballot spec can be derived.


class _DualClient:
    """Client double speaking both surfaces; flips on the jev profile."""

    instances = []
    next_answer = {"sheriff_vote": {"choice": "2", "confidence": 0.8}}

    def __init__(self, *, config, dedicated_http_clients: bool = False):
        self.config = config
        self.model_name = config.model_id
        self.provider_profile = SimpleNamespace(
            profile_id=config.provider_profile or "custom-openai",
        )
        self.is_system_one = config.provider_profile == "jev"
        self.json_calls: list = []
        self.system_one_calls: list = []
        type(self).instances.append(self)

    async def aclose(self):
        return None

    def invoke_json(self, messages, *, tool_name, schema):
        self.json_calls.append((tool_name, schema))
        return SimpleNamespace(
            payload={"action_type": "pass", "reasoning": "chat"}, usage=None,
        )

    def invoke_system_one(self, state, questions):
        self.system_one_calls.append((state, questions))
        return SimpleNamespace(answers=type(self).next_answer, usage=None)


@pytest.fixture
def dual_clients(monkeypatch):
    _DualClient.instances = []
    _DualClient.next_answer = {
        "sheriff_vote": {"choice": "2", "confidence": 0.8},
    }
    monkeypatch.setattr(service_module, "LLMClient", _DualClient)
    monkeypatch.setattr(GameEngine, "start", _noop_start)
    return _DualClient


def _office_ballot(state: GameState, candidates: set[int]) -> None:
    office = state.sheriff_office
    office.step = "vote"
    office.active = set(candidates)


def test_sheriff_vote_route_prefers_the_seat_client_without_a_vote_model():
    from app.services.game_service import _sheriff_vote_route

    seat = SimpleNamespace()
    assert _sheriff_vote_route(None, seat, GameState(game_id="g"), 2) == (
        seat, None,
    )


def test_sheriff_vote_route_sends_a_chat_vote_client_down_night_json():
    from app.services.game_service import _sheriff_vote_route

    vote, seat = SimpleNamespace(is_system_one=False), SimpleNamespace()
    assert _sheriff_vote_route(vote, seat, None, 2) == (vote, None)


def test_sheriff_vote_route_treats_a_mock_is_system_one_as_chat():
    """A bare MagicMock attribute is truthy but never ``True``.

    Test doubles land here constantly; reading the property with truthiness
    would send a chat double down the system-one path and lose the ballot.
    """
    from app.services.game_service import _sheriff_vote_route

    vote, seat = MagicMock(), SimpleNamespace()
    assert _sheriff_vote_route(vote, seat, None, 2) == (vote, None)


def test_sheriff_vote_route_falls_back_when_the_ballot_is_underivable():
    from app.services.game_service import _sheriff_vote_route

    vote, seat = SimpleNamespace(is_system_one=True), SimpleNamespace()
    # Missing state → nothing to derive criteria from.
    assert _sheriff_vote_route(vote, seat, None, 2) == (seat, None)
    # A non-ballot office step → same fallback.
    assert _sheriff_vote_route(vote, seat, GameState(game_id="g"), 2) == (
        seat, None,
    )


def test_sheriff_vote_route_hands_a_jev_client_a_ballot_decision():
    from app.services.game_service import _sheriff_vote_route

    vote, seat = SimpleNamespace(is_system_one=True), SimpleNamespace()
    state = _state_with_players()
    _office_ballot(state, {1})

    client, decision = _sheriff_vote_route(vote, seat, state, 2)

    assert client is vote
    criteria = decision["questions"]["sheriff_vote"]["criteria"]
    assert set(criteria) == {"1", "pass"}
    assert decision["answer_map"]["1"] == {
        "action_type": "vote", "target_seat": 1,
    }


@pytest.mark.asyncio
async def test_sheriff_vote_uses_the_jev_vote_client(
    tmp_path, monkeypatch, dual_clients,
):
    cfg, _ = _store_with(tmp_path, monkeypatch, provider_profile="jev")
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
            vote_model_config_id=cfg.id,
        )
        engine = service._engines[game_id]
        _office_ballot(engine.state, {1, 2})
        # The engine only seats players inside ``start``; a good-camp voter is
        # all ``sheriff_vote_decision`` needs to keep ``explode`` out.
        engine.state.players[3] = PlayerState(3, "wolf-killer-villager", "good")
        vote_client = service._vote_llm_clients[game_id]
        seat_client = service._llm_clients[game_id][3]
        assert vote_client.is_system_one is True

        payload = json.loads(engine._director._invoke(
            [{"role": "user", "content": "投警长票"}],
            "sheriff_vote", {"type": "object"}, 3,
        ))

        assert payload["action_type"] == "vote"
        assert payload["target_seat"] == 2
        assert payload["reasoning"].startswith("JEV 决策")
        state_text, questions = vote_client.system_one_calls[0]
        assert "投警长票" in state_text
        assert set(questions["sheriff_vote"]["criteria"]) == {"1", "2", "pass"}
        assert seat_client.json_calls == []
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_sheriff_vote_uses_a_chat_vote_client_over_night_json(
    tmp_path, monkeypatch, dual_clients,
):
    """A non-Jev vote model still owns the ballot, just on the chat tool."""
    cfg, _ = _store_with(tmp_path, monkeypatch)
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
            vote_model_config_id=cfg.id,
        )
        engine = service._engines[game_id]
        _office_ballot(engine.state, {1, 2})
        vote_client = service._vote_llm_clients[game_id]
        seat_client = service._llm_clients[game_id][3]
        assert vote_client.is_system_one is False

        payload = json.loads(engine._director._invoke(
            [{"role": "user", "content": "投警长票"}],
            "sheriff_vote", {"type": "object"}, 3,
        ))

        assert payload == {"action_type": "pass", "reasoning": "chat"}
        assert vote_client.json_calls[0][0] == "sheriff_vote"
        assert vote_client.system_one_calls == []
        assert seat_client.json_calls == []
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_sheriff_vote_without_a_vote_model_stays_on_the_seat(
    tmp_path, monkeypatch, dual_clients,
):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
        )
        engine = service._engines[game_id]
        _office_ballot(engine.state, {1, 2})
        seat_client = service._llm_clients[game_id][3]

        payload = json.loads(engine._director._invoke(
            [{"role": "user", "content": "投警长票"}],
            "sheriff_vote", {"type": "object"}, 3,
        ))

        assert payload == {"action_type": "pass", "reasoning": "chat"}
        assert seat_client.json_calls[0][0] == "sheriff_vote"
        assert seat_client.system_one_calls == []
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_sheriff_vote_falls_back_to_the_seat_off_the_ballot_step(
    tmp_path, monkeypatch, dual_clients,
):
    """A jev vote client cannot guess an option set the office never produced.

    Any ``sheriff_vote`` arriving outside the vote/pk steps (or before the
    engine bound one) has no declared criteria, so the seat model keeps the
    call rather than letting Jev invent candidates.
    """
    cfg, _ = _store_with(tmp_path, monkeypatch, provider_profile="jev")
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
            vote_model_config_id=cfg.id,
        )
        engine = service._engines[game_id]
        vote_client = service._vote_llm_clients[game_id]
        seat_client = service._llm_clients[game_id][3]

        payload = json.loads(engine._director._invoke(
            [{"role": "user", "content": "投警长票"}],
            "sheriff_vote", {"type": "object"}, 3,
        ))

        assert payload == {"action_type": "pass", "reasoning": "chat"}
        assert seat_client.json_calls[0][0] == "sheriff_vote"
        assert vote_client.system_one_calls == []
    finally:
        await service.aclose()


def test_recovered_sheriff_vote_rides_the_durable_ledger(
    tmp_path, monkeypatch, dual_clients,
):
    """The recovery ``night_invoke`` routes ``sheriff_vote`` the same way.

    Same client, same decision spec, same durable attempt — a resumed sheriff
    election must not re-implement the route, or a crash mid-ballot would
    come back on a different model than the one that asked the question.
    """
    repository = GameRepository(tmp_path / "repo")
    repository.create_game(
        game_id="game", name="game", config={},
        execution_status="running", source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    service._durable_contexts["game"] = {
        "storage_revision": 1,
        "execution_generation": repository.get_game("game")[
            "execution_generation"
        ],
    }
    monkeypatch.setattr(service, "_prompt_capture", lambda *_args: None)
    state = _state_with_players()
    _office_ballot(state, {1})
    env = env_default_client_config()
    monkeypatch.setattr(
        service, "_resolve_recovery_model_configs",
        lambda *_args: {1: env, 2: env},
    )
    cfg, _ = _store_with(tmp_path, monkeypatch, provider_profile="jev")
    record = _vote_model_record(tmp_path, monkeypatch)
    record["config"]["vote_model"]["config_id"] = cfg.id
    vote_config, _ = service_module._saved_client_config(cfg.id, env)
    parameters = _model_parameters(vote_config)
    record["config"]["vote_model"]["parameters"] = parameters
    record["config"]["vote_model"]["parameters_digest"] = _parameter_digest(
        parameters,
    )

    class Engine:
        def __init__(self, **kwargs):
            self.roles = kwargs["roles"]
            self._director = kwargs["director"]
            self._rng = service_module.random.Random(7)
            self.game_logger = SimpleNamespace(
                log_llm_call=lambda *args, **kwargs: None,
            )

        def load_restored_state(self, state, orchestration, codec):
            self.loaded = (state, orchestration, codec)

    monkeypatch.setattr(service_module, "GameEngine", Engine)
    dual_clients.next_answer = {
        "sheriff_vote": {"choice": "1", "confidence": 0.9},
    }
    try:
        engine, clients = service._build_recovered_engine(state, {}, record)
        vote_client = service._vote_llm_clients[state.game_id]
        assert vote_client.is_system_one is True

        raw = engine._director._invoke(
            [{"role": "user", "content": "投警长票"}],
            "sheriff_vote", {"type": "object"}, 2,
        )

        payload = json.loads(raw)
        assert payload["action_type"] == "vote"
        assert payload["target_seat"] == 1
        assert vote_client.system_one_calls
        requests = repository.list_model_requests("game")
        assert len(requests) == 1
        assert requests[0]["status"] == "resolved"
        assert requests[0]["provider_profile"] == "jev"
        assert requests[0]["model_id"] == "vote-model"
    finally:
        asyncio.run(service.aclose())
        repository.close()
