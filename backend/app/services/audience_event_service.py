from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.persistence.repository import GameRepository


class AudienceEventError(Exception):
    """Base class for expected audience event application errors."""

    code = "audience_event_error"


class GameNotFoundError(AudienceEventError):
    code = "game_not_found"

    def __init__(self, game_id: str) -> None:
        self.game_id = game_id
        super().__init__(f"game not found: {game_id}")


class CursorAheadError(AudienceEventError):
    code = "cursor_ahead"

    def __init__(self, game_id: str, after_seq: int, high_watermark: int) -> None:
        self.game_id = game_id
        self.after_seq = after_seq
        self.high_watermark = high_watermark
        super().__init__(
            f"after_seq {after_seq} is ahead of high watermark {high_watermark} "
            f"for game {game_id}"
        )


# Explicit aliases keep the errors unambiguous at call sites that import several
# service layers with similarly named failures.
AudienceGameNotFoundError = GameNotFoundError
AudienceCursorAheadError = CursorAheadError


class AudienceEventService:
    """Application boundary for reading the durable audience projection."""

    def __init__(self, repository: GameRepository) -> None:
        self._repository = repository

    def get_snapshot(self, game_id: str) -> dict[str, object] | None:
        self._require_game(game_id)
        return self._repository.get_audience_snapshot(game_id)

    def get_events(
        self,
        game_id: str,
        *,
        after_seq: int,
        limit: int,
        through_seq: int | None = None,
    ) -> dict[str, object]:
        self._validate_page_request(
            after_seq=after_seq,
            limit=limit,
            through_seq=through_seq,
        )
        self._require_game(game_id)
        page = self._repository.get_audience_events(
            game_id,
            after_seq=after_seq,
            limit=limit,
            through_seq=through_seq,
        )
        high_watermark = page["high_watermark"]
        if type(high_watermark) is not int or high_watermark < 0:
            raise RuntimeError("repository returned an invalid high_watermark")
        if after_seq > high_watermark:
            raise CursorAheadError(game_id, after_seq, high_watermark)
        return page

    def _require_game(self, game_id: str) -> None:
        if self._repository.get_game(game_id) is None:
            raise GameNotFoundError(game_id)

    @staticmethod
    def _validate_page_request(
        *,
        after_seq: int,
        limit: int,
        through_seq: int | None,
    ) -> None:
        if type(after_seq) is not int or after_seq < 0:
            raise ValueError("after_seq must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if through_seq is not None and (
            type(through_seq) is not int or through_seq < 0
        ):
            raise ValueError("through_seq must be a non-negative integer")
        if through_seq is not None and through_seq < after_seq:
            raise ValueError(
                "through_seq must be greater than or equal to after_seq"
            )
