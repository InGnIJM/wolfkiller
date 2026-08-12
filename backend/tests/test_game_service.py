import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, mock_open, patch, PropertyMock
from app.services.game_service import GameService
from app.core.game_engine import GameEngine
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.api.websocket.ws_handler import WSManager
from app.roles.registry import builtin_registry
from app.services.game_manifest import GameManifest


class TestGameService:
    @pytest.mark.asyncio
    async def test_create_and_list_games(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game(
            num_werewolves=3, num_villagers=3,
            num_seers=1, num_witches=1, num_hunters=1,
        )

        assert game_id is not None
        assert len(game_id) == 8

        games = service.list_games()
        assert game_id in games

        state = service.get_game_state(game_id)
        assert state is not None
        assert state.config.total_players == 9

    @pytest.mark.asyncio
    async def test_get_nonexistent_game(self):
        service = GameService(WSManager(), EventBus())
        assert service.get_game_state("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_games_returns_list(self):
        service = GameService(WSManager(), EventBus())
        # May include persisted games from data/games/, list should not be None
        games = service.list_games()
        assert isinstance(games, list)

    @pytest.mark.asyncio
    async def test_create_game_with_custom_config(self):
        service = GameService(WSManager(), EventBus())

        game_id = await service.create_game(
            num_werewolves=2, num_villagers=2,
            num_seers=1, num_witches=1, num_hunters=1,
        )

        state = service.get_game_state(game_id)
        assert state is not None
        assert state.config.total_players == 7

    @pytest.mark.asyncio
    async def test_create_game_accepts_role_counts_and_persists_canonical_config(self, monkeypatch):
        counts = {
            "wolf-killer-werewolf": 1,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 0,
            "wolf-killer-witch": 0,
            "wolf-killer-hunter": 0,
        }
        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        game_id = await service.create_game(role_counts=counts)

        assert service.get_game_state(game_id).config.role_counts == counts
        service._manifest.add_game.assert_called_once_with(
            game_id, {"role_counts": counts}
        )

    @pytest.mark.asyncio
    async def test_create_game_uses_registry_role_factory(self, monkeypatch):
        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        with patch.object(
            builtin_registry,
            "create_roles",
            wraps=builtin_registry.create_roles,
        ) as create_roles:
            await service.create_game(
                num_werewolves=1,
                num_villagers=3,
                num_seers=0,
                num_witches=0,
                num_hunters=0,
            )

        create_roles.assert_called_once()

    def test_manifest_accepts_canonical_role_counts(self, tmp_path):
        manifest = GameManifest(str(tmp_path))
        counts = {"wolf-killer-werewolf": 1, "wolf-killer-villager": 3}

        manifest.add_game("game-1", {"role_counts": counts})

        entry = manifest.load_or_rebuild()["game-1"]
        assert entry["player_count"] == 4
        assert entry["config"] == {"role_counts": counts}

    def test_manifest_loads_legacy_config_and_updates_all_metadata(self, tmp_path):
        manifest = GameManifest(str(tmp_path))
        manifest.update_game("missing", phase="night")
        manifest.add_game("game-1", {"num_werewolves": 1, "num_villagers": 3})
        manifest.update_game(
            "game-1",
            phase="night",
            round_number=2,
            player_count=4,
            alive_count=3,
            winner="good",
        )

        entry = GameManifest(str(tmp_path)).load_or_rebuild()["game-1"]

        assert entry["phase"] == "night"
        assert entry["round_number"] == 2
        assert entry["alive_count"] == 3
        assert entry["winner"] == "good"
        assert entry["finished_at"] is not None

    def test_manifest_rebuilds_logs_and_skips_invalid_index(self, tmp_path):
        games = tmp_path / "games"
        games.mkdir()
        (games / "index.json").write_text("not-json", encoding="utf-8")
        (games / "ignored.txt").write_text("ignored", encoding="utf-8")
        recovered = games / "recovered"
        recovered.mkdir()
        (recovered / "game.log").write_text(
            '{"timestamp":"t1","operation":"role_init","data":{"players":{"1":{},"2":{}}}}\n'
            '{"timestamp":"t2","operation":"phase_change","round":2,"data":{"new_phase":"night"}}\n'
            '{"timestamp":"t3","operation":"game_over","data":{"winner":"good"}}\n',
            encoding="utf-8",
        )
        no_log = games / "no-log"
        no_log.mkdir()

        entry = GameManifest(str(tmp_path)).load_or_rebuild()["recovered"]

        assert entry["player_count"] == 2
        assert entry["config"] == {}
        assert entry["phase"] == "game_over"
        assert entry["winner"] == "good"

    @pytest.mark.parametrize("index_contents", [None, "not-json"])
    def test_manifest_recovery_rebuilds_role_counts_from_role_init(self, tmp_path, index_contents):
        games = tmp_path / "games"
        games.mkdir()
        if index_contents is not None:
            (games / "index.json").write_text(index_contents, encoding="utf-8")
        recovered = games / "recovered"
        recovered.mkdir()
        recovered_counts = {
            "wolf-killer-werewolf": 2,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
        }
        players = {
            str(seat): {"role": role_id}
            for seat, role_id in enumerate(
                [
                    "wolf-killer-werewolf",
                    "wolf-killer-werewolf",
                    "wolf-killer-villager",
                    "wolf-killer-villager",
                    "wolf-killer-villager",
                    "wolf-killer-seer",
                ],
                start=1,
            )
        }
        (recovered / "game.log").write_text(
            json.dumps({
                "timestamp": "t1",
                "operation": "role_init",
                "data": {"players": players},
            }) + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path)).load_or_rebuild()["recovered"]

        assert entry["config"] == {"role_counts": recovered_counts}

    def test_manifest_extracts_fallback_votes_and_ignores_bad_lines(self, tmp_path):
        log = tmp_path / "game.log"
        log.write_text(
            'not-json\n'
            '{"timestamp":"t1","operation":"werewolf_kill","data":{"votes":[{"player_seat":1},{"player_seat":2},{"player_seat":1}]}}\n',
            encoding="utf-8",
        )
        manifest = GameManifest(str(tmp_path))

        assert manifest._extract_meta("game", log)["player_count"] == 2
        assert manifest._extract_meta("game", tmp_path / "missing.log") is None

    def test_manifest_skips_malformed_werewolf_vote_items(self, tmp_path):
        log = tmp_path / "game.log"
        log.write_text(
            json.dumps({
                "operation": "werewolf_kill",
                "data": {
                    "votes": [
                        {"player_seat": 1},
                        "malformed",
                        None,
                        ["not", "a", "vote"],
                        {"player_seat": 2},
                    ],
                },
            }) + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path))._extract_meta("game", log)

        assert entry["player_count"] == 2

    def test_manifest_counts_only_positive_integer_werewolf_vote_seats(self, tmp_path):
        log = tmp_path / "game.log"
        log.write_text(
            json.dumps({
                "operation": "werewolf_kill",
                "data": {
                    "votes": [
                        {"player_seat": 1},
                        {"player_seat": True},
                        {"player_seat": "2"},
                        {"player_seat": 0},
                        {"player_seat": -1},
                        {"player_seat": 1.5},
                        {"player_seat": [1]},
                        {"player_seat": {"seat": 1}},
                        {},
                        "malformed",
                        None,
                        {"player_seat": 2},
                    ],
                },
            }) + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path))._extract_meta("game", log)

        assert entry["player_count"] == 2

    @pytest.mark.parametrize("votes", [{"player_seat": 1}, "malformed", None])
    def test_manifest_skips_non_list_werewolf_votes(self, tmp_path, votes):
        log = tmp_path / "game.log"
        log.write_text(
            json.dumps({
                "operation": "werewolf_kill",
                "data": {"votes": votes},
            }) + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path))._extract_meta("game", log)

        assert entry["player_count"] == 0

    def test_manifest_persists_entries_with_mixed_created_at_types(self, tmp_path):
        games = tmp_path / "games"
        games.mkdir()
        entries = [
            {"game_id": "newer", "created_at": "2026-01-01T00:00:00+00:00"},
            {"game_id": "integer-time", "created_at": 1},
            {"game_id": "list-time", "created_at": []},
            {"game_id": "older", "created_at": "2025-01-01T00:00:00+00:00"},
        ]
        (games / "index.json").write_text(json.dumps(entries), encoding="utf-8")

        restored = GameManifest(str(tmp_path)).load_or_rebuild()
        persisted = json.loads((games / "index.json").read_text(encoding="utf-8"))

        assert set(restored) == {"newer", "integer-time", "list-time", "older"}
        assert {entry["game_id"]: entry["created_at"] for entry in persisted} == {
            entry["game_id"]: entry["created_at"] for entry in entries
        }
        assert [
            entry["game_id"] for entry in persisted
            if isinstance(entry["created_at"], str)
        ] == ["older", "newer"]

    def test_manifest_skips_invalid_utf8_log_and_recovers_other_games(self, tmp_path):
        games = tmp_path / "games"
        games.mkdir()
        broken = games / "broken"
        broken.mkdir()
        (broken / "game.log").write_bytes(b"\xff\xfe")
        recovered = games / "recovered"
        recovered.mkdir()
        (recovered / "game.log").write_text(
            json.dumps({
                "operation": "role_init",
                "data": {"players": {"1": {"role": "wolf-killer-villager"}}},
            }) + "\n",
            encoding="utf-8",
        )

        entries = GameManifest(str(tmp_path)).load_or_rebuild()

        assert "broken" not in entries
        assert entries["recovered"]["player_count"] == 1

    @pytest.mark.parametrize(
        "invalid_record",
        ["[]", '"not-an-object"', "null", '{"operation":"role_init","data":[]}'],
    )
    def test_manifest_skips_valid_json_records_with_invalid_shapes(
        self, tmp_path, invalid_record
    ):
        log = tmp_path / "game.log"
        log.write_text(
            invalid_record
            + "\n"
            + json.dumps({
                "timestamp": "t1",
                "operation": "werewolf_kill",
                "data": {"votes": [{"player_seat": 1}, {"player_seat": 2}]},
            })
            + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path))._extract_meta("game", log)

        assert entry["player_count"] == 2

    @pytest.mark.parametrize("invalid_entry", [None, "not-an-object", []])
    def test_manifest_skips_invalid_index_entries_and_keeps_valid_entries(
        self, tmp_path, invalid_entry
    ):
        games = tmp_path / "games"
        games.mkdir()
        valid_entry = {"game_id": "indexed", "config": {"role_counts": {}}}
        (games / "index.json").write_text(
            json.dumps([invalid_entry, valid_entry]), encoding="utf-8"
        )
        recovered = games / "recovered"
        recovered.mkdir()
        (recovered / "game.log").write_text(
            json.dumps({
                "operation": "role_init",
                "data": {
                    "players": {"1": {"role": "wolf-killer-villager"}},
                },
            })
            + "\n",
            encoding="utf-8",
        )

        entries = GameManifest(str(tmp_path)).load_or_rebuild()

        assert entries["indexed"] == valid_entry
        assert entries["recovered"]["config"] == {
            "role_counts": {"wolf-killer-villager": 1},
        }

    def test_manifest_ignores_non_mapping_role_init_players(self, tmp_path):
        log = tmp_path / "game.log"
        log.write_text(
            json.dumps({
                "timestamp": "t1",
                "operation": "role_init",
                "data": {"players": ["not", "a", "mapping"]},
            }) + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path))._extract_meta("game", log)

        assert entry["player_count"] == 0
        assert entry["config"] == {}

    def test_manifest_keeps_valid_role_init_after_malformed_players_record(
        self, tmp_path, caplog
    ):
        log = tmp_path / "game.log"
        log.write_text(
            "\n".join([
                json.dumps({
                    "operation": "role_init",
                    "data": {"players": {
                        "1": {"role": "wolf-killer-werewolf"},
                        "2": {"role": "wolf-killer-villager"},
                    }},
                }),
                json.dumps({
                    "operation": "role_init",
                    "data": {"players": ["not", "a", "mapping"]},
                }),
            ]) + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path))._extract_meta("game", log)

        assert entry["player_count"] == 2
        assert entry["config"] == {"role_counts": {
            "wolf-killer-werewolf": 1,
            "wolf-killer-villager": 1,
        }}
        assert "malformed role_init players" in caplog.text

    def test_manifest_ignores_invalid_phase_changes(self, tmp_path):
        log = tmp_path / "game.log"
        log.write_text(
            "\n".join([
                json.dumps({
                    "operation": "phase_change",
                    "round": 3,
                    "data": {"new_phase": "night"},
                }),
                json.dumps({
                    "operation": "phase_change",
                    "round": 99,
                    "data": {"new_phase": "unknown-phase"},
                }),
                json.dumps({
                    "operation": "phase_change",
                    "round": 100,
                    "data": {"new_phase": None},
                }),
            ]) + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path))._extract_meta("game", log)

        assert entry["phase"] == "night"
        assert entry["round_number"] == 3

    def test_manifest_does_not_persist_partial_role_counts(self, tmp_path):
        log = tmp_path / "game.log"
        log.write_text(
            json.dumps({
                "timestamp": "t1",
                "operation": "role_init",
                "data": {
                    "players": {
                        "1": {"role": "wolf-killer-werewolf"},
                        "2": {"role": None},
                    },
                },
            }) + "\n",
            encoding="utf-8",
        )

        entry = GameManifest(str(tmp_path))._extract_meta("game", log)

        assert entry["player_count"] == 2
        assert entry["config"] == {}

    @pytest.mark.parametrize("index_config", [None, {}])
    def test_manifest_backfills_empty_index_config_from_complete_role_init(
        self, tmp_path, index_config
    ):
        games = tmp_path / "games"
        games.mkdir()
        entry = {
            "game_id": "recovered",
            "created_at": "index-time",
            "player_count": 2,
            "phase": "night",
        }
        if index_config is not None:
            entry["config"] = index_config
        (games / "index.json").write_text(json.dumps([entry]), encoding="utf-8")
        recovered = games / "recovered"
        recovered.mkdir()
        recovered_counts = {
            "wolf-killer-werewolf": 1,
            "wolf-killer-villager": 1,
        }
        (recovered / "game.log").write_text(
            json.dumps({
                "timestamp": "log-time",
                "operation": "role_init",
                "data": {
                    "players": {
                        "1": {"role": "wolf-killer-werewolf"},
                        "2": {"role": "wolf-killer-villager"},
                    },
                },
            }) + "\n",
            encoding="utf-8",
        )

        result = GameManifest(str(tmp_path)).load_or_rebuild()["recovered"]

        assert result["config"] == {"role_counts": recovered_counts}
        assert result["player_count"] == 2
        assert result["created_at"] == "index-time"
        assert result["phase"] == "night"

    @pytest.mark.parametrize(
        "original_config",
        [
            {
                "role_counts": {
                    "wolf-killer-werewolf": 1,
                    "wolf-killer-seer": 0,
                },
            },
            {
                "num_werewolves": 1,
                "num_villagers": 0,
                "num_seers": 0,
                "num_witches": 0,
                "num_hunters": 0,
            },
        ],
    )
    def test_manifest_keeps_valid_zero_role_counts_during_recovery(
        self, tmp_path, original_config
    ):
        games = tmp_path / "games"
        games.mkdir()
        (games / "index.json").write_text(
            json.dumps([{"game_id": "recovered", "config": original_config}]),
            encoding="utf-8",
        )
        recovered = games / "recovered"
        recovered.mkdir()
        (recovered / "game.log").write_text(
            json.dumps({
                "operation": "role_init",
                "data": {
                    "players": {
                        "1": {"role": "wolf-killer-werewolf"},
                        "2": {"role": "wolf-killer-villager"},
                    },
                },
            }) + "\n",
            encoding="utf-8",
        )

        result = GameManifest(str(tmp_path)).load_or_rebuild()["recovered"]

        assert result["config"] == original_config

    @pytest.mark.parametrize(
        "index_config",
        [
            None,
            {},
            [],
            "not-a-config",
            {"role_counts": {}},
            {"role_counts": {"": 1}},
            {"role_counts": {"wolf-killer-villager": True}},
            {"role_counts": {"wolf-killer-villager": -1}},
            {"role_counts": {"wolf-killer-villager": "2"}},
            {
                "role_counts": {"wolf-killer-villager": 2},
                "unexpected": 1,
            },
            {"unexpected": 1},
            {
                "num_werewolves": 1,
                "num_villagers": 0,
                "num_seers": 0,
                "num_witches": 0,
                "num_hunters": True,
            },
        ],
    )
    def test_manifest_backfills_invalid_index_config(self, tmp_path, index_config):
        games = tmp_path / "games"
        games.mkdir()
        (games / "index.json").write_text(
            json.dumps([{
                "game_id": "recovered",
                "player_count": 100,
                "config": index_config,
            }]),
            encoding="utf-8",
        )
        recovered = games / "recovered"
        recovered.mkdir()
        (recovered / "game.log").write_text(
            json.dumps({
                "operation": "role_init",
                "data": {"players": {
                    "1": {"role": "wolf-killer-werewolf"},
                    "2": {"role": "wolf-killer-villager"},
                }},
            }) + "\n",
            encoding="utf-8",
        )

        result = GameManifest(str(tmp_path)).load_or_rebuild()["recovered"]

        assert result["player_count"] == 2
        assert result["config"] == {"role_counts": {
            "wolf-killer-werewolf": 1,
            "wolf-killer-villager": 1,
        }}

    @pytest.mark.parametrize(
        "index_config",
        [
            {"role_counts": {"wolf-killer-villager": 2}},
            {
                "num_werewolves": 1,
                "num_villagers": 3,
                "num_seers": 1,
                "num_witches": 1,
                "num_hunters": 1,
            },
        ],
    )
    def test_manifest_keeps_valid_canonical_or_legacy_index_config(
        self, tmp_path, index_config
    ):
        games = tmp_path / "games"
        games.mkdir()
        (games / "index.json").write_text(
            json.dumps([{
                "game_id": "recovered",
                "player_count": 4,
                "config": index_config,
            }]),
            encoding="utf-8",
        )
        recovered = games / "recovered"
        recovered.mkdir()
        (recovered / "game.log").write_text(
            json.dumps({
                "operation": "role_init",
                "data": {"players": {
                    "1": {"role": "wolf-killer-werewolf"},
                    "2": {"role": "wolf-killer-villager"},
                }},
            }) + "\n",
            encoding="utf-8",
        )

        result = GameManifest(str(tmp_path)).load_or_rebuild()["recovered"]

        assert result["player_count"] == 4
        assert result["config"] == index_config

    @pytest.mark.asyncio
    async def test_phase_change_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        state = service.get_game_state(game_id)
        assert state is not None

        # Simulate phase change event
        await service._on_phase_changed(
            game_id=game_id, phase="night", round_number=1, state=state,
        )
        # Should not crash; state is updated
        assert service.get_game_state(game_id) is not None

    @pytest.mark.asyncio
    async def test_game_over_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        state = service.get_game_state(game_id)

        from app.models.actions import WinResult
        win = WinResult(winning_camp="good", reason="all_wolves_dead")
        await service._on_game_over(game_id=game_id, win_result=win)
        # Should not crash

    @pytest.mark.asyncio
    async def test_phase_changed_handler_none_state(self):
        service = GameService(WSManager(), EventBus())
        service._games = {"known": MagicMock()}
        await service._on_phase_changed(
            game_id="known", phase="night", round_number=1,
        )
        # Should not crash when state is None

    @pytest.mark.asyncio
    async def test_speech_made_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        from app.models.actions import SpeechRecord
        speech = SpeechRecord(player_seat=1, text="test speech", round_number=1)
        await service._on_speech_made(game_id=game_id, speech=speech)
        # Should not crash

    @pytest.mark.asyncio
    async def test_speech_made_handler_none(self):
        service = GameService(WSManager(), EventBus())
        await service._on_speech_made(game_id="missing", speech=None)
        # Should not crash

    @pytest.mark.asyncio
    async def test_vote_cast_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        from app.models.actions import VoteAction
        vote = VoteAction(voter_seat=1, target_seat=3, reasoning="test")
        await service._on_vote_cast(game_id=game_id, vote=vote)
        # Should not crash

    @pytest.mark.asyncio
    async def test_vote_cast_handler_none(self):
        service = GameService(WSManager(), EventBus())
        await service._on_vote_cast(game_id="missing", vote=None)
        # Should not crash

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target_seat", [3, None])
    async def test_vote_cast_event_bus_broadcasts_exact_public_payload_only(
        self, target_seat,
    ):
        from app.models.actions import VoteAction

        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        bus = EventBus()
        service = GameService(ws_manager, bus)
        service._games = {"game-a": MagicMock(round_number=2)}
        vote = VoteAction(
            voter_seat=1,
            target_seat=target_seat,
            reasoning="private reasoning",
            thinking="private chain of thought",
        )

        await bus.publish(BusEvent.VOTE_CAST, game_id="game-a", vote=vote)

        ws_manager.broadcast.assert_awaited_once_with(
            "game-a",
            "vote_cast",
            vote={
                "round_number": 2,
                "voter_seat": 1,
                "target_seat": target_seat,
            },
        )
        assert not {"reasoning", "thinking"} & set(
            ws_manager.broadcast.await_args.kwargs["vote"]
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("game_id", [None, "", "unknown", 7])
    async def test_vote_cast_drops_missing_or_unknown_game_id(self, game_id):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-a": MagicMock(round_number=2)}

        await service._on_vote_cast(
            game_id=game_id,
            vote={"voter_seat": 1, "target_seat": 2},
        )

        ws_manager.broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("round_number", "voter_seat", "target_seat"),
        [
            (0, 1, 2),
            (-1, 1, 2),
            (True, 1, 2),
            ("2", 1, 2),
            (2, 0, 2),
            (2, -1, 2),
            (2, True, 2),
            (2, "1", 2),
            (2, 1, 0),
            (2, 1, -1),
            (2, 1, True),
            (2, 1, "2"),
        ],
    )
    async def test_vote_cast_drops_invalid_public_identifiers(
        self, round_number, voter_seat, target_seat,
    ):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-a": MagicMock(round_number=round_number)}

        await service._on_vote_cast(
            game_id="game-a",
            vote={
                "voter_seat": voter_seat,
                "target_seat": target_seat,
                "reasoning": "private",
            },
        )

        ws_manager.broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("vote", [{}, object()])
    async def test_vote_cast_drops_missing_public_fields(self, vote):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-a": MagicMock(round_number=2)}

        await service._on_vote_cast(game_id="game-a", vote=vote)

        ws_manager.broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vote_cast_drops_vote_whose_fields_cannot_be_read(self):
        class UnreadableVote:
            @property
            def voter_seat(self):
                raise ValueError("untrusted vote")

        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-a": MagicMock(round_number=2)}

        await service._on_vote_cast(game_id="game-a", vote=UnreadableVote())

        ws_manager.broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_player_died_handler_broadcasts_or_ignores_empty_death(self):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-1": MagicMock()}

        await service._on_player_died(game_id="game-1", death={"player_seat": 1})
        await service._on_player_died(game_id="game-1", death=None)

        ws_manager.broadcast.assert_awaited_once_with(
            "game-1", "player_died", death={"player_seat": 1}
        )

    @pytest.mark.asyncio
    async def test_night_substep_broadcasts_exact_public_payload_only(self):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-a": MagicMock()}

        await service._on_night_substep(
            game_id="game-a",
            step="seer_check",
            highlight_seats=[4],
            action_seat=4,
            action={"target_seat": 2, "seer_result": "werewolf"},
            wolf_kill_target=2,
            round_number=3,
        )

        ws_manager.broadcast.assert_awaited_once_with(
            "game-a",
            "night_substep",
            phase="night",
            round_number=3,
            substep="seer_check",
        )
        assert not {
            "action", "action_seat", "highlight_seats", "wolf_kill_target",
            "target_seat", "seer_result", "actor", "role", "result",
        } & set(ws_manager.broadcast.await_args.kwargs)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "step",
        [
            "werewolf_open",
            "werewolf_vote",
            "werewolf_target",
            "werewolf_close",
            "witch_open",
            "witch_action",
            "witch_close",
            "seer_open",
            "seer_check",
            "seer_close",
        ],
    )
    async def test_night_substep_projects_every_engine_public_step(self, step):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-a": MagicMock()}

        await service._on_night_substep(
            game_id="game-a", step=step, round_number=1,
        )

        ws_manager.broadcast.assert_awaited_once_with(
            "game-a", "night_substep",
            phase="night", round_number=1, substep=step,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("game_id", "step", "round_number"),
        [
            (None, "seer_check", 3),
            ("unknown", "seer_check", 3),
            ("game-a", None, 3),
            ("game-a", 42, 3),
            ("game-a", "seer_check", None),
            ("game-a", "seer_check", "3"),
            ("game-a", "seer_check", True),
            ("game-a", "", 3),
            ("game-a", "wolf_secret_target=2", 3),
            ("game-a", "unrecognized_substep", 3),
            ("game-a", "seer_check", 0),
            ("game-a", "seer_check", -1),
        ],
    )
    async def test_night_substep_drops_unknown_or_invalid_envelopes(
        self, game_id, step, round_number,
    ):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-a": MagicMock()}

        await service._on_night_substep(
            game_id=game_id,
            step=step,
            round_number=round_number,
            target_seat=2,
            role="seer",
            result="werewolf",
        )

        ws_manager.broadcast.assert_not_awaited()

    def test_reconstruct_state_handles_missing_log_open_errors_and_invalid_records(self, monkeypatch):
        service = GameService(WSManager(), EventBus())
        monkeypatch.setattr("app.services.game_service.os.path.exists", lambda _: False)
        assert service._reconstruct_state("missing", {}) is None

        monkeypatch.setattr("app.services.game_service.os.path.exists", lambda _: True)
        with patch("builtins.open", side_effect=OSError):
            state = service._reconstruct_state("broken", {})
        assert state is not None

        with patch("builtins.open", mock_open(read_data="not-json\n")):
            state = service._reconstruct_state("invalid", {})
        assert state is not None

    @pytest.mark.asyncio
    async def test_game_over_handler_none(self):
        service = GameService(WSManager(), EventBus())
        await service._on_game_over(game_id="missing", win_result=None)
        # Should not crash

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
        self, callback, payload, message_type,
    ):
        manager = WSManager()
        manager.broadcast = AsyncMock()
        service = GameService(manager, EventBus())
        first = MagicMock()
        first.round_number = 1
        first.get_public_state.return_value = {"game_id": "game-a"}
        second = MagicMock()
        second.get_public_state.return_value = {"game_id": "game-b"}
        service._games = {"game-a": first, "game-b": second}
        service._manifest = MagicMock()

        await getattr(service, callback)(game_id="game-a", **payload)

        assert manager.broadcast.await_count == 1
        assert manager.broadcast.await_args.args[:2] == ("game-a", message_type)
        if callback == "_on_game_over":
            service._manifest.update_game.assert_called_once_with(
                "game-a", phase="game_over", winner="good",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("game_id", [None, "not-registered", "", 42])
    async def test_public_event_with_missing_or_unknown_game_id_is_dropped(
        self, game_id,
    ):
        manager = WSManager()
        manager.broadcast = AsyncMock()
        service = GameService(manager, EventBus())
        service._games = {"game-a": MagicMock(), "game-b": MagicMock()}

        await service._on_player_died(game_id=game_id, death={"player_seat": 2})

        manager.broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mismatched_phase_event_is_dropped_without_persisting_or_broadcasting(self):
        manager = WSManager()
        manager.broadcast = AsyncMock()
        service = GameService(manager, EventBus())
        original = MagicMock()
        mismatched = MagicMock(game_id="game-b")
        service._games = {"game-a": original, "game-b": MagicMock()}
        service._persist_game = MagicMock()

        await service._on_phase_changed(
            game_id="game-a", phase="night", round_number=1, state=mismatched,
        )

        assert service._games["game-a"] is original
        service._persist_game.assert_not_called()
        manager.broadcast.assert_not_awaited()
