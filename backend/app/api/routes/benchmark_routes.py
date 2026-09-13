from __future__ import annotations

import asyncio
import csv
import io
import json
from collections import Counter
from collections.abc import Mapping
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from app.api.benchmark_schemas import (
    BenchmarkCreateRequest,
    BenchmarkGamesResponse,
    BenchmarkListResponse,
    BenchmarkRunResponse,
)
from app.persistence.repository import CommitConflict, InvalidExecutionTransition


router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])
_TERMINAL_ITEM_STATES = frozenset({"completed", "failed", "cancelled"})


def get_benchmark_service():
    from app.main import benchmark_service
    return benchmark_service


def get_repository():
    from app.main import repository
    return repository


def _error(status_code: int, code: str, message: str, **details: object) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **details},
    )


def _require_run(run_id: str) -> dict[str, object]:
    run = get_repository().get_benchmark_run(run_id)
    if run is None:
        raise _error(404, "benchmark_not_found", "Benchmark run not found")
    return run


def _run_response(
    run: Mapping[str, object], items: list[dict[str, object]],
) -> BenchmarkRunResponse:
    counts = Counter(str(item.get("status", "pending")) for item in items)
    planned = len(items)
    terminal = sum(counts[state] for state in _TERMINAL_ITEM_STATES)
    started = planned - counts["pending"]
    return BenchmarkRunResponse.model_validate({
        **dict(run),
        "planned_count": planned,
        "started_count": started,
        "terminal_count": terminal,
        "completed_count": counts["completed"],
        "failed_count": counts["failed"],
        "progress": terminal / planned if planned else 0.0,
    })


@router.post("", response_model=BenchmarkRunResponse, status_code=201)
async def create_benchmark(request: BenchmarkCreateRequest, response: Response):
    try:
        run = get_benchmark_service().create_run(
            request.frozen_specification(),
            client_request_id=request.client_request_id,
        )
    except CommitConflict as error:
        raise _error(409, "client_request_conflict", str(error)) from None
    except ValueError as error:
        raise _error(422, "invalid_benchmark", str(error)) from None
    if bool(run.get("replayed")):
        response.status_code = 200
    items = get_repository().list_benchmark_items(str(run["run_id"]))
    return _run_response(run, items)


@router.get("", response_model=BenchmarkListResponse)
async def list_benchmarks(
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    repository = get_repository()
    runs = repository.list_benchmark_runs()
    page = runs[offset:offset + limit]
    return BenchmarkListResponse(
        benchmarks=[
            _run_response(run, repository.list_benchmark_items(str(run["run_id"])))
            for run in page
        ],
        offset=offset, limit=limit, total=len(runs),
    )


@router.get("/{run_id}", response_model=BenchmarkRunResponse)
async def get_benchmark(run_id: str):
    run = _require_run(run_id)
    return _run_response(run, get_repository().list_benchmark_items(run_id))


async def _transition(run_id: str, action: str) -> BenchmarkRunResponse:
    _require_run(run_id)
    method = getattr(get_benchmark_service(), action)
    try:
        run = await method(run_id)
    except KeyError:
        raise _error(404, "benchmark_not_found", "Benchmark run not found") from None
    except InvalidExecutionTransition as error:
        raise _error(409, "invalid_benchmark_transition", str(error)) from None
    except RuntimeError as error:
        raise _error(503, "benchmark_executor_unavailable", str(error)) from None
    return _run_response(run, get_repository().list_benchmark_items(run_id))


@router.post("/{run_id}/start", response_model=BenchmarkRunResponse)
async def start_benchmark(run_id: str):
    return await _transition(run_id, "start")


@router.post("/{run_id}/pause", response_model=BenchmarkRunResponse)
async def pause_benchmark(run_id: str):
    return await _transition(run_id, "pause")


@router.post("/{run_id}/resume", response_model=BenchmarkRunResponse)
async def resume_benchmark(run_id: str):
    return await _transition(run_id, "resume")


@router.post("/{run_id}/cancel", response_model=BenchmarkRunResponse)
async def cancel_benchmark(run_id: str):
    return await _transition(run_id, "cancel")


@router.get("/{run_id}/games", response_model=BenchmarkGamesResponse)
async def list_benchmark_games(
    run_id: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    status: Annotated[str | None, Query()] = None,
):
    _require_run(run_id)
    items = get_repository().list_benchmark_items(run_id)
    if status is not None:
        items = [item for item in items if item.get("status") == status]
    return BenchmarkGamesResponse(
        games=items[offset:offset + limit],
        offset=offset, limit=limit, total=len(items),
    )


async def _generate_report(run_id: str) -> dict[str, object]:
    _require_run(run_id)
    try:
        return await asyncio.to_thread(get_benchmark_service().generate_report, run_id)
    except KeyError:
        raise _error(404, "benchmark_not_found", "Benchmark run not found") from None
    except (CommitConflict, ValueError) as error:
        raise _error(409, "report_conflict", str(error)) from None


@router.get("/{run_id}/report")
async def get_benchmark_report(run_id: str):
    _require_run(run_id)
    try:
        report = await get_benchmark_service().request_report(run_id)
    except KeyError:
        raise _error(404, "benchmark_not_found", "Benchmark run not found") from None
    except (CommitConflict, ValueError) as error:
        raise _error(409, "report_conflict", str(error)) from None
    if report.get("status") == "pending":
        return JSONResponse(status_code=202, content=report)
    if report.get("status") == "failed":
        return JSONResponse(status_code=503, content=report)
    return report


@router.post("/{run_id}/report/rebuild")
async def rebuild_benchmark_report(run_id: str):
    return await _generate_report(run_id)


def _assignment_groups(item: Mapping[str, object]) -> list[tuple[str, list[int]]]:
    assignment = item.get("assignment")
    if not isinstance(assignment, Mapping):
        return [("unknown", [])]
    seat_models = assignment.get("seat_models")
    if isinstance(seat_models, Mapping):
        grouped: dict[str, tuple[str, list[int]]] = {}
        for seat, model in seat_models.items():
            if not isinstance(model, Mapping):
                continue
            canonical = json.dumps(dict(model), ensure_ascii=False, sort_keys=True)
            model_name = str(model.get("model_config_id") or model.get("model_id") or "unknown")
            group = grouped.setdefault(canonical, (model_name, []))
            try:
                group[1].append(int(seat))
            except (TypeError, ValueError):
                continue
        return list(grouped.values()) or [("unknown", [])]
    model = assignment.get("model")
    seats = assignment.get("seats")
    if isinstance(model, Mapping):
        model_name = str(model.get("model_config_id") or model.get("model_id") or "unknown")
        valid_seats = [int(seat) for seat in seats or [] if type(seat) is int and seat > 0]
        return [(model_name, valid_seats)]
    return [(str(assignment.get("variant") or "unknown"), [])]


def _csv_rows(
    repository, run: Mapping[str, object], items: list[dict[str, object]],
) -> list[list[object]]:
    rows: list[list[object]] = []
    for item in items:
        game_id = item.get("game_id")
        snapshot_getter = getattr(repository, "get_audience_snapshot", None)
        request_getter = getattr(repository, "list_model_requests", None)
        attempt_getter = getattr(repository, "list_model_attempts", None)
        snapshot = snapshot_getter(game_id) if isinstance(game_id, str) and callable(snapshot_getter) else None
        state = snapshot.get("state", {}) if isinstance(snapshot, Mapping) else {}
        players = state.get("players", {}) if isinstance(state, Mapping) else {}
        players = players if isinstance(players, Mapping) else {}
        win = state.get("win_result") if isinstance(state, Mapping) else None
        winner = win.get("winning_camp") if isinstance(win, Mapping) else None
        requests = request_getter(game_id) if isinstance(game_id, str) and callable(request_getter) else []
        attempts = attempt_getter(game_id) if isinstance(game_id, str) and callable(attempt_getter) else []
        for model_name, seats in _assignment_groups(item):
            seat_set = set(seats)
            group_requests = [
                row for row in requests
                if not seat_set or row.get("actor_seat") in seat_set
            ]
            request_ids = {row.get("request_id") for row in group_requests}
            group_attempts = list(attempts) if not seat_set else [
                row for row in attempts if row.get("request_id") in request_ids
            ]
            known_attempts = [row for row in group_attempts if bool(row.get("usage_known"))]
            known_tokens = sum(
                int(row.get("total_tokens") or 0) for row in known_attempts
            )
            elapsed = [
                int(row["elapsed_ms"]) for row in group_attempts
                if type(row.get("elapsed_ms")) is int
            ]
            role_counts: Counter[str] = Counter()
            camp_counts: Counter[str] = Counter()
            winning_seats = 0
            for seat in seats:
                player = players.get(str(seat), players.get(seat))
                if not isinstance(player, Mapping):
                    continue
                role_counts[str(player.get("role", "unknown"))] += 1
                camp = str(player.get("camp", "unknown"))
                camp_counts[camp] += 1
                winning_seats += int(winner is not None and camp == winner)
            rows.append([
                run["run_id"], run["mode"], item["scenario_id"],
                item.get("pair_id"), item["item_index"], game_id,
                model_name, json.dumps(sorted(seats)),
                json.dumps(role_counts, ensure_ascii=False, sort_keys=True),
                json.dumps(camp_counts, ensure_ascii=False, sort_keys=True),
                winner, winning_seats, item["status"], item.get("terminal_reason"),
                len(group_requests), len(group_attempts), known_tokens,
                f"{len(known_attempts)}/{len(group_attempts)}",
                min(elapsed) if elapsed else "",
                max(elapsed) if elapsed else "",
            ])
    return rows


def _csv_text(repository, run: Mapping[str, object], items: list[dict[str, object]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow([
        "run_id", "mode", "scenario_id", "pair_id", "item_index", "game_id",
        "model", "seats", "role_counts", "camp_counts", "winner", "winning_seats",
        "status", "terminal_reason", "logical_requests", "attempts",
        "known_tokens", "usage_completeness", "min_attempt_latency_ms",
        "max_attempt_latency_ms",
    ])
    for row in _csv_rows(repository, run, items):
        writer.writerow([
            _spreadsheet_text(value) if isinstance(value, str) else value
            for value in row
        ])
    return stream.getvalue()


def _spreadsheet_text(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def _markdown_text(
    run: Mapping[str, object], items: list[dict[str, object]],
    report: Mapping[str, object],
) -> str:
    counts = Counter(str(item["status"]) for item in items)
    return "\n".join([
        f"# Benchmark {run['run_id']}", "",
        f"- Name: {run['name']}",
        f"- Mode: {run['mode']}",
        f"- Status: {run['status']}",
        f"- Planned games: {len(items)}",
        f"- Completed games: {counts['completed']}",
        f"- Failed games: {counts['failed']}",
        f"- Metric version: {report.get('metric_version', 'unavailable')}",
        f"- Input digest: {report.get('input_digest', 'unavailable')}", "",
        "```json", json.dumps(dict(report), ensure_ascii=False, sort_keys=True, indent=2),
        "```", "",
    ])


@router.get("/{run_id}/export")
async def export_benchmark(
    run_id: str,
    format: Annotated[Literal["json", "csv", "markdown"], Query()] = "json",
):
    run = _require_run(run_id)
    items = get_repository().list_benchmark_items(run_id)
    report = await _generate_report(run_id)
    if format == "csv":
        content = _csv_text(get_repository(), run, items)
        media_type, suffix = "text/csv; charset=utf-8", "csv"
    elif format == "markdown":
        content = _markdown_text(run, items, report)
        media_type, suffix = "text/markdown; charset=utf-8", "md"
    else:
        content = json.dumps(
            {"run": run, "games": items, "report": report},
            ensure_ascii=False, sort_keys=True, indent=2,
        )
        media_type, suffix = "application/json", "json"
    return Response(
        content=content, media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="benchmark-{run_id}.{suffix}"'
        },
    )
