import json
import logging
from fastapi import WebSocket, WebSocketDisconnect
from app.core.event_bus import EventBus, GameEvent as BusEvent

logger = logging.getLogger(__name__)


class WSManager:
    """Manages WebSocket connections and broadcasts game state updates."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}  # game_id -> [ws]

    async def connect(self, game_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(game_id, []).append(ws)
        logger.info(f"WS connected: game={game_id}, total_connections={len(self._connections[game_id])}")

    async def disconnect(self, game_id: str, ws: WebSocket) -> None:
        if game_id in self._connections:
            self._connections[game_id] = [c for c in self._connections[game_id] if c != ws]
            logger.info(f"WS disconnected: game={game_id}")

    async def broadcast(self, game_id: str, msg_type: str, **payload) -> None:
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
            await self.ws_manager.disconnect(game_id, ws)

    async def _handle_message(self, game_id: str, ws: WebSocket, raw: str) -> None:
        try:
            data = json.loads(raw)
            msg_type = data.get("type", "")
        except json.JSONDecodeError:
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
            engine = game_service._engines.get(game_id)
            if engine:
                engine.pause()
                await self.ws_manager.broadcast(game_id, "paused_state", paused=True)

        elif msg_type == "resume":
            from app.main import game_service
            engine = game_service._engines.get(game_id)
            if engine:
                engine.resume()
                await self.ws_manager.broadcast(game_id, "paused_state", paused=False)
