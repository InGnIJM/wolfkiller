# Public Event Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent unauthenticated observer WebSockets from receiving another game's events or private night-action details.

**Architecture:** `GameEngine` stamps public EventBus events with its authoritative `game_id`. `GameService` rejects missing, unknown, or mismatched envelopes and broadcasts only to that id's existing WebSocket bucket. A dedicated public-night serializer emits a closed allow-list rather than forwarding raw engine metadata.

**Tech Stack:** Python 3, FastAPI WebSocket, asyncio, pytest, pytest-asyncio, pytest-cov.

---

## File structure and boundaries

| Path | Responsibility |
| --- | --- |
| `backend/app/api/websocket/public_events.py` | Closed public DTO/serializer for night-progress WebSocket payloads; no EventBus or client state. |
| `backend/app/core/game_engine.py` | Produce identified domain events using `self.game_id`. |
| `backend/app/services/game_service.py` | Validate event envelopes, persist only matching state, project night metadata, and call targeted broadcasts. |
| `backend/tests/test_public_events.py` | DTO allow-list tests. |
| `backend/tests/test_game_engine.py` | Producer `game_id` tests. |
| `backend/tests/test_game_service.py` | Two-game routing, invalid-id, and service-to-WebSocket boundary tests. |

Do not modify frontend code, REST routes/schemas, logs/replay, or authentication in this plan.

### Task 1: Define the closed public night-progress DTO

**Files:**

- Create: `backend/app/api/websocket/public_events.py`
- Create: `backend/tests/test_public_events.py`

- [ ] **Step 1: Write failing DTO allow-list tests**

```python
from app.api.websocket.public_events import PublicNightSubstep


def test_public_night_substep_contains_only_public_progress_fields():
    event = PublicNightSubstep.from_internal(
        step="seer_check", round_number=4,
        highlight_seats=[3], action_seat=3,
        action={"action_type": "check", "target_seat": 6,
                "seer_result": "werewolf"},
        wolf_kill_target=2,
    )

    assert event.to_payload() == {
        "phase": "night", "round_number": 4, "substep": "seer_check",
    }


def test_public_night_substep_never_copies_private_or_future_fields():
    event = PublicNightSubstep.from_internal(
        step="witch_action", round_number=2,
        action={"target_seat": 5, "role": "witch", "result": "saved"},
        target_seat=5, unknown_future_secret="do-not-publish",
    )

    payload = event.to_payload()
    assert set(payload) == {"phase", "round_number", "substep"}
    private = {"action", "action_seat", "highlight_seats", "wolf_kill_target",
               "target_seat", "seer_result", "role", "result",
               "unknown_future_secret"}
    assert not private & set(payload)
```

- [ ] **Step 2: Run the tests to verify they fail before implementation**

Run: `python -m pytest backend/tests/test_public_events.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.websocket.public_events'`.

- [ ] **Step 3: Implement the minimal closed serializer**

```python
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PublicNightSubstep:
    substep: str
    round_number: int

    @classmethod
    def from_internal(
        cls, *, step: str, round_number: int, **_private: Any,
    ) -> "PublicNightSubstep":
        return cls(substep=step, round_number=round_number)

    def to_payload(self) -> dict[str, str | int]:
        return {
            "phase": "night",
            "round_number": self.round_number,
            "substep": self.substep,
        }
```

The ignored `**_private` is input compatibility only. Do not add `game_id` to this output: it is routing metadata held by `WSManager.broadcast(game_id, ...)`, not a public field.

- [ ] **Step 4: Run DTO tests with coverage**

Run: `python -m pytest backend/tests/test_public_events.py --cov=app.api.websocket.public_events --cov-report=term-missing -q`

Expected: PASS and 100% coverage for `app.api.websocket.public_events`.

- [ ] **Step 5: Commit the DTO boundary**

```bash
git add backend/app/api/websocket/public_events.py backend/tests/test_public_events.py
git commit -m "feat(ws): define public night event dto"
```

### Task 2: Stamp every engine public event with its game id

**Files:**

- Modify: `backend/app/core/game_engine.py:307-310, 708-709, 761-764, 823-826, 861-862, 1092, 1168-1171`
- Modify: `backend/tests/test_game_engine.py`

- [ ] **Step 1: Write failing producer identity tests**

```python
@pytest.mark.asyncio
async def test_engine_public_events_always_include_own_game_id():
    bus = EventBus()
    observed = {event: [] for event in (
        BusEvent.PHASE_CHANGED, BusEvent.PLAYER_DIED, BusEvent.SPEECH_MADE,
        BusEvent.VOTE_CAST, BusEvent.GAME_OVER,
    )}

    for event in observed:
        async def record(event=event, **kwargs):
            observed[event].append(kwargs)
        bus.subscribe(event, record)

    engine = GameEngine(game_id="game-a", event_bus=bus)
    engine.state.players = {
        1: PlayerState(1, "wolf-killer-villager", "good"),
        2: PlayerState(2, "wolf-killer-werewolf", "wolf"),
    }
    await engine._broadcast_phase_change()
    await engine.event_bus.publish(
        BusEvent.PLAYER_DIED, game_id=engine.game_id,
        death=DeathReport(player_seat=2, cause="wolf_kill", round_number=1),
    )
    await engine.event_bus.publish(
        BusEvent.SPEECH_MADE, game_id=engine.game_id,
        speech=SpeechRecord(player_seat=1, text="speech", round_number=1),
    )
    await engine.event_bus.publish(
        BusEvent.VOTE_CAST, game_id=engine.game_id,
        vote=VoteAction(voter_seat=1, target_seat=2, reasoning="vote"),
    )
    await engine.event_bus.publish(
        BusEvent.GAME_OVER, game_id=engine.game_id,
        win_result=WinResult(winning_camp="good", reason="test"),
    )

    assert all(records for records in observed.values())
    assert all(
        record["game_id"] == "game-a"
        for records in observed.values() for record in records
    )
```

Import `DeathReport`, `SpeechRecord`, `VoteAction`, `WinResult`, and `PlayerState` from their existing model modules. For the production implementation, retain and expand the existing tests that exercise each real producer (night death, hunter death, last words, day speech, vote casting, game-over) so a direct `EventBus.publish` in this illustrative envelope test cannot mask a missed production site.

- [ ] **Step 2: Run the focused test and observe the pre-fix failure**

Run: `python -m pytest backend/tests/test_game_engine.py -k public_events_always_include_own_game_id -q`

Expected: FAIL because one or more currently published records lack `game_id`.

- [ ] **Step 3: Add engine-owned event identity to every producer**

```python
await self.event_bus.publish(
    BusEvent.PLAYER_DIED, game_id=self.game_id, death=d,
)
await self.event_bus.publish(
    BusEvent.SPEECH_MADE, game_id=self.game_id,
    speech=SpeechRecord(player_seat=seat, text=speech_text,
                        round_number=self.state.round_number),
)
await self.event_bus.publish(BusEvent.VOTE_CAST, game_id=self.game_id, vote=vote)
await self.event_bus.publish(
    BusEvent.GAME_OVER, game_id=self.game_id, win_result=win_result,
)
await self.event_bus.publish(
    BusEvent.PHASE_CHANGED, game_id=self.game_id,
    phase=self.state.phase.value, round_number=self.state.round_number,
    state=self.state,
)
```

Apply the death form to both death producers. Keep `_broadcast_night_substep` identified as it already is. Do not add private fields to public events.

- [ ] **Step 4: Run event regression tests**

Run: `python -m pytest backend/tests/test_game_engine.py backend/tests/test_event_bus.py -q`

Expected: PASS.

- [ ] **Step 5: Commit producer identity propagation**

```bash
git add backend/app/core/game_engine.py backend/tests/test_game_engine.py
git commit -m "fix(events): identify engine public events"
```

### Task 3: Route non-night events to the one validated game

**Files:**

- Modify: `backend/app/services/game_service.py:236-306`
- Modify: `backend/tests/test_game_service.py`

- [ ] **Step 1: Write failing two-game and invalid-id tests**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callback", "payload", "message_type"),
    [
        ("_on_player_died", {"death": {"player_seat": 2}}, "player_died"),
        ("_on_speech_made", {"speech": {"player_seat": 2, "text": "hi"}}, "speech"),
        ("_on_vote_cast", {"vote": {"voter_seat": 2, "target_seat": 1}}, "vote_cast"),
        ("_on_game_over", {"win_result": {"winning_camp": "good"}}, "game_over"),
    ],
)
async def test_public_event_routes_only_to_its_registered_game(
    callback, payload, message_type,
):
    manager = WSManager()
    manager.broadcast = AsyncMock()
    service = GameService(manager, EventBus())
    service._games = {"game-a": MagicMock(), "game-b": MagicMock()}

    await getattr(service, callback)(game_id="game-a", **payload)

    assert manager.broadcast.await_count == 1
    assert manager.broadcast.await_args.args[:2] == ("game-a", message_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("game_id", [None, "not-registered"])
async def test_public_event_with_missing_or_unknown_game_id_is_dropped(game_id):
    manager = WSManager()
    manager.broadcast = AsyncMock()
    service = GameService(manager, EventBus())
    service._games = {"game-a": MagicMock(), "game-b": MagicMock()}

    await service._on_player_died(game_id=game_id, death={"player_seat": 2})

    manager.broadcast.assert_not_awaited()
```

Add a phase-change test in which envelope `game-a` conflicts with `state.game_id == "game-b"`; it must neither persist nor broadcast.

- [ ] **Step 2: Run focused tests and verify the existing global behaviour fails**

Run: `python -m pytest backend/tests/test_game_service.py -k "routes_only_to_its_registered_game or missing_or_unknown_game_id or mismatched_phase" -q`

Expected: FAIL because callbacks currently ignore `game_id` or iterate every game.

- [ ] **Step 3: Implement a shared known-game guard and targeted callbacks**

```python
def _known_game_id(self, kwargs: dict) -> str | None:
    game_id = kwargs.get("game_id")
    if not isinstance(game_id, str) or game_id not in self._games:
        logger.warning("Dropping public event for missing or unknown game_id: %r", game_id)
        return None
    return game_id

async def _on_player_died(self, **kwargs) -> None:
    game_id = self._known_game_id(kwargs)
    death = kwargs.get("death")
    if game_id is None or death is None:
        return
    death_dict = death.to_dict() if hasattr(death, "to_dict") else death
    await self.ws_manager.broadcast(game_id, "player_died", death=death_dict)
```

Use the guard-before-broadcast pattern for speech, vote, game over, and phase changes. For phases, reject `state is None` and `state.game_id != game_id` before assigning `_games`, persisting, or broadcasting. For game over, update only `self._games[game_id]` in the manifest and send only that state's public state. Remove all public-event `for game_id in self._games` broadcast loops.

- [ ] **Step 4: Run service and existing WebSocket tests**

Run: `python -m pytest backend/tests/test_game_service.py backend/tests/test_ws_handler.py -q`

Expected: PASS.

- [ ] **Step 5: Commit targeted public event routing**

```bash
git add backend/app/services/game_service.py backend/tests/test_game_service.py
git commit -m "fix(ws): isolate public events by game"
```

### Task 4: Project night events through the public DTO

**Files:**

- Modify: `backend/app/services/game_service.py:300-314`
- Modify: `backend/tests/test_game_service.py`

- [ ] **Step 1: Write the failing service-to-WebSocket privacy test**

```python
@pytest.mark.asyncio
async def test_night_substep_broadcasts_exact_public_payload_only():
    manager = WSManager()
    manager.broadcast = AsyncMock()
    service = GameService(manager, EventBus())
    service._games = {"game-a": MagicMock()}

    await service._on_night_substep(
        game_id="game-a", step="seer_check", round_number=3,
        highlight_seats=[4], action_seat=4,
        action={"target_seat": 2, "seer_result": "werewolf"},
        wolf_kill_target=2,
    )

    manager.broadcast.assert_awaited_once_with(
        "game-a", "night_substep",
        phase="night", round_number=3, substep="seer_check",
    )
    assert not {"action", "action_seat", "highlight_seats",
                "wolf_kill_target", "target_seat", "seer_result"} & set(
        manager.broadcast.await_args.kwargs
    )
```

Add missing and unknown game-id variants; both must leave `broadcast` uncalled.

- [ ] **Step 2: Run the privacy test to show the current leak**

Run: `python -m pytest backend/tests/test_game_service.py -k night_substep -q`

Expected: FAIL because the callback forwards `highlight_seats`, `action_seat`, `action`, and `wolf_kill_target`.

- [ ] **Step 3: Use the closed serializer and nothing else as output**

```python
from app.api.websocket.public_events import PublicNightSubstep

async def _on_night_substep(self, **kwargs) -> None:
    game_id = self._known_game_id(kwargs)
    step = kwargs.get("step")
    round_number = kwargs.get("round_number")
    if game_id is None or not isinstance(step, str) or not isinstance(round_number, int):
        return
    payload = PublicNightSubstep.from_internal(
        step=step, round_number=round_number,
        **{key: value for key, value in kwargs.items()
           if key not in {"game_id", "step", "round_number"}},
    ).to_payload()
    await self.ws_manager.broadcast(game_id, "night_substep", **payload)
```

The input dictionary is only an adapter; `PublicNightSubstep.to_payload()` is the sole wire-payload source. Do not add a catch-all `**payload` forwarding path.

- [ ] **Step 4: Run privacy and changed-branch coverage checks**

Run: `python -m pytest backend/tests/test_public_events.py backend/tests/test_game_service.py --cov=app.api.websocket.public_events --cov=app.services.game_service --cov-report=term-missing -q`

Expected: PASS. Cover every branch added in Tasks 3 and 4; do not lower the project coverage gate because pre-existing unrelated service branches remain uncovered.

- [ ] **Step 5: Commit night-payload projection**

```bash
git add backend/app/services/game_service.py backend/tests/test_game_service.py
git commit -m "fix(ws): hide private night event data"
```

### Task 5: Full verification and scope audit

**Files:**

- Modify only if a verified regression reveals a defect; otherwise no source file.

- [ ] **Step 1: Run the P0 regression set**

Run: `python -m pytest backend/tests/test_public_events.py backend/tests/test_event_bus.py backend/tests/test_ws_handler.py backend/tests/test_game_engine.py backend/tests/test_game_service.py -q`

Expected: PASS, including two-game isolation and exact night-payload assertions.

- [ ] **Step 2: Run the full backend suite and coverage report**

Run: `python -m pytest backend/tests --cov=app --cov-report=term-missing`

Expected: PASS. Record actual coverage; do not claim the repository-wide 100% gate is met unless the command reports it. Every newly introduced branch must be covered.

- [ ] **Step 3: Audit final change scope**

Run: `git diff HEAD~4..HEAD -- backend/app backend/tests docs`

Expected: production changes are limited to `public_events.py`, `game_engine.py`, and `game_service.py`; tests are limited to the three named test files; no frontend, REST, auth, or log/replay file changed.

- [ ] **Step 4: Report evidence without a catch-all commit**

Report exact commands, test counts, and coverage. If a defect is found, repeat red-green-refactor in a new at-most-three-file `fix(ws): ...` commit rather than amending unrelated work.

## Completion checklist

- [ ] Each public engine event carries the owning `game_id`.
- [ ] Missing, unknown, and phase-mismatched ids are dropped rather than globally broadcast.
- [ ] All non-night public callbacks address one registered game bucket only.
- [ ] Serialized public night messages have exactly `type`, `phase`, `round_number`, and `substep`.
- [ ] No actor, target, action, seat highlight, role result, or future internal field crosses the public night boundary.
- [ ] Targeted and full backend tests have been run and outputs inspected.
- [ ] No REST, authentication, player-seat, frontend, or replay/log code changed.
