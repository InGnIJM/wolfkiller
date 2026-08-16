import pytest
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, mock_open, patch, PropertyMock
from app.services.game_service import GameService
from app.core.game_engine import GameEngine
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.models.game import GamePhase
from app.api.websocket.ws_handler import WSManager
from app.roles.registry import builtin_registry
from app.services.game_manifest import GameManifest


@pytest.fixture(autouse=True)
def _isolated_service_data(tmp_path, monkeypatch):
    """Redirect every GameService's default-relative data writes to a per-test
    temp dir so service tests never create games inside the real data/games
    directory (which the running backend also uses)."""
    import app.services.game_service as service_module

    original_engine = service_module.GameEngine

    def isolated_engine(*args, **kwargs):
        if kwargs.get("data_dir") == "data":
            kwargs["data_dir"] = str(tmp_path)
        return original_engine(*args, **kwargs)

    original_manifest = service_module.GameManifest

    def isolated_manifest(data_dir="data"):
        return original_manifest(data_dir=data_dir if data_dir != "data" else str(tmp_path))

    monkeypatch.setattr(service_module, "GameEngine", isolated_engine)
    monkeypatch.setattr(service_module, "GameManifest", isolated_manifest)


def test_system_prompt_demands_chinese_output() -> None:
    from app.services.game_service import _SYSTEM_PROMPT

    assert "简体中文" in _SYSTEM_PROMPT


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
    async def test_engine_crash_marks_game_error_and_logs(self, monkeypatch):
        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()

        async def boom(self):
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(GameEngine, "start", boom)
        game_id = await service.create_game()
        task = service._tasks[game_id]
        with pytest.raises(RuntimeError, match="engine exploded"):
            await task
        await asyncio.sleep(0)  # let the done callback run

        state = service.get_game_state(game_id)
        assert state is not None
        assert state.phase == GamePhase.ERROR
        service._manifest.update_game.assert_any_call(game_id, phase="error")

    @pytest.mark.asyncio
    async def test_engine_clean_completion_does_not_mark_error(self, monkeypatch):
        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()

        async def run_cleanly(self):
            return None

        monkeypatch.setattr(GameEngine, "start", run_cleanly)
        game_id = await service.create_game()
        await service._tasks[game_id]
        await asyncio.sleep(0)

        state = service.get_game_state(game_id)
        assert state is not None
        assert state.phase != GamePhase.ERROR
        assert all(
            call.kwargs.get("phase") != "error"
            for call in service._manifest.update_game.call_args_list
        )

    @pytest.mark.asyncio
    async def test_engine_crash_with_missing_state_is_ignored(self, monkeypatch):
        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()

        async def boom(self):
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(GameEngine, "start", boom)
        game_id = await service.create_game()
        task = service._tasks[game_id]
        service._games.pop(game_id)  # state vanished before the callback ran
        with pytest.raises(RuntimeError, match="engine exploded"):
            await task
        await asyncio.sleep(0)  # callback must not raise

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
            game_id, {"role_counts": counts}, model_snapshot=[],
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

    @pytest.mark.asyncio
    async def test_night_invoke_requires_text_model_response(self, monkeypatch):
        import app.services.game_service as service_module

        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        fake_llm = MagicMock()
        fake_llm.get_model.return_value.invoke.side_effect = [
            MagicMock(content="今晚刀2号"),
            MagicMock(content=123),
            object(),
        ]
        monkeypatch.setattr(
            service_module, "LLMClient",
            lambda model=None, temperature=None, config=None: fake_llm,
        )

        game_id = await service.create_game(num_werewolves=1, num_villagers=3)

        invoke = service._engines[game_id]._director._invoke
        assert invoke([{"role": "user", "content": "x"}]) == "今晚刀2号"
        with pytest.raises(ValueError):
            invoke([{"role": "user", "content": "x"}])
        assert isinstance(invoke([{"role": "user", "content": "x"}]), str)

    def test_manifest_accepts_canonical_role_counts(self, tmp_path):
        manifest = GameManifest(str(tmp_path))
        counts = {"wolf-killer-werewolf": 1, "wolf-killer-villager": 3}

        manifest.add_game("game-1", {"role_counts": counts})
        (tmp_path / "games" / "game-1").mkdir(parents=True, exist_ok=True)

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
        (tmp_path / "games" / "game-1").mkdir(parents=True, exist_ok=True)

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
        for entry in entries:
            (games / entry["game_id"]).mkdir()

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
        (games / "indexed").mkdir()
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


class TestPipelineSnapshotVersioning:
    def _registry(self):
        return builtin_registry.freeze()

    def test_manifest_persists_pipeline_registry_and_schema_versions(self, tmp_path):
        manifest = GameManifest(str(tmp_path))
        manifest.add_game("v2-game", {"role_counts": {"wolf-killer-villager": 1}})
        manifest.update_game(
            "v2-game",
            phase="night",
            round_number=2,
            pipeline_version="v2",
            registry_digest="abc",
            spec_versions={"wolf-killer-seer": 2},
            effect_schema_version=1,
            state_revision=5,
            last_consistent_checkpoint="checkpoint-id",
        )
        (tmp_path / "games" / "v2-game").mkdir(parents=True, exist_ok=True)

        saved = GameManifest(str(tmp_path)).load_or_rebuild()["v2-game"]
        assert saved["pipeline_version"] == "v2"
        assert saved["registry_digest"] == "abc"
        assert saved["spec_versions"] == {"wolf-killer-seer": 2}
        assert saved["effect_schema_version"] == 1
        assert saved["state_revision"] == 5

    def test_restore_rejects_missing_spec_or_effect_migrator(self):
        from app.models.game import SnapshotVersionError
        from app.services.game_manifest import restore_snapshot
        registry = self._registry()
        with pytest.raises(SnapshotVersionError, match="missing role spec"):
            restore_snapshot(
                {"pipeline_version": "v2", "spec_versions": {"wolf-killer-guard": 9}},
                registry=registry,
            )
        with pytest.raises(SnapshotVersionError, match="missing role spec"):
            restore_snapshot(
                {"pipeline_version": "v2", "spec_versions": {"wolf-killer-unknown": 1}},
                registry=registry,
            )
        with pytest.raises(SnapshotVersionError, match="missing effect schema migrator"):
            restore_snapshot(
                {"pipeline_version": "v2", "effect_schema_version": 7},
                registry=registry,
            )

    def test_v2_game_never_rolls_back_to_v1_after_effect_commit(self):
        from app.models.game import SnapshotVersionError
        from app.services.game_manifest import restore_snapshot
        registry = self._registry()
        with pytest.raises(SnapshotVersionError, match="cannot downgrade"):
            restore_snapshot(
                {"pipeline_version": "v2", "state_revision": 3},
                registry=registry,
                pipeline_mode="v1",
            )
        # A V2 archive with no committed effects may still be replayed.
        assert restore_snapshot(
            {"pipeline_version": "v2", "state_revision": 0},
            registry=registry,
            pipeline_mode="v1",
        )["state_revision"] == 0

    def test_restore_rejects_unknown_pipeline_versions(self):
        from app.models.game import SnapshotVersionError
        from app.services.game_manifest import restore_snapshot
        registry = self._registry()
        with pytest.raises(SnapshotVersionError, match="unknown pipeline version"):
            restore_snapshot({"pipeline_version": "v3"}, registry=registry)
        with pytest.raises(SnapshotVersionError, match="unknown pipeline version"):
            restore_snapshot({}, registry=registry, pipeline_mode="v9")

    def test_legacy_manifest_entry_is_migrated_to_v2(self, tmp_path):
        manifest = GameManifest(str(tmp_path))
        manifest.add_game("legacy", {"role_counts": {"wolf-killer-villager": 1}})
        # Simulate an archive written before pipeline versioning.
        manifest._entries["legacy"].pop("pipeline_version", None)
        (tmp_path / "games" / "legacy").mkdir(parents=True, exist_ok=True)

        entries = GameManifest(str(tmp_path)).load_or_rebuild(registry=self._registry())
        migrated = entries["legacy"]
        assert migrated["pipeline_version"] == "v2"
        assert migrated["effect_schema_version"] == 1
        assert migrated["state_revision"] == 0
        assert set(migrated["spec_versions"]) == set(self._registry().specs)

    def test_incompatible_archive_is_skipped_with_warning(self, tmp_path, caplog):
        manifest = GameManifest(str(tmp_path))
        manifest.add_game("bad", {"role_counts": {"wolf-killer-villager": 1}})
        manifest._entries["bad"]["pipeline_version"] = "v2"
        manifest._entries["bad"]["spec_versions"] = {"wolf-killer-guard": 99}
        manifest._persist()
        (tmp_path / "games" / "bad").mkdir(parents=True, exist_ok=True)

        entries = GameManifest(str(tmp_path)).load_or_rebuild(registry=self._registry())
        assert "bad" not in entries
        assert "incompatible" in caplog.text

    @pytest.mark.asyncio
    async def test_create_game_stamps_pipeline_snapshot_versions(self, monkeypatch):
        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        game_id = await service.create_game(num_werewolves=1, num_villagers=3)
        state = service.get_game_state(game_id)
        assert state.pipeline_version == "v2"
        assert state.effect_schema_version == 1
        assert state.registry_digest
        assert set(state.spec_versions) == set(builtin_registry.freeze().specs)


class TestCommandProvider:
    def _request(self, role_id="wolf-killer-werewolf"):
        from app.models.pipeline import IssuedActionRequest
        contract = builtin_registry.freeze().require(role_id).contracts[0]
        return IssuedActionRequest(1, role_id, contract, 0, 1, "night", "w", "k")

    def _provider(self, service, response_content="kill-ok"):
        from unittest.mock import MagicMock
        from app.core.night_flow import NightDirector
        snapshot = builtin_registry.freeze()
        renderer = MagicMock()
        renderer.render.return_value = "prompt"
        llm = MagicMock()
        if isinstance(response_content, Exception):
            llm.get_model.return_value.invoke.side_effect = response_content
        else:
            response = MagicMock()
            response.content = response_content
            llm.get_model.return_value.invoke.return_value = response
        director = NightDirector(snapshot, lambda messages: None)
        return service._command_provider(snapshot, renderer, lambda seat: llm, director), renderer

    def test_provider_returns_parsed_command(self):
        service = GameService(WSManager(), EventBus())
        provider, _ = self._provider(
            service, '{"action_type":"check","target_seat":2,"reasoning":"x"}'
        )
        from unittest.mock import MagicMock
        context = MagicMock(game_id="g")
        command = provider(self._request("wolf-killer-seer"), context, 0)
        assert command.action_type == "check"
        assert command.target_seat == 2

    def test_provider_falls_back_on_bad_json_or_network_error(self, caplog):
        service = GameService(WSManager(), EventBus())
        from unittest.mock import MagicMock
        with caplog.at_level(logging.WARNING, logger="app.services.game_service"):
            for content in ("not json", "[]", Exception("network")):
                provider, _ = self._provider(service, content)
                command = provider(self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0)
                assert command.action_type == "pass"
                assert command.target_seat is None
        assert any("degrading to safe fallback" in record.message for record in caplog.records)

    def test_provider_falls_back_on_non_text_or_disallowed_action(self):
        service = GameService(WSManager(), EventBus())
        from unittest.mock import MagicMock
        provider, _ = self._provider(service, object())
        assert provider(self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0).action_type == "pass"

        provider, _ = self._provider(
            service, '{"action_type":"vote","target_seat":2,"reasoning":"x"}'
        )
        assert provider(self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0).action_type == "pass"

    def test_provider_renders_game_history_when_engine_exists(self):
        service = GameService(WSManager(), EventBus())
        from unittest.mock import MagicMock
        record = MagicMock(round_number=1, phase="speech", speaker_seat=2, content="大家好")
        engine = MagicMock()
        engine.conversation_log.get_conversations_for_role.return_value = [record]
        service._engines = {"g": engine}
        provider, renderer = self._provider(
            service, '{"action_type":"kill","target_seat":2,"reasoning":"x"}'
        )
        from unittest.mock import MagicMock
        context = MagicMock(game_id="g")
        provider(self._request("wolf-killer-seer"), context, 0)
        renderer.render.assert_called_once()
        history = renderer.render.call_args.args[3]
        assert "大家好" in history

    def test_provider_renders_empty_history_without_engine(self):
        service = GameService(WSManager(), EventBus())
        provider, renderer = self._provider(
            service, '{"action_type":"kill","target_seat":2,"reasoning":"x"}'
        )
        from unittest.mock import MagicMock
        provider(self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0)
        assert renderer.render.call_args.args[3] == ""

    def test_provider_dispatches_collected_wolf_vote_without_llm(self):
        from unittest.mock import MagicMock
        from app.core.night_flow import NightDirector, WolfVote
        from app.models.pipeline import SchedulePoint
        from app.agents.prompt_renderer import PromptRenderer

        def raise_invoke(messages):
            raise AssertionError("director invoke must not be called")

        service = GameService(WSManager(), EventBus())
        snapshot = builtin_registry.freeze()
        director = NightDirector(snapshot, raise_invoke)
        director.record_votes((WolfVote(1, "kill", 2, "怀疑2号"),))

        llm = MagicMock()
        provider = service._command_provider(snapshot, PromptRenderer(), lambda seat: llm, director)

        request = self._request()
        assert request.contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE

        command = provider(request, MagicMock(game_id="g"), 0)
        assert command.action_type == "kill"
        assert command.target_seat == 2
        assert command.reasoning == "怀疑2号"
        llm.get_model.assert_not_called()

    def test_provider_falls_back_when_collected_wolf_vote_missing(self):
        from unittest.mock import MagicMock
        from app.core.night_flow import NightDirector
        from app.agents.prompt_renderer import PromptRenderer

        def raise_invoke(messages):
            raise AssertionError("director invoke must not be called")

        service = GameService(WSManager(), EventBus())
        snapshot = builtin_registry.freeze()
        director = NightDirector(snapshot, raise_invoke)

        llm = MagicMock()
        provider = service._command_provider(snapshot, PromptRenderer(), lambda seat: llm, director)

        request = self._request()
        command = provider(request, MagicMock(game_id="g"), 0)
        assert command.action_type == "pass"
        assert command.target_seat is None
        assert command.reasoning == "safe fallback"
        llm.get_model.assert_not_called()


class TestReconstruction:
    def _write_log(self, tmp_path, game_id, lines):
        import os
        games_dir = os.path.join(str(tmp_path), "data", "games", game_id)
        os.makedirs(games_dir, exist_ok=True)
        with open(os.path.join(games_dir, "game.log"), "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def test_reconstruct_state_reads_full_log(self, tmp_path, monkeypatch):
        self._write_log(tmp_path, "full-log", [
            {"operation": "role_init", "data": {"players": {
                "1": {"role": "wolf-killer-werewolf", "camp": "werewolf", "is_alive": True},
                "2": {"role": "wolf-killer-villager", "camp": "good", "is_alive": True},
            }}},
            {"operation": "night_deaths", "data": {"deaths": [
                {"player_seat": 1, "cause": "wolf_kill", "round_number": 1},
                {"player_seat": 99, "cause": "wolf_kill", "round_number": 1},
            ]}},
            {"operation": "vote_result", "data": {"exiled": 2, "tally": {"2": 1}}},
            {"operation": "vote_result", "data": {"exiled": 99, "tally": {}}},
            {"operation": "game_over", "data": {"winner": "good", "reason": "all_wolves_dead"}},
        ])
        monkeypatch.chdir(str(tmp_path))
        service = GameService(WSManager(), EventBus())
        state = service._reconstruct_state("full-log", {
            "phase": "game_over", "winner": "good", "round_number": 2,
            "player_count": 3,
            "config": {"role_counts": {"wolf-killer-werewolf": 1, "wolf-killer-villager": 2}},
        })
        assert state is not None
        assert state.players[1].is_alive is False
        assert state.players[2].is_alive is False
        assert state.players[3].role == "?"
        assert state.win_result == {"winning_camp": "good", "reason": "all_wolves_dead"}

    def test_reconstruct_state_falls_back_to_seat_events_without_role_init(self, tmp_path, monkeypatch):
        self._write_log(tmp_path, "fallback-log", [
            {"operation": "werewolf_kill", "data": {"votes": [
                {"player_seat": 3}, {"player_seat": None}, {"player_seat": 4},
            ]}, "seat": 5},
        ])
        monkeypatch.chdir(str(tmp_path))
        service = GameService(WSManager(), EventBus())
        state = service._reconstruct_state("fallback-log", {
            "phase": "game_over", "round_number": 1, "player_count": 0,
            "config": {"role_counts": {"wolf-killer-villager": 0}},
        })
        assert state is not None
        assert set(state.players) == {3, 4, 5}
        assert all(player.role == "?" for player in state.players.values())

    def test_load_persisted_games_skips_running_games(self, monkeypatch):
        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        service._manifest.load_or_rebuild.return_value = {
            "running": {"phase": "night"},
            "finished": {"phase": "game_over", "winner": "good"},
        }
        service._reconstruct_state = MagicMock(return_value=MagicMock())
        service._load_persisted_games()
        service._reconstruct_state.assert_called_once_with("finished", {"phase": "game_over", "winner": "good"})

    def test_load_persisted_games_skips_unreconstructable_finished_games(self, monkeypatch):
        service = GameService(WSManager(), EventBus())
        service._games = {}
        service._manifest = MagicMock()
        service._manifest.load_or_rebuild.return_value = {
            "finished": {"phase": "game_over", "winner": "good"},
        }
        service._reconstruct_state = MagicMock(return_value=None)
        service._load_persisted_games()
        assert service._games == {}


class TestManifestVersionEdgeCases:
    def _registry(self):
        return builtin_registry.freeze()

    def test_restore_snapshot_rejects_invalid_shapes(self):
        from app.models.game import SnapshotVersionError
        from app.services.game_manifest import restore_snapshot
        registry = self._registry()
        with pytest.raises(SnapshotVersionError, match="invalid snapshot"):
            restore_snapshot(["not", "a", "mapping"], registry=registry)
        with pytest.raises(SnapshotVersionError, match="invalid spec versions"):
            restore_snapshot({"spec_versions": ["guard"]}, registry=registry)
        for bad_version in (0, True, "1"):
            with pytest.raises(SnapshotVersionError, match="missing role spec"):
                restore_snapshot(
                    {"spec_versions": {"wolf-killer-guard": bad_version}},
                    registry=registry,
                )

    def test_load_ignores_non_list_index_and_entries_without_game_id(self, tmp_path):
        import os
        manifest = GameManifest(str(tmp_path))
        manifest._dir.mkdir(parents=True, exist_ok=True)
        with open(manifest._path, "w", encoding="utf-8") as f:
            json.dump({"not": "a-list"}, f)
        assert GameManifest(str(tmp_path)).load_or_rebuild() == {}

        with open(manifest._path, "w", encoding="utf-8") as f:
            json.dump([{"no-game-id": 1}, {"game_id": "valid"}], f)
        (manifest._dir / "valid").mkdir(parents=True, exist_ok=True)
        entries = GameManifest(str(tmp_path)).load_or_rebuild()
        assert list(entries) == ["valid"]

    def test_load_drops_stale_entries_without_directory(self, tmp_path):
        import os
        manifest = GameManifest(str(tmp_path))
        manifest.add_game("gone", {"role_counts": {"wolf-killer-villager": 1}})
        manifest.add_game("alive", {"role_counts": {"wolf-killer-villager": 1}})
        os.makedirs(os.path.join(str(tmp_path), "games", "alive"))
        entries = GameManifest(str(tmp_path)).load_or_rebuild()
        assert list(entries) == ["alive"]

    def test_load_without_games_dir_returns_empty(self, tmp_path):
        assert GameManifest(str(tmp_path)).load_or_rebuild() == {}

    def test_update_game_unknown_id_is_noop(self, tmp_path):
        manifest = GameManifest(str(tmp_path))
        manifest.update_game("missing", phase="night")  # Should not raise

    def test_update_game_skips_none_fields_for_existing_entry(self, tmp_path):
        manifest = GameManifest(str(tmp_path))
        manifest.add_game("g", {"role_counts": {"wolf-killer-villager": 1}})
        manifest.update_game("g", round_number=3, alive_count=1)
        (tmp_path / "games" / "g").mkdir(parents=True, exist_ok=True)
        entry = GameManifest(str(tmp_path)).load_or_rebuild()["g"]
        assert entry["round_number"] == 3
        assert entry["alive_count"] == 1
        assert entry["phase"] == "waiting"

    def test_rebuild_skips_meta_without_config_when_index_config_invalid(self, tmp_path):
        import os
        manifest = GameManifest(str(tmp_path))
        manifest._entries = {"bare": {
            "game_id": "bare", "phase": "waiting", "round_number": 0,
            "player_count": 0, "config": "invalid-config", "winner": None,
        }}
        manifest._persist()
        games_dir = os.path.join(str(tmp_path), "games", "bare")
        os.makedirs(games_dir)
        with open(os.path.join(games_dir, "game.log"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"operation": "phase_change", "data": {"new_phase": "night"}, "round": 1}) + "\n")
        entries = GameManifest(str(tmp_path)).load_or_rebuild()
        # The extraction backfills only config/player_count when the index
        # config is invalid; an empty extracted config leaves it unchanged.
        assert entries["bare"]["phase"] == "waiting"
        assert entries["bare"]["config"] == "invalid-config"


class TestModelAssignmentIntegration:
    @pytest.mark.asyncio
    async def test_create_game_persists_env_snapshot_and_uses_config_client(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock, patch

        import app.services.game_service as service_module

        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        with patch.object(service_module, "LLMClient") as mock_client:
            game_id = await service.create_game(
                num_werewolves=3, num_villagers=3,
                num_seers=1, num_witches=1, num_hunters=1,
            )

        assert service.get_game_model_snapshot(game_id) == []
        service._manifest.add_game.assert_called_once()
        kwargs = service._manifest.add_game.call_args.kwargs
        assert kwargs["model_snapshot"] == []
        assert any(
            call.kwargs.get("config") is not None
            for call in mock_client.call_args_list
        )
