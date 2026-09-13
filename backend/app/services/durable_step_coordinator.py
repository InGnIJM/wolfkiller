"""Atomic application boundary for one recoverable game step."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from app.models.game import GameState
from app.persistence.checkpoint_codec import CheckpointCodec
from app.persistence.repository import GameRepository
from app.services.audience_projector import AudienceProjector


def _digest(value: object) -> str:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("step facts must be canonical JSON") from error
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DurableCommit:
    storage_revision: int
    first_audience_seq: int | None
    last_audience_seq: int
    replayed: bool


class DurableStepCoordinator:
    """Build the checkpoint and audience projection before the DB transaction."""

    def __init__(
        self, repository: GameRepository, codec: CheckpointCodec,
        projector: AudienceProjector,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._repository = repository
        self._codec = codec
        self._projector = projector
        self._fault_injector = fault_injector

    def commit(
        self, *, state: GameState, orchestration: Mapping[str, object],
        expected_storage_revision: int, execution_generation: int,
        step_key: str, input_facts: Mapping[str, object],
        result_facts: Mapping[str, object],
        domain_events: Iterable[Mapping[str, object]],
        consumed_model_request_ids: Iterable[str] = (),
        derived_jobs: Iterable[Mapping[str, object]] = (),
    ) -> DurableCommit:
        game = self._repository.get_game(state.game_id)
        if game is None:
            raise KeyError(state.game_id)
        domains = [dict(item) for item in domain_events]
        audience = self._projector.project_events(state.game_id, domains)
        checkpoint = self._codec.encode(state, orchestration=orchestration)
        audience_state = self._projector.snapshot(
            state, execution_status=str(game["execution_status"]),
            recovery_block_code=(game["recovery_block_code"] if isinstance(game["recovery_block_code"], str) else None),
        )
        if self._fault_injector is not None:
            self._fault_injector("before_step_commit")
        receipt = self._repository.commit_step(
            game_id=state.game_id,
            expected_storage_revision=expected_storage_revision,
            execution_generation=execution_generation,
            step_key=step_key, input_digest=_digest(dict(input_facts)),
            result_digest=_digest(dict(result_facts)), checkpoint=checkpoint,
            domain_events=domains, audience_events=audience,
            audience_state=audience_state,
            projection_version=self._projector.projection_version,
            consumed_model_request_ids=consumed_model_request_ids,
            derived_jobs=derived_jobs,
        )
        if self._fault_injector is not None:
            self._fault_injector("after_step_commit")
            self._fault_injector("before_audience_send")
        return DurableCommit(
            storage_revision=int(receipt["storage_revision"]),
            first_audience_seq=(int(receipt["first_seq"]) if receipt["first_seq"] is not None else None),
            last_audience_seq=int(receipt["last_seq"]),
            replayed=bool(receipt["replayed"]),
        )
