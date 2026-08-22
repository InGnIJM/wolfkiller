"""Trusted voting-domain service with atomic, idempotent terminal ballots."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

from app.core.effect_applier import (
    EffectApplier,
    EffectPermission,
    EffectRejected,
    derive_effect_id,
)
from app.core.state_transaction import state_transaction_lock
from app.models.game import GamePhase, GameState
from app.models.pipeline import EffectKind, GameEffect
from app.models.vote import CastVoteArgs, VoteError, VoteReceipt, VoteStatus, VoteWindow


class VoteService:
    """The sole state-writing boundary for exile votes."""

    def __init__(self, state: GameState, *, clock: Callable[[], float] = time.monotonic):
        if type(state) is not GameState:
            raise TypeError("state must be GameState")
        self._state = state
        self._clock = clock
        self._windows: dict[str, VoteWindow] = {}
        self._closed: set[str] = set()

    def open_window(self, *, timeout_seconds: float) -> VoteWindow:
        with state_transaction_lock(self._state):
            if self._state.phase is not GamePhase.VOTE_CASTING:
                raise VoteError("vote_wrong_phase")
            if type(timeout_seconds) is not float or timeout_seconds <= 0:
                raise ValueError("timeout_seconds must be a positive float")
            alive = frozenset(self._state.alive_players())
            window = VoteWindow(
                game_id=self._state.game_id,
                round_number=self._state.round_number,
                vote_round=self._state.vote_round,
                eligible_voters=alive,
                eligible_targets=alive,
                deadline=float(self._clock() + timeout_seconds),
            )
            return self._windows.setdefault(window.window_id, window)

    def window(self, window_id: str) -> VoteWindow:
        try:
            return self._windows[window_id]
        except KeyError:
            raise VoteError("vote_window_not_found") from None

    def submit(
        self, window_id: str, voter_seat: int, command: CastVoteArgs,
    ) -> VoteReceipt:
        if type(command) is not CastVoteArgs:
            raise TypeError("command must be CastVoteArgs")
        digest = command.command_digest()
        status = (
            VoteStatus.ACCEPTED_VOTE
            if command.action_type == "vote"
            else VoteStatus.VOLUNTARY_ABSTAIN
        )
        return self._commit(
            window_id, voter_seat, status=status,
            target_seat=command.target_seat, command_digest=digest,
        )

    def technical_abstain(
        self, window_id: str, voter_seat: int, *, failure_code: str,
        timeout_type: str | None = None,
    ) -> VoteReceipt:
        digest = hashlib.sha256(
            f"technical_abstain\0{failure_code}\0{timeout_type or ''}".encode()
        ).hexdigest()
        return self._commit(
            window_id, voter_seat, status=VoteStatus.TECHNICAL_ABSTAIN,
            target_seat=None, command_digest=digest, failure_code=failure_code,
            timeout_type=timeout_type, allow_closed=True,
        )

    def close_window(
        self, window_id: str, *, failure_code: str,
        timeout_type: str | None = None,
    ) -> tuple[VoteReceipt, ...]:
        with state_transaction_lock(self._state):
            window = self.window(window_id)
            if self._clock() < window.deadline:
                raise VoteError("vote_deadline_not_reached")
            self._closed.add(window_id)
            for voter_seat in sorted(self.missing_voters(window_id)):
                self.technical_abstain(
                    window_id, voter_seat, failure_code=failure_code,
                    timeout_type=timeout_type,
                )
            return self.receipts(window_id)

    def receipts(self, window_id: str) -> tuple[VoteReceipt, ...]:
        self.window(window_id)
        with state_transaction_lock(self._state):
            runtime = getattr(self._state, "_pipeline_runtime", None)
            ledger = {} if runtime is None else runtime.vote_receipts
            receipts = (
                self._model_receipt(item)
                for item in ledger.values()
                if item.get("window_id") == window_id
            )
            return tuple(sorted(receipts, key=lambda item: item.voter_seat))

    def missing_voters(self, window_id: str) -> frozenset[int]:
        window = self.window(window_id)
        completed = {receipt.voter_seat for receipt in self.receipts(window_id)}
        return window.eligible_voters - completed

    def tally(self, window_id: str) -> dict[int, int]:
        tally: dict[int, int] = {}
        for receipt in self.receipts(window_id):
            if receipt.status is VoteStatus.ACCEPTED_VOTE:
                target = receipt.target_seat
                assert target is not None
                tally[target] = tally.get(target, 0) + 1
        return tally

    def _commit(
        self, window_id: str, voter_seat: int, *, status: VoteStatus,
        target_seat: int | None, command_digest: str,
        failure_code: str | None = None, timeout_type: str | None = None,
        allow_closed: bool = False,
    ) -> VoteReceipt:
        with state_transaction_lock(self._state):
            window = self.window(window_id)
            action_key = window.action_key(voter_seat)
            existing = self._receipt(action_key)
            if existing is not None:
                if existing.command_digest != command_digest:
                    raise VoteError("vote_conflict")
                return existing.as_replay()
            if not allow_closed and (
                window_id in self._closed or self._clock() >= window.deadline
            ):
                raise VoteError("vote_window_closed")
            if (
                self._state.phase is not GamePhase.VOTE_CASTING
                or self._state.round_number != window.round_number
                or self._state.vote_round != window.vote_round
            ):
                raise VoteError("vote_wrong_phase")
            player = self._state.players.get(voter_seat)
            if voter_seat not in window.eligible_voters or player is None or not player.is_alive:
                raise VoteError("vote_actor_ineligible")
            if target_seat is not None:
                target = self._state.players.get(target_seat)
                if (
                    target_seat not in window.eligible_targets
                    or target is None or not target.is_alive
                ):
                    raise VoteError("vote_target_ineligible")
            revision = getattr(getattr(self._state, "_pipeline_runtime", None), "revision", 0)
            accepted_at = float(self._clock())
            payload = {
                "window_id": window_id,
                "action_key": action_key,
                "voter_seat": voter_seat,
                "round_number": window.round_number,
                "vote_round": window.vote_round,
                "status": status.value,
                "target_seat": target_seat,
                "command_digest": command_digest,
                "accepted_at": accepted_at,
                "failure_code": failure_code,
                "timeout_type": timeout_type,
            }
            effects = (
                GameEffect(
                    effect_id=derive_effect_id(action_key, 0),
                    kind=EffectKind.ACCEPT_ACTION,
                    source_action_key=action_key,
                    payload={
                        "actor_seat": voter_seat,
                        "contract_id": "cast_vote",
                        "window_id": window_id,
                        "round_number": window.round_number,
                    },
                    expected_revision=revision,
                    sort_key=(0,),
                ),
                GameEffect(
                    effect_id=derive_effect_id(action_key, 1),
                    kind=EffectKind.RECORD_VOTE,
                    source_action_key=action_key,
                    target_seat=target_seat,
                    payload=payload,
                    expected_revision=revision,
                    sort_key=(1,),
                ),
            )
            allowed_targets = window.eligible_targets | {voter_seat}
            permission = EffectPermission(
                voter_seat,
                frozenset({EffectKind.RECORD_VOTE}),
                frozenset({EffectKind.RECORD_VOTE}),
                allowed_targets,
                frozenset(),
            )
            try:
                EffectApplier().apply(self._state, effects, permission)
            except EffectRejected as error:
                if str(error) == "vote_conflict":
                    raise VoteError("vote_conflict") from error
                raise
            receipt = self._receipt(action_key)
            if receipt is None:
                raise RuntimeError("vote commit did not produce a receipt")
            return receipt

    def _receipt(self, action_key: str) -> VoteReceipt | None:
        runtime = getattr(self._state, "_pipeline_runtime", None)
        if runtime is None:
            return None
        value = runtime.vote_receipts.get(action_key)
        return (
            None if value is None
            else self._model_receipt(value)
        )

    @staticmethod
    def _model_receipt(value: object) -> VoteReceipt:
        fields = VoteReceipt.model_fields
        payload = {
            key: item for key, item in dict(value).items() if key in fields
        }
        payload["status"] = VoteStatus(payload["status"])
        return VoteReceipt.model_validate(payload)
