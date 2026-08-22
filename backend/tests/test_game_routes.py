import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes import game_routes
from app.api.schemas import CreateGameRequest, RenameGameRequest


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

    service.create_game.assert_awaited_once_with(role_counts=counts, model_assignments=None)
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
        model_assignments=None,
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
    service.get_display_name.side_effect = lambda gid: f"{gid}-name"

    response = await game_routes.list_games()

    assert response.games[0].game_id == "present"
    assert response.games[0].name == "present-name"
    assert response.games[0].winner == "good"


@pytest.mark.asyncio
async def test_rename_game_returns_updated_list_item(monkeypatch):
    state = SimpleNamespace(
        phase=SimpleNamespace(value="night"),
        round_number=1,
        alive_players=lambda: [object()],
        win_result=None,
        players={1: object()},
    )
    service = MagicMock()
    service.get_game_state.return_value = state
    service.get_display_name.return_value = "新名字"
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.rename_game("g1", RenameGameRequest(name="新名字"))

    service.rename_game.assert_called_once_with("g1", "新名字")
    assert response.name == "新名字"
    assert response.game_id == "g1"


@pytest.mark.asyncio
async def test_rename_game_returns_404_when_missing(monkeypatch):
    service = MagicMock()
    service.rename_game.side_effect = KeyError("g1")
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.rename_game("g1", RenameGameRequest(name="新名字"))
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_rename_game_returns_404_when_state_vanishes(monkeypatch):
    service = MagicMock()
    service.get_game_state.return_value = None
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.rename_game("g1", RenameGameRequest(name="新名字"))
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_game_returns_204(monkeypatch):
    service = MagicMock()
    service.delete_game = AsyncMock()
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    response = await game_routes.delete_game("g1")
    service.delete_game.assert_awaited_once_with("g1")
    assert response is None


@pytest.mark.asyncio
async def test_delete_game_returns_404_when_missing(monkeypatch):
    service = MagicMock()
    service.delete_game = AsyncMock(side_effect=KeyError("g1"))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.delete_game("g1")
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_game_returns_500_when_archive_busy(monkeypatch):
    service = MagicMock()
    service.delete_game = AsyncMock(side_effect=OSError("busy"))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.delete_game("g1")
    assert caught.value.status_code == 500


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
                    1: {"seat_number": 1, "is_alive": True, "is_sheriff": False,
                        "role": "wolf-killer-villager", "camp": "good"},
                    2: {"seat_number": 2, "is_alive": False, "is_sheriff": True,
                        "role": "wolf-killer-werewolf", "camp": "werewolf"},
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
    forbidden = {"check_results", "has_antidote", "has_poison", "has_gun"}
    assert not (forbidden & _all_keys(detail))
    assert not state.players_read
    assert "votes" not in detail
    assert detail["players"][1] == {
        "seat_number": 1, "is_alive": True, "is_sheriff": False,
        "role": "wolf-killer-villager", "camp": "good",
    }
    assert detail["win_result"] == {"winning_camp": "good", "reason": "all_wolves_dead"}


@pytest.mark.asyncio
async def test_get_game_accepts_initial_real_game_state_without_private_fields(monkeypatch):
    from app.models.game import GameState

    state = GameState(game_id="initial")
    service = MagicMock()
    service.get_game_state.return_value = state
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.get_game("initial")

    assert response.round_number == 0
    serialized = response.model_dump()
    assert serialized["phase"] == "waiting"
    assert not {"check_results", "has_antidote", "has_poison", "has_gun"} & _all_keys(serialized)


def test_read_jsonl_handles_missing_valid_and_invalid_records(tmp_path):
    path = tmp_path / "records.jsonl"
    assert game_routes._read_jsonl(str(path)) == []
    path.write_text('{"valid": true}\nnot-json\n\n', encoding="utf-8")

    assert game_routes._read_jsonl(str(path)) == [{"valid": True}]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({}, None),
        ({"timestamp": 123}, None),
        ({"timestamp": "invalid"}, None),
        ({"timestamp": "2026-01-01T99:00:00"}, None),
        ({"timestamp": "2026-01-01"}, None),
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
    ("record", "expected"),
    [
        (
            {"timestamp": "2026-01-01T00:00:00Z", "scope": "public", "speaker_seat": 1,
             "content": "initial speech", "round_number": 0, "phase": "waiting"},
            {"event_type": "speech", "payload": {
                "player_seat": 1, "text": "initial speech", "round_number": 0,
                "phase": "waiting",
            }},
        ),
        (
            {"timestamp": "2026-01-01T00:00:00Z", "operation": "phase_change", "round": 0,
             "phase": "waiting", "data": {"new_phase": "waiting"}},
            [{"event_type": "phase", "payload": {
                "phase": "waiting", "round_number": 0,
            }}],
        ),
    ],
)
def test_public_projection_accepts_initial_round_zero(record, expected):
    projector = (
        game_routes._public_conversation_event
        if "scope" in record
        else game_routes._public_operation_events
    )

    assert projector(record) == expected


@pytest.mark.parametrize("invalid_round", [True, "0", -1])
def test_public_projection_rejects_non_strict_or_negative_initial_round(invalid_round):
    conversation = {
        "timestamp": "2026-01-01T00:00:00Z", "scope": "public", "speaker_seat": 1,
        "content": "bad round", "round_number": invalid_round, "phase": "waiting",
    }
    operation = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "phase_change", "round": invalid_round,
        "phase": "waiting", "data": {"new_phase": "waiting"},
    }

    assert game_routes._public_conversation_event(conversation) is None
    assert game_routes._public_operation_events(operation) == []


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


@pytest.mark.parametrize(
    "record",
    [
        {
            "timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": 1,
            "phase": "vote_casting", "seat": -1, "data": {"target": 2},
        },
        {
            "timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": 1,
            "phase": "vote_casting", "seat": 1, "data": {"target": 0},
        },
        {
            "timestamp": "2026-01-01T00:00:00Z", "operation": "phase_change", "round": 1,
            "phase": "speech", "data": {"new_phase": "secret: seer checked seat 2"},
        },
        {
            "timestamp": "2026-01-01T00:00:00Z", "operation": "game_over", "round": 1,
            "phase": "game_over", "data": {"winner": "good", "reason": "secret: antidote used"},
        },
        {
            "timestamp": "2026-01-01T00:00:00Z", "operation": "night_deaths", "round": 1,
            "phase": "dawn", "data": {"deaths": [
                {"player_seat": 2, "cause": "secret: player 2 was seer", "round_number": 1},
            ]},
        },
    ],
)
def test_public_operation_projection_skips_invalid_public_domain_values(record):
    assert game_routes._public_operation_events(record) == []


@pytest.mark.parametrize(
    "conversation, operation",
    [
        (
            {"timestamp": "2026-01-01T00:00:00Z", "scope": "public", "speaker_seat": 1,
             "content": "bad phase", "round_number": 1, "phase": malformed},
            None,
        )
        for malformed in ([], {})
    ] + [
        (None, {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote", "round": 1,
                "phase": malformed, "data": {"target": 2}, "seat": 1})
        for malformed in ([], {})
    ] + [
        (None, {"timestamp": "2026-01-01T00:00:00Z", "operation": "phase_change", "round": 1,
                "phase": "speech", "data": {"new_phase": malformed}})
        for malformed in ([], {})
    ] + [
        (None, {"timestamp": "2026-01-01T00:00:00Z", "operation": "night_deaths", "round": 1,
                "phase": "dawn", "data": {"deaths": [
                    {"player_seat": 2, "cause": malformed, "round_number": 1},
                ]}})
        for malformed in ([], {})
    ] + [
        (None, {"timestamp": "2026-01-01T00:00:00Z", "operation": "game_over", "round": 1,
                "phase": "game_over", "data": {"winner": malformed, "reason": "all_wolves_dead"}})
        for malformed in ([], {})
    ] + [
        (None, {"timestamp": "2026-01-01T00:00:00Z", "operation": "game_over", "round": 1,
                "phase": "game_over", "data": {"winner": "good", "reason": malformed}})
        for malformed in ([], {})
    ],
)
def test_public_replay_skips_unhashable_domain_values_and_keeps_later_events(
    conversation, operation,
):
    valid_operation = {
        "timestamp": "2026-01-01T00:00:01Z", "operation": "phase_change", "round": 1,
        "phase": "speech", "data": {"new_phase": "speech"},
    }

    events = game_routes._public_replay_events(
        [conversation] if conversation is not None else [],
        [operation, valid_operation] if operation is not None else [valid_operation],
    )

    assert events == [{
        "event_type": "phase",
        "timestamp": "2026-01-01T00:00:01Z",
        "payload": {"phase": "speech", "round_number": 1},
    }]


def test_public_replay_ignores_non_mapping_records():
    assert game_routes._public_replay_events(["bad"], [None]) == []


def test_public_replay_skips_operation_without_valid_timestamp():
    operation = {
        "timestamp": "invalid", "operation": "phase_change", "round": 1,
        "phase": "speech", "data": {"new_phase": "speech"},
    }
    assert game_routes._public_replay_events([], [operation]) == []


def test_public_replay_skips_date_only_timestamp_without_changing_other_event_status():
    operations = [
        {
            "timestamp": "2026-01-01", "operation": "phase_change", "round": 1,
            "phase": "speech", "data": {"new_phase": "speech"},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z", "operation": "phase_change", "round": 1,
            "phase": "speech", "data": {"new_phase": "speech"},
        },
    ]

    assert game_routes._public_replay_events([], operations) == [{
        "event_type": "phase",
        "timestamp": "2026-01-01T00:00:01Z",
        "payload": {"phase": "speech", "round_number": 1},
    }]


def test_public_vote_result_projection_omits_tally_and_private_fields():
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "vote_result", "round": 2,
        "phase": "vote_resolution",
        "data": {"exiled": 3, "tally": {3: 4, 1: 2}, "reasoning": "private"},
    }

    assert game_routes._public_operation_events(record) == [{
        "event_type": "vote_result",
        "payload": {"round_number": 2, "exiled_seat": 3},
    }]


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"exiled": 0},
        {"exiled": -1},
        {"exiled": True},
        {"exiled": "3"},
    ],
)
def test_public_vote_result_rejects_invalid_exile_data(data):
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "vote_result", "round": 2,
        "phase": "vote_resolution", "data": data,
    }

    assert game_routes._public_operation_events(record) == []


def test_public_audience_action_projects_each_supported_event_shape():
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night",
        "data": {"event_type": "WITCH_SAVE", "payload": {"target_seat": 3}},
    }

    assert game_routes._public_operation_events(record) == [{
        "event_type": "night_action",
        "payload": {"action_type": "witch_save", "target_seat": 3, "round_number": 2},
    }]

    werewolf = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night",
        "data": {"event_type": "WEREWOLF_KILL",
                 "payload": {"target_seat": 4, "vote_counts": {"4": 2, "1": 1}}},
    }
    assert game_routes._public_operation_events(werewolf) == [{
        "event_type": "night_action",
        "payload": {
            "action_type": "werewolf_kill", "target_seat": 4, "round_number": 2,
            "vote_counts": {"4": 2, "1": 1},
        },
    }]

    seer = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night",
        "data": {"event_type": "SEER_CHECK",
                 "payload": {"target_seat": 1, "result": "werewolf"}},
    }
    assert game_routes._public_operation_events(seer) == [{
        "event_type": "night_action",
        "payload": {
            "action_type": "seer_check", "target_seat": 1, "round_number": 2,
            "result": "werewolf",
        },
    }]

    guard = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night",
        "data": {"event_type": "GUARD_PROTECT", "payload": {"target_seat": 6}},
    }
    assert game_routes._public_operation_events(guard) == [{
        "event_type": "night_action",
        "payload": {"action_type": "guard_protect", "target_seat": 6, "round_number": 2},
    }]


@pytest.mark.parametrize(
    ("record",),
    [
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "UNKNOWN_ACTION",
          "payload": {"target_seat": 1}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "WITCH_SAVE",
          "payload": {"target_seat": 1, "extra": True}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "WITCH_SAVE",
          "payload": {"target_seat": 0}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "WITCH_SAVE",
          "payload": {"target_seat": "3"}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "WEREWOLF_KILL",
          "payload": {"target_seat": 1}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "WEREWOLF_KILL",
          "payload": {"target_seat": 1, "vote_counts": [1]}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "WEREWOLF_KILL",
          "payload": {"target_seat": 1, "vote_counts": {"abc": 1}}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "WEREWOLF_KILL",
          "payload": {"target_seat": 1, "vote_counts": {"2": 0}}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "SEER_CHECK",
          "payload": {"target_seat": 1}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "SEER_CHECK",
          "payload": {"target_seat": 1, "result": "third_party"}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": {"event_type": "SEER_CHECK",
          "payload": {"target_seat": 1, "result": 7}}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night", "data": "not-a-dict"},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night",
          "data": {"event_type": "WITCH_SAVE", "payload": "not-a-dict"}},),
        ({"timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
          "round": 2, "phase": "night",
          "data": {"event_type": 9, "payload": {"target_seat": 1}}},),
    ],
)
def test_public_audience_action_rejects_malformed_records(record):
    assert game_routes._public_operation_events(record) == []


def test_public_audience_action_helper_rejects_non_dict_data():
    assert game_routes._public_audience_action_event({"data": "not-a-dict"}, 2) == []


def test_narration_and_staged_night_events_project_to_public_events():
    records = [
        {"timestamp": "2026-08-14T00:00:01Z", "round": 1, "phase": "night",
         "operation": "narration", "seat": None,
         "data": {"title": "天黑请闭眼", "text": "狼人请睁眼，开始讨论今晚的行动。"}},
        {"timestamp": "2026-08-14T00:00:02Z", "round": 1, "phase": "night",
         "operation": "audience_action", "seat": None,
         "data": {"event_type": "WOLF_CHAT_MESSAGE", "payload": {"seat": 1, "text": "我怀疑2号"}}},
        {"timestamp": "2026-08-14T00:00:03Z", "round": 1, "phase": "night",
         "operation": "audience_action", "seat": None,
         "data": {"event_type": "WOLF_VOTE", "payload": {"seat": 1, "target_seat": 2, "reasoning": "像神"}}},
        {"timestamp": "2026-08-14T00:00:04Z", "round": 1, "phase": "night",
         "operation": "audience_action", "seat": None,
         "data": {"event_type": "WITCH_THOUGHT", "payload": {"seat": 3, "text": "考虑救人"}}},
        {"timestamp": "2026-08-14T00:00:05Z", "round": 1, "phase": "night",
         "operation": "audience_action", "seat": None,
         "data": {"event_type": "SEER_THOUGHT", "payload": {"seat": 4, "text": "查验2号"}}},
    ]
    events = game_routes._public_replay_events([], records)
    assert [e["event_type"] for e in events] == [
        "narration", "wolf_chat_message", "wolf_vote", "witch_thought", "seer_thought"]
    narration = events[0]["payload"]
    assert narration == {"round_number": 1, "title": "天黑请闭眼", "text": "狼人请睁眼，开始讨论今晚的行动。"}
    assert events[1]["payload"] == {"round_number": 1, "seat": 1, "text": "我怀疑2号"}
    assert events[2]["payload"] == {"round_number": 1, "seat": 1, "target_seat": 2, "reasoning": "像神"}


@pytest.mark.parametrize(
    "data",
    [
        {"title": 7, "text": "t"},
        {"title": "t", "text": 7},
        {"title": "", "text": "t"},
        {"title": "t", "text": ""},
        {"title": "t" * 101, "text": "t"},
        {"title": "t", "text": "t" * 201},
    ],
)
def test_public_narration_rejects_malformed_records(data):
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "narration", "round": 1,
        "phase": "night", "data": data,
    }

    assert game_routes._public_operation_events(record) == []


def test_public_reasoning_event_projects_closed_night_thought():
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night",
        "data": {"event_type": "HUNTER_REASONING",
                 "payload": {"seat": 7, "action_type": "shoot", "target_seat": 1,
                             "reasoning": "开枪带走跳狼的人", "thought": "决定开枪带走 1 号玩家：开枪带走跳狼的人"}},
    }

    assert game_routes._public_operation_events(record) == [{
        "event_type": "night_thought",
        "payload": {
            "round_number": 2, "seat": 7, "action_type": "hunter_reasoning",
            "target_seat": 1, "reasoning": "开枪带走跳狼的人",
        },
    }]

    passed = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night",
        "data": {"event_type": "HUNTER_REASONING",
                 "payload": {"seat": 3, "action_type": "pass", "target_seat": None,
                             "reasoning": "没有把握不开枪", "thought": "决定不开枪：没有把握不开枪"}},
    }
    assert game_routes._public_operation_events(passed) == [{
        "event_type": "night_thought",
        "payload": {
            "round_number": 2, "seat": 3, "action_type": "hunter_reasoning",
            "target_seat": None, "reasoning": "没有把握不开枪",
        },
    }]


@pytest.mark.parametrize(
    ("event_type", "action", "target", "public_type"),
    [
        ("WITCH_REASONING", "save", 4, "witch_reasoning"),
        ("WITCH_REASONING", "poison", 2, "witch_reasoning"),
        ("WITCH_REASONING", "pass", None, "witch_reasoning"),
        ("SEER_REASONING", "check", 5, "seer_reasoning"),
        ("SEER_REASONING", "pass", None, "seer_reasoning"),
        ("GUARD_REASONING", "guard", 6, "guard_reasoning"),
        ("GUARD_REASONING", "pass", None, "guard_reasoning"),
    ],
)
def test_public_reasoning_event_projects_witch_and_seer_thoughts(
    event_type, action, target, public_type,
):
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night",
        "data": {"event_type": event_type,
                 "payload": {"seat": 8, "action_type": action, "target_seat": target,
                             "reasoning": "理由", "thought": "思考"}},
    }

    assert game_routes._public_operation_events(record) == [{
        "event_type": "night_thought",
        "payload": {
            "round_number": 2, "seat": 8, "action_type": public_type,
            "target_seat": target, "reasoning": "理由",
        },
    }]


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("HUNTER_REASONING", "not-a-dict"),
        ("HUNTER_REASONING", {"seat": 1, "action_type": "shoot", "target_seat": 2,
                              "reasoning": "r", "thought": "t", "extra": 1}),
        ("HUNTER_REASONING", {"seat": 0, "action_type": "shoot", "target_seat": 2,
                              "reasoning": "r", "thought": "t"}),
        ("HUNTER_REASONING", {"seat": True, "action_type": "shoot", "target_seat": 2,
                              "reasoning": "r", "thought": "t"}),
        ("HUNTER_REASONING", {"seat": 1, "action_type": 7, "target_seat": 2,
                              "reasoning": "r", "thought": "t"}),
        ("HUNTER_REASONING", {"seat": 1, "action_type": "save", "target_seat": 2,
                              "reasoning": "r", "thought": "t"}),
        ("HUNTER_REASONING", {"seat": 1, "action_type": "shoot", "target_seat": "x",
                              "reasoning": "r", "thought": "t"}),
        ("HUNTER_REASONING", {"seat": 1, "action_type": "shoot", "target_seat": True,
                              "reasoning": "r", "thought": "t"}),
        ("HUNTER_REASONING", {"seat": 1, "action_type": "shoot", "target_seat": 2,
                              "reasoning": "r" * 501, "thought": "t"}),
        ("HUNTER_REASONING", {"seat": 1, "action_type": "shoot", "target_seat": 2,
                              "reasoning": "r", "thought": 7}),
        ("WITCH_REASONING", {"seat": 1, "action_type": "shoot", "target_seat": 2,
                             "reasoning": "r", "thought": "t"}),
        ("WITCH_REASONING", {"seat": 1, "action_type": "save", "target_seat": 2,
                             "reasoning": "r", "thought": "t", "extra": 1}),
        ("SEER_REASONING", {"seat": 1, "action_type": "shoot", "target_seat": 2,
                            "reasoning": "r", "thought": "t"}),
        ("SEER_REASONING", {"seat": 0, "action_type": "check", "target_seat": 2,
                            "reasoning": "r", "thought": "t"}),
        ("GUARD_REASONING", {"seat": 1, "action_type": "shoot", "target_seat": 2,
                             "reasoning": "r", "thought": "t"}),
        ("GUARD_REASONING", {"seat": 1, "action_type": "guard", "target_seat": 2,
                             "reasoning": "r", "thought": "t", "extra": 1}),
        ("GUARD_PROTECT", {"target_seat": 2, "extra": 1}),
        ("GUARD_PROTECT", {"target_seat": "x"}),
    ],
)
def test_public_reasoning_event_rejects_malformed_records(event_type, payload):
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night", "data": {"event_type": event_type, "payload": payload},
    }

    assert game_routes._public_operation_events(record) == []


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("WOLF_CHAT_MESSAGE", "not-a-dict"),
        ("WOLF_CHAT_MESSAGE", {"seat": 0, "text": "t"}),
        ("WOLF_CHAT_MESSAGE", {"seat": True, "text": "t"}),
        ("WOLF_CHAT_MESSAGE", {"seat": 1, "text": "t", "extra": True}),
        ("WITCH_THOUGHT", {"seat": 1, "text": 7}),
        ("WITCH_THOUGHT", {"seat": 1, "text": "t", "extra": True}),
        ("SEER_THOUGHT", {"seat": 1, "text": ""}),
        ("SEER_THOUGHT", {"seat": 1, "text": "t" * 201}),
        ("SEER_THOUGHT", {"seat": 1, "text": "t", "extra": True}),
        ("WOLF_VOTE", "not-a-dict"),
        ("WOLF_VOTE", {"seat": 0, "target_seat": 2, "reasoning": "r"}),
        ("WOLF_VOTE", {"seat": 1, "target_seat": 0, "reasoning": "r"}),
        ("WOLF_VOTE", {"seat": 1, "target_seat": 2, "reasoning": "r" * 501}),
        ("WOLF_VOTE", {"seat": 1, "target_seat": None, "reasoning": 7}),
        ("WOLF_VOTE", {"seat": 1, "target_seat": 2, "reasoning": "r", "extra": True}),
    ],
)
def test_public_staged_night_audience_event_rejects_malformed_records(event_type, payload):
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "night", "data": {"event_type": event_type, "payload": payload},
    }

    assert game_routes._public_operation_events(record) == []


def test_public_replay_sorts_naive_and_z_timestamps_as_utc_and_projects_timestamps():
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
        {"event_type": "phase", "timestamp": "2026-01-01T00:00:01Z",
         "payload": {"phase": "speech", "round_number": 1}},
        {"event_type": "speech", "timestamp": "2026-01-01T00:00:02Z",
         "payload": {"player_seat": 1, "text": "naive speech", "round_number": 1,
                     "phase": "speech"}},
    ]


def test_public_replay_projects_canonical_utc_timestamps_for_all_event_types():
    conversations = [{
        "timestamp": "2026-01-01T08:00:00+08:00", "scope": "public", "speaker_seat": 1,
        "content": "speech", "round_number": 1, "phase": "speech",
    }]
    operations = [
        {"timestamp": "2026-01-01T00:00:01", "operation": "vote", "round": 1,
         "phase": "vote_casting", "seat": 1, "data": {"target": 2}},
        {"timestamp": "2026-01-01T00:00:02.5Z", "operation": "vote_result", "round": 1,
         "phase": "vote_resolution", "data": {"exiled": 2}},
        {"timestamp": "2026-01-01T01:00:03+01:00", "operation": "night_deaths", "round": 1,
         "phase": "dawn", "data": {"deaths": [
             {"player_seat": 2, "cause": "exile", "round_number": 1},
         ]}},
        {"timestamp": "2026-01-01T00:00:04Z", "operation": "phase_change", "round": 2,
         "phase": "speech", "data": {"new_phase": "speech"}},
        {"timestamp": "2026-01-01T00:00:05.123456Z", "operation": "game_over", "round": 2,
         "phase": "game_over", "data": {"winner": "good", "reason": "all_wolves_dead"}},
    ]

    events = game_routes._public_replay_events(conversations, operations)

    assert [event["event_type"] for event in events] == [
        "speech", "vote", "vote_result", "death", "phase", "winner",
    ]
    assert [event["timestamp"] for event in events] == [
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:01Z",
        "2026-01-01T00:00:02.500000Z",
        "2026-01-01T00:00:03Z",
        "2026-01-01T00:00:04Z",
        "2026-01-01T00:00:05.123456Z",
    ]


def test_public_replay_keeps_stable_order_and_shares_timestamp_for_expanded_deaths():
    conversations = [{
        "timestamp": "2026-01-01T00:00:00Z", "scope": "public", "speaker_seat": 1,
        "content": "first", "round_number": 1, "phase": "speech",
    }]
    operations = [
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "night_deaths", "round": 1,
         "phase": "dawn", "data": {"deaths": [
             {"player_seat": 2, "cause": "wolf_kill", "round_number": 1},
             {"player_seat": 3, "cause": "poison", "round_number": 1},
         ]}},
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "phase_change", "round": 1,
         "phase": "dawn", "data": {"new_phase": "dawn"}},
    ]

    events = game_routes._public_replay_events(conversations, operations)

    assert [(event["event_type"], event["payload"].get("player_seat")) for event in events] == [
        ("speech", 1), ("death", 2), ("death", 3), ("phase", None),
    ]
    assert {event["timestamp"] for event in events} == {"2026-01-01T00:00:00Z"}


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
                {"timestamp": "2026-01-01T00:00:11.500Z", "operation": "vote_result", "round": 2,
                 "phase": "vote_resolution", "data": {"exiled": None, "tally": {1: 2}, "secret": "no"}},
                {"timestamp": "2026-01-01T00:00:12Z", "operation": "night_deaths", "round": 1,
                 "phase": "dawn", "data": {"deaths": [{"player_seat": "bad", "cause": "wolf_kill", "round_number": 1}]}},
            ],
        ]),
    )

    response = await game_routes.get_game_logs("present")

    assert response.model_dump()["events"] == [
        {"event_type": "speech", "timestamp": "2026-01-01T00:00:02Z",
         "payload": {"player_seat": 1, "text": "day speech", "round_number": 1,
                     "phase": "speech"}},
        {"event_type": "vote", "timestamp": "2026-01-01T00:00:07Z",
         "payload": {"voter_seat": 1, "target_seat": 2, "round_number": 1}},
        {"event_type": "death", "timestamp": "2026-01-01T00:00:08.500000Z",
         "payload": {"player_seat": 2, "cause": "wolf_kill", "round_number": 1}},
        {"event_type": "phase", "timestamp": "2026-01-01T00:00:09Z",
         "payload": {"phase": "speech", "round_number": 2}},
        {"event_type": "winner", "timestamp": "2026-01-01T00:00:10Z",
         "payload": {"winning_camp": "good", "reason": "all_wolves_dead"}},
        {"event_type": "vote_result", "timestamp": "2026-01-01T00:00:11.500000Z",
         "payload": {"round_number": 2, "exiled_seat": None}},
    ]
    raw = repr(response.model_dump())
    for secret in ("role_init", "werewolf", "seer result", "private thought", "reasoning", "roles", "tally"):
        assert secret not in raw


@pytest.mark.asyncio
async def test_get_game_logs_serializes_staged_night_events_end_to_end(monkeypatch, tmp_path):
    game_dir = tmp_path / "data" / "games" / "staged-night"
    game_dir.mkdir(parents=True)
    (game_dir / "conversation.log").write_text("", encoding="utf-8")
    records = [
        {"timestamp": "2026-08-14T00:00:01Z", "round": 1, "phase": "night",
         "operation": "narration", "seat": None,
         "data": {"title": "天黑请闭眼", "text": "狼人请睁眼，开始讨论今晚的行动。"}},
        {"timestamp": "2026-08-14T00:00:02Z", "round": 1, "phase": "night",
         "operation": "audience_action", "seat": None,
         "data": {"event_type": "WOLF_CHAT_MESSAGE",
                  "payload": {"seat": 1, "text": "我怀疑2号"}}},
        {"timestamp": "2026-08-14T00:00:03Z", "round": 1, "phase": "night",
         "operation": "audience_action", "seat": None,
         "data": {"event_type": "WOLF_VOTE",
                  "payload": {"seat": 1, "target_seat": 2, "reasoning": "像神"}}},
        {"timestamp": "2026-08-14T00:00:04Z", "round": 1, "phase": "night",
         "operation": "audience_action", "seat": None,
         "data": {"event_type": "WITCH_THOUGHT",
                  "payload": {"seat": 3, "text": "考虑救人"}}},
        {"timestamp": "2026-08-14T00:00:05Z", "round": 1, "phase": "night",
         "operation": "audience_action", "seat": None,
         "data": {"event_type": "SEER_THOUGHT",
                  "payload": {"seat": 4, "text": "查验2号"}}},
        {"timestamp": "2026-08-14T00:00:06Z", "round": 1, "phase": "dawn",
         "operation": "night_deaths", "seat": None,
         "data": {"deaths": [{"player_seat": 2, "cause": "wolf_kill", "round_number": 1}]}},
    ]
    (game_dir / "game.log").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    service = MagicMock()
    service.get_game_state.return_value = object()
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    monkeypatch.chdir(tmp_path)

    response = await game_routes.get_game_logs("staged-night")

    events = response.model_dump()["events"]
    assert [event["event_type"] for event in events] == [
        "narration", "wolf_chat_message", "wolf_vote", "witch_thought", "seer_thought", "death",
    ]
    assert events[0]["payload"] == {
        "round_number": 1, "title": "天黑请闭眼", "text": "狼人请睁眼，开始讨论今晚的行动。",
    }
    assert events[1]["payload"] == {"round_number": 1, "seat": 1, "text": "我怀疑2号"}
    assert events[2]["payload"] == {"round_number": 1, "seat": 1, "target_seat": 2, "reasoning": "像神"}
    assert events[3]["payload"] == {"round_number": 1, "seat": 3, "text": "考虑救人"}
    assert events[4]["payload"] == {"round_number": 1, "seat": 4, "text": "查验2号"}


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


@pytest.mark.asyncio
async def test_get_game_memories_returns_404_without_state(monkeypatch):
    service = MagicMock()
    service.get_game_state.return_value = None
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    with pytest.raises(HTTPException, match="Game not found"):
        await game_routes.get_game_memories("missing")


def test_camp_label_normalizes_str_enum_and_rejects_unknown():
    from app.models.game import Camp

    assert game_routes._camp_label(Camp.WEREWOLF) == "werewolf"
    assert game_routes._camp_label("good") == "good"
    assert game_routes._camp_label(7) == ""


@pytest.mark.asyncio
async def test_get_game_memories_projects_persisted_and_placeholder_entries(monkeypatch):
    from app.models.game import GameState, PlayerState

    state = GameState("mem-game", players={
        1: PlayerState(1, "wolf-killer-witch", "good"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf", is_alive=False),
    })
    service = MagicMock()
    service.get_game_state.return_value = state
    memory_service = MagicMock()
    memory_service.load_memory = MagicMock(side_effect=lambda game_id, seat: {
        "game_id": game_id,
        "seat_number": seat,
        "role": "wolf-killer-witch",
        "camp": "good",
        "is_alive": True,
        "private_knowledge": {"has_antidote": True, "has_poison": False},
        "action_history": [{"round": 1, "phase": "night", "action": {}}],
        "witnessed_events": [],
        "last_updated": "2026-01-01T00:00:00Z",
    } if seat == 1 else None)
    service.memory_service = memory_service
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.get_game_memories("mem-game")

    assert response.model_dump() == {
        "game_id": "mem-game",
        "memories": [
            {
                "seat_number": 1,
                "role": "wolf-killer-witch",
                "camp": "good",
                "is_alive": True,
                "private_knowledge": {"has_antidote": True, "has_poison": False},
                "action_history": [{"round": 1, "phase": "night", "action": {}}],
                "witnessed_events": [],
                "last_updated": "2026-01-01T00:00:00Z",
            },
            {
                "seat_number": 2,
                "role": "wolf-killer-werewolf",
                "camp": "werewolf",
                "is_alive": False,
                "private_knowledge": {},
                "action_history": [],
                "witnessed_events": [],
                "last_updated": "",
            },
        ],
    }


@pytest.mark.asyncio
async def test_get_game_memories_without_memory_service_returns_placeholders(monkeypatch):
    from app.models.game import GameState, PlayerState

    state = GameState("mem-none", players={
        1: PlayerState(1, "wolf-killer-villager", "good"),
    })
    service = MagicMock()
    service.get_game_state.return_value = state
    service.memory_service = None
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.get_game_memories("mem-none")

    assert response.model_dump()["memories"] == [{
        "seat_number": 1,
        "role": "wolf-killer-villager",
        "camp": "good",
        "is_alive": True,
        "private_knowledge": {},
        "action_history": [],
        "witnessed_events": [],
        "last_updated": "",
    }]


def _all_keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value)) if value else set()
    return set()
