from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class GamePhase(str, Enum):
    WAITING = "waiting"
    ROLE_DEAL = "role_deal"
    NIGHT = "night"
    DAWN = "dawn"
    LAST_WORDS = "last_words"
    SHERIFF_ELECTION = "sheriff_election"
    SPEECH = "speech"
    VOTE_CASTING = "vote_casting"
    VOTE_RESOLUTION = "vote_resolution"
    GAME_OVER = "game_over"


class Camp(str, Enum):
    GOOD = "good"
    WEREWOLF = "werewolf"
    THIRD_PARTY = "third_party"


@dataclass
class GameConfig:
    num_werewolves: int = 3
    num_villagers: int = 3
    num_seers: int = 1
    num_witches: int = 1
    num_hunters: int = 1

    @property
    def total_players(self) -> int:
        return (
            self.num_werewolves
            + self.num_villagers
            + self.num_seers
            + self.num_witches
            + self.num_hunters
        )

    def role_distribution(self) -> list[str]:
        roles = []
        roles.extend(["wolf-killer-werewolf"] * self.num_werewolves)
        roles.extend(["wolf-killer-villager"] * self.num_villagers)
        roles.extend(["wolf-killer-seer"] * self.num_seers)
        roles.extend(["wolf-killer-witch"] * self.num_witches)
        roles.extend(["wolf-killer-hunter"] * self.num_hunters)
        return roles


@dataclass
class PlayerState:
    seat_number: int
    role: str
    camp: str
    is_alive: bool = True
    has_antidote: bool = False
    has_poison: bool = False
    has_gun: bool = False
    is_sheriff: bool = False
    check_results: list[dict] = field(default_factory=list)
    revealed_role: Optional[str] = None

    def mark_dead(self, cause: str = "") -> None:
        self.is_alive = False
        # Only public exile reveals the role; night deaths (wolf_kill, poison, hunter_shot) stay hidden
        if cause == "exile":
            self.revealed_role = self.role

    def reset_alive(self) -> None:
        self.is_alive = True
        self.revealed_role = None


@dataclass
class GameState:
    game_id: str
    phase: GamePhase = GamePhase.WAITING
    round_number: int = 0
    config: GameConfig = field(default_factory=GameConfig)
    players: dict[int, PlayerState] = field(default_factory=dict)
    sheriff: Optional[int] = None
    speeches: list = field(default_factory=list)
    votes: list = field(default_factory=list)
    night_actions: list = field(default_factory=list)
    death_history: list = field(default_factory=list)
    win_result: Optional[dict] = None
    last_wolf_kill_target: Optional[int] = None
    sheriff_election_complete: bool = False
    speaking_order: list[int] = field(default_factory=list)  # 本轮发言顺序
    current_speaker: Optional[int] = None  # 当前正在发言的玩家

    def alive_players(self) -> dict[int, PlayerState]:
        return {s: p for s, p in self.players.items() if p.is_alive}

    def dead_players(self) -> dict[int, PlayerState]:
        return {s: p for s, p in self.players.items() if not p.is_alive}

    def players_by_camp(self, camp: str) -> dict[int, PlayerState]:
        return {s: p for s, p in self.players.items() if p.camp == camp}

    def alive_by_camp(self, camp: str) -> dict[int, PlayerState]:
        return {s: p for s, p in self.players.items() if p.camp == camp and p.is_alive}

    def get_public_state(self) -> dict:
        return {
            "game_id": self.game_id,
            "phase": self.phase.value,
            "round_number": self.round_number,
            "players": {
                s: {
                    "seat_number": p.seat_number,
                    "is_alive": p.is_alive,
                    "revealed_role": p.revealed_role,
                    "is_sheriff": p.is_sheriff,
                }
                for s, p in self.players.items()
            },
            "sheriff": self.sheriff,
            "speeches": [s.to_dict() if hasattr(s, "to_dict") else s for s in self.speeches],
            "death_history": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.death_history],
            "win_result": self.win_result,
        }
