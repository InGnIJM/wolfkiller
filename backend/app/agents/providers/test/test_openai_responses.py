from types import SimpleNamespace
from unittest.mock import patch

from app.agents.providers.base import CallPurpose
from app.agents.providers.openai_responses import OpenAIResponsesTransport
from app.agents.providers.registry import ProviderRegistry


def config(**overrides):
    values = dict(
        base_url="https://opencode.ai/zen/v1/responses",
        strict_base_url="https://unused.test/beta",
        api_key="secret",
        model_id="muse-spark-1.3-contributor-free",
        temperature=0.7,
        max_tokens=4096,
        action_max_tokens=2048,
        action_timeout_seconds=90.0,
        action_retry_timeout_seconds=120.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def profile(base_url="https://opencode.ai/zen/v1/responses"):
    return ProviderRegistry().resolve("auto", base_url, "m")


def build(cfg=None, prof=None, purpose=CallPurpose.TEXT):
    cfg = cfg or config()
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAIResponsesTransport().build(cfg, prof or profile(cfg.base_url), purpose)
    return chat.call_args.kwargs


def test_responses_dialect_is_requested():
    assert build()["use_responses_api"] is True


def test_the_published_url_suffix_is_stripped_for_the_sdk():
    """The SDK appends ``/responses`` itself, so ``/responses/responses`` must not happen."""
    assert build()["base_url"] == "https://opencode.ai/zen/v1"


def test_a_plain_base_url_is_left_alone():
    cfg = config(base_url="https://opencode.ai/zen/v1")

    assert build(cfg)["base_url"] == "https://opencode.ai/zen/v1"


def test_go_endpoint_is_stripped_independently():
    cfg = config(base_url="https://opencode.ai/zen/go/v1/responses")

    assert build(cfg)["base_url"] == "https://opencode.ai/zen/go/v1"


def test_budget_headers_and_retries_still_apply():
    kwargs = build()

    assert kwargs["model"] == "muse-spark-1.3-contributor-free"
    assert kwargs["api_key"] == "secret"
    assert kwargs["max_tokens"] == 4096
    assert kwargs["timeout"] == 90.0
    assert kwargs["temperature"] == 0.7
    assert kwargs["default_headers"]["x-opencode-session"].startswith("ses_")


def test_action_purpose_uses_the_action_budget():
    kwargs = build(purpose=CallPurpose.ACTION_JSON)

    assert kwargs["max_tokens"] == 2048
    assert kwargs["timeout"] == 125.0


def test_strict_purpose_never_switches_to_a_strict_endpoint():
    """The responses profiles do not declare a strict endpoint."""
    cfg = config(strict_base_url="https://strict.example")

    assert build(cfg, purpose=CallPurpose.ACTION_STRICT)["base_url"] == (
        "https://opencode.ai/zen/v1"
    )


def test_chat_completions_transport_never_asks_for_the_responses_dialect():
    from app.agents.providers.openai_compatible import OpenAICompatibleTransport

    cfg = config(base_url="https://opencode.ai/zen/v1")
    with patch("app.agents.providers.openai_compatible.ChatOpenAI") as chat:
        OpenAICompatibleTransport().build(cfg, profile(cfg.base_url), CallPurpose.TEXT)

    assert "use_responses_api" not in chat.call_args.kwargs


def test_user_headers_override_the_generated_session_header():
    cfg = config(headers=(("x-opencode-session", "ses_pinned"),))

    assert build(cfg)["default_headers"] == {"x-opencode-session": "ses_pinned"}
