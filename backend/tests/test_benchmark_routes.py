from __future__ import annotations

import csv
import io
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.benchmark_schemas import BenchmarkCreateRequest
from app.api.routes import benchmark_routes
from app.persistence.repository import (
    CommitConflict, GameReferencedByBenchmark, InvalidExecutionTransition,
)
from app.services.benchmark_service import BenchmarkService


def _run(status: str = "pending") -> dict[str, object]:
    return {
        "run_id": "run-1", "client_request_id": "client-1",
        "request_digest": "digest", "name": "Regression",
        "mode": "paired_regression", "status": status,
        "config": {"seed": 7}, "schedule_digest": "schedule",
        "created_at": "2026-09-06T00:00:00+00:00",
        "updated_at": "2026-09-06T00:00:00+00:00",
    }


def _item(index: int, status: str = "pending") -> dict[str, object]:
    return {
        "run_id": "run-1", "item_index": index,
        "scenario_id": "standard", "pair_id": "pair-1",
        "block_index": 0, "assignment": {
            "variant": "baseline",
            "model": {"model_config_id": "model-a"}, "seats": [1],
        },
        "game_id": f"game-{index}" if status != "pending" else None,
        "status": status, "terminal_reason": None,
        "created_at": "2026-09-06T00:00:00+00:00",
        "updated_at": "2026-09-06T00:00:00+00:00",
    }


class FakeRepository:
    def __init__(self) -> None:
        self.runs = {"run-1": _run()}
        self.items = [_item(0, "completed"), _item(1)]

    def get_benchmark_run(self, run_id):
        value = self.runs.get(run_id)
        return deepcopy(value) if value else None

    def list_benchmark_runs(self):
        return [deepcopy(value) for value in self.runs.values()]

    def list_benchmark_items(self, run_id):
        return deepcopy(self.items) if run_id in self.runs else []

    def get_audience_snapshot(self, game_id):
        return {
            "state": {
                "players": {"1": {"role": "villager", "camp": "good"}},
                "win_result": {"winning_camp": "good"},
            },
        }

    def list_model_requests(self, game_id):
        return [
            {"request_id": "request-1", "actor_seat": 1, "status": "resolved"},
        ]

    def list_model_attempts(self, game_id):
        return [
            {"request_id": "request-1", "usage_known": True,
             "total_tokens": 12, "elapsed_ms": 5},
            {"request_id": "request-1", "usage_known": False,
             "total_tokens": None, "elapsed_ms": 9},
        ]


class FakeService:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.created_spec = None

    def create_run(self, specification, *, client_request_id):
        self.created_spec = specification
        return {**_run(), "client_request_id": client_request_id, "replayed": False}

    async def start(self, run_id):
        self.repository.runs[run_id]["status"] = "running"
        return deepcopy(self.repository.runs[run_id])

    async def pause(self, run_id):
        self.repository.runs[run_id]["status"] = "paused"
        return deepcopy(self.repository.runs[run_id])

    async def resume(self, run_id):
        self.repository.runs[run_id]["status"] = "running"
        return deepcopy(self.repository.runs[run_id])

    async def cancel(self, run_id):
        self.repository.runs[run_id]["status"] = "cancelled"
        return deepcopy(self.repository.runs[run_id])

    def generate_report(self, run_id):
        return {
            "metric_version": "v1", "input_digest": "facts",
            "planned": 2, "completed": 1,
        }

    async def request_report(self, run_id):
        return self.generate_report(run_id)

    async def delete_owned_game(self, run_id, game_id):
        self.repository.items = [
            item for item in self.repository.items if item.get("game_id") != game_id
        ]

    async def delete_owned_games(self, run_id, game_ids):
        deleted = []
        for game_id in game_ids:
            await self.delete_owned_game(run_id, game_id)
            deleted.append(game_id)
        return {"deleted": deleted, "failed": []}

    async def delete_run(self, run_id):
        self.repository.runs.pop(run_id, None)
        self.repository.items = []


@pytest.fixture
def api(monkeypatch):
    repository = FakeRepository()
    service = FakeService(repository)
    monkeypatch.setattr(benchmark_routes, "get_repository", lambda: repository)
    monkeypatch.setattr(benchmark_routes, "get_benchmark_service", lambda: service)
    monkeypatch.setattr(benchmark_routes, "get_game_service", lambda: None)
    app = FastAPI()
    app.include_router(benchmark_routes.router)
    return TestClient(app), repository, service


def test_create_list_detail_and_transition_routes(api):
    client, _, service = api
    response = client.post("/api/benchmarks", json={
        "client_request_id": "client-1", "name": "Regression",
        "mode": "paired_regression", "seed": 7,
        "scenario": {"scenario_id": "standard", "role_counts": {"villager": 2}},
        "repetitions": 1,
        "baseline": {"model_config_id": "base"},
        "candidate": {"model_config_id": "candidate"},
    })
    assert response.status_code == 201
    assert response.json()["planned_count"] == 2
    assert service.created_spec["scenario"]["scenario_id"] == "standard"

    listing = client.get("/api/benchmarks?offset=0&limit=10")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["benchmarks"][0]["progress"] == 0.5

    detail = client.get("/api/benchmarks/run-1")
    assert detail.status_code == 200
    assert detail.json()["completed_count"] == 1

    started = client.post("/api/benchmarks/run-1/start")
    assert started.status_code == 200
    assert started.json()["status"] == "running"


def test_games_report_rebuild_and_exports(api):
    client, repository, _ = api
    repository.items[1] = _item(1, "failed")

    games = client.get("/api/benchmarks/run-1/games?status=failed")
    assert games.status_code == 200
    assert games.json()["total"] == 1
    assert games.json()["games"][0]["game_id"] == "game-1"

    for path in ("report", "report/rebuild"):
        response = client.get(f"/api/benchmarks/run-1/{path}") if path == "report" else client.post(f"/api/benchmarks/run-1/{path}")
        assert response.status_code == 200
        assert response.json()["metric_version"] == "v1"

    exported = client.get("/api/benchmarks/run-1/export?format=json")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == 'attachment; filename="benchmark-run-1.json"'
    assert exported.json()["report"]["input_digest"] == "facts"

    repository.items[0]["scenario_id"] = "=FORMULA()"
    csv_response = client.get("/api/benchmarks/run-1/export?format=csv")
    rows = list(csv.reader(io.StringIO(csv_response.text)))
    assert rows[1][2] == "'=FORMULA()"
    exported_row = dict(zip(rows[0], rows[1]))
    assert exported_row["model"] == "model-a"
    assert exported_row["known_tokens"] == "12"
    assert exported_row["usage_completeness"] == "1/2"
    assert exported_row["min_attempt_latency_ms"] == "5"
    assert exported_row["max_attempt_latency_ms"] == "9"

    markdown = client.get("/api/benchmarks/run-1/export?format=markdown")
    assert "Metric version: v1" in markdown.text


def test_report_get_exposes_pending_and_failed_background_states(api, monkeypatch):
    client, _, service = api

    async def pending(_run_id):
        return {"status": "pending", "run_id": "run-1"}

    monkeypatch.setattr(service, "request_report", pending)
    response = client.get("/api/benchmarks/run-1/report")
    assert response.status_code == 202
    assert response.json() == {"status": "pending", "run_id": "run-1"}

    async def failed(_run_id):
        return {
            "status": "failed", "run_id": "run-1",
            "error_code": "ValueError",
        }

    monkeypatch.setattr(service, "request_report", failed)
    response = client.get("/api/benchmarks/run-1/report")
    assert response.status_code == 503
    assert response.json()["error_code"] == "ValueError"


def test_routes_return_stable_not_found_and_conflict_errors(api, monkeypatch):
    client, _, service = api
    missing = client.get("/api/benchmarks/missing")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "benchmark_not_found"

    async def invalid(_run_id):
        raise InvalidExecutionTransition("already completed")

    service.pause = invalid
    conflict = client.post("/api/benchmarks/run-1/pause")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "invalid_benchmark_transition"

    def duplicate(*_args, **_kwargs):
        raise CommitConflict("client_request_id has conflicting benchmark content")

    service.create_run = duplicate
    create = client.post("/api/benchmarks", json={
        "client_request_id": "client-1", "name": "Regression",
        "mode": "paired_regression", "seed": 7,
        "scenario": {"scenario_id": "standard", "role_counts": {"villager": 2}},
        "repetitions": 1,
        "baseline": {"model_config_id": "base"},
        "candidate": {"model_config_id": "candidate"},
    })
    assert create.status_code == 409
    assert create.json()["detail"]["code"] == "client_request_conflict"


def test_create_schema_normalizes_plan_fields_and_rejects_odd_pair_truncation():
    mixed = BenchmarkCreateRequest(
        client_request_id="client", name="Arena", mode="mixed_arena", seed=1,
        role_counts={"villager": 2}, block_count=2,
        model_assignments=[{"model_config_id": "m", "count": 2}],
    ).frozen_specification()
    assert mixed["scenario"] == {
        "scenario_id": "default", "role_counts": {"villager": 2},
    }
    assert mixed["games"] == 8
    assert len(mixed["models"]) == 2

    paired = BenchmarkCreateRequest(
        client_request_id="client", name="AB", mode="paired_regression", seed=1,
        role_counts={"villager": 2}, max_games=3,
        baseline_config_id="a", candidate_config_id="b",
    )
    with pytest.raises(ValueError, match="must be even"):
        paired.frozen_specification()


@pytest.mark.parametrize(
    ("player_count", "model_ids", "expected_ids"),
    [
        (5, ["a"], ["a"] * 5),
        (5, ["a", "b"], ["a"] * 3 + ["b"] * 2),
        (8, ["a", "b", "c"], ["a"] * 3 + ["b"] * 3 + ["c"] * 2),
        (3, ["b", "a", "b"], ["b", "a", "b"]),
    ],
)
def test_mixed_model_selection_expands_to_deterministic_seat_plan(
    player_count, model_ids, expected_ids,
):
    request = BenchmarkCreateRequest(
        client_request_id="client", name="Arena", mode="mixed_arena", seed=1,
        role_counts={"villager": player_count}, games=1,
        models=[{"model_config_id": model_id} for model_id in model_ids],
    )

    specification = request.frozen_specification()
    assert specification["models"] == [
        {"model_config_id": model_id} for model_id in expected_ids
    ]
    assert request.models == [{"model_config_id": model_id} for model_id in model_ids]
    assert specification == request.frozen_specification()
    schedule = BenchmarkService._mixed_schedule(specification)
    assert len(schedule[0]["assignment"]["seat_models"]) == player_count


def test_explicit_model_assignment_counts_are_preserved():
    specification = BenchmarkCreateRequest(
        client_request_id="client", name="Arena", mode="mixed_arena", seed=1,
        role_counts={"villager": 5}, games=1,
        model_assignments=[
            {"model_config_id": "a", "count": 1},
            {"model_config_id": "b", "count": 4},
        ],
    ).frozen_specification()
    assert specification["models"] == [
        {"model_config_id": "a"}, *[{"model_config_id": "b"}] * 4,
    ]


@pytest.mark.parametrize(
    "model_fields",
    [
        {"models": []},
        {"models": [{"model_config_id": str(index)} for index in range(6)]},
        {"model_assignments": [{"model_config_id": "a", "count": 4}]},
        {"model_assignments": [{"model_config_id": "a", "count": 6}]},
    ],
)
def test_invalid_model_seat_counts_return_422(api, model_fields):
    client, _, service = api
    response = client.post("/api/benchmarks", json={
        "client_request_id": "client", "name": "Arena", "mode": "mixed_arena",
        "seed": 1, "role_counts": {"villager": 5}, "games": 1,
        **model_fields,
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_benchmark"
    assert service.created_spec is None


def test_create_schema_covers_explicit_and_invalid_compatibility_fields():
    with pytest.raises(ValidationError, match="value must not be blank"):
        BenchmarkCreateRequest(
            client_request_id=" ", name="Arena", mode="mixed_arena", seed=1,
        )

    explicit = BenchmarkCreateRequest(
        client_request_id=" client ", name=" Arena ", mode="mixed_arena", seed=1,
        scenario={"scenario_id": "tiny", "role_counts": {"villager": 1}},
        games=3, models=[{"model_config_id": "m"}], max_games=2,
    ).frozen_specification()
    assert "client_request_id" not in explicit
    assert explicit["name"] == "Arena"
    assert explicit["games"] == 3
    assert explicit["models"] == [{"model_config_id": "m"}]

    invalid_assignments = BenchmarkCreateRequest(
        client_request_id="client", name="Arena", mode="mixed_arena", seed=1,
        role_counts={"villager": 1},
        model_assignments=[{"model_config_id": "m", "count": 0}],
    )
    with pytest.raises(ValueError, match="count must be positive"):
        invalid_assignments.frozen_specification()

    same = BenchmarkCreateRequest(
        client_request_id="client", name="AB", mode="paired_regression", seed=1,
        role_counts={"villager": 1},
        baseline={"model_config_id": "m"}, candidate={"model_config_id": "m"},
    )
    with pytest.raises(ValueError, match="must differ"):
        same.frozen_specification()

    bounded = BenchmarkCreateRequest(
        client_request_id="client", name="AB", mode="paired_regression", seed=1,
        role_counts={"villager": 1}, block_count=5, max_games=6,
        baseline_config_id="base", candidate_config_id="candidate",
    ).frozen_specification()
    assert bounded["repetitions"] == 3
    assert bounded["baseline"] == {"model_config_id": "base"}
    assert bounded["candidate"] == {"model_config_id": "candidate"}

    unbounded = BenchmarkCreateRequest(
        client_request_id="client", name="AB", mode="paired_regression", seed=1,
        role_counts={"villager": 1}, block_count=2,
        baseline_config_id="base", candidate_config_id="candidate",
    ).frozen_specification()
    assert unbounded["repetitions"] == 2


def test_default_dependency_accessors_read_application_singletons(monkeypatch):
    repository = object()
    service = object()
    monkeypatch.setitem(
        sys.modules, "app.main",
        SimpleNamespace(repository=repository, benchmark_service=service),
    )

    assert benchmark_routes.get_repository() is repository
    assert benchmark_routes.get_benchmark_service() is service


def test_replay_and_all_control_routes(api):
    client, _, service = api

    original_create = service.create_run

    def replay(specification, *, client_request_id):
        result = original_create(
            specification, client_request_id=client_request_id,
        )
        result["replayed"] = True
        return result

    service.create_run = replay
    created = client.post("/api/benchmarks", json={
        "client_request_id": "client-1", "name": "Regression",
        "mode": "paired_regression", "seed": 7,
        "scenario": {"scenario_id": "standard", "role_counts": {"villager": 2}},
        "repetitions": 1,
        "baseline": {"model_config_id": "base"},
        "candidate": {"model_config_id": "candidate"},
    })
    assert created.status_code == 200
    assert client.post("/api/benchmarks/run-1/resume").json()["status"] == "running"
    assert client.post("/api/benchmarks/run-1/cancel").json()["status"] == "cancelled"
    assert client.get("/api/benchmarks/run-1/games").json()["total"] == 2


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (ValueError("bad plan"), 422, "invalid_benchmark"),
        (KeyError("lost"), 404, "benchmark_not_found"),
        (RuntimeError("executor offline"), 503, "benchmark_executor_unavailable"),
    ],
)
def test_routes_translate_creation_and_transition_failures(
    api, failure, expected_status, expected_code,
):
    client, _, service = api
    if isinstance(failure, ValueError):
        def fail_create(*_args, **_kwargs):
            raise failure
        service.create_run = fail_create
        response = client.post("/api/benchmarks", json={
            "client_request_id": "client-1", "name": "Regression",
            "mode": "mixed_arena", "seed": 7,
            "scenario": {"scenario_id": "standard", "role_counts": {"villager": 1}},
            "games": 1, "models": [{"model_config_id": "m"}],
        })
    else:
        async def fail_start(_run_id):
            raise failure
        service.start = fail_start
        response = client.post("/api/benchmarks/run-1/start")
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (KeyError("gone"), 404, "benchmark_not_found"),
        (CommitConflict("changed facts"), 409, "report_conflict"),
        (ValueError("invalid facts"), 409, "report_conflict"),
    ],
)
def test_report_routes_translate_generation_failures(
    api, failure, expected_status, expected_code,
):
    client, _, service = api

    def fail_report(_run_id):
        raise failure

    service.generate_report = fail_report
    response = client.get("/api/benchmarks/run-1/report")
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code


def test_assignment_groups_and_csv_rows_cover_malformed_optional_data():
    assert benchmark_routes._assignment_groups({"assignment": None}) == [
        ("unknown", []),
    ]
    assert benchmark_routes._assignment_groups({
        "assignment": {"seat_models": {}},
    }) == [("unknown", [])]
    grouped = benchmark_routes._assignment_groups({
        "assignment": {"seat_models": {
            "1": {"model_id": "provider-model"},
            "2": {"model_id": "provider-model"},
            "bad": {"temperature": 0.2},
            "3": "invalid",
        }},
    })
    assert ("provider-model", [1, 2]) in grouped
    assert any(name == "unknown" and seats == [] for name, seats in grouped)
    assert benchmark_routes._assignment_groups({
        "assignment": {"model": {"model_id": "m"}, "seats": [1, 0, "2"]},
    }) == [("m", [1])]
    assert benchmark_routes._assignment_groups({
        "assignment": {"variant": "candidate"},
    }) == [("candidate", [])]

    repository = SimpleNamespace(
        get_audience_snapshot=lambda _game_id: {
            "state": {"players": {"1": "hidden"}, "win_result": None},
        },
        list_model_requests=lambda _game_id: [],
        list_model_attempts=lambda _game_id: [],
    )
    rows = benchmark_routes._csv_rows(
        repository,
        {"run_id": "run", "mode": "mixed_arena"},
        [{
            "run_id": "run", "item_index": 0, "scenario_id": "tiny",
            "pair_id": None, "game_id": "game", "status": "failed",
            "terminal_reason": "bad", "assignment": {
                "model": {"model_config_id": "m"}, "seats": [1],
            },
        }],
    )
    assert rows[0][8:12] == ["{}", "{}", None, 0]


@pytest.mark.parametrize("failure,status,code", [
    (KeyError("deleted during rebuild"), 404, "benchmark_not_found"),
    (ValueError("invalid report inputs"), 409, "report_conflict"),
    (CommitConflict("concurrent report write"), 409, "report_conflict"),
])
def test_rebuild_report_maps_generation_errors(api, failure, status, code):
    client, _, service = api
    def fail(_run_id):
        raise failure
    service.generate_report = fail
    response = client.post("/api/benchmarks/run-1/report/rebuild")
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_get_game_service_reads_main(monkeypatch):
    service = object()
    monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(game_service=service))
    assert benchmark_routes.get_game_service() is service


def test_enrich_benchmark_game_projects_state_and_handles_missing(monkeypatch):
    state = SimpleNamespace(
        phase=SimpleNamespace(value="night"),
        round_number=2,
        players={1: object(), 2: object()},
        alive_players=lambda: [1],
        win_result={"winning_camp": "good"},
    )
    service = SimpleNamespace(
        get_game_state=lambda gid: state if gid == "game-0" else None,
        get_display_name=lambda gid: "评测局",
        get_execution_info=lambda gid: (_ for _ in ()).throw(KeyError(gid))
        if gid == "game-0" else {"execution_status": "failed"},
    )
    monkeypatch.setattr(benchmark_routes, "get_game_service", lambda: service)
    filled = benchmark_routes._enrich_benchmark_game(_item(0, "completed"))
    assert filled["name"] == "评测局"
    assert filled["phase"] == "night"
    assert filled["player_count"] == 2
    assert filled["alive_count"] == 1
    assert filled["winner"] == "good"
    assert filled["execution_status"] is None
    pending = benchmark_routes._enrich_benchmark_game(_item(1))
    assert pending["phase"] is None
    missing_state = benchmark_routes._enrich_benchmark_game(_item(2, "failed"))
    assert missing_state["name"] == "评测局"
    assert missing_state["execution_status"] == "failed"
    monkeypatch.setattr(benchmark_routes, "get_game_service", lambda: None)
    assert benchmark_routes._enrich_benchmark_game(_item(0, "completed"))["name"] is None
    opaque = SimpleNamespace(
        phase=SimpleNamespace(value="dawn"),
        round_number=1,
        players={},
        alive_players=None,
        win_result=None,
    )
    partial = SimpleNamespace(
        get_game_state=lambda _gid: opaque,
        get_execution_info=lambda _gid: "running",
    )
    monkeypatch.setattr(benchmark_routes, "get_game_service", lambda: partial)
    empty = benchmark_routes._enrich_benchmark_game(_item(0, "completed"))
    assert empty["winner"] is None
    assert empty["alive_count"] is None
    assert empty["execution_status"] is None
    assert empty["name"] is None


def test_delete_benchmark_game_and_run_routes(api, monkeypatch):
    client, repository, service = api
    assert client.delete("/api/benchmarks/run-1/games/game-0").status_code == 204
    batch = client.post(
        "/api/benchmarks/run-1/games/batch-delete",
        json={"game_ids": ["game-1"]},
    )
    assert batch.status_code == 200
    assert batch.json()["deleted"] == ["game-1"]

    async def missing(*_args, **_kwargs):
        raise KeyError("gone")

    service.delete_owned_game = missing
    gone = client.delete("/api/benchmarks/run-1/games/game-0")
    assert gone.status_code == 404

    async def owned(*_args, **_kwargs):
        raise GameReferencedByBenchmark("run-2")

    service.delete_owned_game = owned
    conflict = client.delete("/api/benchmarks/run-1/games/game-0")
    assert conflict.status_code == 409

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("offline")

    service.delete_owned_game = unavailable
    assert client.delete("/api/benchmarks/run-1/games/game-0").status_code == 503

    async def busy(*_args, **_kwargs):
        raise OSError("busy")

    service.delete_owned_game = busy
    assert client.delete("/api/benchmarks/run-1/games/game-0").status_code == 500

    service.delete_run = missing
    assert client.delete("/api/benchmarks/run-1").status_code == 404
    service.delete_run = unavailable
    assert client.delete("/api/benchmarks/run-1").status_code == 503

    async def invalid(*_args, **_kwargs):
        raise InvalidExecutionTransition("busy")

    service.delete_run = invalid
    assert client.delete("/api/benchmarks/run-1").status_code == 409

    async def succeed(*_args, **_kwargs):
        repository.runs.pop("run-1", None)

    service.delete_run = succeed
    assert client.delete("/api/benchmarks/run-1").status_code == 204
