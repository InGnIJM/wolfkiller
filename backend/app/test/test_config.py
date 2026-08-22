from app.config import GameConfig, LLMConfig


def test_vote_retry_default_allows_slow_provider_completion():
    assert LLMConfig.action_retry_timeout_seconds == 120.0


def test_vote_phase_default_covers_two_five_worker_retry_batches():
    assert GameConfig.vote_phase_timeout_seconds == 500.0
