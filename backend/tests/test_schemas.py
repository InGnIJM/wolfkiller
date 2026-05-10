import pytest
from app.api.schemas import (
    CreateGameRequest, CreateGameResponse, GameListItem,
    GameListResponse, GameDetailResponse, SetSpeedRequest, WSMessage,
)


class TestSchemas:
    def test_create_game_request_defaults(self):
        req = CreateGameRequest()
        assert req.num_werewolves == 3
        assert req.num_villagers == 3

    def test_create_game_request_custom(self):
        req = CreateGameRequest(num_werewolves=4, num_villagers=4)
        assert req.num_werewolves == 4

    def test_create_game_response(self):
        resp = CreateGameResponse(game_id="abc", player_count=9, config={"test": 1})
        assert resp.game_id == "abc"
        assert resp.player_count == 9

    def test_game_list_item(self):
        item = GameListItem(
            game_id="abc", phase="night", round_number=2,
            player_count=9, alive_count=7, winner=None,
        )
        assert item.game_id == "abc"
        assert item.phase == "night"

    def test_game_list_item_with_winner(self):
        item = GameListItem(
            game_id="abc", phase="game_over", round_number=5,
            player_count=9, alive_count=4, winner="good",
        )
        assert item.winner == "good"

    def test_game_list_response(self):
        resp = GameListResponse(games=[])
        assert resp.games == []

    def test_game_detail_response(self):
        resp = GameDetailResponse(
            game_id="abc", phase="speech", round_number=2,
            players={}, sheriff=None, speeches=[], votes=[],
            death_history=[], win_result=None,
        )
        assert resp.game_id == "abc"

    def test_set_speed_request(self):
        req = SetSpeedRequest(delay_seconds=5.0)
        assert req.delay_seconds == 5.0

    def test_ws_message(self):
        msg = WSMessage(type="phase_change", payload={"phase": "night"})
        assert msg.type == "phase_change"
        assert msg.payload["phase"] == "night"
