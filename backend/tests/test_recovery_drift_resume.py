"""A game whose role declarations moved on must still be resumable.

The point of the frozen registry identity is that committed work is never
re-keyed. What is left is a judgement call: the game was set up under an older
declaration, so continuing it changes the rules it plays by. That call belongs
to the user — one explicit confirmation — and never to a silent reset: the
resume adopts the *marker* only, so a spent potion stays spent and a declaration
that grew does not hand out the difference.
"""

from __future__ import annotations

import json
import sqlite3
import hashlib
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.core.role_runtime import (
    initialize_role_resources, resource_declaration_marker,
)
from app.persistence.checkpoint_codec import CheckpointCodec
from app.persistence.repository import GameRepository
from app.roles.registry import RegistrySnapshot
from app.services.game_service import GameService

DRIFTED_REGISTRY = "c" * 64
VILLAGER = "wolf-killer-villager"
WITCH = "wolf-killer-witch"
WOLF_BEAUTY = "wolf-killer-wolf-beauty"


def _drifted_snapshot(
    service: GameService, *, drop: str | None = None,
    resources: dict[str, dict[str, int]] | None = None,
) -> RegistrySnapshot:
    """The live registry as it would look after an unrelated declaration change."""
    overrides = resources or {}
    specs = {}
    for role_id, spec in service._registry_snapshot.specs.items():
        if role_id == drop:
            continue
        if role_id in overrides:
            spec = replace(
                spec, initial_resources=MappingProxyType(dict(overrides[role_id])),
            )
        specs[role_id] = spec
    return RegistrySnapshot(specs, DRIFTED_REGISTRY)


def _install(service: GameService, registry: RegistrySnapshot) -> None:
    """Make ``service`` the drifted deployment, not the one that froze the game."""
    service._registry_snapshot = registry
    service._checkpoint_codec = CheckpointCodec(registry)


def _write_document(repository: GameRepository, game_id: str, document: dict) -> None:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE game_checkpoints SET checkpoint_json=?,checkpoint_digest=? "
            "WHERE game_id=?",
            (raw, hashlib.sha256(raw.encode()).hexdigest(), game_id),
        )


def _seed_resource_setup(
    repository: GameRepository, game_id: str, snapshot: RegistrySnapshot,
) -> None:
    """Store the checkpoint as it looks once a scheduling point has run.

    Role resources are granted by the first ``Scheduler.issue``, so a game that
    stopped before its first night has nothing set up yet. Encoding the state
    the same way the durable commit does keeps the document honest.
    """
    document = repository.load_checkpoint(game_id)["checkpoint"]
    codec = CheckpointCodec(snapshot)
    state, orchestration = codec.decode(document)
    initialize_role_resources(state, snapshot.specs, state.registry_digest)
    _write_document(
        repository, game_id, codec.encode(state, orchestration=orchestration),
    )


@pytest.fixture
def stopped_client(monkeypatch):
    import app.services.game_service as service_module
    from app.core.game_engine import GameEngine

    class FakeClient:
        def __init__(self, *, config) -> None:
            self.config = config

        async def aclose(self) -> None:
            pass

    async def stop_after_initial_checkpoint(self) -> None:
        self._running = False

    monkeypatch.setattr(service_module, "LLMClient", FakeClient)
    monkeypatch.setattr(GameEngine, "_game_loop", stop_after_initial_checkpoint)


async def _interrupted_game(tmp_path, repository, role_counts) -> str:
    first = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    game_id = await first.create_game(role_counts=role_counts)
    await first._tasks[game_id]
    repository.transition_execution(
        game_id, expected=("running",), target="interrupted",
    )
    await first.aclose()
    return game_id


def _seat_of(state, role: str) -> int:
    return next(seat for seat, player in state.players.items() if player.role == role)


def _recovery_audit(tmp_path, game_id: str) -> dict:
    lines = [
        json.loads(line)
        for line in (Path(tmp_path) / "games" / game_id / "game.log")
        .read_text(encoding="utf-8").splitlines()
    ]
    return [line for line in lines if line["operation"] == "recovery"][-1]


@pytest.mark.asyncio
async def test_a_moved_declaration_asks_for_confirmation_then_resumes(
    tmp_path, monkeypatch, stopped_client,
) -> None:
    from app.core.game_engine import GameEngine

    repository = GameRepository(tmp_path)
    game_id = await _interrupted_game(
        tmp_path, repository, {WITCH: 1, VILLAGER: 3},
    )
    second = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    _seed_resource_setup(repository, game_id, second._registry_snapshot)
    drifted = _drifted_snapshot(second, resources={WITCH: {"antidote": 2, "poison": 1}})
    _install(second, drifted)
    observed: list = []

    async def restored_loop(self, state, orchestration, codec) -> None:
        observed.append(state)
        self._running = False

    monkeypatch.setattr(GameEngine, "restore", restored_loop)
    try:
        with pytest.raises(ValueError, match="role_declaration_drift"):
            await second.recover_game(game_id)
        # A refusal to continue silently is not a verdict: the game keeps its
        # status so the user can answer and try again.
        assert repository.get_game(game_id)["execution_status"] == "interrupted"
        assert repository.get_game(game_id)["recovery_block_code"] is None

        info = await second.recover_game(game_id, force=True)
        await second._tasks[game_id]
        assert info["execution_status"] == "running"
        state = observed[0]
        assert state.registry_digest != drifted.digest
        # Only the setup marker moved: the seats keep the resources they had, so
        # the antidote the declaration now promises twice is still granted once.
        assert state._pipeline_runtime.role_resources[_seat_of(state, WITCH)] == {
            "antidote": 1, "poison": 1,
        }
        assert state._pipeline_runtime.resource_setup_digest == (
            resource_declaration_marker(state, drifted.specs, state.registry_digest)
        )
        # Without the adoption the next scheduling point would refuse the game
        # with "role resource configuration changed".
        initialize_role_resources(state, drifted.specs, state.registry_digest)
        assert state._pipeline_runtime.role_resources[_seat_of(state, WITCH)] == {
            "antidote": 1, "poison": 1,
        }

        audit = _recovery_audit(tmp_path, game_id)
        assert audit["data"]["level"] == "drift"
        assert audit["data"]["code"] == "role_declaration_drift"
        assert audit["data"]["forced"] is True
        assert audit["data"]["from_status"] == "interrupted"
        assert audit["data"]["execution_generation"] == 2
        assert audit["data"]["live_registry_digest"] == drifted.digest
        assert audit["data"]["frozen_registry_digest"] == state.registry_digest
    finally:
        await second.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_a_declaration_added_later_is_set_up_on_the_next_point(
    tmp_path, monkeypatch, stopped_client,
) -> None:
    from app.core.game_engine import GameEngine

    repository = GameRepository(tmp_path)
    game_id = await _interrupted_game(
        tmp_path, repository, {WOLF_BEAUTY: 1, VILLAGER: 3},
    )
    second = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    drifted = _drifted_snapshot(
        second, resources={WOLF_BEAUTY: {"self_kill_forbidden": 1, "charm_marker": 1}},
    )
    _install(second, drifted)
    observed: list = []

    async def restored_loop(self, state, orchestration, codec) -> None:
        observed.append(state)
        self._running = False

    monkeypatch.setattr(GameEngine, "restore", restored_loop)
    try:
        # Nothing was set up for this game, so there is nothing to preserve and
        # nothing to confirm: the current declaration is applied by the next
        # scheduling point instead.
        info = await second.recover_game(game_id)
        await second._tasks[game_id]
        assert info["execution_status"] == "running"
        state = observed[0]
        assert state._pipeline_runtime.resource_setup_digest is None
        initialize_role_resources(state, drifted.specs, state.registry_digest)
        assert state._pipeline_runtime.role_resources[_seat_of(state, WOLF_BEAUTY)] == {
            "self_kill_forbidden": 1, "charm_marker": 1,
        }
        audit = _recovery_audit(tmp_path, game_id)
        assert audit["data"]["level"] == "compatible"
        assert audit["data"]["forced"] is False
    finally:
        await second.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_prompt_drift_is_recorded_and_the_game_continues(
    tmp_path, monkeypatch, stopped_client,
) -> None:
    import app.services.game_service as service_module
    from app.core.game_engine import GameEngine

    repository = GameRepository(tmp_path)
    game_id = await _interrupted_game(tmp_path, repository, {VILLAGER: 2})
    second = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )

    async def restored_loop(self, state, orchestration, codec) -> None:
        self._running = False

    monkeypatch.setattr(GameEngine, "restore", restored_loop)
    monkeypatch.setattr(service_module, "_prompt_digest", lambda: "rewritten")
    try:
        info = await second.recover_game(game_id)
        await second._tasks[game_id]
        assert info["execution_status"] == "running"
        audit = _recovery_audit(tmp_path, game_id)
        assert audit["data"]["level"] == "exact"
        assert audit["data"]["warnings"] == ["prompt_drift"]
        assert audit["data"]["forced"] is False
    finally:
        await second.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_a_game_whose_role_disappeared_is_refused_even_when_forced(
    tmp_path, stopped_client,
) -> None:
    repository = GameRepository(tmp_path)
    game_id = await _interrupted_game(tmp_path, repository, {VILLAGER: 2})
    second = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    _install(second, _drifted_snapshot(second, drop=VILLAGER))
    try:
        with pytest.raises(ValueError, match="registry_incompatible"):
            await second.recover_game(game_id, force=True)
        record = repository.get_game(game_id)
        assert record["execution_status"] == "recovery_blocked"
        assert record["recovery_block_code"] == "registry_incompatible"
    finally:
        await second.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_a_blocked_game_can_be_retried_once_the_cause_is_gone(
    tmp_path, monkeypatch, stopped_client,
) -> None:
    from app.core.game_engine import GameEngine

    repository = GameRepository(tmp_path)
    game_id = await _interrupted_game(tmp_path, repository, {VILLAGER: 2})
    saved = repository.load_checkpoint(game_id)["checkpoint"]
    second = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )

    async def restored_loop(self, state, orchestration, codec) -> None:
        self._running = False

    monkeypatch.setattr(GameEngine, "restore", restored_loop)
    try:
        monkeypatch.setattr(repository, "load_checkpoint", lambda _game_id: None)
        with pytest.raises(ValueError, match="checkpoint_missing"):
            await second.recover_game(game_id)
        assert repository.get_game(game_id)["execution_status"] == "recovery_blocked"

        # A block is a diagnosis, not a verdict: once the cause is gone the game
        # is recoverable again, and the retry re-evaluates it from scratch.
        monkeypatch.setattr(
            repository, "load_checkpoint",
            lambda _game_id: {"checkpoint": saved, "storage_revision": 0},
        )
        assert second.get_execution_info(game_id)["recoverable"] is True
        info = await second.recover_game(game_id)
        await second._tasks[game_id]
        assert info["execution_status"] == "running"
        assert repository.get_game(game_id)["recovery_block_code"] is None
    finally:
        await second.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_a_withdrawn_declaration_keeps_the_established_resources(
    tmp_path, monkeypatch, stopped_client,
) -> None:
    """A declaration that moved is still a drift once resources were set up.

    Emptied out, the witch's declaration has no marker to adopt, so the one the
    setup stamped stands — and the seats keep the resources they were granted.
    """
    from app.core.game_engine import GameEngine

    repository = GameRepository(tmp_path)
    game_id = await _interrupted_game(
        tmp_path, repository, {WITCH: 1, VILLAGER: 3},
    )
    second = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    _seed_resource_setup(repository, game_id, second._registry_snapshot)
    drifted = _drifted_snapshot(second, resources={WITCH: {}})
    _install(second, drifted)
    observed: list = []

    async def restored_loop(self, state, orchestration, codec) -> None:
        observed.append(state)
        self._running = False

    monkeypatch.setattr(GameEngine, "restore", restored_loop)
    try:
        with pytest.raises(ValueError, match="role_declaration_drift"):
            await second.recover_game(game_id)
        await second.recover_game(game_id, force=True)
        await second._tasks[game_id]
        state = observed[0]
        # The declaration declares nothing now, so there is no marker to adopt:
        # the established one stands and the next point leaves the seats alone.
        assert state._pipeline_runtime.resource_setup_digest is not None
        assert state._pipeline_runtime.role_resources[_seat_of(state, WITCH)] == {
            "antidote": 1, "poison": 1,
        }
        assert initialize_role_resources(
            state, drifted.specs, state.registry_digest,
        ) is None
    finally:
        await second.aclose()
        repository.close()
