"""Tests for benchmark metrics, reports and comparisons."""

import json
from pathlib import Path

import pytest

from app.benchmark.metrics import (
    aggregate,
    compute_compliance_metrics,
    compute_decision_metrics,
    compute_llm_metrics,
    compute_outcome_metrics,
)
from app.benchmark.report import (
    _dig,
    compare,
    render_comparison_markdown,
    render_markdown,
    write_report,
)


def _roles() -> dict:
    return {
        "1": {"role": "wolf-killer-seer", "camp": "good"},
        "2": {"role": "wolf-killer-werewolf", "camp": "werewolf"},
        "3": {"role": "wolf-killer-witch", "camp": "good"},
        "4": {"role": "wolf-killer-villager", "camp": "good"},
    }


def test_role_classifiers_cover_idiot_and_werewolf_king() -> None:
    from app.benchmark.metrics import _is_god, _is_wolf_role

    assert _is_god("wolf-killer-idiot") is True
    assert _is_god("wolf-killer-werewolf-king") is False
    assert _is_wolf_role("wolf-killer-werewolf-king") is True
    assert _is_wolf_role("wolf-killer-werewolf") is True
    assert _is_wolf_role("wolf-killer-idiot") is False
    assert _is_wolf_role(None) is False


def _llm_block() -> dict:
    return {
        "calls": 4, "errors": 1, "fallbacks": 1,
        "tokens": {"prompt": 1000, "completion": 200, "total": 1200},
        "elapsed_ms": {"p50": 1000, "p95": 2000, "p99": 3000},
        "calls_by_kind": {"action": 2, "speech": 1, "night": 1},
    }


def _summary_g1() -> dict:
    return {
        "game_id": "g1",
        "winner": "werewolf",
        "rounds_played": 3,
        "roles": _roles(),
        "model_errors": 2,
        "llm": _llm_block(),
        "rounds": {
            "1": {
                "technical_abstains": 1,
                "exiled": 2,
                "votes": [
                    {"seat": 1, "target": 2},
                    {"seat": 2, "target": 1},
                    {"seat": 3, "target": None},
                    {"seat": 4, "target": 2},
                ],
                "night_actions": [
                    {"event": "SEER_CHECK", "target_seat": 2, "result": "werewolf"},
                    {"event": "WEREWOLF_KILL", "target_seat": 3},
                    {"event": "WITCH_POISON", "target_seat": 4},
                    {"event": "HUNTER_SHOT", "target_seat": 2},
                    {"event": "WITCH_SAVE", "target_seat": 4},
                ],
            },
        },
    }


def _summary_g2() -> dict:
    summary = _summary_g1()
    summary["game_id"] = "g2"
    summary["winner"] = "good"
    summary["rounds_played"] = 5
    summary["model_errors"] = 0
    summary["llm"] = {
        "calls": 2, "errors": 0, "fallbacks": 0,
        "tokens": {"prompt": 500, "completion": 100, "total": 600},
        "elapsed_ms": {"p50": 800, "p95": 1200, "p99": 1200},
        "calls_by_kind": {"action": 1, "speech": 1},
    }
    summary["rounds"] = {
        "1": {
            "technical_abstains": 0,
            "exiled": 2,
            "votes": [
                {"seat": 1, "target": 2},
                {"seat": 2, "target": 3},
                {"seat": 3, "target": 2},
            ],
            "night_actions": [
                {"event": "SEER_CHECK", "target_seat": 3, "result": "good"},
                {"event": "WEREWOLF_KILL", "target_seat": 4},
            ],
        },
    }
    return summary


def test_outcome_metrics_pooled() -> None:
    outcome = compute_outcome_metrics([_summary_g1(), _summary_g2()])

    assert outcome["games"] == 2
    assert outcome["finished"] == 2
    assert outcome["aborted"] == 0
    assert outcome["win_rate_by_camp"] == {"werewolf": 0.5, "good": 0.5}
    assert outcome["win_rate_by_role"]["wolf-killer-werewolf"] == 0.5
    assert outcome["win_rate_by_role"]["wolf-killer-seer"] == 0.5
    assert outcome["games_by_role"]["wolf-killer-witch"] == 2
    assert outcome["avg_rounds"] == 4.0


def test_decision_metrics_pooled() -> None:
    decision = compute_decision_metrics([_summary_g1(), _summary_g2()])

    assert decision["seer_check_hit_rate"] == 0.5
    assert decision["witch_save_uses"] == 1
    assert decision["witch_poison_uses"] == 1
    assert decision["witch_poison_hit_rate"] == 0.0
    assert decision["hunter_shot_hit_rate"] == 1.0
    assert decision["wolf_kill_god_rate"] == 0.5
    assert decision["good_vote_hit_rate"] == 0.8
    assert decision["good_vote_abstain_rate"] == 0.2
    assert decision["exile_wolf_rate"] == 1.0
    assert decision["exile_god_misfire_rate"] == 0.0
    assert decision["wolf_vote_distinct_targets"] == 2
    assert decision["wolf_vote_concentration"] == 0.5


def test_compliance_metrics_pooled() -> None:
    compliance = compute_compliance_metrics([_summary_g1(), _summary_g2()])

    assert compliance["llm_calls"] == 6
    assert compliance["llm_error_rate"] == 0.1667
    assert compliance["llm_fallback_rate"] == 0.1667
    assert compliance["technical_abstains"] == 1
    assert compliance["model_errors"] == 2


def test_llm_metrics_averaged_per_game() -> None:
    llm = compute_llm_metrics([_summary_g1(), _summary_g2()])

    assert llm["tokens_per_game"] == {
        "prompt": 750.0, "completion": 150.0, "total": 900.0,
    }
    assert llm["calls_per_game"] == 3.0
    assert llm["elapsed_ms_p50"] == 900.0
    assert llm["elapsed_ms_p95"] == 1600.0
    assert llm["elapsed_ms_p99"] == 2100.0


def test_aggregate_on_empty_input() -> None:
    result = aggregate([])

    assert result["games"] == 0
    assert result["outcome"]["win_rate_by_camp"] == {"werewolf": None, "good": None}
    assert result["outcome"]["avg_rounds"] is None
    assert result["decision"]["seer_check_hit_rate"] is None
    assert result["compliance"]["llm_fallback_rate"] is None
    assert result["llm"]["tokens_per_game"]["total"] is None
    assert result["llm"]["elapsed_ms_p99"] is None


def test_metrics_tolerate_malformed_summaries() -> None:
    broken = {
        "rounds": {
            "1": {
                "night_actions": [
                    "junk",
                    {"event": "WITCH_POISON", "target_seat": 1},
                    {"event": "HUNTER_SHOT", "target_seat": 3},
                    {"event": "GUARD_PROTECT", "target_seat": 6},
                ],
                "votes": [
                    "junk",
                    {"seat": 99, "target": 1},
                    {"seat": 4, "target": 3},
                    {"seat": 1, "target": None},
                    {"seat": 1, "target": 3},
                ],
                "exiled": 1,
                "technical_abstains": 0,
            },
            "2": {"exiled": 4, "technical_abstains": 0},
            "3": {"exiled": 6, "technical_abstains": 0},
            "4": {"exiled": 5, "technical_abstains": 0},
            "5": {"exiled": None, "technical_abstains": 0},
            "junk": "junk",
        },
        "roles": {
            "1": {"role": "wolf-killer-werewolf", "camp": "werewolf"},
            "4": {"role": "wolf-killer-seer", "camp": "good"},
            "6": {"role": "wolf-killer-villager", "camp": "good"},
            "2": "junk",
            "5": {"camp": "good"},
        },
        "llm": {"calls": 1},
        "rounds_played": "3",
        "winner": None,
        "model_errors": "x",
    }
    no_details = {"rounds": "junk", "roles": "junk"}
    decision = compute_decision_metrics([broken, "not-a-dict", no_details])
    outcome = compute_outcome_metrics([
        broken, "not-a-dict", no_details,
        {"winner": "good", "roles": {"9": {"role": 42}}, "rounds_played": 4},
    ])
    compliance = compute_compliance_metrics([
        broken, "not-a-dict", no_details,
        {"llm": {"calls": 2}, "rounds": "junk"},
        {"llm": "junk", "rounds": {"1": {"technical_abstains": 2}}},
    ])
    llm = compute_llm_metrics([
        broken, "not-a-dict", no_details, {"llm": {"calls": 2}, "rounds": "junk"},
    ])

    assert decision["seer_check_hit_rate"] is None
    assert decision["witch_poison_hit_rate"] == 1.0
    assert decision["hunter_shot_hit_rate"] == 0.0
    assert decision["good_vote_hit_rate"] == 0.0
    assert decision["good_vote_abstain_rate"] == 0.0
    assert decision["exile_wolf_rate"] == 0.25
    assert decision["exile_god_misfire_rate"] == 0.25
    assert decision["wolf_vote_concentration"] == 0.5
    assert decision["wolf_vote_distinct_targets"] == 1
    assert outcome["finished"] == 1
    assert outcome["avg_rounds"] == 4.0
    assert outcome["win_rate_by_camp"]["good"] == 1.0
    assert outcome["games_by_role"] == {}
    assert compliance["llm_calls"] == 3
    assert compliance["llm_error_rate"] == 0.0
    assert compliance["technical_abstains"] == 2
    assert compliance["model_errors"] == 0
    assert llm["calls_per_game"] == 1.5
    assert llm["tokens_per_game"]["total"] == 0.0


def test_dig_reads_nested_numeric_paths() -> None:
    data = {"a": {"b": {"c": 1.5}}, "n": "text"}

    assert _dig(data, ("a", "b", "c")) == 1.5
    assert _dig(data, ("a", "b", "missing")) is None
    assert _dig(data, ("n",)) is None
    assert _dig(data, ("a", "deep", "deeper")) is None


def test_compare_reports_delta_over_shared_paths() -> None:
    base = {"outcome": {"win_rate_by_camp": {"werewolf": 0.4, "good": 0.6},
                        "avg_rounds": 4.0}}
    candidate = {"outcome": {"win_rate_by_camp": {"werewolf": 0.55, "good": 0.45},
                             "avg_rounds": 4.0}}

    rows = compare(base, candidate)

    assert rows == [
        {"metric": "outcome.win_rate_by_camp.werewolf",
         "base": 0.4, "candidate": 0.55, "delta": 0.15},
        {"metric": "outcome.win_rate_by_camp.good",
         "base": 0.6, "candidate": 0.45, "delta": -0.15},
        {"metric": "outcome.avg_rounds",
         "base": 4.0, "candidate": 4.0, "delta": 0.0},
    ]


def test_render_markdown_contains_sections() -> None:
    markdown = render_markdown(aggregate([_summary_g1()]))

    assert "# 狼人杀 Benchmark 报告" in markdown
    assert "## 阵营胜率" in markdown
    assert "| 狼人 | 100.0% |" in markdown
    assert "## 角色胜率" in markdown
    assert "wolf-killer-seer" in markdown
    assert "## 决策质量（跨局汇总）" in markdown
    assert "| 预言家验中狼率 | 100.0% |" in markdown
    assert "## LLM 成本与延迟" in markdown
    assert "| 每局总 tokens | 1,200.0 |" in markdown


def test_render_markdown_handles_empty_aggregate() -> None:
    markdown = render_markdown(aggregate([]))

    assert "（无完成对局）" in markdown
    assert "n/a" in markdown


def test_render_comparison_markdown() -> None:
    base = {"outcome": {"win_rate_by_camp": {"werewolf": 0.4}}}
    candidate = {"outcome": {"win_rate_by_camp": {"werewolf": 0.6}}}

    markdown = render_comparison_markdown(base, candidate)

    assert "# Benchmark 对比报告" in markdown
    assert "| outcome.win_rate_by_camp.werewolf | 0.4 | 0.6 | +0.2 |" in markdown


def test_write_report_persists_json_and_markdown(tmp_path: Path) -> None:
    aggregate_data = aggregate([_summary_g1()])

    paths = write_report(str(tmp_path), aggregate_data)

    written = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert written == aggregate_data
    assert Path(paths["markdown"]).read_text(encoding="utf-8").startswith(
        "# 狼人杀 Benchmark 报告",
    )


def _native_metric_facts() -> dict:
    players = [
        {"seat": 1, "role_id": "wolf-killer-seer", "camp_id": "good"},
        {"seat": 2, "role_id": "wolf-killer-werewolf", "camp_id": "werewolf"},
    ]
    return {
        "games": [
            {"game_id": "g-b1", "winner": "good", "rounds": 3,
             "duration_ms": 100, "players": players},
            {"game_id": "g-c1", "winner": "werewolf", "rounds": 5,
             "duration_ms": 300, "players": players},
            {"game_id": "g-b2", "winner": "werewolf", "rounds": 4,
             "duration_ms": 200, "players": players},
            {"game_id": "g-c2", "winner": "werewolf", "rounds": 4,
             "duration_ms": 400, "players": players},
        ],
        "items": [
            {"game_id": "g-b1", "pair_id": "p1", "variant": "baseline",
             "evaluation_camp": "good", "assignment": {
                 "model": {"model_config_id": "base"}, "seats": [1, 2],
             }},
            {"game_id": "g-c1", "pair_id": "p1", "variant": "candidate",
             "evaluation_camp": "good", "assignment": {
                 "model": {"model_config_id": "candidate"}, "seats": [1, 2],
             }},
            {"game_id": "g-b2", "pair_id": "p2", "variant": "baseline",
             "evaluation_camp": "werewolf", "assignment": {
                 "model": {"model_config_id": "base"}, "seats": [1, 2],
             }},
            {"game_id": "g-c2", "pair_id": "p2", "variant": "candidate",
             "evaluation_camp": "werewolf", "assignment": {
                 "model": {"model_config_id": "candidate"}, "seats": [1, 2],
             }},
        ],
        "model_requests": [
            {"game_id": "g-b1", "request_id": "r1", "status": "resolved",
             "recovery_retry_count": 1,
             "normalized_result": {"action_type": "vote", "target_seat": 2}},
            {"game_id": "g-c1", "request_id": "r2", "status": "resolved",
             "normalized_result": {"action_type": "abstain"}},
            {"game_id": "g-b2", "request_id": "r3", "status": "failed"},
            {"game_id": "g-c2", "request_id": "r4", "status": "succeeded"},
        ],
        "model_attempts": [
            {"attempt_id": "a1", "game_id": "g-b1", "request_id": "r1",
             "status": "succeeded", "elapsed_ms": 100,
             "usage_known": True, "prompt_tokens": 10,
             "completion_tokens": 5, "total_tokens": 15},
            {"attempt_id": "a2", "game_id": "g-b1", "request_id": "r1",
             "status": "failed", "elapsed_ms": 200,
             "usage_known": False},
            {"attempt_id": "a3", "game_id": "g-c1", "request_id": "r2",
             "status": "succeeded", "elapsed_ms": 300,
             "usage_known": True, "prompt_tokens": 20,
             "completion_tokens": 10, "total_tokens": 30},
            {"attempt_id": "a4", "game_id": "g-c2", "request_id": "r4",
             "execution_generation": 2,
             "status": "succeeded", "elapsed_ms": 400,
             "usage_known": False},
        ],
    }


def test_native_benchmark_metrics_cover_outcomes_requests_and_attempts() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    facts = _native_metric_facts()
    report = BenchmarkMetrics.compute(**facts)

    assert report["metric_version"] == "v2"
    assert len(report["input_digest"]) == 64
    summary = report["summary"]
    assert summary["games"] == {
        "total": 4,
        "completed": 4,
        "interrupted_games": 0,
        "interruption_rate": 0.0,
        "win_rate_by_camp": {"good": 0.25, "werewolf": 0.75},
        "win_rate_by_seat": {
            "1": {"games": 4, "wins": 1, "win_rate": 0.25},
            "2": {"games": 4, "wins": 3, "win_rate": 0.75},
        },
        "win_rate_by_role": {
            "wolf-killer-seer": {"games": 4, "wins": 1, "win_rate": 0.25},
            "wolf-killer-werewolf": {"games": 4, "wins": 3, "win_rate": 0.75},
        },
        "average_rounds": 4.0,
        "average_duration_ms": 250.0,
    }
    assert summary["requests"] == {
        "total": 4, "successful": 2, "abstained": 1, "failed": 1,
        "pending": 0, "cancelled": 0, "eligible_terminal": 4,
        "unknown": 0, "valid": 3, "technical_abstained": 0,
        "terminal_votes": 0, "fallback": 1,
        "valid_rate": 0.75,
        "success_rate": 0.5, "abstain_rate": 0.25, "failure_rate": 0.25,
        "fallback_rate": 0.25, "technical_abstain_rate": None,
        "logical_latency_ms": {
            "count": 0, "p50": None, "p95": None, "p99": None,
        },
    }
    assert summary["attempts"]["latency_ms"] == {
        "count": 4, "p50": 250.0, "p95": 385.0, "p99": 397.0,
    }
    assert summary["attempts"]["known_tokens"] == {
        "usage_count": 2, "prompt_tokens": 30,
        "completion_tokens": 15, "total_tokens": 45,
    }
    assert summary["attempts"]["unknown_usage_count"] == 2
    assert summary["attempts"]["usage_completeness_rate"] == 0.5
    assert summary["attempts"]["token_usage"] == {
        "known_attempts": 2,
        "unknown_attempts": 2,
        "known_prompt_tokens": 30,
        "known_completion_tokens": 15,
        "known_total_tokens": 45,
        "completeness_rate": 0.5,
    }
    assert summary["requests"]["unknown"] == 0
    assert summary["attempts"]["executed_requests"] == 3
    assert summary["attempts"]["retried_requests"] == 1

    performance = {
        (row["model"], row.get("role"), row.get("camp")):
        (row["samples"], row["wins"], row["win_rate"])
        for row in summary["model_performance"]
    }
    assert performance[("base", "wolf-killer-seer", None)] == (2, 1, 0.5)
    assert performance[("base", None, "good")] == (2, 1, 0.5)
    assert performance[("candidate", "wolf-killer-werewolf", None)] == (2, 2, 1.0)
    assert performance[("candidate", None, "werewolf")] == (2, 2, 1.0)


def test_native_benchmark_digest_and_bootstrap_are_order_independent() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    facts = _native_metric_facts()
    first = BenchmarkMetrics.compute(**facts)
    reordered = {
        name: [dict(reversed(tuple(row.items()))) for row in reversed(rows)]
        for name, rows in facts.items()
    }
    second = BenchmarkMetrics.compute(**reordered)

    assert second == first
    paired = first["summary"]["paired_regression"]
    assert paired["pair_count"] == 2
    assert paired["mean_difference"] == -0.5
    assert paired["differences"] == [
        {"pair_id": "p1", "baseline": 1.0, "candidate": 0.0,
         "difference": -1.0},
        {"pair_id": "p2", "baseline": 1.0, "candidate": 1.0,
         "difference": 0.0},
    ]
    assert paired["bootstrap"] == {
        "seed": 1729, "samples": 2000, "confidence": 0.95,
        "low": -1.0, "high": 0.0,
    }


def test_native_benchmark_metrics_empty_facts_are_explicit() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    report = BenchmarkMetrics.compute(
        games=(), items=(), model_requests=(), model_attempts=(),
    )

    assert report["summary"]["games"]["average_rounds"] is None
    assert report["summary"]["games"]["win_rate_by_camp"] == {}
    assert report["summary"]["requests"]["success_rate"] is None
    assert report["summary"]["attempts"]["latency_ms"] == {
        "count": 0, "p50": None, "p95": None, "p99": None,
    }
    assert report["summary"]["paired_regression"]["bootstrap"]["low"] is None


def test_native_metrics_never_infer_camp_from_role_name() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    report = BenchmarkMetrics.compute(
        games=[{
            "game_id": "g", "winner": "good",
            "players": [{"seat": 1, "role_id": "wolf-killer-seer"}],
        }],
        items=[], model_requests=[], model_attempts=[],
    )

    games = report["summary"]["games"]
    assert games["win_rate_by_seat"] == {}
    assert games["win_rate_by_role"] == {}


def test_native_metrics_reject_noncanonical_or_non_tabular_facts() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    valid = {
        "games": [], "items": [], "model_requests": [], "model_attempts": [],
    }
    with pytest.raises(TypeError, match="games must be a sequence"):
        BenchmarkMetrics.compute(**{**valid, "games": "not rows"})
    with pytest.raises(TypeError, match="games must be a sequence"):
        BenchmarkMetrics.compute(**{**valid, "games": {"row"}})
    with pytest.raises(TypeError, match="only dictionaries"):
        BenchmarkMetrics.compute(**{**valid, "games": ["row"]})
    with pytest.raises(ValueError, match="canonical JSON"):
        BenchmarkMetrics.compute(**{**valid, "games": [{"rounds": float("nan")}]})


def test_native_metrics_accept_legacy_player_and_request_shapes_without_inference() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    report = BenchmarkMetrics.compute(
        games=[
            {
                "game_id": "mapped", "winner_camp": "good",
                "rounds_played": -1, "active_elapsed_ms": "unknown",
                "roles": {
                    "": {"role": "seer", "camp": "good"},
                    "1": {"role": "villager", "camp": "good"},
                    "2": "legacy-role-without-camp",
                    "3": 3,
                },
            },
            {"game_id": "invalid-players", "winner": "good", "players": 42},
            {"game_id": "invalid-seat", "winner": "good", "players": [
                {"seat": True, "role": "villager", "camp": "good"},
            ]},
        ],
        items=[],
        model_requests=[
            {"status": "resolved", "normalized_result": "not-json"},
            {"status": "resolved", "normalized_result": "[]"},
            {"status": 7, "normalized_result": None},
            {"status": "resolved", "normalized_result": {
                "action_type": "technical_abstain",
            }},
        ],
        model_attempts=[{
            "game_id": "mapped", "request_id": "request", "usage_known": True,
            "elapsed_ms": -1, "prompt_tokens": True,
            "completion_tokens": "2", "total_tokens": -3,
        }],
    )

    assert report["summary"]["games"]["completed"] == 3
    assert report["summary"]["games"]["average_rounds"] is None
    assert report["summary"]["games"]["win_rate_by_seat"] == {
        "1": {"games": 1, "wins": 1, "win_rate": 1.0},
    }
    assert report["summary"]["requests"]["technical_abstained"] == 1
    assert report["summary"]["requests"]["successful"] == 3
    assert report["summary"]["requests"]["failed"] == 1
    assert report["summary"]["attempts"]["known_tokens"]["total_tokens"] == 0


def test_assignment_and_model_helpers_cover_legacy_and_malformed_shapes() -> None:
    from app.services import benchmark_metrics as metrics

    assert metrics._assignment({"assignment_json": {"variant": "baseline"}}) == {
        "variant": "baseline",
    }
    assert metrics._assignment({"assignment_json": '{"variant":"candidate"}'}) == {
        "variant": "candidate",
    }
    assert metrics._assignment({"assignment_json": "not-json"}) == {}
    assert metrics._assignment({"assignment_json": "[]"}) == {}
    assert metrics._assignment({}) == {}

    assert metrics._model_name(None) == "default"
    assert metrics._model_name({"model_config_id": ""}) == "default"
    assert metrics._model_name({"model_config_id": 3, "config_id": "config"}) == "config"
    assert metrics._model_name({"model_id": "provider"}) == "provider"
    assert metrics._model_name({"name": "friendly"}) == "friendly"

    assert metrics._seat_number(1) == 1
    assert metrics._seat_number(0) is None
    assert metrics._seat_number("2") == 2
    assert metrics._seat_number("seat") is None
    assert metrics._seat_number(True) is None

    assert metrics._assigned_models({
        "seat_models": {
            "1": {"model_config_id": "a"},
            "bad": {"model_config_id": "b"},
            "2": None,
        },
    }) == [(1, "a"), (2, "default")]
    assert metrics._assigned_models({"seats": 1}) == []
    assert metrics._assigned_models({"seats": "1"}) == []
    assert metrics._assigned_models({
        "model": {"model_config_id": "a"}, "seats": [1, "bad", -1],
    }) == [(1, "a")]


def test_model_performance_excludes_unusable_seats_and_keeps_camp_only_rows() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    report = BenchmarkMetrics.compute(
        games=[
            {"game_id": "game", "winner": "good", "players": {
                "1": {"camp": "good"},
                "2": {"role": "seer"},
            }},
            {"game_id": "unfinished", "winner": None, "players": {}},
        ],
        items=[
            {"game_id": "game", "assignment": {"seat_models": {
                "1": {"model_config_id": "a"},
                "2": {"model_config_id": "a"},
                "3": {"model_config_id": "a"},
            }}},
            {"game_id": "missing", "assignment": {"seat_models": {}}},
            {"game_id": 3, "assignment": {"seat_models": {}}},
        ],
        model_requests=[], model_attempts=[],
    )

    assert report["summary"]["model_performance"] == [{
        "id": "a:camp:good", "model": "a", "samples": 1,
        "wins": 1, "win_rate": 1.0, "camp": "good",
    }]


def test_paired_regression_supports_explicit_scores_wins_and_legacy_aliases() -> None:
    from app.services import benchmark_metrics as metrics

    games = [
        {"game_id": "g1", "winner": "good"},
        {"game_id": "g2", "winner": None},
    ]
    items = [
        {"pair_id": "score", "arm": "control", "score": 0.25},
        {"pair_id": "score", "arm": "treatment", "won": True},
        {"pair_id": "camp", "variant": "baseline", "game_id": "g1",
         "target_camp": "good"},
        {"pair_id": "camp", "variant": "candidate", "game_id": "g1",
         "evaluation_camp": "werewolf"},
        {"pair_id": "incomplete", "variant": "baseline", "score": 1},
        {"pair_id": "", "variant": "candidate", "score": 1},
        {"pair_id": 7, "variant": "candidate", "score": 1},
        {"pair_id": "bad-variant", "variant": "other", "score": 1},
        {"pair_id": "bad-score", "variant": "baseline"},
        {"pair_id": "no-game", "variant": "baseline", "target_camp": "good"},
        {"pair_id": "no-winner", "variant": "baseline", "game_id": "g2",
         "target_camp": "good"},
    ]

    result = metrics._paired_regression(games, items)

    assert result["pair_count"] == 2
    assert result["differences"] == [
        {"pair_id": "camp", "baseline": 1.0, "candidate": 0.0,
         "difference": -1.0},
        {"pair_id": "score", "baseline": 0.25, "candidate": 1.0,
         "difference": 0.75},
    ]


def test_native_request_metrics_separate_terminal_quality_fallback_and_vote_abstention() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    report = BenchmarkMetrics.compute(
        games=[], items=[], model_attempts=[],
        model_requests=[
            {
                "request_id": "pass", "status": "consumed",
                "action_position": "round:1:day:speech:seat:1",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00.250000+00:00",
                "normalized_result": {"action_type": "pass"},
            },
            {
                "request_id": "technical-vote", "status": "failed",
                "action_position": "round:1:day:vote:seat:2",
                "created_at": "2026-01-01T00:00:01Z",
                "updated_at": "2026-01-01T00:00:01.750000Z",
            },
            {
                "request_id": "cancelled", "status": "cancelled",
                "action_position": "round:1:day:vote:seat:3",
            },
            {
                "request_id": "unknown", "status": "unknown",
                "action_position": "round:1:day:vote:seat:4",
            },
        ],
    )

    requests = report["summary"]["requests"]
    assert requests["eligible_terminal"] == 2
    assert requests["valid"] == 1
    assert requests["valid_rate"] == 0.5
    assert requests["fallback"] == 1
    assert requests["fallback_rate"] == 0.5
    assert requests["terminal_votes"] == 1
    assert requests["technical_abstained"] == 1
    assert requests["technical_abstain_rate"] == 1.0
    assert requests["logical_latency_ms"] == {
        "count": 2, "p50": 500.0, "p95": 725.0, "p99": 745.0,
    }


def test_mixed_uncertainty_resamples_complete_rotation_blocks() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    players = [
        {"seat": 1, "role_id": "seer", "camp_id": "good"},
        {"seat": 2, "role_id": "werewolf", "camp_id": "werewolf"},
    ]
    games = [
        {"game_id": "g1", "winner": "good", "players": players},
        {"game_id": "g2", "winner": "werewolf", "players": players},
        {"game_id": "g3", "winner": "good", "players": players},
        {"game_id": "g4", "winner": "good", "players": players},
    ]
    items = [
        {
            "game_id": game["game_id"], "block_index": index // 2,
            "assignment": {"variant": "mixed", "seat_models": {
                "1": {"model_config_id": "model-a"},
                "2": {"model_config_id": "model-b"},
            }},
        }
        for index, game in enumerate(games)
    ]

    mixed = BenchmarkMetrics.compute(
        games=games, items=items, model_requests=[], model_attempts=[],
    )["summary"]["mixed_uncertainty"]

    good = next(
        row for row in mixed
        if row["model"] == "model-a" and row.get("camp") == "good"
    )
    assert good["games"] == 4
    assert good["valid_seats"] == 4
    assert good["block_count"] == 2
    assert good["confidence"] == 0.95
    assert good["samples"] == 2000
    assert good["low"] == 0.5
    assert good["high"] == 1.0


def test_paired_latency_excludes_interrupted_and_incomplete_pairs() -> None:
    from app.services.benchmark_metrics import BenchmarkMetrics

    games = [
        {"game_id": "b1", "winner": "good", "interruption_count": 0},
        {"game_id": "c1", "winner": "good", "interruption_count": 0},
        {"game_id": "b2", "winner": "good", "interruption_count": 1},
        {"game_id": "c2", "winner": "good", "interruption_count": 0},
        {"game_id": "b3", "winner": "good", "interruption_count": 0},
        {"game_id": "c3", "winner": "good", "interruption_count": 0},
    ]
    items = [
        {"game_id": f"{arm}{pair}", "pair_id": f"p{pair}",
         "variant": "baseline" if arm == "b" else "candidate",
         "score": 1}
        for pair in range(1, 4) for arm in ("b", "c")
    ]
    attempts = [
        {"game_id": "b1", "request_id": "r", "elapsed_ms": 100},
        {"game_id": "c1", "request_id": "r", "elapsed_ms": 120},
        {"game_id": "b2", "request_id": "r", "elapsed_ms": 200},
        {"game_id": "c2", "request_id": "r", "elapsed_ms": 260},
        {"game_id": "b3", "request_id": "r", "elapsed_ms": 300},
    ]

    paired = BenchmarkMetrics.compute(
        games=games, items=items, model_requests=[], model_attempts=attempts,
    )["summary"]["paired_latency_ms"]

    assert paired["pair_count"] == 1
    assert paired["excluded_interrupted_pairs"] == 1
    assert paired["excluded_incomplete_pairs"] == 1
    assert paired["mean_difference"] == 20.0
    assert paired["differences"] == [{
        "pair_id": "p1", "baseline": 100.0,
        "candidate": 120.0, "difference": 20.0,
    }]
    assert paired["bootstrap"]["low"] == 20.0
    assert paired["bootstrap"]["high"] == 20.0
