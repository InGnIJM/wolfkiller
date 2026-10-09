import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from typing import get_args

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import game_routes
from app.services import audience_projector
from app.api.schemas import (
    CreateGameRequest, GameLogsResponse, PublicDeathCause, PublicGamePhase,
    PublicWinReason, RenameGameRequest,
)
from app.persistence.repository import GameReferencedByBenchmark


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


def test_get_audience_service_reads_main_service(monkeypatch):
    service = object()
    monkeypatch.setitem(
        sys.modules, "app.main", SimpleNamespace(audience_event_service=service),
    )

    assert game_routes.get_audience_service() is service


@pytest.mark.asyncio
async def test_game_control_returns_execution_metadata(monkeypatch):
    service = MagicMock()
    service.pause_game = AsyncMock(return_value={
        "execution_status": "paused",
        "recoverable": True,
        "recovery_block_code": None,
        "interruption_count": 2,
        "benchmark_run_id": None,
    })
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.pause_game("game-1")

    service.pause_game.assert_awaited_once_with("game-1")
    assert response.model_dump() == {
        "game_id": "game-1",
        "execution_status": "paused",
        "recoverable": True,
        "recovery_block_code": None,
        "interruption_count": 2,
        "benchmark_run_id": None,
    }


@pytest.mark.asyncio
async def test_game_control_maps_transition_and_not_found_errors(monkeypatch):
    from app.persistence.repository import InvalidExecutionTransition

    service = MagicMock()
    service.resume_game = AsyncMock(side_effect=InvalidExecutionTransition("completed"))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.resume_game("game-1")
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "invalid_execution_transition"

    service.resume_game = AsyncMock(side_effect=KeyError("missing"))
    with pytest.raises(HTTPException) as caught:
        await game_routes.resume_game("missing")
    assert caught.value.status_code == 404
    assert caught.value.detail["code"] == "game_not_found"


@pytest.mark.asyncio
async def test_snapshot_and_event_page_use_audience_service(monkeypatch):
    audience = MagicMock()
    audience.get_snapshot.return_value = {
        "game_id": "game-1", "schema_version": 1,
        "projection_version": 1, "last_seq": 1,
        "state": {"phase": "night"},
    }
    audience.get_events.return_value = {
        "game_id": "game-1",
        "events": [{
            "game_id": "game-1", "seq": 1, "event_id": "event-1",
            "schema_version": 1, "event_type": "phase",
            "timestamp": "2026-09-06T00:00:00+00:00",
            "payload": {"phase": "night", "round_number": 1},
        }],
        "next_seq": 1, "high_watermark": 1, "has_more": False,
    }
    monkeypatch.setattr(game_routes, "get_audience_service", lambda: audience)

    service = MagicMock()
    service.get_game_model_snapshot.return_value = []
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    snapshot = await game_routes.get_game_snapshot("game-1")
    page = await game_routes.get_game_events(
        "game-1", after_seq=0, limit=200, through_seq=1,
    )

    assert snapshot.last_seq == 1
    assert snapshot.state.get("model_snapshot") == []
    assert page.events[0].event_id == "event-1"
    audience.get_events.assert_called_once_with(
        "game-1", after_seq=0, limit=200, through_seq=1,
    )


@pytest.mark.asyncio
async def test_event_page_maps_cursor_ahead_to_stable_conflict(monkeypatch):
    from app.services.audience_event_service import AudienceCursorAheadError

    audience = MagicMock()
    audience.get_events.side_effect = AudienceCursorAheadError("game-1", 5, 3)
    monkeypatch.setattr(game_routes, "get_audience_service", lambda: audience)

    with pytest.raises(HTTPException) as caught:
        await game_routes.get_game_events("game-1", after_seq=5, limit=200)

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "code": "cursor_ahead",
        "message": "after_seq 5 is ahead of high watermark 3 for game game-1",
        "after_seq": 5,
        "high_watermark": 3,
    }


@pytest.mark.asyncio
async def test_create_game_passes_dynamic_role_counts_and_returns_canonical_config(monkeypatch):
    counts = {"wolf-killer-werewolf": 1, "wolf-killer-villager": 3}
    service = MagicMock()
    service.create_game = AsyncMock(return_value="game-123")
    service.get_game_state.return_value = SimpleNamespace(
        players={1: object(), 2: object(), 3: object(), 4: object()},
        config=SimpleNamespace(role_counts=counts, reveal_on_death=False, enable_sheriff=False),
    )
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.create_game(CreateGameRequest(role_counts=counts))

    service.create_game.assert_awaited_once_with(
        role_counts=counts, reveal_on_death=False, enable_sheriff=False,
        witch_self_save="first_night_only", model_assignments=None,
    )
    assert response.config == {
        "role_counts": counts,
        "reveal_on_death": False,
        "enable_sheriff": False,
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
        config=SimpleNamespace(role_counts=counts, reveal_on_death=False, enable_sheriff=False),
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
        reveal_on_death=False,
        enable_sheriff=False,
        witch_self_save="first_night_only",
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
async def test_list_games_skips_benchmark_owned_games(monkeypatch):
    service = MagicMock()
    service.list_games.return_value = ["native", "bench"]
    service.is_lobby_game.side_effect = lambda gid: gid == "native"
    state = SimpleNamespace(
        phase=SimpleNamespace(value="night"),
        round_number=1,
        alive_players=lambda: [object()],
        win_result=None,
        players={1: object()},
    )
    service.get_game_state.return_value = state
    service.get_display_name.return_value = "普通局"
    service.get_game_folder_id.return_value = "folder-1"
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.list_games()

    assert [item.game_id for item in response.games] == ["native"]
    assert response.games[0].folder_id == "folder-1"
    assert response.total == 1
    assert response.page == 1
    assert response.page_size == 10


@pytest.mark.asyncio
async def test_list_games_pagination_and_folder_filtering(monkeypatch):
    service = MagicMock()
    service.list_games.return_value = ["g1", "g2", "g3"]
    service.is_lobby_game.return_value = True
    state = SimpleNamespace(
        phase=SimpleNamespace(value="night"),
        round_number=1,
        alive_players=lambda: [object()],
        win_result=None,
        players={1: object()},
    )
    service.get_game_state.return_value = state
    service.get_display_name.side_effect = lambda gid: f"{gid}-name"
    folders = {"g1": "f1", "g2": None, "g3": "f2"}
    service.get_game_folder_id.side_effect = lambda gid: folders.get(gid)
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    # Test pagination slicing
    resp_slice = await game_routes.list_games(page=2, page_size=1)
    assert resp_slice.total == 3
    assert resp_slice.page == 2
    assert resp_slice.page_size == 1
    assert [g.game_id for g in resp_slice.games] == ["g2"]

    # Test folder_id="all"
    resp_all = await game_routes.list_games(folder_id="all")
    assert resp_all.total == 3
    assert len(resp_all.games) == 3

    # Test folder_id="unfiled"
    resp_unfiled = await game_routes.list_games(folder_id="unfiled")
    assert resp_unfiled.total == 1
    assert [g.game_id for g in resp_unfiled.games] == ["g2"]

    # Test folder_id="f1"
    resp_f1 = await game_routes.list_games(folder_id="f1")
    assert resp_f1.total == 1
    assert [g.game_id for g in resp_f1.games] == ["g1"]

    # Test folder_id="f_nonexistent"
    resp_none = await game_routes.list_games(folder_id="f_nonexistent")
    assert resp_none.total == 0
    assert resp_none.games == []


@pytest.mark.asyncio
async def test_list_games_with_unregistered_folder_getter(monkeypatch):
    state = SimpleNamespace(
        phase=SimpleNamespace(value="night"),
        round_number=1,
        alive_players=lambda: [object()],
        win_result=None,
        players={1: object()},
    )
    service = SimpleNamespace(
        list_games=lambda: ["g1"],
        get_game_state=lambda _gid: state,
        get_display_name=lambda _gid: "普通局",
        is_lobby_game="non_callable",
        get_game_folder_id="non_callable",
    )
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    resp = await game_routes.list_games(folder_id="unfiled")
    assert resp.total == 1
    assert resp.games[0].game_id == "g1"


def test_list_item_skips_missing_folder_lookup():
    state = SimpleNamespace(
        phase=SimpleNamespace(value="night"),
        round_number=1,
        players={1: object()},
        alive_players=lambda: [1],
        win_result=None,
    )
    service = SimpleNamespace(get_display_name=lambda _gid: "普通局")
    item = game_routes._list_item(service, "g1", state)
    assert item.folder_id is None
    assert item.name == "普通局"


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
async def test_delete_game_rejects_benchmark_owned_game_with_stable_code(monkeypatch):
    service = MagicMock()
    service.delete_game = AsyncMock(
        side_effect=GameReferencedByBenchmark("benchmark-1"),
    )
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    with pytest.raises(HTTPException) as caught:
        await game_routes.delete_game("g1")

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "code": "game_referenced_by_benchmark",
        "benchmark_run_id": "benchmark-1",
    }


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
@pytest.mark.parametrize("reveal_on_death", [True, False])
async def test_get_game_returns_public_reveal_on_death(monkeypatch, reveal_on_death):
    class PublicState:
        def get_public_state(self):
            return {
                "game_id": "present",
                "phase": "waiting",
                "round_number": 0,
                "reveal_on_death": reveal_on_death,
                "players": {},
                "sheriff": None,
                "speeches": [],
                "death_history": [],
                "win_result": None,
            }

    service = MagicMock()
    service.get_game_state.return_value = PublicState()
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.get_game("present")

    assert response.model_dump()["reveal_on_death"] is reveal_on_death
    assert response.model_snapshot == []


_V2_MODEL_SNAPSHOT = [{
    "config_id": "model-a",
    "name": "Model A",
    "model_id": "provider/model-a",
    "base_url": "https://models.example/v1",
    "provider_profile": "openrouter",
    "count": 2,
    "seats": [1, 3],
    "api_key": "sk-should-not-leak",
}]


def _public_game_state(game_id="present"):
    return {
        "game_id": game_id,
        "phase": "waiting",
        "round_number": 0,
        "reveal_on_death": False,
        "players": {},
        "sheriff": None,
        "speeches": [],
        "death_history": [],
        "win_result": None,
    }


@pytest.mark.asyncio
async def test_get_game_returns_validated_v2_model_snapshot(monkeypatch):
    class PublicState:
        def get_public_state(self):
            return _public_game_state()

    service = MagicMock()
    service.get_game_state.return_value = PublicState()
    service.get_game_model_snapshot.return_value = _V2_MODEL_SNAPSHOT
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.get_game("present")

    assert [entry.model_dump() for entry in response.model_snapshot] == [{
        "config_id": "model-a",
        "name": "Model A",
        "model_id": "provider/model-a",
        "base_url": "https://models.example/v1",
        "provider_profile": "openrouter",
        "count": 2,
        "seats": [1, 3],
    }]
    assert "api_key" not in response.model_dump()["model_snapshot"][0]


@pytest.mark.asyncio
async def test_get_game_drops_legacy_model_snapshot_rows(monkeypatch):
    class PublicState:
        def get_public_state(self):
            return _public_game_state()

    service = MagicMock()
    service.get_game_state.return_value = PublicState()
    service.get_game_model_snapshot.return_value = [{"name": "legacy", "model_id": "x"}]
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.get_game("present")

    assert response.model_snapshot == []


@pytest.mark.asyncio
async def test_get_game_snapshot_injects_public_model_snapshot(monkeypatch):
    audience = MagicMock()
    audience.get_snapshot.return_value = {
        "game_id": "game-1", "schema_version": 1,
        "projection_version": 1, "last_seq": 1,
        "state": {"phase": "night"},
    }
    service = MagicMock()
    service.get_game_model_snapshot.return_value = _V2_MODEL_SNAPSHOT
    monkeypatch.setattr(game_routes, "get_audience_service", lambda: audience)
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    snapshot = await game_routes.get_game_snapshot("game-1")

    assert snapshot.state["phase"] == "night"
    assert snapshot.state["model_snapshot"] == [{
        "config_id": "model-a",
        "name": "Model A",
        "model_id": "provider/model-a",
        "base_url": "https://models.example/v1",
        "provider_profile": "openrouter",
        "count": 2,
        "seats": [1, 3],
    }]


@pytest.mark.asyncio
async def test_get_game_snapshot_injects_live_execution_info(monkeypatch):
    audience = MagicMock()
    audience.get_snapshot.return_value = {
        "game_id": "game-1", "schema_version": 1,
        "projection_version": 1, "last_seq": 1,
        "state": {
            "phase": "night",
            "execution_status": "running",
            "recoverable": False,
            "recovery_block_code": None,
        },
    }
    service = MagicMock()
    service.get_game_model_snapshot.return_value = []
    service.get_execution_info.return_value = {
        "execution_status": "interrupted",
        "recoverable": True,
        "recovery_block_code": None,
        "interruption_count": 1,
        "benchmark_run_id": None,
    }
    monkeypatch.setattr(game_routes, "get_audience_service", lambda: audience)
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    snapshot = await game_routes.get_game_snapshot("game-1")

    assert snapshot.state["execution_status"] == "interrupted"
    assert snapshot.state["recoverable"] is True
    assert snapshot.state["recovery_block_code"] is None


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
                "reveal_on_death": False,
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
    assert detail["model_snapshot"] == []


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
    assert serialized["reveal_on_death"] is False
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
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "game_over", "round": 1, "phase": "game_over", "data": {"winner": "third_party"}},
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


@pytest.mark.parametrize(
    "cause",
    ["wolf_kill", "poison", "hunter_shot", "exile", "self_explode",
     "knight_duel", "charm", "wolf_king_shot", "reflect", "dream_death"],
)
def test_public_replay_keeps_every_death_cause_the_engine_produces(cause):
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "night_deaths",
        "round": 2, "phase": "dawn",
        "data": {"deaths": [{"player_seat": 3, "cause": cause, "round_number": 2}]},
    }

    assert game_routes._public_operation_events(record) == [{
        "event_type": "death",
        "payload": {"player_seat": 3, "cause": cause, "round_number": 2},
    }]


def test_public_replay_keeps_the_outnumber_win_reason():
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "game_over",
        "round": 4, "phase": "game_over",
        "data": {"winner": "werewolf", "reason": "wolves_outnumber_good"},
    }

    assert game_routes._public_operation_events(record) == [{
        "event_type": "winner",
        "payload": {"winning_camp": "werewolf", "reason": "wolves_outnumber_good"},
    }]


def test_old_archive_whitelists_match_the_public_dto() -> None:
    """The route's filters are the DTO's second hand-written copy.

    They were free to drift apart -- nothing tied them together -- and a value
    missing from the filter drops the whole record from the replay while a value
    missing from the DTO fails the response. Binding both to the same literal is
    what makes a future addition update both.
    """
    assert game_routes._PUBLIC_DEATH_CAUSES == set(get_args(PublicDeathCause))
    assert game_routes._PUBLIC_WIN_REASONS == set(get_args(PublicWinReason))
    assert game_routes._PUBLIC_GAME_PHASES == set(get_args(PublicGamePhase))


@pytest.fixture(autouse=True)
def _clear_replay_drop_counters():
    """丢弃计数是模块级的观测指标，逐条测试前后清空以免互相干扰。"""
    game_routes._DROPPED_REPLAY_ROWS.clear()
    yield
    game_routes._DROPPED_REPLAY_ROWS.clear()


def _replay_record(operation: str, data: dict, *, phase: str = "dawn", round_number: int = 1) -> dict:
    return {
        "timestamp": "2026-01-01T00:00:00Z", "operation": operation,
        "round": round_number, "phase": phase, "data": data,
    }


def test_undeclared_death_cause_drops_only_that_row(caplog) -> None:
    """一条未声明死因曾丢掉整批死亡事件。

    实测：`[poison, charm]` 两死只出 0 条事件 —— 循环里的 `return []` 把同批普通的死亡
    一起带走了，而且没有任何日志，所以只能在界面上「少了一条」时才被发现。
    """
    record = _replay_record("night_deaths", {"deaths": [
        {"player_seat": 3, "cause": "wolf_kill", "round_number": 1},
        {"player_seat": 4, "cause": "love_death", "round_number": 1},
        {"player_seat": 5, "cause": "charm", "round_number": 1},
    ]})

    with caplog.at_level(logging.WARNING):
        events = game_routes._public_operation_events(record)

    assert [event["payload"]["player_seat"] for event in events] == [3, 5]
    assert game_routes._DROPPED_REPLAY_ROWS["night_deaths:undeclared_cause"] == 1
    assert "love_death" in caplog.text


def test_absent_win_reason_is_carried_without_a_reason() -> None:
    """表里没有胜因的旧档不该丢掉整条结算事件。

    清单只记赢的阵营，胜因只在日志的 `game_over` 记录里；公开 DTO 已把 `reason` 设为
    可选，「未知」用缺省表示，而不是丢掉这条事件或编一个值。缺键与显式 `None` 都是缺省。
    """
    for data in ({"winner": "werewolf", "reason": None}, {"winner": "werewolf"}):
        record = _replay_record("game_over", data, phase="game_over")

        assert game_routes._public_operation_events(record) == [
            {"event_type": "winner", "payload": {"winning_camp": "werewolf"}},
        ]
        assert not game_routes._DROPPED_REPLAY_ROWS


def test_undeclared_win_reason_is_reported(caplog) -> None:
    record = _replay_record(
        "game_over", {"winner": "werewolf", "reason": "third_party_wins"},
        phase="game_over",
    )

    with caplog.at_level(logging.WARNING):
        events = game_routes._public_operation_events(record)

    assert events == []
    assert game_routes._DROPPED_REPLAY_ROWS["game_over:undeclared_reason"] == 1
    assert "third_party_wins" in caplog.text


#: 引擎会写进 game.log、但投影器**不**声明为公开的 audience_action 类型。
#: 它们只是引擎内部的编排标记（白天中断、放逐前的待定宣告），旧档回放必须**静默跳过**：
#: 按「未知类型」计数会让 `EXILE_PENDING`（每次放逐都写）把计数刷成噪音。
_INTERNAL_AUDIENCE_ACTION_TYPES = frozenset({"DAY_INTERRUPTED", "EXILE_PENDING"})

#: 投影器声明为公开、但旧档回放**不由** audience_action 记录产出的类型：它们分别来自
#: 会话记录（speech）、`night_deaths`（death）与 `narration` / `phase_change` /
#: `vote` / `vote_result` / `vote_technical_abstain` / `game_over` 等操作记录。
_REPLAY_FROM_OTHER_RECORDS = frozenset({
    "SPEECH_MADE", "PLAYER_DIED", "VOTE_CAST", "VOTE_RESULT", "GAME_OVER",
    "TECHNICAL_ABSTAIN", "NARRATION", "PHASE_CHANGED",
})

#: 观众流会发、旧档日志里**没有**对应来源的类型 —— 已知的路径差异，不是遗漏：
#: `SPEAKING` 表示「该座位开始生成发言」，`GAME_INITIALIZED` / `EXECUTION_STATE` 由
#: 领域事件直接进 audience 表，`NIGHT_ACTION` 是投影器聚合夜间动作用的条目（回放路径
#: 直接按 6 个角色动作类型产出 `night_action`，它们是 `_EVENTS` 的独立键）。
_NO_REPLAY_MECHANISM = frozenset({
    "SPEAKING", "GAME_INITIALIZED", "EXECUTION_STATE", "NIGHT_ACTION",
})

#: 日志里这 4 个类型的 payload 形状取自真实 `game.log`（`tests` 之外无第二来源）。
_LOG_PAYLOAD_OVERRIDES: dict[str, dict] = {
    "KNIGHT_DUEL": {"seat": 1, "target_seat": 2, "camp": "good", "round_number": 1},
    "WOLF_BEAUTY_CHARM": {"seat": 1, "target_seat": 2, "round_number": 1},
    "WOLF_BEAUTY_REVENGE": {"seat": 1, "target_seat": 2, "cause": "exile", "round_number": 1},
    "STATUS_ADDED": {"seat": 1, "status": "poisoned", "round_number": 1},
    "CRIMSON_EXPLODE": {
        "seat": 1, "silenced_seats": [2, 3], "round_number": 1,
    },
    "DANCE_POOL": {"seat": 1, "pool_seats": [2, 3, 4], "round_number": 1},
    "MASK_GRANTED": {
        "seat": 1, "target_seat": 2, "in_pool": True, "round_number": 1,
    },
}

_DEFAULT_VALUES: dict[str, object] = {
    "seat": 1, "player_seat": 1, "voter_seat": 2, "target_seat": 2, "from_seat": 1,
    "to_seat": 2, "round_number": 1, "exiled_seat": 2, "seat_number": 2,
    "text": "发言内容", "reasoning": "推理内容", "thought": "思考内容",
    "status": "poisoned", "camp": "good", "result": "good", "cause": "exile",
    "choice": "run", "kind": "vote", "side": "sheriff_left", "reason": "vote",
    "winning_camp": "good", "failure_code": "timeout", "phase": "speech",
    "action_type": "pass", "role": "wolf-killer-villager",
}

#: 取值受限的字段按类型改写（`run` 只对 SHERIFF_RUN 合法）。
_FIELD_OVERRIDES: dict[str, dict[str, object]] = {
    "SHERIFF_WITHDRAW": {"choice": "stay"},
}
_REASONING_ACTIONS = {
    "WITCH_REASONING": "save", "SEER_REASONING": "check", "GUARD_REASONING": "guard",
    "KNIGHT_REASONING": "duel", "WEREWOLF_KING_REASONING": "explode",
    "WOLF_BEAUTY_REASONING": "charm", "HUNTER_REASONING": "shoot",
    "WOLF_KING_REASONING": "shoot", "DREAM_WEAVER_REASONING": "dream", "GARGOYLE_REASONING": "check",
    "PURE_WHITE_REASONING": "check", "WOLF_WITCH_REASONING": "check",
    "DEMON_HUNTER_REASONING": "hunt", "CRIMSON_ENVOY_REASONING": "explode",
    "NIGHTMARE_REASONING": "fear", "DANCER_REASONING": "pool", "MASK_REASONING": "mask",
}
_VOTE_COUNT_FIELDS = frozenset({"vote_counts", "counts"})


def _log_payload(event_type: str, allowed: frozenset[str]) -> dict:
    """造一份与真实日志同形的 payload（形状以路由/日志为准，而不是投影器的过滤集）。"""
    if event_type in _LOG_PAYLOAD_OVERRIDES:
        return dict(_LOG_PAYLOAD_OVERRIDES[event_type])
    if event_type in audience_projector._PUBLIC_REASONING_EVENTS:
        return {
            "seat": 1, "action_type": _REASONING_ACTIONS[event_type],
            "target_seat": 2, "reasoning": "推理内容", "thought": "思考内容",
        }
    required = game_routes._AUDIENCE_ACTION_SCHEMAS.get(event_type)
    fields = required if required else (allowed - {"round_number"})
    overrides = _FIELD_OVERRIDES.get(event_type, {})
    payload: dict[str, object] = {}
    for field in fields:
        if field in _VOTE_COUNT_FIELDS:
            payload[field] = {"2": 1}
        elif field in overrides:
            payload[field] = overrides[field]
        elif field in _DEFAULT_VALUES:
            payload[field] = _DEFAULT_VALUES[field]
    return payload


def _projector_declared_events() -> dict[str, tuple[str, frozenset[str]]]:
    return dict(audience_projector._EVENTS)


def test_crimson_explode_replay_requires_a_usable_sealed_list() -> None:
    """赤月使徒自爆的旧档回放：座位必须是正整数，封印名单必须是座位数组。"""
    base = {"event_type": "CRIMSON_EXPLODE", "payload": {
        "seat": 1, "silenced_seats": [2, 3], "round_number": 4,
    }}
    events = game_routes._public_operation_events(
        _replay_record("audience_action", base, phase="speech", round_number=4)
    )
    # The record's own round wins, exactly like every other replay row.
    assert events == [{
        "event_type": "crimson_explode",
        "payload": {"round_number": 4, "seat": 1, "silenced_seats": [2, 3]},
    }]
    for payload in (
        {"seat": 0, "silenced_seats": [2], "round_number": 4},
        {"seat": 1, "silenced_seats": "2,3", "round_number": 4},
        {"seat": 1, "silenced_seats": [0], "round_number": 4},
        {"seat": 1, "silenced_seats": [2], "round_number": 4, "extra": 1},
    ):
        assert game_routes._public_operation_events(
            _replay_record("audience_action", {
                "event_type": "CRIMSON_EXPLODE", "payload": payload,
            }, phase="speech")
        ) == [], payload


def test_masquerade_replay_branches_validate_their_payloads() -> None:
    """舞池与面具的旧档回放：座位必须是正整数，in_pool 必须是布尔。"""
    def replay(payload):
        return game_routes._public_operation_events(
            _replay_record("audience_action", {
                "event_type": "DANCE_POOL" if "pool_seats" in payload else "MASK_GRANTED",
                "payload": payload,
            }, phase="night", round_number=3)
        )

    assert replay({"seat": 1, "pool_seats": [2, 3, 4], "round_number": 3}) == [{
        "event_type": "dance_pool",
        "payload": {"round_number": 3, "seat": 1, "pool_seats": [2, 3, 4]},
    }]
    assert replay({
        "seat": 1, "target_seat": 2, "in_pool": False, "round_number": 3,
    }) == [{
        "event_type": "mask_granted",
        "payload": {"round_number": 3, "seat": 1, "target_seat": 2, "in_pool": False},
    }]
    for payload in (
        {"seat": 1, "pool_seats": [], "round_number": 3},
        {"seat": 1, "pool_seats": [0], "round_number": 3},
        {"seat": 0, "pool_seats": [2], "round_number": 3},
        {"seat": 1, "target_seat": 2, "in_pool": "yes", "round_number": 3},
        {"seat": 1, "target_seat": 0, "in_pool": True, "round_number": 3},
    ):
        assert replay(payload) == [], payload


def test_every_public_audience_action_type_has_a_replay_branch() -> None:
    """投影器声明为公开的每个类型，旧档回放路径都必须能产出事件。

    这条门禁的由来：`KNIGHT_DUEL` / `WOLF_BEAUTY_CHARM` / `WOLF_BEAUTY_REVENGE` /
    `KNIGHT_REASONING` / `WOLF_BEAUTY_REASONING` / `STATUS_ADDED` 六个类型后端能发、
    投影器声明公开、前端也早已渲染，唯独回放白名单没收录 —— 旧档回退路径上它们直接
    消失，而没有任何测试会红。以后再加一个公开类型却忘了补回放分支，这里就会失败。
    """
    declared = _projector_declared_events()
    classified = _REPLAY_FROM_OTHER_RECORDS | _NO_REPLAY_MECHANISM
    # 归类表要干净：不能出现投影器没声明的类型（改名后忘了同步这里）。
    assert classified <= set(declared)

    missing: list[str] = []
    for event_type, (public_type, allowed) in sorted(declared.items()):
        if event_type in classified:
            continue
        record = _replay_record("audience_action", {
            "event_type": event_type, "payload": _log_payload(event_type, allowed),
        }, phase="speech")
        events = game_routes._public_operation_events(record)
        if len(events) != 1 or events[0]["event_type"] != public_type:
            missing.append(f"{event_type} -> {public_type}: 产出 {events!r}")

    assert missing == []


#: 可选字段的样本值。门禁必须把它们**一起**发出去：只发必填字段的那版曾经全绿，
#: 而 `degraded` 恰恰是被公开 DTO 拒收的那个键。
_OPTIONAL_KEY_SAMPLES: dict[str, object] = {
    "degraded": True,
    "day_plan": "明天我跳预言家",
    "reasoning": "推理内容",
    "weighted_counts": {"2": 2},
}

#: `_public_operation_events` 只产出 `{event_type, payload}`，时间戳由
#: `_public_replay_events` 统一补；直接构造回放 DTO 时要自己带上它。
_PUBLIC_TIMESTAMP = "2026-08-12T12:34:56Z"


def test_every_projected_public_payload_is_accepted_by_the_replay_dto() -> None:
    """投影器放行的键，公开回放 DTO 必须全部接受 —— **含可选键**。

    这条门禁的由来（两类真实的 500，都是同一个病因）：

    * `degraded` 进了投影器白名单与回放分支，却没进 `api/schemas.py` 的
      `PublicNightThoughtResponse` / `PublicWolfVoteResponse`，而 `_PublicResponse`
      是 `extra="forbid"` —— 含降级动作的对局一打开回放就 500；
    * `pool_seats` / `silenced_seats` 的回放分支发 list，DTO 却声明 tuple +
      `strict=True`，实测 **13/253 局**的 `/api/games/{id}/logs` 直接 500。

    判别力来自「必填 + 可选各发一份」：只发必填版本时这条门禁是全绿的，因为可选键
    根本不在样本里。产出必须恰好一条，否则「行被静默丢弃」会被读成通过。
    """
    classified = _REPLAY_FROM_OTHER_RECORDS | _NO_REPLAY_MECHANISM
    rejected: list[str] = []
    missing: list[str] = []

    for event_type, (public_type, allowed) in sorted(_projector_declared_events().items()):
        if event_type in classified:
            continue
        optional = game_routes._AUDIENCE_ACTION_OPTIONAL.get(event_type, frozenset())
        payload = _log_payload(event_type, allowed)
        payload.update({
            key: _OPTIONAL_KEY_SAMPLES[key]
            for key in optional if key in _OPTIONAL_KEY_SAMPLES
        })
        record = _replay_record("audience_action", {
            "event_type": event_type, "payload": payload,
        }, phase="speech")
        events = game_routes._public_operation_events(record)
        if len(events) != 1 or events[0]["event_type"] != public_type:
            missing.append(f"{event_type} -> {public_type}: 产出 {events!r}")
            continue
        try:
            GameLogsResponse(game_id="g", events=[
                {**events[0], "timestamp": _PUBLIC_TIMESTAMP},
            ])
        except ValidationError as error:
            detail = " ".join(str(error).split())
            rejected.append(f"{event_type} -> {public_type}: {detail}")

    assert missing == []
    assert rejected == []


def test_wolf_chat_carries_the_day_plan_and_accepts_records_without_it() -> None:
    """狼聊的「次日计划」要进旧档回放；**已有旧记录没有该字段，不能被丢掉**。

    计划目前只存在于观众流（投影器的 schema 已含 `day_plan`）与狼队频道的会话记录里，
    而旧档回放只投影 public 会话，所以旧档看不到计划。补字段时如果把字段集写死成
    「必须有 day_plan」，所有历史日志的狼聊会从「少一个字段」变成「整条丢弃」。
    """
    with_plan = _replay_record("audience_action", {
        "event_type": "WOLF_CHAT_MESSAGE",
        "payload": {"seat": 1, "text": "今晚刀 4 号", "day_plan": "明天我跳预言家"},
    }, phase="night")
    without_plan = _replay_record("audience_action", {
        "event_type": "WOLF_CHAT_MESSAGE",
        "payload": {"seat": 1, "text": "今晚刀 4 号"},
    }, phase="night")

    events = game_routes._public_operation_events(with_plan)
    assert events == [{"event_type": "wolf_chat_message", "payload": {
        "round_number": 1, "seat": 1, "text": "今晚刀 4 号", "day_plan": "明天我跳预言家",
    }}]

    legacy = game_routes._public_operation_events(without_plan)
    assert legacy == [{"event_type": "wolf_chat_message", "payload": {
        "round_number": 1, "seat": 1, "text": "今晚刀 4 号",
    }}]
    assert not game_routes._DROPPED_REPLAY_ROWS


def test_sheriff_badge_carries_the_reason_and_accepts_records_without_it() -> None:
    """警长的交/撕徽理由要进旧档回放；**已有旧记录没有该字段，不能被丢掉**。

    理由只在观众流（投影器的 schema 已含 `reasoning`）与 `game.log` 里，而旧档回放
    走的是后者的字段集判定。把它写进必填集，本仓库里所有早于它的警徽记录都会从
    「少一个字段」变成「整条丢弃」——那正是当年 `day_plan` 踩过的坑。
    """
    with_reason = _replay_record("audience_action", {
        "event_type": "SHERIFF_BADGE",
        "payload": {"from_seat": 1, "to_seat": None, "round_number": 3,
                    "reasoning": "9号是金水，警徽本应传给他"},
    }, phase="night")
    without_reason = _replay_record("audience_action", {
        "event_type": "SHERIFF_BADGE",
        "payload": {"from_seat": 1, "to_seat": None, "round_number": 3},
    }, phase="night")

    events = game_routes._public_operation_events(with_reason)
    assert events == [{"event_type": "sheriff_badge", "payload": {
        "round_number": 1, "from_seat": 1, "to_seat": None,
        "reasoning": "9号是金水，警徽本应传给他",
    }}]

    legacy = game_routes._public_operation_events(without_reason)
    assert legacy == [{"event_type": "sheriff_badge", "payload": {
        "round_number": 1, "from_seat": 1, "to_seat": None,
    }}]
    assert not game_routes._DROPPED_REPLAY_ROWS


@pytest.mark.parametrize(("event_type", "payload", "code"), [
    ("KNIGHT_DUEL", {"seat": 0, "target_seat": 2, "camp": "good", "round_number": 1}, "invalid_duel"),
    ("KNIGHT_DUEL", {"seat": 1, "target_seat": 2, "camp": "third_party", "round_number": 1}, "invalid_duel"),
    ("WOLF_BEAUTY_CHARM", {"seat": 0, "target_seat": 2, "round_number": 1}, "invalid_charm"),
    ("WOLF_BEAUTY_CHARM", {"seat": 1, "target_seat": "2", "round_number": 1}, "invalid_charm_target"),
    ("WOLF_BEAUTY_REVENGE", {"seat": 1, "target_seat": 0, "cause": "exile", "round_number": 1}, "invalid_revenge"),
    ("WOLF_BEAUTY_REVENGE", {"seat": 1, "target_seat": 2, "cause": "love_death", "round_number": 1}, "invalid_revenge"),
    ("STATUS_ADDED", {"seat": 0, "status": "poisoned", "round_number": 1}, "invalid_status"),
    ("STATUS_ADDED", {"seat": 1, "status": "", "round_number": 1}, "invalid_status"),
    ("STATUS_ADDED", {"seat": 1, "status": 3, "round_number": 1}, "invalid_status"),
    ("WOLF_CHAT_MESSAGE", "不是一个对象", "malformed_payload"),
    # 字段集不符（缺 camp）走的是另一条分支。
    ("KNIGHT_DUEL", {"seat": 1, "target_seat": 2, "round_number": 1}, "malformed_payload"),
])
def test_role_skill_events_drop_only_the_bad_record(event_type, payload, code, caplog) -> None:
    """字段集合法但取值非法的记录只丢自己那一条，并留下计数与告警。"""
    record = _replay_record("audience_action", {
        "event_type": event_type, "payload": payload,
    }, phase="speech")

    with caplog.at_level(logging.WARNING):
        events = game_routes._public_operation_events(record)

    assert events == []
    assert game_routes._DROPPED_REPLAY_ROWS[f"audience_action:{code}"] == 1
    assert event_type in caplog.text


def test_internal_audience_action_types_are_skipped_silently() -> None:
    """引擎内部的记录不是「未知类型」，跳过时不该计数。

    `EXILE_PENDING` 每次放逐都会写一条，若按未知类型计数，计数会被它刷满。
    """
    record = _replay_record("audience_action", {
        "event_type": "EXILE_PENDING",
        "payload": {"target_seat": 2, "cause": "exile", "round_number": 1},
    }, phase="vote_resolution")

    assert game_routes._public_operation_events(record) == []
    assert not game_routes._DROPPED_REPLAY_ROWS


def test_unsupported_audience_event_type_is_reported(caplog) -> None:
    """白名单里真的没有的事件类型，必须留下痕迹（计数 + 日志）。

    曾经的现实例子是 `KNIGHT_DUEL` / `WOLF_BEAUTY_CHARM` 等六个类型：投影器声明公开、
    前端早已渲染，回放白名单却没有它们，于是旧档回退路径静默丢弃、没有任何痕迹说明
    「这条为什么不显示」。那六个已补齐（见 `test_every_public_audience_action_type_has_a_replay_branch`），
    这里用一个真正未知的类型守住计数与告警这条兜底。
    """
    record = _replay_record("audience_action", {
        "event_type": "NOT_A_REAL_TYPE",
        "payload": {"seat": 3, "target_seat": 5, "round_number": 1},
    }, phase="speech")

    with caplog.at_level(logging.WARNING):
        events = game_routes._public_operation_events(record)

    assert events == []
    assert game_routes._DROPPED_REPLAY_ROWS["audience_action:unsupported_event_type"] == 1
    assert "NOT_A_REAL_TYPE" in caplog.text


def test_game_logs_response_accepts_a_technical_abstain_record():
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "vote_technical_abstain",
        "round": 1, "phase": "vote_casting", "seat": 8,
        "data": {"failure_code": "request_timeout"},
    }

    response = GameLogsResponse(
        game_id="g", events=game_routes._public_replay_events([], [record]),
    )

    assert [event.event_type for event in response.events] == ["technical_abstain"]


def test_game_logs_response_accepts_an_error_phase_record():
    record = {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "phase_change",
        "round": 3, "phase": "error", "data": {"new_phase": "error"},
    }

    response = GameLogsResponse(
        game_id="g", events=game_routes._public_replay_events([], [record]),
    )

    assert [event.event_type for event in response.events] == ["phase"]


def test_public_technical_abstain_projection_hides_private_diagnostics():
    record = {
        "timestamp": "2026-01-01T00:00:00Z",
        "operation": "vote_technical_abstain",
        "round": 1,
        "phase": "vote_casting",
        "seat": 8,
        "data": {
            "failure_code": "request_timeout",
            "timeout_type": "local_deadline",
            "window_id": "g:1:vote_casting:1:cast_vote:v2",
        },
    }

    assert game_routes._public_operation_events(record) == [{
        "event_type": "technical_abstain",
        "payload": {
            "voter_seat": 8,
            "round_number": 1,
            "failure_code": "request_timeout",
        },
    }]


@pytest.mark.parametrize(
    "record",
    [
        # wrong phase
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote_technical_abstain",
         "round": 1, "phase": "speech", "seat": 8,
         "data": {"failure_code": "request_timeout"}},
        # missing seat
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote_technical_abstain",
         "round": 1, "phase": "vote_casting",
         "data": {"failure_code": "request_timeout"}},
        # missing failure code
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote_technical_abstain",
         "round": 1, "phase": "vote_casting", "seat": 8, "data": {}},
        # blank failure code
        {"timestamp": "2026-01-01T00:00:00Z", "operation": "vote_technical_abstain",
         "round": 1, "phase": "vote_casting", "seat": 8,
         "data": {"failure_code": ""}},
    ],
)
def test_public_technical_abstain_rejects_invalid_records(record):
    assert game_routes._public_operation_events(record) == []


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


def _day_record(event_type: str, payload: object) -> dict:
    return {
        "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action", "round": 2,
        "phase": "speech", "data": {"event_type": event_type, "payload": payload},
    }


def test_public_day_verdict_events_project_flip_explode_and_reveal():
    assert game_routes._public_operation_events(
        _day_record("EXILE_CANCELLED", {"target_seat": 3, "round_number": 2}),
    ) == [{"event_type": "exile_cancelled", "payload": {"round_number": 2, "target_seat": 3}}]
    assert game_routes._public_operation_events(
        _day_record("SELF_EXPLODE", {"seat": 1, "target_seat": 4, "round_number": 2}),
    ) == [{"event_type": "self_explode", "payload": {"round_number": 2, "seat": 1, "target_seat": 4}}]
    assert game_routes._public_operation_events(
        _day_record("PLAYER_REVEALED", {"seat_number": 3, "role": "wolf-killer-idiot", "camp": "good"}),
    ) == [{"event_type": "player_revealed", "payload": {
        "seat_number": 3, "role": "wolf-killer-idiot", "camp": "good"}}]
    assert game_routes._public_operation_events(
        _day_record("WEREWOLF_KING_REASONING", {
            "seat": 1, "action_type": "explode", "target_seat": 4, "reasoning": "r", "thought": "t",
        }),
    ) == [{"event_type": "night_thought", "payload": {
        "round_number": 2, "seat": 1, "action_type": "werewolf_king_reasoning",
        "target_seat": 4, "reasoning": "r"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_ELECTED", {"seat": 2, "round_number": 1, "reason": "auto"}),
    ) == [{"event_type": "sheriff_elected", "payload": {
        "round_number": 2, "seat": 2, "reason": "auto"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_ELECTED", {"seat": None, "round_number": 1, "reason": "none"}),
    ) == [{"event_type": "sheriff_elected", "payload": {
        "round_number": 2, "seat": None, "reason": "none"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_BADGE", {"from_seat": 3, "to_seat": 5, "round_number": 1}),
    ) == [{"event_type": "sheriff_badge", "payload": {
        "round_number": 2, "from_seat": 3, "to_seat": 5}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_BADGE", {"from_seat": 3, "to_seat": None, "round_number": 1}),
    ) == [{"event_type": "sheriff_badge", "payload": {
        "round_number": 2, "from_seat": 3, "to_seat": None}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_RUN", {"seat": 2, "choice": "run", "round_number": 1}),
    ) == [{"event_type": "sheriff_run", "payload": {
        "round_number": 2, "seat": 2, "choice": "run"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_RUN", {"seat": 4, "choice": "pass", "round_number": 1}),
    ) == [{"event_type": "sheriff_run", "payload": {
        "round_number": 2, "seat": 4, "choice": "pass"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_WITHDRAW", {"seat": 2, "choice": "withdraw", "round_number": 1}),
    ) == [{"event_type": "sheriff_withdraw", "payload": {
        "round_number": 2, "seat": 2, "choice": "withdraw"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_WITHDRAW", {"seat": 3, "choice": "stay", "round_number": 1}),
    ) == [{"event_type": "sheriff_withdraw", "payload": {
        "round_number": 2, "seat": 3, "choice": "stay"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_VOTE", {
            "voter_seat": 4, "target_seat": 2, "kind": "vote", "round_number": 1,
        }),
    ) == [{"event_type": "sheriff_vote", "payload": {
        "round_number": 2, "voter_seat": 4, "target_seat": 2, "kind": "vote"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_VOTE", {
            "voter_seat": 1, "target_seat": None, "kind": "pk", "round_number": 1,
        }),
    ) == [{"event_type": "sheriff_vote", "payload": {
        "round_number": 2, "voter_seat": 1, "target_seat": None, "kind": "pk"}}]
    assert game_routes._public_operation_events(
        _day_record("SHERIFF_SIDE", {
            "seat": 2, "side": "sheriff_left", "round_number": 1,
        }),
    ) == [{"event_type": "sheriff_side", "payload": {
        "round_number": 2, "seat": 2, "side": "sheriff_left"}}]


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("EXILE_CANCELLED", {"target_seat": 3}),
        ("EXILE_CANCELLED", {"target_seat": 0, "round_number": 2}),
        ("EXILE_CANCELLED", "not-a-dict"),
        ("SELF_EXPLODE", {"seat": 1, "target_seat": "4", "round_number": 2}),
        ("SELF_EXPLODE", {"seat": -1, "target_seat": 4, "round_number": 2}),
        ("SHERIFF_ELECTED", {"seat": 0, "round_number": 2, "reason": "auto"}),
        ("SHERIFF_ELECTED", {"seat": None, "round_number": 2, "reason": ""}),
        ("SHERIFF_ELECTED", {"seat": 1, "round_number": 2, "reason": 1}),
        ("SHERIFF_BADGE", {"from_seat": 0, "to_seat": None, "round_number": 2}),
        ("SHERIFF_BADGE", {"from_seat": 1, "to_seat": 0, "round_number": 2}),
        ("SHERIFF_RUN", {"seat": 0, "choice": "run", "round_number": 2}),
        ("SHERIFF_RUN", {"seat": 1, "choice": "explode", "round_number": 2}),
        ("SHERIFF_WITHDRAW", {"seat": 1, "choice": "run", "round_number": 2}),
        ("SHERIFF_VOTE", {"voter_seat": 0, "target_seat": None, "kind": "vote", "round_number": 2}),
        ("SHERIFF_VOTE", {"voter_seat": 1, "target_seat": 0, "kind": "pk", "round_number": 2}),
        ("SHERIFF_VOTE", {"voter_seat": 1, "target_seat": None, "kind": "final", "round_number": 2}),
        ("SHERIFF_SIDE", {"seat": 1, "side": "left", "round_number": 2}),
        ("SHERIFF_SIDE", {"seat": 0, "side": "sheriff_left", "round_number": 2}),
        ("PLAYER_REVEALED", {"seat_number": 3, "role": "", "camp": "good"}),
        ("PLAYER_REVEALED", {"seat_number": 3, "role": 7, "camp": "good"}),
        ("PLAYER_REVEALED", {"seat_number": 3, "role": "x", "camp": "third_party"}),
        ("PLAYER_REVEALED", {"seat_number": "3", "role": "x", "camp": "good"}),
    ],
)
def test_public_day_verdict_events_reject_malformed_payloads(event_type, payload):
    assert game_routes._public_operation_events(_day_record(event_type, payload)) == []


def test_game_logs_response_accepts_day_verdict_events():
    from app.api.schemas import GameLogsResponse

    response = GameLogsResponse(game_id="g", events=[
        {"event_type": "exile_cancelled", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"round_number": 2, "target_seat": 3}},
        {"event_type": "self_explode", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"round_number": 2, "seat": 1, "target_seat": 4}},
        {"event_type": "player_revealed", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"seat_number": 3, "role": "wolf-killer-idiot", "camp": "good"}},
        {"event_type": "sheriff_elected", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"round_number": 1, "seat": 2, "reason": "vote"}},
        {"event_type": "sheriff_run", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"round_number": 1, "seat": 2, "choice": "pass"}},
        {"event_type": "sheriff_withdraw", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"round_number": 1, "seat": 2, "choice": "stay"}},
        {"event_type": "sheriff_vote", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"round_number": 1, "voter_seat": 3, "target_seat": None, "kind": "pk"}},
        {"event_type": "sheriff_side", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"round_number": 1, "seat": 2, "side": "death_right"}},
        {"event_type": "death", "timestamp": "2026-01-01T00:00:00Z",
         "payload": {"player_seat": 1, "cause": "self_explode", "round_number": 2}},
    ])
    assert [event.event_type for event in response.events] == [
        "exile_cancelled", "self_explode", "player_revealed",
        "sheriff_elected", "sheriff_run", "sheriff_withdraw", "sheriff_vote", "sheriff_side",
        "death"]


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
    # 直接投影不做响应模型序列化：没有 day_plan 的记录就不带这个键。
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
    assert events[1]["payload"] == {
        "round_number": 1, "seat": 1, "text": "我怀疑2号", "day_plan": None,
    }
    # `degraded` 与 `day_plan` 同款：旧记录没有这个键，DTO 用 `None` 表示缺省。
    assert events[2]["payload"] == {
        "round_number": 1, "seat": 1, "target_seat": 2, "reasoning": "像神",
        "degraded": None,
    }
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


@pytest.mark.parametrize("getter,expected", [
    (None, "completed"),
    (MagicMock(side_effect=KeyError("gone")), "completed"),
    (lambda _game: {"execution_status": "paused", "recoverable": True}, "paused"),
])
def test_execution_metadata_prefers_durable_state_and_falls_back_for_legacy_archives(getter, expected):
    service = SimpleNamespace(get_execution_info=getter)
    state = SimpleNamespace(phase=SimpleNamespace(value="game_over"))
    info = game_routes._execution_info(service, "g", state)
    assert info["execution_status"] == expected
    assert info["recoverable"] is (expected == "paused")


@pytest.mark.asyncio
async def test_missing_recovery_capability_returns_a_stable_conflict(monkeypatch):
    monkeypatch.setattr(game_routes, "get_service", lambda: SimpleNamespace())
    with pytest.raises(HTTPException) as caught:
        await game_routes.recover_game("g")
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "recovery_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("message,getter,expected_code,expected_run", [
    ("", None, "recovery_blocked", None),
    ("model configuration is missing", None, "recovery_blocked", None),
    ("game_managed_by_benchmark", None, "game_managed_by_benchmark", None),
    ("game_managed_by_benchmark", lambda _g: None, "game_managed_by_benchmark", None),
    ("game_managed_by_benchmark", lambda _g: {}, "game_managed_by_benchmark", None),
    ("game_managed_by_benchmark", lambda _g: {"benchmark_run_id": "run-1"}, "game_managed_by_benchmark", "run-1"),
])
async def test_recovery_blocks_preserve_stable_codes_and_benchmark_ownership(monkeypatch, message, getter, expected_code, expected_run):
    service = SimpleNamespace(recover_game=AsyncMock(side_effect=ValueError(message)), get_execution_info=getter)
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.recover_game("g")
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == expected_code
    assert caught.value.detail.get("benchmark_run_id") == expected_run


@pytest.mark.asyncio
async def test_control_forwards_the_force_flag_only_where_it_is_meaningful(monkeypatch):
    service = SimpleNamespace(
        recover_game=AsyncMock(return_value={"execution_status": "running"}),
        resume_game=AsyncMock(return_value={"execution_status": "running"}),
        pause_game=AsyncMock(return_value={"execution_status": "paused"}),
    )
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    await game_routes.recover_game("g", force=True)
    await game_routes.resume_game("g", force=True)
    await game_routes.pause_game("g")

    service.recover_game.assert_awaited_once_with("g", force=True)
    service.resume_game.assert_awaited_once_with("g", force=True)
    service.pause_game.assert_awaited_once_with("g")


@pytest.mark.asyncio
@pytest.mark.parametrize("code,confirmation", [
    ("role_declaration_drift", True),
    ("contract_digest_drift", True),
    ("registry_identity_unknown", True),
    ("registry_incompatible", False),
    ("checkpoint_corrupt", False),
])
async def test_only_resumable_drift_asks_the_user_to_confirm(
    monkeypatch, code, confirmation,
):
    service = SimpleNamespace(recover_game=AsyncMock(side_effect=ValueError(code)))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.recover_game("g")
    assert caught.value.detail["code"] == code
    if confirmation:
        assert caught.value.detail["confirmation_required"] is True
    else:
        assert "confirmation_required" not in caught.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("state,status", [(None, 404), (SimpleNamespace(phase=SimpleNamespace(value="game_over")), 200)])
async def test_control_without_response_uses_game_state_or_reports_disappearance(monkeypatch, state, status):
    service = SimpleNamespace(recover_game=AsyncMock(return_value=None), get_game_state=lambda _g: state)
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    if status == 404:
        with pytest.raises(HTTPException) as caught:
            await game_routes.recover_game("g")
        assert caught.value.status_code == 404
    else:
        response = await game_routes.recover_game("g")
        assert response.execution_status == "completed"
        assert response.recovery_block_code == "legacy_archive"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,failure,status,code", [
    ("snapshot", "missing", 404, "game_not_found"),
    ("snapshot", "unavailable", 409, "snapshot_unavailable"),
    ("events", "missing", 404, "game_not_found"),
    ("events", "invalid", 422, "invalid_cursor"),
])
async def test_audience_endpoints_map_unavailable_archives_and_invalid_cursors(monkeypatch, endpoint, failure, status, code):
    from app.services.audience_event_service import AudienceGameNotFoundError
    audience = MagicMock()
    method = audience.get_snapshot if endpoint == "snapshot" else audience.get_events
    if failure == "missing":
        method.side_effect = AudienceGameNotFoundError("g")
    elif failure == "invalid":
        method.side_effect = ValueError("cursor is outside pinned range")
    else:
        method.return_value = None
    monkeypatch.setattr(game_routes, "get_audience_service", lambda: audience)
    with pytest.raises(HTTPException) as caught:
        if endpoint == "snapshot":
            await game_routes.get_game_snapshot("g")
        else:
            await game_routes.get_game_events("g")
    assert caught.value.status_code == status
    assert caught.value.detail["code"] == code


@pytest.mark.asyncio
async def test_replay_reads_the_configured_string_data_directory(monkeypatch, tmp_path):
    directory = tmp_path / "games" / "g"
    directory.mkdir(parents=True)
    (directory / "conversation.log").write_text("", encoding="utf-8")
    (directory / "game.log").write_text("", encoding="utf-8")
    service = SimpleNamespace(data_dir=str(tmp_path), get_game_state=lambda _g: object())
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    response = await game_routes.get_game_logs("g")
    assert response.game_id == "g"
    assert response.events == []


@pytest.mark.asyncio
async def test_create_game_maps_runtime_configuration_validation_to_bad_request(monkeypatch):
    service = SimpleNamespace(create_game=AsyncMock(side_effect=ValueError("unknown model configuration")))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.create_game(CreateGameRequest())
    assert caught.value.status_code == 400
    assert caught.value.detail == "unknown model configuration"
