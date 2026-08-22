import asyncio

import httpx
import pytest
from langchain_core.messages import AIMessage
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from app.agents.prompt_builder import PromptBuilder
from app.core.conversation_log import ConversationLog
from app.core.game_engine import VOTE_CONTRACT
from app.models.contracts import ActionRequest
from app.models.game import GamePhase, GameState, PlayerState
from app.roles.base import BaseRole
from app.roles.registry import builtin_registry


class RecordingModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class JsonClient:
    supports_strict_actions = False
    action_timeout_seconds = 0.1
    action_retry_timeout_seconds = 0.1

    def __init__(self, responses):
        self.model = RecordingModel(responses)

    def get_action_model(self):
        return self.model


class TelemetryLogger:
    def __init__(self):
        self.records = []
        self.technical_abstentions = []

    def log_vote_telemetry(self, game_id, round_num, seat, **data):
        self.records.append(data)

    def log_vote_technical_abstain(self, game_id, round_num, seat, **data):
        self.technical_abstentions.append(data)

    def log_conversation(self, game_id, record):
        pass


def make_state() -> GameState:
    state = GameState(game_id="g", phase=GamePhase.VOTE_CASTING, round_number=1)
    camp = builtin_registry.freeze().specs["wolf-killer-villager"].camp_id
    state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", camp)
        for seat in (1, 2)
    }
    return state


def make_request(state: GameState) -> ActionRequest:
    return ActionRequest(
        actor_seat=1,
        role_id="wolf-killer-villager",
        contract=VOTE_CONTRACT,
        phase=GamePhase.VOTE_CASTING,
        round_id=1,
        idempotency_key="1:vote_casting:1:1:exile_vote",
    )


def make_log(logger=None) -> ConversationLog:
    log = ConversationLog(logger=logger, game_id="g")
    for index in range(20):
        log.add_public_speech(
            2,
            "wolf-killer-villager",
            f"old-history-{index}-" + "x" * 300,
            0,
            "speech",
        )
    log.add_public_speech(
        2, "wolf-killer-villager", "current-round-evidence", 1, "speech",
    )
    return log


@pytest.mark.asyncio
async def test_invalid_vote_retry_uses_existing_compact_prompt():
    client = JsonClient([
        AIMessage(content="not json"),
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(), make_request(state))

    first_chars = sum(len(message.content) for message in client.model.messages[0])
    retry_chars = sum(len(message.content) for message in client.model.messages[1])
    assert accepted.command.target_seat == 2
    assert "current-round-evidence" in client.model.messages[1][1].content
    assert "old-history-0" not in client.model.messages[1][1].content
    assert "previous action was invalid" in client.model.messages[1][-1].content
    assert retry_chars < first_chars * 0.75


@pytest.mark.asyncio
async def test_invalid_vote_logs_stable_non_secret_failure_code():
    client = JsonClient([
        AIMessage(content="not json"),
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    await role.request_action(state, make_log(logger), make_request(state))

    assert logger.records[0]["parse_result"] == "invalid"
    assert logger.records[0]["failure_code"] == "action_payload_not_json"
    assert "response" not in logger.records[0]
    assert "reasoning" not in logger.records[0]


@pytest.mark.asyncio
async def test_repeated_invalid_vote_logs_technical_abstention():
    client = JsonClient([AIMessage(content="not json"), AIMessage(content="still invalid")])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(logger), make_request(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.technical_failure_code == "action_payload_not_json"
    assert logger.technical_abstentions == [
        {
            "failure_code": "action_payload_not_json",
            "window_id": "1:vote_casting:1:1:exile_vote",
        },
    ]


@pytest.mark.asyncio
async def test_local_deadline_is_distinguished_in_vote_telemetry():
    class SlowModel:
        async def ainvoke(self, messages):
            await asyncio.sleep(0.05)

    client = JsonClient([])
    client.model = SlowModel()
    client.action_timeout_seconds = 0.001
    client.action_retry_timeout_seconds = 0.001
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(logger), make_request(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.technical_failure_code == "request_timeout"
    assert accepted.timeout_type == "local_deadline"
    assert [record["timeout_type"] for record in logger.records] == [
        "local_deadline", "local_deadline",
    ]
    assert logger.technical_abstentions == [{
        "failure_code": "request_timeout", "timeout_type": "local_deadline",
        "window_id": "1:vote_casting:1:1:exile_vote",
    }]


@pytest.mark.asyncio
async def test_provider_timeout_is_distinguished_in_vote_telemetry():
    timeout = APITimeoutError(request=httpx.Request("POST", "https://model.test"))
    client = JsonClient([timeout, timeout])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(logger), make_request(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.technical_failure_code == "request_timeout"
    assert accepted.timeout_type == "provider_timeout"
    assert [record["timeout_type"] for record in logger.records] == [
        "provider_timeout", "provider_timeout",
    ]
    assert logger.technical_abstentions == [{
        "failure_code": "request_timeout", "timeout_type": "provider_timeout",
        "window_id": "1:vote_casting:1:1:exile_vote",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("make_error", "failure_code"),
    [
        (
            lambda: APIConnectionError(
                request=httpx.Request("POST", "https://model.test")
            ),
            "provider_connection_error",
        ),
        (
            lambda: RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://model.test")
                ),
                body={},
            ),
            "provider_rate_limit",
        ),
        (
            lambda: InternalServerError(
                "unavailable",
                response=httpx.Response(
                    500, request=httpx.Request("POST", "https://model.test")
                ),
                body={},
            ),
            "provider_server_error",
        ),
    ],
)
async def test_transient_provider_failure_retries_then_technically_abstains(
    make_error, failure_code,
):
    client = JsonClient([make_error(), make_error()])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(logger), make_request(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.technical_failure_code == failure_code
    assert [record["failure_code"] for record in logger.records] == [
        failure_code, failure_code,
    ]
    assert [record["parse_result"] for record in logger.records] == [
        "invoke_error_retry", "invoke_error_fallback",
    ]
    assert logger.technical_abstentions == [{
        "failure_code": failure_code,
        "window_id": "1:vote_casting:1:1:exile_vote",
    }]


@pytest.mark.asyncio
async def test_unexpected_invoke_error_logs_generic_failure_code_before_raising():
    client = JsonClient([RuntimeError("client bug")])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    with pytest.raises(RuntimeError, match="client bug"):
        await role.request_action(state, make_log(logger), make_request(state))

    assert logger.records[0]["parse_result"] == "invoke_error"
    assert logger.records[0]["failure_code"] == "invoke_error"
