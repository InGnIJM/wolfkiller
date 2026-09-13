import pytest
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, mock_open, patch, PropertyMock
from app.services.game_service import GameService
from app.core.game_engine import GameEngine
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.models.game import GamePhase
from app.api.websocket.ws_handler import WSManager
from app.roles.registry import builtin_registry
from app.services.game_manifest import GameManifest, default_game_name


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


def test_model_failure_code_detects_nested_timeout() -> None:
    from app.services.game_service import _model_failure_code

    error = RuntimeError("provider request failed")
    error.__cause__ = TimeoutError("timed out")

    assert _model_failure_code(error, None) == "provider_timeout"


def test_model_error_diagnostics_keeps_sanitized_nested_provider_cause() -> None:
    from types import SimpleNamespace

    from httpx import Request, Response
    from openai import BadRequestError

    from app.services.game_service import _model_error_diagnostics

    request = Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    provider_error = BadRequestError(
        "provider returned error",
        response=Response(400, request=request),
        body={
            "error": {
                "message": "Provider returned error",
                "code": 400,
                "metadata": {"provider_name": "Stealth", "raw": "ERROR"},
            },
            "user_id": "must-not-be-persisted",
        },
    )
    timeout = TimeoutError("fallback timed out")
    timeout.__context__ = provider_error
    llm = SimpleNamespace(
        model_name="stealth/ox-alpha",
        provider_profile=SimpleNamespace(profile_id="openrouter"),
    )

    details = _model_error_diagnostics(timeout, llm)

    assert details["failure_code"] == "provider_timeout"
    assert details["cause_chain"] == [{
        "exception_type": "BadRequestError",
        "status_code": 400,
        "error_code": 400,
        "message": "Provider returned error",
        "provider_name": "Stealth",
        "provider_raw": "ERROR",
    }]
    assert "must-not-be-persisted" not in json.dumps(details)


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
        service._manifest.add_game.assert_called_once()
        args = service._manifest.add_game.call_args.args
        kwargs = service._manifest.add_game.call_args.kwargs
        assert args == (
            game_id,
            {"role_counts": counts, "reveal_on_death": False},
        )
        assert kwargs["model_snapshot_version"] == 2
        assert kwargs["name"] is not None
        assert kwargs["model_snapshot"][0]["config_id"] is None
        assert kwargs["model_snapshot"][0]["count"] == 4
        assert kwargs["model_snapshot"][0]["seats"] == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_create_game_accepts_ten_player_standard_board(self, monkeypatch):
        counts = {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        }
        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        game_id = await service.create_game(role_counts=counts)

        state = service.get_game_state(game_id)
        assert state is not None
        assert state.config.role_counts == counts
        assert state.config.total_players == 10

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
    async def test_night_invoke_requires_object_gateway_payload(self, monkeypatch):
        import app.services.game_service as service_module
        from types import SimpleNamespace

        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        fake_llm = MagicMock()
        fake_llm.invoke_json.side_effect = [
            SimpleNamespace(payload={"speak": False}),
            SimpleNamespace(payload=[]),
            object(),
        ]
        monkeypatch.setattr(
            service_module, "LLMClient",
            lambda model=None, temperature=None, config=None: fake_llm,
        )

        game_id = await service.create_game(num_werewolves=1, num_villagers=3)

        invoke = service._engines[game_id]._director._invoke
        schema = {"type": "object"}
        assert json.loads(invoke(
            [{"role": "user", "content": "x"}], "test_night", schema, 1,
        )) == {
            "speak": False,
        }
        with pytest.raises(ValueError):
            invoke([{"role": "user", "content": "x"}], "test_night", schema, 1)
        with pytest.raises(ValueError):
            invoke([{"role": "user", "content": "x"}], "test_night", schema, 1)

    @pytest.mark.asyncio
    async def test_night_invoke_routes_json_through_model_gateway(self, monkeypatch):
        import app.services.game_service as service_module
        from types import SimpleNamespace

        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        fake_llm = MagicMock()
        fake_llm.invoke_json.return_value = SimpleNamespace(
            payload={"speak": False},
        )
        monkeypatch.setattr(
            service_module, "LLMClient",
            lambda model=None, temperature=None, config=None: fake_llm,
        )

        game_id = await service.create_game(num_werewolves=1, num_villagers=3)
        invoke = service._engines[game_id]._director._invoke

        schema = {"type": "object"}
        assert json.loads(invoke(
            [{"role": "user", "content": "x"}], "test_night", schema, 1,
        )) == {
            "speak": False,
        }
        fake_llm.invoke_json.assert_called_once()
        assert fake_llm.invoke_json.call_args.kwargs == {
            "tool_name": "test_night",
            "schema": schema,
        }
        fake_llm.get_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_night_invoke_uses_distinct_wolf_response_contracts(self, monkeypatch):
        import app.services.game_service as service_module
        from app.models.game import GameConfig, GameState, PlayerState
        from types import SimpleNamespace

        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        fake_llm = MagicMock()
        fake_llm.invoke_json.side_effect = [
            SimpleNamespace(payload={
                "speak": True,
                "text": "建议刀2号",
                "preferred_target": 2,
                "day_plan": "明天保持低调",
            }),
            SimpleNamespace(payload={
                "schema_version": 1,
                "action_type": "kill",
                "target_seat": 2,
                "reasoning": "统一刀口",
            }),
        ]
        monkeypatch.setattr(
            service_module, "LLMClient",
            lambda model=None, temperature=None, config=None: fake_llm,
        )

        game_id = await service.create_game(num_werewolves=1, num_villagers=3)
        engine = service._engines[game_id]
        state = GameState(game_id=game_id, config=GameConfig())
        state.round_number = 1
        state.players = {
            1: PlayerState(
                seat_number=1, role="wolf-killer-werewolf", camp="werewolf",
            ),
            2: PlayerState(
                seat_number=2, role="wolf-killer-villager", camp="good",
            ),
        }
        wolf_seat = 1

        discussion = engine._director.wolf_discussion_turn(state, wolf_seat, [])
        vote = engine._director.wolf_vote_turn(state, wolf_seat, [], [])

        assert discussion.spoke is True
        assert vote.target_seat == 2
        discussion_call, vote_call = fake_llm.invoke_json.call_args_list
        assert discussion_call.kwargs["tool_name"] == "werewolf_discussion"
        assert discussion_call.kwargs["schema"] == {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "speak": {"type": "boolean"},
                "text": {"type": "string", "maxLength": 200},
                "preferred_target": {"type": ["integer", "null"]},
                "day_plan": {"type": "string", "maxLength": 150},
            },
            "required": ["speak", "text", "preferred_target", "day_plan"],
        }
        assert vote_call.kwargs["tool_name"] == "werewolf_kill"
        assert vote_call.kwargs["schema"]["additionalProperties"] is False
        assert vote_call.kwargs["schema"]["required"] == [
            "schema_version", "action_type", "target_seat", "reasoning",
        ]

    @pytest.mark.asyncio
    async def test_night_invoke_persists_wolf_model_error_with_seat(
        self, monkeypatch, tmp_path,
    ):
        import app.services.game_service as service_module
        from httpx import Request, Response
        from openai import BadRequestError

        service = GameService(WSManager(), EventBus())
        service._manifest = MagicMock()
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        fake_llm = MagicMock()
        fake_llm.model_name = "stealth/ox-alpha"
        fake_llm.provider_profile.profile_id = "openrouter"
        request = Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        fake_llm.invoke_json.side_effect = BadRequestError(
            "provider returned error",
            response=Response(400, request=request),
            body={
                "error": {
                    "message": "Provider returned error",
                    "code": 400,
                    "metadata": {"provider_name": "Stealth", "raw": "ERROR"},
                },
            },
        )
        monkeypatch.setattr(
            service_module, "LLMClient",
            lambda model=None, temperature=None, config=None: fake_llm,
        )

        game_id = await service.create_game(num_werewolves=1, num_villagers=3)
        engine = service._engines[game_id]
        engine.state.round_number = 1
        engine.state.players = {
            1: service_module.PlayerState(
                seat_number=1, role="wolf-killer-werewolf", camp="werewolf",
            ),
            2: service_module.PlayerState(
                seat_number=2, role="wolf-killer-villager", camp="good",
            ),
        }

        vote = engine._director.wolf_vote_turn(engine.state, 1, [], [])

        assert vote.reasoning == "系统异常，本轮未行动"
        log_path = tmp_path / "games" / game_id / "game.log"
        assert log_path.exists(), "wolf model error should be persisted"
        record = json.loads(log_path.read_text("utf-8").splitlines()[-1])
        assert record["operation"] == "model_error"
        assert record["seat"] == 1
        assert record["data"]["contract_id"] == "werewolf_kill"
        assert record["data"]["schedule_point"] == "night_wolf_vote"
        assert record["data"]["status_code"] == 400

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

        assert entry["config"] == {
            "role_counts": recovered_counts, "reveal_on_death": False,
        }

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
            "reveal_on_death": False,
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

    def test_manifest_extracts_reveal_on_death_from_game_config_record(self, tmp_path):
        games = tmp_path / "games"
        games.mkdir()
        log = games / "flagged"
        log.mkdir()
        (log / "game.log").write_text(
            json.dumps({
                "operation": "game_config",
                "data": {"role_counts": {"wolf-killer-villager": 1}, "reveal_on_death": True},
            }) + "\n"
            + json.dumps({
                "operation": "role_init",
                "data": {"players": {"1": {"role": "wolf-killer-villager"}}},
            }) + "\n",
            encoding="utf-8",
        )
        log2 = games / "unparsed"
        log2.mkdir()
        (log2 / "game.log").write_text(
            json.dumps({
                "operation": "game_config",
                "data": {"reveal_on_death": "yes"},
            }) + "\n"
            + json.dumps({
                "operation": "role_init",
                "data": {"players": {"1": {"role": "wolf-killer-villager"}}},
            }) + "\n",
            encoding="utf-8",
        )

        entries = GameManifest(str(tmp_path)).load_or_rebuild()

        assert entries["flagged"]["config"] == {
            "role_counts": {"wolf-killer-villager": 1},
            "reveal_on_death": True,
        }
        assert entries["unparsed"]["config"] == {
            "role_counts": {"wolf-killer-villager": 1},
            "reveal_on_death": False,
        }

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
        }, "reveal_on_death": False}
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

        assert result["config"] == {
            "role_counts": recovered_counts, "reveal_on_death": False,
        }
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
        }, "reveal_on_death": False}

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


def test_default_game_name_formats_shanghai_time_without_padding_month_day():
    utc = datetime(2026, 8, 22, 13, 50, tzinfo=timezone.utc)
    assert default_game_name(8, now=utc) == "8人局 · 8月22日 21:50"


def test_default_game_name_treats_naive_datetime_as_shanghai():
    naive = datetime(2026, 1, 5, 9, 5)
    assert default_game_name(9, now=naive) == "9人局 · 1月5日 09:05"


def test_manifest_stores_and_updates_name(tmp_path):
    manifest = GameManifest(str(tmp_path))
    manifest.add_game(
        "game-1",
        {"role_counts": {"wolf-killer-villager": 3}},
        name="开局名",
    )
    (tmp_path / "games" / "game-1").mkdir(parents=True)

    assert manifest.get_entry("game-1")["name"] == "开局名"
    manifest.update_game("game-1", name="新名字")
    restored = GameManifest(str(tmp_path)).load_or_rebuild()["game-1"]
    assert restored["name"] == "新名字"


def test_manifest_remove_game_drops_entry_and_persists(tmp_path):
    manifest = GameManifest(str(tmp_path))
    manifest.add_game("game-1", {"role_counts": {"wolf-killer-villager": 3}}, name="A")
    (tmp_path / "games" / "game-1").mkdir(parents=True)
    manifest.remove_game("game-1")
    manifest.remove_game("missing")

    assert manifest.get_entry("game-1") is None
    assert GameManifest(str(tmp_path)).load_or_rebuild() == {}


class TestGameDisplayName:
    def _service(self, tmp_path):
        return GameService(WSManager(), EventBus(), data_dir=str(tmp_path))

    def test_get_display_name_falls_back_to_short_id(self, tmp_path):
        service = self._service(tmp_path)
        service._games["abcd1234"] = MagicMock()
        service._manifest.add_game(
            "abcd1234", {"role_counts": {"wolf-killer-villager": 3}},
        )
        assert service.get_display_name("abcd1234") == "abcd1234"
        assert service.get_display_name("missing") == "missing"
        service._manifest._entries["abcd1234"]["name"] = "   "
        assert service.get_display_name("abcd1234") == "abcd1234"
        service._manifest._entries["abcd1234"]["name"] = 1
        assert service.get_display_name("abcd1234") == "abcd1234"

    def test_rename_game_updates_manifest(self, tmp_path):
        service = self._service(tmp_path)
        service._games["game-1"] = MagicMock()
        service._manifest.add_game(
            "game-1", {"role_counts": {"wolf-killer-villager": 3}}, name="旧名",
        )
        service.rename_game("game-1", "新名字")
        assert service.get_display_name("game-1") == "新名字"

    def test_rename_missing_game_raises_key_error(self, tmp_path):
        service = self._service(tmp_path)
        with pytest.raises(KeyError):
            service.rename_game("missing", "名字")

    @pytest.mark.asyncio
    async def test_create_game_writes_default_name(self, tmp_path):
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        game_id = await service.create_game(
            num_werewolves=1, num_villagers=3, num_seers=0, num_witches=0, num_hunters=0,
        )
        name = service.get_display_name(game_id)
        assert name.startswith("4人局 · ")
        assert "月" in name and "日" in name


class TestDeleteGame:
    def _seed(self, tmp_path, game_id="game-1", phase=GamePhase.GAME_OVER):
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        game_dir = tmp_path / "games" / game_id
        game_dir.mkdir(parents=True)
        (game_dir / "game.log").write_text("{}\n", encoding="utf-8")
        state = MagicMock()
        state.phase = phase
        state.game_id = game_id
        service._games[game_id] = state
        service._manifest.add_game(
            game_id, {"role_counts": {"wolf-killer-villager": 3}}, name="待删",
        )
        service._model_snapshots[game_id] = [{"name": "m"}]
        return service, game_dir

    @pytest.mark.asyncio
    async def test_delete_completed_game_removes_dir_and_index(self, tmp_path):
        service, game_dir = self._seed(tmp_path)
        await service.delete_game("game-1")
        assert "game-1" not in service.list_games()
        assert not game_dir.exists()
        assert service._manifest.get_entry("game-1") is None
        assert "game-1" not in service._model_snapshots

    @pytest.mark.asyncio
    async def test_delete_running_game_stops_engine_and_cancels_task(self, tmp_path):
        service, game_dir = self._seed(tmp_path, phase=GamePhase.NIGHT)
        engine = MagicMock()
        engine.stop = AsyncMock()
        service._engines["game-1"] = engine

        async def hang():
            await asyncio.sleep(3600)

        task = asyncio.create_task(hang())
        service._tasks["game-1"] = task

        await service.delete_game("game-1")

        engine.stop.assert_awaited_once()
        assert task.cancelled() or task.done()
        assert not game_dir.exists()
        assert "game-1" not in service._engines
        assert "game-1" not in service._tasks

    @pytest.mark.asyncio
    async def test_delete_closes_each_seat_client_only_once(self, tmp_path):
        service, _ = self._seed(tmp_path)
        client = MagicMock()
        client.aclose = AsyncMock()
        service._llm_clients["game-1"] = {1: client, 2: client}

        await service.delete_game("game-1")
        await service.aclose()

        client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_missing_game_raises_key_error(self, tmp_path):
        service, _ = self._seed(tmp_path)
        with pytest.raises(KeyError):
            await service.delete_game("missing")

    @pytest.mark.asyncio
    async def test_delete_keeps_memory_when_rmtree_fails(self, tmp_path, monkeypatch):
        service, game_dir = self._seed(tmp_path)
        monkeypatch.setattr(
            "app.services.game_service.shutil.rmtree",
            lambda path: (_ for _ in ()).throw(OSError("busy")),
        )
        with pytest.raises(OSError, match="busy"):
            await service.delete_game("game-1")
        assert "game-1" in service._games
        assert game_dir.exists()

    @pytest.mark.asyncio
    async def test_delete_continues_after_engine_wait_timeout(self, tmp_path, monkeypatch):
        service, game_dir = self._seed(tmp_path, phase=GamePhase.NIGHT)
        engine = MagicMock()
        engine.stop = AsyncMock()
        service._engines["game-1"] = engine
        task = MagicMock()
        task.done.return_value = False
        service._tasks["game-1"] = task

        async def boom_wait(awaitable, timeout=None):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(asyncio, "wait_for", boom_wait)
        await service.delete_game("game-1")
        task.cancel.assert_called_once()
        assert not game_dir.exists()
        assert "game-1" not in service._games

    @pytest.mark.asyncio
    async def test_delete_skips_finished_task_and_missing_directory(self, tmp_path):
        service, game_dir = self._seed(tmp_path)
        task = MagicMock()
        task.done.return_value = True
        service._tasks["game-1"] = task
        import shutil
        shutil.rmtree(game_dir)

        await service.delete_game("game-1")

        task.cancel.assert_not_called()
        assert "game-1" not in service._games


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
    def _request(self, role_id="wolf-killer-werewolf", actor_seat=1):
        from app.models.pipeline import IssuedActionRequest
        contract = builtin_registry.freeze().require(role_id).contracts[0]
        return IssuedActionRequest(
            actor_seat, role_id, contract, 0, 1, "night", "w", "k",
        )

    def _provider(self, service, response_content="kill-ok"):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from app.agents.output_parser import extract_json_object
        from app.core.night_flow import NightDirector
        snapshot = builtin_registry.freeze()
        renderer = MagicMock()
        renderer.render.return_value = "prompt"
        llm = MagicMock()
        if isinstance(response_content, Exception):
            llm.invoke_action.side_effect = response_content
        else:
            payload = extract_json_object(response_content)
            if isinstance(payload, dict):
                llm.invoke_action.return_value = SimpleNamespace(payload=payload)
            else:
                llm.invoke_action.side_effect = ValueError(
                    "model gateway rejected structured response"
                )
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

    def test_provider_routes_action_through_model_gateway(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from app.core.night_flow import NightDirector

        service = GameService(WSManager(), EventBus())
        snapshot = builtin_registry.freeze()
        llm = MagicMock()
        llm.invoke_action.return_value = SimpleNamespace(payload={
            "schema_version": 1,
            "action_type": "check",
            "target_seat": 2,
            "reasoning": "x",
        })
        director = NightDirector(snapshot, lambda messages: None)
        renderer = MagicMock()
        renderer.render.return_value = "prompt"
        provider = service._command_provider(
            snapshot, renderer, lambda seat: llm, director,
        )

        command = provider(
            self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0,
        )

        assert command.action_type == "check"
        assert command.target_seat == 2
        llm.invoke_action.assert_called_once()
        llm.get_model.assert_not_called()

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

    def _provider_with_llm(self, service, llm):
        from unittest.mock import MagicMock
        from app.core.night_flow import NightDirector
        snapshot = builtin_registry.freeze()
        renderer = MagicMock()
        renderer.render.return_value = "prompt"
        director = NightDirector(snapshot, lambda messages: None)
        return service._command_provider(
            snapshot, renderer, lambda seat: llm, director,
        )

    def test_provider_retries_transient_timeout_and_recovers(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from app.agents.output_parser import extract_json_object

        class ReadTimeout(Exception):
            pass

        service = GameService(WSManager(), EventBus())
        llm = MagicMock()
        payload = extract_json_object(
            '{"action_type":"pass","target_seat":null,"reasoning":"ok"}'
        )
        llm.invoke_action.side_effect = [
            ReadTimeout("provider read timed out"),
            SimpleNamespace(payload=payload),
        ]
        provider = self._provider_with_llm(service, llm)

        command = provider(self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0)

        assert command.action_type == "pass"
        assert llm.invoke_action.call_count == 2

    def test_provider_routes_actor_seat_and_keeps_retry_on_same_client(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from app.agents.output_parser import extract_json_object
        from app.core.night_flow import NightDirector

        class ReadTimeout(Exception):
            pass

        service = GameService(WSManager(), EventBus())
        snapshot = builtin_registry.freeze()
        renderer = MagicMock()
        renderer.render.return_value = "prompt"
        clients = {1: MagicMock(), 2: MagicMock()}
        clients[2].invoke_action.side_effect = [
            ReadTimeout("provider read timed out"),
            SimpleNamespace(payload=extract_json_object(
                '{"action_type":"check","target_seat":1,"reasoning":"ok"}'
            )),
        ]
        requested_seats = []

        def client_provider(seat):
            requested_seats.append(seat)
            return clients[seat]

        provider = service._command_provider(
            snapshot,
            renderer,
            client_provider,
            NightDirector(snapshot, lambda *args: None),
        )

        command = provider(
            self._request("wolf-killer-seer", actor_seat=2),
            MagicMock(game_id="g"),
            0,
        )

        assert command.action_type == "check"
        assert command.target_seat == 1
        assert requested_seats == [2]
        assert clients[2].invoke_action.call_count == 2
        clients[1].invoke_action.assert_not_called()

    def test_provider_retries_once_then_falls_back_on_persistent_timeout(self):
        class APITimeoutError(Exception):
            pass

        service = GameService(WSManager(), EventBus())
        llm = MagicMock()
        llm.invoke_action.side_effect = APITimeoutError("provider timeout")
        provider = self._provider_with_llm(service, llm)

        command = provider(self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0)

        assert command.action_type == "pass"
        assert command.reasoning == "系统异常，本轮未行动"
        assert llm.invoke_action.call_count == 2

    def test_provider_retries_rate_limit_error_by_status_code(self):
        import httpx
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from app.agents.output_parser import extract_json_object
        from openai import RateLimitError

        service = GameService(WSManager(), EventBus())
        llm = MagicMock()
        payload = extract_json_object(
            '{"action_type":"check","target_seat":2,"reasoning":"ok"}'
        )
        llm.invoke_action.side_effect = [
            RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://model.test")
                ),
                body={},
            ),
            SimpleNamespace(payload=payload),
        ]
        provider = self._provider_with_llm(service, llm)

        command = provider(self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0)

        assert command.action_type == "check"
        assert command.target_seat == 2
        assert llm.invoke_action.call_count == 2

    def test_provider_logs_fallback_when_model_error_persistence_fails(self, caplog):
        import logging
        from unittest.mock import MagicMock

        service = GameService(WSManager(), EventBus())
        llm = MagicMock()
        llm.model_name = "stealth/ox-alpha"
        llm.provider_profile.profile_id = "openrouter"
        llm.invoke_action.side_effect = RuntimeError("client bug")
        engine = MagicMock()
        engine.game_logger.log_model_error.side_effect = RuntimeError("disk full")
        service._engines["g"] = engine
        provider = self._provider_with_llm(service, llm)

        command = provider(self._request("wolf-killer-seer"), MagicMock(game_id="g"), 0)

        assert command.action_type == "pass"
        assert command.reasoning == "系统异常，本轮未行动"
        assert any(
            "Failed to persist model error" in record.message
            for record in caplog.records
        )

    def test_provider_persists_sanitized_model_error_in_game_log(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from httpx import Request, Response
        from openai import BadRequestError

        from app.core.game_logger import GameLogger
        from app.core.night_flow import NightDirector

        service = GameService(WSManager(), EventBus())
        snapshot = builtin_registry.freeze()
        renderer = MagicMock()
        renderer.render.return_value = "prompt"
        llm = MagicMock()
        llm.model_name = "stealth/ox-alpha"
        llm.provider_profile.profile_id = "openrouter"
        request = Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        llm.invoke_action.side_effect = BadRequestError(
            "provider returned error",
            response=Response(400, request=request),
            body={
                "error": {
                    "message": "Provider returned error",
                    "code": 400,
                    "metadata": {
                        "provider_name": "Stealth",
                        "raw": "ERROR",
                    },
                },
                "user_id": "must-not-be-persisted",
            },
        )
        engine = SimpleNamespace(
            conversation_log=MagicMock(),
            game_logger=GameLogger(data_dir=str(tmp_path)),
            _rng=random.Random(7),
        )
        engine.conversation_log.get_conversations_for_role.return_value = []
        service._engines = {"g": engine}
        director = NightDirector(snapshot, lambda messages: None)
        provider = service._command_provider(
            snapshot, renderer, lambda seat: llm, director,
        )
        context = MagicMock(
            game_id="g", round_number=1, phase="night", facts={},
        )

        command = provider(self._request("wolf-killer-guard"), context, 0)

        assert command.action_type == "pass"
        log_path = tmp_path / "games" / "g" / "game.log"
        assert log_path.exists(), "model error should be persisted per game"
        record = json.loads(log_path.read_text("utf-8").splitlines()[-1])
        assert record["operation"] == "model_error"
        assert record["seat"] == 1
        assert record["data"] == {
            "contract_id": "guard_action",
            "schedule_point": "night_action",
            "attempt": 0,
            "provider_profile": "openrouter",
            "provider_name": "Stealth",
            "model_id": "stealth/ox-alpha",
            "failure_code": "provider_bad_request",
            "exception_type": "BadRequestError",
            "status_code": 400,
            "error_code": 400,
            "message": "Provider returned error",
            "provider_raw": "ERROR",
        }
        assert "must-not-be-persisted" not in log_path.read_text("utf-8")
        error_log_path = tmp_path / "games" / "g" / "model_errors.log"
        assert error_log_path.exists(), "model traceback should be persisted per game"
        error_record = json.loads(
            error_log_path.read_text("utf-8").splitlines()[-1]
        )
        assert error_record["operation"] == "model_error"
        assert any(
            frame["function"] == "provider"
            for frame in error_record["data"]["stack"]
        )
        assert "must-not-be-persisted" not in error_log_path.read_text("utf-8")

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

    def test_provider_injects_server_random_hint_into_rendered_context(self):
        from unittest.mock import MagicMock
        from app.models.pipeline import ActionContext
        service = GameService(WSManager(), EventBus())
        provider, renderer = self._provider(
            service, '{"action_type":"check","target_seat":2,"reasoning":"x"}'
        )
        snapshot = builtin_registry.freeze()
        contract = snapshot.require("wolf-killer-seer").contracts[0]
        context = ActionContext(
            game_id="g", revision=0,
            facts={"alive_seats": (1, 2, 3, 4, 5)},
            contract_id=contract.contract_id,
            contract_version=contract.schema_version,
            contract_digest=contract.stable_digest(),
            round_number=1, phase="night", window_id="w",
            schedule_point=contract.schedule_point,
            actor_seat=9, actor_role_id="wolf-killer-seer",
            actor_alive=True, action_key="k",
        )
        provider(self._request("wolf-killer-seer"), context, 0)
        rendered_context = renderer.render.call_args.args[2]
        hint = rendered_context.facts["RANDOM_HINT"]
        assert hint in {1, 2, 3, 4, 5}

    def test_provider_omits_random_hint_when_prior_round_has_evidence(self):
        from app.models.pipeline import ActionContext
        service = GameService(WSManager(), EventBus())
        provider, renderer = self._provider(
            service, '{"action_type":"check","target_seat":2,"reasoning":"x"}'
        )
        contract = builtin_registry.freeze().require("wolf-killer-seer").contracts[0]
        context = ActionContext(
            game_id="g", revision=0, facts={"alive_seats": (1, 2, 3)},
            contract_id=contract.contract_id, contract_version=contract.schema_version,
            contract_digest=contract.stable_digest(), round_number=2, phase="night",
            window_id="w", schedule_point=contract.schedule_point, actor_seat=9,
            actor_role_id="wolf-killer-seer", actor_alive=True, action_key="k",
        )

        provider(self._request("wolf-killer-seer"), context, 0)

        assert "RANDOM_HINT" not in renderer.render.call_args.args[2].facts

    def test_provider_never_injects_random_hint_for_witch_potions(self):
        from app.models.pipeline import ActionContext

        service = GameService(WSManager(), EventBus())
        provider, renderer = self._provider(
            service, '{"action_type":"pass","target_seat":null,"reasoning":"没有依据"}'
        )
        contract = builtin_registry.freeze().require("wolf-killer-witch").contracts[0]
        context = ActionContext(
            game_id="g", revision=0,
            facts={"alive_seats": (1, 2, 3), "wolf_kill_target": 2},
            contract_id=contract.contract_id, contract_version=contract.schema_version,
            contract_digest=contract.stable_digest(), round_number=1, phase="night",
            window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
            actor_role_id="wolf-killer-witch", actor_alive=True,
            resources={"antidote": 1, "poison": 1}, action_key="k",
        )

        provider(self._request("wolf-killer-witch"), context, 0)

        assert "RANDOM_HINT" not in renderer.render.call_args.args[2].facts

    def test_fallback_target_requires_tuple_seats_and_targeting_contract(self):
        from unittest.mock import MagicMock

        service = GameService(WSManager(), EventBus())
        context = MagicMock(round_number=1, facts={"alive_seats": [1, 2]})
        assert service._fallback_target(self._request("wolf-killer-seer"), context, "") is None

        context = MagicMock(round_number=1, facts={"alive_seats": (1, 2)})
        request = MagicMock()
        request.contract.actions_requiring_target = frozenset()
        assert service._fallback_target(request, context, "") is None

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
        assert command.reasoning == "系统异常，本轮未行动"
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

    def test_reconstruct_state_ignores_records_without_discoverable_seats(
        self, tmp_path, monkeypatch,
    ):
        self._write_log(tmp_path, "empty-fallback", [
            {"operation": "phase_change", "data": {}, "seat": None},
        ])
        monkeypatch.chdir(str(tmp_path))
        service = GameService(WSManager(), EventBus())

        state = service._reconstruct_state("empty-fallback", {
            "phase": "game_over", "round_number": 1, "player_count": 0,
            "config": {"role_counts": {"wolf-killer-villager": 0}},
        })

        assert state is not None
        assert state.players == {}

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

        model_snapshot = service.get_game_model_snapshot(game_id)
        assert len(model_snapshot) == 1
        assert model_snapshot[0]["config_id"] is None
        assert model_snapshot[0]["count"] == 9
        assert model_snapshot[0]["seats"] == list(range(1, 10))
        service._manifest.add_game.assert_called_once()
        kwargs = service._manifest.add_game.call_args.kwargs
        assert kwargs["model_snapshot"] == model_snapshot
        assert kwargs["model_snapshot_version"] == 2
        assert any(
            call.kwargs.get("config") is not None
            for call in mock_client.call_args_list
        )

    @pytest.mark.asyncio
    async def test_clients_are_created_once_per_seat_and_reused_by_roles_and_night(
        self, monkeypatch, tmp_path,
    ):
        from types import SimpleNamespace

        import app.services.game_service as service_module
        from app.agents.llm_client import LLMClientConfig

        def config(model_id: str) -> LLMClientConfig:
            return LLMClientConfig(
                base_url=f"https://{model_id}.test/v1",
                api_key=f"secret-{model_id}",
                model_id=model_id,
                temperature=0.1,
                max_tokens=100,
                strict_base_url=f"https://{model_id}.test/v1",
            )

        seat_configs = {
            1: config("model-a"),
            2: config("model-b"),
            3: config("model-a"),
            4: config("model-b"),
        }
        snapshot = [
            {
                "config_id": "a", "name": "A", "model_id": "model-a",
                "base_url": "https://model-a.test/v1",
                "provider_profile": "custom-openai", "count": 2,
                "seats": [1, 3],
            },
            {
                "config_id": "b", "name": "B", "model_id": "model-b",
                "base_url": "https://model-b.test/v1",
                "provider_profile": "custom-openai", "count": 2,
                "seats": [2, 4],
            },
        ]

        class FakeClient:
            instances = []

            def __init__(self, *, config):
                self.config = config
                self.model_name = config.model_id
                self.provider_profile = SimpleNamespace(profile_id="custom-openai")
                self.supports_strict_actions = False
                self.supports_action_tools = False
                self.json_calls = []
                self.async_json_calls = []
                self.model_calls = []
                self.close_count = 0
                self.instances.append(self)

            def invoke_json(self, messages, **kwargs):
                self.json_calls.append((messages, kwargs))
                return SimpleNamespace(payload={"speak": False})

            async def ainvoke_json(self, messages, **kwargs):
                self.async_json_calls.append((messages, kwargs))
                return SimpleNamespace(payload={"text": "我是2号，今天先听大家发言再判断。"})

            def get_model(self):
                owner = self

                class Model:
                    async def ainvoke(self, messages):
                        owner.model_calls.append(messages)
                        return SimpleNamespace(content=json.dumps({
                            "action_type": "vote",
                            "target_seat": 1,
                            "reasoning": "根据白天发言投票",
                        }, ensure_ascii=False))

                return Model()

            async def aclose(self):
                self.close_count += 1

        monkeypatch.setattr(
            service_module,
            "resolve_model_assignments",
            lambda assignments, total: (seat_configs, snapshot),
        )
        monkeypatch.setattr(service_module, "LLMClient", FakeClient)
        monkeypatch.setattr(GameEngine, "start", AsyncMock())

        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        service._manifest = MagicMock()
        game_id = await service.create_game(
            role_counts={
                "wolf-killer-werewolf": 1,
                "wolf-killer-villager": 3,
            },
            model_assignments=[
                {"config_id": "a", "count": 2},
                {"config_id": "b", "count": 2},
            ],
        )

        clients = {seat: FakeClient.instances[seat - 1] for seat in range(1, 5)}
        engine = service._engines[game_id]
        assert len(FakeClient.instances) == 4
        assert all(
            engine.roles[seat].llm_client is clients[seat]
            for seat in range(1, 5)
        )

        invoke = engine._director._invoke
        assert json.loads(invoke(
            [{"role": "user", "content": "seat two"}], "night_test", {}, 2,
        )) == {"speak": False}
        assert clients[2].json_calls
        assert not clients[1].json_calls

        engine._assign_roles()
        engine.state.phase = GamePhase.SPEECH
        speech = await engine.speak(2, "day_speech")
        assert speech == "我是2号，今天先听大家发言再判断。"
        assert clients[2].async_json_calls
        assert not clients[1].async_json_calls

        engine.state.phase = GamePhase.VOTE_CASTING
        vote = await engine.vote(2)
        assert vote is not None
        assert vote.target_seat == 1
        assert clients[2].model_calls
        assert not clients[1].model_calls

        log_path = tmp_path / "games" / game_id / "game.log"
        assignment_record = json.loads(log_path.read_text("utf-8").splitlines()[0])
        assert assignment_record["operation"] == "model_assignment"
        assert assignment_record["data"] == {
            "model_snapshot_version": 2,
            "model_snapshot": snapshot,
        }
        assert "secret-" not in log_path.read_text("utf-8")

        await service._tasks[game_id]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert [client.close_count for client in FakeClient.instances] == [1, 1, 1, 1]
        await service.aclose()
        assert [client.close_count for client in FakeClient.instances] == [1, 1, 1, 1]

    @pytest.mark.asyncio
    async def test_partial_client_construction_failure_closes_and_leaves_no_game(
        self, monkeypatch, tmp_path,
    ):
        import app.services.game_service as service_module
        from app.agents.llm_client import LLMClientConfig

        client_config = LLMClientConfig(
            base_url="https://model.test/v1",
            api_key="secret",
            model_id="model",
            temperature=0.1,
            max_tokens=100,
            strict_base_url="https://model.test/v1",
        )

        class SharedClient:
            def __init__(self):
                self.close_count = 0

            async def aclose(self):
                self.close_count += 1

        shared_client = SharedClient()
        construction_count = 0

        def failing_client_factory(*, config):
            nonlocal construction_count
            construction_count += 1
            if construction_count == 3:
                raise RuntimeError("client construction failed")
            return shared_client

        monkeypatch.setattr(
            service_module,
            "resolve_model_assignments",
            lambda assignments, total: (
                {1: client_config, 2: client_config, 3: client_config},
                [{
                    "config_id": None, "name": "env", "model_id": "model",
                    "base_url": "https://model.test/v1", "count": 3,
                    "provider_profile": "custom-openai", "seats": [1, 2, 3],
                }],
            ),
        )
        monkeypatch.setattr(service_module, "LLMClient", failing_client_factory)
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))

        with pytest.raises(RuntimeError, match="construction failed"):
            await service.create_game(
                role_counts={
                    "wolf-killer-werewolf": 1,
                    "wolf-killer-villager": 2,
                },
            )

        assert shared_client.close_count == 1
        assert service._games == {}
        assert service._engines == {}
        assert service._tasks == {}
        assert service._model_snapshots == {}
        assert service._llm_clients == {}
        assert not (tmp_path / "games").exists()

    @pytest.mark.asyncio
    async def test_client_cleanup_is_idempotent_across_concurrent_lifecycle_paths(
        self, tmp_path,
    ):
        client = MagicMock()
        client.aclose = AsyncMock()
        shutdown_client = MagicMock()
        shutdown_client.aclose = AsyncMock()
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        service._llm_clients["g"] = {1: client, 2: client}
        service._llm_clients["shutdown"] = {1: shutdown_client}

        await asyncio.gather(
            service._close_game_clients("g"),
            service._close_game_clients("g"),
        )
        await service.aclose()
        await service.aclose()

        client.aclose.assert_awaited_once()
        shutdown_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_game_over_closes_clients_only_once(
        self, monkeypatch, tmp_path,
    ):
        import app.services.game_service as service_module

        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        service._manifest = MagicMock()
        state = MagicMock(game_id="g")
        state.get_public_state.return_value = {}
        service._games["g"] = state
        client = MagicMock()
        client.aclose = AsyncMock()
        service._llm_clients["g"] = {1: client}
        monkeypatch.setattr(
            service_module, "build_and_write_summary", lambda *args: {},
        )

        await service._on_game_over(
            game_id="g", win_result={"winning_camp": "good"},
        )
        await service._close_game_clients("g")
        await service.aclose()

        client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_role_factory_rejects_a_seat_without_model_assignment(
        self, monkeypatch, tmp_path,
    ):
        import app.services.game_service as service_module

        real_create_roles = service_module.builtin_registry.create_roles

        def create_roles(*args, **kwargs):
            with pytest.raises(ValueError, match="unknown model seat"):
                kwargs["llm_client_factory"](5)
            return real_create_roles(*args, **kwargs)

        monkeypatch.setattr(
            service_module.builtin_registry, "create_roles", create_roles,
        )
        monkeypatch.setattr(GameEngine, "start", AsyncMock())
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        service._manifest = MagicMock()

        game_id = await service.create_game(
            role_counts={
                "wolf-killer-werewolf": 1,
                "wolf-killer-villager": 3,
            },
        )

        await service._tasks[game_id]

    @pytest.mark.asyncio
    async def test_benchmark_creation_injects_frozen_roles_and_runtime_rng(
        self, monkeypatch, tmp_path,
    ):
        import app.services.game_service as service_module

        real_create_roles = service_module.builtin_registry.create_roles
        captured = {}

        def create_roles(*args, **kwargs):
            captured.update(kwargs)
            return real_create_roles(*args, **kwargs)

        monkeypatch.setattr(
            service_module.builtin_registry, "create_roles", create_roles,
        )
        monkeypatch.setattr(GameEngine, "start", AsyncMock())
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        service._manifest = MagicMock()
        role_by_seat = {
            1: "wolf-killer-villager",
            2: "wolf-killer-werewolf",
            3: "wolf-killer-villager",
            4: "wolf-killer-villager",
        }

        game_id = await service.create_game(
            role_counts={
                "wolf-killer-werewolf": 1,
                "wolf-killer-villager": 3,
            },
            role_by_seat=role_by_seat,
            runtime_seed=123456,
        )
        engine = service._engines[game_id]

        assert captured["role_by_seat"] == role_by_seat
        assert captured["rng"] is engine._rng
        assert {
            seat: role.role_name for seat, role in engine.roles.items()
        } == role_by_seat
        assert engine._rng.random() == random.Random(123456).random()

        await service._tasks[game_id]
        await service.aclose()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invoke_fails", [False, True])
    async def test_night_invoke_is_safe_before_engine_reference_is_bound(
        self, invoke_fails, monkeypatch, tmp_path,
    ):
        from types import SimpleNamespace

        import app.services.game_service as service_module

        class ProbeStopped(RuntimeError):
            pass

        class FakeClient:
            def __init__(self, *, config):
                pass

            def invoke_json(self, messages, **kwargs):
                if invoke_fails:
                    raise RuntimeError("early invoke failed")
                return SimpleNamespace(payload={"speak": False})

            async def aclose(self):
                pass

        class ProbeDirector:
            def __init__(self, snapshot, invoke):
                if invoke_fails:
                    with pytest.raises(RuntimeError, match="early invoke failed"):
                        invoke([], "werewolf_discussion", {}, 1)
                else:
                    assert json.loads(invoke(
                        [], "werewolf_discussion", {}, 1,
                    )) == {"speak": False}
                raise ProbeStopped("probe complete")

        monkeypatch.setattr(service_module, "LLMClient", FakeClient)
        monkeypatch.setattr(service_module, "NightDirector", ProbeDirector)
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        service._manifest = MagicMock()

        with pytest.raises(ProbeStopped, match="probe complete"):
            await service.create_game(
                role_counts={
                    "wolf-killer-werewolf": 1,
                    "wolf-killer-villager": 3,
                },
            )

        assert service._games == {}
        assert service._llm_clients == {}

    @pytest.mark.asyncio
    async def test_night_error_logging_failure_does_not_hide_provider_error(
        self, monkeypatch, tmp_path, caplog,
    ):
        import app.services.game_service as service_module

        fake_llm = MagicMock()
        fake_llm.model_name = "model-a"
        fake_llm.provider_profile.profile_id = "custom-openai"
        fake_llm.invoke_json.side_effect = RuntimeError("provider failed")
        fake_llm.aclose = AsyncMock()
        monkeypatch.setattr(service_module, "LLMClient", lambda **kwargs: fake_llm)
        monkeypatch.setattr(GameEngine, "start", AsyncMock())
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        service._manifest = MagicMock()
        game_id = await service.create_game(
            role_counts={
                "wolf-killer-werewolf": 1,
                "wolf-killer-villager": 3,
            },
        )
        engine = service._engines[game_id]
        engine.game_logger.log_model_error = MagicMock(
            side_effect=OSError("log unavailable"),
        )

        with caplog.at_level(logging.ERROR, logger="app.services.game_service"):
            with pytest.raises(RuntimeError, match="provider failed"):
                engine._director._invoke(
                    [], "werewolf_kill", {}, 1,
                )

        assert "Failed to persist wolf model error" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cleanup_fails", [False, True])
    async def test_setup_failure_rolls_back_runtime_manifest_and_directory(
        self, cleanup_fails, monkeypatch, tmp_path,
    ):
        import app.services.game_service as service_module

        class FakeClient:
            def __init__(self, *, config):
                self.close_count = 0

            async def aclose(self):
                self.close_count += 1

        game_dirs = []

        def fail_after_creating_directory(*args, **kwargs):
            game_dir = tmp_path / "games" / args[0]
            game_dir.mkdir(parents=True)
            game_dirs.append(game_dir)
            raise RuntimeError("manifest setup failed")

        monkeypatch.setattr(service_module, "LLMClient", FakeClient)
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        service._manifest = MagicMock()
        service._manifest.add_game.side_effect = fail_after_creating_directory
        if cleanup_fails:
            service._manifest.remove_game.side_effect = OSError("index unavailable")
            monkeypatch.setattr(
                service_module.shutil,
                "rmtree",
                MagicMock(side_effect=OSError("directory busy")),
            )

        with pytest.raises(RuntimeError, match="manifest setup failed"):
            await service.create_game(
                role_counts={
                    "wolf-killer-werewolf": 1,
                    "wolf-killer-villager": 3,
                },
            )

        assert service._games == {}
        assert service._engines == {}
        assert service._tasks == {}
        assert service._model_snapshots == {}
        assert service._llm_clients == {}
        service._manifest.remove_game.assert_called_once()
        assert game_dirs[0].exists() is cleanup_fails
