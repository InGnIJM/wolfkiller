from dataclasses import dataclass


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
