import asyncio
import json
import logging
import os
import random
import uuid
from typing import Optional

from app.config import config as app_config
from app.models.game import GameState, GameConfig, GamePhase, PlayerState
from app.core.game_engine import GameEngine
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.agents.llm_client import LLMClient
from app.agents.prompt_builder import PromptBuilder
from app.roles.registry import builtin_registry
from app.api.websocket.ws_handler import WSManager
from app.services.game_manifest import GameManifest

logger = logging.getLogger(__name__)


class GameService:
    """Manages game lifecycle: creation, execution, state access, and event broadcasting.

    Game metadata is persisted to data/games/index.json so the game list
    survives backend restarts.  Completed games can be browsed and their
    logs replayed without the engine still running.
    """

    def __init__(
        self,
        ws_manager: WSManager,
        event_bus: EventBus,
        memory_service=None,
    ):
        self.ws_manager = ws_manager
        self.event_bus = event_bus
        self.memory_service = memory_service
        self._games: dict[str, GameState] = {}
        self._engines: dict[str, GameEngine] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._manifest = GameManifest()

        # Restore completed games so list / detail endpoints still work
        self._load_persisted_games()

        self.event_bus.subscribe(BusEvent.PHASE_CHANGED, self._on_phase_changed)
        self.event_bus.subscribe(BusEvent.PLAYER_DIED, self._on_player_died)
        self.event_bus.subscribe(BusEvent.SPEECH_MADE, self._on_speech_made)
        self.event_bus.subscribe(BusEvent.VOTE_CAST, self._on_vote_cast)
        self.event_bus.subscribe(BusEvent.GAME_OVER, self._on_game_over)
        self.event_bus.subscribe(BusEvent.NIGHT_SUBSTEP, self._on_night_substep)

    # ── Persistence helpers ────────────────────────────────────────

    def _load_persisted_games(self) -> None:
        """Reconstruct lightweight GameState for every completed game on disk."""
        entries = self._manifest.load_or_rebuild()
        for game_id, meta in entries.items():
            # Skip games still running (engine will re-register them)
            if meta.get("phase") == "game_over" or meta.get("winner"):
                state = self._reconstruct_state(game_id, meta)
                if state is not None:
                    self._games[game_id] = state
        logger.info(f"Restored {len(self._games)} completed games from disk")

    def _reconstruct_state(
        self, game_id: str, meta: dict,
    ) -> Optional[GameState]:
        """Build a minimal GameState from manifest metadata + game.log."""
        log_path = os.path.join("data", "games", game_id, "game.log")
        if not os.path.exists(log_path):
            return None

        config = GameConfig(**meta.get("config", {})) if meta.get("config") else GameConfig()

        state = GameState(
            game_id=game_id,
            phase=GamePhase(meta.get("phase", "game_over")),
            round_number=meta.get("round_number", 0),
            config=config,
        )
        state.win_result = {
            "winning_camp": meta["winner"],
            "reason": "",
        } if meta.get("winner") else None

        # Reconstruct players + events from log
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return state

        seen_seats: set[int] = set()

        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            data = rec.get("data") or {}
            op = rec.get("operation")
            seat = rec.get("seat")

            # ── Discover players ──────────────────────────────
            if op == "role_init":
                for seat_str, pdata in (data.get("players") or {}).items():
                    s = int(seat_str)
                    seen_seats.add(s)
                    state.players[s] = PlayerState(
                        seat_number=s,
                        role=pdata.get("role", ""),
                        camp=pdata.get("camp", ""),
                        is_alive=pdata.get("is_alive", True),
                    )

            # Fallback: extract seats from werewolf votes or any seat-bearing event
            if not state.players:
                if op == "werewolf_kill":
                    for v in data.get("votes") or []:
                        s = v.get("player_seat")
                        if s and s not in seen_seats:
                            seen_seats.add(s)
                            state.players[s] = PlayerState(
                                seat_number=s, role="?", camp="?",
                            )
                if isinstance(seat, int) and seat > 0 and seat not in seen_seats:
                    seen_seats.add(seat)
                    state.players[seat] = PlayerState(
                        seat_number=seat, role="?", camp="?",
                    )

            # ── Apply deaths ──────────────────────────────────
            if op == "night_deaths":
                for d in (data.get("deaths") or []):
                    ds = d.get("player_seat")
                    if ds and ds in state.players:
                        state.players[ds].mark_dead()

            elif op == "vote_result":
                exiled = data.get("exiled")
                if exiled and exiled in state.players:
                    state.players[exiled].mark_dead()

            elif op == "game_over":
                state.win_result = {
                    "winning_camp": data.get("winner"),
                    "reason": data.get("reason", ""),
                }

        # If manifest or config says N players but we found fewer, fill in placeholder seats
        total = meta.get("player_count", 0) or config.total_players
        if total > 0 and len(state.players) < total:
            for s in range(1, total + 1):
                if s not in state.players:
                    state.players[s] = PlayerState(
                        seat_number=s, role="?", camp="?",
                    )

        return state

    def _persist_game(self, state: GameState) -> None:
        """Write current game metadata to the manifest."""
        self._manifest.update_game(
            state.game_id,
            phase=state.phase.value,
            round_number=state.round_number,
            alive_count=len(state.alive_players()),
        )

    async def create_game(
        self,
        num_werewolves: Optional[int] = None,
        num_villagers: Optional[int] = None,
        num_seers: Optional[int] = None,
        num_witches: Optional[int] = None,
        num_hunters: Optional[int] = None,
        role_counts: Optional[dict[str, int]] = None,
    ) -> str:
        game_id = str(uuid.uuid4())[:8]
        config = GameConfig(
            role_counts=role_counts,
            num_werewolves=num_werewolves,
            num_villagers=num_villagers,
            num_seers=num_seers,
            num_witches=num_witches,
            num_hunters=num_hunters,
        )

        # Create role instances with random model assignment
        models = app_config.llm.models
        prompt_builder = PromptBuilder()
        roles = self._create_roles(config, prompt_builder, models)

        # Create engine
        engine = GameEngine(
            game_id=game_id,
            config=config,
            event_bus=self.event_bus,
            roles=roles,
            memory_service=self.memory_service,
        )

        self._engines[game_id] = engine
        self._games[game_id] = engine.state

        # Persist to disk immediately so the game shows up after restart
        self._manifest.add_game(game_id, {
            "role_counts": dict(config.role_counts),
        })

        task = asyncio.create_task(engine.start())
        self._tasks[game_id] = task

        logger.info(f"Game created: {game_id}, {config.total_players} players")
        return game_id

    def _create_roles(
        self, config: GameConfig, prompt_builder: PromptBuilder, models: list[str],
    ) -> dict:
        return builtin_registry.create_roles(
            config.role_counts,
            config.total_players,
            prompt_builder,
            llm_client_factory=lambda: LLMClient(model=random.choice(models)),
        )

    def get_game_state(self, game_id: str) -> Optional[GameState]:
        return self._games.get(game_id)

    def list_games(self) -> list[str]:
        return list(self._games.keys())

    # ── Event Handlers ─────────────────────────────────────────

    async def _on_phase_changed(self, **kwargs) -> None:
        state = kwargs.get("state")
        if state is None:
            return
        self._games[state.game_id] = state
        self._persist_game(state)
        await self.ws_manager.broadcast(
            state.game_id, "phase_change",
            phase=kwargs.get("phase", ""),
            round_number=kwargs.get("round_number", 0),
            state=state.get_public_state(),
        )

    async def _on_player_died(self, **kwargs) -> None:
        death = kwargs.get("death")
        if death is None:
            return
        death_dict = death.to_dict() if hasattr(death, "to_dict") else death
        for game_id in self._games:
            await self.ws_manager.broadcast(
                game_id, "player_died", death=death_dict,
            )

    async def _on_speech_made(self, **kwargs) -> None:
        speech = kwargs.get("speech")
        if speech is None:
            return
        speech_dict = speech.to_dict() if hasattr(speech, "to_dict") else speech
        for game_id in self._games:
            engine = self._engines.get(game_id)
            if engine:
                await self.ws_manager.broadcast(
                    game_id, "speech", speech=speech_dict,
                )

    async def _on_vote_cast(self, **kwargs) -> None:
        vote = kwargs.get("vote")
        if vote is None:
            return
        vote_dict = vote.to_dict() if hasattr(vote, "to_dict") else vote
        for game_id in self._games:
            await self.ws_manager.broadcast(
                game_id, "vote_cast", vote=vote_dict,
            )

    async def _on_game_over(self, **kwargs) -> None:
        win_result = kwargs.get("win_result")
        if win_result is None:
            return
        wr_dict = win_result.to_dict() if hasattr(win_result, "to_dict") else win_result
        for game_id in self._games:
            state = self._games.get(game_id)
            if state:
                # Persist final result
                self._manifest.update_game(
                    game_id,
                    phase="game_over",
                    winner=wr_dict.get("winning_camp"),
                )
                await self.ws_manager.broadcast(
                    game_id, "game_over", win_result=wr_dict,
                    state=state.get_public_state(),
                )

    async def _on_night_substep(self, **kwargs) -> None:
        game_id = kwargs.get("game_id")
        if game_id is None:
            return
        await self.ws_manager.broadcast(
            game_id, "night_substep",
            step=kwargs.get("step", ""),
            highlight_seats=kwargs.get("highlight_seats", []),
            action_seat=kwargs.get("action_seat"),
            action=kwargs.get("action"),
            wolf_kill_target=kwargs.get("wolf_kill_target"),
            round_number=kwargs.get("round_number", 0),
        )
