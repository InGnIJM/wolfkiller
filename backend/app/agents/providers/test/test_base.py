import re
from types import SimpleNamespace

import pytest

from app.agents.providers.base import (
    ACTION_PURPOSES, MAX_CUSTOM_HEADERS, CallBudget, CallPurpose, ProviderProfile,
    ModelCapabilities, call_budget, header_error, is_responses_endpoint,
    merge_request_headers, new_session_id, provider_max_retries,
    strip_responses_suffix,
)


def _config():
    return SimpleNamespace(
        max_tokens=768,
        action_max_tokens=2048,
        action_timeout_seconds=90.0,
        action_retry_timeout_seconds=120.0,
    )


def test_text_purpose_uses_general_budget_and_primary_timeout():
    assert call_budget(_config(), CallPurpose.TEXT) == CallBudget(
        max_tokens=768, timeout=90.0,
    )


@pytest.mark.parametrize("purpose", sorted(ACTION_PURPOSES))
def test_action_purposes_use_action_budget_with_retry_headroom(purpose):
    assert call_budget(_config(), purpose) == CallBudget(
        max_tokens=2048, timeout=125.0,
    )


def test_action_timeout_headroom_follows_the_larger_of_primary_and_retry():
    config = _config()
    config.action_timeout_seconds = 200.0

    assert call_budget(config, CallPurpose.TOOLS).timeout == 205.0


def test_action_purposes_exclude_text():
    assert CallPurpose.TEXT not in ACTION_PURPOSES
    assert ACTION_PURPOSES == {
        CallPurpose.ACTION_JSON, CallPurpose.ACTION_STRICT, CallPurpose.TOOLS,
    }


def test_provider_max_retries_defaults_to_four(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER_MAX_RETRIES", raising=False)

    assert provider_max_retries() == 4


def test_provider_max_retries_reads_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_MAX_RETRIES", "0")

    assert provider_max_retries() == 0


# ── custom request headers ────────────────────────────────────

def _profile(**overrides):
    base = {
        "profile_id": "custom-openai",
        "api_mode": "chat_completions",
        "capabilities": ModelCapabilities(True, False, True, False, True),
        "default_action_max_tokens": 2048,
    }
    base.update(overrides)
    return ProviderProfile(**base)


def test_header_error_accepts_a_plain_token_name():
    assert header_error("x-opencode-session", "ses_abc") is None


@pytest.mark.parametrize(
    "name",
    ["", "bad name", "bad:name", "bad\nname", "bad(name)", "名字"],
)
def test_header_error_rejects_names_outside_the_token_charset(name):
    assert header_error(name, "value") == "name must be an RFC 7230 token"


@pytest.mark.parametrize("value", ["a\rb", "a\nb", "a\x00b"])
def test_header_error_rejects_control_characters_in_the_value(value):
    assert header_error("x-custom", value) == "value must not contain CR, LF or NUL"


@pytest.mark.parametrize(
    "name",
    [
        "authorization", "Content-Type", "CONTENT-LENGTH", "host", "connection",
        "keep-alive", "proxy-authenticate", "proxy-authorization",
        "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
        "accept-encoding",
    ],
)
def test_header_error_rejects_names_managed_by_the_sdk(name):
    assert header_error(name, "value") == "header is managed by the SDK or gateway"


def test_header_error_accepts_an_ordinary_value():
    assert header_error("http-referer", "https://example.test/app") is None


def test_merge_request_headers_layers_profile_then_session_then_user():
    profile = _profile(
        default_headers=(("x-opencode-client", "wolfkiller"),),
        session_header="x-opencode-session",
    )

    merged = merge_request_headers(
        profile,
        (("x-opencode-session", "manual"), ("x-extra", "1")),
        session_id="ses_generated",
    )

    assert merged == {
        "x-opencode-client": "wolfkiller",
        "x-opencode-session": "manual",
        "x-extra": "1",
    }


def test_merge_request_headers_uses_the_generated_session_when_unset():
    profile = _profile(session_header="x-opencode-session")

    merged = merge_request_headers(profile, (), session_id="ses_generated")

    assert merged == {"x-opencode-session": "ses_generated"}


def test_merge_request_headers_omits_the_session_header_without_a_session():
    profile = _profile(session_header="x-opencode-session")

    assert merge_request_headers(profile, (), session_id=None) == {}


def test_merge_request_headers_ignores_profiles_without_a_session_header():
    assert merge_request_headers(_profile(), (), session_id="ses_x") == {}


def test_merge_request_headers_drops_unusable_entries():
    profile = _profile(default_headers=(("bad name", "v"), ("ok", "v")))

    merged = merge_request_headers(
        profile, (("x-custom", "a\nb"), ("x-good", "yes")), session_id=None,
    )

    assert merged == {"ok": "v", "x-good": "yes"}


def test_merge_request_headers_overrides_a_session_header_regardless_of_case():
    profile = _profile(session_header="x-opencode-session")

    merged = merge_request_headers(
        profile,
        (("X-Opencode-Session", "ses_pinned"),),
        session_id="ses_generated",
    )

    assert merged == {"X-Opencode-Session": "ses_pinned"}


def test_merge_request_headers_drops_reserved_user_entries():
    merged = merge_request_headers(
        _profile(), (("Authorization", "Bearer x"), ("x-good", "yes")), session_id=None,
    )

    assert merged == {"x-good": "yes"}


def test_new_session_id_matches_the_reference_client_shape():
    session_id = new_session_id()

    assert re.fullmatch(r"ses_[0-9a-f]{26}", session_id)


def test_new_session_ids_are_unique():
    assert new_session_id() != new_session_id()


def test_max_custom_headers_is_thirty_two():
    assert MAX_CUSTOM_HEADERS == 32


# ── OpenAI Responses dialect ──────────────────────────────────

@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/zen/v1/responses", True),
        ("/zen/v1/responses/", True),
        ("/v1/responses", True),
        ("/zen/v1", False),
        ("/zen/v1/chat/completions", False),
        ("", False),
        ("/", False),
    ],
)
def test_is_responses_endpoint_reads_the_path_suffix(path, expected):
    assert is_responses_endpoint(path) is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://opencode.ai/zen/v1/responses", "https://opencode.ai/zen/v1"),
        ("https://opencode.ai/zen/v1/responses/", "https://opencode.ai/zen/v1"),
        ("https://opencode.ai/zen/go/v1/responses", "https://opencode.ai/zen/go/v1"),
        ("https://opencode.ai/zen/v1", "https://opencode.ai/zen/v1"),
        ("https://api.openai.com/v1", "https://api.openai.com/v1"),
    ],
)
def test_strip_responses_suffix_removes_only_the_resource_path(url, expected):
    assert strip_responses_suffix(url) == expected
