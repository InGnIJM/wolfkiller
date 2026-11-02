import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from app.api.local_access import LOCAL_ORIGINS, LocalAccessMiddleware

from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.agents.providers.prewarm import prewarm_providers
from app.api.routes.game_routes import router as game_router
from app.api.routes.model_routes import router as model_router, warm_latency_cache
from app.api.routes.catalog_routes import router as catalog_router
from app.api.routes.benchmark_routes import router as benchmark_router
from app.api.routes.folder_routes import router as folder_router
from app.api.routes.settings_routes import router as settings_router
from app.api.routes.diagnostics_routes import router as diagnostics_router
from app.api.websocket.ws_handler import WSManager, WSHandler
from app.persistence.process_lock import ProcessLock
from app.persistence.repository import GameRepository
from app.services.audience_event_service import AudienceEventService
from app.services.benchmark_game_executor import BenchmarkGameExecutor
from app.services.benchmark_service import BenchmarkService
from app.services.commentary_service import CommentaryService
from app.services.game_export import GameExportService
from app.services.game_service import GameService
from app.services.memory_service import MemoryService
from app.services.review_service import ReviewService

# ── Startup warmups ─────────────────────────────────────
# Neither the provider SDKs nor the latency aggregate are needed to answer the
# first HTTP request, and both are expensive on a cold machine (the SDKs are
# ~thousands of module files; the aggregate scans the attempts table). Pay them
# on one daemon thread while the rest of the boot continues, so the first page
# load and the first LLM call do not have to. The lazy imports in ``providers``
# keep this optional: with the environment variable set to 0 the cost simply
# moves to the first call that needs it.
def _warm_startup_caches() -> None:
    warm_latency_cache()
    prewarm_providers()


def _start_startup_warmups() -> threading.Thread | None:
    if os.environ.get("WOLFKILLER_PROVIDER_PREWARM", "1") == "0":
        return None
    thread = threading.Thread(
        target=_warm_startup_caches, name="startup-warmup", daemon=True,
    )
    thread.start()
    return thread


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
# The repository exists now; warm both caches while the archives load.
_start_startup_warmups()
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
commentary_service = CommentaryService(
    repository=repository,
    ws_manager=ws_manager,
    data_dir=str(data_dir),
)
review_service = ReviewService(
    repository=repository,
    data_dir=str(data_dir),
)
game_export_service = GameExportService(
    repository=repository,
    data_dir=str(data_dir),
    game_service=game_service,
)
event_bus.subscribe(BusEvent.PHASE_CHANGED, commentary_service.on_phase_changed)
event_bus.subscribe(BusEvent.GAME_OVER, commentary_service.on_game_over)
event_bus.subscribe(BusEvent.GAME_OVER, review_service.on_game_over)
benchmark_game_executor = BenchmarkGameExecutor(repository, game_service)
benchmark_service = BenchmarkService(
    repository,
    item_executor=benchmark_game_executor,
    game_pauser=benchmark_game_executor.pause,
    game_canceller=benchmark_game_executor.cancel,
    owned_game_deleter=game_service.delete_benchmark_owned_game,
    data_dir=str(data_dir),
)
ws_handler = WSHandler(ws_manager=ws_manager, event_bus=event_bus)


async def shutdown_services():
    """Stop dispatch, persist interruption state, and release the data lock."""
    try:
        await commentary_service.aclose()
        await review_service.aclose()
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
app.include_router(settings_router)
app.include_router(diagnostics_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "wolf-killer"}


@app.websocket("/ws/game/{game_id}")
async def websocket_endpoint(ws: WebSocket, game_id: str):
    await ws_handler.handle_connection(ws, game_id)
