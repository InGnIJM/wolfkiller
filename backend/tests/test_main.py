import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_main_exposes_app_routes_and_services():
    import app.main as main

    assert main.app is not None
    assert main.event_bus is not None
    assert main.ws_manager is not None
    assert main.memory_service is not None
    assert main.game_service is not None
    assert main.ws_handler is not None
    paths = {route.path for route in main.app.routes}
    assert "/api/health" in paths
    assert "/api/config" in paths
    assert "/ws/game/{game_id}" in paths
    assert "/api/games" in paths


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
async def test_websocket_endpoint_delegates_to_handler():
    import app.main as main
    mock_ws = MagicMock()
    handler = AsyncMock()
    with patch.object(main, "ws_handler", handler):
        await main.websocket_endpoint(mock_ws, "game-1")
    handler.handle_connection.assert_awaited_once_with(mock_ws, "game-1")
