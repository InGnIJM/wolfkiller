"""Benchmark report rendering: JSON aggregate + Markdown tables + A/B compare."""

from __future__ import annotations

import json
import os
from typing import Optional

_REPORT_JSON = "report.json"
_REPORT_MD = "report.md"


def _fmt_rate(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _fmt_num(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:,.1f}"


def render_markdown(aggregate: dict) -> str:
    """Render one aggregate as a Markdown report."""
    outcome = aggregate.get("outcome") or {}
    decision = aggregate.get("decision") or {}
    compliance = aggregate.get("compliance") or {}
    llm = aggregate.get("llm") or {}
    camps = outcome.get("win_rate_by_camp") or {}
    lines: list[str] = [
        "# 狼人杀 Benchmark 报告", "",
        f"- 对局数量：{outcome.get('games', 0)}（完成 {outcome.get('finished', 0)}"
        f" / 中止 {outcome.get('aborted', 0)}）",
        f"- 平均局长：{_fmt_num(outcome.get('avg_rounds'))} 天", "",
        "## 阵营胜率", "",
        "| 阵营 | 胜率 |", "| --- | --- |",
        f"| 狼人 | {_fmt_rate(camps.get('werewolf'))} |",
        f"| 好人 | {_fmt_rate(camps.get('good'))} |", "",
        "## 角色胜率", "",
        "| 角色 | 场次 | 胜率 |", "| --- | --- | --- |",
    ]
    by_role = outcome.get("win_rate_by_role") or {}
    games_by_role = outcome.get("games_by_role") or {}
    if by_role:
        for role, rate in by_role.items():
            lines.append(
                f"| {role} | {games_by_role.get(role, 0)} | {_fmt_rate(rate)} |",
            )
    else:
        lines.append("| （无完成对局） | - | - |")

    lines += [
        "", "## 决策质量（跨局汇总）", "",
        "| 指标 | 数值 |", "| --- | --- |",
        f"| 预言家验中狼率 | {_fmt_rate(decision.get('seer_check_hit_rate'))} |",
        f"| 女巫毒药命中狼率 | {_fmt_rate(decision.get('witch_poison_hit_rate'))} |",
        f"| 女巫救药使用次数 | {decision.get('witch_save_uses', 0)} |",
        f"| 猎人开枪带狼率 | {_fmt_rate(decision.get('hunter_shot_hit_rate'))} |",
        f"| 狼刀命中神职率 | {_fmt_rate(decision.get('wolf_kill_god_rate'))} |",
        f"| 好人投票命中率 | {_fmt_rate(decision.get('good_vote_hit_rate'))} |",
        f"| 好人投票弃票率 | {_fmt_rate(decision.get('good_vote_abstain_rate'))} |",
        f"| 放逐命中狼率 | {_fmt_rate(decision.get('exile_wolf_rate'))} |",
        f"| 放逐误伤神职率 | {_fmt_rate(decision.get('exile_god_misfire_rate'))} |",
        f"| 狼队投票集中度 | {_fmt_rate(decision.get('wolf_vote_concentration'))} |",
        "", "## 规范性", "",
        f"- LLM 调用总数：{compliance.get('llm_calls', 0)}"
        f"（fallback 率 {_fmt_rate(compliance.get('llm_fallback_rate'))}，"
        f"错误率 {_fmt_rate(compliance.get('llm_error_rate'))}）",
        f"- 技术性弃票：{compliance.get('technical_abstains', 0)} 次",
        f"- 模型错误记录：{compliance.get('model_errors', 0)} 条", "",
        "## LLM 成本与延迟", "",
        "| 指标 | 数值 |", "| --- | --- |",
    ]
    tokens = llm.get("tokens_per_game") or {}
    lines += [
        f"| 每局 prompt tokens | {_fmt_num(tokens.get('prompt'))} |",
        f"| 每局 completion tokens | {_fmt_num(tokens.get('completion'))} |",
        f"| 每局总 tokens | {_fmt_num(tokens.get('total'))} |",
        f"| 每局 LLM 调用次数 | {_fmt_num(llm.get('calls_per_game'))} |",
        f"| 调用延迟 p50 (ms) | {_fmt_num(llm.get('elapsed_ms_p50'))} |",
        f"| 调用延迟 p95 (ms) | {_fmt_num(llm.get('elapsed_ms_p95'))} |",
        f"| 调用延迟 p99 (ms) | {_fmt_num(llm.get('elapsed_ms_p99'))} |",
        "",
    ]
    return "\n".join(lines)


# Numeric paths that participate in A/B comparisons.
_COMPARE_PATHS = (
    ("outcome", "win_rate_by_camp", "werewolf"),
    ("outcome", "win_rate_by_camp", "good"),
    ("outcome", "avg_rounds"),
    ("decision", "seer_check_hit_rate"),
    ("decision", "witch_poison_hit_rate"),
    ("decision", "hunter_shot_hit_rate"),
    ("decision", "wolf_kill_god_rate"),
    ("decision", "good_vote_hit_rate"),
    ("decision", "exile_wolf_rate"),
    ("llm", "tokens_per_game", "total"),
    ("llm", "calls_per_game"),
    ("llm", "elapsed_ms_p95"),
)


def _dig(aggregate: dict, path: tuple[str, ...]) -> Optional[float]:
    node: object = aggregate
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, (int, float)) and not isinstance(node, bool) else None


def compare(base: dict, candidate: dict) -> list[dict]:
    """Compare two aggregates over the shared numeric metric paths."""
    rows: list[dict] = []
    for path in _COMPARE_PATHS:
        base_value = _dig(base, path)
        candidate_value = _dig(candidate, path)
        if base_value is None or candidate_value is None:
            continue
        rows.append({
            "metric": ".".join(path),
            "base": base_value,
            "candidate": candidate_value,
            "delta": round(candidate_value - base_value, 4),
        })
    return rows


def render_comparison_markdown(base: dict, candidate: dict) -> str:
    """Render a Markdown delta table between two aggregates."""
    rows = compare(base, candidate)
    lines = [
        "# Benchmark 对比报告", "",
        "| 指标 | 基线 | 候选 | 变化 |", "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['metric']} | {row['base']} | {row['candidate']} "
            f"| {row['delta']:+} |",
        )
    lines.append("")
    return "\n".join(lines)


def write_report(out_dir: str, aggregate: dict) -> dict:
    """Persist report.json and report.md; returns the written paths."""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, _REPORT_JSON)
    md_path = os.path.join(out_dir, _REPORT_MD)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(aggregate, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(aggregate))
    return {"json": json_path, "markdown": md_path}
