from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NightAction:
    player_seat: int
    action_type: str  # kill, check, save, poison, pass
    target_seat: Optional[int] = None
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "player_seat": self.player_seat,
            "action_type": self.action_type,
            "target_seat": self.target_seat,
            "reasoning": self.reasoning,
        }


@dataclass
class VoteAction:
    voter_seat: int
    target_seat: Optional[int] = None  # None or 0 = abstain
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "voter_seat": self.voter_seat,
            "target_seat": self.target_seat,
            "reasoning": self.reasoning,
        }


@dataclass
class SpeechRecord:
    player_seat: int
    text: str
    round_number: int

    def to_dict(self) -> dict:
        return {
            "player_seat": self.player_seat,
            "text": self.text,
            "round_number": self.round_number,
        }


@dataclass
class DeathReport:
    player_seat: int
    cause: str  # wolf_kill, poison, exile, hunter_shot, self_explode, love_death
    round_number: int

    def to_dict(self) -> dict:
        return {
            "player_seat": self.player_seat,
            "cause": self.cause,
            "round_number": self.round_number,
        }


@dataclass
class WinResult:
    winning_camp: str  # good, werewolf, third_party
    reason: str  # all_wolves_dead, all_gods_dead, all_villagers_dead

    def to_dict(self) -> dict:
        return {
            "winning_camp": self.winning_camp,
            "reason": self.reason,
        }
