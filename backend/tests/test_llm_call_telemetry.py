"""Tests for the universal llm_calls.log benchmark telemetry."""

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
from app.services.game_service import _log_night_llm_telemetry


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

    def __init__(self, plain=(), model_name="stub-model"):
        self.plain_model = ModelStub(plain)
        self.model_name = model_name
        self.supports_strict_actions = False
        self.supports_action_tools = False
        self.action_timeout_seconds = 90.0
        self.action_retry_timeout_seconds = 60.0

    def get_model(self):
        return self.plain_model

    def get_action_model(self):
        return self.plain_model


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
        client = ClientStub()
        client.supports_action_tools = True
        client.get_model_with_tools = lambda tools: ModelStub([
            RuntimeError("provider down"), RuntimeError("provider down"),
        ])
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        log = ConversationLog(logger=GameLogger(data_dir=str(tmp_path)), game_id="g1")
        state = make_state()

        speech = await role.speak(state, log, "day_speech")

        assert "我是1号" in speech
        records = read_llm_calls(tmp_path, "g1")
        assert len(records) == 1
        data = records[0]["data"]
        assert data["call_kind"] == "speech"
        assert data["attempt"] == 2
        assert data["retried"] is True
        assert data["parse_result"] == "fallback"

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

        _log_night_llm_telemetry(
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

    def test_error_record_carries_failure_code(self, tmp_path: Path):
        logger = GameLogger(data_dir=str(tmp_path))

        _log_night_llm_telemetry(
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

        _log_night_llm_telemetry(
            BrokenLogger(), "g1", 1, 1,
            tool_name="werewolf_kill", model_id="m", prompt_chars=10,
            parse_result="ok",
        )

        assert any("wolf llm telemetry" in message for message in caplog.messages)
