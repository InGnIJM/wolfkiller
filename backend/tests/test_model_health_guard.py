"""The guard that turns provider-health events into pause/fail decisions."""

from __future__ import annotations

import pytest

from app.agents.model_health import AbortPolicy, HealthEvent
from app.core.model_health_guard import (
    CIRCUIT_OPEN_CODE,
    ModelHealthGuard,
    ProviderOutageFailed,
    ProviderOutagePaused,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def record(
    seat: int, *, code: str | None = "provider_auth_error",
    parse: str | None = "error", contract: str = "night_action",
    timeout_type: str | None = None,
) -> dict:
    return {
        "seat": seat,
        "data": {
            "parse_result": parse,
            "failure_code": code,
            "contract_id": contract,
            "timeout_type": timeout_type,
        },
    }


def guard(**policy_kwargs) -> tuple[ModelHealthGuard, Clock]:
    clock = Clock()
    policy = AbortPolicy(**policy_kwargs)
    return ModelHealthGuard(
        policy=policy,
        seat_keys={1: "k", 2: "k", 3: "k"},
        mode=policy.mode,
        clock=clock,
    ), clock


def test_check_is_a_noop_until_the_circuit_opens():
    health, _ = guard()
    health.check(1)
    health.record_telemetry(record(1))
    health.check(1)


def test_terminal_failure_pauses_and_carries_the_reason():
    health, _ = guard()
    health.record_telemetry(record(1))
    health.record_telemetry(record(2))
    with pytest.raises(ProviderOutagePaused) as captured:
        health.check(1)
    assert captured.value.code == CIRCUIT_OPEN_CODE
    assert captured.value.detail["rule"] == "provider_terminal"
    assert captured.value.detail["failure_code"] == "provider_auth_error"
    assert captured.value.detail["seats"] == [1, 2]


def test_fail_mode_raises_the_terminal_variant():
    health, _ = guard(mode="fail")
    health.record_telemetry(record(1))
    health.record_telemetry(record(2))
    with pytest.raises(ProviderOutageFailed) as captured:
        health.check(1)
    assert captured.value.code == CIRCUIT_OPEN_CODE


def test_warn_mode_records_but_never_raises():
    events: list[HealthEvent] = []
    policy = AbortPolicy(mode="warn")
    health = ModelHealthGuard(
        policy=policy, seat_keys={1: "k", 2: "k"},
        mode="warn", on_event=events.append,
    )
    health.record_telemetry(record(1))
    health.record_telemetry(record(2))
    health.check(1)
    assert events and events[0].rule == "provider_terminal"


def test_event_sink_can_be_attached_after_construction():
    events: list[HealthEvent] = []
    health, _ = guard()
    health.set_event_sink(events.append)
    health.record_telemetry(record(1))
    health.record_telemetry(record(2))
    assert events and events[0].provider_key == "k"


def test_telemetry_filtering_ignores_intermediate_and_scheduling_rows():
    health, _ = guard()
    # Intermediate retry rows do not count.
    health.record_telemetry(record(1, parse="invoke_error_retry", code="invoke_error"))
    health.record_telemetry(record(2, parse="timeout_retry", code="request_timeout"))
    # A capability fallback is a transport switch, not a failure.
    health.record_telemetry(record(1, parse="strict_capability_fallback", code=None))
    # The vote window's own deadline is scheduling, not a provider fault.
    health.record_telemetry(record(2, code="request_timeout", parse="timeout_fallback", timeout_type="phase_deadline"))
    # A missing vote result is engine scheduling.
    health.record_telemetry(record(3, code="missing_vote_result", parse="fallback"))
    health.check(1)


def test_a_success_clears_the_streak():
    health, _ = guard()
    health.record_telemetry(record(1))
    health.record_telemetry(record(2, code=None, parse="ok"))
    health.record_telemetry(record(3))
    health.check(1)


def test_client_failure_is_counted_even_without_a_telemetry_row():
    health, _ = guard()
    error = RuntimeError("connection reset")
    error.status_code = 401  # type: ignore[attr-defined]
    health.record_client_failure(1, error)
    health.record_client_failure(2, error)
    with pytest.raises(ProviderOutagePaused):
        health.check(1)


def test_reset_clears_the_circuit_and_snapshot():
    health, _ = guard()
    health.record_telemetry(record(1))
    health.record_telemetry(record(2))
    assert health.snapshot()["k"]["state"] == "open"
    health.reset()
    assert health.snapshot()["k"]["state"] == "closed"
    health.check(1)


def test_unknown_seat_gets_a_stable_fallback_key():
    health, _ = guard()
    assert health.key_for(99) == "unknown|seat:99"


def test_telemetry_without_a_seat_is_ignored():
    health, _ = guard()
    health.record_telemetry({"data": {"parse_result": "error"}})
    health.record_telemetry({"seat": "1", "data": {}})
    health.record_telemetry({"seat": 1, "data": "nope"})
    assert health.snapshot() == {}


def test_record_telemetry_never_raises_on_a_bad_sink():
    health, _ = guard()

    def boom(_event):
        raise RuntimeError("audit sink down")

    health.set_event_sink(boom)
    health.record_telemetry(record(1))
    health.record_telemetry(record(2))


def test_mode_property_and_disabled_policy():
    policy = AbortPolicy(enabled=False)
    health = ModelHealthGuard(policy=policy, seat_keys={1: "k"}, mode=policy.mode)
    assert health.mode == "pause"
    health.record_telemetry(record(1))
    health.record_telemetry(record(2))
    health.check(1)
    assert health.snapshot() == {}


def test_an_ignored_client_failure_is_not_counted():
    health, _ = guard()

    class _Ignored(Exception):
        code = "missing_vote_result"

    health.record_client_failure(1, _Ignored("x"))
    assert health.snapshot() == {}


def test_policy_property():
    health, _ = guard()
    assert health.policy.mode == "pause"
