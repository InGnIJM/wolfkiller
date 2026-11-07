from app.config import GameConfig, LLMConfig


def test_vote_retry_default_allows_slow_provider_completion():
    assert LLMConfig.action_retry_timeout_seconds == 240.0


def test_final_retry_covers_the_bulk_of_observed_latencies():
    """The last attempt is the only remaining chance, so its budget must not be
    the smallest of the three. The floor in ``_action_timeout_seconds`` lifts
    the effective final budget to the first attempt's anyway; the configured
    value only has to stay inside the ladder order."""
    assert LLMConfig.action_final_retry_timeout_seconds == 120.0
    assert (
        LLMConfig.action_final_retry_timeout_seconds
        < LLMConfig.action_retry_timeout_seconds
    )


def test_vote_phase_default_covers_two_five_worker_retry_batches():
    """The phase waits on ``vote_concurrency`` workers at a time, so the worst
    case is two batches of one full retry ladder. A phase window below that
    would cut off ballots that the ladder still had budget for, turning a
    recoverable call into a phase-deadline abstention. The default also covers
    the three batches a twelve-seat board can need."""
    ladder = (
        LLMConfig.action_timeout_seconds
        + LLMConfig.action_retry_timeout_seconds
        + LLMConfig.action_final_retry_timeout_seconds
    )
    batches = -(-10 // GameConfig.vote_concurrency)  # ceil for a ten-seat board

    assert GameConfig.vote_phase_timeout_seconds == 1800.0
    assert GameConfig.vote_phase_timeout_seconds >= batches * ladder
