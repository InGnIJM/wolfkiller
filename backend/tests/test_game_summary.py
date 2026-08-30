"""Tests for per-game benchmark summary construction."""

import json
from pathlib import Path

import pytest

from app.services.game_summary import (
    _load_manifest_entry,
    _percentile,
    _read_jsonl,
    build_and_write_summary,
    build_game_summary,
    write_game_summary,
)


def _write_lines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _seed_game(data_dir: Path, game_id: str = "g1") -> None:
    log = [
        {"timestamp": "t0", "round": 0, "phase": "role_deal",
         "operation": "role_init",
         "data": {"players": {
             "1": {"role": "wolf-killer-seer", "camp": "good"},
             "2": {"role": "wolf-killer-werewolf", "camp": "wolf"},
             "3": {"role": "wolf-killer-villager", "camp": "good"},
         }}},
        {"timestamp": "t1", "round": 1, "phase": "night",
         "operation": "audience_action",
         "data": {"event_type": "SEER_CHECK",
                  "payload": {"seat": 1, "target_seat": 2, "result": "wolf"}}},
        {"timestamp": "t2", "round": 1, "phase": "night",
         "operation": "audience_action",
         "data": {"event_type": "WEREWOLF_KILL",
                  "payload": {"target_seat": 3, "vote_counts": {"2": 1}}}},
        {"timestamp": "t3", "round": 1, "phase": "night",
         "operation": "audience_action",
         "data": {"event_type": "WITCH_REASONING",
                  "payload": {"seat": 4}}},
        {"timestamp": "t4", "round": 1, "phase": "dawn",
         "operation": "night_deaths",
         "data": {"deaths": [{"player_seat": 3, "cause": "wolf_kill"}]}},
        {"timestamp": "t5", "round": 1, "phase": "speech",
         "operation": "speak", "seat": 1, "data": {"text": "二号玩家昨晚查验是狼！"}},
        {"timestamp": "t6", "round": 1, "phase": "vote_casting",
         "operation": "vote", "seat": 1, "data": {"target": 2, "vote_round": 1}},
        {"timestamp": "t7", "round": 1, "phase": "vote_casting",
         "operation": "vote_technical_abstain", "seat": 3,
         "data": {"failure_code": "request_timeout"}},
        {"timestamp": "t8", "round": 1, "phase": "vote_resolution",
         "operation": "vote_result", "data": {"exiled": 2, "tally": {"1": 1}}},
        {"timestamp": "t9", "round": 1, "phase": "night",
         "operation": "model_error", "seat": 2,
         "data": {"failure_code": "provider_server_error"}},
        {"timestamp": "t10", "round": 1, "phase": "night",
         "operation": "stage_telemetry",
         "data": {"scope": "phase", "stage": "night", "elapsed_ms": 900}},
        {"timestamp": "t11", "round": 1, "phase": "night",
         "operation": "stage_telemetry",
         "data": {"scope": "hook", "stage": "night_action",
                  "label": "validation", "elapsed_ms": 77}},
        {"timestamp": "t12", "round": 2, "phase": "game_over",
         "operation": "game_over",
         "data": {"winner": "good", "reason": "all wolves eliminated"}},
    ]
    _write_lines(data_dir / "games" / "g1" / "game.log", log)
    _write_lines(data_dir / "games" / "g1" / "llm_calls.log", [
        {"timestamp": "a", "round": 1, "phase": "night", "operation": "llm_call",
         "seat": 1, "data": {"call_kind": "action", "contract_id": "seer_action",
                             "elapsed_ms": 1200, "prompt_tokens": 800,
                             "completion_tokens": 90, "total_tokens": 890,
                             "parse_result": "accepted", "attempt": 1}},
        {"timestamp": "b", "round": 1, "phase": "speech", "operation": "llm_call",
         "seat": 1, "data": {"call_kind": "speech", "elapsed_ms": 2500,
                             "prompt_tokens": 1500, "completion_tokens": 200,
                             "total_tokens": 1700, "parse_result": "accepted"}},
        {"timestamp": "c", "round": 1, "phase": "night", "operation": "llm_call",
         "seat": 2, "data": {"call_kind": "night", "elapsed_ms": 90,
                             "parse_result": "error", "failure_code": "TimeoutError"}},
        {"timestamp": "d", "round": 1, "phase": "speech", "operation": "llm_call",
         "seat": 3, "data": {"call_kind": "speech", "elapsed_ms": 3000,
                             "parse_result": "fallback"}},
    ])


def test_percentile_nearest_rank() -> None:
    assert _percentile([], 50) is None
    assert _percentile([7], 50) == 7
    assert _percentile([1, 2, 3, 4], 50) == 2
    assert _percentile([1, 2, 3, 4], 95) == 4
    assert _percentile([5, 1, 3], 99) == 5
    assert _percentile([5, 1, 3], 0) == 1


def test_read_jsonl_tolerates_missing_and_malformed(tmp_path: Path) -> None:
    assert _read_jsonl(str(tmp_path / "missing.log")) == []

    path = tmp_path / "mixed.log"
    path.write_text(
        '{"a": 1}\nnot json\n\n{"b": 2}\n[1, 2]\n', encoding="utf-8",
    )

    assert _read_jsonl(str(path)) == [{"a": 1}, {"b": 2}]


def test_load_manifest_entry_variants(tmp_path: Path) -> None:
    assert _load_manifest_entry(str(tmp_path), "g1") is None

    games_dir = tmp_path / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    (games_dir / "index.json").write_text(
        json.dumps([{"game_id": "g1", "winner": "wolf"}]), encoding="utf-8",
    )
    assert _load_manifest_entry(str(tmp_path), "g1") == {"game_id": "g1", "winner": "wolf"}
    assert _load_manifest_entry(str(tmp_path), "nope") is None

    (games_dir / "index.json").write_text("{broken", encoding="utf-8")
    assert _load_manifest_entry(str(tmp_path), "g1") is None

    (games_dir / "index.json").write_text('{"games": []}', encoding="utf-8")
    assert _load_manifest_entry(str(tmp_path), "g1") is None


def test_build_game_summary_from_fixture(tmp_path: Path) -> None:
    _seed_game(tmp_path)
    (tmp_path / "games" / "index.json").write_text(json.dumps([{
        "game_id": "g1", "created_at": "c0", "finished_at": "f0",
        "winner": "good", "model_snapshot": [{"model_id": "deepseek-x"}],
        "pipeline_version": "v2",
    }]), encoding="utf-8")

    summary = build_game_summary(str(tmp_path), "g1")

    assert summary["game_id"] == "g1"
    assert summary["created_at"] == "c0"
    assert summary["finished_at"] == "f0"
    assert summary["winner"] == "good"
    assert summary["reason"] == "all wolves eliminated"
    assert summary["rounds_played"] == 2
    assert summary["pipeline_version"] == "v2"
    assert summary["model_snapshot"] == [{"model_id": "deepseek-x"}]
    assert summary["roles"]["2"] == {"role": "wolf-killer-werewolf", "camp": "wolf"}

    round1 = summary["rounds"]["1"]
    assert round1["exiled"] == 2
    assert round1["tally"] == {"1": 1}
    assert round1["technical_abstains"] == 1
    assert round1["votes"] == [{"seat": 1, "target": 2}]
    assert round1["speeches"] == [{"seat": 1, "chars": 11}]
    assert round1["deaths"] == [{"player_seat": 3, "cause": "wolf_kill"}]
    night_events = round1["night_actions"]
    assert [event["event"] for event in night_events] == [
        "SEER_CHECK", "WEREWOLF_KILL",
    ]
    assert night_events[0] == {
        "event": "SEER_CHECK", "seat": 1, "target_seat": 2, "result": "wolf",
    }

    assert summary["llm"]["calls"] == 4
    assert summary["llm"]["errors"] == 1
    assert summary["llm"]["fallbacks"] == 1
    assert summary["llm"]["tokens"] == {"prompt": 2300, "completion": 290, "total": 2590}
    assert summary["llm"]["calls_by_kind"] == {"action": 1, "speech": 2, "night": 1}
    assert summary["llm"]["elapsed_ms"]["p50"] == 1200
    assert summary["llm"]["elapsed_ms"]["p99"] == 3000

    assert summary["engine"]["phase_ms"] == {"night": 900}
    assert summary["engine"]["slow_rules"] == [{
        "stage": "night_action", "label": "validation", "elapsed_ms": 77,
    }]
    assert summary["model_errors"] == 1


def test_winner_falls_back_to_manifest_without_game_over(tmp_path: Path) -> None:
    log = [
        {"timestamp": "t0", "round": 0, "phase": "role_deal",
         "operation": "role_init",
         "data": {"players": {"1": {"role": "wolf-killer-villager", "camp": "good"}}}},
    ]
    _write_lines(tmp_path / "games" / "g9" / "game.log", log)
    (tmp_path / "games" / "index.json").write_text(
        json.dumps([{"game_id": "g9", "winner": "wolf"}]), encoding="utf-8",
    )

    summary = build_game_summary(str(tmp_path), "g9")

    assert summary["winner"] == "wolf"
    assert summary["reason"] is None
    assert summary["rounds_played"] == 0
    assert summary["llm"]["calls"] == 0
    assert summary["llm"]["elapsed_ms"]["p50"] is None


def test_missing_game_log_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_game_summary(str(tmp_path), "ghost")


def test_summary_tolerates_malformed_records(tmp_path: Path) -> None:
    log = [
        {"operation": "mystery_op", "round": 1, "data": {"x": 1}},
        {"operation": "role_init", "data": {"players": "junk"}},
        {"operation": "role_init", "round": 0,
         "data": {"players": {"1": "junk", "2": {"camp": "good"}}}},
        {"operation": "speak", "data": "junk"},
        {"operation": "game_over", "round": "final", "data": {"winner": None}},
        {"operation": "stage_telemetry",
         "data": {"scope": "other", "stage": "x", "elapsed_ms": 5}},
        {"operation": "stage_telemetry",
         "data": {"scope": "phase", "stage": 5, "elapsed_ms": "x"}},
    ]
    _write_lines(tmp_path / "games" / "gz" / "game.log", log)
    _write_lines(tmp_path / "games" / "gz" / "llm_calls.log", [
        {"operation": "llm_call",
         "data": {"call_kind": 5, "elapsed_ms": -4, "prompt_tokens": "many"}},
        {"operation": "llm_call", "data": "junk"},
        {"operation": "llm_call"},
    ])

    summary = build_game_summary(str(tmp_path), "gz")

    assert summary["rounds_played"] == 0
    assert summary["roles"] == {}
    assert summary["engine"]["phase_ms"] == {}
    assert summary["engine"]["slow_rules"] == []
    assert summary["llm"]["calls"] == 3
    assert summary["llm"]["calls_by_kind"] == {}
    assert summary["llm"]["tokens"] == {"prompt": 0, "completion": 0, "total": 0}
    assert summary["llm"]["elapsed_ms"]["p50"] is None


def test_write_and_round_trip(tmp_path: Path) -> None:
    _seed_game(tmp_path)

    path = build_and_write_summary(str(tmp_path), "g1")
    summary = json.loads(Path(path).read_text(encoding="utf-8"))

    assert summary == build_game_summary(str(tmp_path), "g1")
    assert path.endswith("summary.json")
    assert Path(path).exists()


def test_write_game_summary_creates_directory(tmp_path: Path) -> None:
    path = write_game_summary(str(tmp_path), "fresh", {"game_id": "fresh"})

    assert json.loads(Path(path).read_text(encoding="utf-8")) == {"game_id": "fresh"}
