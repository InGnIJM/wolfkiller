"""Durable logical model requests separated from individual provider attempts."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from app.persistence.repository import CommitConflict, GameRepository


class ModelRequestUnavailable(RuntimeError):
    """A logical request cannot safely be dispatched in its durable state."""


class StaleModelResult(RuntimeError):
    """A provider result belongs to an earlier execution generation."""


@dataclass(frozen=True)
class InvocationResult:
    normalized_result: dict[str, object]
    reused: bool
    attempt_id: str | None


ProviderResult = tuple[Mapping[str, object], Mapping[str, int] | None]


class ModelInvocationService:
    def __init__(
        self, repository: GameRepository,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._repository = repository
        self._fault_injector = fault_injector

    def _inject_fault(self, point: str) -> None:
        injector = getattr(self, "_fault_injector", None)
        if injector is not None:
            injector(point)

    @staticmethod
    def request_digest(frozen_request: Mapping[str, object]) -> str:
        try:
            raw = json.dumps(
                dict(frozen_request), ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("frozen request must be canonical JSON") from error
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def invoke(
        self, *, game_id: str, request_id: str, actor_seat: int,
        action_position: str, frozen_request: Mapping[str, object],
        provider_profile: str, model_id: str, execution_generation: int,
        provider_call: Callable[[], Awaitable[ProviderResult] | ProviderResult],
        recovery: bool = False,
    ) -> InvocationResult:
        digest = self.request_digest(frozen_request)
        self._repository.prepare_model_request(
            game_id=game_id, request_id=request_id, actor_seat=actor_seat,
            action_position=action_position, request_digest=digest,
            provider_profile=provider_profile, model_id=model_id,
        )
        self._inject_fault("request_prepared")
        request = self._repository.get_model_request(game_id, request_id)
        if request is None:  # pragma: no cover - repository contract
            raise KeyError(request_id)
        if request["request_digest"] != digest:
            raise CommitConflict(f"request_id {request_id!r} has conflicting content")
        if request["status"] in {"resolved", "consumed"}:
            result = request["normalized_result"]
            if not isinstance(result, Mapping):
                raise ModelRequestUnavailable("resolved request has no normalized result")
            return InvocationResult(dict(result), True, None)
        if request["status"] == "unknown":
            if not recovery:
                raise ModelRequestUnavailable("unknown request requires explicit recovery")
            if not self._repository.reserve_recovery_retry(game_id, request_id):
                raise ModelRequestUnavailable("recovery retry already consumed")
        elif request["status"] != "prepared":
            raise ModelRequestUnavailable(
                f"request in {request['status']} state cannot be dispatched"
            )

        game = self._repository.get_game(game_id)
        if game is None:
            raise KeyError(game_id)
        if game["execution_status"] != "running":
            raise ModelRequestUnavailable(
                f"game in {game['execution_status']} state cannot dispatch requests"
            )
        if game["execution_generation"] != execution_generation:
            raise StaleModelResult("execution generation is stale before dispatch")
        attempt = self._repository.start_model_attempt(
            game_id=game_id, request_id=request_id,
            execution_generation=execution_generation,
        )
        self._inject_fault("attempt_dispatched")
        started = time.monotonic()
        try:
            produced = provider_call()
            if inspect.isawaitable(produced):
                produced = await produced
            normalized, usage = produced
            result = self._normalize_result(normalized)
            usage_row = self._normalize_usage(usage)
        except BaseException as error:
            elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
            self._repository.finish_model_attempt(
                str(attempt["attempt_id"]), status="failed",
                failure_code=self._failure_code(error), elapsed_ms=elapsed_ms,
                usage=None,
            )
            self._repository.resolve_model_request(
                game_id, request_id, status="failed", normalized_result=None,
            )
            raise

        elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        current = self._repository.get_game(game_id)
        if current is None or current["execution_generation"] != execution_generation:
            self._repository.finish_model_attempt(
                str(attempt["attempt_id"]), status="stale",
                failure_code="stale_execution_generation", elapsed_ms=elapsed_ms,
                usage=usage_row,
            )
            self._repository.resolve_model_request(
                game_id, request_id, status="unknown", normalized_result=None,
            )
            raise StaleModelResult("provider result belongs to an old execution generation")
        self._repository.finish_model_attempt(
            str(attempt["attempt_id"]), status="resolved", failure_code=None,
            elapsed_ms=elapsed_ms, usage=usage_row,
        )
        self._repository.resolve_model_request(
            game_id, request_id, status="resolved", normalized_result=result,
        )
        self._inject_fault("result_persisted")
        return InvocationResult(result, False, str(attempt["attempt_id"]))

    def invoke_sync(
        self, *, game_id: str, request_id: str, actor_seat: int,
        action_position: str, frozen_request: Mapping[str, object],
        provider_profile: str, model_id: str, execution_generation: int,
        provider_call: Callable[[], ProviderResult], recovery: bool = False,
    ) -> InvocationResult:
        """Synchronous equivalent for scheduler work already running off-loop."""
        digest = self.request_digest(frozen_request)
        self._repository.prepare_model_request(
            game_id=game_id, request_id=request_id, actor_seat=actor_seat,
            action_position=action_position, request_digest=digest,
            provider_profile=provider_profile, model_id=model_id,
        )
        self._inject_fault("request_prepared")
        request = self._repository.get_model_request(game_id, request_id)
        if request is None:
            raise KeyError(request_id)
        if request["request_digest"] != digest:
            raise CommitConflict(f"request_id {request_id!r} has conflicting content")
        if request["status"] in {"resolved", "consumed"}:
            normalized = request["normalized_result"]
            if not isinstance(normalized, Mapping):
                raise ModelRequestUnavailable("resolved request has no normalized result")
            return InvocationResult(dict(normalized), True, None)
        if request["status"] == "unknown":
            if not recovery:
                raise ModelRequestUnavailable("unknown request requires explicit recovery")
            if not self._repository.reserve_recovery_retry(game_id, request_id):
                raise ModelRequestUnavailable("recovery retry already consumed")
        elif request["status"] != "prepared":
            raise ModelRequestUnavailable(
                f"request in {request['status']} state cannot be dispatched"
            )
        game = self._repository.get_game(game_id)
        if game is None:
            raise KeyError(game_id)
        if game["execution_status"] != "running":
            raise ModelRequestUnavailable(
                f"game in {game['execution_status']} state cannot dispatch requests"
            )
        if game["execution_generation"] != execution_generation:
            raise StaleModelResult("execution generation is stale before dispatch")
        attempt = self._repository.start_model_attempt(
            game_id=game_id, request_id=request_id,
            execution_generation=execution_generation,
        )
        self._inject_fault("attempt_dispatched")
        started = time.monotonic()
        try:
            produced = provider_call()
            normalized, usage = produced
            result = self._normalize_result(normalized)
            usage_row = self._normalize_usage(usage)
        except BaseException as error:
            elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
            self._repository.finish_model_attempt(
                str(attempt["attempt_id"]), status="failed",
                failure_code=self._failure_code(error), elapsed_ms=elapsed_ms,
                usage=None,
            )
            self._repository.resolve_model_request(
                game_id, request_id, status="failed", normalized_result=None,
            )
            raise
        elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        current = self._repository.get_game(game_id)
        if current is None or current["execution_generation"] != execution_generation:
            self._repository.finish_model_attempt(
                str(attempt["attempt_id"]), status="stale",
                failure_code="stale_execution_generation", elapsed_ms=elapsed_ms,
                usage=usage_row,
            )
            self._repository.resolve_model_request(
                game_id, request_id, status="unknown", normalized_result=None,
            )
            raise StaleModelResult("provider result belongs to an old execution generation")
        self._repository.finish_model_attempt(
            str(attempt["attempt_id"]), status="resolved", failure_code=None,
            elapsed_ms=elapsed_ms, usage=usage_row,
        )
        self._repository.resolve_model_request(
            game_id, request_id, status="resolved", normalized_result=result,
        )
        self._inject_fault("result_persisted")
        return InvocationResult(result, False, str(attempt["attempt_id"]))

    @staticmethod
    def _normalize_result(value: object) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError("normalized model result must be an object")
        try:
            return json.loads(json.dumps(
                dict(value), ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ))
        except (TypeError, ValueError) as error:
            raise ValueError("normalized model result must be canonical JSON") from error

    @staticmethod
    def _normalize_usage(value: object) -> dict[str, int] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TypeError("usage must be an object or null")
        names = {"prompt_tokens", "completion_tokens", "total_tokens"}
        if set(value) != names or any(type(value[name]) is not int or value[name] < 0 for name in names):
            raise ValueError("usage must contain non-negative token counts")
        if value["prompt_tokens"] + value["completion_tokens"] != value["total_tokens"]:
            raise ValueError("usage token totals do not add up")
        return {name: int(value[name]) for name in names}

    @staticmethod
    def _failure_code(error: BaseException) -> str:
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
            return "provider_timeout"
        return "model_invocation_error"
