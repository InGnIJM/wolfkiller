from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.services.audience_event_service import (
    AudienceEventService,
    CursorAheadError,
    GameNotFoundError,
)


@dataclass
class FakeRepository:
    game: dict[str, object] | None = field(
        default_factory=lambda: {"game_id": "game-1"},
    )
    snapshot: dict[str, object] | None = field(
        default_factory=lambda: {
            "game_id": "game-1",
            "schema_version": 1,
            "projection_version": 1,
            "last_seq": 2,
            "state": {"phase": "day"},
        },
    )
    page: dict[str, object] = field(
        default_factory=lambda: {
            "game_id": "game-1",
            "events": [{"seq": 2, "event_type": "phase"}],
            "next_seq": 2,
            "high_watermark": 3,
            "has_more": True,
        },
    )
    game_calls: list[str] = field(default_factory=list)
    snapshot_calls: list[str] = field(default_factory=list)
    event_calls: list[tuple[str, int, int, int | None]] = field(default_factory=list)

    def get_game(self, game_id: str) -> dict[str, object] | None:
        self.game_calls.append(game_id)
        return self.game

    def get_audience_snapshot(self, game_id: str) -> dict[str, object] | None:
        self.snapshot_calls.append(game_id)
        return self.snapshot

    def get_audience_events(
        self,
        game_id: str,
        *,
        after_seq: int,
        limit: int,
        through_seq: int | None = None,
    ) -> dict[str, object]:
        self.event_calls.append((game_id, after_seq, limit, through_seq))
        return self.page


def test_get_snapshot_returns_the_repository_projection() -> None:
    repository = FakeRepository()

    result = AudienceEventService(repository).get_snapshot("game-1")

    assert result == repository.snapshot
    assert repository.game_calls == ["game-1"]
    assert repository.snapshot_calls == ["game-1"]


def test_get_snapshot_returns_none_when_existing_game_has_no_projection() -> None:
    repository = FakeRepository(snapshot=None)

    assert AudienceEventService(repository).get_snapshot("game-1") is None


@pytest.mark.parametrize("operation", ["snapshot", "events"])
def test_missing_game_has_an_explicit_application_error(operation: str) -> None:
    repository = FakeRepository(game=None)
    service = AudienceEventService(repository)

    with pytest.raises(GameNotFoundError) as error:
        if operation == "snapshot":
            service.get_snapshot("missing")
        else:
            service.get_events("missing", after_seq=0, limit=100)

    assert error.value.code == "game_not_found"
    assert error.value.game_id == "missing"
    assert repository.snapshot_calls == []
    assert repository.event_calls == []


def test_get_events_returns_a_stable_paginated_response() -> None:
    repository = FakeRepository()

    result = AudienceEventService(repository).get_events(
        "game-1", after_seq=1, limit=1, through_seq=3,
    )

    assert result == repository.page
    assert repository.event_calls == [("game-1", 1, 1, 3)]


@pytest.mark.parametrize("after_seq", [-1, True, False, 1.0, "1", None])
def test_after_seq_requires_a_non_negative_integer(after_seq: object) -> None:
    repository = FakeRepository()

    with pytest.raises(ValueError, match="after_seq must be a non-negative integer"):
        AudienceEventService(repository).get_events(
            "game-1", after_seq=after_seq, limit=100,  # type: ignore[arg-type]
        )

    assert repository.game_calls == []
    assert repository.event_calls == []


@pytest.mark.parametrize("limit", [0, 1001, True, False, 1.0, "10", None])
def test_limit_requires_an_integer_between_one_and_one_thousand(limit: object) -> None:
    repository = FakeRepository()

    with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
        AudienceEventService(repository).get_events(
            "game-1", after_seq=0, limit=limit,  # type: ignore[arg-type]
        )

    assert repository.game_calls == []
    assert repository.event_calls == []


@pytest.mark.parametrize("through_seq", [-1, True, False, 1.0, "1"])
def test_through_seq_requires_a_non_negative_integer_or_none(
    through_seq: object,
) -> None:
    repository = FakeRepository()

    with pytest.raises(ValueError, match="through_seq must be a non-negative integer"):
        AudienceEventService(repository).get_events(
            "game-1",
            after_seq=0,
            limit=100,
            through_seq=through_seq,  # type: ignore[arg-type]
        )

    assert repository.game_calls == []
    assert repository.event_calls == []


def test_through_seq_cannot_precede_after_seq() -> None:
    repository = FakeRepository()

    with pytest.raises(ValueError, match="through_seq must be greater than or equal to after_seq"):
        AudienceEventService(repository).get_events(
            "game-1", after_seq=2, limit=100, through_seq=1,
        )

    assert repository.game_calls == []
    assert repository.event_calls == []


@pytest.mark.parametrize("through_seq", [None, 4, 10])
def test_after_seq_beyond_the_current_stream_raises_cursor_ahead(
    through_seq: int | None,
) -> None:
    repository = FakeRepository(page={
        "game_id": "game-1",
        "events": [],
        "next_seq": 4,
        "high_watermark": 3,
        "has_more": False,
    })

    with pytest.raises(CursorAheadError) as error:
        AudienceEventService(repository).get_events(
            "game-1", after_seq=4, limit=100, through_seq=through_seq,
        )

    assert error.value.code == "cursor_ahead"
    assert error.value.game_id == "game-1"
    assert error.value.after_seq == 4
    assert error.value.high_watermark == 3


def test_cursor_at_the_high_watermark_returns_an_empty_page() -> None:
    page = {
        "game_id": "game-1",
        "events": [],
        "next_seq": 3,
        "high_watermark": 3,
        "has_more": False,
    }
    repository = FakeRepository(page=page)

    assert AudienceEventService(repository).get_events(
        "game-1", after_seq=3, limit=100,
    ) == page


def test_cursor_at_a_pinned_through_seq_is_not_mistaken_for_cursor_ahead() -> None:
    page = {
        "game_id": "game-1",
        "events": [],
        "next_seq": 2,
        "high_watermark": 2,
        "has_more": False,
    }
    repository = FakeRepository(page=page)

    assert AudienceEventService(repository).get_events(
        "game-1", after_seq=2, limit=100, through_seq=2,
    ) == page
