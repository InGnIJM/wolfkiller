from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PublicNightSubstep:
    substep: str
    round_number: int

    @classmethod
    def from_internal(
        cls,
        *,
        step: str,
        round_number: int,
        **_private: object,
    ) -> "PublicNightSubstep":
        return cls(substep=step, round_number=round_number)

    def to_payload(self) -> dict[str, str | int]:
        return {
            "phase": "night",
            "round_number": self.round_number,
            "substep": self.substep,
        }


@dataclass(frozen=True)
class PublicVoteEvent:
    round_number: int
    voter_seat: int
    target_seat: int | None

    @staticmethod
    def _field(vote: object, name: str) -> object:
        if isinstance(vote, Mapping):
            return vote.get(name)
        return getattr(vote, name, None)

    @staticmethod
    def _is_positive_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    @classmethod
    def from_internal(
        cls,
        *,
        vote: object,
        round_number: object,
        **_private: object,
    ) -> "PublicVoteEvent | None":
        try:
            voter_seat = cls._field(vote, "voter_seat")
            target_seat = cls._field(vote, "target_seat")
        except Exception:
            return None

        if not cls._is_positive_int(round_number):
            return None
        if not cls._is_positive_int(voter_seat):
            return None
        if target_seat is not None and not cls._is_positive_int(target_seat):
            return None

        return cls(
            round_number=round_number,
            voter_seat=voter_seat,
            target_seat=target_seat,
        )

    def to_payload(self) -> dict[str, int | None]:
        return {
            "round_number": self.round_number,
            "voter_seat": self.voter_seat,
            "target_seat": self.target_seat,
        }
