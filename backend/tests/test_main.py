import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_main_exposes_app_routes_and_services():
    import app.main as main

    assert main.app is not None
    assert main.event_bus is not None
    assert main.ws_manager is not None
    assert main.memory_service is not None
    assert main.game_service is not None
    assert main.repository is not None
    assert main.process_lock.locked is True
    assert main.audience_event_service is not None
    assert main.benchmark_service is not None
    assert main.ws_handler is not None
    paths = {route.path for route in main.app.routes}
    assert "/api/health" in paths
    assert "/api/config" in paths
    assert "/ws/game/{game_id}" in paths
    assert "/api/games" in paths
    assert "/api/models" in paths
    assert "/api/models/{config_id}" in paths
    assert "/api/models/test" in paths
    assert "/api/catalog/roles" in paths
    assert "/api/catalog/presets" in paths
    assert "/api/catalog/constraints" in paths
    assert "/api/benchmarks" in paths
    assert "/api/games/{game_id}/snapshot" in paths


@pytest.mark.asyncio
async def test_health_check_returns_ok():
    import app.main as main
    result = await main.health_check()
    assert result == {"status": "ok", "service": "wolf-killer"}


@pytest.mark.asyncio
async def test_get_config_returns_models_and_games():
    import app.main as main
    original = main.game_service.list_games
    main.game_service.list_games = lambda: ["game-a"]
    try:
        result = await main.get_config()
    finally:
        main.game_service.list_games = original
    assert result["active_games"] == ["game-a"]
    assert "llm_models" in result
    assert isinstance(result["debug"], bool)


@pytest.mark.asyncio
async def test_shutdown_closes_game_service_clients():
    import app.main as main

    with patch.object(main.game_service, "aclose", new=AsyncMock()) as close:
        async with main.lifespan(main.app):
            close.assert_not_awaited()

    close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_websocket_endpoint_delegates_to_handler():
    import app.main as main
    mock_ws = MagicMock()
    handler = AsyncMock()
    with patch.object(main, "ws_handler", handler):
        await main.websocket_endpoint(mock_ws, "game-1")
    handler.handle_connection.assert_awaited_once_with(mock_ws, "game-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,host,origin,status", [
    ("127.0.0.1", "localhost:8000", None, 200),
    ("::1", "[::1]:8000", "http://localhost:5173", 200),
    ("127.0.0.1", "127.0.0.1:8000", "http://127.0.0.1:4173", 200),
    ("192.0.2.1", "localhost:8000", None, 403),
    ("127.0.0.1", "rebound.example:8000", None, 403),
    ("127.0.0.1", "localhost:8000", "https://untrusted.example", 403),
    ("127.0.0.1", "localhost:8000", "null", 403),
    ("not-an-ip", "localhost:8000", None, 403),
    ("127.0.0.1", "[", None, 403),
    ("127.0.0.1", "", None, 403),
    ("127.0.0.1", "localhost:9000", "http://localhost:9000", 200),
])
async def test_api_enforces_local_peer_host_and_browser_origin(peer, host, origin, status):
    import httpx
    from app.main import app

    headers = {"host": host}
    if origin is not None:
        headers["origin"] = origin
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 12345)),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.get("/api/health", headers=headers)
    assert response.status_code == status


@pytest.mark.asyncio
async def test_untrusted_origin_cannot_write_model_configuration(tmp_path, monkeypatch):
    import httpx
    from app.main import app
    from app.api.routes import model_routes
    from app.stores.model_config_store import JsonModelConfigStore

    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    monkeypatch.setattr(model_routes, "get_model_config_store", lambda: store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post("/api/models", headers={"origin": "https://untrusted.example"}, json={
            "name": "untrusted", "base_url": "https://example.invalid/v1", "model_id": "fake",
        })
    assert response.status_code == 403
    assert store.list_all() == []


@pytest.mark.asyncio
async def test_untrusted_websocket_origin_never_reaches_handler(monkeypatch):
    import app.main as main

    handler = AsyncMock()
    monkeypatch.setattr(main.ws_handler, "handle_connection", handler)
    sent = []
    async def send(message):
        sent.append(message)
    async def receive():
        return {"type": "websocket.connect"}
    await main.app({
        "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
        "path": "/ws/game/test", "raw_path": b"/ws/game/test", "query_string": b"",
        "root_path": "", "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 8000),
        "headers": [(b"host", b"localhost:8000"), (b"origin", b"https://untrusted.example")],
        "subprotocols": [],
    }, receive, send)
    handler.assert_not_awaited()
    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
@pytest.mark.parametrize("origin,status,allowed", [
    ("http://localhost:5173", 200, "http://localhost:5173"),
    ("https://untrusted.example", 403, None),
])
async def test_browser_preflight_only_allows_trusted_origins(origin, status, allowed):
    import httpx
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.options("/api/models", headers={
            "origin": origin,
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        })
    assert response.status_code == status
    assert response.headers.get("access-control-allow-origin") == allowed
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.asyncio
async def test_access_filter_preserves_lifespan_and_rejects_missing_peer():
    from app.api.local_access import LocalAccessMiddleware

    app = AsyncMock()
    receive, send = AsyncMock(), AsyncMock()
    middleware = LocalAccessMiddleware(app)
    lifespan = {"type": "lifespan"}
    await middleware(lifespan, receive, send)
    app.assert_awaited_once_with(lifespan, receive, send)
    app.reset_mock()
    await middleware({"type": "websocket", "headers": [(b"host", b"localhost:8000")]}, receive, send)
    app.assert_not_awaited()
    send.assert_awaited_once_with({"type": "websocket.close", "code": 1008})


def test_repository_initialization_failure_releases_the_acquired_process_lock(monkeypatch):
    import runpy
    from pathlib import Path
    from app.persistence import process_lock, repository
    acquired = MagicMock()
    factory = MagicMock()
    factory.return_value.acquire.return_value = acquired
    monkeypatch.setattr(process_lock, "ProcessLock", factory)
    monkeypatch.setattr(repository, "GameRepository", MagicMock(side_effect=RuntimeError("database unavailable")))
    with pytest.raises(RuntimeError, match="database unavailable"):
        runpy.run_path(str(Path(__file__).parents[1] / "app" / "main.py"))
    acquired.release.assert_called_once_with()
