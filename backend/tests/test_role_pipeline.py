from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from app.config import LLMConfig, PipelineMode, pipeline_mode_from_env
from app.core.effect_applier import CommitResult
from app.core.role_pipeline import (
    PipelineDiff, PipelineObservation, PipelineResult, RolePipeline,
)
from app.core.scheduler import PointResult
from app.models.game import GameState
from app.models.pipeline import SchedulePoint


def observation(**changes) -> PipelineObservation:
    values = {
        "accepted_actions": ("legacy",), "effects": ("old-effect",),
        "state_digest": "v1-digest",
        "public_events": ({"event_type": "OLD", "payload": {"seat": 1}},),
    }
    values.update(changes)
    return PipelineObservation(**values)


def point() -> PointResult:
    commits = (
        CommitResult("a2", ("e2",), 1, (
            {"event_type": "PRIVATE", "payload": {"secret": "hidden"}, "visibility": ("ACTOR",)},
            {"event_type": "PUBLIC", "payload": {"seat": 2}, "visibility": ("PUBLIC",)},
        ), "digest-1"),
        CommitResult("a1", ("e1", "e3"), 2, (), "digest-2"),
    )
    return PointResult((), commits, commits[0].events, "v2-digest")


class FakeScheduler:
    def __init__(self, result: PointResult) -> None:
        self.result = result; self.calls = []

    def run_point(self, state, schedule_point):
        self.calls.append((state, schedule_point)); state.round_number += 10
        return self.result


class SequencedScheduler:
    def __init__(self, results, fail_at=None):
        self.results, self.fail_at, self.calls = results, fail_at, []

    def run_point(self, state, schedule_point):
        self.calls.append((state, schedule_point))
        state.round_number += 1
        if len(self.calls) == self.fail_at: raise RuntimeError("point failed")
        return self.results[len(self.calls) - 1]


def test_pipeline_mode_from_env_is_strict_and_reads_at_call_time(monkeypatch) -> None:
    monkeypatch.delenv("ROLE_PIPELINE_V2", raising=False)
    assert pipeline_mode_from_env() == PipelineMode.V1
    monkeypatch.setenv("ROLE_PIPELINE_V2", "shadow")
    assert pipeline_mode_from_env() == PipelineMode.SHADOW
    assert pipeline_mode_from_env("v2") == PipelineMode.V2
    for invalid in ("V2", " v2 ", "", "unknown", 1):
        with pytest.raises((TypeError, ValueError)): pipeline_mode_from_env(invalid)


def test_existing_llm_model_environment_branches_remain_stable(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODELS", " a, ,b "); assert LLMConfig().models == ["a"]
    monkeypatch.delenv("LLM_MODELS"); monkeypatch.setenv("LLM_MODEL", "single")
    assert LLMConfig().models == ["single"]
    monkeypatch.delenv("LLM_MODEL"); assert LLMConfig().models == ["deepseek-v4-pro"]


def test_v1_only_calls_runner_and_mode_is_frozen() -> None:
    calls = []
    def runner(state, point): calls.append((state, point)); state.round_number += 1; return observation()
    game = GameState("g"); adapter = RolePipeline(PipelineMode.V1, runner, None)
    result = adapter.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert result.mode is PipelineMode.V1 and result.accepted_actions == ("legacy",)
    assert result.diff is None and game.round_number == 1 and len(calls) == 1
    with pytest.raises(FrozenInstanceError): adapter.mode = PipelineMode.V2


def test_v2_converts_commits_and_filters_nonpublic_events() -> None:
    scheduler = FakeScheduler(point()); game = GameState("g")
    result = RolePipeline(PipelineMode.V2, None, scheduler).run_point(
        game, SchedulePoint.NIGHT_ACTION
    )
    assert result.accepted_actions == ("a2", "a1")
    assert result.effects == ("e2", "e1", "e3")
    assert result.state_digest == "v2-digest"
    assert result.public_events == ({"event_type": "PUBLIC", "payload": {"seat": 2}, "visibility": ("PUBLIC",)},)
    assert "hidden" not in repr(result) and game.round_number == 10


def test_execute_v2_point_returns_raw_exact_result_for_v2_and_shadow() -> None:
    raw = point()
    for mode in (PipelineMode.V2, PipelineMode.SHADOW):
        scheduler = FakeScheduler(raw)
        runner = None if mode is PipelineMode.V2 else lambda *_: observation()
        pipeline = RolePipeline(mode, runner, scheduler)
        game = GameState(mode.value)
        assert pipeline.execute_v2_point(game, SchedulePoint.NIGHT_ACTION) is raw
        assert scheduler.calls == [(game, SchedulePoint.NIGHT_ACTION)]
    v1 = RolePipeline(PipelineMode.V1, lambda *_: observation(), None)
    with pytest.raises(ValueError, match="V2 execution"):
        v1.execute_v2_point(GameState("v1"), SchedulePoint.NIGHT_ACTION)


def test_execute_v2_point_is_exact_and_does_not_observe() -> None:
    subclass = type("SubPoint", (PointResult,), {})
    for raw in (object(), subclass((), (), (), "d")):
        scheduler = FakeScheduler(raw)
        pipeline = RolePipeline(PipelineMode.V2, None, scheduler)
        with pytest.raises(TypeError, match="exact PointResult"):
            pipeline.execute_v2_point(GameState("g"), SchedulePoint.NIGHT_ACTION)
        assert len(scheduler.calls) == 1
    pipeline = RolePipeline(PipelineMode.V2, None, FakeScheduler(point()))
    with pytest.raises(TypeError): pipeline.execute_v2_point(object(), SchedulePoint.NIGHT_ACTION)
    with pytest.raises(TypeError): pipeline.execute_v2_point(GameState("g"), "night")
    with pytest.raises(TypeError, match="slot"):
        pipeline.execute_v2_point(GameState("g"), SchedulePoint.DAY_ACTION, slot=1)


def test_execute_v2_point_forwards_a_slot_only_when_requested() -> None:
    class SlotScheduler:
        def __init__(self) -> None: self.calls = []
        def run_point(self, state, schedule_point, *, slot=""):
            self.calls.append(slot); return point()

    scheduler = SlotScheduler()
    pipeline = RolePipeline(PipelineMode.V2, None, scheduler)
    pipeline.execute_v2_point(GameState("g"), SchedulePoint.DAY_ACTION)
    pipeline.execute_v2_point(GameState("g"), SchedulePoint.DAY_ACTION, slot="r1-s2")
    assert scheduler.calls == ["", "r1-s2"]


def test_observe_v2_is_pure_repeatable_and_never_executes_scheduler() -> None:
    scheduler = FakeScheduler(point())
    pipeline = RolePipeline(PipelineMode.V2, None, scheduler)
    raw = point()
    first = pipeline.observe_v2(raw); second = RolePipeline.observe_v2(raw)
    assert first == second and type(first) is PipelineObservation
    assert first.accepted_actions == ("a2", "a1") and scheduler.calls == []
    malformed = point(); object.__setattr__(malformed, "commits", (object(),))
    with pytest.raises(TypeError, match="commit"):
        pipeline.observe_v2(malformed)
    assert scheduler.calls == []


def test_observe_v2_rejects_nonexact_point_without_scheduler_access() -> None:
    subclass = type("SubPoint", (PointResult,), {})
    scheduler = FakeScheduler(point()); pipeline = RolePipeline(PipelineMode.SHADOW, lambda *_: observation(), scheduler)
    for raw in (object(), subclass((), (), (), "d")):
        with pytest.raises(TypeError, match="exact PointResult"):
            pipeline.observe_v2(raw)
    assert scheduler.calls == []


def test_shadow_mutates_live_state_only_with_v1_and_deepcopies_runtime() -> None:
    game = GameState("g"); game._pipeline_runtime = {"nested": [{"seat": 1}]}
    seen = []
    def runner(state, point): state.round_number = 3; state._pipeline_runtime["nested"][0]["seat"] = 2; return observation()
    scheduler = FakeScheduler(point())
    original = scheduler.run_point
    def shadow_run(state, schedule_point):
        seen.append(state); state._pipeline_runtime["nested"][0]["seat"] = 99
        return original(state, schedule_point)
    scheduler.run_point = shadow_run
    result = RolePipeline(PipelineMode.SHADOW, runner, scheduler).run_point(
        game, SchedulePoint.NIGHT_ACTION
    )
    assert result.mode is PipelineMode.SHADOW and result.state_digest == "v1-digest"
    assert result.diff is not None and result.diff.matched is False
    assert game.round_number == 3 and game._pipeline_runtime["nested"][0]["seat"] == 2
    assert seen[0] is not game


def test_shadow_diff_is_deterministic_and_matches_equal_observations() -> None:
    same = observation(accepted_actions=(), effects=(), public_events=())
    scheduler = FakeScheduler(PointResult((), (), (), same.state_digest))
    result = RolePipeline(PipelineMode.SHADOW, lambda *_: same, scheduler).run_point(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    )
    assert result.diff == PipelineDiff(True, ())


def test_run_points_v2_uses_one_live_state_and_merges_in_point_order() -> None:
    first, second = point(), PointResult((), (
        CommitResult("a3", ("e4",), 3, (
            {"event_type": "LAST", "payload": {}, "visibility": ("PUBLIC",)},
        ), "digest-3"),
    ), (), "final-digest")
    scheduler = SequencedScheduler((first, second)); game = GameState("g")
    result = RolePipeline(PipelineMode.V2, None, scheduler).run_points(
        game, (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT),
    )
    assert all(call[0] is game for call in scheduler.calls)
    assert result.accepted_actions == ("a2", "a1", "a3")
    assert result.effects == ("e2", "e1", "e3", "e4")
    assert [event["event_type"] for event in result.public_events] == ["PUBLIC", "LAST"]
    assert result.state_digest == "final-digest" and game.round_number == 2


def test_run_points_shadow_copies_once_and_calls_legacy_once() -> None:
    same = observation(accepted_actions=("a2", "a1", "a2", "a1"),
        effects=("e2", "e1", "e3", "e2", "e1", "e3"),
        public_events=(
            {"event_type": "PUBLIC", "payload": {"seat": 2}, "visibility": ("PUBLIC",)},
            {"event_type": "PUBLIC", "payload": {"seat": 2}, "visibility": ("PUBLIC",)},
        ), state_digest="v2-digest")
    scheduler = SequencedScheduler((point(), point())); calls = []
    def legacy(state, first_point):
        calls.append((state, first_point)); state.round_number = 10; return same
    game = GameState("g")
    result = RolePipeline(PipelineMode.SHADOW, legacy, scheduler).run_points(
        game, (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT),
    )
    shadow = scheduler.calls[0][0]
    assert len(calls) == 1 and calls[0] == (game, SchedulePoint.NIGHT_ACTION)
    assert shadow is scheduler.calls[1][0] and shadow is not game
    assert game.round_number == 10 and shadow.round_number == 2
    assert result.diff == PipelineDiff(True, ())


def test_run_points_validates_exact_points_and_stops_after_failure() -> None:
    adapter = RolePipeline(PipelineMode.V2, None, SequencedScheduler((point(), point()), fail_at=1))
    for points in ([], (), ("night",), (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_ACTION)):
        with pytest.raises((TypeError, ValueError)): adapter.run_points(GameState("g"), points)
    with pytest.raises(RuntimeError, match="point failed"):
        adapter.run_points(GameState("g"), (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT))
    assert len(adapter.scheduler.calls) == 1


def test_dependencies_boundaries_and_exceptions_do_not_compensate() -> None:
    with pytest.raises(TypeError): RolePipeline("v1", lambda *_: observation(), None)
    with pytest.raises(ValueError): RolePipeline(PipelineMode.V1, None, FakeScheduler(point()))
    with pytest.raises(ValueError): RolePipeline(PipelineMode.V2, lambda *_: observation(), None)
    with pytest.raises(TypeError): RolePipeline(PipelineMode.V1, object(), None)
    adapter = RolePipeline(PipelineMode.V1, lambda *_: (_ for _ in ()).throw(RuntimeError("boom")), None)
    game = GameState("g")
    with pytest.raises(RuntimeError, match="boom"): adapter.run_point(game, SchedulePoint.NIGHT_ACTION)
    assert game.round_number == 0
    with pytest.raises(TypeError): RolePipeline(PipelineMode.V1, lambda *_: observation(), None).run_point(object(), SchedulePoint.NIGHT_ACTION)
    with pytest.raises(TypeError): RolePipeline(PipelineMode.V1, lambda *_: observation(), None).run_point(GameState("g"), "night_action")
    with pytest.raises(TypeError): RolePipeline(PipelineMode.V1, lambda *_: object(), None).run_point(GameState("g"), SchedulePoint.NIGHT_ACTION)


def test_dtos_are_strict_deep_frozen_and_bounded() -> None:
    raw = {"event_type": "E", "payload": {"items": [1]}}
    value = observation(public_events=(raw,)); raw["payload"]["items"].append(2)
    assert value.public_events[0]["payload"]["items"] == (1,)
    with pytest.raises(TypeError): value.public_events[0]["payload"]["items"] += (2,)
    with pytest.raises(TypeError): PipelineObservation(["a"], (), "d", ())
    with pytest.raises(TypeError): PipelineObservation((), (1,), "d", ())
    with pytest.raises(TypeError): PipelineObservation((), (), 1, ())
    with pytest.raises(TypeError): PipelineObservation((), (), "d", [])
    with pytest.raises(TypeError): PipelineObservation((), (), "d", (object(),))
    with pytest.raises(TypeError): PipelineDiff(1, ())
    with pytest.raises(TypeError): PipelineDiff(True, (1,))
    with pytest.raises(TypeError): PipelineResult((), (), "d", (), "v1")
    with pytest.raises(TypeError): PipelineResult((), (), "d", (), PipelineMode.V1, object())
    cycle = {}; cycle["self"] = cycle
    with pytest.raises(ValueError): observation(public_events=(cycle,))
    with pytest.raises(ValueError): observation(public_events=({"n": float("nan")},))
    assert observation(public_events=({"n": 1.5},)).public_events[0]["n"] == 1.5
    with pytest.raises(TypeError): observation(public_events=(MappingProxyType({1: "bad"}),))
    with pytest.raises(TypeError): observation(public_events=({"value": object()},))
    for value in (-1, 2_147_483_648):
        with pytest.raises(ValueError): observation(public_events=({"value": value},))
    with pytest.raises(ValueError): observation(public_events=({"text": "\ud800"},))
    with pytest.raises(ValueError): observation(public_events=({"text": "x" * 65_537},))
    nested = {}; current = nested
    for _ in range(65): current["next"] = {}; current = current["next"]
    with pytest.raises(ValueError): observation(public_events=(nested,))
    with pytest.raises(ValueError): observation(public_events=({"items": [None] * 10_001},))


def test_public_filter_handles_event_without_visibility() -> None:
    commit = CommitResult("a", (), 1, ({"event_type": "X", "payload": {}},), "d")
    scheduler = FakeScheduler(PointResult((), (commit,), commit.events, "d"))
    assert RolePipeline(PipelineMode.V2, None, scheduler).run_point(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    ).public_events == ()


@pytest.mark.parametrize("visibility", ["NOTPUBLIC", ["PUBLIC"], {"PUBLIC": True}, ("PUBLIC", 1), ("x" * 257,)])
def test_public_filter_drops_malformed_visibility(visibility) -> None:
    commit = CommitResult("a", (), 1, (), "d")
    object.__setattr__(commit, "events", ({"event_type": "X", "payload": {}, "visibility": visibility},))
    scheduler = FakeScheduler(PointResult((), (commit,), (), "d"))
    assert RolePipeline(PipelineMode.V2, None, scheduler).run_point(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    ).public_events == ()


def test_observation_identifiers_and_diff_mismatches_are_closed_and_bounded() -> None:
    for values in (("",), ("x" * 257,), ("\ud800",), tuple("x" for _ in range(4097))):
        with pytest.raises(ValueError): observation(accepted_actions=values)
    with pytest.raises(ValueError): observation(effects=("x" * 256,) * 257)
    for digest in ("", "x" * 257, "\ud800"):
        with pytest.raises(ValueError): observation(state_digest=digest)
    for mismatches in (("unknown",), ("effects", "accepted_actions"), ("effects", "effects")):
        with pytest.raises(ValueError): PipelineDiff(False, mismatches)
    assert PipelineDiff(False, ("accepted_actions", "public_events")).mismatches == (
        "accepted_actions", "public_events",
    )


def test_v2_requires_exact_point_result_and_commit_values() -> None:
    for result in (object(), type("SubPoint", (PointResult,), {})((), (), (), "d")):
        with pytest.raises(TypeError, match="PointResult"):
            RolePipeline(PipelineMode.V2, None, FakeScheduler(result)).run_point(
                GameState("g"), SchedulePoint.NIGHT_ACTION
            )
    result = point(); object.__setattr__(result, "commits", (object(),))
    with pytest.raises(TypeError, match="commit"):
        RolePipeline(PipelineMode.V2, None, FakeScheduler(result)).run_point(
            GameState("g"), SchedulePoint.NIGHT_ACTION
        )


def test_public_filter_defensively_drops_invalid_utf8_visibility() -> None:
    commit = CommitResult("a", (), 1, (), "d")
    object.__setattr__(commit, "events", ({"event_type": "X", "payload": {}, "visibility": ("PUBLIC\ud800",)},))
    scheduler = FakeScheduler(PointResult((), (commit,), (), "d"))
    assert RolePipeline(PipelineMode.V2, None, scheduler).run_point(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    ).public_events == ()


def test_public_filter_accepts_multiple_valid_labels_and_nonmapping_is_dropped() -> None:
    commit = CommitResult("a", (), 1, (), "d")
    object.__setattr__(commit, "events", (
        object(), {"event_type": "X", "payload": {}, "visibility": ("ACTOR", "PUBLIC")},
    ))
    scheduler = FakeScheduler(PointResult((), (commit,), (), "d"))
    assert len(RolePipeline(PipelineMode.V2, None, scheduler).run_point(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    ).public_events) == 1
