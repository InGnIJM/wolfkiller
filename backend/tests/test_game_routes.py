import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes import game_routes
from app.api.schemas import CreateGameRequest


def test_create_game_request_accepts_dynamic_role_counts():
    counts = {"wolf-killer-werewolf": 1, "wolf-killer-villager": 3}

    req = CreateGameRequest(role_counts=counts)

    assert req.role_counts == counts
    assert req.num_werewolves == 3


def test_create_game_request_rejects_dynamic_and_legacy_counts_together():
    with pytest.raises(ValueError, match="role_counts"):
        CreateGameRequest(
            role_counts={"wolf-killer-werewolf": 1},
            num_werewolves=1,
        )


def test_get_service_reads_main_game_service(monkeypatch):
    service = object()
    monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(game_service=service))

    assert game_routes.get_service() is service


@pytest.mark.asyncio
async def test_create_game_passes_dynamic_role_counts_and_returns_canonical_config(monkeypatch):
    counts = {"wolf-killer-werewolf": 1, "wolf-killer-villager": 3}
    service = MagicMock()
    service.create_game = AsyncMock(return_value="game-123")
    service.get_game_state.return_value = SimpleNamespace(
        players={1: object(), 2: object(), 3: object(), 4: object()},
        config=SimpleNamespace(role_counts=counts),
    )
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.create_game(CreateGameRequest(role_counts=counts))

    service.create_game.assert_awaited_once_with(role_counts=counts)
    assert response.config == {
        "role_counts": counts,
        "num_werewolves": 1,
        "num_villagers": 3,
        "num_seers": 0,
        "num_witches": 0,
        "num_hunters": 0,
    }
    assert response.player_count == 4


@pytest.mark.asyncio
async def test_create_game_keeps_legacy_request_and_returns_canonical_role_counts(monkeypatch):
    counts = {
        "wolf-killer-werewolf": 2,
        "wolf-killer-villager": 2,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 0,
        "wolf-killer-hunter": 0,
    }
    service = MagicMock()
    service.create_game = AsyncMock(return_value="game-456")
    service.get_game_state.return_value = SimpleNamespace(
        players={seat: object() for seat in range(1, 6)},
        config=SimpleNamespace(role_counts=counts),
    )
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.create_game(
        CreateGameRequest(num_werewolves=2, num_villagers=2, num_seers=1, num_witches=0, num_hunters=0)
    )

    service.create_game.assert_awaited_once_with(
        num_werewolves=2,
        num_villagers=2,
        num_seers=1,
        num_witches=0,
        num_hunters=0,
    )
    assert response.config["role_counts"] == counts


@pytest.mark.asyncio
async def test_create_game_returns_404_when_service_cannot_find_new_game(monkeypatch):
    service = MagicMock()
    service.create_game = AsyncMock(return_value="missing")
    service.get_game_state.return_value = None
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    with pytest.raises(HTTPException, match="after creation"):
        await game_routes.create_game(CreateGameRequest())


@pytest.mark.asyncio
async def test_list_games_omits_missing_states(monkeypatch):
    service = MagicMock()
    service.list_games.return_value = ["present", "missing"]
    service.get_game_state.side_effect = [
        SimpleNamespace(
            phase=SimpleNamespace(value="night"),
            round_number=2,
            alive_players=lambda: [object()],
            win_result={"winning_camp": "good"},
            players={1: object(), 2: object()},
        ),
        None,
    ]
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.list_games()

    assert response.games[0].game_id == "present"
    assert response.games[0].winner == "good"


@pytest.mark.asyncio
async def test_get_game_returns_404_when_missing(monkeypatch):
    service = MagicMock()
    service.get_game_state.return_value = None
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    with pytest.raises(HTTPException, match="Game not found"):
        await game_routes.get_game("missing")


@pytest.mark.asyncio
async def test_get_game_projects_only_public_state_without_reading_players(monkeypatch):
    class PublicOnlyState:
        def __init__(self):
            self.players_read = False

        @property
        def players(self):
            self.players_read = True
            raise AssertionError("observer route must not read private players")

        def get_public_state(self):
            return {
                "game_id": "present",
                "phase": "speech",
                "round_number": 3,
                "players": {
                    1: {"seat_number": 1, "is_alive": True, "is_sheriff": False},
                    2: {"seat_number": 2, "is_alive": False, "is_sheriff": True},
                },
                "sheriff": 2,
                "speeches": [{"player_seat": 1, "text": "public", "round_number": 3}],
                "death_history": [{"player_seat": 2, "cause": "wolf_kill", "round_number": 3}],
                "win_result": {"winning_camp": "good", "reason": "all_wolves_dead"},
            }

    state = PublicOnlyState()
    service = MagicMock()
    service.get_game_state.return_value = state
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.get_game("present")

    detail = response.model_dump()
    forbidden = {"role", "camp", "has_antidote", "has_poison", "has_gun", "check_results"}
    assert not (forbidden & _all_keys(detail))
    assert "wolf-killer-villager" not in repr(detail)
    assert not state.players_read
    assert detail["players"][1] == {"seat_number": 1, "is_alive": True, "is_sheriff": False}
    assert detail["win_result"] == {"winning_camp": "good", "reason": "all_wolves_dead"}


def test_read_jsonl_handles_missing_valid_and_invalid_records(tmp_path):
    path = tmp_path / "records.jsonl"
    assert game_routes._read_jsonl(str(path)) == []
    path.write_text('{"valid": true}\nnot-json\n\n', encoding="utf-8")

    assert game_routes._read_jsonl(str(path)) == [{"valid": True}]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({}, None),
        ({"timestamp": "invalid"}, None),
        ({"timestamp": "2026-01-01T00:00:00Z"}, pytest.approx(0, abs=1)),
    ],
)
def test_timestamp_accepts_only_iso_timestamps(record, expected):
    result = game_routes._timestamp(record)

    if expected is None:
        assert result is None
    else:
        assert result.isoformat() == "2026-01-01T00:00:00+00:00"


@pytest.mark.parametrize("value, expected", [(1, True), (True, False), ("1", False)])
def test_is_int_excludes_boolean(value, expected):
    assert game_routes._is_int(value) is expected


@pytest.mark.parametrize(
    "record",
    [
        {"timestamp": "2026-01-01T00:00:00Z", "scope": "werewolf"},
        {"timestamp": "2026-01-01T00:00:00Z", "scope": "public", "speaker_seat": 1,
         "content": "x", "round_number": 1},
    ],
)
def test_public_conversation_requires_all_public_speech_fields(record):
    assert game_routes._public_conversation_event(record) is None


@pytest.mark.parametrize(
    "record",
    [
        {"timestamp": "invalid", "operation": "vote", "round": 1, "phase": "vote_casting", "data": {}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": "1", "phase": "vote_casting", "data": {}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": 1, "phase": 1, "data": {}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": 1, "phase": "vote_casting", "data": []},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": 1, "phase": "night", "seat": 1, "data": {"target": 2}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": 1, "phase": "vote_casting", "seat": True, "data": {"target": 2}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": 1, "phase": "vote_casting", "seat": 1, "data": {"target": "2"}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "night_deaths", "round": 1, "phase": "night", "data": {"deaths": []}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "night_deaths", "round": 1, "phase": "dawn", "data": {"deaths": "bad"}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "phase_change", "round": 1, "phase": "speech", "data": {}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "game_over", "round": 1, "phase": "game_over", "data": {"winner": "good"}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "unknown", "round": 1, "phase": "x", "data": {}},
    ],
)
def test_public_operation_rejects_malformed_or_unknown_records(record):
    assert game_routes._public_operation_events(record) == []


@pytest.mark.parametrize(
    "deaths",
    [
        [{"player_seat": 1, "cause": "wolf_kill"}],
        [{"player_seat": 1, "cause": "wolf_kill", "round_number": 1, "role": "wolf"}],
        [{"player_seat": True, "cause": "wolf_kill", "round_number": 1}],
    ],
)
def test_public_death_projection_rejects_any_malformed_or_private_death(deaths):
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "night_deaths", "round": 1,
        "phase": "dawn", "data": {"deaths": deaths},
    }
    assert game_routes._public_operation_events(record) == []


def test_public_replay_ignores_non_mapping_records():
    assert game_routes._public_replay_events(["bad"], [None]) == []


def test_public_replay_skips_operation_without_valid_timestamp():
    operation = {
        "timestamp": "invalid", "operation": "phase_change", "round": 1,
        "phase": "speech", "data": {"new_phase": "speech"},
    }
    assert game_routes._public_replay_events([], [operation]) == []


def test_public_replay_sorts_naive_and_z_timestamps_as_utc_without_leaking_timestamps():
    conversations = [{
        "timestamp": "2026-01-01T00:00:02", "scope": "public", "speaker_seat": 1,
        "content": "naive speech", "round_number": 1, "phase": "speech",
    }]
    operations = [{
        "timestamp": "2026-01-01T00:00:01Z", "operation": "phase_change", "round": 1,
        "phase": "speech", "data": {"new_phase": "speech"},
    }]

    events = game_routes._public_replay_events(conversations, operations)

    assert events == [
        {"event_type": "phase", "payload": {"phase": "speech", "round_number": 1}},
        {"event_type": "speech", "payload": {"player_seat": 1, "text": "naive speech", "round_number": 1}},
    ]
    assert "timestamp" not in repr(events)


@pytest.mark.asyncio
async def test_get_game_logs_projects_only_closed_public_replay_events(monkeypatch):
    service = MagicMock()
    service.get_game_state.return_value = object()
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    monkeypatch.setattr(
        game_routes,
        "_read_jsonl",
        MagicMock(side_effect=[
            [
                {"timestamp": "2026-01-01T00:00:02Z", "scope": "public", "speaker_seat": 1,
                 "speaker_role": "wolf-killer-villager", "content": "day speech", "round_number": 1,
                 "phase": "speech"},
                {"timestamp": "2026-01-01T00:00:01Z", "scope": "werewolf", "speaker_seat": 2,
                 "speaker_role": "wolf-killer-werewolf", "content": "kill 1", "round_number": 1,
                 "phase": "night"},
                {"timestamp": "2026-01-01T00:00:03Z", "scope": "night_intel", "content": "seer result",
                 "round_number": 1, "phase": "night"},
                {"timestamp": "2026-01-01T00:00:04Z", "scope": "thought", "speaker_seat": 3,
                 "content": "private thought", "round_number": 1, "phase": "speech"},
                {"timestamp": "2026-01-01T00:00:05Z", "scope": "public", "content": "system without speaker",
                 "round_number": 1, "phase": "dawn"},
                {"timestamp": "not-a-timestamp", "scope": "public", "speaker_seat": 4,
                 "content": "bad timestamp", "round_number": 1, "phase": "speech"},
            ],
            [
                {"timestamp": "2026-01-01T00:00:04Z", "operation": "role_init", "round": 0,
                 "phase": "role_deal", "data": {"players": {"1": {"role": "wolf"}}}},
                {"timestamp": "2026-01-01T00:00:05Z", "operation": "werewolf_kill", "round": 1,
                 "phase": "night", "data": {"target": 1}},
                {"timestamp": "2026-01-01T00:00:06Z", "operation": "seer_check", "round": 1,
                 "phase": "night", "data": {"result": "werewolf"}},
                {"timestamp": "2026-01-01T00:00:07Z", "operation": "vote", "round": 1,
                 "phase": "vote_casting", "seat": 1, "data": {"target": 2, "reasoning": "private"}},
                {"timestamp": "2026-01-01T00:00:08Z", "operation": "night_deaths", "round": 1,
                 "phase": "dawn", "data": {"deaths": [{"player_seat": 2, "cause": "wolf_kill", "round_number": 1, "role": "werewolf"}]}},
                {"timestamp": "2026-01-01T00:00:08.500Z", "operation": "night_deaths", "round": 1,
                 "phase": "dawn", "data": {"deaths": [{"player_seat": 2, "cause": "wolf_kill", "round_number": 1}]}},
                {"timestamp": "2026-01-01T00:00:09Z", "operation": "phase_change", "round": 2,
                 "phase": "speech", "data": {"new_phase": "speech"}},
                {"timestamp": "2026-01-01T00:00:10Z", "operation": "game_over", "round": 2,
                 "phase": "game_over", "data": {"winner": "good", "reason": "all_wolves_dead", "roles": ["wolf"]}},
                {"timestamp": "2026-01-01T00:00:11Z", "operation": "unknown", "round": 2,
                 "phase": "game_over", "data": {"secret": "no"}},
                {"timestamp": "2026-01-01T00:00:12Z", "operation": "night_deaths", "round": 1,
                 "phase": "dawn", "data": {"deaths": [{"player_seat": "bad", "cause": "wolf_kill", "round_number": 1}]}},
            ],
        ]),
    )

    response = await game_routes.get_game_logs("present")

    assert response.model_dump()["events"] == [
        {"event_type": "speech", "payload": {"player_seat": 1, "text": "day speech", "round_number": 1}},
        {"event_type": "vote", "payload": {"voter_seat": 1, "target_seat": 2, "round_number": 1}},
        {"event_type": "death", "payload": {"player_seat": 2, "cause": "wolf_kill", "round_number": 1}},
        {"event_type": "phase", "payload": {"phase": "speech", "round_number": 2}},
        {"event_type": "winner", "payload": {"winning_camp": "good", "reason": "all_wolves_dead"}},
    ]
    raw = repr(response.model_dump())
    for secret in ("role_init", "werewolf", "seer result", "private thought", "reasoning", "roles"):
        assert secret not in raw


@pytest.mark.asyncio
async def test_get_game_logs_returns_404_without_reading_log_files(monkeypatch):
    service = MagicMock()
    service.get_game_state.return_value = None
    reader = MagicMock()
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    monkeypatch.setattr(game_routes, "_read_jsonl", reader)

    with pytest.raises(HTTPException, match="Game not found"):
        await game_routes.get_game_logs("missing")

    reader.assert_not_called()


def _all_keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value)) if value else set()
    return set()
