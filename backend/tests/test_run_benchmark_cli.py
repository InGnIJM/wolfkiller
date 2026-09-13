from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from urllib.error import URLError

import pytest

from app.api.benchmark_schemas import BenchmarkCreateRequest
from app.services.benchmark_service import BenchmarkService


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_benchmark.py"
SPEC = importlib.util.spec_from_file_location("run_benchmark_cli", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)


class _Headers:
    @staticmethod
    def get_content_type() -> str:
        return "application/json"


class _Response:
    headers = _Headers()

    def __init__(self, body: object) -> None:
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, object]] = []

    def json_request(self, method: str, path: str, payload=None):
        self.calls.append((method, path, payload))
        return self.responses.pop(0)


class FakeExportClient:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, str, str]] = []

    def request(self, method: str, path: str, *, accept: str):
        self.calls.append((method, path, accept))
        return cli.ApiResponse(self.body, accept)


def test_legacy_arguments_build_explicit_single_config_spec() -> None:
    args = cli.parse_args([
        "--games", "3", "--label", "baseline", "--model-config-id", "model-a",
        "--client-request-id", "request-a",
    ])

    payload = cli.build_create_payload(args)

    assert args.command == "run"
    assert payload["client_request_id"] == "request-a"
    assert payload["name"] == "baseline"
    assert payload["mode"] == "mixed_arena"
    assert payload["games"] == 3
    assert payload["models"] == [{"model_config_id": "model-a"}]
    assert payload["concurrency"] == 1
    assert payload["game_timeout_seconds"] == 3600


@pytest.mark.parametrize("model_id", [None, "model-a"])
def test_single_config_payload_schedules_every_seat(model_id) -> None:
    arguments = ["create", "--games", "3"]
    if model_id is not None:
        arguments.extend(["--model-config-id", model_id])
    payload = cli.build_create_payload(cli.parse_args(arguments))
    specification = BenchmarkCreateRequest.model_validate(payload).frozen_specification()

    schedule = BenchmarkService._mixed_schedule(specification)

    assert len(schedule) == 3
    for item in schedule:
        assert item["assignment"]["seat_models"] == {
            str(seat): {"model_config_id": model_id} for seat in range(1, 11)
        }


def test_paired_mode_requires_and_freezes_both_variants() -> None:
    args = cli.parse_args([
        "create", "--mode", "paired", "--repetitions", "4",
        "--baseline-model-config-id", "base", "--candidate-model-config-id", "candidate",
    ])

    payload = cli.build_create_payload(args)

    assert payload["mode"] == "paired_regression"
    assert payload["repetitions"] == 4
    assert payload["baseline"] == {"model_config_id": "base"}
    assert payload["candidate"] == {"model_config_id": "candidate"}


def test_paired_mode_rejects_half_a_pair() -> None:
    args = cli.parse_args(["create", "--baseline-model-config-id", "base"])
    with pytest.raises(ValueError, match="both paired model ids"):
        cli.build_create_payload(args)


def test_run_without_yes_creates_preview_but_does_not_start(capsys) -> None:
    args = cli.parse_args(["--games", "1", "--client-request-id", "request"])
    client = FakeClient([{"run_id": "run-1", "status": "pending", "schedule_count": 1}])

    assert cli.execute(args, client) == 0

    assert client.calls == [("POST", "/api/benchmarks", cli.build_create_payload(args))]
    captured = capsys.readouterr()
    assert "Draft preview (run-1)" in captured.err
    assert "start run-1 --yes" in captured.err


def test_start_requires_yes_before_network_call() -> None:
    args = cli.parse_args(["start", "run-1"])
    client = FakeClient([])

    with pytest.raises(cli.ApiError, match="pass --yes"):
        cli.execute(args, client)
    assert client.calls == []


def test_yes_starts_and_polling_observes_completion() -> None:
    args = cli.parse_args([
        "--games", "1", "--yes", "--poll-interval", "0.001",
        "--client-request-id", "request",
    ])
    client = FakeClient([
        {"run_id": "run-1", "status": "pending"},
        {"run_id": "run-1", "status": "running"},
        {"run_id": "run-1", "status": "completed"},
    ])

    assert cli.execute(args, client) == 0
    assert [(method, path) for method, path, _ in client.calls] == [
        ("POST", "/api/benchmarks"),
        ("POST", "/api/benchmarks/run-1/start"),
        ("GET", "/api/benchmarks/run-1"),
    ]


def test_unreachable_service_has_actionable_error() -> None:
    def unavailable(_request, **_kwargs):
        raise URLError("connection refused")

    client = cli.ApiClient("http://127.0.0.1:9", opener=unavailable)
    with pytest.raises(cli.ServiceUnavailable, match="Start the backend or pass --api-url"):
        client.json_request("GET", "/api/benchmarks")


def test_client_sends_json_to_expected_endpoint() -> None:
    requests = []

    def open_request(request, **_kwargs):
        requests.append(request)
        return _Response({"run_id": "run-1"})

    client = cli.ApiClient("http://localhost:8000/", opener=open_request)
    assert client.json_request("POST", "/api/benchmarks", {"name": "x"}) == {
        "run_id": "run-1"
    }
    request = requests[0]
    assert request.full_url == "http://localhost:8000/api/benchmarks"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"name": "x"}


def test_export_writes_the_service_response_without_recomputing(tmp_path: Path) -> None:
    output = tmp_path / "report.md"
    args = cli.parse_args([
        "export", "run-1", "--format", "markdown", "--output", str(output),
    ])
    client = FakeExportClient(b"# durable report\n")

    assert cli.execute(args, client) == 0

    assert output.read_bytes() == b"# durable report\n"
    assert client.calls == [
        ("GET", "/api/benchmarks/run-1/export?format=markdown", "text/markdown")
    ]
