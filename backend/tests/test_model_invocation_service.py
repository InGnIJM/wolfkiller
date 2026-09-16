from __future__ import annotations

import asyncio

import pytest

from app.persistence.repository import (
    CommitConflict,
    GameRepository,
    ModelAttemptQuotaExceeded,
)
from app.services.model_invocation_service import (
    ModelInvocationService, ModelRequestUnavailable, StaleModelResult,
)


class FakeInvocationRepository:
    def __init__(
        self, *, request: dict | None = None, game: dict | None = None,
        current: dict | None | object = ..., reserve: bool = True,
    ) -> None:
        self.request = request
        self.game = game
        self.current = game if current is ... else current
        self.reserve = reserve
        self.game_reads = 0
        self.finished: list[dict] = []
        self.resolved: list[dict] = []

    def prepare_model_request(self, **_kwargs) -> None:
        return None

    def get_model_request(self, _game_id, _request_id):
        return self.request

    def reserve_recovery_retry(self, _game_id, _request_id) -> bool:
        return self.reserve

    def get_game(self, _game_id):
        self.game_reads += 1
        return self.game if self.game_reads == 1 else self.current

    def start_model_attempt(self, **_kwargs):
        return {"attempt_id": "attempt"}

    def finish_model_attempt(self, _attempt_id, **kwargs) -> None:
        self.finished.append(kwargs)

    def resolve_model_request(self, _game_id, _request_id, **kwargs) -> None:
        self.resolved.append(kwargs)


def _request(status: str = "prepared", result: object = None) -> dict:
    return {
        "request_digest": ModelInvocationService.request_digest({"prompt": "x"}),
        "status": status,
        "normalized_result": result,
    }


def _game(status: str = "running", generation: int = 1) -> dict:
    return {"execution_status": status, "execution_generation": generation}


def _sync_invoke(service: ModelInvocationService, provider_call=lambda: ({"text": "ok"}, None)):
    return service.invoke_sync(
        game_id="game", request_id="request", actor_seat=1,
        action_position="speech:1", frozen_request={"prompt": "x"},
        provider_profile="test", model_id="fake", execution_generation=1,
        provider_call=provider_call,
    )


def _repository(tmp_path) -> GameRepository:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="running",
        source="native", model_snapshot=[], execution_generation=1,
    )
    return repository


def test_resolved_logical_request_is_reused_without_calling_provider(tmp_path) -> None:
    repository = _repository(tmp_path)
    calls = 0

    async def provider():
        nonlocal calls
        calls += 1
        return {"text": "hello"}, {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}

    async def scenario() -> None:
        service = ModelInvocationService(repository)
        first = await service.invoke(
            game_id="game", request_id="request", actor_seat=1,
            action_position="speech:1:1", frozen_request={"prompt": "x"},
            provider_profile="test", model_id="fake", execution_generation=1,
            provider_call=provider,
        )
        second = await service.invoke(
            game_id="game", request_id="request", actor_seat=1,
            action_position="speech:1:1", frozen_request={"prompt": "x"},
            provider_profile="test", model_id="fake", execution_generation=1,
            provider_call=provider,
        )
        assert first.normalized_result == second.normalized_result == {"text": "hello"}
        assert first.reused is False and second.reused is True
        assert calls == 1

    try:
        asyncio.run(scenario())
        attempts = repository.list_model_attempts("game")
        assert len(attempts) == 1 and attempts[0]["total_tokens"] == 3
    finally:
        repository.close()


def test_provider_failure_is_ledgered_without_raw_response(tmp_path) -> None:
    repository = _repository(tmp_path)

    async def provider():
        raise TimeoutError("secret provider body")

    async def scenario() -> None:
        service = ModelInvocationService(repository)
        with pytest.raises(TimeoutError):
            await service.invoke(
                game_id="game", request_id="request", actor_seat=1,
                action_position="vote:1:1", frozen_request={"prompt": "x"},
                provider_profile="test", model_id="fake", execution_generation=1,
                provider_call=provider,
            )

    try:
        asyncio.run(scenario())
        request = repository.get_model_request("game", "request")
        assert request["status"] == "failed"
        assert request["normalized_result"] is None
        attempt = repository.list_model_attempts("game")[0]
        assert attempt["status"] == "failed"
        assert attempt["failure_code"] == "provider_timeout"
        assert "secret" not in str(attempt)
    finally:
        repository.close()


def test_unknown_request_gets_only_one_explicit_recovery_retry(tmp_path) -> None:
    repository = _repository(tmp_path)
    repository.prepare_model_request(
        game_id="game", request_id="request", actor_seat=1,
        action_position="vote:1:1", request_digest=ModelInvocationService.request_digest({"prompt": "x"}),
        provider_profile="test", model_id="fake",
    )
    repository.resolve_model_request("game", "request", status="unknown", normalized_result=None)

    async def provider():
        raise TimeoutError()

    async def scenario() -> None:
        service = ModelInvocationService(repository)
        with pytest.raises(TimeoutError):
            await service.invoke(
                game_id="game", request_id="request", actor_seat=1,
                action_position="vote:1:1", frozen_request={"prompt": "x"},
                provider_profile="test", model_id="fake", execution_generation=1,
                provider_call=provider, recovery=True,
            )
        repository.resolve_model_request("game", "request", status="unknown", normalized_result=None)
        with pytest.raises(ModelRequestUnavailable, match="retry"):
            await service.invoke(
                game_id="game", request_id="request", actor_seat=1,
                action_position="vote:1:1", frozen_request={"prompt": "x"},
                provider_profile="test", model_id="fake", execution_generation=1,
                provider_call=provider, recovery=True,
            )

    try:
        asyncio.run(scenario())
    finally:
        repository.close()


def test_late_result_from_old_generation_is_not_resolved(tmp_path) -> None:
    repository = _repository(tmp_path)

    async def provider():
        repository.transition_execution(
            "game", expected=("running",), target="paused",
        )
        repository.transition_execution(
            "game", expected=("paused",), target="running", increment_generation=True,
        )
        return {"text": "late"}, None

    async def scenario() -> None:
        with pytest.raises(StaleModelResult):
            await ModelInvocationService(repository).invoke(
                game_id="game", request_id="request", actor_seat=1,
                action_position="speech:1:1", frozen_request={"prompt": "x"},
                provider_profile="test", model_id="fake", execution_generation=1,
                provider_call=provider,
            )

    try:
        asyncio.run(scenario())
        assert repository.get_model_request("game", "request")["status"] == "unknown"
        assert repository.list_model_attempts("game")[0]["status"] == "stale"
    finally:
        repository.close()


def test_benchmark_attempt_quota_stops_before_second_provider_call(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    calls = 0
    repository.create_benchmark_run(
        run_id="run", client_request_id="client", request_digest="digest",
        name="quota", mode="mixed_arena",
        config={"max_attempts_per_game": 1}, schedule=[],
    )
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="running",
        source="benchmark", benchmark_run_id="run", model_snapshot=[],
        execution_generation=1,
    )

    async def provider():
        nonlocal calls
        calls += 1
        return {"text": "ok"}, None

    async def scenario() -> None:
        service = ModelInvocationService(repository)
        await service.invoke(
            game_id="game", request_id="first", actor_seat=1,
            action_position="speech:1", frozen_request={"prompt": "one"},
            provider_profile="test", model_id="fake", execution_generation=1,
            provider_call=provider,
        )
        with pytest.raises(ModelAttemptQuotaExceeded, match="attempt quota"):
            await service.invoke(
                game_id="game", request_id="second", actor_seat=1,
                action_position="speech:2", frozen_request={"prompt": "two"},
                provider_profile="test", model_id="fake", execution_generation=1,
                provider_call=provider,
            )

    try:
        asyncio.run(scenario())
        assert calls == 1
        assert len(repository.list_model_attempts("game")) == 1
    finally:
        repository.close()


def test_request_digest_and_result_usage_validation_reject_noncanonical_values() -> None:
    with pytest.raises(ValueError, match="canonical JSON"):
        ModelInvocationService.request_digest({"temperature": float("nan")})
    with pytest.raises(TypeError, match="must be an object"):
        ModelInvocationService._normalize_result("text")
    with pytest.raises(ValueError, match="canonical JSON"):
        ModelInvocationService._normalize_result({"value": float("nan")})
    with pytest.raises(TypeError, match="object or null"):
        ModelInvocationService._normalize_usage("tokens")
    with pytest.raises(ValueError, match="non-negative"):
        ModelInvocationService._normalize_usage({"total_tokens": 1})
    with pytest.raises(ValueError, match="non-negative"):
        ModelInvocationService._normalize_usage({
            "prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1,
        })
    assert ModelInvocationService._normalize_usage({
        "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4,
    }) == {
        "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4,
    }


def test_sync_invocation_keeps_payload_when_usage_totals_include_thinking() -> None:
    repository = FakeInvocationRepository(request=_request(), game=_game())
    usage = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 20}
    result = _sync_invoke(
        ModelInvocationService(repository),
        lambda: ({"action_type": "kill", "target_seat": 7}, usage),
    )
    assert result.normalized_result == {"action_type": "kill", "target_seat": 7}
    assert repository.resolved[0]["status"] == "resolved"
    assert repository.finished[0]["status"] == "resolved"
    assert repository.finished[0]["usage"] == usage


@pytest.mark.asyncio
async def test_async_invocation_keeps_payload_when_usage_totals_include_thinking() -> None:
    repository = FakeInvocationRepository(request=_request(), game=_game())
    usage = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 20}
    result = await ModelInvocationService(repository).invoke(
        game_id="game", request_id="request", actor_seat=1,
        action_position="night:1", frozen_request={"prompt": "x"},
        provider_profile="test", model_id="fake", execution_generation=1,
        provider_call=lambda: ({"speak": True, "text": "刀7"}, usage),
    )
    assert result.normalized_result == {"speak": True, "text": "刀7"}
    assert repository.resolved[0]["status"] == "resolved"
    assert repository.finished[0]["status"] == "resolved"
    assert repository.finished[0]["usage"] == usage


def test_sync_invocation_resolves_payload_when_usage_is_unusable() -> None:
    repository = FakeInvocationRepository(request=_request(), game=_game())
    result = _sync_invoke(
        ModelInvocationService(repository),
        lambda: ({"text": "ok"}, "tokens"),
    )
    assert result.normalized_result == {"text": "ok"}
    assert repository.resolved[0] == {
        "status": "resolved", "normalized_result": {"text": "ok"},
    }
    assert repository.finished[0]["status"] == "resolved"
    assert repository.finished[0]["usage"] is None


@pytest.mark.asyncio
async def test_async_invocation_resolves_payload_when_usage_is_unusable() -> None:
    repository = FakeInvocationRepository(request=_request(), game=_game())
    result = await ModelInvocationService(repository).invoke(
        game_id="game", request_id="request", actor_seat=1,
        action_position="night:1", frozen_request={"prompt": "x"},
        provider_profile="test", model_id="fake", execution_generation=1,
        provider_call=lambda: ({"text": "ok"}, {"total_tokens": 1}),
    )
    assert result.normalized_result == {"text": "ok"}
    assert repository.resolved[0]["status"] == "resolved"
    assert repository.finished[0]["status"] == "resolved"
    assert repository.finished[0]["usage"] is None


@pytest.mark.asyncio
async def test_async_invocation_supports_sync_provider_and_fault_boundaries() -> None:
    faults: list[str] = []
    repository = FakeInvocationRepository(request=_request(), game=_game())
    service = ModelInvocationService(repository, fault_injector=faults.append)

    result = await service.invoke(
        game_id="game", request_id="request", actor_seat=1,
        action_position="speech:1", frozen_request={"prompt": "x"},
        provider_profile="test", model_id="fake", execution_generation=1,
        provider_call=lambda: (
            {"text": "ok"},
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        ),
    )

    assert result.normalized_result == {"text": "ok"}
    assert faults == ["request_prepared", "attempt_dispatched", "result_persisted"]
    assert repository.finished[0]["usage"]["total_tokens"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_row", "game", "recovery", "reserve", "error", "message"),
    [
        ({"request_digest": "different", "status": "prepared",
          "normalized_result": None}, _game(), False, True, CommitConflict, "conflicting"),
        (_request("resolved", None), _game(), False, True,
         ModelRequestUnavailable, "no normalized result"),
        (_request("unknown"), _game(), False, True,
         ModelRequestUnavailable, "explicit recovery"),
        (_request("in_flight"), _game(), False, True,
         ModelRequestUnavailable, "cannot be dispatched"),
        (_request(), None, False, True, KeyError, "game"),
        (_request(), _game("paused"), False, True,
         ModelRequestUnavailable, "paused"),
        (_request(), _game(generation=2), False, True,
         StaleModelResult, "stale before dispatch"),
    ],
)
async def test_async_invocation_rejects_unsafe_durable_states(
    request_row, game, recovery, reserve, error, message,
) -> None:
    repository = FakeInvocationRepository(
        request=request_row, game=game, reserve=reserve,
    )
    with pytest.raises(error, match=message):
        await ModelInvocationService(repository).invoke(
            game_id="game", request_id="request", actor_seat=1,
            action_position="speech:1", frozen_request={"prompt": "x"},
            provider_profile="test", model_id="fake", execution_generation=1,
            provider_call=lambda: ({"text": "unused"}, None), recovery=recovery,
        )


@pytest.mark.asyncio
async def test_async_invocation_marks_result_stale_when_game_disappears() -> None:
    repository = FakeInvocationRepository(
        request=_request(), game=_game(), current=None,
    )
    with pytest.raises(StaleModelResult, match="old execution"):
        await ModelInvocationService(repository).invoke(
            game_id="game", request_id="request", actor_seat=1,
            action_position="speech:1", frozen_request={"prompt": "x"},
            provider_profile="test", model_id="fake", execution_generation=1,
            provider_call=lambda: ({"text": "late"}, None),
        )
    assert repository.finished == [{
        "status": "stale", "failure_code": "stale_execution_generation",
        "elapsed_ms": 0, "usage": None,
    }]
    assert repository.resolved == [{"status": "unknown", "normalized_result": None}]


def test_sync_invocation_success_reuse_and_failure_are_durable() -> None:
    faults: list[str] = []
    success_repository = FakeInvocationRepository(request=_request(), game=_game())
    result = _sync_invoke(
        ModelInvocationService(success_repository, fault_injector=faults.append),
        lambda: ({"text": "ok"}, {
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        }),
    )
    assert result.normalized_result == {"text": "ok"}
    assert faults == ["request_prepared", "attempt_dispatched", "result_persisted"]
    assert success_repository.finished[0]["status"] == "resolved"

    reused_repository = FakeInvocationRepository(
        request=_request("consumed", {"text": "cached"}), game=_game(),
    )
    reused = _sync_invoke(ModelInvocationService(reused_repository))
    assert reused.reused is True
    assert reused.normalized_result == {"text": "cached"}

    failed_repository = FakeInvocationRepository(request=_request(), game=_game())
    with pytest.raises(RuntimeError, match="provider failed"):
        _sync_invoke(
            ModelInvocationService(failed_repository),
            lambda: (_ for _ in ()).throw(RuntimeError("provider failed")),
        )
    assert failed_repository.finished[0]["failure_code"] == "model_invocation_error"
    assert failed_repository.resolved[0]["status"] == "failed"

    recovery_repository = FakeInvocationRepository(
        request=_request("unknown"), game=_game(), reserve=True,
    )
    recovered = ModelInvocationService(recovery_repository).invoke_sync(
        game_id="game", request_id="request", actor_seat=1,
        action_position="speech:1", frozen_request={"prompt": "x"},
        provider_profile="test", model_id="fake", execution_generation=1,
        provider_call=lambda: ({"text": "recovered"}, None), recovery=True,
    )
    assert recovered.normalized_result == {"text": "recovered"}


@pytest.mark.parametrize(
    ("request_row", "game", "recovery", "reserve", "error", "message"),
    [
        (None, _game(), False, True, KeyError, "request"),
        ({"request_digest": "different", "status": "prepared",
          "normalized_result": None}, _game(), False, True, CommitConflict, "conflicting"),
        (_request("resolved", None), _game(), False, True,
         ModelRequestUnavailable, "no normalized result"),
        (_request("unknown"), _game(), False, True,
         ModelRequestUnavailable, "explicit recovery"),
        (_request("unknown"), _game(), True, False,
         ModelRequestUnavailable, "already consumed"),
        (_request("in_flight"), _game(), False, True,
         ModelRequestUnavailable, "cannot be dispatched"),
        (_request(), None, False, True, KeyError, "game"),
        (_request(), _game("paused"), False, True,
         ModelRequestUnavailable, "paused"),
        (_request(), _game(generation=2), False, True,
         StaleModelResult, "stale before dispatch"),
    ],
)
def test_sync_invocation_rejects_unsafe_durable_states(
    request_row, game, recovery, reserve, error, message,
) -> None:
    repository = FakeInvocationRepository(
        request=request_row, game=game, reserve=reserve,
    )
    service = ModelInvocationService(repository)
    with pytest.raises(error, match=message):
        service.invoke_sync(
            game_id="game", request_id="request", actor_seat=1,
            action_position="speech:1", frozen_request={"prompt": "x"},
            provider_profile="test", model_id="fake", execution_generation=1,
            provider_call=lambda: ({"text": "unused"}, None), recovery=recovery,
        )


@pytest.mark.parametrize("current", [None, _game(generation=2)])
def test_sync_invocation_rejects_results_after_generation_changes(current) -> None:
    repository = FakeInvocationRepository(
        request=_request(), game=_game(), current=current,
    )
    with pytest.raises(StaleModelResult, match="old execution"):
        _sync_invoke(ModelInvocationService(repository))
    assert repository.finished[0]["status"] == "stale"
