from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SnapshotVersionError(RuntimeError):
    """Raised when a persisted game snapshot is incompatible with the running registry or pipeline version."""


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
    ERROR = "error"


class Camp(str, Enum):
    GOOD = "good"
    WEREWOLF = "werewolf"
    THIRD_PARTY = "third_party"


def _default_role_counts() -> dict[str, int]:
    return {
        "wolf-killer-werewolf": 3,
        "wolf-killer-villager": 3,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-hunter": 1,
    }


@dataclass
class SheriffOfficeState:
    candidates: set[int] = field(default_factory=set)
    active: set[int] = field(default_factory=set)
    pk_seats: set[int] = field(default_factory=set)
    badge_destroyed: bool = False
    skip_remaining_day: bool = False
    speech_side: Optional[str] = None
    step: str = ""


#: When the witch may save herself with the antidote.
#:
#: The official card text is flat — "女巫在每天晚上最多使用一瓶药，女巫不可自救" —
#: with no first-night exception, so ``never`` is the base ruling and **every
#: preset that reproduces an official board must say so explicitly**. The
#: default stays ``first_night_only`` because the original (Jiamian) boards and
#: the project's long-standing behaviour use it; a preset that omits the key
#: silently inherits that, which is exactly how the NetEase boards drifted.
#: The policy travels in ``GameConfig`` and is projected into the witch's action
#: context, so the role hook stays free of any board-specific branch.
DEFAULT_WITCH_SELF_SAVE = "first_night_only"
WITCH_SELF_SAVE_POLICIES = ("first_night_only", "never")

#: How a board decides the game. ``edge`` is the historical 屠边 rule. ``city``
#: is 屠城: one side wins only when the other side is entirely gone. A living
#: third-party allegiance blocks both, on either setting.
DEFAULT_VICTORY = "edge"
VICTORY_POLICIES = ("edge", "city")


@dataclass(init=False)
class GameConfig:
    role_counts: dict[str, int] = field(default_factory=_default_role_counts)
    reveal_on_death: bool = False
    enable_sheriff: bool = False
    witch_self_save: str = DEFAULT_WITCH_SELF_SAVE
    victory: str = DEFAULT_VICTORY

    def __init__(
        self,
        role_counts: Optional[dict[str, int]] = None,
        *,
        num_werewolves: Optional[int] = None,
        num_villagers: Optional[int] = None,
        num_seers: Optional[int] = None,
        num_witches: Optional[int] = None,
        num_hunters: Optional[int] = None,
        reveal_on_death: bool = False,
        enable_sheriff: bool = False,
        witch_self_save: str = DEFAULT_WITCH_SELF_SAVE,
        victory: str = DEFAULT_VICTORY,
    ) -> None:
        if witch_self_save not in WITCH_SELF_SAVE_POLICIES:
            raise ValueError(f"unknown witch self-save policy: {witch_self_save!r}")
        if victory not in VICTORY_POLICIES:
            raise ValueError(f"unknown victory policy: {victory!r}")
        legacy_counts = (
            ("wolf-killer-werewolf", num_werewolves, 3),
            ("wolf-killer-villager", num_villagers, 3),
            ("wolf-killer-seer", num_seers, 1),
            ("wolf-killer-witch", num_witches, 1),
            ("wolf-killer-hunter", num_hunters, 1),
        )
        has_legacy_counts = any(count is not None for _, count, _ in legacy_counts)

        if role_counts is not None:
            if has_legacy_counts:
                raise ValueError("role_counts cannot be combined with legacy role counts")
            self.role_counts = role_counts
        elif has_legacy_counts:
            self.role_counts = {
                role_id: count if count is not None else default_count
                for role_id, count, default_count in legacy_counts
            }
        else:
            self.role_counts = _default_role_counts()
        self.reveal_on_death = bool(reveal_on_death)
        self.enable_sheriff = bool(enable_sheriff)
        self.witch_self_save = witch_self_save
        self.victory = victory

    @property
    def total_players(self) -> int:
        return sum(self.role_counts.values())

    def role_distribution(self) -> list[str]:
        return [
            role_id
            for role_id, count in self.role_counts.items()
            for _ in range(count)
        ]


@dataclass
class PlayerState:
    seat_number: int
    role: str
    camp: str
    is_alive: bool = True
    # Legacy mirrors of the gun and the two potions: the engine wrote them until
    # the generic role pipeline moved every resource into ``runtime.role_resources``
    # (2026-08-13). They stay in the dataclass because the checkpoint decoder
    # checks the player row's field set *exactly* — dropping them would make every
    # existing checkpoint fail to decode. Nothing writes them any more and nothing
    # reads them as a source of truth: the resource view is canonical.
    has_antidote: bool = False
    has_poison: bool = False
    has_gun: bool = False
    is_sheriff: bool = False
    check_results: list[dict] = field(default_factory=list)
    revealed_role: Optional[str] = None

    def mark_dead(self, cause: str = "") -> None:
        self.is_alive = False


@dataclass
class GameState:
    game_id: str
    phase: GamePhase = GamePhase.WAITING
    round_number: int = 0
    vote_round: int = 1
    is_tiebreak: bool = False
    tiebreak_candidates: set[int] = field(default_factory=set)
    supplemental_speakers: set[int] = field(default_factory=set)
    voted_seats: set[int] = field(default_factory=set)
    accepted_action_keys: set[str] = field(default_factory=set)
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
    sheriff_office: SheriffOfficeState = field(default_factory=SheriffOfficeState)
    speaking_order: list[int] = field(default_factory=list)  # 本轮发言顺序
    current_speaker: Optional[int] = None  # 当前正在发言的玩家
    # ── Pipeline snapshot versioning ─────────────────────────────
    state_revision: int = 0
    pipeline_version: str = ""
    registry_digest: str = ""
    spec_versions: dict[str, int] = field(default_factory=dict)
    effect_schema_version: int = 0
    last_consistent_checkpoint: Optional[str] = None

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
            "reveal_on_death": self.config.reveal_on_death,
            "enable_sheriff": self.config.enable_sheriff,
            "players": {
                s: {
                    "seat_number": p.seat_number,
                    "is_alive": p.is_alive,
                    "is_sheriff": p.is_sheriff,
                    "role": p.role,
                    "camp": p.camp,
                }
                for s, p in self.players.items()
            },
            "sheriff": self.sheriff,
            "speeches": [s.to_dict() if hasattr(s, "to_dict") else s for s in self.speeches],
            "death_history": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.death_history],
            "win_result": self.win_result,
        }
