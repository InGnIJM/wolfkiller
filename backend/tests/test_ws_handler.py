import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from app.api.websocket.ws_handler import WSManager, WSHandler
from app.core.event_bus import EventBus


class TestWSManager:
    @pytest.mark.asyncio
    async def test_connect(self):
        mgr = WSManager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()

        await mgr.connect("game-1", mock_ws)

        mock_ws.accept.assert_called_once()
        assert "game-1" in mgr._connections
        assert mock_ws in mgr._connections["game-1"]

    @pytest.mark.asyncio
    async def test_disconnect(self):
        mgr = WSManager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()

        await mgr.connect("game-1", mock_ws)
        await mgr.disconnect("game-1", mock_ws)

        assert mock_ws not in mgr._connections["game-1"]

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_game(self):
        mgr = WSManager()
        mock_ws = MagicMock()
        # Should not raise
        await mgr.disconnect("nonexistent", mock_ws)

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self):
        mgr = WSManager()
        # Should not raise when no connections
        await mgr.broadcast("nonexistent", "test", data="hello")

    @pytest.mark.asyncio
    async def test_broadcast_with_connections(self):
        mgr = WSManager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()

        await mgr.connect("game-1", mock_ws)
        await mgr.broadcast("game-1", "phase_change", phase="night")

        mock_ws.send_text.assert_called_once()
        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["type"] == "phase_change"
        assert sent["phase"] == "night"

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self):
        mgr = WSManager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock(side_effect=Exception("disconnected"))

        await mgr.connect("game-1", mock_ws)
        await mgr.broadcast("game-1", "test")

        # Dead connection should be removed
        assert mock_ws not in mgr._connections.get("game-1", [])

    @pytest.mark.asyncio
    async def test_send_to(self):
        mgr = WSManager()
        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock()

        await mgr.send_to("game-1", mock_ws, "game_state", state={"test": True})

        mock_ws.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_error_handling(self):
        mgr = WSManager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock(side_effect=Exception("error"))

        await mgr.connect("game-1", mock_ws)
        await mgr.send_to("game-1", mock_ws, "test")

        # Should remove dead connection
        assert mock_ws not in mgr._connections.get("game-1", [])


class TestWSHandler:
    @pytest.mark.asyncio
    async def test_handle_message_set_speed(self):
        mgr = WSManager()
        bus = EventBus()
        handler = WSHandler(ws_manager=mgr, event_bus=bus)

        mock_ws = MagicMock()
        handler._game_speeds["test-game"] = 2.0

        mock_engine = MagicMock()
        mock_service = MagicMock()
        mock_service._engines = {"test-game": mock_engine}
        with patch.dict("sys.modules", {"app.main": MagicMock()}):
            import sys
            sys.modules["app.main"].game_service = mock_service
            await handler._handle_message("test-game", mock_ws, '{"type": "set_speed", "delay_seconds": 1.5}')
        assert handler._game_speeds["test-game"] == 1.5

    @pytest.mark.asyncio
    async def test_handle_message_skip_phase(self):
        mgr = WSManager()
        bus = EventBus()
        handler = WSHandler(ws_manager=mgr, event_bus=bus)

        mock_ws = MagicMock()
        mock_engine = MagicMock()
        mock_service = MagicMock()
        mock_service._engines = {"test-game": mock_engine}
        with patch.dict("sys.modules", {"app.main": MagicMock()}):
            import sys
            sys.modules["app.main"].game_service = mock_service
            await handler._handle_message("test-game", mock_ws, '{"type": "skip_phase"}')
        # Should not crash

    @pytest.mark.asyncio
    async def test_handle_message_invalid_json(self):
        mgr = WSManager()
        bus = EventBus()
        handler = WSHandler(ws_manager=mgr, event_bus=bus)

        mock_ws = MagicMock()
        await handler._handle_message("test-game", mock_ws, "not valid json")
        # Should not crash

    @pytest.mark.asyncio
    async def test_handle_message_unknown_type(self):
        mgr = WSManager()
        bus = EventBus()
        handler = WSHandler(ws_manager=mgr, event_bus=bus)

        mock_ws = MagicMock()
        await handler._handle_message("test-game", mock_ws, '{"type": "unknown_cmd"}')
        # Should not crash

    def test_init(self):
        mgr = WSManager()
        bus = EventBus()
        handler = WSHandler(ws_manager=mgr, event_bus=bus)
        assert handler.ws_manager is mgr
        assert handler.event_bus is bus
        assert handler._game_speeds == {}
