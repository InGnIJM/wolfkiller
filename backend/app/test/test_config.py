from app.config import GameConfig, LLMConfig


def test_vote_retry_default_allows_slow_provider_completion():
    assert LLMConfig.action_retry_timeout_seconds == 120.0


def test_final_retry_covers_the_bulk_of_observed_latencies():
    """The last attempt is the only remaining chance, so its budget must not be
    the smallest of the three. Measured successes: p50 19.7s, p90 49.5s,
    p95 60.2s, so 30s forfeited roughly a third of recoverable calls."""
    assert LLMConfig.action_final_retry_timeout_seconds == 60.0
    assert (
        LLMConfig.action_final_retry_timeout_seconds
        < LLMConfig.action_retry_timeout_seconds
    )


def test_vote_phase_default_covers_two_five_worker_retry_batches():
    """The phase waits on ``vote_concurrency`` workers at a time, so the worst
    case is two batches of one full retry ladder. A phase window below that
    would cut off ballots that the ladder still had budget for, turning a
    recoverable call into a phase-deadline abstention."""
    ladder = (
        LLMConfig.action_timeout_seconds
        + LLMConfig.action_retry_timeout_seconds
        + LLMConfig.action_final_retry_timeout_seconds
    )
    batches = -(-10 // GameConfig.vote_concurrency)  # ceil for a ten-seat board

    assert GameConfig.vote_phase_timeout_seconds == 540.0
    assert GameConfig.vote_phase_timeout_seconds >= batches * ladder
