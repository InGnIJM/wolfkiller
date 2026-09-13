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
    assert mixed[0]["low"] is None and mixed[0]["high"] is None


def test_empty_resampled_blocks_do_not_generate_nan_intervals():
    assert _bootstrap_block_ratio({0: (0, 0), 1: (0, 0)}) == (None, None)
    assert _bootstrap_block_ratio({0: (0, 0), 1: (1, 1)}) == (1.0, 1.0)


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
