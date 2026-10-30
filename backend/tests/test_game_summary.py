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
        {"timestamp": "t7a", "round": 1, "phase": "sheriff_election",
         "operation": "audience_action",
         "data": {"event_type": "SHERIFF_VOTE",
                  "payload": {"voter_seat": 1, "target_seat": 2, "kind": "vote"}}},
        {"timestamp": "t7b", "round": 1, "phase": "sheriff_election",
         "operation": "audience_action",
         "data": {"event_type": "SHERIFF_VOTE",
                  "payload": {"voter_seat": 2, "target_seat": None, "kind": "vote"}}},
        {"timestamp": "t7c", "round": 1, "phase": "sheriff_election",
         "operation": "sheriff_vote_technical_abstain", "seat": 2,
         "data": {"failure_code": "sheriff_vote_unusable"}},
        {"timestamp": "t7d", "round": 1, "phase": "night",
         "operation": "audience_action",
         "data": {"event_type": "GUARD_REASONING",
                  "payload": {"seat": 5, "degraded": True, "reasoning": "本轮未提交决策"}}},
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
                             "parse_result": "accepted", "attempt": 2,
                             "retried": True}},
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
    assert round1["sheriff_ballots"] == 2
    assert round1["sheriff_abstains"] == 1
    assert round1["sheriff_technical_abstains"] == 1
    assert round1["degraded_actions"] == 1
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
    # 内层梯子的重试次数只在这份日志里：`model_attempts` 每次逻辑请求只有一行。
    assert summary["llm"]["retried"] == 1
    assert summary["llm"]["tokens"] == {"prompt": 2300, "completion": 290, "total": 2590}
    assert summary["llm"]["calls_by_kind"] == {"action": 1, "speech": 2, "night": 1}
    assert summary["llm"]["elapsed_ms"]["p50"] == 1200
    assert summary["llm"]["elapsed_ms"]["p99"] == 3000
    assert summary["llm"]["degraded_actions"] == 1

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


def test_every_death_batch_of_a_round_survives_the_summary(tmp_path: Path) -> None:
    """一轮里的多批死亡必须合并，不能被后一批覆盖。

    ``log_deaths`` 在一轮里会被调用多次：夜死批次、白天中断（自爆/决斗）、
    延迟死亡（`hunt` / 毒伤 / 中枪）。从前汇总是赋值而不是追加，第二、三批
    把前面的整批挤掉 —— 实测最新一局 r1 只剩自爆的 5 号、丢掉夜刀的 6 号，
    r3 只剩被狩猎的 2 号、丢掉夜刀的 8 号（近 200 局里 13 局命中）。
    """
    log = [
        {"timestamp": "t0", "round": 0, "phase": "role_deal",
         "operation": "role_init",
         "data": {"players": {"6": {"role": "wolf-killer-witch", "camp": "good"}}}},
        {"timestamp": "t1", "round": 1, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": [{"player_seat": 6, "cause": "wolf_kill", "round_number": 1}]}},
        {"timestamp": "t2", "round": 1, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": [{"player_seat": 5, "cause": "self_explode", "round_number": 1}]}},
        {"timestamp": "t3", "round": 1, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": []}},
        {"timestamp": "t4", "round": 2, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": [{"player_seat": 8, "cause": "wolf_kill", "round_number": 2}]}},
        {"timestamp": "t5", "round": 2, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": [{"player_seat": 2, "cause": "hunt", "round_number": 2}]}},
        {"timestamp": "t6", "round": 3, "phase": "game_over", "operation": "game_over",
         "data": {"winner": "good", "reason": "all_wolves_dead"}},
    ]
    _write_lines(tmp_path / "games" / "gb" / "game.log", log)

    summary = build_game_summary(str(tmp_path), "gb")

    assert summary["rounds"]["1"]["deaths"] == [
        {"player_seat": 6, "cause": "wolf_kill", "round_number": 1},
        {"player_seat": 5, "cause": "self_explode", "round_number": 1},
    ]
    assert summary["rounds"]["2"]["deaths"] == [
        {"player_seat": 8, "cause": "wolf_kill", "round_number": 2},
        {"player_seat": 2, "cause": "hunt", "round_number": 2},
    ]


def test_a_replayed_batch_is_not_counted_twice(tmp_path: Path) -> None:
    """崩溃恢复重跑 stage 0 会把同一批再写一次；汇总要幂等。"""
    death = {"player_seat": 4, "cause": "wolf_kill", "round_number": 1}
    log = [
        {"timestamp": "t1", "round": 1, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": [death]}},
        {"timestamp": "t2", "round": 1, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": [dict(death)]}},
    ]
    _write_lines(tmp_path / "games" / "gr" / "game.log", log)

    summary = build_game_summary(str(tmp_path), "gr")

    assert summary["rounds"]["1"]["deaths"] == [death]


def test_malformed_death_rows_never_break_a_batch(tmp_path: Path) -> None:
    """非 dict 行、非 list 的 deaths 都跳过，合法行照旧进汇总。"""
    log = [
        {"timestamp": "t1", "round": 1, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": [
             {"player_seat": 3, "cause": "wolf_kill", "round_number": 1},
             "junk", 7, None,
         ]}},
        {"timestamp": "t2", "round": 1, "phase": "dawn", "operation": "night_deaths",
         "data": {"deaths": "junk"}},
    ]
    _write_lines(tmp_path / "games" / "gm" / "game.log", log)

    summary = build_game_summary(str(tmp_path), "gm")

    assert summary["rounds"]["1"]["deaths"] == [
        {"player_seat": 3, "cause": "wolf_kill", "round_number": 1},
    ]


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


def test_rejected_actions_are_counted_per_round_not_as_slow_rules(tmp_path: Path) -> None:
    """模型答了规则禁止的动作 → 引擎兜底：这条故障要能数出来。

    调度点从前对「回答合法但被规则拒绝」不记任何东西，故障行的唯一痕迹是兜底命令本身，
    读起来和 provider 故障一模一样。现在按轮计数（`slow_rules` 只留真正的耗时行），
    报表才能把「模型弃权」与「引擎代行」分开。
    """
    log = [
        {"timestamp": "t0", "round": 1, "phase": "night",
         "operation": "stage_telemetry",
         "data": {"scope": "hook", "stage": "night_action", "code": "action_rejected",
                  "contract_id": "dance_pool", "actor_seat": 1, "attempt": 0,
                  "violation": "dance_mandatory"}},
        {"timestamp": "t1", "round": 1, "phase": "night",
         "operation": "stage_telemetry",
         "data": {"scope": "hook", "stage": "night_action", "code": "action_rejected",
                  "contract_id": "dance_pool", "actor_seat": 1, "attempt": 1,
                  "violation": "dance_mandatory"}},
        {"timestamp": "t2", "round": 1, "phase": "night",
         "operation": "stage_telemetry",
         "data": {"scope": "hook", "stage": "night_action", "label": "validation",
                  "elapsed_ms": 12}},
        {"timestamp": "t3", "round": 2, "phase": "game_over",
         "operation": "game_over", "data": {"winner": "good", "reason": "all_wolves_dead"}},
    ]
    _write_lines(tmp_path / "games" / "gr" / "game.log", log)

    summary = build_game_summary(str(tmp_path), "gr")

    assert summary["rounds"]["1"]["rejected_actions"] == 2
    assert summary["engine"]["slow_rules"] == [{
        "stage": "night_action", "label": "validation", "elapsed_ms": 12,
    }]


def test_the_new_roles_decision_rows_carry_what_a_hit_rate_needs(tmp_path: Path) -> None:
    """舞池与假面的关键事实要进 summary：池内座位与「是否在池内」。

    其它事件的行形状逐字节不变（没带这两个键的事件不多写键），否则旧报告会因为
    summary 变化重新 digest —— 那是期望行为，但不该由无关事件触发。
    """
    log = [
        {"timestamp": "t0", "round": 0, "phase": "role_deal", "operation": "role_init",
         "data": {"players": {"1": {"role": "wolf-killer-dancer", "camp": "good"}}}},
        {"timestamp": "t1", "round": 2, "phase": "night", "operation": "audience_action",
         "data": {"event_type": "DANCE_POOL",
                  "payload": {"seat": 1, "pool_seats": [1, 2, 3], "round_number": 2}}},
        {"timestamp": "t2", "round": 2, "phase": "night", "operation": "audience_action",
         "data": {"event_type": "MASK_GRANTED",
                  "payload": {"seat": 4, "target_seat": 2, "in_pool": True, "round_number": 2}}},
        {"timestamp": "t3", "round": 2, "phase": "night", "operation": "audience_action",
         "data": {"event_type": "PURE_WHITE_REASONING",
                  "payload": {"seat": 5, "action_type": "check", "target_seat": 6,
                              "reasoning": "查 6 号", "degraded": False, "thought": "…"}}},
        {"timestamp": "t4", "round": 2, "phase": "game_over", "operation": "game_over",
         "data": {"winner": "good", "reason": "all_wolves_dead"}},
    ]
    _write_lines(tmp_path / "games" / "gn" / "game.log", log)

    summary = build_game_summary(str(tmp_path), "gn")

    assert summary["rounds"]["2"]["night_actions"] == [
        {"event": "DANCE_POOL", "seat": 1, "target_seat": None, "result": None,
         "pool_seats": [1, 2, 3]},
        {"event": "MASK_GRANTED", "seat": 4, "target_seat": 2, "result": None,
         "in_pool": True},
        {"event": "PURE_WHITE_REASONING", "seat": 5, "target_seat": 6, "result": None},
    ]


def test_tool_content_fallbacks_are_counted_separately_from_speech_fallbacks() -> None:
    from app.services.game_summary import _llm_aggregate

    result = _llm_aggregate([{
        "data": {
            "call_kind": "action",
            "parse_result": "strict_content_fallback",
            "failure_code": None,
        },
    }])

    assert result["tool_content_fallbacks"] == 1
    assert result["fallbacks"] == 0
    assert result["errors"] == 0
