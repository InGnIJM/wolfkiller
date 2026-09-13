import asyncio
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

    @pytest.mark.asyncio
    async def test_close_game_closes_sockets_and_drops_entry(self):
        mgr = WSManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        await mgr.connect("game-1", ws)

        await mgr.close_game("game-1")

        ws.close.assert_awaited_once()
        assert "game-1" not in mgr._connections

    @pytest.mark.asyncio
    async def test_close_game_ignores_missing_and_close_errors(self):
        mgr = WSManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock(side_effect=RuntimeError("already closed"))
        await mgr.connect("game-1", ws)

        await mgr.close_game("missing")
        await mgr.close_game("game-1")

        assert "game-1" not in mgr._connections

    @pytest.mark.asyncio
    async def test_v2_connections_are_isolated_from_legacy_broadcasts(self):
        mgr = WSManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()

        wakeup = await mgr.connect_v2("game-1", ws)
        await mgr.broadcast("game-1", "phase_change", phase="night")

        ws.accept.assert_awaited_once()
        ws.send_text.assert_not_awaited()
        assert ws in mgr._v2_connections["game-1"]
        assert wakeup.is_set()


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

    @pytest.mark.asyncio
    async def test_handle_message_pause_and_resume(self):
        mgr = WSManager()
        mgr.broadcast = AsyncMock()
        handler = WSHandler(ws_manager=mgr, event_bus=EventBus())
        mock_engine = MagicMock()
        mock_engine.pause = MagicMock()
        mock_engine.resume = MagicMock()
        mock_service = MagicMock()
        mock_service._engines = {"test-game": mock_engine}
        with patch.dict("sys.modules", {"app.main": MagicMock()}):
            import sys
            sys.modules["app.main"].game_service = mock_service
            await handler._handle_message("test-game", MagicMock(), '{"type": "pause"}')
            await handler._handle_message("test-game", MagicMock(), '{"type": "resume"}')
        mock_engine.pause.assert_called_once()
        mock_engine.resume.assert_called_once()
        assert mgr.broadcast.await_count == 2

    @pytest.mark.asyncio
    async def test_handle_message_control_without_engine(self):
        mgr = WSManager()
        mgr.broadcast = AsyncMock()
        handler = WSHandler(ws_manager=mgr, event_bus=EventBus())
        mock_service = MagicMock()
        mock_service._engines = {}
        with patch.dict("sys.modules", {"app.main": MagicMock()}):
            import sys
            sys.modules["app.main"].game_service = mock_service
            await handler._handle_message("missing", MagicMock(), '{"type": "set_speed", "delay_seconds": 0.5}')
            await handler._handle_message("missing", MagicMock(), '{"type": "skip_phase"}')
            await handler._handle_message("missing", MagicMock(), '{"type": "pause"}')
            await handler._handle_message("missing", MagicMock(), '{"type": "resume"}')
        assert handler._game_speeds == {"missing": 0.5}

    @pytest.mark.asyncio
    async def test_handle_connection_sends_state_and_routes_messages(self):
        mgr = WSManager()
        mgr.connect = AsyncMock()
        mgr.send_to = AsyncMock()
        handler = WSHandler(ws_manager=mgr, event_bus=EventBus())
        mock_ws = MagicMock()
        mock_state = MagicMock()
        mock_state.get_public_state.return_value = {"phase": "night"}
        mock_service = MagicMock()
        mock_service.get_game_state.return_value = mock_state
        mock_service._engines = {}
        received = []

        async def iter_text():
            for item in ('{"type": "set_speed", "delay_seconds": 1.0}',):
                yield item

        mock_ws.iter_text = iter_text
        with patch.dict("sys.modules", {"app.main": MagicMock()}):
            import sys
            sys.modules["app.main"].game_service = mock_service
            await handler.handle_connection(mock_ws, "game-1")
        mgr.connect.assert_called_once_with("game-1", mock_ws)
        mgr.send_to.assert_called_once()
        assert mgr.send_to.call_args.args[2] == "game_state"
        assert handler._game_speeds == {"game-1": 1.0}

    @pytest.mark.asyncio
    async def test_handle_connection_without_state_and_disconnect(self):
        mgr = WSManager()
        mgr.connect = AsyncMock()
        mgr.send_to = AsyncMock()
        mgr.disconnect = AsyncMock()
        handler = WSHandler(ws_manager=mgr, event_bus=EventBus())
        mock_ws = MagicMock()
        mock_service = MagicMock()
        mock_service.get_game_state.return_value = None
        mock_service._engines = {}

        from fastapi import WebSocketDisconnect

        async def iter_text():
            if False:
                yield ""
            raise WebSocketDisconnect(code=1000)

        mock_ws.iter_text = iter_text
        with patch.dict("sys.modules", {"app.main": MagicMock()}):
            import sys
            sys.modules["app.main"].game_service = mock_service
            await handler.handle_connection(mock_ws, "game-x")
        mgr.send_to.assert_not_awaited()
        mgr.disconnect.assert_called_once_with("game-x", mock_ws)

    @pytest.mark.asyncio
    async def test_v2_connection_sends_hello_events_and_caught_up(self):
        mgr = WSManager()
        handler = WSHandler(ws_manager=mgr, event_bus=EventBus())
        ws = MagicMock()
        ws.query_params = {"protocol": "2", "after_seq": "0"}
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock()

        async def iter_text():
            while ws.send_text.await_count < 3:
                await asyncio.sleep(0)
            yield '{"type":"ack","last_seq":1}'

        ws.iter_text = iter_text
        audience = MagicMock()

        def page(_game_id, *, after_seq, limit):
            events = [{
                "game_id": "game-1", "seq": 1, "event_id": "event-1",
                "schema_version": 1, "event_type": "phase",
                "timestamp": "2026-09-06T00:00:00+00:00",
                "payload": {"phase": "night", "round_number": 1},
            }] if after_seq == 0 else []
            return {
                "game_id": "game-1", "events": events,
                "next_seq": 1 if events else after_seq,
                "high_watermark": 1, "has_more": False,
            }

        audience.get_events.side_effect = page
        handler._audience_service = lambda: audience

        await asyncio.wait_for(handler.handle_connection(ws, "game-1"), timeout=2.0)

        messages = [json.loads(call.args[0]) for call in ws.send_text.await_args_list]
        assert [message["type"] for message in messages] == [
            "hello", "events", "caught_up",
        ]
        assert messages[1]["events"][0]["seq"] == 1
        assert mgr._v2_connections["game-1"] == []

    @pytest.mark.asyncio
    async def test_v2_connection_rejects_cursor_ahead_with_stable_error(self):
        from app.services.audience_event_service import AudienceCursorAheadError

        mgr = WSManager()
        handler = WSHandler(ws_manager=mgr, event_bus=EventBus())
        ws = MagicMock()
        ws.query_params = {"protocol": "2", "after_seq": "5"}
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock()
        audience = MagicMock()
        audience.get_events.side_effect = AudienceCursorAheadError("game-1", 5, 3)
        handler._audience_service = lambda: audience

        await handler.handle_connection(ws, "game-1")

        error = json.loads(ws.send_text.await_args.args[0])
        assert error["type"] == "error"
        assert error["code"] == "cursor_ahead"
        assert error["high_watermark"] == 3
        ws.close.assert_awaited_once_with(code=1008)

    @pytest.mark.asyncio
    async def test_v2_control_uses_game_service_control_boundary(self):
        mgr = WSManager()
        mgr.broadcast = AsyncMock()
        handler = WSHandler(ws_manager=mgr, event_bus=EventBus())
        service = MagicMock()
        service.pause_game = AsyncMock(return_value={"execution_status": "paused"})
        service._engines = {}

        with patch.dict("sys.modules", {"app.main": MagicMock()}):
            import sys
            sys.modules["app.main"].game_service = service
            await handler._handle_message("game-1", MagicMock(), '{"type":"pause"}')

        service.pause_game.assert_awaited_once_with("game-1")
        mgr.broadcast.assert_awaited_once_with(
            "game-1", "paused_state", paused=True,
        )


class TestV2FailureAndCleanup:
    @staticmethod
    def socket():
        ws = MagicMock()
        ws.query_params = {"protocol": "2", "after_seq": "0"}
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock()
        return ws

    @staticmethod
    def handler():
        return WSHandler(WSManager(), EventBus())

    def test_protocol_defaults_and_cursor_validation(self):
        from types import SimpleNamespace
        handler = self.handler()
        for ws in (SimpleNamespace(), SimpleNamespace(query_params=object())):
            assert handler._protocol(ws) == "1"
            assert handler._v2_after_seq(ws) == 0
        for cursor in ("-1", "abc", 2, None):
            with pytest.raises(ValueError, match="non-negative integer"):
                handler._v2_after_seq(SimpleNamespace(query_params={"after_seq": cursor}))

    def test_audience_service_uses_the_application_boundary(self, monkeypatch):
        from types import SimpleNamespace
        import sys
        audience = object()
        monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(audience_event_service=audience))
        assert self.handler()._audience_service() is audience

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure,code", [("missing", "game_not_found"), ("invalid", "invalid_cursor")])
    async def test_initial_cursor_failure_closes_and_unregisters(self, failure, code):
        from app.services.audience_event_service import AudienceGameNotFoundError
        handler, ws = self.handler(), self.socket()
        audience = MagicMock()
        audience.get_events.side_effect = AudienceGameNotFoundError("g") if failure == "missing" else ValueError("bad cursor")
        handler._audience_service = lambda: audience
        await handler.handle_connection(ws, "g")
        message = json.loads(ws.send_text.await_args.args[0])
        assert message["code"] == code
        ws.close.assert_awaited_once_with(code=1008)
        assert handler.ws_manager._v2_connections["g"] == []
        assert handler.ws_manager._v2_wakeups["g"] == {}

    @pytest.mark.asyncio
    async def test_slow_client_is_closed_and_send_timeout_is_wrapped(self, monkeypatch):
        from app.api.websocket import ws_handler
        monkeypatch.setattr(ws_handler, "_V2_SEND_TIMEOUT_SECONDS", 0.001)
        handler, ws = self.handler(), self.socket()
        async def blocked_send(_message):
            await asyncio.Event().wait()
        ws.send_text.side_effect = blocked_send
        audience = MagicMock()
        audience.get_events.return_value = {"high_watermark": 0}
        handler._audience_service = lambda: audience
        await handler.handle_connection(ws, "g")
        ws.close.assert_awaited_once_with(code=1013)
        assert handler.ws_manager._v2_connections["g"] == []
        ws.close.side_effect = RuntimeError("already disconnected")
        await handler._close_v2(ws, 1013)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["complete", "cancel", "disconnect", "error"])
    async def test_session_reaps_remaining_tasks_after_sender_finishes(self, outcome):
        from fastapi import WebSocketDisconnect
        handler, ws = self.handler(), self.socket()
        audience = MagicMock()
        audience.get_events.return_value = {"high_watermark": 0}
        handler._audience_service = lambda: audience
        receiver_started = asyncio.Event()
        cancelled = []
        async def sender(*_args):
            await receiver_started.wait()
            if outcome == "cancel":
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            if outcome == "disconnect":
                raise WebSocketDisconnect(code=1000)
            if outcome == "error":
                raise RuntimeError("storage failed")
        async def receiver(*_args):
            receiver_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append("receiver")
                if outcome == "complete":
                    raise RuntimeError("receiver cleanup failed")
        async def heartbeat(*_args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append("heartbeat")
        handler._v2_sender = sender
        handler._v2_receiver = receiver
        handler._v2_heartbeat = heartbeat
        if outcome == "error":
            with pytest.raises(RuntimeError, match="storage failed"):
                await handler.handle_connection(ws, "g")
        else:
            await handler.handle_connection(ws, "g")
        assert sorted(cancelled) == ["heartbeat", "receiver"]
        assert handler.ws_manager._v2_connections["g"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("events,error", [(None, RuntimeError), ([{}] * 1001, RuntimeError)])
    async def test_sender_rejects_invalid_or_unbounded_event_buffers(self, events, error):
        handler, ws = self.handler(), self.socket()
        with pytest.raises(error):
            await handler._v2_sender(ws, "g", 0, {"events": events}, asyncio.Event())
        ws.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sender_pages_and_polls_without_duplicate_caught_up_notifications(self, monkeypatch):
        from app.api.websocket import ws_handler
        from fastapi import WebSocketDisconnect
        monkeypatch.setattr(ws_handler, "_V2_POLL_SECONDS", 0.001)
        handler, ws = self.handler(), self.socket()
        initial = {"events": [{"seq": 1}], "next_seq": 1, "high_watermark": 2, "has_more": True}
        audience = MagicMock()
        audience.get_events.side_effect = [
            {"events": [{"seq": 2}], "next_seq": 2, "high_watermark": 2, "has_more": False},
            {"events": [], "next_seq": 2, "high_watermark": 2, "has_more": False},
            WebSocketDisconnect(),
        ]
        handler._audience_service = lambda: audience
        wakeup = asyncio.Event()
        wakeup.set()
        with pytest.raises(WebSocketDisconnect):
            await handler._v2_sender(ws, "g", 0, initial, wakeup)
        assert [json.loads(call.args[0])["type"] for call in ws.send_text.await_args_list] == ["events", "events", "caught_up"]
        assert audience.get_events.call_args.kwargs["after_seq"] == 2
        assert not wakeup.is_set()

    @pytest.mark.asyncio
    async def test_receiver_rejects_bad_messages_and_routes_valid_controls(self):
        handler, ws = self.handler(), self.socket()
        async def messages():
            for raw in ("broken", "[]", '{"type":"ack"}', '{"type":"pong"}', '{"type":"pause"}'):
                yield raw
        ws.iter_text = messages
        handler._handle_message = AsyncMock()
        activity = {"time": 0.0}
        await handler._v2_receiver(ws, "g", activity)
        assert activity["time"] > 0
        assert ws.send_text.await_count == 2
        assert all(json.loads(call.args[0])["code"] == "invalid_message" for call in ws.send_text.await_args_list)
        handler._handle_message.assert_awaited_once_with("g", ws, '{"type":"pause"}')

    @pytest.mark.asyncio
    async def test_heartbeat_pings_then_disconnects_an_idle_client(self, monkeypatch):
        from app.api.websocket import ws_handler
        monkeypatch.setattr(ws_handler, "_V2_HEARTBEAT_SECONDS", 0.001)
        handler, ws = self.handler(), self.socket()
        activity = {"time": asyncio.get_running_loop().time()}
        async def sent(_message):
            activity["time"] -= 100
        ws.send_text.side_effect = sent
        await asyncio.wait_for(handler._v2_heartbeat(ws, activity), timeout=1)
        assert json.loads(ws.send_text.await_args.args[0])["type"] == "ping"
        ws.close.assert_awaited_once_with(code=1013)

    @pytest.mark.asyncio
    async def test_control_fallback_and_async_resume(self, monkeypatch):
        from types import SimpleNamespace
        import sys
        handler = self.handler()
        assert await handler._forward_control(SimpleNamespace(), "resume", "g") is False
        service = SimpleNamespace(resume_game=AsyncMock(), _engines={})
        monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(game_service=service))
        handler.ws_manager.broadcast = AsyncMock()
        await handler._handle_message("g", self.socket(), '{"type":"resume"}')
        service.resume_game.assert_awaited_once_with("g")
        handler.ws_manager.broadcast.assert_awaited_once_with("g", "paused_state", paused=False)


@pytest.mark.asyncio
async def test_cancelling_v2_connection_reaps_all_session_tasks():
    handler, ws = TestV2FailureAndCleanup.handler(), TestV2FailureAndCleanup.socket()
    audience = MagicMock()
    audience.get_events.return_value = {"high_watermark": 0}
    handler._audience_service = lambda: audience
    children = []
    started = asyncio.Event()
    async def child(*_args):
        children.append(asyncio.current_task())
        if len(children) == 3:
            started.set()
        await asyncio.Event().wait()
    handler._v2_sender = child
    handler._v2_receiver = child
    handler._v2_heartbeat = child
    connection = asyncio.create_task(handler.handle_connection(ws, "g"))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        connection.cancel()
        with pytest.raises(asyncio.CancelledError):
            await connection
        assert all(task.done() for task in children)
        assert handler.ws_manager._v2_connections["g"] == []
    finally:
        for task in [connection, *children]:
            task.cancel()
        await asyncio.gather(connection, *children, return_exceptions=True)
