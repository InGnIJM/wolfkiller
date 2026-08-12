# Public Event Isolation Design

## Goal

Make the current unauthenticated observer WebSocket safe for concurrent games. Every public event must route only to its own `game_id`; night-progress messages must expose only public progression, never actors, targets, actions, or role-check results.

This is the first, deliberately narrow P0 subproject. It remains observer mode: connected clients are not authenticated as players and receive only public state.

## Confirmed decisions

- `game_id` is mandatory for every public event emitted by `GameEngine` and consumed by `GameService`.
- `GameService` drops events with a missing or unknown `game_id`; it must never fall back to broadcasting to every game.
- The existing `WSManager._connections[game_id]` bucket remains the transport routing boundary.
- The public `night_substep` payload has exactly `type`, `phase`, `round_number`, and `substep`. Its phase is always `night`.
- A public night event may not include `highlight_seats`, `action_seat`, `action`, `wolf_kill_target`, targets, actor seats, roles, or role/check results.
- REST detail/log/replay redaction, player-seat authentication, private role streams, and frontend work are intentionally outside this subproject.

## Current-state evidence

`WSManager` already stores connections by game id and sends `broadcast(game_id, ...)` only to that bucket. The failures are upstream:

1. `GameEngine` publishes `PLAYER_DIED`, `SPEECH_MADE`, `VOTE_CAST`, `GAME_OVER`, and `PHASE_CHANGED` without `game_id` in `backend/app/core/game_engine.py`.
2. The corresponding `GameService` callbacks iterate all `self._games` and globally broadcast in `backend/app/services/game_service.py`.
3. `NIGHT_SUBSTEP` already identifies its game but carries `highlight_seats`, `action_seat`, `action`, `wolf_kill_target`, and an embedded seer result; `GameService` forwards all of them.

Therefore concurrent games can observe each other's public events, and unauthenticated night observers can learn private actions before dawn.

## Options considered

### Filter only in `WSHandler`

This is a small diff, but global broadcast loops remain and future `WSManager` callers could bypass the filter. Rejected.

### Event identity envelope plus closed public-night DTO (chosen)

Require engine event identity, validate it in `GameService`, route only to that game bucket, and construct night messages through an allow-list serializer. This fixes both P0 causes without prematurely designing authentication.

### Authenticated per-seat streams now

This is the eventual player-view architecture, but requires product decisions on credentials, reconnection, REST authorization, and frontend behaviour. Deferred.

## Architecture

```text
GameEngine
  -- event(game_id, internal data) --> EventBus --> GameService
                                              validate known game_id
                                              project public night DTO
                                                        |
                                                        v
                                      WSManager.broadcast(game_id, payload)
                                                        |
                                                        v
                                  only _connections[game_id] clients
```

### Event identity contract

`GameEngine` owns `self.game_id` and adds it to every publish of `PHASE_CHANGED`, `PLAYER_DIED`, `SPEECH_MADE`, `VOTE_CAST`, `GAME_OVER`, and `NIGHT_SUBSTEP`. The latter already includes it.

`GameService` validates the envelope against `self._games`. A callback returns without persistence or broadcast when the id is absent or unknown; it must not infer an id from a payload object or iterate every game. For phase changes, the `state.game_id` must also equal envelope `game_id`; a mismatch is dropped before persistence.

### Closed public night DTO

Create `backend/app/api/websocket/public_events.py` with `PublicNightSubstep`. It accepts the internal event only to select values and outputs:

```python
{
    "phase": "night",
    "round_number": round_number,
    "substep": step,
}
```

`WSManager` continues to add the top-level `type: "night_substep"`. The serializer neither copies arbitrary keys nor offers a free-form output path, so newly added engine fields are non-public by default.

### Failure handling

Missing, unknown, and mismatched game identifiers are ignored and warning-logged with only event type and supplied id. Raw night-action data must not be logged. Invalid events must not terminate EventBus delivery and must never cause global broadcast.

## Acceptance criteria

1. With `game-a` and `game-b`, death, speech, vote, phase-change, and game-over events for `game-a` make exactly one broadcast addressed to `game-a`, never `game-b`.
2. Missing or unknown ids make no broadcast and do not mutate/persist another game.
3. Every listed engine public event carries its own id.
4. A private-rich night event serializes with exactly `{"type", "phase", "round_number", "substep"}`; it has no action, target, actor, role, or result field.
5. Targeted tests have complete changed-branch coverage and the complete backend suite stays green.

## Non-goals

- REST `/detail`, logs, replay, and file redaction.
- Authentication, seat ownership, player credentials, and wolf/private channels.
- Observer frontend changes.
- Restart persistence, rate limits, deployment, or unrelated refactoring.
