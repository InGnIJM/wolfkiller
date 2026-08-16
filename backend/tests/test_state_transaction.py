from __future__ import annotations

from threading import Thread
from weakref import ref

import pytest

import app.core.state_transaction as module
from app.core.state_transaction import state_transaction_lock
from app.models.game import GameState, PlayerState


def state() -> GameState:
    return GameState("g", players={1: PlayerState(1, "role", "good")})


def test_state_transaction_lock_is_identity_bound_and_validated() -> None:
    current = state()
    assert state_transaction_lock(current) is state_transaction_lock(current)
    with pytest.raises(TypeError):
        state_transaction_lock(object())


def test_state_transaction_drop_is_reentrant_under_guard() -> None:
    """The weakref callback may fire synchronously while the guard is held;
    it must not deadlock the calling thread."""
    current = state()
    identity = id(current); reference = ref(current)
    module._LOCKS[identity] = (reference, module.RLock())
    results: list[bool] = []

    def work() -> None:
        with module._GUARD:
            module._drop(identity, reference)
        results.append(identity not in module._LOCKS)

    thread = Thread(target=work, daemon=True); thread.start(); thread.join(timeout=5)
    assert not thread.is_alive(), "transaction drop deadlocked while guard was held"
    assert results == [True]
