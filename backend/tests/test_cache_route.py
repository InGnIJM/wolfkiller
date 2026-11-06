import asyncio
import itertools
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.cache_route import (
    BurstWarmupMode,
    CacheRouteMode,
    MIN_CACHED_TOKENS,
    SharedSessionMode,
    apply_shared_session,
    burst_warmup_max_wait_seconds,
    burst_warmup_mode,
    cache_route_mode,
    pin_cache_routes,
    probe_and_pin,
    probe_messages,
    shared_session_id,
    shared_session_mode,
    should_probe,
)
from app.agents.llm_client import LLMClient
from app.agents.providers.base import (
    API_MODE_CHAT_COMPLETIONS,
    CacheMode,
    ModelCapabilities,
    ProviderProfile,
)
from app.agents.session_affinity import treatment_seats
from app.services.game_service import GameService


def _profile(
    *,
    cache_mode: CacheMode = CacheMode.ROUTING,
    session_header: str | None = "x-opencode-session",
) -> ProviderProfile:
    return ProviderProfile(
        profile_id="opencode-go",
        api_mode=API_MODE_CHAT_COMPLETIONS,
        capabilities=ModelCapabilities(True, True, True, True, True),
        default_action_max_tokens=2048,
        session_header=session_header,
        cache_mode=cache_mode,
    )


_fake_ids = itertools.count(1)


class _FakeClient:
    def __init__(
        self, profile: ProviderProfile, values, *, reset_error=None,
        session_id: str | None = None,
    ):
        self.provider_profile = profile
        self._values = list(values)
        self.resets = 0
        self.reset_error = reset_error
        self.session_id = session_id or f"ses_fake_{next(_fake_ids)}"

    async def probe_cache(self, messages, *, timeout=None):
        value = self._values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def reset_session(self):
        self.resets += 1
        if self.reset_error is not None:
            raise self.reset_error

    async def set_session_id(self, session_id: str):
        self.resets += 1
        self.session_id = session_id
        if self.reset_error is not None:
            raise self.reset_error


class _SlowClient(_FakeClient):
    async def probe_cache(self, messages, *, timeout=None):
        await asyncio.sleep(0.05)
        return 0


def test_cache_route_mode_defaults_and_validates(monkeypatch):
    monkeypatch.delenv("WOLFKILLER_CACHE_ROUTE_PIN", raising=False)
    assert cache_route_mode() is CacheRouteMode.OFF
    assert cache_route_mode("") is CacheRouteMode.OFF
    assert cache_route_mode(" ON ") is CacheRouteMode.ON
    assert cache_route_mode("ab") is CacheRouteMode.AB
    with pytest.raises(ValueError, match="WOLFKILLER_CACHE_ROUTE_PIN"):
        cache_route_mode("maybe")


def test_should_probe_only_targets_routing_treatment_seats():
    game_id = "game"
    total = 12
    assert not should_probe(
        _profile(), game_id=game_id, seat=1, total_players=total,
        mode=CacheRouteMode.OFF,
    )
    assert not should_probe(
        _profile(cache_mode=CacheMode.AUTOMATIC),
        game_id=game_id, seat=1, total_players=total, mode=CacheRouteMode.ON,
    )
    assert not should_probe(
        _profile(session_header=None),
        game_id=game_id, seat=1, total_players=total, mode=CacheRouteMode.ON,
    )
    assert should_probe(
        _profile(), game_id=game_id, seat=1, total_players=total,
        mode=CacheRouteMode.ON,
    )
    treated = sorted(treatment_seats(game_id, total))[0]
    control = sorted(set(range(1, total + 1)) - treatment_seats(game_id, total))[0]
    assert should_probe(
        _profile(), game_id=game_id, seat=treated, total_players=total,
        mode=CacheRouteMode.AB,
    )
    assert not should_probe(
        _profile(), game_id=game_id, seat=control, total_players=total,
        mode=CacheRouteMode.AB,
    )


def test_probe_messages_are_stable_and_long_enough():
    first = probe_messages()
    second = probe_messages()
    assert isinstance(first[0], SystemMessage)
    assert isinstance(first[1], HumanMessage)
    assert first[0].content == second[0].content
    assert len(first[0].content) > 1000


@pytest.mark.asyncio
async def test_probe_and_pin_rotates_a_cold_session_then_keeps_the_hot_one():
    client = _FakeClient(_profile(), [0, MIN_CACHED_TOKENS])

    result = await probe_and_pin(client)

    assert result == {
        "attempts": 2, "hot": True, "cached": MIN_CACHED_TOKENS,
    }
    assert client.resets == 1


@pytest.mark.asyncio
async def test_probe_and_pin_stops_after_the_last_cold_attempt():
    client = _FakeClient(_profile(), [0, None])

    result = await probe_and_pin(client)

    assert result["hot"] is False
    assert result["attempts"] == 2
    assert client.resets == 1


@pytest.mark.asyncio
async def test_probe_and_pin_survives_a_probe_error():
    client = _FakeClient(_profile(), [RuntimeError("boom"), 0])

    result = await probe_and_pin(client)

    assert result["hot"] is False
    assert result["attempts"] == 2
    assert result["cached"] == 0
    assert client.resets == 1


@pytest.mark.asyncio
async def test_probe_and_pin_stops_when_reset_fails():
    client = _FakeClient(_profile(), [0, MIN_CACHED_TOKENS], reset_error=RuntimeError("no"))

    result = await probe_and_pin(client)

    assert result["attempts"] == 1
    assert result["hot"] is False
    assert client.resets == 1


@pytest.mark.asyncio
async def test_pin_cache_routes_skips_untargeted_seats():
    routing = _FakeClient(_profile(), [0, MIN_CACHED_TOKENS])
    automatic = _FakeClient(_profile(cache_mode=CacheMode.AUTOMATIC), [0])

    result = await pin_cache_routes(
        {1: routing, 2: automatic},
        game_id="game", total_players=12, mode=CacheRouteMode.ON,
    )

    assert set(result) == {1}
    assert result[1]["hot"] is True
    assert automatic._values == [0]


@pytest.mark.asyncio
async def test_pin_cache_routes_is_a_noop_when_off():
    client = _FakeClient(_profile(), [0])

    result = await pin_cache_routes(
        {1: client}, game_id="game", total_players=12, mode=CacheRouteMode.OFF,
    )

    assert result == {}
    assert client._values == [0]


@pytest.mark.asyncio
async def test_pin_cache_routes_returns_an_error_row_for_a_crashing_probe(monkeypatch):
    async def boom(client, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.agents.cache_route.probe_and_pin", boom)
    client = _FakeClient(_profile(), [MIN_CACHED_TOKENS])

    result = await pin_cache_routes(
        {1: client}, game_id="game", total_players=12, mode=CacheRouteMode.ON,
    )

    assert result[1]["error"] == "RuntimeError"
    assert result[1]["hot"] is False


@pytest.mark.asyncio
async def test_pin_cache_routes_times_out_without_raising():
    client = _SlowClient(_profile(), [0])

    result = await pin_cache_routes(
        {1: client}, game_id="game", total_players=12, mode=CacheRouteMode.ON,
        total_timeout=0.001,
    )

    assert result == {}


@pytest.mark.asyncio
async def test_llm_client_probe_cache_reads_the_second_call(monkeypatch):
    client = object.__new__(LLMClient)
    calls = []

    class _Response:
        usage_metadata = {
            "input_tokens": 100, "output_tokens": 1, "total_tokens": 101,
            "prompt_tokens_details": {"cached_tokens": 64},
        }

    async def fake_acomplete(model, messages, *, timeout=None):
        calls.append(timeout)
        return _Response()

    monkeypatch.setattr(client, "_build", lambda purpose: object())
    monkeypatch.setattr(client, "_acomplete", fake_acomplete)

    result = await client.probe_cache([{"role": "user", "content": "x"}], timeout=3)

    assert result == 64
    assert calls == [3, 3]


@pytest.mark.asyncio
async def test_llm_client_probe_cache_returns_none_without_usage(monkeypatch):
    client = object.__new__(LLMClient)

    class _Response:
        usage_metadata = None
        response_metadata = {}

    async def fake_acomplete(model, messages, *, timeout=None):
        return _Response()

    monkeypatch.setattr(client, "_build", lambda purpose: object())
    monkeypatch.setattr(client, "_acomplete", fake_acomplete)

    assert await client.probe_cache([], timeout=1) is None


@pytest.mark.asyncio
async def test_llm_client_reset_session_mints_a_new_id(monkeypatch):
    client = object.__new__(LLMClient)
    client._transport = SimpleNamespace(session_id="ses_old")
    closed = []

    async def fake_aclose():
        closed.append(True)

    monkeypatch.setattr(client, "aclose", fake_aclose)

    await client.reset_session()

    assert closed == [True]
    assert client._transport.session_id != "ses_old"
    assert client._transport.session_id.startswith("ses_")


@pytest.mark.asyncio
async def test_llm_client_reset_session_is_a_noop_without_a_session(monkeypatch):
    client = object.__new__(LLMClient)
    client._transport = SimpleNamespace()

    async def fail():
        raise AssertionError("aclose must not run")

    monkeypatch.setattr(client, "aclose", fail)

    await client.reset_session()


@pytest.mark.asyncio
async def test_game_service_writes_the_cache_route_log(tmp_path, monkeypatch):
    service = object.__new__(GameService)
    service.data_dir = str(tmp_path)

    async def fake_pin(clients, *, game_id, total_players, mode):
        return {
            2: {"attempts": 1, "hot": True, "cached": 2048},
            1: {"attempts": 2, "hot": False, "cached": 0},
        }

    monkeypatch.setattr("app.services.game_service.pin_cache_routes", fake_pin)

    await service._pin_cache_routes("game-1", 12, CacheRouteMode.ON, {})

    path = tmp_path / "games" / "game-1" / "cache_route.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["seat"] for line in lines] == [1, 2]
    assert lines[1]["hot"] is True


@pytest.mark.asyncio
async def test_game_service_skips_the_log_when_nothing_was_probed(tmp_path, monkeypatch):
    service = object.__new__(GameService)
    service.data_dir = str(tmp_path)

    async def empty(clients, *, game_id, total_players, mode):
        return {}

    monkeypatch.setattr("app.services.game_service.pin_cache_routes", empty)

    await service._pin_cache_routes("game-1", 12, CacheRouteMode.ON, {})

    assert not (tmp_path / "games" / "game-1").exists()


@pytest.mark.asyncio
async def test_game_service_swallows_a_cache_route_failure(tmp_path, monkeypatch):
    service = object.__new__(GameService)
    service.data_dir = str(tmp_path)

    async def boom(clients, *, game_id, total_players, mode):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.game_service.pin_cache_routes", boom)

    await service._pin_cache_routes("game-1", 12, CacheRouteMode.ON, {})

    assert not (tmp_path / "games" / "game-1").exists()


@pytest.mark.asyncio
async def test_create_game_pins_cache_routes_when_the_flag_is_on(tmp_path, monkeypatch):
    from app.api.websocket.ws_handler import WSManager
    from app.core.event_bus import EventBus
    from app.core.game_engine import GameEngine

    async def noop(self):
        return None

    monkeypatch.setattr(GameEngine, "start", noop)
    calls = {}

    async def fake_pin(clients, *, game_id, total_players, mode):
        calls["args"] = (game_id, total_players, mode)
        return {}

    monkeypatch.setattr("app.services.game_service.pin_cache_routes", fake_pin)
    monkeypatch.setenv("WOLFKILLER_CACHE_ROUTE_PIN", "on")

    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    game_id = await service.create_game()
    await service._tasks[game_id]

    assert calls["args"][0] == game_id
    assert calls["args"][2] is CacheRouteMode.ON


def test_shared_session_mode_defaults_and_validates(monkeypatch):
    monkeypatch.delenv("WOLFKILLER_SHARED_CACHE_SESSION", raising=False)
    assert shared_session_mode() is SharedSessionMode.OFF
    assert shared_session_mode("") is SharedSessionMode.OFF
    assert shared_session_mode(" ON ") is SharedSessionMode.ON
    with pytest.raises(ValueError, match="WOLFKILLER_SHARED_CACHE_SESSION"):
        shared_session_mode("ab")


def test_shared_session_id_is_deterministic_and_shaped():
    one = shared_session_id("game-a", "x-opencode-session")
    assert one == shared_session_id("game-a", "x-opencode-session")
    assert one != shared_session_id("game-b", "x-opencode-session")
    assert one != shared_session_id("game-a", "x-other-session")
    assert one.startswith("ses_")
    assert len(one) == 30


@pytest.mark.asyncio
async def test_apply_shared_session_is_off_by_default():
    client = _FakeClient(_profile(), [])
    original = client.session_id

    applied = await apply_shared_session(
        {1: client}, game_id="game", mode=SharedSessionMode.OFF,
    )

    assert applied == []
    assert client.session_id == original


@pytest.mark.asyncio
async def test_apply_shared_session_points_routing_seats_at_one_id():
    first = _FakeClient(_profile(), [])
    second = _FakeClient(_profile(), [])
    automatic = _FakeClient(_profile(cache_mode=CacheMode.AUTOMATIC), [])
    headerless = _FakeClient(_profile(session_header=None), [])

    applied = await apply_shared_session(
        {1: first, 2: second, 3: automatic, 4: headerless},
        game_id="game", mode=SharedSessionMode.ON,
    )

    expected = shared_session_id("game", "x-opencode-session")
    assert applied == [1, 2]
    assert first.session_id == second.session_id == expected
    assert automatic.session_id != expected
    assert headerless.session_id != expected


@pytest.mark.asyncio
async def test_apply_shared_session_groups_by_session_header():
    opencode = _FakeClient(_profile(), [])
    other = _FakeClient(_profile(session_header="x-other-session"), [])

    applied = await apply_shared_session(
        {1: opencode, 2: other}, game_id="game", mode=SharedSessionMode.ON,
    )

    assert applied == [1, 2]
    assert opencode.session_id == shared_session_id(
        "game", "x-opencode-session",
    )
    assert other.session_id == shared_session_id("game", "x-other-session")
    assert opencode.session_id != other.session_id


@pytest.mark.asyncio
async def test_pin_cache_routes_probes_a_shared_group_once():
    shared = "ses_shared_group"
    rep = _FakeClient(_profile(), [MIN_CACHED_TOKENS], session_id=shared)
    member = _FakeClient(
        _profile(), [MIN_CACHED_TOKENS], session_id=shared,
    )
    solo = _FakeClient(_profile(), [MIN_CACHED_TOKENS])

    result = await pin_cache_routes(
        {1: rep, 2: member, 3: solo},
        game_id="game", total_players=12, mode=CacheRouteMode.ON,
    )

    assert rep._values == []
    assert member._values == [MIN_CACHED_TOKENS]
    assert solo._values == []
    assert result[1]["group_size"] == 2
    assert result[2]["group_size"] == 2
    assert result[3]["group_size"] == 1


@pytest.mark.asyncio
async def test_pin_cache_routes_rotates_a_cold_group_to_one_new_id():
    shared = "ses_shared_cold"
    rep = _FakeClient(
        _profile(), [0, MIN_CACHED_TOKENS], session_id=shared,
    )
    member = _FakeClient(_profile(), [], session_id=shared)

    result = await pin_cache_routes(
        {1: rep, 2: member},
        game_id="game", total_players=12, mode=CacheRouteMode.ON,
    )

    assert result[1]["hot"] is True
    assert rep.session_id == member.session_id != shared
    assert rep.session_id.startswith("ses_")


@pytest.mark.asyncio
async def test_pin_cache_routes_reports_a_group_probe_error():
    shared = "ses_shared_boom"
    rep = _FakeClient(
        _profile(), [RuntimeError("boom"), RuntimeError("boom")],
        session_id=shared,
    )
    member = _FakeClient(_profile(), [], session_id=shared)

    result = await pin_cache_routes(
        {1: rep, 2: member},
        game_id="game", total_players=12, mode=CacheRouteMode.ON,
    )

    assert result[1]["hot"] is False
    assert result[1]["attempts"] == 2
    assert rep.session_id != shared


@pytest.mark.asyncio
async def test_pin_cache_routes_stops_when_a_group_member_cannot_rotate():
    shared = "ses_shared_stuck"
    rep = _FakeClient(
        _profile(), [0, MIN_CACHED_TOKENS], session_id=shared,
    )
    member = _FakeClient(
        _profile(), [], reset_error=RuntimeError("no"), session_id=shared,
    )

    result = await pin_cache_routes(
        {1: rep, 2: member},
        game_id="game", total_players=12, mode=CacheRouteMode.ON,
    )

    assert result[1]["hot"] is False
    assert result[1]["attempts"] == 1
    assert rep._values == [MIN_CACHED_TOKENS]


def test_llm_client_session_id_property():
    client = object.__new__(LLMClient)
    client._transport = SimpleNamespace(session_id="ses_a")
    assert client.session_id == "ses_a"
    client._transport = SimpleNamespace()
    assert client.session_id is None


@pytest.mark.asyncio
async def test_llm_client_set_session_id_uses_the_given_value(monkeypatch):
    client = object.__new__(LLMClient)
    client._transport = SimpleNamespace(session_id="ses_old")
    closed = []

    async def fake_aclose():
        closed.append(True)

    monkeypatch.setattr(client, "aclose", fake_aclose)

    await client.set_session_id("ses_custom")

    assert closed == [True]
    assert client._transport.session_id == "ses_custom"


@pytest.mark.asyncio
async def test_llm_client_set_session_id_is_a_noop_without_a_session(
    monkeypatch,
):
    client = object.__new__(LLMClient)
    client._transport = SimpleNamespace()

    async def fail():
        raise AssertionError("aclose must not run")

    monkeypatch.setattr(client, "aclose", fail)

    await client.set_session_id("ses_x")


@pytest.mark.asyncio
async def test_create_game_shares_the_cache_session_before_probing(
    tmp_path, monkeypatch,
):
    from app.api.websocket.ws_handler import WSManager
    from app.core.event_bus import EventBus
    from app.core.game_engine import GameEngine

    async def noop(self):
        return None

    monkeypatch.setattr(GameEngine, "start", noop)
    order = []

    async def fake_apply(clients, *, game_id, mode):
        order.append("share")
        return sorted(clients)

    async def fake_pin(clients, *, game_id, total_players, mode):
        order.append("pin")
        return {}

    monkeypatch.setattr(
        "app.services.game_service.apply_shared_session", fake_apply,
    )
    monkeypatch.setattr(
        "app.services.game_service.pin_cache_routes", fake_pin,
    )
    monkeypatch.setenv("WOLFKILLER_SHARED_CACHE_SESSION", "on")
    monkeypatch.setenv("WOLFKILLER_CACHE_ROUTE_PIN", "on")

    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    game_id = await service.create_game()
    await service._tasks[game_id]

    assert order == ["share", "pin"]


@pytest.mark.asyncio
async def test_share_cache_session_swallows_failures(monkeypatch):
    service = object.__new__(GameService)

    async def boom(clients, *, game_id, mode):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.services.game_service.apply_shared_session", boom,
    )

    await service._share_cache_session(
        "game-1", {1: object()}, SharedSessionMode.ON,
    )


def test_burst_warmup_mode_defaults_and_validates(monkeypatch):
    monkeypatch.delenv("WOLFKILLER_BURST_WARMUP", raising=False)
    assert burst_warmup_mode() is BurstWarmupMode.OFF
    assert burst_warmup_mode("") is BurstWarmupMode.OFF
    assert burst_warmup_mode(" ON ") is BurstWarmupMode.ON
    with pytest.raises(ValueError, match="WOLFKILLER_BURST_WARMUP"):
        burst_warmup_mode("ab")


def test_burst_warmup_max_wait_defaults_to_three_seconds(monkeypatch):
    monkeypatch.delenv("WOLFKILLER_BURST_WARMUP_MAX_WAIT_SECONDS", raising=False)
    assert burst_warmup_max_wait_seconds() == 3.0
    assert burst_warmup_max_wait_seconds("") == 3.0


def test_burst_warmup_max_wait_parses_seconds_and_zero_disables():
    assert burst_warmup_max_wait_seconds("1.5") == 1.5
    assert burst_warmup_max_wait_seconds(" 0.25 ") == 0.25
    # Zero or negative restores the old "wait for the whole first ballot".
    assert burst_warmup_max_wait_seconds("0") is None
    assert burst_warmup_max_wait_seconds("-2") is None


def test_burst_warmup_max_wait_rejects_garbage():
    with pytest.raises(ValueError, match="WOLFKILLER_BURST_WARMUP_MAX_WAIT_SECONDS"):
        burst_warmup_max_wait_seconds("soon")


@pytest.mark.asyncio
async def test_create_game_enables_vote_warmup_with_the_flag(
    tmp_path, monkeypatch,
):
    from app.api.websocket.ws_handler import WSManager
    from app.core.event_bus import EventBus
    from app.core.game_engine import GameEngine

    async def noop(self):
        return None

    monkeypatch.setattr(GameEngine, "start", noop)
    monkeypatch.setenv("WOLFKILLER_BURST_WARMUP", "on")
    monkeypatch.setenv("WOLFKILLER_BURST_WARMUP_MAX_WAIT_SECONDS", "1.5")

    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    game_id = await service.create_game()
    await service._tasks[game_id]

    assert service._engines[game_id]._vote_warmup is True
    assert service._engines[game_id]._vote_warmup_max_wait_seconds == 1.5


@pytest.mark.asyncio
async def test_create_game_leaves_vote_warmup_off_by_default(
    tmp_path, monkeypatch,
):
    from app.api.websocket.ws_handler import WSManager
    from app.core.event_bus import EventBus
    from app.core.game_engine import GameEngine

    async def noop(self):
        return None

    monkeypatch.setattr(GameEngine, "start", noop)
    monkeypatch.delenv("WOLFKILLER_BURST_WARMUP", raising=False)

    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    game_id = await service.create_game()
    await service._tasks[game_id]

    assert service._engines[game_id]._vote_warmup is False


@pytest.mark.asyncio
async def test_recovery_carries_vote_warmup_into_the_engine(
    tmp_path, monkeypatch,
):
    import app.services.game_service as service_module
    from app.agents.llm_client import env_default_client_config
    from app.api.websocket.ws_handler import WSManager
    from app.core.event_bus import EventBus
    from tests.test_game_service_lifecycle import _state_with_players

    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    state.registry_digest = service._registry_snapshot.digest
    service._durable_contexts[state.game_id] = {
        "storage_revision": 1, "execution_generation": 2,
    }
    config = env_default_client_config()
    captured: dict = {}

    class Client:
        provider_profile = SimpleNamespace(profile_id="profile")
        model_name = "model"

        def __init__(self, *, config, dedicated_http_clients: bool = False):
            self.config = config

        async def aclose(self):
            pass

        def close(self):
            pass

    class Engine:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self._rng = service_module.random.Random(7)
            self.game_logger = SimpleNamespace()

        def load_restored_state(self, *args):
            self.loaded = args

    monkeypatch.setattr(service_module, "LLMClient", Client)
    monkeypatch.setattr(service_module, "GameEngine", Engine)
    monkeypatch.setattr(
        service, "_resolve_recovery_model_configs",
        lambda *_args: {1: config, 2: config},
    )
    monkeypatch.setenv("WOLFKILLER_BURST_WARMUP", "on")
    # Zero disables the cap: recovery must hand ``None`` through, not the
    # default.
    monkeypatch.setenv("WOLFKILLER_BURST_WARMUP_MAX_WAIT_SECONDS", "0")
    try:
        service._build_recovered_engine(
            state, {"position": "night"}, {"config": {}},
        )
        assert captured["vote_warmup"] is True
        assert captured["vote_warmup_max_wait_seconds"] is None
    finally:
        await service.aclose()