from __future__ import annotations

import weakref
from threading import Lock, RLock

from app.models.game import GameState

_GUARD = Lock()
_LOCKS: dict[int, tuple[weakref.ReferenceType[GameState], RLock]] = {}


def state_transaction_lock(state: GameState) -> RLock:
    """Return the identity-bound transaction lock shared by all state writers."""
    if type(state) is not GameState:
        raise TypeError("state must be GameState")
    key = id(state)
    with _GUARD:
        entry = _LOCKS.get(key)
        if entry is not None and entry[0]() is state:
            return entry[1]
        lock = RLock()
        reference = weakref.ref(state, lambda current, k=key: _drop(k, current))
        _LOCKS[key] = (reference, lock)
        return lock


def _drop(key: int, reference: weakref.ReferenceType[GameState]) -> None:
    with _GUARD:
        entry = _LOCKS.get(key)
        if entry is not None and entry[0] is reference:
            _LOCKS.pop(key, None)
