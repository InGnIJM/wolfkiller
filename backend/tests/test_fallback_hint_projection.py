"""The first-night RANDOM_HINT must be validated against a projected context.

Regression for the 2026-10-26 crash: the Awakened Wolf King's
``awakened_wolf_claw_pass`` validate hook reads the *target's*
``resource_labels``. ``_fallback_target`` validated every candidate against the
unprojected base context, the hook raised ``ValueError("selected target fact is
missing")``, the provider reported it as a failed model call and the scheduler
turned it into a fatal ``PipelinePaused`` — the game died on its first night,
before the seat ever reached the model.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.agents.prompt_renderer import PromptRenderer
from app.api.websocket.ws_handler import WSManager
from app.config import PipelineMode
from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.conversation_log import ConversationLog
from app.core.effect_applier import EffectApplier, initialize_role_resources
from app.core.event_bus import EventBus
from app.core.night_flow import NightDirector
from app.core.registry_identity import frozen_registry_digest
from app.core.role_pipeline import RolePipeline
from app.core.scheduler import Scheduler
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import IssuedActionRequest, SchedulePoint
from app.roles.registry import builtin_registry
from app.services.game_service import GameService


#: The board that crashed: the Awakened Wolf King sits on 8, its pack on 5/6/9.
_BOARD = {
    1: "wolf-killer-villager",
    2: "wolf-killer-guard",
    3: "wolf-killer-villager",
    4: "wolf-killer-hunter",
    5: "wolf-killer-werewolf",
    6: "wolf-killer-werewolf",
    7: "wolf-killer-seer",
    8: "wolf-killer-awakened-wolf-king",
    9: "wolf-killer-werewolf",
    10: "wolf-killer-villager",
    11: "wolf-killer-awakened-witch",
    12: "wolf-killer-villager",
}
_PACK = {5, 6, 9}


def _night_state(snapshot) -> GameState:
    state = GameState(
        game_id="hint-projection", round_number=1,
        phase=GamePhase.NIGHT, config=GameConfig(),
    )
    for seat, role_id in _BOARD.items():
        state.players[seat] = PlayerState(
            seat, role_id, snapshot.require(role_id).camp_id,
        )
    initialize_role_resources(
        state, snapshot.specs, frozen_registry_digest(state, snapshot),
    )
    return state


def _claw_pass_request(state, snapshot) -> IssuedActionRequest:
    spec = snapshot.require("wolf-killer-awakened-wolf-king")
    contract = next(
        item for item in spec.contracts
        if item.schedule_point is SchedulePoint.NIGHT_ACTION
    )
    return IssuedActionRequest(
        8, spec.role_id, contract, state._pipeline_runtime.revision,
        1, "night", "w", "k",
    )


def _base_context(state, request, snapshot):
    return ContextProjector().project(state, request, snapshot)


def test_the_hint_is_projected_before_the_claw_pass_is_validated() -> None:
    """The old code raised here; the fix projects each candidate and returns a pack seat."""
    snapshot = builtin_registry.freeze()
    state = _night_state(snapshot)
    request = _claw_pass_request(state, snapshot)
    context = _base_context(state, request, snapshot)

    hint = GameService._fallback_target(
        request, context, "", rng=random.Random(0),
        state=state, registry=snapshot,
    )

    assert hint in _PACK


def test_an_undecidable_candidate_is_skipped_instead_of_ending_the_game() -> None:
    """Without the live state there is nothing to project, so every candidate is skipped.

    The regression surfaced exactly here: an unprojected validation hook raised
    out of the provider, which the scheduler reported as a fatal rule failure.
    A missing hint is acceptable; a dead game is not.
    """
    snapshot = builtin_registry.freeze()
    state = _night_state(snapshot)
    request = _claw_pass_request(state, snapshot)
    context = _base_context(state, request, snapshot)

    assert GameService._fallback_target(request, context, "", rng=random.Random(0)) is None


def test_the_hint_reads_the_projected_resource_labels_of_each_candidate() -> None:
    """A teammate that already received a claw is projected out of the hint set.

    This is what proves the candidate context really carries
    ``selected_target.resource_labels``: an implementation that only caught the
    exception would return no hint at all, and one that projected nothing would
    still offer the gifted teammate.
    """
    snapshot = builtin_registry.freeze()
    state = _night_state(snapshot)
    state._pipeline_runtime.role_resources.setdefault(5, {})[
        "awakened_claw_received"
    ] = 1
    request = _claw_pass_request(state, snapshot)
    context = _base_context(state, request, snapshot)

    hints = {
        GameService._fallback_target(
            request, context, "", rng=random.Random(seed),
            state=state, registry=snapshot,
        )
        for seed in range(40)
    }

    assert hints == {6, 9}


def test_the_provider_puts_the_projected_hint_into_the_rendered_context() -> None:
    """The wiring that matters: ``_command_provider`` hands the live state to the hint."""
    snapshot = builtin_registry.freeze()
    state = _night_state(snapshot)
    request = _claw_pass_request(state, snapshot)
    context = _base_context(state, request, snapshot)

    service = GameService(WSManager(), EventBus())
    engine = MagicMock()
    engine.state = state
    engine.conversation_log = ConversationLog()
    engine._rng = random.Random(0)
    service._engines[state.game_id] = engine

    renderer = MagicMock()
    renderer.render.return_value = "prompt"
    llm = MagicMock()
    llm.invoke_action.return_value = SimpleNamespace(
        payload={"action_type": "pass", "target_seat": None, "reasoning": "首夜不传"},
    )
    director = NightDirector(snapshot, lambda messages: None)
    provider = service._command_provider(
        snapshot, renderer, lambda seat: llm, director,
    )

    command = provider(request, context, 0)

    rendered_context = renderer.render.call_args.args[2]
    assert rendered_context.facts["RANDOM_HINT"] in _PACK
    assert command.action_type == "pass"


def test_the_night_action_point_completes_with_the_live_board() -> None:
    """Whole-point regression: this board's NIGHT_ACTION runs to completion.

    The incident died right after seat 2's guard answer, when the point moved on
    to seat 8's claw gift. The scheduler turns any provider exception into a
    fatal ``PipelinePaused``, so the assertion is on the point result: both
    seats reached the model and both commits landed.
    """
    snapshot = builtin_registry.freeze()
    state = _night_state(snapshot)
    service = GameService(WSManager(), EventBus())
    engine = MagicMock()
    engine.state = state
    engine.conversation_log = ConversationLog()
    engine._rng = random.Random(0)
    service._engines[state.game_id] = engine

    llm = MagicMock()
    llm.invoke_action.return_value = SimpleNamespace(
        payload={"action_type": "pass", "target_seat": None, "reasoning": "首夜空过"},
    )
    director = NightDirector(snapshot, lambda messages: None)
    scheduler = Scheduler(
        snapshot, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(),
        service._command_provider(
            snapshot, PromptRenderer(), lambda seat: llm, director,
        ),
    )
    pipeline = RolePipeline(PipelineMode.V2, None, scheduler)

    result = pipeline.execute_v2_point(state, SchedulePoint.NIGHT_ACTION)

    assert [
        (request.actor_seat, request.contract.contract_id)
        for request in result.requests
    ] == [(2, "guard_action"), (8, "awakened_wolf_claw_pass")]
    assert llm.invoke_action.call_count == 2
    assert len(result.commits) == 2
