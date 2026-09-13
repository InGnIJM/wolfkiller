from __future__ import annotations

import pytest

from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.persistence.checkpoint_codec import CheckpointCodec
from app.persistence.repository import GameRepository
from app.roles.registry import builtin_registry
from app.services.audience_projector import AudienceProjector
from app.services.durable_step_coordinator import DurableStepCoordinator


def _state(registry_digest: str) -> GameState:
    return GameState(
        game_id="game", phase=GamePhase.SPEECH, round_number=1,
        config=GameConfig(role_counts={"wolf-killer-villager": 1}),
        players={1: PlayerState(1, "wolf-killer-villager", "good")},
        registry_digest=registry_digest,
    )


def test_projector_whitelists_public_events_and_payload_fields() -> None:
    projector = AudienceProjector()
    events = projector.project_events("game", [
        {
            "event_id": "d1", "event_type": "SPEECH_MADE",
            "visibility": ["PUBLIC"],
            "payload": {"player_seat": 1, "text": "hello", "round_number": 1, "private_prompt": "secret"},
        },
        {
            "event_id": "d2", "event_type": "PRIVATE_FACT",
            "visibility": ["ACTOR"], "payload": {"secret": "x"},
        },
    ])
    assert events == [{
        "event_id": "d1:audience", "event_type": "speech",
        "schema_version": 1,
        "payload": {"player_seat": 1, "text": "hello", "round_number": 1},
    }]


def test_projector_normalizes_public_role_actions_without_private_reasoning() -> None:
    events = AudienceProjector().project_events("game", [{
        "event_id": "wolf-kill",
        "event_type": "WEREWOLF_KILL",
        "visibility": ["PUBLIC"],
        "payload": {
            "target_seat": 4,
            "round_number": 2,
            "vote_counts": {"4": 2},
            "reasoning": "private channel",
        },
    }, {
        "event_id": "seer-check",
        "event_type": "SEER_CHECK",
        "visibility": ["PUBLIC"],
        "payload": {
            "target_seat": 1,
            "round_number": 2,
            "result": "werewolf",
            "thought": "private thought",
        },
    }])

    assert [event["payload"] for event in events] == [{
        "action_type": "werewolf_kill",
        "target_seat": 4,
        "round_number": 2,
        "vote_counts": {"4": 2},
    }, {
        "action_type": "seer_check",
        "target_seat": 1,
        "round_number": 2,
        "result": "werewolf",
    }]


def test_projector_applies_nested_whitelists_to_game_initialization() -> None:
    events = AudienceProjector().project_events("game", [{
        "event_id": "init",
        "event_type": "GAME_INITIALIZED",
        "visibility": ["PUBLIC"],
        "payload": {
            "players": {
                "1": {
                    "seat_number": 1, "is_alive": True, "is_sheriff": False,
                    "role": "wolf-killer-villager", "camp": "good",
                    "private_prompt": "secret",
                },
            },
            "config": {
                "role_counts": {"wolf-killer-villager": 1},
                "reveal_on_death": True,
                "api_key": "secret",
            },
        },
    }])

    assert events[0]["payload"] == {
        "players": {
            "1": {
                "seat_number": 1, "is_alive": True, "is_sheriff": False,
                "role": "wolf-killer-villager", "camp": "good",
            },
        },
        "config": {
            "role_counts": {"wolf-killer-villager": 1},
            "reveal_on_death": True,
        },
    }


def test_durable_coordinator_commits_checkpoint_projection_and_consumption(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    registry = builtin_registry.freeze()
    try:
        repository.create_game(
            game_id="game", name="game", config={}, execution_status="running",
            source="native", model_snapshot=[], execution_generation=1,
        )
        repository.prepare_model_request(
            game_id="game", request_id="speech-request", actor_seat=1,
            action_position="speech:1:1", request_digest="request",
            provider_profile="test", model_id="fake",
        )
        repository.resolve_model_request(
            "game", "speech-request", status="resolved",
            normalized_result={"text": "hello"},
        )
        state = _state(registry.digest)
        coordinator = DurableStepCoordinator(
            repository, CheckpointCodec(registry), AudienceProjector(),
        )
        result = coordinator.commit(
            state=state, orchestration={"execution_position": "speech:1:1"},
            expected_storage_revision=0, execution_generation=1,
            step_key="speech:1:1", input_facts={"request_id": "speech-request"},
            result_facts={"text": "hello"},
            domain_events=[{
                "event_id": "speech-domain", "event_type": "SPEECH_MADE",
                "visibility": ["PUBLIC"],
                "payload": {"player_seat": 1, "text": "hello", "round_number": 1},
            }],
            consumed_model_request_ids=("speech-request",),
        )
        assert result.storage_revision == 1
        assert result.last_audience_seq == 1
        assert repository.load_checkpoint("game")["checkpoint"]["state"]["phase"] == "speech"
        assert repository.get_model_request("game", "speech-request")["status"] == "consumed"
        snapshot = repository.get_audience_snapshot("game")
        assert snapshot["state"]["execution_status"] == "running"
        assert snapshot["state"]["players"]["1"]["role"] == "wolf-killer-villager"
        assert repository.get_audience_events("game", after_seq=0, limit=10)["events"][0]["event_type"] == "speech"
    finally:
        repository.close()


def test_coordinator_replay_returns_same_receipt_without_duplicate_events(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    registry = builtin_registry.freeze()
    try:
        repository.create_game(
            game_id="game", name="game", config={}, execution_status="running",
            source="native", model_snapshot=[], execution_generation=1,
        )
        coordinator = DurableStepCoordinator(
            repository, CheckpointCodec(registry), AudienceProjector(),
        )
        values = dict(
            state=_state(registry.digest), orchestration={},
            expected_storage_revision=0, execution_generation=1,
            step_key="step", input_facts={"a": 1}, result_facts={"b": 2},
            domain_events=[], consumed_model_request_ids=(),
        )
        first = coordinator.commit(**values)
        second = coordinator.commit(**values)
        assert first.storage_revision == second.storage_revision == 1
        assert second.replayed is True
        assert repository.list_commits("game")[0]["step_key"] == "step"
    finally:
        repository.close()


def test_coordinator_rejects_missing_game_and_non_json_step_facts(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    registry = builtin_registry.freeze()
    coordinator = DurableStepCoordinator(
        repository, CheckpointCodec(registry), AudienceProjector(),
    )
    try:
        with pytest.raises(KeyError, match="missing"):
            coordinator.commit(
                state=GameState(game_id="missing"), orchestration={},
                expected_storage_revision=0, execution_generation=1,
                step_key="missing", input_facts={}, result_facts={},
                domain_events=[],
            )

        repository.create_game(
            game_id="game", name="game", config={}, execution_status="running",
            source="native", model_snapshot=[], execution_generation=1,
        )
        with pytest.raises(ValueError, match="canonical JSON"):
            coordinator.commit(
                state=_state(registry.digest), orchestration={},
                expected_storage_revision=0, execution_generation=1,
                step_key="invalid", input_facts={"bad": object()}, result_facts={},
                domain_events=[],
            )
    finally:
        repository.close()


def test_coordinator_reports_fault_boundaries_and_blocked_snapshot(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    registry = builtin_registry.freeze()
    observed: list[str] = []
    try:
        repository.create_game(
            game_id="game", name="game", config={},
            execution_status="recovery_blocked", source="native",
            model_snapshot=[], execution_generation=1,
        )
        repository.transition_execution(
            "game", expected=("recovery_blocked",), target="recovery_blocked",
            recovery_block_code="checkpoint_corrupt",
        )
        # A same-state transition is intentionally idempotent, so set the code
        # through a real transition before exercising the snapshot branch.
        repository.transition_execution(
            "game", expected=("recovery_blocked",), target="interrupted",
            recovery_block_code="checkpoint_corrupt",
        )
        coordinator = DurableStepCoordinator(
            repository, CheckpointCodec(registry), AudienceProjector(),
            fault_injector=observed.append,
        )
        result = coordinator.commit(
            state=_state(registry.digest), orchestration={},
            expected_storage_revision=0, execution_generation=1,
            step_key="blocked", input_facts={}, result_facts={},
            domain_events=[],
        )
        assert result.first_audience_seq is None
        assert observed == [
            "before_step_commit", "after_step_commit", "before_audience_send",
        ]
        snapshot = repository.get_audience_snapshot("game")
        assert snapshot["state"]["recovery_block_code"] == "checkpoint_corrupt"
        assert snapshot["state"]["recoverable"] is False
    finally:
        repository.close()
