"""游戏导出（bundle + HTML + 路由）与双语标签门禁。

标签表的门禁和 `models/values.py` 同一份约定：公开取值增加而标签表不补，
这里先红 —— 不让它变成导出文件里的裸机器值。
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes import game_routes
from app.catalog import ROLE_METADATA
from app.export.labels import export_labels
from app.models.game import Camp, GamePhase
from app.models.values import DeathCause, WinReason, WinningCamp
from app.services import audience_projector
from app.services import legacy_replay
from app.services.game_export import (
    EXPORT_SCHEMA,
    GameExportNotFoundError,
    GameExportService,
    _camp_label,
    _config_from_operations,
    _derive_deaths,
    _normalize_player,
    _normalize_winner,
    _players_from_init_event,
    _players_from_role_init,
    _players_from_snapshot,
    _players_from_state,
    _public_config,
    _read_review,
    _seat_models,
    _winner_from_events,
)


# ── 测试替身 ────────────────────────────────────────────────


class FakeRepository:
    """Pages ``get_audience_events`` exactly like GameRepository does."""

    def __init__(self, *, game=None, snapshot=None, events=None, pages=None):
        self._game = game
        self._snapshot = snapshot
        self._events = list(events or [])
        self._pages = list(pages) if pages is not None else None

    def get_game(self, game_id):
        return self._game

    def get_audience_snapshot(self, game_id):
        return self._snapshot

    def get_audience_events(
        self, game_id, *, after_seq, limit, through_seq=None,
    ):
        if self._pages is not None:
            return self._pages.pop(0)
        high = max(
            (e["seq"] for e in self._events if isinstance(e, dict)),
            default=0,
        )
        if through_seq is not None:
            high = min(high, through_seq)
        page = [
            e for e in self._events
            if e["seq"] > after_seq and e["seq"] <= high
        ][:limit]
        next_seq = page[-1]["seq"] if page else after_seq
        return {
            "game_id": game_id,
            "events": page,
            "next_seq": next_seq,
            "high_watermark": high,
            "has_more": next_seq < high,
        }


def _audience_event(seq, event_type, payload):
    return {
        "game_id": "g1", "seq": seq, "event_id": f"e{seq}",
        "schema_version": 1, "event_type": event_type,
        "timestamp": "2026-01-01T00:00:00Z", "payload": payload,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )


def _write_game_logs(data_dir: Path, game_id: str, *, conversations=None, operations=None):
    game_dir = data_dir / "games" / game_id
    _write_jsonl(game_dir / "conversation.log", conversations or [])
    _write_jsonl(game_dir / "game.log", operations or [])


# ── 标签表门禁 ──────────────────────────────────────────────


def test_labels_cover_public_value_catalogs():
    labels = export_labels()
    for table in (labels["zh"], labels["en"]):
        for cause in DeathCause:
            assert f"death.{cause.value}" in table
        for reason in WinReason:
            assert f"win.{reason.value}" in table
        for camp in WinningCamp:
            assert f"camp.{camp.value}" in table
            assert f"camp_name.{camp.value}" in table
            assert f"winner.{camp.value}" in table
            assert f"check.{camp.value}" in table
        for phase in GamePhase:
            assert f"phase.{phase.value}" in table


def test_labels_cover_public_event_and_action_vocab():
    labels = export_labels()
    for table in (labels["zh"], labels["en"]):
        for public_type, _allowed in audience_projector._EVENTS.values():
            assert f"ev.{public_type}" in table, public_type
        for action in audience_projector._PUBLIC_ROLE_ACTIONS.values():
            assert f"action.{action}" in table, action
        for reasoning in audience_projector._PUBLIC_REASONING_EVENTS.values():
            assert f"thought.{reasoning}" in table, reasoning
        for reasoning, _allowed in legacy_replay._REASONING_EVENT_TYPES.values():
            assert f"thought.{reasoning}" in table, reasoning
        for choice in legacy_replay._SHERIFF_RUN_CHOICES:
            assert f"choice.{choice}" in table
        for choice in legacy_replay._SHERIFF_WITHDRAW_CHOICES:
            assert f"choice.{choice}" in table
        for kind in legacy_replay._SHERIFF_VOTE_KINDS:
            assert f"choice.{kind}" in table
        for side in legacy_replay._SHERIFF_SIDES:
            assert f"side.{side}" in table


def test_labels_cover_every_catalog_role():
    labels = export_labels()
    for role_id, meta in ROLE_METADATA.items():
        if not isinstance(meta, dict):
            continue
        assert labels["zh"].get(f"role.{role_id}") == meta["name_zh"]
        assert labels["en"].get(f"role.{role_id}") == meta["name_en"]


def test_labels_key_sets_are_symmetric():
    labels = export_labels()
    assert set(labels["zh"]) == set(labels["en"])


def test_labels_skip_non_dict_metadata_and_missing_names(monkeypatch):
    monkeypatch.setattr(
        "app.export.labels.ROLE_METADATA",
        {
            "bad-entry": "not-a-dict",
            "no-names": {"description": "x"},
            "zh-only": {"name_zh": "仅中文", "name_en": 5},
        },
    )
    labels = export_labels()
    assert "role.bad-entry" not in labels["zh"]
    assert "role.no-names" not in labels["en"]
    assert labels["zh"]["role.zh-only"] == "仅中文"
    assert "role.zh-only" not in labels["en"]


# ── 纯函数 ─────────────────────────────────────────────────


def test_camp_label_normalizes_str_enum_and_garbage():
    assert _camp_label("good") == "good"
    assert _camp_label(Camp.WEREWOLF) == "werewolf"
    assert _camp_label(5) is None
    assert _camp_label(None) is None
    assert _camp_label(SimpleNamespace(value=9)) is None


def test_public_config_filters_mapping_and_object():
    raw = {
        "role_counts": {"wolf-killer-werewolf": 2},
        "enable_sheriff": True,
        "reveal_on_death": False,
        "victory": "city",
        "witch_self_save": "first_night",
        "internal_secret": "drop-me",
    }
    assert _public_config(raw) == {
        "role_counts": {"wolf-killer-werewolf": 2},
        "enable_sheriff": True,
        "reveal_on_death": False,
        "victory": "city",
        "witch_self_save": "first_night",
    }
    config_obj = SimpleNamespace(
        enable_sheriff=True, victory="edge", role_counts=None,
    )
    assert _public_config(config_obj) == {
        "enable_sheriff": True, "victory": "edge",
    }
    assert _public_config(None) == {}


def test_normalize_player_tolerates_partial_rows():
    player = _normalize_player(3, {
        "role": "wolf-killer-seer", "camp": Camp.GOOD,
        "revealed_role": 7, "is_alive": 0, "is_sheriff": 1,
    })
    assert player == {
        "seat": 3, "role": "wolf-killer-seer", "camp": "good",
        "revealed_role": None, "is_alive": False, "is_sheriff": True,
        "model": None, "death": None,
    }
    assert _normalize_player(1, {})["is_alive"] is True


def test_players_from_snapshot_skips_malformed_rows():
    assert _players_from_snapshot({}) == {}
    assert _players_from_snapshot({"players": "not-a-mapping"}) == {}
    roster = _players_from_snapshot({"players": {
        "1": {"seat_number": 1, "role": "wolf-killer-seer", "camp": "good"},
        "2": {"role": "wolf-killer-werewolf", "camp": "werewolf"},
        "junk": {"role": "x"},
        "4": "not-a-mapping",
    }})
    assert sorted(roster) == [1, 2]
    assert roster[2]["role"] == "wolf-killer-werewolf"


def test_players_from_init_event_reads_payload_players():
    events = [
        {"event_type": "speech", "payload": {}},
        {"event_type": "game_initialized", "payload": "corrupt"},
        {"event_type": "game_initialized", "payload": {
            "players": {"1": {"seat_number": 1, "role": "r"}},
        }},
    ]
    assert _players_from_init_event(events)[1]["role"] == "r"
    assert _players_from_init_event([]) == {}
    assert _players_from_init_event(
        [{"event_type": "game_initialized"}],
    ) == {}


def test_players_from_state_reads_player_objects():
    assert _players_from_state(SimpleNamespace(players="no")) == {}
    state = SimpleNamespace(players={
        1: SimpleNamespace(role="seer", camp=Camp.GOOD, is_alive=True,
                           is_sheriff=True, revealed_role=None),
        "2": SimpleNamespace(role="wolf", camp="werewolf"),
    })
    roster = _players_from_state(state)
    assert sorted(roster) == [1]
    assert roster[1]["is_sheriff"] is True
    assert roster[1]["camp"] == "good"


def test_players_from_role_init_walks_operation_records():
    assert _players_from_role_init([]) == {}
    assert _players_from_role_init([
        "not-a-dict",
        {"operation": "role_init", "data": "no-players"},
        {"operation": "role_init", "data": {"players": "bad"}},
        {"operation": "role_init", "data": {"players": {}}},
        {"operation": "role_init", "data": {"players": {
            "x": {"role": "skip"},
            "3": "not-a-mapping",
            "7": {"role": "wolf-killer-witch"},
        }}},
    ]) == {
        7: {
            "seat": 7, "role": "wolf-killer-witch", "camp": None,
            "revealed_role": None, "is_alive": True, "is_sheriff": False,
            "model": None, "death": None,
        },
    }


def test_init_config_reads_first_init_event():
    helper = GameExportService._init_config
    assert helper([]) == {}
    assert helper([{"event_type": "speech", "payload": {}}]) == {}
    # payload 非 Mapping → 当无配置处理
    assert helper([
        {"event_type": "game_initialized", "payload": "corrupt"},
    ]) == {}
    assert helper([
        {"event_type": "game_initialized",
         "payload": {"config": {"victory": "city", "secret": 1}}},
    ]) == {"victory": "city"}


def test_config_from_operations_reads_game_config_record():
    assert _config_from_operations([]) == {}
    assert _config_from_operations([
        "junk",
        {"operation": "game_config", "data": None},
        {"operation": "game_config", "data": {
            "enable_sheriff": True, "secret": 1,
        }},
    ]) == {"enable_sheriff": True}


def test_derive_deaths_reads_death_and_exile_events():
    events = [
        {"event_type": "death", "payload": "corrupt"},
        {"event_type": "death", "payload": {
            "player_seat": "x", "cause": "wolf_kill",
        }},
        {"event_type": "death", "payload": {
            "player_seat": 2, "cause": "wolf_kill", "round_number": 1,
        }},
        {"event_type": "death", "payload": {
            "player_seat": 2, "cause": "poison", "round_number": 1,
        }},
        {"event_type": "vote_result", "payload": {
            "exiled_seat": "bad", "round_number": 1,
        }},
        {"event_type": "vote_result", "payload": {
            "exiled_seat": 5, "round_number": 1,
        }},
    ]
    assert _derive_deaths(events) == {
        2: {"cause": "wolf_kill", "round": 1},
        5: {"cause": "exile", "round": 1},
    }


def test_winner_helpers_tolerate_malformed_streams():
    assert _winner_from_events([]) is None
    assert _winner_from_events([
        {"event_type": "winner", "payload": "corrupt"},
        {"event_type": "winner", "payload": {"winning_camp": 3}},
        {"event_type": "winner", "payload": {
            "winning_camp": "good", "reason": "all_wolves_dead",
        }},
    ]) == {"winning_camp": "good", "reason": "all_wolves_dead"}
    # 最后一条 winner 坏了 → 继续向更早的有效事件回退
    assert _winner_from_events([
        {"event_type": "winner", "payload": {"winning_camp": "good"}},
        {"event_type": "winner", "payload": {"winning_camp": 3}},
    ]) == {"winning_camp": "good"}
    assert _normalize_winner(None) is None
    assert _normalize_winner({"reason": "x"}) is None
    assert _normalize_winner({
        "winning_camp": "good", "reason": 9,
        "winners": [1, "x", 3],
    }) == {"winning_camp": "good", "reason": None, "winners": [1, 3]}
    assert _normalize_winner({"winning_camp": "good", "winners": "bad"}) == {
        "winning_camp": "good", "reason": None, "winners": [],
    }


def test_seat_models_flattens_snapshot_groups():
    assert _seat_models(None) == {}
    assert _seat_models("bad") == {}
    assert _seat_models([
        "junk",
        {"model_id": "m1", "seats": "bad"},
        {"model_id": 5, "seats": [1]},
        {"model": "m2", "seats": [1, "x", 2]},
        {"model_id": "m3", "seats": [2, 3]},
    ]) == {1: "m2", 2: "m2", 3: "m3"}


def test_read_review_tolerates_missing_corrupt_and_non_dict(tmp_path):
    assert _read_review(str(tmp_path), "g") is None
    path = tmp_path / "games" / "g" / "review.json"
    path.parent.mkdir(parents=True)
    path.write_text("{corrupt", encoding="utf-8")
    assert _read_review(str(tmp_path), "g") is None
    path.write_text("[1,2]", encoding="utf-8")
    assert _read_review(str(tmp_path), "g") is None
    path.write_text('{"status":"ready","review":{"overview":"x"}}',
                    encoding="utf-8")
    assert _read_review(str(tmp_path), "g") == {
        "status": "ready", "review": {"overview": "x"},
    }


# ── 服务：存在性与两条数据路径 ──────────────────────────────


def test_build_bundle_raises_for_unknown_game(tmp_path):
    service = GameExportService(FakeRepository(), str(tmp_path))
    with pytest.raises(GameExportNotFoundError):
        service.build_bundle("missing")
    service = GameExportService(None, str(tmp_path), game_service=SimpleNamespace(
        get_game_state=lambda _g: None,
    ))
    with pytest.raises(GameExportNotFoundError):
        service.build_bundle("missing")


def _audience_fixture(tmp_path):
    """一个有 snapshot/事件/record/复盘/统计 的完整对局。"""
    snapshot = {
        "game_id": "g1", "projection_version": 1, "last_seq": 5,
        "state": {
            "phase": "game_over", "round_number": 2,
            "execution_status": "completed",
            "players": {
                "1": {"seat_number": 1, "is_alive": True, "is_sheriff": True,
                      "role": "wolf-killer-seer", "camp": "good",
                      "revealed_role": None},
                "2": {"seat_number": 2, "is_alive": False, "is_sheriff": False,
                      "role": "wolf-killer-werewolf", "camp": "werewolf",
                      "revealed_role": None},
            },
            "death_history": [
                {"player_seat": 2, "cause": "poison", "round_number": 1},
                "corrupt",
                {"player_seat": "x", "cause": "wolf_kill"},
            ],
            "win_result": {"winning_camp": "good",
                           "reason": "all_wolves_dead"},
        },
    }
    events = [
        _audience_event(1, "game_initialized", {
            "players": {"1": {"seat_number": 1}}, "config": {"victory": "city"},
        }),
        _audience_event(2, "speech", {
            "player_seat": 1, "text": "我是预言家", "round_number": 1,
        }),
        _audience_event(3, "death", {
            "player_seat": 2, "cause": "wolf_kill", "round_number": 1,
        }),
        _audience_event(4, "vote_result", {
            "exiled_seat": 2, "round_number": 1, "counts": {"2": 3},
        }),
        _audience_event(5, "winner", {
            "winning_camp": "good", "reason": "all_wolves_dead",
            "winners": [1],
        }),
    ]
    record = {
        "game_id": "g1", "name": "测试对局",
        "execution_status": "completed",
        "config": {
            "role_counts": {"wolf-killer-seer": 1}, "enable_sheriff": True,
            "reveal_on_death": True, "victory": "edge",
            "witch_self_save": "first_night", "secret": "drop",
        },
        "model_snapshot": [{"model_id": "m-record", "seats": [1, 2]}],
        "created_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-02T00:00:00Z",
    }
    game_service = SimpleNamespace(
        get_game_state=lambda _g: None,
        get_display_name=lambda _g: "测试对局",
        get_game_model_snapshot=lambda _g: [{"model_id": "m-svc", "seats": [1]}],
    )
    (tmp_path / "games" / "g1").mkdir(parents=True)
    (tmp_path / "games" / "g1" / "review.json").write_text(
        json.dumps({"status": "ready", "review": {
            "overview": "好人运营成功",
            "mvp": {"seat": 1, "reason": "查验命中"},
            "turning_points": [{"round": 1, "title": "毒狼", "detail": "x"}],
            "mistakes": [],
        }}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "games" / "g1" / "summary.json").write_text(
        json.dumps({
            "created_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-02T00:00:00Z",
            "winner": "good", "reason": "all_wolves_dead",
            "rounds_played": 2, "model_errors": 1,
            "model_snapshot": [{"model_id": "m-sum", "seats": [2]}],
            "llm": {
                "calls": 40, "errors": 1, "fallbacks": 2, "retried": 3,
                "degraded_actions": 1,
                "tokens": {"prompt": 10, "completion": 5, "total": 15},
                "elapsed_ms": {"p50": 1, "p95": 2, "p99": 3},
                "calls_by_kind": {"speech": 10},
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    repo = FakeRepository(game=record, snapshot=snapshot, events=events)
    return GameExportService(repo, str(tmp_path), game_service=game_service)


def test_build_bundle_audience_path_full(tmp_path):
    bundle = _audience_fixture(tmp_path).build_bundle("g1")

    assert bundle["schema"] == EXPORT_SCHEMA
    assert bundle["source"] == "audience"
    game = bundle["game"]
    assert game == {
        "game_id": "g1", "name": "测试对局", "phase": "game_over",
        "round_number": 2, "execution_status": "completed",
        "config": {
            "role_counts": {"wolf-killer-seer": 1},
            "enable_sheriff": True, "reveal_on_death": True,
            "victory": "edge", "witch_self_save": "first_night",
        },
        "winner": {"winning_camp": "good", "reason": "all_wolves_dead",
                   "winners": []},
        "created_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-02T00:00:00Z",
    }
    players = bundle["players"]
    assert [p["seat"] for p in players] == [1, 2]
    assert players[0]["role"] == "wolf-killer-seer"
    assert players[0]["model"] == "m-svc"
    assert players[1]["death"] == {"cause": "wolf_kill", "round": 1}
    assert players[1]["is_alive"] is False
    assert [e["event_type"] for e in bundle["events"]] == [
        "game_initialized", "speech", "death", "vote_result", "winner",
    ]
    assert bundle["review"]["status"] == "ready"
    assert bundle["stats"]["llm"]["calls"] == 40
    assert bundle["stats"]["degraded_actions"] == 1
    assert bundle["stats"]["model_errors"] == 1
    assert set(bundle["labels"]) == {"zh", "en"}


def test_audience_events_without_snapshot_use_init_event(tmp_path):
    events = [
        _audience_event(1, "game_initialized", {
            "players": {
                "1": {"seat_number": 1, "role": "wolf-killer-witch",
                      "camp": "good", "is_alive": True},
            },
            "config": {"enable_sheriff": False},
        }),
        _audience_event(2, "speech", {"player_seat": 1, "text": "hi",
                                      "round_number": 3}),
        _audience_event(3, "winner", {"winning_camp": "werewolf"}),
    ]
    repo = FakeRepository(
        game={"game_id": "g1", "name": "x"}, snapshot=None, events=events,
    )
    service = GameExportService(repo, str(tmp_path))
    bundle = service.build_bundle("g1")

    assert bundle["source"] == "audience"
    assert bundle["players"][0]["role"] == "wolf-killer-witch"
    assert bundle["game"]["config"] == {"enable_sheriff": False}
    # round_number 无快照/状态时取事件最大值；阶段与执行态同理走兜底。
    assert bundle["game"]["round_number"] == 3
    assert bundle["game"]["phase"] is None
    assert bundle["game"]["execution_status"] == "running"
    assert bundle["game"]["winner"] == {
        "winning_camp": "werewolf", "reason": None, "winners": [],
    }


def test_audience_snapshot_without_players_falls_back_to_state(tmp_path):
    snapshot = {"state": {"phase": "night", "round_number": 1,
                          "execution_status": "running"}}
    events = [_audience_event(1, "phase", {"phase": "night", "round_number": 1})]
    state = SimpleNamespace(
        players={4: SimpleNamespace(
            role="wolf-killer-villager", camp=Camp.GOOD,
            is_alive=True, is_sheriff=False, revealed_role=None,
        )},
        phase=GamePhase.NIGHT, round_number=1, win_result=None,
    )
    # record 缺席 → 执行态/名称从 snapshot state 与 game_id 兜底。
    repo = FakeRepository(
        game=None, snapshot=snapshot, events=events,
    )
    service = GameExportService(
        repo, str(tmp_path),
        game_service=SimpleNamespace(
            get_game_state=lambda _g: state,
            get_display_name=lambda _g: "",
            get_game_model_snapshot=lambda _g: [],
        ),
    )
    bundle = service.build_bundle("g1")

    assert bundle["players"][0]["seat"] == 4
    # state 上的 win_result=None → 事件兜底无 winner；summary 也不存在。
    assert bundle["game"]["winner"] is None
    assert bundle["game"]["name"] == "g1"[:8]
    assert bundle["game"]["execution_status"] == "running"


def test_audience_events_paginate_across_pages(tmp_path):
    events = [
        _audience_event(seq, "speech", {"player_seat": 1, "text": f"s{seq}",
                                        "round_number": 1})
        for seq in range(1, 505)
    ]
    repo = FakeRepository(
        game={"game_id": "g1", "execution_status": "completed"},
        snapshot={"state": {"players": {}}}, events=events,
    )
    service = GameExportService(repo, str(tmp_path))
    bundle = service.build_bundle("g1")

    assert len(bundle["events"]) == 504
    # 事件行被归一成 {seq,event_type,timestamp,payload}
    assert set(bundle["events"][0]) == {
        "seq", "event_type", "timestamp", "payload",
    }


def test_normalize_events_skips_non_mapping_and_bad_payload(tmp_path):
    snapshot = {"state": {"players": {"1": {"seat_number": 1, "role": "r"}}}}
    page = {
        "game_id": "g1",
        "events": [
            "corrupt-row",
            _audience_event(1, "phase", {"phase": "night"}),
            {**_audience_event(2, "speech", {"player_seat": 1, "text": "x"}),
             "payload": "not-a-mapping"},
        ],
        "next_seq": 2, "high_watermark": 2, "has_more": False,
    }
    repo = FakeRepository(
        game={"game_id": "g1"}, snapshot=snapshot, pages=[page],
    )
    bundle = GameExportService(repo, str(tmp_path)).build_bundle("g1")
    assert [e["event_type"] for e in bundle["events"]] == ["phase", "speech"]
    assert bundle["events"][1]["payload"] == {}


# ── 服务：旧档路径 ─────────────────────────────────────────


def _legacy_state(**overrides):
    base = dict(
        players={
            1: SimpleNamespace(role="wolf-killer-seer", camp=Camp.GOOD,
                               is_alive=True, is_sheriff=False,
                               revealed_role=None),
            2: SimpleNamespace(role="wolf-killer-werewolf", camp=Camp.WEREWOLF,
                               is_alive=False, is_sheriff=False,
                               revealed_role="wolf-killer-werewolf"),
        },
        phase=GamePhase.GAME_OVER, round_number=2,
        win_result={"winning_camp": "good", "reason": "all_wolves_dead",
                    "winners": [1]},
        config=SimpleNamespace(
            role_counts={"wolf-killer-seer": 1}, enable_sheriff=True,
            reveal_on_death=False, victory="edge",
            witch_self_save="never",
        ),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_legacy_path_uses_state_and_log_projection(tmp_path):
    _write_game_logs(
        tmp_path, "g-legacy",
        conversations=[{
            "timestamp": "2026-01-01T00:00:02Z", "scope": "public",
            "speaker_seat": 1, "content": "白天发言", "round_number": 1,
            "phase": "speech",
        }],
        operations=[
            {"timestamp": "2026-01-01T00:00:01Z", "operation": "role_init",
             "round": 0, "phase": "role_deal",
             "data": {"players": {"1": {"role": "wolf-killer-seer"}}}},
            {"timestamp": "2026-01-01T00:00:03Z", "operation": "night_deaths",
             "round": 1, "phase": "dawn",
             "data": {"deaths": [{
                 "player_seat": 2, "cause": "wolf_kill", "round_number": 1,
             }]}},
            {"timestamp": "2026-01-01T00:00:04Z", "operation": "vote_result",
             "round": 1, "phase": "vote_resolution",
             "data": {"exiled": 2, "tally": {"2": 3}}},
            {"timestamp": "2026-01-01T00:00:05Z", "operation": "game_over",
             "round": 2, "phase": "game_over",
             "data": {"winner": "good", "reason": "all_wolves_dead"}},
        ],
    )
    state = _legacy_state()
    game_service = SimpleNamespace(
        get_game_state=lambda _g: state,
        get_display_name=lambda _g: "旧档对局",
        get_game_model_snapshot=lambda _g: [],
    )
    repo = FakeRepository(game=None, snapshot=None, events=[])
    service = GameExportService(repo, str(tmp_path), game_service=game_service)
    bundle = service.build_bundle("g-legacy")

    assert bundle["source"] == "legacy"
    assert bundle["game"]["phase"] == "game_over"
    assert bundle["game"]["round_number"] == 2
    assert bundle["game"]["execution_status"] == "completed"
    assert bundle["game"]["winner"] == {
        "winning_camp": "good", "reason": "all_wolves_dead", "winners": [1],
    }
    assert bundle["game"]["config"] == {
        "role_counts": {"wolf-killer-seer": 1}, "enable_sheriff": True,
        "reveal_on_death": False, "victory": "edge",
        "witch_self_save": "never",
    }
    roles = {p["seat"]: p["role"] for p in bundle["players"]}
    assert roles == {1: "wolf-killer-seer", 2: "wolf-killer-werewolf"}
    assert bundle["players"][1]["death"] == {
        "cause": "wolf_kill", "round": 1,
    }
    assert [e["event_type"] for e in bundle["events"]] == [
        "speech", "death", "vote_result", "winner",
    ]


def test_legacy_roster_falls_back_to_role_init_records(tmp_path):
    """state 缺席时阵容从 game.log 的 role_init 记录恢复。"""
    _write_game_logs(
        tmp_path, "g-role-init",
        operations=[
            {"timestamp": "2026-01-01T00:00:01Z", "operation": "role_init",
             "round": 0, "phase": "role_deal",
             "data": {"players": {
                 "1": {"role": "wolf-killer-villager", "camp": "good"},
                 "9": {"role": "wolf-killer-werewolf", "camp": "werewolf"},
             }}},
            {"timestamp": "2026-01-01T00:00:02Z", "operation": "game_config",
             "round": 0, "phase": "role_deal",
             "data": {"enable_sheriff": True, "reveal_on_death": False}},
            {"timestamp": "2026-01-01T00:00:03Z", "operation": "vote_result",
             "round": 2, "phase": "vote_resolution",
             "data": {"exiled": 9, "tally": {"9": 5}}},
        ],
    )
    record = {"game_id": "g-role-init", "name": "旧档",
              "execution_status": "completed"}
    repo = FakeRepository(game=record, snapshot=None, events=[])
    game_service = SimpleNamespace(
        get_game_state=lambda _g: None,
        get_display_name=lambda _g: "旧档",
        get_game_model_snapshot=lambda _g: [],
    )
    bundle = GameExportService(
        repo, str(tmp_path), game_service=game_service,
    ).build_bundle("g-role-init")

    assert bundle["source"] == "legacy"
    assert [p["seat"] for p in bundle["players"]] == [1, 9]
    assert bundle["players"][1]["death"] == {"cause": "exile", "round": 2}
    assert bundle["game"]["round_number"] == 2
    assert bundle["game"]["config"] == {
        "enable_sheriff": True, "reveal_on_death": False,
    }
    assert bundle["game"]["phase"] is None


def test_legacy_meta_falls_back_to_summary(tmp_path):
    """record/state 都没有的元数据从 summary.json 补。"""
    _write_game_logs(tmp_path, "g-meta", operations=[
        {"timestamp": "2026-01-01T00:00:01Z", "operation": "role_init",
         "round": 0, "phase": "role_deal",
         "data": {"players": {"1": {"role": "wolf-killer-villager"}}}},
    ])
    (tmp_path / "games" / "g-meta" / "summary.json").write_text(
        json.dumps({
            "created_at": "2020-01-01T00:00:00Z",
            "finished_at": "2020-01-02T00:00:00Z",
            "winner": "werewolf", "reason": "all_gods_dead",
            "llm": "corrupt",
        }),
        encoding="utf-8",
    )
    record = {"game_id": "g-meta", "execution_status": "completed"}
    repo = FakeRepository(game=record, snapshot=None, events=[])
    bundle = GameExportService(
        repo, str(tmp_path),
        game_service=SimpleNamespace(
            get_game_state=lambda _g: None,
            get_display_name=lambda _g: None,
            get_game_model_snapshot=lambda _g: [],
        ),
    ).build_bundle("g-meta")

    assert bundle["game"]["created_at"] == "2020-01-01T00:00:00Z"
    assert bundle["game"]["finished_at"] == "2020-01-02T00:00:00Z"
    assert bundle["game"]["winner"] == {
        "winning_camp": "werewolf", "reason": "all_gods_dead", "winners": [],
    }
    assert bundle["stats"]["llm"] is None
    assert bundle["stats"]["degraded_actions"] is None
    assert bundle["game"]["name"] == "g-meta"


def test_model_snapshot_fallback_order(tmp_path):
    """模型快照：service → record → summary。"""
    _write_game_logs(tmp_path, "g-m1")
    (tmp_path / "games" / "g-m1" / "summary.json").write_text(
        json.dumps({
            "model_snapshot": [{"model_id": "m-sum", "seats": [2]}],
        }),
        encoding="utf-8",
    )
    record = {
        "game_id": "g-m1", "name": "x",
        "model_snapshot": [{"model_id": "m-record", "seats": [1, 2]}],
        "execution_status": "completed",
    }
    state = _legacy_state()
    base_service = dict(
        get_game_state=lambda _g: state,
        get_display_name=lambda _g: "x",
    )
    # service 快照优先
    svc = GameExportService(
        FakeRepository(game=record), str(tmp_path),
        game_service=SimpleNamespace(
            **base_service,
            get_game_model_snapshot=lambda _g: [{"model_id": "m-svc",
                                                 "seats": [1]}],
        ),
    )
    players = svc.build_bundle("g-m1")["players"]
    assert players[0]["model"] == "m-svc"
    assert players[1]["model"] is None
    # service 空 → record
    svc = GameExportService(
        FakeRepository(game=record), str(tmp_path),
        game_service=SimpleNamespace(
            **base_service, get_game_model_snapshot=lambda _g: [],
        ),
    )
    players = svc.build_bundle("g-m1")["players"]
    assert players[0]["model"] == "m-record"
    # record 也空 → summary
    svc = GameExportService(
        FakeRepository(game={**record, "model_snapshot": []}),
        str(tmp_path),
        game_service=SimpleNamespace(
            **base_service, get_game_model_snapshot=lambda _g: [],
        ),
    )
    players = svc.build_bundle("g-m1")["players"]
    assert players[1]["model"] == "m-sum"


def test_display_name_fallbacks(tmp_path):
    _write_game_logs(tmp_path, "g-n1")
    repo = FakeRepository(game={"game_id": "g-n1", "name": "记录名",
                                "execution_status": "completed"})
    # service 抛错 → record.name
    svc = GameExportService(
        repo, str(tmp_path),
        game_service=SimpleNamespace(
            get_game_state=lambda _g: _legacy_state(),
            get_display_name=MagicMock(side_effect=RuntimeError("boom")),
            get_game_model_snapshot=lambda _g: [],
        ),
    )
    assert svc.build_bundle("g-n1")["game"]["name"] == "记录名"
    # 无 service → record.name
    svc = GameExportService(repo, str(tmp_path))
    assert svc.build_bundle("g-n1")["game"]["name"] == "记录名"
    # 两者都没有 → game_id 前缀
    state_only = FakeRepository(game=None)
    svc = GameExportService(
        state_only, str(tmp_path),
        game_service=SimpleNamespace(
            get_game_state=lambda _g: _legacy_state(),
            get_display_name=lambda _g: " ",
            get_game_model_snapshot=lambda _g: [],
        ),
    )
    assert svc.build_bundle("g-n1")["game"]["name"] == "g-n1"


def test_stats_none_when_summary_missing(tmp_path):
    _write_game_logs(tmp_path, "g-s0")
    repo = FakeRepository(game={"game_id": "g-s0",
                                "execution_status": "running"})
    svc = GameExportService(
        repo, str(tmp_path),
        game_service=SimpleNamespace(
            get_game_state=lambda _g: _legacy_state(
                phase=GamePhase.NIGHT, win_result=None),
            get_display_name=lambda _g: "x",
            get_game_model_snapshot=lambda _g: [],
        ),
    )
    bundle = svc.build_bundle("g-s0")
    assert bundle["stats"] is None
    assert bundle["game"]["execution_status"] == "running"


# ── HTML 渲染 ──────────────────────────────────────────────


def _island_json(html: str) -> dict:
    marker = '<script id="export-data" type="application/json">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    return json.loads(html[start:end].replace("<\\/", "</"))


def test_render_html_embeds_bundle_and_escapes_title(tmp_path):
    svc = _audience_fixture(tmp_path)
    html = svc.render_html("g1")

    assert html.startswith("<!DOCTYPE html>")
    assert "__DATA__" not in html
    assert "__TITLE__" not in html
    assert "<title>测试对局 · WolfKiller</title>" in html
    bundle = _island_json(html)
    assert bundle["schema"] == EXPORT_SCHEMA
    assert bundle["game"]["game_id"] == "g1"
    assert bundle["players"][0]["role"] == "wolf-killer-seer"


def test_render_html_neutralizes_script_breakout_and_title_tags(tmp_path):
    record = {"game_id": "g-evil", "name": "<script>alert(1)</script>",
              "execution_status": "completed"}
    snapshot = {
        "state": {
            "phase": "game_over", "round_number": 1,
            "players": {"1": {"seat_number": 1, "role": "r"}},
        },
    }
    events = [
        _audience_event(1, "speech", {
            "player_seat": 1, "round_number": 1,
            "text": "</script><script>alert('xss')</script>",
        }),
    ]
    repo = FakeRepository(game=record, snapshot=snapshot, events=events)
    svc = GameExportService(
        repo, str(tmp_path),
        game_service=SimpleNamespace(
            get_game_state=lambda _g: None,
            get_display_name=lambda _g: "<script>alert(1)</script>",
            get_game_model_snapshot=lambda _g: [],
        ),
    )
    html = svc.render_html("g-evil")

    assert "&lt;script&gt;" in html.split("<title>", 1)[1].split("</title>")[0]
    island = html.split('id="export-data"', 1)[1].split("</script>", 1)[0]
    assert "</script>" not in island
    assert "alert('xss')" in island
    bundle = _island_json(html)
    assert bundle["events"][0]["payload"]["text"] == (
        "</script><script>alert('xss')</script>"
    )


def test_render_html_works_for_in_progress_legacy_game(tmp_path):
    """进行中对局：无 winner、执行态 running，也能导出。"""
    _write_game_logs(
        tmp_path, "g-live",
        conversations=[{
            "timestamp": "2026-01-01T00:00:02Z", "scope": "public",
            "speaker_seat": 1, "content": "进行中发言", "round_number": 1,
            "phase": "speech",
        }],
        operations=[{
            "timestamp": "2026-01-01T00:00:01Z", "operation": "role_init",
            "round": 0, "phase": "role_deal",
            "data": {"players": {"1": {"role": "wolf-killer-seer",
                                      "camp": "good"}}},
        }],
    )
    state = _legacy_state(phase=GamePhase.SPEECH, round_number=1,
                          win_result=None)
    svc = GameExportService(
        FakeRepository(game=None), str(tmp_path),
        game_service=SimpleNamespace(
            get_game_state=lambda _g: state,
            get_display_name=lambda _g: "进行中",
            get_game_model_snapshot=lambda _g: [],
        ),
    )
    html = svc.render_html("g-live")
    bundle = _island_json(html)
    assert bundle["game"]["winner"] is None
    assert bundle["game"]["execution_status"] == "running"
    assert bundle["game"]["phase"] == "speech"


# ── 路由 ───────────────────────────────────────────────────


def test_get_export_service_reads_main(monkeypatch):
    service = object()
    monkeypatch.setitem(
        sys.modules, "app.main",
        SimpleNamespace(game_export_service=service),
    )
    assert game_routes.get_export_service() is service


@pytest.mark.asyncio
async def test_export_game_returns_html_attachment(monkeypatch):
    fake = SimpleNamespace(render_html=lambda _g: "<!DOCTYPE html><title>x</title>")
    monkeypatch.setattr(game_routes, "get_export_service", lambda: fake)

    response = await game_routes.export_game("abcdef123456")

    assert response.media_type == "text/html; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="wolfkiller-abcdef12.html"'
    )
    assert response.body == b"<!DOCTYPE html><title>x</title>"


@pytest.mark.asyncio
async def test_export_game_404_for_unknown_game(monkeypatch):
    fake = SimpleNamespace(
        render_html=MagicMock(side_effect=GameExportNotFoundError("nope")),
    )
    monkeypatch.setattr(game_routes, "get_export_service", lambda: fake)

    with pytest.raises(HTTPException, match="Game not found") as error:
        await game_routes.export_game("nope")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_export_game_rejects_non_html_format(monkeypatch):
    fake = SimpleNamespace(render_html=MagicMock())
    monkeypatch.setattr(game_routes, "get_export_service", lambda: fake)

    with pytest.raises(HTTPException) as error:
        await game_routes.export_game("g1", format="json")
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "unsupported_format"
    fake.render_html.assert_not_called()
