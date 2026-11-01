"""Incomplete historical facts must not create fabricated benchmark evidence."""

from app.api.benchmark_schemas import BenchmarkCreateRequest
from app.services.benchmark_metrics import BenchmarkMetrics, _bootstrap_block_ratio
from app.services.benchmark_service import BenchmarkService

import pytest


def test_explicit_request_latency_wins_over_corrupt_or_missing_timestamps():
    report = BenchmarkMetrics.compute(
        games=[], items=[], model_attempts=[], model_requests=[
            {"request_id": "explicit", "status": "resolved", "logical_elapsed_ms": 25},
            {"request_id": "broken", "status": "resolved", "created_at": "invalid", "updated_at": "2026-01-01"},
            {"request_id": "backwards", "status": "resolved", "created_at": "2026-01-02", "updated_at": "2026-01-01"},
        ],
    )
    assert report["summary"]["requests"]["logical_latency_ms"] == {
        "count": 1, "p50": 25.0, "p95": 25.0, "p99": 25.0,
    }


def test_mixed_uncertainty_excludes_invalid_blocks_and_incomplete_player_facts():
    game = {"game_id": "game", "winner": "good", "players": [
        {"seat": 1, "camp": "good"},
        {"seat": 2},
    ]}
    assignment = {"variant": "mixed", "seat_models": {
        "1": {"model_config_id": "valid"},
        "2": {"model_config_id": "unknown-camp"},
        "3": {"model_config_id": "missing-player"},
    }}
    items = [
        {"game_id": "game", "assignment": assignment, "block_index": index}
        for index in (0, -1, "bad")
    ]
    mixed = BenchmarkMetrics.compute(
        games=[game], items=items, model_requests=[], model_attempts=[],
    )["summary"]["mixed_uncertainty"]
    assert len(mixed) == 1
    assert mixed[0]["model"] == "valid"
    assert mixed[0]["camp"] == "good"
    assert mixed[0]["valid_seats"] == 1
    assert mixed[0]["block_count"] == 1
    # 单 block 无法 bootstrap，回退 Wilson 95% 区间并标记 interval_method，
    # 报告仍说清没有做过重采样（samples=0 + insufficient_blocks）。
    assert mixed[0]["low"] == 0.206543 and mixed[0]["high"] == 1.0
    assert mixed[0]["interval_method"] == "wilson"
    assert mixed[0]["samples"] == 0
    assert mixed[0]["insufficient_blocks"] is True


def test_empty_resampled_blocks_do_not_generate_nan_intervals():
    # 第三个值是真正抽到的重采样次数：分母全 0 时一次都没抽到，报告必须写 0，
    # 不能再报配置里的 2000（那会让 null 区间读起来像算过的）。
    # 单块：一次都没抽（0）；有块但分母全 0：抽了 2000 次、只是没有可用的比例，
    # 区间同样缺失，但样本数不是 0 —— 报告另用 insufficient_* 说明原因。
    assert _bootstrap_block_ratio({0: (0, 0), 1: (0, 0)}) == (None, None, 2000)
    assert _bootstrap_block_ratio({0: (0, 0), 1: (1, 1)}) == (1.0, 1.0, 2000)
    assert _bootstrap_block_ratio({0: (1, 1)}) == (None, None, 0)


def test_invalid_paired_items_do_not_create_latency_pairs():
    result = BenchmarkMetrics.compute(
        games=[], model_requests=[], model_attempts=[], items=[
            {"pair_id": "pair", "game_id": "game", "variant": "unknown"},
            {"pair_id": "pair", "game_id": None, "variant": "candidate"},
        ],
    )["summary"]["paired_latency_ms"]
    assert result["pair_count"] == 0
    assert result["excluded_incomplete_pairs"] == 0


def test_missing_model_selection_is_rejected_by_scheduler_without_inventing_a_default():
    specification = BenchmarkCreateRequest(
        client_request_id="client", name="Arena", mode="mixed_arena", seed=1,
        role_counts={"villager": 2}, games=1,
    ).frozen_specification()
    with pytest.raises(ValueError, match="requires model configurations"):
        BenchmarkService._mixed_schedule(specification)


def test_paired_regression_without_pairs_reports_no_samples():
    """没有可配对的 pair：`samples` 写 0 并标注原因，不再写配置里的 2000。"""
    report = BenchmarkMetrics.compute(
        games=[], items=[], model_requests=[], model_attempts=[],
    )
    paired = report["summary"]["paired_regression"]["bootstrap"]
    assert paired["samples"] == 0
    assert paired["insufficient_pairs"] is True
    assert paired["low"] is None and paired["high"] is None
    assert paired["seed"] == 1729 and paired["confidence"] == 0.95

