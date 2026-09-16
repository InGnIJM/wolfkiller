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

# Role-agnostic runtime statuses that shape exile-vote eligibility. Roles grant
# them through ADD_STATUS effects; the vote domain only reads them.
NO_VOTE_STATUS = "no_vote"
EXILE_IMMUNE_STATUS = "exile_immune"


def seats_with_status(state: GameState, status: str) -> frozenset[int]:
    """Seats whose pipeline runtime currently carries ``status``."""
    if type(state) is not GameState:
        raise TypeError("state must be GameState")
    if type(status) is not str or not status:
        raise ValueError("status must be a non-empty string")
    with state_transaction_lock(state):
        runtime = getattr(state, "_pipeline_runtime", None)
        statuses = {} if runtime is None else runtime.statuses
        return frozenset(
            seat for seat, values in statuses.items()
            if isinstance(values, (set, frozenset)) and status in values
        )


def eligible_exile_voters(state: GameState) -> frozenset[int]:
    """Alive seats that may cast an exile ballot."""
    return frozenset(state.alive_players()) - seats_with_status(state, NO_VOTE_STATUS)


def eligible_exile_targets(state: GameState) -> frozenset[int]:
    """Alive seats that may still be exiled by vote."""
    return frozenset(state.alive_players()) - seats_with_status(state, EXILE_IMMUNE_STATUS)


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
            window = VoteWindow(
                game_id=self._state.game_id,
                round_number=self._state.round_number,
                vote_round=self._state.vote_round,
                eligible_voters=eligible_exile_voters(self._state),
                eligible_targets=eligible_exile_targets(self._state),
                deadline=float(self._clock() + timeout_seconds),
            )
            return self._windows.setdefault(window.window_id, window)

    def window(self, window_id: str) -> VoteWindow:
        try:
            return self._windows[window_id]
        except KeyError:
            raise VoteError("vote_window_not_found") from None

    def arm_window(self, window_id: str, *, timeout_seconds: float) -> VoteWindow:
        """Start the authoritative deadline after engine setup is complete."""
        if type(timeout_seconds) is not float or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive float")
        with state_transaction_lock(self._state):
            window = self.window(window_id)
            armed = window.model_copy(update={
                "deadline": float(self._clock() + timeout_seconds),
            })
            self._windows[window_id] = armed
            return armed

    def checkpoint(self) -> dict[str, object]:
        """Serialize windows using remaining active time, never a process deadline."""
        with state_transaction_lock(self._state):
            now = float(self._clock())
            windows = []
            for window_id, window in sorted(self._windows.items()):
                windows.append({
                    "window_id": window_id,
                    "game_id": window.game_id,
                    "round_number": window.round_number,
                    "vote_round": window.vote_round,
                    "eligible_voters": sorted(window.eligible_voters),
                    "eligible_targets": sorted(window.eligible_targets),
                    "remaining_ms": max(0, round((window.deadline - now) * 1000)),
                })
            return {"windows": windows, "closed": sorted(self._closed)}

    def restore_checkpoint(self, value: object) -> None:
        """Replace window orchestration and rebase deadlines on this process clock."""
        if not isinstance(value, dict) or set(value) != {"windows", "closed"}:
            raise ValueError("invalid vote checkpoint")
        windows_raw, closed_raw = value["windows"], value["closed"]
        if type(windows_raw) is not list or type(closed_raw) is not list:
            raise ValueError("invalid vote checkpoint")
        now = float(self._clock())
        windows: dict[str, VoteWindow] = {}
        for raw in windows_raw:
            names = {
                "window_id", "game_id", "round_number", "vote_round",
                "eligible_voters", "eligible_targets", "remaining_ms",
            }
            if not isinstance(raw, dict) or set(raw) != names:
                raise ValueError("invalid vote window checkpoint")
            remaining = raw["remaining_ms"]
            if type(remaining) is not int or remaining < 0:
                raise ValueError("invalid vote remaining time")
            try:
                window = VoteWindow(
                    game_id=raw["game_id"], round_number=raw["round_number"],
                    vote_round=raw["vote_round"],
                    eligible_voters=frozenset(raw["eligible_voters"]),
                    eligible_targets=frozenset(raw["eligible_targets"]),
                    deadline=max(1e-9, now + remaining / 1000.0),
                )
            except (TypeError, ValueError) as error:
                raise ValueError("invalid vote window checkpoint") from error
            if raw["window_id"] != window.window_id or window.window_id in windows:
                raise ValueError("invalid vote window id")
            windows[window.window_id] = window
        if any(type(item) is not str or item not in windows for item in closed_raw):
            raise ValueError("invalid closed vote window")
        if len(closed_raw) != len(set(closed_raw)):
            raise ValueError("duplicate closed vote window")
        with state_transaction_lock(self._state):
            self._windows = windows
            self._closed = set(closed_raw)

    def submit(
        self, window_id: str, voter_seat: int, command: CastVoteArgs,
        *, received_at: float | None = None,
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
            received_at=received_at,
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
        allow_closed: bool = False, received_at: float | None = None,
    ) -> VoteReceipt:
        with state_transaction_lock(self._state):
            window = self.window(window_id)
            action_key = window.action_key(voter_seat)
            existing = self._receipt(action_key)
            if existing is not None:
                if existing.command_digest != command_digest:
                    raise VoteError("vote_conflict")
                return existing.as_replay()
            arrival = float(self._clock()) if received_at is None else received_at
            if type(arrival) is not float or arrival < 0:
                raise TypeError("received_at must be a non-negative float")
            if not allow_closed and (
                window_id in self._closed or arrival >= window.deadline
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
