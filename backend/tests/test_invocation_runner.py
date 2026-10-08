"""The shared model-call driver: ladder, ledger envelope, telemetry, capture."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

import pytest

from app.agents.invocation import (
    CALL_KEY,
    PAYLOAD_KEY,
    CallAdapter,
    CallOutcome,
    CallSpec,
    Exhausted,
    FailureBucket,
    InvocationRunner,
    RawAnswer,
    _decode,
    _encoded,
    _failure_code,
    _from_ledger,
    _joined,
    _optional_int,
    _optional_str,
    _transport_list,
    _usage_row,
    _validate,
    exhausted_bucket,
)
from app.agents.llm_client import LLMUsage
from app.agents.retry import RetryPolicy, UnusableModelOutput
from app.agents.providers.errors import classify_provider_error
from app.services.model_invocation_service import InvocationResult

#: Mirrors the call sites that would otherwise forfeit a player's turn: three
#: attempts, no real waiting in tests, and unclassified errors still resent.
POLICY = RetryPolicy(
    attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0,
    retry_unknown_errors=True,
)

#: The vote policy shape: unknown errors stop rather than resend.
STRICT_POLICY = RetryPolicy(
    attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0,
)


@dataclass(frozen=True)
class FakeContract:
    """Stand-in for ``ActionContract``: only the derived fields matter."""

    per_game_limit: int | None = None
    contract_id: str = "fake"


SPEC = CallSpec(
    game_id="game-1",
    request_id="night:1:tool:2:abcdef",
    action_position="round:1:night:tool:seat:2",
    frozen_request={"kind": "night_json", "seat": 2},
    call_kind="night",
    actor_seat=2,
    round_number=1,
    phase="night",
    contract_id="tool",
)

USAGE = LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)


def answer(value, *, transport="json", usage=None) -> RawAnswer:
    return RawAnswer(value=value, transport=transport, usage=usage)


def runner(**kwargs) -> InvocationRunner:
    kwargs.setdefault("sleeper", lambda _seconds: None)
    kwargs.setdefault("async_sleeper", _no_sleep)
    return InvocationRunner(**kwargs)


async def _no_sleep(_seconds: float) -> None:
    return None


def adapter(send, parse, encode=None, decode=None) -> CallAdapter:
    return CallAdapter(send=send, parse=parse, encode=encode, decode=decode)


def ok_parse(raw: RawAnswer) -> str:
    return str(raw.value)


def raising_parse(error: BaseException):
    def parse(_raw: RawAnswer):
        raise error

    return parse


class TestExhaustedBucket:
    def test_a_none_contract_raises_for_the_director_paths(self):
        assert exhausted_bucket(None, call_kind="night") is FailureBucket.RAISE

    def test_a_speech_degrades_to_the_template(self):
        contract = FakeContract(per_game_limit=1)
        assert (
            exhausted_bucket(contract, call_kind="speech")
            is FailureBucket.DEGRADE
        )

    def test_a_one_shot_contract_pauses_instead_of_losing_the_ability(self):
        contract = FakeContract(per_game_limit=1)
        assert (
            exhausted_bucket(contract, call_kind="night") is FailureBucket.PAUSE
        )

    def test_repeatable_contracts_degrade(self):
        assert (
            exhausted_bucket(FakeContract(per_game_limit=2), call_kind="night")
            is FailureBucket.DEGRADE
        )
        assert (
            exhausted_bucket("not-a-contract", call_kind="pipeline")
            is FailureBucket.DEGRADE
        )


class TestExhausted:
    def test_a_raising_sink_carries_no_factory(self):
        with pytest.raises(ValueError, match="raising sink"):
            Exhausted(FailureBucket.RAISE, make=lambda _code: None)

    def test_a_degrading_sink_needs_a_factory(self):
        with pytest.raises(ValueError, match="needs a factory"):
            Exhausted(FailureBucket.DEGRADE)
        with pytest.raises(ValueError, match="needs a factory"):
            Exhausted(FailureBucket.PAUSE)

    def test_the_three_factories_build_their_bucket(self):
        assert Exhausted.raise_error().bucket is FailureBucket.RAISE
        assert Exhausted.degrade(str).bucket is FailureBucket.DEGRADE
        pause = Exhausted.pause(lambda code: RuntimeError(code))
        assert pause.bucket is FailureBucket.PAUSE


class TestLadder:
    def test_a_first_attempt_success_records_usage_and_attempts(self):
        calls: list[tuple[str, object]] = []
        records = []

        def send(kind, sink):
            calls.append((kind, sink))
            return answer("ok", usage=USAGE)

        r = runner(record_call=records.append)
        outcome = r.run_sync(
            SPEC, adapter(send, ok_parse), policy=POLICY,
            on_exhausted=Exhausted.raise_error(), prompt_chars=42,
        )

        assert calls == [("transport", None)]
        assert outcome.value == "ok"
        assert outcome.degraded is False
        assert outcome.attempts == 1
        assert outcome.usage == USAGE
        assert outcome.transports == ("json",)
        assert records[0].parse_result == "ok"
        assert records[0].prompt_chars == 42
        assert records[0].transport == "json"

    def test_a_repairable_failure_is_reasked_and_recovers(self):
        kinds: list[str] = []

        def send(kind, _sink):
            kinds.append(kind)
            if len(kinds) == 1:
                raise UnusableModelOutput("no json", code="response_no_tool_call")
            return answer("ok")

        outcome = runner().run_sync(
            SPEC, adapter(send, ok_parse), policy=POLICY,
            on_exhausted=Exhausted.raise_error(),
        )

        assert kinds == ["transport", "repair"]
        assert outcome.attempts == 2
        assert outcome.degraded is False

    def test_a_verbatim_retry_is_resent_under_the_transport_kind(self):
        kinds: list[str] = []

        def send(kind, _sink):
            kinds.append(kind)
            if len(kinds) == 1:
                raise TimeoutError("slow")
            return answer("ok")

        runner().run_sync(
            SPEC, adapter(send, ok_parse), policy=POLICY,
            on_exhausted=Exhausted.raise_error(),
        )

        assert kinds == ["transport", "transport"]

    def test_an_exhausted_budget_degrades_and_marks_the_code(self):
        records = []
        failures = []
        r = runner(record_call=records.append, record_failure=failures.append)

        outcome = r.run_sync(
            SPEC,
            adapter(
                lambda _kind, _sink: answer(None, usage=USAGE),
                raising_parse(UnusableModelOutput("no json", code="response_no_tool_call")),
            ),
            policy=POLICY,
            on_exhausted=Exhausted.degrade(lambda code: f"fallback:{code}"),
        )

        assert outcome.degraded is True
        assert outcome.value == "fallback:response_no_tool_call"
        assert outcome.failure_code == "response_no_tool_call"
        assert outcome.attempts == POLICY.attempts
        assert records[0].parse_result == "degraded"
        # Usage accumulates across attempts, exactly as the old ladders did
        # through ``merge_usage``: three attempts cost three calls.
        assert records[0].usage.total_tokens == USAGE.total_tokens * POLICY.attempts
        assert failures[0].failure_code == "response_no_tool_call"
        assert failures[0].attempts == POLICY.attempts

    def test_an_exhausted_budget_reraises_the_last_error(self):
        records = []
        r = runner(record_call=records.append)
        error = UnusableModelOutput("no json", code="response_no_tool_call")

        with pytest.raises(UnusableModelOutput):
            r.run_sync(
                SPEC, adapter(lambda _kind, _sink: answer(None), raising_parse(error)),
                policy=POLICY, on_exhausted=Exhausted.raise_error(),
            )

        assert records[0].parse_result == "error"
        assert records[0].failure_code == "response_no_tool_call"

    def test_an_exhausted_budget_can_pause_with_the_failure_code(self):
        records = []
        r = runner(record_call=records.append)

        with pytest.raises(RuntimeError, match="paused:response_no_tool_call"):
            r.run_sync(
                SPEC,
                adapter(
                    lambda _kind, _sink: answer(None),
                    raising_parse(UnusableModelOutput("x", code="response_no_tool_call")),
                ),
                policy=POLICY,
                on_exhausted=Exhausted.pause(
                    lambda code: RuntimeError(f"paused:{code}")
                ),
            )

        assert records[0].parse_result == "error"

    def test_a_sync_ladder_refuses_an_async_send(self):
        class Awaitable:
            """Awaitable, but not a coroutine: nothing to leave un-awaited."""

            def __await__(self):
                return iter(())

        with pytest.raises(TypeError, match="synchronous send"):
            runner().run_sync(
                SPEC, adapter(lambda _kind, _sink: Awaitable(), ok_parse),
                policy=POLICY, on_exhausted=Exhausted.raise_error(),
            )

    def test_a_refused_gateway_gets_one_long_wait_then_stops(self):
        delays: list[float] = []
        attempts: list[int] = []
        policy = RetryPolicy(
            attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0,
            refused_delay_seconds=20.0,
        )

        class Refused(Exception):
            status_code = 503

        def send(_kind, _sink):
            attempts.append(1)
            raise Refused("no capacity")

        outcome = runner(sleeper=delays.append).run_sync(
            SPEC, adapter(send, ok_parse), policy=policy,
            on_exhausted=Exhausted.degrade(lambda code: code),
        )

        # One refused attempt, one long wait, one more attempt, then final.
        assert len(attempts) == 2
        assert delays == [20.0]
        assert outcome.degraded is True

    def test_a_zero_delay_retry_does_not_call_the_sleeper(self):
        delays: list[float] = []
        remaining = {"count": 1}

        def send(_kind, _sink):
            if remaining["count"]:
                remaining["count"] = 0
                raise TimeoutError("slow")
            return answer("ok")

        runner(sleeper=delays.append).run_sync(
            SPEC, adapter(send, ok_parse), policy=POLICY,
            on_exhausted=Exhausted.raise_error(),
        )

        assert delays == []

    def test_a_policy_that_forbids_unknown_resends_stops_immediately(self):
        attempts: list[int] = []

        def send(_kind, _sink):
            attempts.append(1)
            raise TimeoutError("slow")

        with pytest.raises(TimeoutError):
            runner().run_sync(
                SPEC, adapter(send, ok_parse), policy=STRICT_POLICY,
                on_exhausted=Exhausted.raise_error(),
            )

        assert len(attempts) == 1

    def test_no_telemetry_sinks_is_allowed(self):
        outcome = runner().run_sync(
            SPEC, adapter(lambda _kind, _sink: answer("ok"), ok_parse),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
        )
        assert outcome.value == "ok"


class TestAsyncLadder:
    def test_an_async_send_is_awaited_and_retried(self):
        delays: list[float] = []
        kinds: list[str] = []

        async def send(kind, _sink):
            kinds.append(kind)
            if len(kinds) == 1:
                raise TimeoutError("slow")
            return answer("ok", usage=USAGE)

        outcome = asyncio.run(runner(async_sleeper=delays.append).run_async(
            SPEC, adapter(send, ok_parse), policy=POLICY,
            on_exhausted=Exhausted.raise_error(),
        ))

        assert kinds == ["transport", "transport"]
        assert outcome.usage == USAGE

    def test_an_async_ladder_can_degrade(self):
        async def send(_kind, _sink):
            return answer(None)

        outcome = asyncio.run(runner().run_async(
            SPEC, adapter(send, raising_parse(TimeoutError("slow"))),
            policy=POLICY,
            on_exhausted=Exhausted.degrade(lambda code: code),
        ))

        assert outcome.degraded is True
        assert outcome.attempts == POLICY.attempts
        assert outcome.failure_code == classify_provider_error(TimeoutError("slow"))

    def test_an_async_ladder_accepts_a_synchronous_send(self):
        """A day-path sender may be plain sync; the async ladder only awaits
        what is actually awaitable."""
        outcome = asyncio.run(runner().run_async(
            SPEC, adapter(lambda _kind, _sink: answer("ok"), ok_parse),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
        ))
        assert outcome.value == "ok"
        assert outcome.transports == ("json",)

    def test_an_async_retry_waits_through_the_async_sleeper(self):
        delays: list[float] = []
        remaining = {"count": 1}

        def send(_kind, _sink):
            if remaining["count"]:
                remaining["count"] = 0
                raise UnusableModelOutput("x", code="response_no_tool_call")
            return answer("ok")

        async def collect(seconds: float) -> None:
            delays.append(seconds)

        policy = RetryPolicy(
            attempts=3, base_delay_seconds=1.0, max_delay_seconds=1.0,
        )
        outcome = asyncio.run(runner(async_sleeper=collect).run_async(
            SPEC, adapter(send, ok_parse), policy=policy,
            on_exhausted=Exhausted.raise_error(),
        ))

        assert outcome.value == "ok"
        assert len(delays) == 1
        assert delays[0] > 0

    def test_an_async_ladder_can_pause(self):
        async def send(_kind, _sink):
            return answer(None)

        with pytest.raises(RuntimeError):
            asyncio.run(runner().run_async(
                SPEC, adapter(send, raising_parse(TimeoutError("slow"))),
                policy=POLICY,
                on_exhausted=Exhausted.pause(lambda code: RuntimeError(code)),
            ))


class TestLedgerIntegration:
    @pytest.fixture()
    def ledger(self):
        return FakeLedger()

    def test_a_fresh_row_returns_the_live_outcome(self, ledger):
        r = runner(ledger=ledger)
        outcome = r.run_sync(
            SPEC,
            adapter(
                lambda _kind, _sink: answer("ok", usage=USAGE),
                ok_parse,
                encode=lambda value, result: {"text": value},
                decode=lambda row: row["text"],
            ),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
            model_id="model-x", provider_profile="custom-openai",
        )

        assert outcome.value == "ok"
        assert outcome.usage == USAGE
        assert ledger.calls[0]["request_id"] == SPEC.request_id
        assert ledger.calls[0]["frozen_request"] is SPEC.frozen_request
        assert ledger.calls[0]["usage"] == {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        }
        row = ledger.calls[0]["encoded"]
        assert row[PAYLOAD_KEY] == {"text": "ok"}
        assert row[CALL_KEY]["status"] == "succeeded"

    def test_a_degraded_row_is_marked_for_the_report(self, ledger):
        r = runner(ledger=ledger)
        r.run_sync(
            SPEC,
            adapter(
                lambda _kind, _sink: answer(None),
                raising_parse(UnusableModelOutput("x", code="response_no_tool_call")),
                encode=lambda value, result: {"value": value},
                decode=lambda row: row["value"],
            ),
            policy=POLICY,
            on_exhausted=Exhausted.degrade(lambda code: f"fallback:{code}"),
        )

        row = ledger.calls[0]["encoded"]
        assert row[CALL_KEY] == {
            "status": "degraded",
            "failure_code": "response_no_tool_call",
            "attempts": POLICY.attempts,
            # One entry per attempt: a three-attempt ladder tried three times.
            "transports": ["json"] * POLICY.attempts,
        }
        assert ledger.calls[0]["usage"] is None

    def test_a_reused_row_is_rebuilt_from_the_ledger(self, ledger):
        ledger.reused = True
        ledger.row = {
            PAYLOAD_KEY: {"value": "cached"},
            CALL_KEY: {
                "status": "degraded",
                "failure_code": "response_missing_fields",
                "attempts": 3,
                "transports": ["tool", "json"],
            },
        }
        outcome = runner(ledger=ledger).run_sync(
            SPEC,
            adapter(
                _never_send, ok_parse,
                encode=lambda value, result: {"value": value},
                decode=lambda row: row["value"],
            ),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
        )

        assert outcome.value == "cached"
        assert outcome.degraded is True
        assert outcome.failure_code == "response_missing_fields"
        assert outcome.attempts == 3
        assert outcome.transports == ("tool", "json")
        # A cache hit spends no tokens, so it must not report a usage figure.
        assert outcome.usage is None

    def test_a_legacy_row_without_the_envelope_still_decodes(self, ledger):
        ledger.reused = True
        ledger.row = {"command": {"action_type": "pass"}}
        outcome = runner(ledger=ledger).run_sync(
            SPEC,
            adapter(
                _never_send, ok_parse,
                encode=lambda value, result: {"value": value},
                decode=lambda row: row["command"]["action_type"],
            ),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
        )

        assert outcome.value == "pass"
        assert outcome.degraded is False
        assert outcome.attempts == 1
        assert outcome.transports == ()

    def test_an_async_ledger_is_used_for_async_runs(self, ledger):
        async def send(_kind, _sink):
            return answer("ok")

        outcome = asyncio.run(runner(ledger=ledger).run_async(
            SPEC,
            adapter(
                send, ok_parse,
                encode=lambda value, result: {"text": value},
                decode=lambda row: row["text"],
            ),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
        ))

        assert outcome.value == "ok"
        assert ledger.async_calls == 1


class TestValidation:
    def test_an_unknown_call_kind_is_refused(self):
        bad = CallSpec(**{**SPEC.__dict__, "call_kind": "telepathy"})
        with pytest.raises(ValueError, match="unknown call kind"):
            runner().run_sync(
                bad, adapter(_never_send, ok_parse), policy=POLICY,
                on_exhausted=Exhausted.raise_error(),
            )

    def test_a_spec_without_a_game_id_is_refused(self):
        bad = CallSpec(**{**SPEC.__dict__, "game_id": ""})
        with pytest.raises(ValueError, match="game id"):
            runner().run_sync(
                bad, adapter(_never_send, ok_parse), policy=POLICY,
                on_exhausted=Exhausted.raise_error(),
            )

    def test_half_a_codec_is_refused(self):
        half = adapter(_never_send, ok_parse, encode=lambda value, result: {})
        with pytest.raises(ValueError, match="together"):
            runner().run_sync(
                SPEC, half, policy=POLICY, on_exhausted=Exhausted.raise_error(),
            )

    def test_a_wired_ledger_demands_both_codec_halves(self):
        with pytest.raises(ValueError, match="encode and decode"):
            runner(ledger=FakeLedger()).run_sync(
                SPEC, adapter(_never_send, ok_parse), policy=POLICY,
                on_exhausted=Exhausted.raise_error(),
            )

    def test_capture_without_a_sink_factory_is_refused(self):
        with pytest.raises(ValueError, match="sink_factory"):
            InvocationRunner(capture=lambda *args: None)

    def test_validate_reads_the_ledger_flag(self):
        good = adapter(
            _never_send, ok_parse,
            encode=lambda value, result: {}, decode=lambda row: None,
        )
        _validate(SPEC, good, ledger=True)
        _validate(SPEC, adapter(_never_send, ok_parse), ledger=False)


class TestCapture:
    def test_a_recorded_sink_is_flushed_once(self):
        flushed = []

        class Sink:
            empty = False

        r = runner(capture=lambda *args: flushed.append(args), sink_factory=Sink)
        outcome = r.run_sync(
            SPEC, adapter(lambda _kind, _sink: answer("ok"), ok_parse),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
        )

        assert outcome.value == "ok"
        sink, spec, attempts, transport = flushed[0]
        assert isinstance(sink, Sink)
        assert spec is SPEC
        assert attempts == 1
        assert transport == "json"

    def test_an_empty_sink_is_not_flushed(self):
        flushed = []

        class Sink:
            empty = True

        runner(capture=lambda *args: flushed.append(args), sink_factory=Sink).run_sync(
            SPEC, adapter(lambda _kind, _sink: answer("ok"), ok_parse),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
        )

        assert flushed == []

    def test_a_failed_ladder_still_flushes_what_was_sent(self):
        flushed = []

        class Sink:
            empty = False

        r = runner(capture=lambda *args: flushed.append(args), sink_factory=Sink)
        with pytest.raises(UnusableModelOutput):
            r.run_sync(
                SPEC,
                adapter(
                    lambda _kind, _sink: answer(None),
                    raising_parse(
                        UnusableModelOutput("x", code="response_no_tool_call")
                    ),
                ),
                policy=POLICY, on_exhausted=Exhausted.raise_error(),
            )

        assert len(flushed) == 1
        assert flushed[0][2] == POLICY.attempts  # attempts
        assert flushed[0][3] is None  # transport: never produced an outcome


class TestLedgerPlumbing:
    def test_a_ledger_call_forwards_the_identity_unchanged(self):
        ledger = FakeLedger()
        runner(ledger=ledger).run_sync(
            SPEC,
            adapter(
                lambda _kind, _sink: answer("ok"), ok_parse,
                encode=lambda value, result: {"text": value},
                decode=lambda row: row["text"],
            ),
            policy=POLICY, on_exhausted=Exhausted.raise_error(),
        )
        call = ledger.calls[0]
        assert call["game_id"] == "game-1"
        assert call["request_id"] == SPEC.request_id
        assert call["action_position"] == SPEC.action_position
        assert call["actor_seat"] == 2
        # Missing identity falls back to an explicit placeholder, never None:
        # the ledger columns are NOT NULL.
        assert call["model_id"] == "unknown"
        assert call["provider_profile"] == "unknown"
        assert call["execution_generation"] == 1
        assert call["recovery"] is False
        assert isinstance(call["encoded"], dict)

    def test_a_cached_row_missing_the_call_block_falls_back_to_defaults(self):
        result = InvocationResult(
            normalized_result={PAYLOAD_KEY: {"value": 1}}, reused=True,
            attempt_id=None,
        )
        outcome = _from_ledger(
            adapter(_never_send, ok_parse, encode=lambda value, result: {},
                    decode=lambda row: row["value"]),
            result, {},
        )
        assert outcome.value == 1
        assert outcome.degraded is False
        assert outcome.failure_code is None
        assert outcome.attempts == 1


class TestPureHelpers:
    def test_the_envelope_keeps_payload_and_metadata_apart(self):
        outcome = CallOutcome(
            value="v", degraded=False, failure_code=None, timeout_type=None,
            attempts=1, transports=("json",), usage=None, elapsed_ms=3,
        )
        row = _encoded(
            adapter(_never_send, ok_parse, encode=lambda value, result: {"a": value}),
            outcome,
        )
        assert row[PAYLOAD_KEY] == {"a": "v"}
        assert row[CALL_KEY]["transports"] == ["json"]

    def test_decode_tolerates_a_row_without_the_envelope(self):
        seen = []

        def decode(row):
            seen.append(row)
            return row

        a = adapter(_never_send, ok_parse, encode=lambda value, result: {}, decode=decode)
        assert _decode(a, {PAYLOAD_KEY: {"x": 1}}) == {"x": 1}
        assert _decode(a, {"legacy": True}) == {"legacy": True}

    def test_usage_is_only_reported_when_the_provider_sent_it(self):
        assert _usage_row(None) is None
        assert _usage_row(USAGE) == {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        }

    def test_the_failure_code_prefers_the_stable_token(self):
        assert _failure_code(UnusableModelOutput("x", code="response_no_tool_call")) == (
            "response_no_tool_call"
        )
        assert _failure_code(ValueError("x")) == classify_provider_error(ValueError("x"))

    def test_transport_lists_accept_only_strings(self):
        assert _transport_list(("tool", 7, "json")) == ["tool", "json"]
        assert _transport_list("json") == []
        assert _joined(("tool", "json")) == "tool+json"
        assert _joined(()) is None

    def test_optional_scalars_reject_the_wrong_type(self):
        assert _optional_str("a") == "a"
        assert _optional_str(3) is None
        assert _optional_int(2) == 2
        assert _optional_int(0) is None
        assert _optional_int("2") is None


def _never_send(_kind, _sink):  # pragma: no cover - reached only on a bug
    raise AssertionError("the ladder must not send here")


class FakeLedger:
    """The narrow slice of ``ModelInvocationService`` the driver uses."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.async_calls = 0
        self.reused = False
        self.row: dict = {}

    def _store(self, kwargs: dict, produced) -> InvocationResult:
        normalized, usage = produced
        self.calls.append({**kwargs, "encoded": normalized, "usage": usage})
        return InvocationResult(normalized, False, "attempt-1")

    def invoke_sync(self, **kwargs) -> InvocationResult:
        if self.reused:
            return InvocationResult(self.row, True, None)
        return self._store(kwargs, kwargs["provider_call"]())

    async def invoke(self, **kwargs) -> InvocationResult:
        self.async_calls += 1
        if self.reused:
            return InvocationResult(self.row, True, None)
        produced = kwargs["provider_call"]()
        if inspect.isawaitable(produced):  # the real service awaits it too
            produced = await produced
        return self._store(kwargs, produced)
