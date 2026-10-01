"""Wiring coverage for prompt capture inside ``GameService``.

The sink itself is unit-tested in ``app/core/test/test_prompt_capture.py``. What
matters here is the *wiring*:

* which role callables may receive a sink at all (``_accepts_capture`` /
  ``_capture_adapter`` adapt historical three-argument doubles),
* that one game keeps one capture handle until it is released,
* that the durable wrappers skip the flush when capture is off or the result was
  a replay, so a resumed game never duplicates corpus lines.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.services.game_service as service_module
from app.agents.llm_client import env_default_client_config
from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.core.prompt_capture import PromptSink
from app.models.contracts import (
    AcceptedAction, ActionCommand, ActionContract, ActionRequest,
)
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.persistence.repository import GameRepository
from app.services.game_service import GameService
from app.services.model_invocation_service import InvocationResult

CAPTURE_ENV = "PROMPT_CAPTURE_ENABLED"


def _state_with_players(game_id: str = "game") -> GameState:
    state = GameState(game_id=game_id, config=GameConfig(
        role_counts={"wolf-killer-villager": 2}, reveal_on_death=True,
    ))
    state.round_number = 2
    state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", "good")
        for seat in (1, 2)
    }
    return state


def _capture_lines(tmp_path):
    """Lines in this game's capture file, or ``None`` when it was never opened.

    ``0`` and ``None`` mean different things: the switch decides file creation
    once per game, so a game whose replay produced no fresh request still has the
    (empty) file.
    """
    path = tmp_path / "games" / "game" / "llm_prompts.jsonl"
    if not path.exists():
        return None
    return len(path.read_text(encoding="utf-8").splitlines())


# ── sink 适配层 ────────────────────────────────────────────────


def test_accepts_capture_is_false_without_an_introspectable_signature():
    # A non-callable has no signature at all; the adapter must not explode.
    assert service_module._accepts_capture(object()) is False


def test_accepts_capture_detects_the_sink_parameter_and_var_keyword():
    async def takes_capture(state, log, payload, capture=None):  # pragma: no cover
        raise AssertionError("never called")

    async def takes_kwargs(state, log, payload, **kwargs):  # pragma: no cover
        raise AssertionError("never called")

    async def takes_neither(state, log, payload):  # pragma: no cover
        raise AssertionError("never called")

    assert service_module._accepts_capture(takes_capture) is True
    assert service_module._accepts_capture(takes_kwargs) is True
    assert service_module._accepts_capture(takes_neither) is False


@pytest.mark.asyncio
async def test_capture_adapter_only_forwards_the_sink_where_it_is_accepted():
    seen: list[tuple] = []

    async def takes_neither(state, log, payload):
        seen.append(("three", state, log, payload))
        return "plain"

    async def takes_capture(state, log, payload, capture=None):
        seen.append(("sink", capture))
        return capture

    sink = PromptSink()

    assert await service_module._capture_adapter(takes_neither)("s", "l", "p", sink) == "plain"
    assert await service_module._capture_adapter(takes_capture)("s", "l", "p", sink) is sink
    assert seen == [("three", "s", "l", "p"), ("sink", sink)]


# ── 每局一个句柄 ───────────────────────────────────────────────


def test_prompt_capture_handle_latches_per_game_and_closes_once(tmp_path, monkeypatch):
    monkeypatch.setenv(CAPTURE_ENV, "1")
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        capture = service._prompt_capture("game")
        assert capture is not None
        assert (tmp_path / "games" / "game" / "llm_prompts.jsonl").exists()

        # Flipping the switch mid-game must not split the corpus: the handle that
        # was already decided stays in place.
        monkeypatch.setenv(CAPTURE_ENV, "0")
        assert service._prompt_capture("game") is capture

        service._close_prompt_capture("game")
        assert capture._fd is None
        service._close_prompt_capture("game")  # idempotent

        # The capture file is itself the marker: a game that already has a corpus
        # keeps capturing, so the next decision hands back a fresh handle instead
        # of silently stopping half-way through the file.
        reopened = service._prompt_capture("game")
        assert reopened is not None
        assert reopened is not capture
        service._close_prompt_capture("game")
    finally:
        asyncio.run(service.aclose())


def test_prompt_capture_is_skipped_when_the_switch_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv(CAPTURE_ENV, "0")
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        assert service._prompt_capture("game") is None
        assert not (tmp_path / "games" / "game").exists()
    finally:
        asyncio.run(service.aclose())


# ── 白天两条链路的 flush 门禁 ──────────────────────────────────


@pytest.mark.parametrize(
    ("capture_enabled", "reused", "expected_lines"),
    [
        ("0", False, None),   # capture off: no file is opened at all
        ("1", True, 0),       # durable replay: the file stays empty
        ("1", False, 2),      # speech + action, one line each
    ],
)
@pytest.mark.asyncio
async def test_speech_and_action_flush_respects_capture_and_replay(
    tmp_path, monkeypatch, capture_enabled, reused, expected_lines,
) -> None:
    monkeypatch.setenv(CAPTURE_ENV, capture_enabled)
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="running",
        source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    service._durable_contexts["game"] = {
        "storage_revision": 0, "execution_generation": 2,
    }

    class Invocations:
        async def invoke(self, **kwargs):
            normalized, _usage = await kwargs["provider_call"]()
            return InvocationResult(
                normalized_result=normalized, reused=reused, attempt_id=None,
            )

    service._model_invocations = Invocations()

    def record(capture, tool_name):
        """Stand-in for ``BaseRole``: only a sink-aware role fills the record."""
        if capture is not None:
            capture.record(
                [
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "rendered prompt"},
                ],
                tool_schema={"type": "object", "required": ["text"]},
                tool_name=tool_name,
            )

    async def speak(_state, _log, _context, capture=None):
        record(capture, "speak")
        return "public words"

    contract = ActionContract(
        contract_id="vote", phase=GamePhase.VOTE_CASTING,
        action_types=("vote",), actions_requiring_target=frozenset({"vote"}),
        resolution_priority=1, fallback_action_type="vote",
    )
    request = ActionRequest(
        actor_seat=1, role_id="wolf-killer-villager", contract=contract,
        phase=GamePhase.VOTE_CASTING, round_id=2, idempotency_key="vote:2:1",
    )

    async def request_action(_state, _log, original_request, capture=None):
        record(capture, original_request.contract.resolved_tool_name)
        return AcceptedAction(
            request=original_request,
            command=ActionCommand(action_type="vote", target_seat=2, reasoning="ok"),
            technical_failure_code="retry", timeout_type="soft",
        )

    role = SimpleNamespace(
        speak=speak, request_action=request_action,
        llm_client=SimpleNamespace(
            provider_profile=SimpleNamespace(profile_id="profile"), model_name="model",
        ),
    )
    state = _state_with_players()
    state.phase = GamePhase.VOTE_CASTING
    try:
        service._instrument_roles("game", {1: role})
        assert await role.speak(state, [], "day") == "public words"
        accepted = await role.request_action(state, [], request)
        assert accepted.command.target_seat == 2
        assert _capture_lines(tmp_path) == expected_lines
    finally:
        await service.aclose()
        repository.close()


# ── 夜间指挥链路的 flush 门禁 ──────────────────────────────────


@pytest.mark.parametrize(
    ("capture_enabled", "reused", "expected_lines"),
    [
        ("0", False, None),
        ("1", True, 0),
        ("1", False, 1),
    ],
)
def test_recovered_night_command_flush_respects_capture_and_replay(
    tmp_path, monkeypatch, capture_enabled, reused, expected_lines,
) -> None:
    monkeypatch.setenv(CAPTURE_ENV, capture_enabled)
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    state.registry_digest = service._registry_snapshot.digest
    service._durable_contexts[state.game_id] = {
        "storage_revision": 1, "execution_generation": 2,
    }
    config = env_default_client_config()

    class Client:
        provider_profile = SimpleNamespace(profile_id="profile")
        model_name = "model"

        def __init__(self, *, config):
            self.config = config

        def invoke_json(self, messages, *, tool_name, schema):
            return SimpleNamespace(
                payload={"action_type": "pass", "target_seat": None}, usage=None,
            )

    class Invocations:
        @staticmethod
        def request_digest(_request):
            return "abcdef0123456789"

        @staticmethod
        def invoke_sync(**kwargs):
            normalized, _usage = kwargs["provider_call"]()
            return InvocationResult(
                normalized_result=normalized, reused=reused, attempt_id=None,
            )

    class Engine:
        def __init__(self, **kwargs):
            self.director = kwargs["director"]
            self._rng = service_module.random.Random(7)
            self.game_logger = SimpleNamespace(log_llm_call=lambda *a, **k: None)

        def load_restored_state(self, state, orchestration, codec):
            self.loaded = (state, orchestration, codec)

    service._model_invocations = Invocations()
    monkeypatch.setattr(service_module, "LLMClient", Client)
    monkeypatch.setattr(service_module, "GameEngine", Engine)
    monkeypatch.setattr(
        service, "_resolve_recovery_model_configs",
        lambda *_args: {1: config, 2: config},
    )
    try:
        engine, clients = service._build_recovered_engine(
            state, {"position": "night"}, {"config": {}},
        )
        engine.director._invoke(
            [{"content": "prompt"}], "werewolf_kill", {"type": "object"}, 1,
        )
        assert _capture_lines(tmp_path) == expected_lines
        assert set(clients) == {1, 2}
    finally:
        asyncio.run(service.aclose())
