import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from app.api.local_access import LOCAL_ORIGINS, LocalAccessMiddleware
from app.config import config as app_config
from app.core.event_bus import EventBus
from app.api.routes.game_routes import router as game_router
from app.api.routes.model_routes import router as model_router
from app.api.routes.catalog_routes import router as catalog_router
from app.api.routes.benchmark_routes import router as benchmark_router
from app.api.routes.folder_routes import router as folder_router
from app.api.websocket.ws_handler import WSManager, WSHandler
from app.persistence.process_lock import ProcessLock
from app.persistence.repository import GameRepository
from app.services.audience_event_service import AudienceEventService
from app.services.benchmark_game_executor import BenchmarkGameExecutor
from app.services.benchmark_service import BenchmarkService
from app.services.game_service import GameService
from app.services.memory_service import MemoryService

# ── Initialize global services ──────────────────────────
data_dir = Path(os.environ.get("WOLFKILLER_DATA_DIR", "data")).expanduser().resolve()
process_lock = ProcessLock(data_dir).acquire()
try:
    repository = GameRepository(data_dir)
except BaseException:
    process_lock.release()
    raise

# A restart never dispatches models. Persist interrupted state before loading
# checkpoints into the in-memory browse/recovery facade.
repository.interrupt_running_games()
repository.interrupt_running_benchmarks()
event_bus = EventBus()
ws_manager = WSManager()
memory_service = MemoryService(data_dir=str(data_dir))

game_service = GameService(
    ws_manager=ws_manager,
    event_bus=event_bus,
    memory_service=memory_service,
    data_dir=str(data_dir),
    repository=repository,
)

audience_event_service = AudienceEventService(repository)
benchmark_game_executor = BenchmarkGameExecutor(repository, game_service)
benchmark_service = BenchmarkService(
    repository,
    item_executor=benchmark_game_executor,
    game_pauser=benchmark_game_executor.pause,
    game_canceller=benchmark_game_executor.cancel,
)
ws_handler = WSHandler(ws_manager=ws_manager, event_bus=event_bus)


async def shutdown_services():
    """Stop dispatch, persist interruption state, and release the data lock."""
    try:
        await benchmark_service.aclose()
        await game_service.aclose()
    finally:
        repository.close()
        process_lock.release()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        yield
    finally:
        await shutdown_services()


# ── FastAPI app ─────────────────────────────────────────
app = FastAPI(
    title="Wolf Killer - Multi-Agent Werewolf Game",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LocalAccessMiddleware)

app.include_router(game_router)
app.include_router(model_router)
app.include_router(catalog_router)
app.include_router(benchmark_router)
app.include_router(folder_router)


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
