import asyncio
from enum import Enum
from typing import Callable, Coroutine, Any
import logging

logger = logging.getLogger(__name__)


class GameEvent(str, Enum):
    GAME_STARTED = "game_started"
    PHASE_CHANGED = "phase_changed"
    PLAYER_DIED = "player_died"
    SPEECH_MADE = "speech_made"
    VOTE_CAST = "vote_cast"
    NIGHT_ACTION_SUBMITTED = "night_action_submitted"
    NIGHT_SUBSTEP = "night_substep"
    WIN_CONDITION_MET = "win_condition_met"
    GAME_OVER = "game_over"


EventHandler = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """Simple async pub/sub event bus for game events."""

    def __init__(self):
        self._subscribers: dict[str, list[EventHandler]] = {}

    def subscribe(self, event_type: str | GameEvent, handler: EventHandler) -> None:
        key = event_type.value if isinstance(event_type, GameEvent) else event_type
        self._subscribers.setdefault(key, []).append(handler)

    def unsubscribe(self, event_type: str | GameEvent, handler: EventHandler) -> None:
        key = event_type.value if isinstance(event_type, GameEvent) else event_type
        if key in self._subscribers:
            self._subscribers[key] = [h for h in self._subscribers[key] if h != handler]

    async def publish(self, event_type: str | GameEvent, **kwargs: Any) -> None:
        key = event_type.value if isinstance(event_type, GameEvent) else event_type
        handlers = self._subscribers.get(key, [])
        if not handlers:
            return

        tasks = [handler(**kwargs) for handler in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Event handler error for {key}: {result}")

    def clear(self) -> None:
        self._subscribers.clear()
