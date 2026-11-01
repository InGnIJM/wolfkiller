from __future__ import annotations


import pytest

from app.config import LLMConfig
from app.core.effect_applier import CommitResult
from app.core.role_pipeline import (
    PipelineDiff, PipelineObservation, RolePipeline,
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




def test_existing_llm_model_environment_branches_remain_stable(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODELS", " a, ,b "); assert LLMConfig().models == ["a"]
    monkeypatch.delenv("LLM_MODELS"); monkeypatch.setenv("LLM_MODEL", "single")
    assert LLMConfig().models == ["single"]
    monkeypatch.delenv("LLM_MODEL"); assert LLMConfig().models == ["deepseek-v4-pro"]




def test_v2_converts_commits_and_filters_nonpublic_events() -> None:
    scheduler = FakeScheduler(point()); game = GameState("g")
    result = RolePipeline(scheduler).run_point(
        game, SchedulePoint.NIGHT_ACTION
    )
    assert result.accepted_actions == ("a2", "a1")
    assert result.effects == ("e2", "e1", "e3")
    assert result.state_digest == "v2-digest"
    assert result.public_events == ({"event_type": "PUBLIC", "payload": {"seat": 2}, "visibility": ("PUBLIC",)},)
    assert "hidden" not in repr(result) and game.round_number == 10




def test_execute_v2_point_is_exact_and_does_not_observe() -> None:
    subclass = type("SubPoint", (PointResult,), {})
    for raw in (object(), subclass((), (), (), "d")):
        scheduler = FakeScheduler(raw)
        pipeline = RolePipeline(scheduler)
        with pytest.raises(TypeError, match="exact PointResult"):
            pipeline.execute_v2_point(GameState("g"), SchedulePoint.NIGHT_ACTION)
        assert len(scheduler.calls) == 1
    pipeline = RolePipeline(FakeScheduler(point()))
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
    pipeline = RolePipeline(scheduler)
    pipeline.execute_v2_point(GameState("g"), SchedulePoint.DAY_ACTION)
    pipeline.execute_v2_point(GameState("g"), SchedulePoint.DAY_ACTION, slot="r1-s2")
    assert scheduler.calls == ["", "r1-s2"]


def test_execute_v2_point_forwards_deferred_event_types_only_when_requested() -> None:
    """夜死批次的反应窗口延后时，调度点必须收到那份「不在这里开窗」的清单。"""
    class DeferScheduler:
        def __init__(self) -> None: self.calls = []
        def run_point(self, state, schedule_point, *, slot="", defer_event_types=()):
            self.calls.append((slot, defer_event_types)); return point()

    scheduler = DeferScheduler()
    pipeline = RolePipeline(scheduler)
    pipeline.execute_v2_point(GameState("g"), SchedulePoint.NIGHT_COMMIT)
    pipeline.execute_v2_point(
        GameState("g"), SchedulePoint.NIGHT_COMMIT,
        defer_event_types=("PLAYER_DIED",),
    )
    assert scheduler.calls == [("", ()), ("", ("PLAYER_DIED",))]

    with pytest.raises(TypeError, match="defer_event_types"):
        pipeline.execute_v2_point(
            GameState("g"), SchedulePoint.NIGHT_COMMIT, defer_event_types="PLAYER_DIED",
        )
    with pytest.raises(TypeError, match="defer_event_types"):
        pipeline.execute_v2_point(
            GameState("g"), SchedulePoint.NIGHT_COMMIT, defer_event_types=(1,),
        )


def test_observe_v2_is_pure_repeatable_and_never_executes_scheduler() -> None:
    scheduler = FakeScheduler(point())
    pipeline = RolePipeline(scheduler)
    raw = point()
    first = pipeline.observe_v2(raw); second = RolePipeline.observe_v2(raw)
    assert first == second and type(first) is PipelineObservation
    assert first.accepted_actions == ("a2", "a1") and scheduler.calls == []
    malformed = point(); object.__setattr__(malformed, "commits", (object(),))
    with pytest.raises(TypeError, match="commit"):
        pipeline.observe_v2(malformed)
    assert scheduler.calls == []








def test_run_points_v2_uses_one_live_state_and_merges_in_point_order() -> None:
    first, second = point(), PointResult((), (
        CommitResult("a3", ("e4",), 3, (
            {"event_type": "LAST", "payload": {}, "visibility": ("PUBLIC",)},
        ), "digest-3"),
    ), (), "final-digest")
    scheduler = SequencedScheduler((first, second)); game = GameState("g")
    result = RolePipeline(scheduler).run_points(
        game, (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT),
    )
    assert all(call[0] is game for call in scheduler.calls)
    assert result.accepted_actions == ("a2", "a1", "a3")
    assert result.effects == ("e2", "e1", "e3", "e4")
    assert [event["event_type"] for event in result.public_events] == ["PUBLIC", "LAST"]
    assert result.state_digest == "final-digest" and game.round_number == 2




def test_run_points_validates_exact_points_and_stops_after_failure() -> None:
    adapter = RolePipeline(SequencedScheduler((point(), point()), fail_at=1))
    for points in ([], (), ("night",), (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_ACTION)):
        with pytest.raises((TypeError, ValueError)): adapter.run_points(GameState("g"), points)
    with pytest.raises(RuntimeError, match="point failed"):
        adapter.run_points(GameState("g"), (SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_COMMIT))
    assert len(adapter.scheduler.calls) == 1






def test_public_filter_handles_event_without_visibility() -> None:
    commit = CommitResult("a", (), 1, ({"event_type": "X", "payload": {}},), "d")
    scheduler = FakeScheduler(PointResult((), (commit,), commit.events, "d"))
    assert RolePipeline(scheduler).run_point(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    ).public_events == ()


@pytest.mark.parametrize("visibility", ["NOTPUBLIC", ["PUBLIC"], {"PUBLIC": True}, ("PUBLIC", 1), ("x" * 257,)])
def test_public_filter_drops_malformed_visibility(visibility) -> None:
    commit = CommitResult("a", (), 1, (), "d")
    object.__setattr__(commit, "events", ({"event_type": "X", "payload": {}, "visibility": visibility},))
    scheduler = FakeScheduler(PointResult((), (commit,), (), "d"))
    assert RolePipeline(scheduler).run_point(
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
            RolePipeline(FakeScheduler(result)).run_point(
                GameState("g"), SchedulePoint.NIGHT_ACTION
            )
    result = point(); object.__setattr__(result, "commits", (object(),))
    with pytest.raises(TypeError, match="commit"):
        RolePipeline(FakeScheduler(result)).run_point(
            GameState("g"), SchedulePoint.NIGHT_ACTION
        )


def test_public_filter_defensively_drops_invalid_utf8_visibility() -> None:
    commit = CommitResult("a", (), 1, (), "d")
    object.__setattr__(commit, "events", ({"event_type": "X", "payload": {}, "visibility": ("PUBLIC\ud800",)},))
    scheduler = FakeScheduler(PointResult((), (commit,), (), "d"))
    assert RolePipeline(scheduler).run_point(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    ).public_events == ()


def test_public_filter_accepts_multiple_valid_labels_and_nonmapping_is_dropped() -> None:
    commit = CommitResult("a", (), 1, (), "d")
    object.__setattr__(commit, "events", (
        object(), {"event_type": "X", "payload": {}, "visibility": ("ACTOR", "PUBLIC")},
    ))
    scheduler = FakeScheduler(PointResult((), (commit,), (), "d"))
    assert len(RolePipeline(scheduler).run_point(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    ).public_events) == 1
