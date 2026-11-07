import asyncio

import anthropic
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
    action_final_retry_timeout_seconds = 0.1
    #: Keep the transient backoff out of the test clock.
    action_retry_backoff_seconds = 0.0
    #: A 503's real floor is tens of seconds; the suite must never wait on it.
    action_refused_backoff_seconds = 0.0

    def __init__(self, responses):
        self.model = RecordingModel(responses)

    def get_action_model(self):
        return self.model


class TelemetryLogger:
    def __init__(self):
        self.records = []
        self.llm_call_records = []
        self.technical_abstentions = []

    def log_vote_telemetry(self, game_id, round_num, seat, **data):
        self.records.append(data)

    def log_vote_technical_abstain(self, game_id, round_num, seat, **data):
        self.technical_abstentions.append(data)

    def log_llm_call(self, game_id, round_num, phase, seat, **data):
        self.llm_call_records.append(data)

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
async def test_json_vote_accepts_anthropic_thinking_and_text_blocks():
    client = JsonClient([
        AIMessage(content=[
            {"type": "thinking", "thinking": "Seat 2 is the safest exile."},
            {"type": "text", "text": '{"action_type":"vote","target_seat":2,"reasoning":"x"}'},
        ]),
    ])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(), make_request(state))

    assert accepted.command.action_type == "vote"
    assert accepted.command.target_seat == 2


@pytest.mark.asyncio
async def test_invalid_vote_retry_keeps_original_prompt_prefix():
    client = JsonClient([
        AIMessage(content="not json"),
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(), make_request(state))

    first, retry = client.model.messages[0], client.model.messages[1]
    assert accepted.command.target_seat == 2
    # The retry appends one reminder to the original exchange instead of
    # rebuilding a compact prompt: every shared prefix byte stays warm.
    assert retry[: len(first)] == first
    assert len(retry) == len(first) + 1
    assert "previous action was invalid" in retry[-1].content


@pytest.mark.asyncio
async def test_truncated_vote_is_classified_and_retried_with_brief_hint():
    client = JsonClient([
        AIMessage(content="", response_metadata={"finish_reason": "length"}),
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(logger), make_request(state))

    assert accepted.command.target_seat == 2
    assert logger.records[0]["failure_code"] == "action_response_truncated"
    retry = client.model.messages[1]
    assert retry[: len(client.model.messages[0])] == client.model.messages[0]
    assert "直接" in retry[-1].content


@pytest.mark.asyncio
async def test_empty_vote_without_length_reason_keeps_not_text_code():
    client = JsonClient([
        AIMessage(content="", response_metadata={"finish_reason": "stop"}),
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    await role.request_action(state, make_log(logger), make_request(state))

    assert logger.records[0]["failure_code"] == "action_response_not_text"


def test_vote_instruction_asks_for_brief_reasoning():
    state = make_state()
    instruction = PromptBuilder._vote_instruction(state)
    assert "简短" in instruction
    assert "reasoning" in instruction


@pytest.mark.asyncio
async def test_exile_vote_uses_the_shared_vote_client_when_set():
    seat_client = JsonClient([
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    vote_client = JsonClient([
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), seat_client)
    role.vote_llm_client = vote_client

    accepted = await role.request_action(state, make_log(), make_request(state))

    assert accepted.command.target_seat == 2
    assert len(vote_client.model.messages) == 1
    assert seat_client.model.messages == []


def test_resolve_action_client_only_switches_exile_vote():
    from dataclasses import replace

    seat_client = JsonClient([])
    vote_client = JsonClient([])
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), seat_client)
    role.vote_llm_client = vote_client
    other = replace(
        make_request(state),
        contract=replace(VOTE_CONTRACT, contract_id="day_action"),
        idempotency_key="1:vote_casting:1:1:day_action",
    )

    assert role._resolve_action_client(make_request(state)) is vote_client
    assert role._resolve_action_client(other) is seat_client

    role.vote_llm_client = None
    assert role._resolve_action_client(make_request(state)) is seat_client


@pytest.mark.asyncio
async def test_vote_telemetry_reports_the_vote_model_name():
    seat_client = JsonClient([
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    seat_client.model_name = "seat-model"
    vote_client = JsonClient([
        AIMessage(content='{"action_type":"vote","target_seat":2,"reasoning":"x"}'),
    ])
    vote_client.model_name = "vote-model"
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), seat_client)
    role.vote_llm_client = vote_client

    await role.request_action(state, make_log(logger), make_request(state))

    model_rows = [r["model_id"] for r in logger.llm_call_records if "model_id" in r]
    assert model_rows == ["vote-model"]


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
    client = JsonClient([
        AIMessage(content="not json"),
        AIMessage(content="still invalid"),
        AIMessage(content="still invalid"),
    ])
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
    client.action_final_retry_timeout_seconds = 0.001
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(logger), make_request(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.technical_failure_code == "request_timeout"
    assert accepted.timeout_type == "local_deadline"
    assert [record["timeout_type"] for record in logger.records] == [
        "local_deadline", "local_deadline", "local_deadline",
    ]
    assert logger.technical_abstentions == [{
        "failure_code": "request_timeout", "timeout_type": "local_deadline",
        "window_id": "1:vote_casting:1:1:exile_vote",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_timeout",
    [
        lambda: APITimeoutError(request=httpx.Request("POST", "https://model.test")),
        lambda: anthropic.APITimeoutError(
            request=httpx.Request("POST", "https://model.test/v1/messages"),
        ),
    ],
    ids=["openai", "anthropic"],
)
async def test_provider_timeout_is_distinguished_in_vote_telemetry(make_timeout):
    client = JsonClient([make_timeout(), make_timeout(), make_timeout()])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(logger), make_request(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.technical_failure_code == "request_timeout"
    assert accepted.timeout_type == "provider_timeout"
    assert [record["timeout_type"] for record in logger.records] == [
        "provider_timeout", "provider_timeout", "provider_timeout",
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
        (
            lambda: anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://model.test/v1/messages")
            ),
            "provider_connection_error",
        ),
        (
            lambda: anthropic.RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://model.test/v1/messages")
                ),
                body=None,
            ),
            "provider_rate_limit",
        ),
        (
            lambda: anthropic.OverloadedError(
                "overloaded",
                response=httpx.Response(
                    529, request=httpx.Request("POST", "https://model.test/v1/messages")
                ),
                body=None,
            ),
            "provider_server_error",
        ),
    ],
)
async def test_transient_provider_failure_retries_then_technically_abstains(
    make_error, failure_code,
):
    client = JsonClient([make_error(), make_error(), make_error()])
    logger = TelemetryLogger()
    state = make_state()
    role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)

    accepted = await role.request_action(state, make_log(logger), make_request(state))

    assert accepted.command.action_type == "abstain"
    assert accepted.technical_failure_code == failure_code
    assert [record["failure_code"] for record in logger.records] == [
        failure_code, failure_code, failure_code,
    ]
    assert [record["parse_result"] for record in logger.records] == [
        "invoke_error_retry", "invoke_error_retry", "invoke_error_fallback",
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
