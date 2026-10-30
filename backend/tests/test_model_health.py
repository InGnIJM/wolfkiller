"""The cross-call provider health monitor."""

from __future__ import annotations

import pytest

from app.agents.failure_taxonomy import FailureKind
from app.agents.model_health import (
    AbortPolicy,
    CallSample,
    HealthState,
    MODE_PAUSE,
    MODE_WARN,
    ProviderHealthMonitor,
    policy_from_env,
    resolve_abort_policy,
)

KEY = "openai|https://model.test|model-a"


def sample(
    seat: int, *, kind: FailureKind = FailureKind.TRANSIENT,
    code: str | None = "provider_server_error", at: float = 0.0,
    contract: str = "night_action", key: str = KEY, ok: bool = False,
) -> CallSample:
    return CallSample(
        seat=seat, provider_key=key, contract_id=contract,
        kind=kind, failure_code=code, at=at, ok=ok,
    )


def test_terminal_needs_two_distinct_seats():
    monitor = ProviderHealthMonitor(AbortPolicy())
    assert monitor.record(sample(1, kind=FailureKind.TERMINAL, code="provider_auth_error")) is None
    event = monitor.record(sample(2, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    assert event is not None
    assert event.rule == "provider_terminal"
    assert event.seats == (1, 2)
    assert monitor.open_event(KEY) == event


def test_request_shape_trips_only_with_three_contracts_and_seats():
    monitor = ProviderHealthMonitor(AbortPolicy())
    for seat in (1, 2, 3):
        assert monitor.record(
            sample(seat, kind=FailureKind.REQUEST_SHAPE, code="provider_bad_request"),
        ) is None
    assert monitor.open_event(KEY) is None

    monitor = ProviderHealthMonitor(AbortPolicy())
    events = [
        monitor.record(sample(
            seat, kind=FailureKind.REQUEST_SHAPE,
            code="provider_bad_request", contract=contract,
        ))
        for seat, contract in ((1, "c1"), (2, "c2"), (3, "c3"))
    ]
    assert events[0] is None and events[1] is None
    assert events[2] is not None
    assert events[2].rule == "request_shape"
    assert events[2].contracts == ("c1", "c2", "c3")


def test_sustained_transient_needs_five_failures_and_three_seats():
    monitor = ProviderHealthMonitor(AbortPolicy())
    results = [monitor.record(sample(seat, at=float(index))) for index, seat in enumerate((1, 2, 3, 1, 2))]
    assert results[:4] == [None, None, None, None]
    assert results[4] is not None
    assert results[4].rule == "provider_sustained"


def test_failure_ratio_trips_at_eighty_percent():
    monitor = ProviderHealthMonitor(AbortPolicy())
    seats = (1, 2, 3, 1, 2, 3, 1, 2, 3, 1)
    events = []
    for index, seat in enumerate(seats):
        if index in (2, 5):
            events.append(monitor.record(
                sample(seat, at=float(index), ok=True),
            ))
            continue
        events.append(monitor.record(sample(seat, at=float(index))))
    # 8 failures of 10 samples, spread over 3 seats, never 5 in a row.
    assert events[-1] is not None
    assert events[-1].rule == "failure_ratio"


def test_quality_breakdown_needs_four_failures_across_three_seats():
    monitor = ProviderHealthMonitor(AbortPolicy())
    events = [
        monitor.record(sample(seat, kind=FailureKind.QUALITY, code="response_no_tool_call", at=float(index)))
        for index, seat in enumerate((1, 2, 3, 1))
    ]
    assert events[-1] is not None
    assert events[-1].rule == "quality_breakdown"


def test_three_timeouts_across_seats_trip():
    monitor = ProviderHealthMonitor(AbortPolicy())
    events = [
        monitor.record(sample(seat, code="provider_timeout", at=float(index)))
        for index, seat in enumerate((1, 2, 3))
    ]
    assert events[-1] is not None
    assert events[-1].rule == "provider_timeout"


def test_one_seat_warns_by_default_and_can_pause():
    monitor = ProviderHealthMonitor(AbortPolicy())
    for index in range(3):
        assert monitor.record(sample(1, at=float(index))) is None
    assert monitor.open_event(KEY) is None

    strict = ProviderHealthMonitor(AbortPolicy(single_seat_policy=MODE_PAUSE))
    event = None
    for index in range(3):
        event = strict.record(sample(1, at=float(index)))
    assert event is not None
    assert event.rule == "single_seat"


def test_success_resets_the_streak():
    monitor = ProviderHealthMonitor(AbortPolicy())
    for index, seat in enumerate((1, 2, 3, 1)):
        monitor.record(sample(seat, at=float(index)))
    assert monitor.record(sample(2, at=4.0, ok=True)) is None
    for index, seat in enumerate((1, 2, 3, 1)):
        assert monitor.record(sample(seat, at=10.0 + index)) is None
    assert monitor.open_event(KEY) is None


def test_a_long_gap_restarts_the_streak():
    monitor = ProviderHealthMonitor(AbortPolicy(window_seconds=120))
    for index, seat in enumerate((1, 2, 3, 1)):
        monitor.record(sample(seat, at=float(index)))
    assert monitor.record(sample(2, at=500.0)) is None
    assert monitor.open_event(KEY) is None


def test_open_state_is_sticky_until_reset():
    monitor = ProviderHealthMonitor(AbortPolicy())
    monitor.record(sample(1, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    event = monitor.record(sample(2, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    assert event is not None
    assert monitor.record(sample(3)) is None
    assert monitor.open_event(KEY) == event


def test_reset_preserves_the_reopen_counter():
    monitor = ProviderHealthMonitor(AbortPolicy())
    monitor.record(sample(1, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    monitor.record(sample(2, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    monitor.reset(KEY)
    assert monitor.open_event(KEY) is None
    assert monitor.snapshot()[KEY]["reopen_count"] == 1

    monitor.record(sample(1, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    event = monitor.record(sample(2, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    assert event is not None
    assert event.reopen_count == 1


def test_ignored_samples_are_not_recorded():
    monitor = ProviderHealthMonitor(AbortPolicy())
    assert monitor.record(sample(
        1, kind=FailureKind.IGNORED, code="missing_vote_result",
    )) is None
    assert monitor.snapshot() == {}


def test_snapshot_reports_open_and_closed():
    monitor = ProviderHealthMonitor(AbortPolicy())
    monitor.record(sample(1))
    assert monitor.snapshot()[KEY]["state"] is HealthState.CLOSED
    monitor.record(sample(2, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    monitor.record(sample(3, kind=FailureKind.TERMINAL, code="provider_auth_error"))
    assert monitor.snapshot()[KEY]["state"] is HealthState.OPEN
    assert monitor.snapshot()[KEY]["rule"] == "provider_terminal"


def test_policy_from_env(monkeypatch):
    monkeypatch.setenv("MODEL_ABORT_MODE", "fail")
    monkeypatch.setenv("MODEL_ABORT_CONSECUTIVE", "7")
    monkeypatch.setenv("MODEL_ABORT_RATIO", "0.5")
    monkeypatch.setenv("MODEL_ABORT_ENABLED", "0")
    policy = policy_from_env()
    assert policy.mode == "fail"
    assert policy.consecutive_failures == 7
    assert policy.failure_ratio == 0.5
    assert policy.enabled is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MODEL_ABORT_MODE", "bogus"),
        ("MODEL_ABORT_CONSECUTIVE", "-1"),
        ("MODEL_ABORT_RATIO", "nan"),
        ("MODEL_ABORT_SINGLE_SEAT", "bogus"),
    ],
)
def test_invalid_env_values_fall_back_to_defaults(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    policy = policy_from_env()
    assert policy == AbortPolicy()


def test_resolve_abort_policy_lets_the_interface_override(monkeypatch):
    monkeypatch.setenv("MODEL_ABORT_CONSECUTIVE", "9")
    policy = resolve_abort_policy({
        "mode": "warn",
        "consecutive_failures": 2,
        "failure_ratio": 0.5,
        "unknown": "ignored",
        "quality_failures": -1,
    })
    assert policy.mode == "warn"
    assert policy.consecutive_failures == 2
    assert policy.failure_ratio == 0.5
    assert policy.quality_failures == 4


def test_resolve_abort_policy_ignores_non_mapping_stored():
    assert resolve_abort_policy("nope") == policy_from_env()  # type: ignore[arg-type]


def test_abort_policy_validation():
    with pytest.raises(ValueError):
        AbortPolicy(mode="bogus")
    with pytest.raises(ValueError):
        AbortPolicy(failure_ratio=2)
    with pytest.raises(ValueError):
        AbortPolicy(consecutive_failures=-1)
    with pytest.raises(ValueError):
        AbortPolicy(enabled="yes")  # type: ignore[arg-type]


def test_benchmark_policy_forces_fail_mode():
    assert AbortPolicy(mode=MODE_WARN).for_benchmark().mode == "fail"


def test_env_boolean_accepts_true_and_rejects_garbage(monkeypatch):
    monkeypatch.setenv("MODEL_ABORT_ENABLED", "yes")
    assert policy_from_env().enabled is True
    monkeypatch.setenv("MODEL_ABORT_ENABLED", "maybe")
    assert policy_from_env().enabled is True


def test_env_int_rejects_garbage_and_negatives(monkeypatch):
    monkeypatch.setenv("MODEL_ABORT_CONSECUTIVE", "abc")
    assert policy_from_env().consecutive_failures == 5
    monkeypatch.setenv("MODEL_ABORT_CONSECUTIVE", "-3")
    assert policy_from_env().consecutive_failures == 5


def test_env_float_rejects_garbage_and_non_finite(monkeypatch):
    monkeypatch.setenv("MODEL_ABORT_RATIO", "abc")
    assert policy_from_env().failure_ratio == 0.8
    monkeypatch.setenv("MODEL_ABORT_RATIO", "inf")
    assert policy_from_env().failure_ratio == 0.8


def test_invalid_choice_falls_back(monkeypatch):
    monkeypatch.setenv("MODEL_ABORT_MODE", "sometimes")
    assert policy_from_env().mode == "pause"


def test_policy_property_and_benchmark_mode_validation():
    monitor = ProviderHealthMonitor(AbortPolicy())
    assert monitor.policy.mode == "pause"
    with pytest.raises(ValueError):
        AbortPolicy(benchmark_mode="pause")
    with pytest.raises(ValueError):
        AbortPolicy(single_seat_policy="fail")


def test_resolve_accepts_booleans_and_ints(monkeypatch):
    policy = resolve_abort_policy({
        "enabled": False, "consecutive_failures": 3, "failure_ratio": 0.5,
        "mode": "off", "benchmark_mode": "off",
    })
    assert policy.enabled is False
    assert policy.consecutive_failures == 3
    assert policy.failure_ratio == 0.5
    assert policy.mode == "off"
    assert policy.benchmark_mode == "off"


def test_gap_after_a_success_is_a_noop():
    monitor = ProviderHealthMonitor(AbortPolicy(window_seconds=60))
    assert monitor.record(sample(1, ok=True, at=1.0)) is None
    # last_failure_at is None, so the gap check returns immediately.
    assert monitor.record(sample(1, at=2.0)) is None


def test_reset_an_unseen_key_does_not_crash():
    monitor = ProviderHealthMonitor(AbortPolicy())
    monitor.reset("missing")
    assert monitor.snapshot()["missing"]["reopen_count"] == 0


def test_resolve_ignores_wrong_typed_fields():
    policy = resolve_abort_policy({
        "enabled": "yes", "failure_ratio": "high", "mode": "bogus",
    })
    assert policy.enabled is True
    assert policy.failure_ratio == 0.8
    assert policy.mode == "pause"


def test_the_recent_window_is_trimmed():
    monitor = ProviderHealthMonitor(AbortPolicy(sample_window=2))
    for index in range(4):
        monitor.record(sample(1, at=float(index)))
    assert len(monitor._state(KEY).recent) == 2
