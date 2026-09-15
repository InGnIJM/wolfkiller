import asyncio
import inspect
import json
import logging
from collections.abc import Mapping
from fastapi import WebSocket, WebSocketDisconnect
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.services.audience_event_service import (
    AudienceCursorAheadError,
    AudienceGameNotFoundError,
)

logger = logging.getLogger(__name__)

_V2_PAGE_SIZE = 200
_V2_SEND_TIMEOUT_SECONDS = 5.0
_V2_POLL_SECONDS = 1.0
_V2_HEARTBEAT_SECONDS = 20.0
_V2_IDLE_TIMEOUT_SECONDS = 60.0


class _V2SendError(RuntimeError):
    pass


class WSManager:
    """Manages WebSocket connections and broadcasts game state updates."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}  # game_id -> [ws]
        self._v2_connections: dict[str, list[WebSocket]] = {}
        self._v2_wakeups: dict[str, dict[int, asyncio.Event]] = {}

    async def connect(self, game_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(game_id, []).append(ws)
        logger.info(f"WS connected: game={game_id}, total_connections={len(self._connections[game_id])}")

    async def disconnect(self, game_id: str, ws: WebSocket) -> None:
        if game_id in self._connections:
            self._connections[game_id] = [c for c in self._connections[game_id] if c != ws]
            logger.info(f"WS disconnected: game={game_id}")
        if game_id in self._v2_connections:
            self._v2_connections[game_id] = [
                connection for connection in self._v2_connections[game_id]
                if connection != ws
            ]
        if game_id in self._v2_wakeups:
            self._v2_wakeups[game_id].pop(id(ws), None)

    async def connect_v2(self, game_id: str, ws: WebSocket) -> asyncio.Event:
        await ws.accept()
        self._v2_connections.setdefault(game_id, []).append(ws)
        wakeup = asyncio.Event()
        self._v2_wakeups.setdefault(game_id, {})[id(ws)] = wakeup
        logger.info(
            "WS V2 connected: game=%s, total_connections=%s",
            game_id, len(self._v2_connections[game_id]),
        )
        return wakeup

    async def broadcast(self, game_id: str, msg_type: str, **payload) -> None:
        for wakeup in self._v2_wakeups.get(game_id, {}).values():
            wakeup.set()
        connections = self._connections.get(game_id, [])
        if not connections:
            return

        message = json.dumps({"type": msg_type, **payload}, ensure_ascii=False)
        dead = []
        for ws in connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.disconnect(game_id, ws)

    async def close_game(self, game_id: str) -> None:
        connections = list(self._connections.pop(game_id, []))
        connections.extend(self._v2_connections.pop(game_id, []))
        self._v2_wakeups.pop(game_id, None)
        for ws in connections:
            try:
                await ws.close()
            except Exception:
                logger.debug("Ignoring WebSocket close error for game %s", game_id)

    async def send_to(self, game_id: str, ws: WebSocket, msg_type: str, **payload) -> None:
        message = json.dumps({"type": msg_type, **payload}, ensure_ascii=False)
        try:
            await ws.send_text(message)
        except Exception:
            await self.disconnect(game_id, ws)


class WSHandler:
    """Handles WebSocket lifecycle and message routing."""

    def __init__(self, ws_manager: WSManager, event_bus: EventBus):
        self.ws_manager = ws_manager
        self.event_bus = event_bus
        self._game_speeds: dict[str, float] = {}

    async def handle_connection(self, ws: WebSocket, game_id: str) -> None:
        if self._protocol(ws) == "2":
            await self._handle_v2_connection(ws, game_id)
            return

        await self.ws_manager.connect(game_id, ws)

        # Send initial game state on connect
        from app.main import game_service
        state = game_service.get_game_state(game_id)
        if state:
            await self.ws_manager.send_to(
                game_id, ws, "game_state",
                state=state.get_public_state(),
            )

        try:
            async for raw_msg in ws.iter_text():
                await self._handle_message(game_id, ws, raw_msg)
        except WebSocketDisconnect:
            pass
        finally:
            await self.ws_manager.disconnect(game_id, ws)

    @staticmethod
    def _protocol(ws: WebSocket) -> str:
        query = getattr(ws, "query_params", None)
        if query is None or not hasattr(query, "get"):
            return "1"
        value = query.get("protocol")
        return value if isinstance(value, str) else "1"

    @staticmethod
    def _v2_after_seq(ws: WebSocket) -> int:
        query = getattr(ws, "query_params", None)
        value = "0" if query is None or not hasattr(query, "get") else query.get("after_seq", "0")
        if not isinstance(value, str) or not value.isdecimal():
            raise ValueError("after_seq must be a non-negative integer")
        return int(value)

    @staticmethod
    def _audience_service():
        from app.main import audience_event_service
        return audience_event_service

    async def _v2_get_events(self, game_id: str, *, after_seq: int) -> Mapping[str, object]:
        return await asyncio.to_thread(
            self._audience_service().get_events,
            game_id, after_seq=after_seq, limit=_V2_PAGE_SIZE,
        )

    async def _send_v2(self, ws: WebSocket, msg_type: str, **payload) -> None:
        message = json.dumps({"type": msg_type, **payload}, ensure_ascii=False)
        try:
            await asyncio.wait_for(
                ws.send_text(message), timeout=_V2_SEND_TIMEOUT_SECONDS,
            )
        except Exception as error:
            # WebSocket implementations use several concrete disconnect errors.
            raise _V2SendError(str(error)) from error

    async def _close_v2(self, ws: WebSocket, code: int) -> None:
        try:
            await ws.close(code=code)
        except Exception:
            logger.debug("Ignoring WebSocket V2 close error", exc_info=True)

    async def _handle_v2_connection(self, ws: WebSocket, game_id: str) -> None:
        wakeup = await self.ws_manager.connect_v2(game_id, ws)
        try:
            try:
                cursor = self._v2_after_seq(ws)
                page = await self._v2_get_events(game_id, after_seq=cursor)
            except AudienceGameNotFoundError:
                await self._send_v2(
                    ws, "error", code="game_not_found", message="Game not found",
                )
                await self._close_v2(ws, 1008)
                return
            except AudienceCursorAheadError as error:
                await self._send_v2(
                    ws, "error", code="cursor_ahead", message=str(error),
                    high_watermark=error.high_watermark,
                )
                await self._close_v2(ws, 1008)
                return
            except ValueError as error:
                await self._send_v2(
                    ws, "error", code="invalid_cursor", message=str(error),
                )
                await self._close_v2(ws, 1008)
                return

            await self._send_v2(
                ws, "hello", protocol=2, game_id=game_id,
                after_seq=cursor, high_watermark=page["high_watermark"],
            )
            activity = {"time": asyncio.get_running_loop().time()}
            sender = asyncio.create_task(
                self._v2_sender(ws, game_id, cursor, page, wakeup),
            )
            receiver = asyncio.create_task(
                self._v2_receiver(ws, game_id, activity),
            )
            heartbeat = asyncio.create_task(self._v2_heartbeat(ws, activity))
            tasks = {sender, receiver, heartbeat}
            try:
                done, _pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task.cancelled():
                        continue
                    error = task.exception()
                    if error is not None:
                        raise error
            finally:
                # Parent cancellation must also stop every session worker.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except WebSocketDisconnect:
            pass
        except _V2SendError:
            await self._close_v2(ws, 1013)
        finally:
            await self.ws_manager.disconnect(game_id, ws)

    async def _v2_sender(
        self, ws: WebSocket, game_id: str, cursor: int,
        initial_page: Mapping[str, object], wakeup: asyncio.Event,
    ) -> None:
        page = initial_page
        caught_up_at: int | None = None
        while True:
            events = page.get("events", [])
            if not isinstance(events, list):
                raise RuntimeError("audience service returned invalid events")
            if len(events) > 1000:
                raise _V2SendError("audience event buffer limit exceeded")
            if events:
                await self._send_v2(ws, "events", events=events)
                cursor = int(page["next_seq"])
            high_watermark = int(page["high_watermark"])
            if not bool(page["has_more"]):
                if caught_up_at != high_watermark:
                    await self._send_v2(ws, "caught_up", last_seq=high_watermark)
                    caught_up_at = high_watermark
                try:
                    await asyncio.wait_for(
                        wakeup.wait(), timeout=_V2_POLL_SECONDS,
                    )
                except TimeoutError:
                    pass
                finally:
                    wakeup.clear()
            page = await self._v2_get_events(game_id, after_seq=cursor)

    async def _v2_receiver(
        self, ws: WebSocket, game_id: str, activity: dict[str, float],
    ) -> None:
        async for raw in ws.iter_text():
            activity["time"] = asyncio.get_running_loop().time()
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                await self._send_v2(
                    ws, "error", code="invalid_message", message="Invalid JSON message",
                )
                continue
            if not isinstance(value, dict):
                await self._send_v2(
                    ws, "error", code="invalid_message", message="Message must be an object",
                )
                continue
            if value.get("type") in {"ack", "pong"}:
                continue
            await self._handle_message(game_id, ws, raw)

    async def _v2_heartbeat(
        self, ws: WebSocket, activity: dict[str, float],
    ) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(_V2_HEARTBEAT_SECONDS)
            if loop.time() - activity["time"] > _V2_IDLE_TIMEOUT_SECONDS:
                await self._close_v2(ws, 1013)
                return
            await self._send_v2(ws, "ping", timestamp=loop.time())

    async def _handle_message(self, game_id: str, ws: WebSocket, raw: str) -> None:
        try:
            data = json.loads(raw)
            msg_type = data.get("type", "")
        except json.JSONDecodeError:
            logger.warning(
                f"WS message parse error (game={game_id}): %r", raw[:200],
            )
            return

        if msg_type == "set_speed":
            delay = data.get("delay_seconds", 2.0)
            self._game_speeds[game_id] = delay
            from app.main import game_service
            engine = game_service._engines.get(game_id)
            if engine:
                engine.phase_delay = delay

        elif msg_type == "skip_phase":
            from app.main import game_service
            engine = game_service._engines.get(game_id)
            if engine:
                engine.phase_delay = 0.1

        elif msg_type == "pause":
            from app.main import game_service
            handled = await self._forward_control(game_service, "pause", game_id)
            engine = game_service._engines.get(game_id)
            if handled or engine:
                if not handled:
                    engine.pause()
                await self.ws_manager.broadcast(game_id, "paused_state", paused=True)

        elif msg_type == "resume":
            from app.main import game_service
            handled = await self._forward_control(game_service, "resume", game_id)
            engine = game_service._engines.get(game_id)
            if handled or engine:
                if not handled:
                    engine.resume()
                await self.ws_manager.broadcast(game_id, "paused_state", paused=False)

    @staticmethod
    async def _forward_control(service, action: str, game_id: str) -> bool:
        method = getattr(service, f"{action}_game", None)
        if not callable(method):
            return False
        result = method(game_id)
        if not inspect.isawaitable(result):
            return False
        await result
        return True
