"""Tests for the universal llm_calls.log benchmark telemetry."""

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from app.agents.prompt_builder import PromptBuilder
from app.core.conversation_log import ConversationLog
from app.core.game_engine import VOTE_CONTRACT
from app.core.game_logger import GameLogger
from app.models.contracts import ActionRequest
from app.models.game import GamePhase, GameState, PlayerState
from app.roles.base import BaseRole
from app.roles.registry import builtin_registry
from app.services.game_service import _log_llm_telemetry


class ModelStub:
    def __init__(self, responses):
        self.responses = list(responses)

    async def ainvoke(self, messages):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ClientStub:
    """Minimal LLM client double: plain model for speech, no tool support."""

    def __init__(self, plain=(), model_name="stub-model", action_timeout_seconds=90.0):
        self.plain_model = ModelStub(plain)
        self.model_name = model_name
        self.supports_strict_actions = False
        self.supports_action_tools = False
        self.action_timeout_seconds = action_timeout_seconds
        self.action_retry_timeout_seconds = 60.0
        #: The suite must never wait on a retry: the refusal floor alone is 20s.
        self.action_retry_backoff_seconds = 0.0
        self.action_refused_backoff_seconds = 0.0

    def get_model(self):
        return self.plain_model

    def get_action_model(self):
        return self.plain_model


def tool_msg(name: str, text: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"text": text}, "id": "call_1"}],
    )


def make_state(phase=GamePhase.SPEECH, seats=2) -> GameState:
    state = GameState(game_id="telemetry-test", phase=phase, round_number=1)
    specs = builtin_registry.freeze().specs
    state.players = {
        seat: PlayerState(
            seat, "wolf-killer-villager", specs["wolf-killer-villager"].camp_id,
        )
        for seat in range(1, seats + 1)
    }
    return state


def read_llm_calls(data_dir: Path, game_id: str) -> list[dict]:
    path = data_dir / "games" / game_id / "llm_calls.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestGameLoggerLlmCall:
    def test_writes_full_record_to_dedicated_file(self, tmp_path: Path):
        logger = GameLogger(data_dir=str(tmp_path))

        logger.log_llm_call(
            "g1", 3, "vote_casting", 2,
            call_kind="action", contract_id="exile_vote",
            transport="strict_tool", attempt=2, model_id="deepseek-x",
            prompt_chars=4321, elapsed_ms=1500,
            prompt_tokens=900, completion_tokens=120, total_tokens=1020,
            retried=True, parse_result="accepted", failure_code=None,
            timeout_type=None, window_id="vote:1",
        )

        records = read_llm_calls(tmp_path, "g1")
        assert len(records) == 1
        record = records[0]
        assert record["operation"] == "llm_call"
        assert record["round"] == 3
        assert record["phase"] == "vote_casting"
        assert record["seat"] == 2
        assert record["timestamp"]
        assert record["data"] == {
            "call_kind": "action", "contract_id": "exile_vote",
            "transport": "strict_tool", "attempt": 2,
            "model_id": "deepseek-x", "prompt_chars": 4321,
            "elapsed_ms": 1500, "retried": True,
            "parse_result": "accepted",
            "prompt_tokens": 900, "completion_tokens": 120,
            "total_tokens": 1020, "window_id": "vote:1",
        }

    def test_omits_optional_fields_when_absent(self, tmp_path: Path):
        logger = GameLogger(data_dir=str(tmp_path))

        logger.log_llm_call(
            "g1", 1, "night", 3, call_kind="night",
            timeout_type="provider_timeout",
        )

        record = read_llm_calls(tmp_path, "g1")[0]
        for absent in (
            "contract_id", "transport", "model_id", "prompt_tokens",
            "completion_tokens", "total_tokens", "failure_code",
            "window_id", "parse_result",
        ):
            assert absent not in record["data"]
        assert record["data"]["call_kind"] == "night"
        assert record["data"]["attempt"] == 0
        assert record["data"]["retried"] is False
        assert record["data"]["timeout_type"] == "provider_timeout"


class TestConversationLogFacade:
    def test_forwards_to_game_logger(self, tmp_path: Path):
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")

        log.log_llm_call(2, 1, "speech", call_kind="speech", parse_result="ok")

        records = read_llm_calls(tmp_path, "g1")
        assert len(records) == 1
        assert records[0]["phase"] == "speech"
        assert records[0]["seat"] == 1

    def test_is_noop_without_persistence(self):
        log = ConversationLog()

        log.log_llm_call(2, 1, "speech", call_kind="speech", parse_result="ok")

        assert log.log_llm_call is not None


class TestActionTelemetry:
    def _request(self, contract, phase):
        return ActionRequest(
            actor_seat=1, role_id="wolf-killer-villager", contract=contract,
            phase=phase, round_id=4, idempotency_key="win:4:1",
        )

    def test_vote_contract_writes_both_telemetry_streams(self, tmp_path: Path):
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub())
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")
        request = self._request(VOTE_CONTRACT, GamePhase.VOTE_CASTING)

        role._vote_telemetry(
            log, request, transport="strict_tool", attempt=1,
            prompt_chars=800, elapsed_ms=250, retried=False,
            parse_result="accepted",
        )

        records = read_llm_calls(tmp_path, "g1")
        assert len(records) == 1
        data = records[0]["data"]
        assert data["call_kind"] == "action"
        assert data["contract_id"] == "exile_vote"
        assert data["attempt"] == 1
        assert data["model_id"] == "stub-model"
        assert data["window_id"] == "win:4:1"
        assert records[0]["phase"] == "vote_casting"

    def test_non_vote_contract_writes_universal_log_only(self, tmp_path: Path):
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub())
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")
        contract = builtin_registry.require("wolf-killer-werewolf").contracts[0]
        request = self._request(contract, GamePhase.NIGHT)

        role._vote_telemetry(
            log, request, transport="tool", attempt=1,
            prompt_chars=500, elapsed_ms=180, retried=False,
            parse_result="accepted",
        )

        assert read_llm_calls(tmp_path, "g1")[0]["data"]["contract_id"] == (
            contract.contract_id
        )

    def test_skips_universal_log_without_facade_support(self, tmp_path: Path):
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub())
        request = self._request(VOTE_CONTRACT, GamePhase.VOTE_CASTING)

        role._vote_telemetry(
            ConversationLog(), request, transport="tool", attempt=1,
            prompt_chars=500, elapsed_ms=180, retried=False,
            parse_result="accepted",
        )

        assert read_llm_calls(tmp_path, "g1") == []


class TestSpeechTelemetry:
    @pytest.mark.asyncio
    async def test_plain_speech_path_records_call(self, tmp_path: Path):
        role = BaseRole(
            1, "wolf-killer-villager", PromptBuilder(),
            ClientStub(plain=[AIMessage(content="我是普通村民，昨晚平安夜信息不多。")]),
        )
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")

        speech = await role.speak(make_state(), log, "werewolf_chat")

        assert speech
        records = read_llm_calls(tmp_path, "g1")
        assert len(records) == 1
        data = records[0]["data"]
        assert data["call_kind"] == "speech_plain"
        assert data["contract_id"] == "werewolf_chat"
        assert data["parse_result"] == "ok"
        assert data["attempt"] == 1
        assert records[0]["phase"] == "speech"

    @pytest.mark.asyncio
    async def test_plain_speech_parse_failure_is_recorded(self, tmp_path: Path):
        role = BaseRole(
            1, "wolf-killer-villager", PromptBuilder(),
            ClientStub(plain=[AIMessage(content="")]),
        )
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")

        await role.speak(make_state(), log, "werewolf_chat")

        assert read_llm_calls(tmp_path, "g1")[0]["data"]["parse_result"] == "parse_failed"

    @pytest.mark.asyncio
    async def test_fallback_speech_after_failed_attempts_is_recorded(self, tmp_path: Path):
        from app.roles.base import _SPEECH_RETRY_POLICY

        client = ClientStub()
        client.supports_action_tools = True
        client.action_retry_backoff_seconds = 0.0
        # Every attempt fails: an unclassified fault is retried, so the recorded
        # attempt count is the whole ladder rather than a single blind retry.
        client.get_model_with_tools = lambda tools: ModelStub([
            RuntimeError("provider down"),
        ] * _SPEECH_RETRY_POLICY.attempts)
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")
        state = make_state()

        speech = await role.speak(state, log, "day_speech")

        assert "我是1号" in speech
        records = read_llm_calls(tmp_path, "g1")
        assert len(records) == 1
        data = records[0]["data"]
        assert data["call_kind"] == "speech"
        assert data["attempt"] == _SPEECH_RETRY_POLICY.attempts
        assert data["retried"] is True
        assert data["parse_result"] == "fallback"

    @pytest.mark.asyncio
    async def test_a_provider_refusal_is_named_on_the_fallback_row(self, tmp_path: Path):
        """The row must say *why* the seat got a template.

        One real game produced 270 template speeches out of 275 and the log
        recorded only ``fallback`` with no failure code, so an emptied upstream
        account pool was indistinguishable from a model that refused to answer.
        """
        import httpx
        from openai import InternalServerError

        from app.roles.base import _SPEECH_RETRY_POLICY

        refusal = InternalServerError(
            "no available account",
            response=httpx.Response(
                503, request=httpx.Request("POST", "https://model.test/v1"),
            ),
            body=None,
        )
        client = ClientStub()
        client.supports_action_tools = True
        client.get_model_with_tools = lambda tools: ModelStub([refusal] * 8)
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")

        speech = await role.speak(make_state(), log, "day_speech")

        assert "我是1号" in speech
        data = read_llm_calls(tmp_path, "g1")[0]["data"]
        assert data["parse_result"] == "fallback"
        assert data["failure_code"] == "provider_unavailable"
        # A refusal is not the ordinary ladder: it gets one wait and one resend,
        # then stops, because a capacity cooldown cannot be out-waited inside a
        # turn. The ladder's full budget is reserved for faults that might clear.
        assert data["attempt"] == 2
        assert _SPEECH_RETRY_POLICY.attempts > data["attempt"]

    @pytest.mark.asyncio
    async def test_an_unusable_answer_is_named_on_the_fallback_row(self, tmp_path: Path):
        client = ClientStub()
        client.supports_action_tools = True
        # Always a tool call, never any text: a model that answers unusably.
        client.get_model_with_tools = lambda tools: ModelStub(
            [tool_msg("speak", "过")] * 8,
        )
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")

        await role.speak(make_state(), log, "day_speech")

        data = read_llm_calls(tmp_path, "g1")[0]["data"]
        assert data["parse_result"] == "fallback"
        assert data["failure_code"] == "speech_text_too_short"

    @pytest.mark.asyncio
    async def test_a_local_deadline_is_named_on_the_fallback_row(self, tmp_path: Path):
        class HangingModel:
            async def ainvoke(self, messages):
                await asyncio.sleep(10)

        client = ClientStub(action_timeout_seconds=0.02)
        client.supports_action_tools = True
        client.get_model_with_tools = lambda tools: HangingModel()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")

        await role.speak(make_state(), log, "day_speech")

        data = read_llm_calls(tmp_path, "g1")[0]["data"]
        assert data["parse_result"] == "fallback"
        assert data["failure_code"] == "request_timeout"
        assert data["timeout_type"] == "local_deadline"

    @pytest.mark.asyncio
    async def test_a_provider_timeout_is_named_on_the_fallback_row(self, tmp_path: Path):
        """The SDK's own timeout keeps the vote ladder's vocabulary."""
        import httpx
        from openai import APITimeoutError

        from app.roles.base import _SPEECH_RETRY_POLICY

        timeout = APITimeoutError(
            request=httpx.Request("POST", "https://model.test/v1"),
        )
        client = ClientStub()
        client.supports_action_tools = True
        client.get_model_with_tools = lambda tools: ModelStub(
            [timeout] * _SPEECH_RETRY_POLICY.attempts,
        )
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")

        await role.speak(make_state(), log, "day_speech")

        data = read_llm_calls(tmp_path, "g1")[0]["data"]
        assert data["parse_result"] == "fallback"
        assert data["failure_code"] == "request_timeout"
        assert data["timeout_type"] == "provider_timeout"

    @pytest.mark.asyncio
    async def test_a_successful_speech_records_no_failure_code(self, tmp_path: Path):
        client = ClientStub()
        client.supports_action_tools = True
        client.get_model_with_tools = lambda tools: ModelStub([
            tool_msg("speak", "我认为三号玩家昨晚的发言有明显矛盾。"),
        ])
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")

        await role.speak(make_state(), log, "day_speech")

        data = read_llm_calls(tmp_path, "g1")[0]["data"]
        assert data["parse_result"] == "ok"
        assert "failure_code" not in data

    @pytest.mark.asyncio
    async def test_successful_tool_speech_records_ok(self, tmp_path: Path):
        client = ClientStub()
        client.supports_action_tools = True
        client.get_model_with_tools = lambda tools: ModelStub([
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "speak",
                    "args": {"text": "我认为三号玩家昨晚的发言有明显矛盾。"},
                    "id": "call_1",
                }],
            ),
        ])
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")
        state = make_state()

        speech = await role.speak(state, log, "day_speech")

        assert speech.startswith("我认为三号")
        data = read_llm_calls(tmp_path, "g1")[0]["data"]
        assert data["attempt"] == 1
        assert data["parse_result"] == "ok"


class TestNightTelemetry:
    def test_success_record_includes_usage(self, tmp_path: Path):
        from app.agents.llm_client import LLMUsage

        logger = GameLogger(data_dir=str(tmp_path))

        _log_llm_telemetry(
            logger, "g1", 2, 3,
            tool_name="werewolf_discussion", model_id="deepseek-x",
            prompt_chars=2500, elapsed_ms=3200,
            usage=LLMUsage(1400, 210, 1610), attempts=1,
            parse_result="ok",
        )

        record = read_llm_calls(tmp_path, "g1")[0]
        data = record["data"]
        assert data["call_kind"] == "night"
        assert data["contract_id"] == "werewolf_discussion"
        assert data["prompt_tokens"] == 1400
        assert data["total_tokens"] == 1610
        assert data["retried"] is False
        assert "cached_prompt_tokens" not in data

    def test_success_record_includes_prefix_cache_hits(self, tmp_path: Path):
        from app.agents.llm_client import LLMUsage

        logger = GameLogger(data_dir=str(tmp_path))

        _log_llm_telemetry(
            logger, "g1", 2, 3,
            tool_name="werewolf_discussion", model_id="deepseek-x",
            prompt_chars=2500, elapsed_ms=3200,
            usage=LLMUsage(1400, 210, 1610, cached_prompt_tokens=1280), attempts=1,
            parse_result="ok",
        )

        assert read_llm_calls(tmp_path, "g1")[0]["data"]["cached_prompt_tokens"] == 1280

    def test_error_record_carries_failure_code(self, tmp_path: Path):
        logger = GameLogger(data_dir=str(tmp_path))

        _log_llm_telemetry(
            logger, "g1", 2, 3,
            tool_name="werewolf_kill", model_id="deepseek-x",
            prompt_chars=1800, parse_result="error",
            failure_code="TimeoutError",
        )

        data = read_llm_calls(tmp_path, "g1")[0]["data"]
        assert data["parse_result"] == "error"
        assert data["failure_code"] == "TimeoutError"
        assert "prompt_tokens" not in data

    def test_telemetry_failure_never_raises(self, tmp_path: Path, caplog):
        class BrokenLogger:
            def log_llm_call(self, *args, **kwargs):
                raise RuntimeError("disk full")

        _log_llm_telemetry(
            BrokenLogger(), "g1", 1, 1,
            tool_name="werewolf_kill", model_id="m", prompt_chars=10,
            parse_result="ok",
        )

        assert any("llm telemetry" in message for message in caplog.messages)


class TestCallMetadata:
    def test_unknown_night_tool_is_still_a_night_call(self):
        from app.services.game_service import _night_call_meta

        assert _night_call_meta(None, "werewolf_kill") == ("night", "night")
        assert _night_call_meta(
            make_state(phase=GamePhase.SHERIFF_ELECTION), "sheriff_campaign",
        ) == ("action", "sheriff_election")
        assert _night_call_meta(
            type("State", (), {"phase": "vote_resolution"})(), "sheriff_badge",
        ) == ("action", "vote_resolution")
        assert _night_call_meta(
            type("State", (), {"phase": ""})(), "test_night",
        ) == ("night", "night")

    def test_log_llm_telemetry_uses_the_supplied_phase(self, tmp_path: Path):
        logger = GameLogger(data_dir=str(tmp_path))

        _log_llm_telemetry(
            logger, "g1", 2, 3,
            tool_name="awakened_wolf_king_explode",
            model_id="m", prompt_chars=100,
            parse_result="ok", call_kind="action",
            transport="action_tool", phase="speech",
        )

        records = read_llm_calls(tmp_path, "g1")
        assert len(records) == 1
        assert records[0]["phase"] == "speech"
        assert records[0]["data"]["call_kind"] == "action"


class TestCacheRegressionTelemetry:
    def test_log_llm_call_marks_a_regressed_cached_prefix(self, tmp_path: Path):
        logger = GameLogger(data_dir=str(tmp_path))

        logger.log_llm_call(
            "g1", 1, "vote_casting", 2, call_kind="action",
            parse_result="accepted", cached_prompt_tokens=2048,
            cache_regressed=True,
        )
        logger.log_llm_call(
            "g1", 1, "vote_casting", 3, call_kind="action",
            parse_result="accepted", cached_prompt_tokens=9984,
        )

        rows = read_llm_calls(tmp_path, "g1")
        assert rows[0]["data"]["cache_regressed"] is True
        assert "cache_regressed" not in rows[1]["data"]

    def test_ladder_flags_the_attempt_that_lost_prefix(self, tmp_path: Path):
        """Retry sequences compare each attempt to the best prior attempt."""
        from app.agents.llm_client import LLMUsage

        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub())
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")
        request = ActionRequest(
            actor_seat=1, role_id="wolf-killer-villager",
            contract=VOTE_CONTRACT, phase=GamePhase.VOTE_CASTING,
            round_id=4, idempotency_key="win:4:1",
        )
        role._last_call_usage = None
        role._last_attempt_usage = None
        role._call_best_cached = None

        for attempt, cached in ((1, 9984), (2, 2048), (3, 9216), (4, 11000)):
            role._record_usage(
                LLMUsage(12000, 50, 12050, cached_prompt_tokens=cached),
            )
            role._vote_telemetry(
                log, request, transport="json", attempt=attempt,
                prompt_chars=800, elapsed_ms=100, retried=attempt > 1,
                parse_result="accepted",
            )

        rows = read_llm_calls(tmp_path, "g1")
        assert [row["data"].get("cache_regressed") for row in rows] == [
            None, True, True, None,
        ]

    def test_ladder_never_flags_when_usage_reports_no_cache(self, tmp_path: Path):
        from app.agents.llm_client import LLMUsage

        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub())
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")
        request = ActionRequest(
            actor_seat=1, role_id="wolf-killer-villager",
            contract=VOTE_CONTRACT, phase=GamePhase.VOTE_CASTING,
            round_id=4, idempotency_key="win:4:1",
        )
        role._last_call_usage = None
        role._last_attempt_usage = None
        role._call_best_cached = None

        for attempt in (1, 2):
            role._record_usage(LLMUsage(12000, 50, 12050))
            role._vote_telemetry(
                log, request, transport="json", attempt=attempt,
                prompt_chars=800, elapsed_ms=100, retried=attempt > 1,
                parse_result="accepted",
            )

        rows = read_llm_calls(tmp_path, "g1")
        assert all("cache_regressed" not in row["data"] for row in rows)

    def test_service_telemetry_compares_against_the_seat_contract_best(
        self, tmp_path: Path,
    ):
        from app.agents.llm_client import LLMUsage

        logger = GameLogger(data_dir=str(tmp_path))
        baseline: dict = {}

        def call(seat, tool, cached):
            _log_llm_telemetry(
                logger, "g1", 2, seat,
                tool_name=tool, model_id="m", prompt_chars=100,
                usage=LLMUsage(12000, 50, 12050, cached_prompt_tokens=cached),
                parse_result="ok", cache_baseline=baseline,
            )

        call(3, "werewolf_discussion", 9984)
        call(3, "werewolf_discussion", 2048)   # regressed vs the seat's best
        call(3, "werewolf_kill", 1024)         # new contract: no baseline
        call(4, "werewolf_discussion", 1408)   # other seat: its own baseline
        call(3, "werewolf_discussion", None)   # unmeasured: neither flag nor update
        call(3, "werewolf_discussion", 12000)  # new best: no flag

        rows = read_llm_calls(tmp_path, "g1")
        assert [row["data"].get("cache_regressed") for row in rows] == [
            None, True, None, None, None, None,
        ]
