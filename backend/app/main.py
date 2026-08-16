import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from app.config import config as app_config
from app.core.event_bus import EventBus
from app.api.routes.game_routes import router as game_router
from app.api.routes.model_routes import router as model_router
from app.api.routes.catalog_routes import router as catalog_router
from app.api.websocket.ws_handler import WSManager, WSHandler
from app.services.game_service import GameService
from app.services.memory_service import MemoryService

# ── Initialize global services ──────────────────────────
event_bus = EventBus()
ws_manager = WSManager()
memory_service = MemoryService(data_dir="data")

game_service = GameService(
    ws_manager=ws_manager,
    event_bus=event_bus,
    memory_service=memory_service,
)

ws_handler = WSHandler(ws_manager=ws_manager, event_bus=event_bus)

# ── FastAPI app ─────────────────────────────────────────
app = FastAPI(title="Wolf Killer - Multi-Agent Werewolf Game")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(game_router)
app.include_router(model_router)
app.include_router(catalog_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "wolf-killer"}


@app.get("/api/config")
async def get_config():
    return {
        "llm_models": app_config.llm.models,
        "debug": app_config.debug,
        "active_games": game_service.list_games(),
    }


@app.websocket("/ws/game/{game_id}")
async def websocket_endpoint(ws: WebSocket, game_id: str):
    await ws_handler.handle_connection(ws, game_id)
