from __future__ import annotations
from enum import Enum, auto
from typing import Callable, Optional
from app.models.game import GamePhase, GameState


class GameEvent(str, Enum):
    START = "start"
    ROLES_ASSIGNED = "roles_assigned"
    NIGHT_ACTIONS_COMPLETE = "night_actions_complete"
    DAWN_COMPLETE = "dawn_complete"
    LAST_WORDS_COMPLETE = "last_words_complete"
    SPEECHES_COMPLETE = "speeches_complete"
    VOTES_COMPLETE = "votes_complete"
    VOTE_RESOLVED = "vote_resolved"
    WIN_CHECKED = "win_checked"
    WEREWOLF_EXPLODED = "werewolf_exploded"
    SHERIFF_ELECTION_START = "sheriff_election_start"
    SHERIFF_ELECTION_COMPLETE = "sheriff_election_complete"
    GAME_OVER = "game_over"


Transition = tuple[GamePhase, GameEvent, GamePhase]


class GameStateMachine:
    """FSM governing all Werewolf game phase transitions."""

    transitions: list[Transition] = [
        # Night cycle
        (GamePhase.WAITING, GameEvent.START, GamePhase.ROLE_DEAL),
        (GamePhase.ROLE_DEAL, GameEvent.ROLES_ASSIGNED, GamePhase.NIGHT),
        (GamePhase.NIGHT, GameEvent.NIGHT_ACTIONS_COMPLETE, GamePhase.DAWN),
        (GamePhase.NIGHT, GameEvent.SHERIFF_ELECTION_START, GamePhase.SHERIFF_ELECTION),
        (GamePhase.NIGHT, GameEvent.GAME_OVER, GamePhase.GAME_OVER),
        (GamePhase.SHERIFF_ELECTION, GameEvent.SHERIFF_ELECTION_COMPLETE, GamePhase.DAWN),
        (GamePhase.SHERIFF_ELECTION, GameEvent.GAME_OVER, GamePhase.GAME_OVER),
        (GamePhase.DAWN, GameEvent.DAWN_COMPLETE, GamePhase.LAST_WORDS),

        # Day cycle
        (GamePhase.LAST_WORDS, GameEvent.LAST_WORDS_COMPLETE, GamePhase.SPEECH),
        (GamePhase.SPEECH, GameEvent.SPEECHES_COMPLETE, GamePhase.VOTE_CASTING),
        # A daytime interruption (e.g. a self-destruct) cancels the rest of
        # the day: no vote is held and the game goes straight to night.
        (GamePhase.SPEECH, GameEvent.WEREWOLF_EXPLODED, GamePhase.NIGHT),
        (GamePhase.VOTE_CASTING, GameEvent.VOTES_COMPLETE, GamePhase.VOTE_RESOLUTION),
        (GamePhase.VOTE_RESOLUTION, GameEvent.VOTE_RESOLVED, GamePhase.NIGHT),
        (GamePhase.VOTE_RESOLUTION, GameEvent.GAME_OVER, GamePhase.GAME_OVER),
    ]

    def __init__(self):
        self.current_state: GamePhase = GamePhase.WAITING
        self._on_enter_hooks: dict[GamePhase, list[Callable]] = {}
        self._on_exit_hooks: dict[GamePhase, list[Callable]] = {}
        self._event_names = {e.value: e for e in GameEvent}

    def on_enter(self, phase: GamePhase, callback: Callable) -> None:
        self._on_enter_hooks.setdefault(phase, []).append(callback)

    def on_exit(self, phase: GamePhase, callback: Callable) -> None:
        self._on_exit_hooks.setdefault(phase, []).append(callback)

    def get_state(self) -> GamePhase:
        return self.current_state

    def set_state(self, state: GamePhase) -> None:
        self.current_state = state

    def can_transition(self, event: GameEvent) -> bool:
        for src, evt, _ in self.transitions:
            if src == self.current_state and evt == event:
                return True
        return False

    def transition(self, event: GameEvent) -> GamePhase:
        for src, evt, dst in self.transitions:
            if src == self.current_state and evt == event:
                for hook in self._on_exit_hooks.get(self.current_state, []):
                    hook()
                self.current_state = dst
                for hook in self._on_enter_hooks.get(dst, []):
                    hook()
                return dst
        allowed = [evt for s, evt, _ in self.transitions if s == self.current_state]
        raise ValueError(
            f"Invalid transition: {self.current_state.value} -> {event.value}. "
            f"Allowed events from {self.current_state.value}: {[e.value for e in allowed]}"
        )

    def is_terminal(self) -> bool:
        return self.current_state == GamePhase.GAME_OVER

    def reset(self) -> None:
        self.current_state = GamePhase.WAITING
        self._on_enter_hooks.clear()
        self._on_exit_hooks.clear()
