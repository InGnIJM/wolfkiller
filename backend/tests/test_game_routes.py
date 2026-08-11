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
async def test_get_game_serializes_records(monkeypatch):
    record = MagicMock()
    record.to_dict.return_value = {"record": True}
    raw = {"raw": True}
    state = SimpleNamespace(
        game_id="present",
        phase=SimpleNamespace(value="speech"),
        round_number=3,
        players={
            1: SimpleNamespace(
                seat_number=1,
                role="wolf-killer-villager",
                camp="good",
                is_alive=True,
                has_antidote=False,
                has_poison=False,
                has_gun=False,
                is_sheriff=False,
            ),
        },
        sheriff=None,
        speeches=[record, raw],
        votes=[record, raw],
        death_history=[record, raw],
        win_result=None,
    )
    service = MagicMock()
    service.get_game_state.return_value = state
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.get_game("present")

    assert response.players[1]["role"] == "wolf-killer-villager"
    assert response.speeches == [{"record": True}, raw]


def test_read_jsonl_handles_missing_valid_and_invalid_records(tmp_path):
    path = tmp_path / "records.jsonl"
    assert game_routes._read_jsonl(str(path)) == []
    path.write_text('{"valid": true}\nnot-json\n\n', encoding="utf-8")

    assert game_routes._read_jsonl(str(path)) == [{"valid": True}]


@pytest.mark.asyncio
async def test_get_game_logs_reads_both_log_types(monkeypatch):
    monkeypatch.setattr(
        game_routes,
        "_read_jsonl",
        MagicMock(side_effect=[[{"conversation": True}], [{"operation": True}]]),
    )

    response = await game_routes.get_game_logs("present")

    assert response.conversations == [{"conversation": True}]
    assert response.operations == [{"operation": True}]
