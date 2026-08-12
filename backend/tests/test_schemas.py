import pytest
from pydantic import ValidationError

from app.api.schemas import (
    CreateGameRequest, CreateGameResponse, GameListItem,
    GameListResponse, GameDetailResponse, GameLogsResponse,
    PublicDeathResponse, PublicPhaseResponse, PublicPlayerResponse,
    PublicSpeechResponse, PublicVoteResponse, PublicVoteResultResponse,
    PublicWinnerResponse,
    SetSpeedRequest, WSMessage,
)


class TestSchemas:

    @pytest.mark.parametrize(
        "payload",
        [
            {"voter_seat": "1", "target_seat": "2", "round_number": "3"},
            {"voter_seat": True, "target_seat": 2, "round_number": 3},
            {"voter_seat": 1, "target_seat": True, "round_number": 3},
            {"voter_seat": 1, "target_seat": 2, "round_number": False},
            {"voter_seat": 0, "target_seat": 2, "round_number": 3},
            {"voter_seat": 1, "target_seat": 0, "round_number": 3},
            {"voter_seat": 1, "target_seat": 2, "round_number": -1},
        ],
    )
    def test_public_vote_rejects_coerced_or_non_positive_identifiers(self, payload):
        with pytest.raises(ValidationError):
            GameLogsResponse(game_id="abc", events=[{
                "event_type": "vote", "payload": payload,
            }])

    @pytest.mark.parametrize(
        "response_factory, kwargs",
        [
            (PublicPlayerResponse, {"seat_number": True, "is_alive": "true", "is_sheriff": False}),
            (PublicSpeechResponse, {"player_seat": "1", "text": "public", "round_number": 1}),
            (PublicDeathResponse, {"player_seat": 1, "cause": "secret: seer", "round_number": 1}),
            (PublicPhaseResponse, {"phase": "secret: seer checked", "round_number": 1}),
            (PublicWinnerResponse, {"winning_camp": "third_party", "reason": "all_wolves_dead"}),
            (PublicWinnerResponse, {"winning_camp": "good", "reason": "secret: antidote used"}),
            (PublicVoteResultResponse, {"round_number": 1, "exiled_seat": False}),
        ],
    )
    def test_public_responses_reject_coercion_and_unknown_public_values(
        self, response_factory, kwargs,
    ):
        with pytest.raises(ValidationError):
            response_factory(**kwargs)

    def test_create_game_request_defaults(self):
        req = CreateGameRequest()
        assert req.num_werewolves == 3
        assert req.num_villagers == 3

    def test_create_game_request_custom(self):
        req = CreateGameRequest(num_werewolves=4, num_villagers=4)
        assert req.num_werewolves == 4

    def test_create_game_request_rejects_mixed_role_count_formats(self):
        with pytest.raises(ValidationError, match="role_counts cannot be combined"):
            CreateGameRequest(
                role_counts={"wolf-killer-werewolf": 1},
                num_werewolves=1,
            )

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
            players={}, sheriff=None, speeches=[],
            death_history=[], win_result=None,
        )
        assert resp.game_id == "abc"

    def test_game_detail_response_rejects_votes_outside_public_contract(self):
        with pytest.raises(ValidationError):
            GameDetailResponse(
                game_id="abc", phase="speech", round_number=2,
                players={}, sheriff=None, speeches=[], votes=[],
                death_history=[], win_result=None,
            )

    def test_public_vote_result_replay_event_is_closed(self):
        logs = GameLogsResponse(
            game_id="abc",
            events=[{
                "event_type": "vote_result",
                "payload": {"round_number": 2, "exiled_seat": None},
            }],
        )

        assert logs.model_dump()["events"] == [{
            "event_type": "vote_result",
            "payload": {"round_number": 2, "exiled_seat": None},
        }]
        with pytest.raises(ValidationError):
            PublicVoteResultResponse(
                round_number=2,
                exiled_seat=3,
                tally={3: 4},
            )

    def test_public_observer_models_expose_only_public_fields(self):
        detail = GameDetailResponse(
            game_id="abc",
            phase="speech",
            round_number=2,
            players={1: PublicPlayerResponse(
                seat_number=1,
                is_alive=True,
                is_sheriff=False,
            )},
            sheriff=None,
            speeches=[PublicSpeechResponse(
                player_seat=1,
                text="公开发言",
                round_number=2,
            )],
            death_history=[PublicDeathResponse(
                player_seat=2,
                cause="exile",
                round_number=2,
            )],
            win_result=PublicWinnerResponse(
                winning_camp="good",
                reason="all_wolves_dead",
            ),
        )
        logs = GameLogsResponse(
            game_id="abc",
            events=[
                {"event_type": "speech", "payload": {
                    "player_seat": 1, "text": "公开发言", "round_number": 2,
                }},
                {"event_type": "phase", "payload": PublicPhaseResponse(
                    phase="speech", round_number=2,
                )},
                {
                    "event_type": "vote_result",
                    "payload": {"round_number": 2, "exiled_seat": None},
                },
            ],
        )

        serialized = {"detail": detail.model_dump(), "logs": logs.model_dump()}
        forbidden = {"role", "camp", "has_antidote", "has_poison", "has_gun"}
        assert not (forbidden & _all_keys(serialized))

    def test_public_player_rejects_private_identity_fields(self):
        with pytest.raises(ValidationError):
            PublicPlayerResponse(
                seat_number=1,
                is_alive=True,
                is_sheriff=False,
                role="wolf-killer-werewolf",
            )

    def test_game_logs_rejects_legacy_private_log_collections(self):
        with pytest.raises(ValidationError):
            GameLogsResponse(
                game_id="abc",
                events=[],
                conversations=[],
                operations=[],
            )

    def test_set_speed_request(self):
        req = SetSpeedRequest(delay_seconds=5.0)
        assert req.delay_seconds == 5.0

    def test_ws_message(self):
        msg = WSMessage(type="phase_change", payload={"phase": "night"})
        assert msg.type == "phase_change"
        assert msg.payload["phase"] == "night"


def _all_keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value)) if value else set()
    return set()
