from types import SimpleNamespace

import pytest

from app.agents.providers.base import (
    ACTION_PURPOSES, CallBudget, CallPurpose, call_budget, provider_max_retries,
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
