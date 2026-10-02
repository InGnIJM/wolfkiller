"""Guards the durable role instrumentation against closure late-binding.

``GameService._instrument_roles`` wraps every role's ``speak`` /
``request_action`` so the durable invocation layer can record and replay them.
The wrapped callables are loop-local names, so a wrapper that reads them from
the enclosing scope instead of binding them as default arguments sends **every**
seat through the **last** seat's role: seat N is prompted with the identity,
private facts and camp of the final seat in the dict.

That defect shipped on 2026-10-01 (the prompt-capture commit) and made a whole
12-player game speak, campaign and vote as one seat. It stayed invisible because
``_seat`` *was* bound correctly, so the database, the durable requests and
``game.log`` all recorded the right actor — only the model-facing prompt and
``llm_calls.log`` were wrong.

The invariant these tests pin: whatever a wrapper is installed on, calling it
must reach that same role.
"""
from __future__ import annotations

import asyncio

from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.services.game_service import GameService


class _FakeRole:
    """Minimal stand-in exposing the two instrumented callables."""

    def __init__(self, seat: int) -> None:
        self.seat = seat
        self.role_name = "wolf-killer-villager"
        self.llm_client = None
        self.calls: list[tuple[str, object]] = []

    async def speak(self, state, conversation_log, context, *, capture=None):
        self.calls.append(("speak", context))
        return f"speech-from-{self.seat}"

    async def request_action(self, state, conversation_log, request, *, capture=None):
        self.calls.append(("action", request))
        return f"action-from-{self.seat}"


def _service(tmp_path) -> GameService:
    service = GameService(
        ws_manager=WSManager(), event_bus=EventBus(), data_dir=str(tmp_path),
    )
    # ``_instrument_roles`` returns early without an invocation service, and the
    # real one needs a repository; this test only exercises the wrapper binding.
    service._model_invocations = object()  # type: ignore[assignment]
    service._durable_contexts["g"] = {"execution_generation": 1}
    return service


def _bindings(role: _FakeRole) -> tuple[object, object]:
    """The callables each installed wrapper closed over."""
    return (
        role.speak.__kwdefaults__["_call"],  # type: ignore[attr-defined]
        role.request_action.__kwdefaults__["_call"],  # type: ignore[attr-defined]
    )


async def _call_each(roles: dict[int, _FakeRole]) -> list[tuple[int, str, str]]:
    reached = []
    for seat, role in roles.items():
        speak_call, action_call = _bindings(role)
        reached.append((seat, await speak_call(None, None, "day_speech", None)))
        reached.append((seat, await action_call(None, None, "request", None)))
    return reached


def test_instrumented_roles_reach_their_own_seat(tmp_path) -> None:
    service = _service(tmp_path)
    roles = {seat: _FakeRole(seat) for seat in range(1, 4)}

    service._instrument_roles("g", roles)
    reached = asyncio.run(_call_each(roles))

    for seat, role in roles.items():
        assert role.calls == [("speak", "day_speech"), ("action", "request")], (
            f"seat {seat} was not driven through its own wrapper"
        )
    assert reached == [
        (1, "speech-from-1"), (1, "action-from-1"),
        (2, "speech-from-2"), (2, "action-from-2"),
        (3, "speech-from-3"), (3, "action-from-3"),
    ]


def test_instrumented_roles_bind_before_the_loop_advances(tmp_path) -> None:
    """The bindings must differ per seat, not all be the final iteration's."""
    service = _service(tmp_path)
    roles = {seat: _FakeRole(seat) for seat in range(1, 6)}

    service._instrument_roles("g", roles)

    speak_targets = [id(_bindings(role)[0]) for role in roles.values()]
    action_targets = [id(_bindings(role)[1]) for role in roles.values()]
    assert len(set(speak_targets)) == len(roles)
    assert len(set(action_targets)) == len(roles)
