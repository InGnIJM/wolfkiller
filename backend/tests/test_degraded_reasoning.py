"""The marker that separates an engine-made command from a seat's own answer.

Why this file exists: every fallback used to be a plain ``ActionCommand`` whose
``reasoning`` was invented in-tree ("系统异常，本轮未行动"). Because the command
carried no provenance, four consumers could not tell it from a real answer —
the resolve hook built a thought line out of it, the projector published that as
``night_thought``, ``_log_audience_pairs`` wrote it into the seat's own history,
and the next prompt fed it back as the seat's memory. This module pins down the
invariants that make the ``degraded`` flag trustworthy:

* it is **not** part of the command's serialized form, so ``stable_digest`` —
  which also sorts aggregate command groups — cannot change because of it;
* the wolf-vote dataclass carries it through construction and into the command
  the pack aggregate consumes;
* the placeholder wording never claims a system fault, because a command that
  failed validation three times is degraded without anything being broken.

The checkpoint round trip lives in ``test_engine_recovery.py``, next to the
engine fixture that can actually encode and restore a pending night batch.
"""

from __future__ import annotations

import pytest

from app.core.night_flow import WolfVote
from app.models.pipeline import DEGRADED_REASONING, DEGRADED_THOUGHT, ActionCommand


class TestDegradedIsNotPartOfTheIdentity:
    def test_the_flag_is_excluded_from_the_serialized_command(self) -> None:
        command = ActionCommand(
            action_type="pass", target_seat=None, reasoning="先看", degraded=True,
        )

        assert command.degraded is True
        assert "degraded" not in command.model_dump()
        assert "degraded" not in command.to_json()

    def test_the_stable_digest_ignores_the_flag(self) -> None:
        """`aggregate_effects` sorts a command group by this digest.

        If the flag reached the digest, two otherwise identical fallback
        commands could swap order mid-contract — a silent behaviour change for a
        field that is purely engine bookkeeping.
        """
        plain = ActionCommand(action_type="pass", target_seat=None, reasoning="先看")
        marked = ActionCommand(
            action_type="pass", target_seat=None, reasoning="先看", degraded=True,
        )

        assert plain.stable_digest() == marked.stable_digest()

    def test_a_round_trip_through_json_yields_a_live_command(self) -> None:
        """Decoding cannot resurrect the flag, so it must default to False."""
        restored = ActionCommand.from_json(
            ActionCommand(action_type="kill", target_seat=2, reasoning="刀他",
                          degraded=True).to_json()
        )

        assert restored.degraded is False
        assert restored.target_seat == 2

    def test_the_placeholder_never_claims_a_system_fault(self) -> None:
        """Degraded is not the same as broken.

        The old wording was "系统异常，本轮未行动" and it was wrong twice over: a
        command that failed validation three times had no fault behind it, and
        "本轮未行动" read as a decision the seat made. The replacement names who
        acted (the system) without inventing a fault or a motive.
        """
        for text in (DEGRADED_REASONING, DEGRADED_THOUGHT):
            assert "异常" not in text
            assert "决定" not in text
        assert "未提交" in DEGRADED_REASONING
        assert "系统代行" in DEGRADED_THOUGHT


class TestWolfVoteProvenance:
    def test_a_degraded_ballot_carries_both_the_flag_and_the_placeholder(self) -> None:
        vote = WolfVote(1, "pass", None, DEGRADED_REASONING, degraded=True)

        assert vote.degraded is True
        assert vote.to_command().degraded is True

    def test_a_real_ballot_is_not_degraded(self) -> None:
        vote = WolfVote(1, "kill", 3, "他发言像预言家")

        assert vote.degraded is False
        assert vote.to_command().degraded is False

    def test_a_non_boolean_flag_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid degraded flag"):
            WolfVote(1, "pass", None, "先看", degraded=1)  # type: ignore[arg-type]

