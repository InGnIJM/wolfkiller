import asyncio
import logging
import random
import uuid
from typing import Optional

from app.models.game import GameState, GameConfig
from app.core.game_engine import GameEngine
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.agents.llm_client import LLMClient
from app.agents.prompt_builder import PromptBuilder
from app.roles import Werewolf, Witch, Seer, Hunter, Villager
from app.api.websocket.ws_handler import WSManager

logger = logging.getLogger(__name__)


class GameService:
    """Manages game lifecycle: creation, execution, state access, and event broadcasting."""

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

        self.event_bus.subscribe(BusEvent.PHASE_CHANGED, self._on_phase_changed)
        self.event_bus.subscribe(BusEvent.SPEECH_MADE, self._on_speech_made)
        self.event_bus.subscribe(BusEvent.VOTE_CAST, self._on_vote_cast)
        self.event_bus.subscribe(BusEvent.GAME_OVER, self._on_game_over)
        self.event_bus.subscribe(BusEvent.NIGHT_SUBSTEP, self._on_night_substep)

    async def create_game(
        self,
        num_werewolves: int = 3,
        num_villagers: int = 3,
        num_seers: int = 1,
        num_witches: int = 1,
        num_hunters: int = 1,
    ) -> str:
        game_id = str(uuid.uuid4())[:8]
        config = GameConfig(
            num_werewolves=num_werewolves,
            num_villagers=num_villagers,
            num_seers=num_seers,
            num_witches=num_witches,
            num_hunters=num_hunters,
        )

        # Create role instances
        llm_client = LLMClient()
        prompt_builder = PromptBuilder()
        roles = self._create_roles(config, prompt_builder, llm_client)

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

        task = asyncio.create_task(engine.start())
        self._tasks[game_id] = task

        logger.info(f"Game created: {game_id}, {config.total_players} players")
        return game_id

    def _create_roles(
        self, config: GameConfig, prompt_builder: PromptBuilder, llm_client: LLMClient,
    ) -> dict:
        role_names = config.role_distribution()
        random.shuffle(role_names)
        role_map = {
            "wolf-killer-werewolf": Werewolf,
            "wolf-killer-villager": Villager,
            "wolf-killer-seer": Seer,
            "wolf-killer-witch": Witch,
            "wolf-killer-hunter": Hunter,
        }
        roles = {}
        for seat, role_name in enumerate(role_names, start=1):
            cls = role_map.get(role_name)
            if cls is None:
                raise ValueError(f"Unknown role: {role_name}")
            roles[seat] = cls(seat, role_name, prompt_builder, llm_client)
        return roles

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
        await self.ws_manager.broadcast(
            state.game_id, "phase_change",
            phase=kwargs.get("phase", ""),
            round_number=kwargs.get("round_number", 0),
            state=state.get_public_state(),
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
