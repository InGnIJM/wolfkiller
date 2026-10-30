"""GameService wiring for the provider-health guard."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.model_health import AbortPolicy
from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.services.game_service import GameService


def _service(tmp_path, **policy) -> GameService:
    return GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path),
        abort_policy=AbortPolicy(**policy),
    )


def _clients() -> dict[int, object]:
    return {
        1: SimpleNamespace(provider_key="profile|https://model.test|m"),
        2: SimpleNamespace(provider_key="profile|https://model.test|m"),
    }


@pytest.mark.asyncio
async def test_a_live_game_pauses(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        guard = service._make_health_guard("g", _clients(), benchmark=False)
        assert guard.mode == "pause"
        assert guard.key_for(1) == "profile|https://model.test|m"
        assert service._health_guards["g"] is guard
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_a_benchmark_game_fails_fast(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        guard = service._make_health_guard("g", _clients(), benchmark=True)
        assert guard.mode == "fail"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_warn_mode_is_preserved(tmp_path) -> None:
    service = _service(tmp_path, mode="warn")
    try:
        guard = service._make_health_guard("g", _clients(), benchmark=False)
        assert guard.mode == "warn"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_unknown_clients_get_a_stable_fallback_key(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        guard = service._make_health_guard(
            "g", {1: SimpleNamespace()}, benchmark=False,
        )
        assert guard.key_for(1) == "unknown|seat:1"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_the_health_sink_writes_an_audit_row(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        records: list[tuple[str, str, dict]] = []

        class _Logger:
            def log_operation(self, game_id, operation, round_num, phase, data=None, seat=None):
                records.append((operation, phase, data or {}))

        engine = SimpleNamespace(
            state=SimpleNamespace(phase=SimpleNamespace(value="night"), round_number=2),
            game_logger=_Logger(),
        )
        guard = service._make_health_guard("g", _clients(), benchmark=False)
        service._attach_health_sink("g", guard, engine)
        guard.record_telemetry({
            "seat": 1,
            "data": {"parse_result": "error", "failure_code": "provider_auth_error",
                     "contract_id": "night_action"},
        })
        guard.record_telemetry({
            "seat": 2,
            "data": {"parse_result": "error", "failure_code": "provider_auth_error",
                     "contract_id": "night_action"},
        })
        assert records and records[0][0] == "provider_health"
        assert records[0][1] == "night"
        assert records[0][2]["rule"] == "provider_terminal"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_pause_reason_labels(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        assert service._pause_reason(None) is None
        assert service._pause_reason("provider_circuit_open") == "模型通道连续失败，已自动暂停"
        assert service._pause_reason("one_shot_budget_exhausted") == "一次性技能模型预算耗尽，已暂停"
        assert service._pause_reason("manual") == "手动暂停"
        assert "custom" in service._pause_reason("custom")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_the_health_audit_failure_is_swallowed(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        class _Boom:
            def log_operation(self, *_args, **_kwargs):
                raise RuntimeError("disk full")

        engine = SimpleNamespace(
            state=SimpleNamespace(phase="night", round_number=1),
            game_logger=_Boom(),
        )
        guard = service._make_health_guard("g", _clients(), benchmark=False)
        service._attach_health_sink("g", guard, engine)
        guard.record_telemetry({
            "seat": 1,
            "data": {"parse_result": "error", "failure_code": "provider_auth_error"},
        })
        guard.record_telemetry({
            "seat": 2,
            "data": {"parse_result": "error", "failure_code": "provider_auth_error"},
        })
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_broadcast_failures_are_swallowed(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        def boom(*_args, **_kwargs):
            raise RuntimeError("socket closed")

        service.ws_manager.broadcast = boom  # type: ignore[method-assign]
        service._broadcast_pause("g", "provider_circuit_open", {})
        service._broadcast_resume("g")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_the_watcher_tolerates_a_provider_failure_without_repository(tmp_path) -> None:
    from app.agents.model_health import HealthEvent
    from app.core.model_health_guard import ProviderOutageFailed

    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    event = HealthEvent(
        provider_key="k", rule="provider_sustained", failure_code="provider_server_error",
        seats=(1, 2), contracts=("c",), sample_count=5, reopen_count=0,
    )

    async def crash() -> None:
        raise ProviderOutageFailed(event)

    task = __import__("asyncio").get_running_loop().create_task(crash())
    service._attach_task_watcher("g", SimpleNamespace(game_logger=None), task)
    try:
        with pytest.raises(ProviderOutageFailed):
            await task
        await __import__("asyncio").sleep(0)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_the_watcher_tolerates_an_already_finalized_provider_failure(
    tmp_path, monkeypatch,
) -> None:
    from app.agents.model_health import HealthEvent
    from app.core.model_health_guard import ProviderOutageFailed
    from app.persistence.repository import GameRepository, InvalidExecutionTransition

    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="g", name="g", config={}, execution_status="running",
        source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    monkeypatch.setattr(
        repository, "transition_execution",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            InvalidExecutionTransition("already finalized")
        ),
    )
    event = HealthEvent(
        provider_key="k", rule="provider_sustained", failure_code="provider_server_error",
        seats=(1, 2), contracts=("c",), sample_count=5, reopen_count=0,
    )

    async def crash() -> None:
        raise ProviderOutageFailed(event)

    task = __import__("asyncio").get_running_loop().create_task(crash())
    service._attach_task_watcher("g", SimpleNamespace(game_logger=None), task)
    try:
        with pytest.raises(ProviderOutageFailed):
            await task
        await __import__("asyncio").sleep(0)
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_a_client_build_failure_is_reported_to_the_guard(tmp_path) -> None:
    from app.models.pipeline import ActionContract, SchedulePoint

    service = _service(tmp_path)
    try:
        reported: list[tuple[int, BaseException]] = []

        class _Guard:
            def check(self, _seat: int) -> None:
                return None

            def record_client_failure(self, seat: int, error: BaseException) -> None:
                reported.append((seat, error))

        contract = ActionContract(
            contract_id="test_action", schedule_point=SchedulePoint.NIGHT_ACTION,
            order=0, action_types=("pass",),
            actions_requiring_target=frozenset(),
            fallback_action_type="pass",
            visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        )
        request = SimpleNamespace(
            contract=contract, actor_seat=1, action_key="k", role_id="x",
            round_number=1, phase=SimpleNamespace(value="night"), window_id="w",
            context_revision=0,
        )
        context = SimpleNamespace(
            game_id="g", round_number=1, phase=SimpleNamespace(value="night"),
            facts={"alive_seats": ()}, revision=0,
        )
        snapshot = SimpleNamespace(
            acting_spec=lambda *_args, **_kwargs: SimpleNamespace(camp_id="good"),
        )
        renderer = SimpleNamespace(render=lambda *_args, **_kwargs: "prompt")
        provider = service._command_provider(
            snapshot, renderer, lambda _seat: (_ for _ in ()).throw(ValueError("bad key")),
            SimpleNamespace(),
        )
        service._health_guards["g"] = _Guard()

        command = provider(request, context, 0)

        assert command.action_type == "pass"
        assert command.degraded is True
        assert reported and isinstance(reported[0][1], ValueError)
    finally:
        await service.aclose()
