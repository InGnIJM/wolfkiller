from unittest.mock import patch

from app.agents.llm_client import LLMClient, LLMClientConfig
from app.models.contracts import ActionContract
from app.models.game import GamePhase


def make_client() -> LLMClient:
    return LLMClient(config=LLMClientConfig(
        base_url="https://model.test/v1",
        api_key="test-key",
        model_id="test-model",
        temperature=0,
        max_tokens=128,
        strict_base_url="https://model.test/beta",
        action_timeout_seconds=90,
        action_retry_timeout_seconds=120,
    ))


def test_json_action_client_timeout_has_guard_above_retry_budget():
    client = make_client()

    with patch("app.agents.llm_client.ChatOpenAI") as chat:
        client.get_action_model()

    assert chat.call_args.kwargs["timeout"] == 125


def test_strict_action_client_timeout_has_guard_above_retry_budget():
    client = make_client()
    contract = ActionContract(
        contract_id="exile_vote",
        phase=GamePhase.VOTE_CASTING,
        action_types=("vote", "abstain"),
        actions_requiring_target=frozenset({"vote"}),
        resolution_priority=0,
        fallback_action_type="abstain",
    )

    with patch("app.agents.llm_client.ChatOpenAI") as chat:
        client.get_model_with_action_tool(contract)

    assert chat.call_args.kwargs["timeout"] == 125
