from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


def is_last_words_eligible(cause: str, round_number: int, *, daytime: bool = False) -> bool:
    """Return whether a death cause grants the player last words.

    ``daytime`` selects the daytime rule ("every daytime death gets last
    words") instead of the night rule ("only the first night does"). The
    interrupted-day path passes it; the night path never does, which is what
    keeps the knight's own death silent even though it shares the
    ``knight_duel`` cause with the victim's.
    """
    if cause == "exile":
        return True
    if daytime:
        return cause in _DAYTIME_LAST_WORDS_CAUSES
    return round_number == 1 and cause in {"wolf_kill", "poison"}


# Daytime deaths that earn last words. The duel victim dies during the day; the
# knight's penance death is an explicit no-last-words exception in the rule
# text, and the werewolf king's self-explosion takes both sides down without
# words, so neither is listed here.
_DAYTIME_LAST_WORDS_CAUSES = frozenset({"knight_duel"})


@dataclass
class NightAction:
    player_seat: int
    action_type: str  # kill, check, save, poison, pass
    target_seat: Optional[int] = None
    reasoning: str = ""
    thinking: str = ""  # LLM 的内心思考过程

    def to_dict(self) -> dict:
        return {
            "player_seat": self.player_seat,
            "action_type": self.action_type,
            "target_seat": self.target_seat,
            "reasoning": self.reasoning,
            "thinking": self.thinking,
        }


@dataclass
class VoteAction:
    voter_seat: int
    target_seat: Optional[int] = None  # None or 0 = abstain
    reasoning: str = ""
    thinking: str = ""  # LLM 的内心思考过程

    def __post_init__(self) -> None:
        if self.target_seat == 0:
            self.target_seat = None

    def to_dict(self) -> dict:
        return {
            "voter_seat": self.voter_seat,
            "target_seat": self.target_seat,
            "reasoning": self.reasoning,
            "thinking": self.thinking,
        }


@dataclass
class SpeechRecord:
    player_seat: int
    text: str
    round_number: int
    phase: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "player_seat": self.player_seat,
            "text": self.text,
            "round_number": self.round_number,
        }
        if self.phase is not None:
            payload["phase"] = self.phase
        return payload


@dataclass
class DeathReport:
    player_seat: int
    # See ``app.models.values.DeathCause``. ``love_death`` grants no last words.
    cause: str
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
    #: The seats that share the win, when they are not simply "everyone of the
    #: winning camp" — the Lonely Girl wins with her idol's camp. ``None`` (the
    #: default, and every board without her) keeps ``to_dict()`` byte-identical
    #: to the historical shape, so no old reader or archive sees a new key.
    winners: tuple[int, ...] | None = None

    def to_dict(self) -> dict:
        result: dict[str, object] = {
            "winning_camp": self.winning_camp,
            "reason": self.reason,
        }
        if self.winners is not None:
            result["winners"] = list(self.winners)
        return result
