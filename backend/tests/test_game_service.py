import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, mock_open, patch, PropertyMock
from app.services.game_service import GameService
from app.core.game_engine import GameEngine
from app.core.event_bus import EventBus
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

    @pytest.mark.asyncio
    async def test_phase_change_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        state = service.get_game_state(game_id)
        assert state is not None

        # Simulate phase change event
        await service._on_phase_changed(phase="night", round_number=1, state=state)
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
        await service._on_game_over(win_result=win)
        # Should not crash

    @pytest.mark.asyncio
    async def test_phase_changed_handler_none_state(self):
        service = GameService(WSManager(), EventBus())
        await service._on_phase_changed(phase="night", round_number=1)
        # Should not crash when state is None

    @pytest.mark.asyncio
    async def test_speech_made_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        from app.models.actions import SpeechRecord
        speech = SpeechRecord(player_seat=1, text="test speech", round_number=1)
        await service._on_speech_made(speech=speech)
        # Should not crash

    @pytest.mark.asyncio
    async def test_speech_made_handler_none(self):
        service = GameService(WSManager(), EventBus())
        await service._on_speech_made(speech=None)
        # Should not crash

    @pytest.mark.asyncio
    async def test_vote_cast_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        from app.models.actions import VoteAction
        vote = VoteAction(voter_seat=1, target_seat=3, reasoning="test")
        await service._on_vote_cast(vote=vote)
        # Should not crash

    @pytest.mark.asyncio
    async def test_vote_cast_handler_none(self):
        service = GameService(WSManager(), EventBus())
        await service._on_vote_cast(vote=None)
        # Should not crash

    @pytest.mark.asyncio
    async def test_player_died_handler_broadcasts_or_ignores_empty_death(self):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())
        service._games = {"game-1": MagicMock()}

        await service._on_player_died(death={"player_seat": 1})
        await service._on_player_died(death=None)

        ws_manager.broadcast.assert_awaited_once_with(
            "game-1", "player_died", death={"player_seat": 1}
        )

    @pytest.mark.asyncio
    async def test_night_substep_handler_broadcasts_or_ignores_missing_game_id(self):
        ws_manager = WSManager()
        ws_manager.broadcast = AsyncMock()
        service = GameService(ws_manager, EventBus())

        await service._on_night_substep()
        await service._on_night_substep(
            game_id="game-1",
            step="witch",
            highlight_seats=[2],
            action_seat=1,
            action="save",
            wolf_kill_target=2,
            round_number=3,
        )

        ws_manager.broadcast.assert_awaited_once_with(
            "game-1",
            "night_substep",
            step="witch",
            highlight_seats=[2],
            action_seat=1,
            action="save",
            wolf_kill_target=2,
            round_number=3,
        )

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
        await service._on_game_over(win_result=None)
        # Should not crash
