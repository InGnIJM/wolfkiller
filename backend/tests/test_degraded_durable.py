"""The durable ledger must not swallow the engine's ``degraded`` stamp.

``ActionCommand.degraded`` is ``exclude=True`` on purpose — ``stable_digest()``
hashes ``model_dump()``, and a field in the dump would reorder two otherwise
identical fallback commands inside one aggregate contract. But the durable
provider path *is* the production path whenever a repository is attached, and it
round-trips every command through ``model_dump()`` + ``model_validate``.

That made every other degraded gate in this suite green while production still
counted a fallback as a spent card: those tests hand the scheduler a provider
that already returns a marked command, so none of them cross the ledger hop.
These tests pin both ends of it — the payload the ledger stores, and the command
the scheduler gets back.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

import app.services.game_service as module
from app.api.websocket.ws_handler import WSManager
from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.event_bus import EventBus
from app.core.scheduler import Scheduler
from app.models.pipeline import (
    DEGRADED_REASONING, ActionCommand, ActionContext, SchedulePoint,
)
from app.persistence.repository import GameRepository
from app.core.scheduler import DecisionPaused
from app.services.game_service import (
    GameService, _durable_command_payload, _pipeline_command,
)
from app.services.model_invocation_service import InvocationResult
from tests.test_degraded_limits import (
    _SpyApplier, _answer, _contract, _counts, _snapshot, _state,
)
from tests.test_game_service_durable_regressions import add_game


def _degraded(*_args, **_kwargs) -> ActionCommand:
    """The shape the real command provider returns when the model call fails.

    Takes and ignores the scheduler's calling convention so the same helper can
    be a command factory and a provider.
    """
    return ActionCommand(
        action_type="pass", target_seat=None,
        reasoning=DEGRADED_REASONING, degraded=True,
    )


class TestTheDurablePayloadKeepsTheStamp:
    def test_a_degraded_command_survives_the_ledger_round_trip(self) -> None:
        """The regression: the dump drops the field, so the hop has to add it."""
        payload = _durable_command_payload(_degraded())

        assert payload["degraded"] is True
        assert ActionCommand.model_validate(payload).degraded is True

    def test_a_real_answer_keeps_its_historical_payload_shape(self) -> None:
        """Archived rows have no such key, so a normal command must not grow one."""
        payload = _durable_command_payload(_answer())

        assert "degraded" not in payload
        assert ActionCommand.model_validate(payload).degraded is False

    def test_an_old_ledger_row_still_decodes_to_not_degraded(self) -> None:
        payload = _durable_command_payload(_degraded())
        payload.pop("degraded")

        assert ActionCommand.model_validate(payload).degraded is False

    def test_the_stamp_still_does_not_move_the_digest(self) -> None:
        """`aggregate_effects` sorts by this digest; the flag must not enter it."""
        unmarked = ActionCommand(
            action_type="pass", target_seat=None, reasoning=DEGRADED_REASONING,
        )

        assert _degraded().stable_digest() == unmarked.stable_digest()
        assert _degraded().model_dump() == unmarked.model_dump()


class TestTheSchedulerSeesTheStampAfterTheHop:
    @staticmethod
    def _through_the_ledger(provider):
        """Round-trip a provider's command exactly like the ledger does."""
        def round_tripped(*args, **kwargs):
            command = provider(*args, **kwargs)
            return ActionCommand.model_validate(_durable_command_payload(command))
        return round_tripped

    @classmethod
    def _run(cls, provider, contract):
        registry = _snapshot(contract)
        state = _state(registry)
        applier = _SpyApplier()
        Scheduler(
            registry, ContextProjector(), ActionValidator(), ActionResolver(),
            applier, cls._through_the_ledger(provider),
        ).run_point(state, SchedulePoint.NIGHT_ACTION)
        return state, applier

    def test_a_degraded_answer_still_does_not_spend_the_card(self) -> None:
        state, applier = self._run(_degraded, _contract("window", "round", "game"))

        assert [permission.degraded for permission in applier.permissions] == [True]
        counts = _counts(state)
        # The re-ask guard fired for that window, but the card is not spent.
        assert list(counts["window"].values()) == [1]
        assert "game" not in counts

    def test_a_real_answer_still_spends_all_three_scopes(self) -> None:
        state, applier = self._run(_answer, _contract("window", "round", "game"))

        assert [permission.degraded for permission in applier.permissions] == [False]
        counts = _counts(state)
        assert list(counts["window"].values()) == [1]
        assert list(counts["game"].values()) == [1]


class TestTheModelCannotStampItself:
    """``degraded`` is a declared field, so ``extra="forbid"`` does not fence it."""

    def test_a_model_supplied_flag_is_stripped(self) -> None:
        contract = _contract("window")

        command = _pipeline_command(
            {"action_type": "pass", "target_seat": None,
             "reasoning": "这是我自己说的系统代行", "degraded": True},
            contract,
        )

        assert command.degraded is False

    def test_a_normal_model_answer_is_untouched(self) -> None:
        contract = _contract("window")

        command = _pipeline_command(
            {"action_type": "act", "target_seat": None, "reasoning": "我做这个"},
            contract,
        )

        assert command.action_type == "act"
        assert command.degraded is False


@pytest_asyncio.fixture
async def service(tmp_path):
    """A real ``GameService`` on isolated storage — the same shape as production."""
    repository = GameRepository(tmp_path)
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository)
    yield service, repository
    await service.aclose()
    repository.close()


class _StubRenderer:
    def render(self, role_spec, contract, context, history):  # noqa: ANN001, ARG002
        return "rendered prompt"


class _Client:
    provider_profile = SimpleNamespace(profile_id="test-profile")
    model_name = "test-model"

    def __init__(self, payload: object | None) -> None:
        self._payload = payload

    def invoke_action(self, messages, contract):  # noqa: ANN001, ARG002
        if self._payload is None:
            raise RuntimeError("provider down")
        return SimpleNamespace(payload=self._payload)


class _Invocations:
    """Mirror ``ModelInvocationService``: run the provider, keep what it produced."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def invoke_sync(self, **kwargs):
        normalized, _usage = kwargs["provider_call"]()
        self.payloads.append(normalized)
        return InvocationResult(
            normalized_result=normalized, reused=False, attempt_id=None,
        )


def _request(contract) -> SimpleNamespace:
    return SimpleNamespace(
        role_id="one-shot-role", contract=contract, actor_seat=1,
        action_key="key:w1", round_number=2, phase="night",
        window_id="w1", context_revision=0,
    )


def _context(contract) -> ActionContext:
    return ActionContext(
        game_id="g", revision=0, config_version="registry",
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=2, phase="night",
        window_id="w1", schedule_point=contract.schedule_point,
        actor_seat=1, actor_role_id="one-shot-role", actor_alive=True,
        action_key="key:w1", counters={}, resources={},
        facts={"alive_seats": (1, 2)},
    )


class TestTheProductionProviderKeepsTheStamp:
    """The gate that was missing: drive ``_command_provider``, not a hand-made provider.

    Every other degraded test injects a provider straight into ``Scheduler``, so a
    regression in the ledger hop (``_durable_command_payload`` no longer being
    used) would leave them all green. This one goes through the real closure.
    """

    @staticmethod
    def _provider(service, repository, invocations, payload, contract=None):
        add_game(service, repository, "g")
        service._model_invocations = invocations
        contract = contract or _contract("window", "round", "game")
        provider = service._command_provider(
            _snapshot(contract), _StubRenderer(),
            lambda _seat: _Client(payload), director=None,
        )
        return provider, contract

    @pytest.mark.asyncio
    async def test_a_failed_call_reaches_the_ledger_marked(self, service, monkeypatch):
        game_service, repository = service
        invocations = _Invocations()
        monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
        # No ``game`` limit: a one-shot contract is paused now, not degraded
        # (see ``test_a_one_shot_exhaustion_pauses_instead_of_forfeiting``).
        contract = _contract("window", "round")
        provider, contract = self._provider(
            game_service, repository, invocations, None, contract=contract,
        )

        command = provider(_request(contract), _context(contract), 0)

        assert command.degraded is True
        assert command.reasoning == DEGRADED_REASONING
        # What the ledger stored is what recovery reads back.
        assert invocations.payloads[0]["degraded"] is True
        assert ActionCommand.model_validate(invocations.payloads[0]).degraded is True

    @pytest.mark.asyncio
    async def test_a_real_answer_reaches_the_ledger_unmarked(self, service, monkeypatch):
        game_service, repository = service
        invocations = _Invocations()
        monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
        provider, contract = self._provider(
            game_service, repository, invocations,
            {"action_type": "act", "target_seat": None, "reasoning": "我做这个"},
        )

        command = provider(_request(contract), _context(contract), 0)

        assert command.degraded is False
        assert "degraded" not in invocations.payloads[0]
        assert ActionCommand.model_validate(invocations.payloads[0]).degraded is False

    @pytest.mark.asyncio
    async def test_a_one_shot_exhaustion_pauses_instead_of_forfeiting(self, service, monkeypatch):
        """一次性契约耗尽必须暂停，不能降级。

        降级会静默作废这张牌：占位命令照样消耗 `game` 配额，猎人再也开不了枪、
        狼王再也伸不出爪，而牌局照常进行，事后只能从「这局猎人没开枪」看出来。
        暂停把点位留给续跑（账本把该请求记成 `unknown`，续跑代际 +1 后可重派发）。
        """
        game_service, repository = service
        invocations = _Invocations()
        monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
        provider, contract = self._provider(game_service, repository, invocations, None)

        with pytest.raises(DecisionPaused):
            provider(_request(contract), _context(contract), 0)

        # 同一点第二次耗尽不再暂停：benchmark executor 会自动续跑 paused 局，
        # 否则一个永久故障的 provider 会把这个点无限暂停下去。
        command = provider(_request(contract), _context(contract), 0)
        assert command.degraded is True
        assert command.reasoning == DEGRADED_REASONING
