"""Tests for benchmark metrics, reports and comparisons."""

import json
from pathlib import Path

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
