import pytest
from pydantic import ValidationError

from app.api.schemas import (
    CreateGameRequest, CreateGameResponse, GameListItem,
    GameListResponse, GameDetailResponse, GameLogsResponse,
    GameMemoriesResponse, PlayerMemoryResponse,
    PublicDeathResponse, PublicNarrationResponse, PublicNightActionResponse,
    PublicNightThoughtResponse, PublicPhaseResponse, PublicPlayerResponse,
    PublicSeerThoughtResponse, PublicSpeechResponse, PublicVoteResponse,
    PublicVoteResultResponse, PublicWinnerResponse, PublicWitchThoughtResponse,
    PublicWolfChatMessageResponse, PublicWolfVoteResponse,
    RenameGameRequest, SetSpeedRequest, WSMessage, FolderNameRequest,
)


PUBLIC_TIMESTAMP = "2026-08-12T12:34:56Z"


PUBLIC_REPLAY_EVENTS = [
    {"event_type": "speech", "payload": {
        "player_seat": 1, "text": "public", "round_number": 2,
    }},
    {"event_type": "death", "payload": {
        "player_seat": 2, "cause": "exile", "round_number": 2,
    }},
    {"event_type": "vote", "payload": {
        "voter_seat": 1, "target_seat": 2, "round_number": 2,
    }},
    {"event_type": "vote_result", "payload": {
        "round_number": 2, "exiled_seat": 2,
    }},
    {"event_type": "night_action", "payload": {
        "action_type": "witch_save", "target_seat": 3, "round_number": 2,
    }},
    {"event_type": "night_thought", "payload": {
        "round_number": 2, "seat": 7, "action_type": "hunter_reasoning",
        "target_seat": 1, "reasoning": "先查跳预言家的人",
    }},
    {"event_type": "narration", "payload": {
        "round_number": 2, "title": "天黑请闭眼", "text": "狼人请睁眼",
    }},
    {"event_type": "wolf_chat_message", "payload": {
        "round_number": 2, "seat": 1, "text": "我怀疑2号",
    }},
    {"event_type": "wolf_vote", "payload": {
        "round_number": 2, "seat": 1, "target_seat": 2, "reasoning": "像神",
    }},
    {"event_type": "witch_thought", "payload": {
        "round_number": 2, "seat": 3, "text": "考虑救人",
    }},
    {"event_type": "seer_thought", "payload": {
        "round_number": 2, "seat": 4, "text": "查验2号",
    }},
    {"event_type": "phase", "payload": {
        "phase": "speech", "round_number": 2,
    }},
    {"event_type": "winner", "payload": {
        "winning_camp": "good", "reason": "all_wolves_dead",
    }},
]


class TestSchemas:

    @pytest.mark.parametrize("event", PUBLIC_REPLAY_EVENTS)
    @pytest.mark.parametrize(
        "timestamp",
        [PUBLIC_TIMESTAMP, "2026-08-12T12:34:56.123456Z"],
    )
    def test_public_replay_events_accept_canonical_utc_timestamps(self, event, timestamp):
        response = GameLogsResponse(
            game_id="abc", events=[{**event, "timestamp": timestamp}],
        )

        assert response.model_dump()["events"][0]["timestamp"] == timestamp

    @pytest.mark.parametrize("event", PUBLIC_REPLAY_EVENTS)
    def test_public_replay_events_require_timestamp_on_envelope(self, event):
        with pytest.raises(ValidationError):
            GameLogsResponse(game_id="abc", events=[event])

    @pytest.mark.parametrize(
        "action_type",
        ["hunter_reasoning", "witch_reasoning", "seer_reasoning", "guard_reasoning"],
    )
    def test_night_thought_accepts_all_role_reasoning_types(self, action_type):
        response = GameLogsResponse(game_id="abc", events=[{
            "event_type": "night_thought", "timestamp": PUBLIC_TIMESTAMP,
            "payload": {
                "round_number": 2, "seat": 2, "action_type": action_type,
                "target_seat": 4, "reasoning": "根据已有信息行动",
            },
        }])
        assert response.events[0].payload.action_type == action_type

    def test_night_thought_rejects_unknown_reasoning_type(self):
        with pytest.raises(ValidationError):
            GameLogsResponse(game_id="abc", events=[{
                "event_type": "night_thought", "timestamp": PUBLIC_TIMESTAMP,
                "payload": {
                    "round_number": 2, "seat": 2, "action_type": "wolf_reasoning",
                    "target_seat": None, "reasoning": "未知类型",
                },
            }])

    @pytest.mark.parametrize("event", PUBLIC_REPLAY_EVENTS)
    @pytest.mark.parametrize(
        "timestamp",
        [
            "", "2026-08-12", "2026-08-12T12:34:56",
            "2026-08-12T12:34:56+08:00", "2026-08-12T12:34:56.1234567Z",
            "2026-02-30T12:34:56Z", "02026-08-12T12:34:56Z",
            "2026-08-12T12:34Z", 1, True,
        ],
    )
    def test_public_replay_events_reject_noncanonical_timestamps(self, event, timestamp):
        with pytest.raises(ValidationError):
            GameLogsResponse(
                game_id="abc",
                events=[{**event, "timestamp": timestamp}],
            )

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
                "event_type": "vote", "timestamp": PUBLIC_TIMESTAMP,
                "payload": payload,
            }])

    @pytest.mark.parametrize(
        "response_factory, kwargs",
        [
            (GameDetailResponse, {
                "game_id": "initial", "phase": "waiting", "round_number": 0,
                "players": {}, "sheriff": None, "speeches": [],
                "death_history": [], "win_result": None, "reveal_on_death": False,
            }),
            (PublicSpeechResponse, {"player_seat": 1, "text": "public", "round_number": 0}),
            (PublicDeathResponse, {"player_seat": 1, "cause": "exile", "round_number": 0}),
            (PublicVoteResponse, {"voter_seat": 1, "target_seat": None, "round_number": 0}),
            (PublicVoteResultResponse, {"exiled_seat": None, "round_number": 0}),
            (PublicPhaseResponse, {"phase": "waiting", "round_number": 0}),
        ],
    )
    def test_public_round_numbers_accept_initial_zero(self, response_factory, kwargs):
        assert response_factory(**kwargs).round_number == 0

    @pytest.mark.parametrize("invalid_round", [True, "0", -1])
    def test_public_round_numbers_remain_strict_and_non_negative(self, invalid_round):
        with pytest.raises(ValidationError):
            PublicPhaseResponse(phase="waiting", round_number=invalid_round)

    @pytest.mark.parametrize(
        "response_factory, kwargs",
        [
            (PublicPlayerResponse, {"seat_number": True, "is_alive": "true", "is_sheriff": False, "role": "wolf-killer-werewolf", "camp": "werewolf"}),
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
        assert req.reveal_on_death is False

    def test_create_game_request_custom(self):
        req = CreateGameRequest(num_werewolves=4, num_villagers=4)
        assert req.num_werewolves == 4

    def test_create_game_request_accepts_reveal_on_death(self):
        req = CreateGameRequest(reveal_on_death=True)
        assert req.reveal_on_death is True

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
            game_id="abc", name="夜局", phase="night", round_number=2,
            player_count=9, alive_count=7, winner=None,
        )
        assert item.game_id == "abc"
        assert item.name == "夜局"
        assert item.phase == "night"

    def test_game_list_item_with_winner(self):
        item = GameListItem(
            game_id="abc", name="终局", phase="game_over", round_number=5,
            player_count=9, alive_count=4, winner="good",
        )
        assert item.winner == "good"

    def test_rename_game_request_strips_and_rejects_blank(self):
        assert RenameGameRequest(name="  新名字  ").name == "新名字"
        with pytest.raises(ValidationError):
            RenameGameRequest(name="   ")
        with pytest.raises(ValidationError):
            RenameGameRequest(name="x" * 51)

    def test_folder_name_request_strips_and_rejects_blank(self):
        assert FolderNameRequest(name="  九月  ").name == "九月"
        with pytest.raises(ValidationError):
            FolderNameRequest(name="   ")
        with pytest.raises(ValidationError):
            FolderNameRequest(name="x" * 51)

    def test_game_list_response(self):
        resp = GameListResponse(games=[])
        assert resp.games == []

    def test_game_detail_response(self):
        resp = GameDetailResponse(
            game_id="abc", phase="speech", round_number=2,
            players={}, sheriff=None, speeches=[],
            death_history=[], win_result=None, reveal_on_death=True,
        )
        assert resp.game_id == "abc"
        assert resp.model_dump()["reveal_on_death"] is True
        assert resp.model_snapshot == []

    def test_game_detail_response_requires_reveal_on_death(self):
        with pytest.raises(ValidationError):
            GameDetailResponse(
                game_id="abc", phase="speech", round_number=2,
                players={}, sheriff=None, speeches=[],
                death_history=[], win_result=None,
            )

    def test_game_detail_response_rejects_votes_outside_public_contract(self):
        with pytest.raises(ValidationError):
            GameDetailResponse(
                game_id="abc", phase="speech", round_number=2,
                players={}, sheriff=None, speeches=[], votes=[],
                death_history=[], win_result=None, reveal_on_death=False,
            )

    @pytest.mark.parametrize("invalid_key", [0, -1, "1", True])
    def test_game_detail_rejects_non_strict_or_non_positive_player_map_keys(self, invalid_key):
        with pytest.raises(ValidationError):
            GameDetailResponse(
                game_id="abc", phase="speech", round_number=2,
                players={invalid_key: PublicPlayerResponse(
                    seat_number=1, is_alive=True, is_sheriff=False,
                    role="wolf-killer-villager", camp="good",
                )},
                sheriff=None, speeches=[], death_history=[], win_result=None,
                reveal_on_death=False,
            )

    def test_game_detail_keeps_valid_player_map_key_in_model_dump(self):
        response = GameDetailResponse(
            game_id="abc", phase="speech", round_number=2,
            players={1: PublicPlayerResponse(
                seat_number=1, is_alive=True, is_sheriff=False,
                role="wolf-killer-villager", camp="good",
            )},
            sheriff=None, speeches=[], death_history=[], win_result=None,
            reveal_on_death=False,
        )

        assert response.model_dump()["players"] == {
            1: {"seat_number": 1, "is_alive": True, "is_sheriff": False,
                "role": "wolf-killer-villager", "camp": "good"},
        }

    def test_game_detail_rejects_player_map_key_that_differs_from_embedded_seat(self):
        with pytest.raises(ValidationError, match="must match"):
            GameDetailResponse(
                game_id="abc", phase="speech", round_number=2,
                players={1: PublicPlayerResponse(
                    seat_number=2, is_alive=True, is_sheriff=False,
                    role="wolf-killer-villager", camp="good",
                )},
                sheriff=None, speeches=[], death_history=[], win_result=None,
                reveal_on_death=False,
            )

    def test_public_vote_result_replay_event_is_closed(self):
        logs = GameLogsResponse(
            game_id="abc",
            events=[{
                "event_type": "vote_result",
                "timestamp": PUBLIC_TIMESTAMP,
                "payload": {"round_number": 2, "exiled_seat": None},
            }],
        )

        assert logs.model_dump()["events"] == [{
            "event_type": "vote_result",
            "timestamp": PUBLIC_TIMESTAMP,
            "payload": {"round_number": 2, "exiled_seat": None},
        }]
        with pytest.raises(ValidationError):
            PublicVoteResultResponse(
                round_number=2,
                exiled_seat=3,
                tally={3: 4},
            )

    def test_public_night_action_replay_event_is_closed(self):
        logs = GameLogsResponse(
            game_id="abc",
            events=[{
                "event_type": "night_action",
                "timestamp": PUBLIC_TIMESTAMP,
                "payload": {
                    "action_type": "werewolf_kill", "target_seat": 4,
                    "round_number": 2, "vote_counts": {"4": 2},
                },
            }],
        )

        assert logs.model_dump()["events"] == [{
            "event_type": "night_action",
            "timestamp": PUBLIC_TIMESTAMP,
            "payload": {
                "action_type": "werewolf_kill", "target_seat": 4,
                "round_number": 2, "vote_counts": {"4": 2}, "result": None,
            },
        }]

    @pytest.mark.parametrize(
        "payload",
        [
            {"action_type": "guard_action", "target_seat": 1, "round_number": 2},
            {"action_type": "seer_check", "target_seat": 0, "round_number": 2},
            {"action_type": "seer_check", "target_seat": True, "round_number": 2},
            {"action_type": "seer_check", "target_seat": 1, "round_number": -1},
            {"action_type": "seer_check", "target_seat": 1, "round_number": 2, "result": "third_party"},
            {"action_type": "seer_check", "target_seat": 1, "round_number": 2, "result": 3},
            {"action_type": "werewolf_kill", "target_seat": 1, "round_number": 2, "vote_counts": {1: 1}},
            {"action_type": "werewolf_kill", "target_seat": 1, "round_number": 2, "vote_counts": {"1": 0}},
        ],
    )
    def test_public_night_action_rejects_invalid_payloads(self, payload):
        with pytest.raises(ValidationError):
            GameLogsResponse(game_id="abc", events=[{
                "event_type": "night_action", "timestamp": PUBLIC_TIMESTAMP,
                "payload": payload,
            }])

    def test_public_observer_models_expose_only_public_fields(self):
        detail = GameDetailResponse(
            game_id="abc",
            phase="speech",
            round_number=2,
            players={1: PublicPlayerResponse(
                seat_number=1,
                is_alive=True,
                is_sheriff=False,
                role="wolf-killer-villager",
                camp="good",
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
            reveal_on_death=False,
        )
        logs = GameLogsResponse(
            game_id="abc",
            events=[
                {"event_type": "speech", "payload": {
                    "player_seat": 1, "text": "公开发言", "round_number": 2,
                }, "timestamp": PUBLIC_TIMESTAMP},
                {"event_type": "phase", "timestamp": PUBLIC_TIMESTAMP,
                 "payload": PublicPhaseResponse(
                    phase="speech", round_number=2,
                )},
                {
                    "event_type": "vote_result",
                    "timestamp": PUBLIC_TIMESTAMP,
                    "payload": {"round_number": 2, "exiled_seat": None},
                },
            ],
        )

        serialized = {"detail": detail.model_dump(), "logs": logs.model_dump()}
        forbidden = {"check_results", "has_antidote", "has_poison", "has_gun"}
        assert not (forbidden & _all_keys(serialized))

    def test_speech_accepts_optional_phase_for_replay_and_detail(self):
        with_phase = PublicSpeechResponse(
            player_seat=1, text="遗言", round_number=1, phase="last_words",
        )
        assert with_phase.phase == "last_words"
        without = PublicSpeechResponse(player_seat=1, text="发言", round_number=1)
        assert without.phase is None

    def test_night_thought_rejects_unknown_action_type(self):
        with pytest.raises(ValidationError):
            PublicNightThoughtResponse(
                round_number=1, seat=1, action_type="wolf_reasoning",
                target_seat=None, reasoning="r",
            )

    def test_guard_night_action_and_thought_types_are_accepted(self):
        action = PublicNightActionResponse(
            action_type="guard_protect", target_seat=6, round_number=1,
        )
        assert action.action_type == "guard_protect"
        thought = PublicNightThoughtResponse(
            round_number=1, seat=5, action_type="guard_reasoning",
            target_seat=6, reasoning="守一下",
        )
        assert thought.action_type == "guard_reasoning"

    def test_staged_night_payload_models_enforce_route_boundaries(self):
        assert PublicNarrationResponse(
            round_number=1, title="天" * 100, text="文" * 200,
        ).text == "文" * 200
        assert PublicWolfChatMessageResponse(
            round_number=1, seat=1, text="文" * 200,
        ).seat == 1
        assert PublicWitchThoughtResponse(
            round_number=1, seat=1, text="文" * 200,
        ).round_number == 1
        assert PublicSeerThoughtResponse(
            round_number=1, seat=1, text="文" * 200,
        ).round_number == 1
        assert PublicWolfVoteResponse(
            round_number=1, seat=1, target_seat=None, reasoning="r" * 500,
        ).reasoning == "r" * 500

    @pytest.mark.parametrize(
        "response_factory, kwargs",
        [
            (PublicNarrationResponse, {"round_number": 1, "title": "", "text": "t"}),
            (PublicNarrationResponse, {"round_number": 1, "title": "t", "text": ""}),
            (PublicNarrationResponse, {"round_number": 1, "title": "t" * 101, "text": "t"}),
            (PublicNarrationResponse, {"round_number": 1, "title": "t", "text": "t" * 201}),
            (PublicWolfChatMessageResponse, {"round_number": 1, "seat": 0, "text": "t"}),
            (PublicWolfChatMessageResponse, {"round_number": 1, "seat": 1, "text": ""}),
            (PublicWolfChatMessageResponse, {"round_number": 1, "seat": 1, "text": "t" * 201}),
            (PublicWolfVoteResponse, {"round_number": 1, "seat": 1, "target_seat": 0, "reasoning": "r"}),
            (PublicWolfVoteResponse, {"round_number": 1, "seat": 1, "target_seat": 2, "reasoning": "r" * 501}),
            (PublicWitchThoughtResponse, {"round_number": 1, "seat": 0, "text": "t"}),
            (PublicWitchThoughtResponse, {"round_number": 1, "seat": 1, "text": "t" * 201}),
            (PublicSeerThoughtResponse, {"round_number": 1, "seat": 0, "text": "t"}),
            (PublicSeerThoughtResponse, {"round_number": 1, "seat": 1, "text": ""}),
        ],
    )
    def test_staged_night_payload_models_reject_out_of_bound_values(self, response_factory, kwargs):
        with pytest.raises(ValidationError):
            response_factory(**kwargs)

    def test_memories_response_round_trips_closed_fields(self):
        memory = PlayerMemoryResponse(
            seat_number=1, role="wolf-killer-witch", camp="good", is_alive=True,
            private_knowledge={"has_antidote": True}, action_history=[{"round": 1}],
            witnessed_events=[], last_updated="2026-01-01T00:00:00Z",
        )
        response = GameMemoriesResponse(game_id="g", memories=[memory])
        assert response.model_dump()["memories"][0]["seat_number"] == 1

    def test_public_player_accepts_viewer_identity_but_rejects_private_fields(self):
        public = PublicPlayerResponse(
            seat_number=1,
            is_alive=True,
            is_sheriff=False,
            role="wolf-killer-werewolf",
            camp="werewolf",
        )
        assert public.role == "wolf-killer-werewolf"
        assert public.camp == "werewolf"
        with pytest.raises(ValidationError):
            PublicPlayerResponse(
                seat_number=1,
                is_alive=True,
                is_sheriff=False,
                role="wolf-killer-werewolf",
                camp="werewolf",
                has_antidote=True,
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
