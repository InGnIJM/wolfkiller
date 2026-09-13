"""Create and control durable benchmarks through a running Wolf Killer API.

This command never constructs ``GameService``. Start the backend first::

    python scripts/run_benchmark.py --games 20 --model-config-id ID --yes
    python scripts/run_benchmark.py create --games 20 --model-config-id ID
    python scripts/run_benchmark.py start RUN_ID --yes
    python scripts/run_benchmark.py status RUN_ID
    python scripts/run_benchmark.py pause RUN_ID
    python scripts/run_benchmark.py resume RUN_ID --yes
    python scripts/run_benchmark.py export RUN_ID --format markdown -o report.md

No command means the backwards-compatible ``run`` form: create a draft,
print its frozen preview, and start it only when ``--yes`` is present.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PRESETS: dict[str, dict[str, int]] = {
    "standard10": {
        "wolf-killer-werewolf": 3,
        "wolf-killer-villager": 3,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-hunter": 1,
        "wolf-killer-guard": 1,
    },
}
COMMANDS = {"run", "create", "list", "start", "status", "pause", "resume", "export"}
STOPPED_STATUSES = {
    "completed", "failed", "cancelled", "interrupted", "blocked", "paused",
}


class ApiError(RuntimeError):
    """The service answered, but rejected the request."""


class ServiceUnavailable(RuntimeError):
    """The configured Wolf Killer service could not be reached."""


@dataclass(frozen=True)
class ApiResponse:
    body: bytes
    content_type: str

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError("service returned an invalid JSON response") from error


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        accept: str = "application/json",
    ) -> ApiResponse:
        data = None
        headers = {"Accept": accept}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                return ApiResponse(response.read(), response.headers.get_content_type())
        except HTTPError as error:
            raise ApiError(
                f"service returned HTTP {error.code}: {_http_error_detail(error)}"
            ) from error
        except (URLError, TimeoutError, socket.timeout, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ServiceUnavailable(
                f"cannot reach Wolf Killer service at {self.base_url}: {reason}. "
                "Start the backend or pass --api-url."
            ) from error

    def json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> Any:
        return self.request(method, path, payload=payload).json()


def _http_error_detail(error: HTTPError) -> str:
    try:
        raw = error.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return error.reason or "request rejected"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip() or error.reason or "request rejected"
    if isinstance(parsed, dict) and "detail" in parsed:
        return str(parsed["detail"])
    return json.dumps(parsed, ensure_ascii=False)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values[0] not in COMMANDS:
        values.insert(0, "run")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("run_id", nargs="?", help="benchmark id for control/export commands")
    parser.add_argument(
        "--api-url",
        default=os.getenv("WOLFKILLER_API_URL", "http://127.0.0.1:8000"),
        help="running backend base URL (env: WOLFKILLER_API_URL)",
    )
    parser.add_argument("--request-timeout", type=_positive_float, default=30.0)
    parser.add_argument("--games", type=_positive_int, default=10, help="games in single-config mode")
    parser.add_argument("--repetitions", type=_positive_int, default=10, help="pairs in paired mode")
    parser.add_argument("--concurrency", type=_positive_int, default=1, help="server-side game concurrency (maximum 4)")
    parser.add_argument("--model-config-id", help="single-config model id; omit for env default")
    parser.add_argument("--baseline-model-config-id", help="paired baseline model id")
    parser.add_argument("--candidate-model-config-id", help="paired candidate model id")
    parser.add_argument(
        "--mode", choices=("auto", "single-config", "paired"), default="auto",
        help="auto chooses paired when both paired model ids are supplied",
    )
    parser.add_argument("--preset", choices=sorted(PRESETS), default="standard10")
    parser.add_argument("--label", default="run", help="benchmark name")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--client-request-id", help="idempotency key; generated when omitted")
    parser.add_argument("--poll-interval", type=_positive_float, default=5.0)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=3600.0)
    parser.add_argument(
        "--data-dir", default="data",
        help="deprecated compatibility option; data directory belongs to the API service",
    )
    parser.add_argument(
        "--no-report", action="store_true",
        help="deprecated compatibility option; reports are generated only when requested/exported",
    )
    parser.add_argument("--detach", action="store_true", help="return immediately after starting")
    parser.add_argument("--yes", action="store_true", help="explicitly authorize real model calls")
    parser.add_argument("--format", choices=("json", "csv", "markdown"), default="json")
    parser.add_argument("-o", "--output", help="export file; stdout when omitted")
    args = parser.parse_args(values)

    if args.seed < 0:
        parser.error("--seed must be a non-negative integer")
    if args.concurrency > 4:
        parser.error("--concurrency must be at most 4")
    if args.command in {"start", "status", "pause", "resume", "export"} and not args.run_id:
        parser.error(f"{args.command} requires RUN_ID")
    if args.command in {"run", "create", "list"} and args.run_id:
        parser.error(f"{args.command} does not accept RUN_ID")
    return args


def build_create_payload(args: argparse.Namespace) -> dict[str, object]:
    paired_ids = (args.baseline_model_config_id, args.candidate_model_config_id)
    paired = args.mode == "paired" or (args.mode == "auto" and all(paired_ids))
    if args.mode == "single-config" and any(paired_ids):
        raise ValueError("single-config mode does not accept baseline/candidate model ids")
    if paired and not all(paired_ids):
        raise ValueError(
            "paired mode requires both --baseline-model-config-id and --candidate-model-config-id"
        )
    if not paired and any(paired_ids):
        raise ValueError("supply both paired model ids, or neither")

    payload: dict[str, object] = {
        "client_request_id": args.client_request_id or str(uuid.uuid4()),
        "name": args.label,
        "mode": "paired_regression" if paired else "mixed_arena",
        "seed": args.seed,
        "concurrency": args.concurrency,
        "game_timeout_seconds": max(1, int(args.timeout_seconds)),
        "scenario": {"scenario_id": args.preset, "role_counts": PRESETS[args.preset]},
    }
    if paired:
        payload.update({
            "repetitions": args.repetitions,
            "baseline": {"model_config_id": args.baseline_model_config_id},
            "candidate": {"model_config_id": args.candidate_model_config_id},
        })
    else:
        payload.update({
            "games": args.games,
            "models": [{"model_config_id": args.model_config_id}],
        })
    return payload


def _extract_run_id(value: object) -> str:
    if isinstance(value, dict):
        direct = value.get("run_id")
        if isinstance(direct, str) and direct:
            return direct
        nested = value.get("run")
        if isinstance(nested, dict):
            nested_id = nested.get("run_id")
            if isinstance(nested_id, str) and nested_id:
                return nested_id
    raise ApiError("service response did not contain a benchmark run_id")


def _status(value: object) -> str | None:
    if isinstance(value, dict):
        status = value.get("status")
        if isinstance(status, str):
            return status
        run = value.get("run")
        if isinstance(run, dict) and isinstance(run.get("status"), str):
            return run["status"]
    return None


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def wait_for_run(client: ApiClient, run_id: str, args: argparse.Namespace) -> dict[str, object]:
    deadline = time.monotonic() + args.timeout_seconds
    previous: str | None = None
    while True:
        value = client.json_request("GET", f"/api/benchmarks/{run_id}")
        if not isinstance(value, dict):
            raise ApiError("benchmark status response must be a JSON object")
        current = _status(value)
        if current != previous:
            print(f"benchmark {run_id}: {current or 'unknown'}", file=sys.stderr)
            previous = current
        if current in STOPPED_STATUSES:
            return value
        if time.monotonic() >= deadline:
            raise ApiError(
                f"timed out waiting for benchmark {run_id}; it is still running on the service"
            )
        time.sleep(args.poll_interval)


def _require_yes(args: argparse.Namespace, action: str) -> None:
    if not args.yes:
        raise ApiError(
            f"refusing to {action}: this performs real model calls; inspect the draft and pass --yes"
        )


def export_run(client: ApiClient, run_id: str, args: argparse.Namespace) -> None:
    accept = {
        "json": "application/json", "csv": "text/csv", "markdown": "text/markdown",
    }[args.format]
    response = client.request(
        "GET", f"/api/benchmarks/{run_id}/export?{urlencode({'format': args.format})}",
        accept=accept,
    )
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.body)
        print(f"Export written: {target}", file=sys.stderr)
        return
    sys.stdout.buffer.write(response.body)
    if response.body and not response.body.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")


def execute(args: argparse.Namespace, client: ApiClient) -> int:
    if args.data_dir != "data":
        raise ValueError(
            "--data-dir is no longer a CLI storage option; configure the running API service instead"
        )
    if args.command == "list":
        _print_json(client.json_request("GET", "/api/benchmarks"))
        return 0
    if args.command == "status":
        _print_json(client.json_request("GET", f"/api/benchmarks/{args.run_id}"))
        return 0
    if args.command == "pause":
        _print_json(client.json_request("POST", f"/api/benchmarks/{args.run_id}/pause"))
        return 0
    if args.command == "resume":
        _require_yes(args, "resume benchmark")
        value = client.json_request("POST", f"/api/benchmarks/{args.run_id}/resume")
        _print_json(value)
        if not args.detach:
            wait_for_run(client, args.run_id, args)
        return 0
    if args.command == "start":
        _require_yes(args, "start benchmark")
        value = client.json_request("POST", f"/api/benchmarks/{args.run_id}/start")
        _print_json(value)
        if not args.detach:
            wait_for_run(client, args.run_id, args)
        return 0
    if args.command == "export":
        export_run(client, args.run_id, args)
        return 0

    draft = client.json_request("POST", "/api/benchmarks", build_create_payload(args))
    run_id = _extract_run_id(draft)
    print(f"Draft preview ({run_id}):", file=sys.stderr)
    _print_json(draft)
    if args.command == "create" or not args.yes:
        if args.command == "run" and not args.yes:
            print(
                f"Draft remains pending. Start with: python scripts/run_benchmark.py start {run_id} --yes",
                file=sys.stderr,
            )
        return 0

    started = client.json_request("POST", f"/api/benchmarks/{run_id}/start")
    print(f"Started benchmark {run_id}: {_status(started) or 'running'}", file=sys.stderr)
    if not args.detach:
        final = wait_for_run(client, run_id, args)
        if _status(final) != "completed":
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return execute(args, ApiClient(args.api_url, timeout=args.request_timeout))
    except (ApiError, ServiceUnavailable, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
