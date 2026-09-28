"""Wolf Beauty: a werewolf that charms a good player and drags them along.

Mirrors the other extension samples: pure hook tests, registry checks and
pipeline integrations proving the charm window, the shared kill contract and the
revenge response need no role-specific engine code.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, derive_effect_id
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.registry import builtin_registry
from app.roles.werewolf import (
    WEREWOLF_KILL_CONTRACT, WEREWOLF_SPEC, validate_werewolf_action,
)
from app.roles.wolf_beauty import (
    WOLF_BEAUTY_CHARM_CONTRACT, WOLF_BEAUTY_REVENGE_CONTRACT, WOLF_BEAUTY_SPEC,
    WolfBeauty, react_wolf_beauty_revenge, resolve_wolf_beauty_action,
    validate_wolf_beauty_action, wolf_beauty_applicable,
    wolf_beauty_revenge_applicable,
)

BEAUTY, WOLF, VILLAGER, HUNTER = (
    "wolf-killer-wolf-beauty", "wolf-killer-werewolf",
    "wolf-killer-villager", "wolf-killer-hunter",
)
DRUNKARD = "wolf-killer-old-drunkard"
EVENT_ID = "event:" + "3" * 16


def context(
    *, alive: bool = True, target: int | None = 3, last_charmed: object = None,
    camp: str | None = "good", resources: tuple[str, ...] = (),
    with_fact: bool = True, actor: int = 1,
) -> ActionContext:
    contract = WOLF_BEAUTY_CHARM_CONTRACT
    facts: dict[str, object] = {"alive_seats": (1, 2, 3), "last_charmed": last_charmed}
    if with_fact and target is not None:
        selected: dict[str, object] = {"seat": target}
        if camp is not None:
            selected["camp_label"] = camp
        selected["resource_labels"] = resources
        facts["selected_target"] = selected
    return ActionContext(
        "g", 6, facts, config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=2, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=BEAUTY, actor_alive=alive, resources={"kill": 1},
        action_key="beauty:1",
    )


def revenge_context(
    *, reason: str = "exile", alive: bool = False, last_charmed: object = 3,
    target: int = 1,
) -> ActionContext:
    contract = WOLF_BEAUTY_REVENGE_CONTRACT
    return ActionContext(
        "g", 7, {"alive_seats": (2, 3), "last_charmed": last_charmed},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=2, phase="vote_resolution", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=BEAUTY, actor_alive=alive, resources={},
        action_key="beauty:revenge", source_event_id=EVENT_ID,
        trigger_event={"event_id": EVENT_ID, "type": "PLAYER_DIED",
                       "target_seat": target, "cause": reason},
        trigger_reason=reason,
    )


def command(action: str, target: int | None = None, reasoning: str = "ok") -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning=reasoning)


def test_spec_shares_the_kill_contract_and_adds_charm_and_revenge() -> None:
    assert WOLF_BEAUTY_SPEC.role_id == BEAUTY
    assert WOLF_BEAUTY_SPEC.camp_id == "werewolf"
    assert WOLF_BEAUTY_SPEC.max_count == 1
    assert WOLF_BEAUTY_SPEC.tags == {"wolf"}
    assert WOLF_BEAUTY_SPEC.initial_private_data == {"last_charmed": None}
    # The kill declaration must be byte-identical or the registry refuses the
    # shared aggregation.
    assert WOLF_BEAUTY_SPEC.contracts[0] is WEREWOLF_KILL_CONTRACT
    assert WEREWOLF_SPEC.contracts[0] is WEREWOLF_KILL_CONTRACT
    charm = WOLF_BEAUTY_CHARM_CONTRACT
    assert charm.schedule_point is SchedulePoint.NIGHT_WITCH_ACTION
    assert charm.action_types == ("charm", "pass")
    assert charm.fallback_action_type == "pass"
    assert charm.selected_target_fact_namespaces == {"camp_label", "resource_labels"}
    assert charm.per_window_limit == 1 and charm.per_game_limit is None
    assert charm.is_applicable is wolf_beauty_applicable
    assert charm.validate is validate_wolf_beauty_action
    assert charm.resolve is resolve_wolf_beauty_action
    assert "Wolf Beauty" in WOLF_BEAUTY_SPEC.instructions


def test_revenge_contract_excludes_the_forbidden_ways_out() -> None:
    revenge = WOLF_BEAUTY_REVENGE_CONTRACT
    assert revenge.schedule_point is SchedulePoint.DAWN_REACTION
    assert revenge.response_event_types == {"PLAYER_DIED"}
    assert revenge.response_reasons == {
        "wolf_kill", "poison", "exile", "hunter_shot", "knight_duel"}
    # Self-destruct and self-kill are forbidden, so they never drag anyone along.
    assert "self_explode" not in revenge.response_reasons
    assert revenge.react is react_wolf_beauty_revenge and revenge.resolve is None
    assert revenge.per_window_limit == 1
    assert revenge.is_applicable is wolf_beauty_revenge_applicable


def test_applicable_only_while_alive() -> None:
    assert wolf_beauty_applicable(context()) is True
    assert wolf_beauty_applicable(context(alive=False)) is False


def test_validate_rejects_self_wolves_consecutive_and_immune_targets() -> None:
    assert validate_wolf_beauty_action(context(), command("charm", 3)) == ()
    assert validate_wolf_beauty_action(context(), command("pass")) == ()
    assert validate_wolf_beauty_action(context(), command("sing", 3)) == ()
    missing = validate_wolf_beauty_action(context(target=None), command("charm", None))
    assert [v.code for v in missing] == ["charm_target_required"]
    self_target = validate_wolf_beauty_action(context(target=1), command("charm", 1))
    assert [v.code for v in self_target] == ["self_target"]
    same = validate_wolf_beauty_action(
        context(last_charmed=3), command("charm", 3))
    assert [v.code for v in same] == ["consecutive_charm"]
    wolf = validate_wolf_beauty_action(context(camp="werewolf"), command("charm", 3))
    assert [v.code for v in wolf] == ["charm_wolf"]
    immune = validate_wolf_beauty_action(
        context(resources=("charm_immune",)), command("charm", 3))
    assert [v.code for v in immune] == ["charm_immune"]


def test_validate_tolerates_a_context_without_target_facts() -> None:
    assert validate_wolf_beauty_action(
        context(with_fact=False), command("charm", 3)) == ()


def kill_context(
    *, actor: int = 1, role_id: str = BEAUTY, forced: bool = True,
) -> ActionContext:
    """A shared wolf-kill vote; ``forced`` mirrors the role's spec declaration."""
    contract = WEREWOLF_KILL_CONTRACT
    resources: dict[str, object] = {"kill": 1}
    if forced:
        resources["self_kill_forbidden"] = 1
    return ActionContext(
        "g", 8, {"alive_seats": (1, 2, 3)},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=1, phase="night", window_id="wolf",
        schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=role_id, actor_alive=True, resources=resources,
        action_key="wolf:1",
    )


def test_validate_rejects_the_wolf_beauty_voting_for_itself() -> None:
    violation = validate_werewolf_action(kill_context(actor=1), command("kill", 1))
    assert [v.code for v in violation] == ["self_kill_forbidden"]


def test_validate_lets_the_wolf_beauty_cut_a_teammate_or_pass() -> None:
    context_ = kill_context(actor=1)
    assert validate_werewolf_action(context_, command("kill", 2)) == ()
    assert validate_werewolf_action(context_, command("pass")) == ()
    assert validate_werewolf_action(context_, command("kill")) == ()


def test_validate_lets_every_other_wolf_vote_for_itself() -> None:
    for role_id in (WOLF, "wolf-killer-werewolf-king"):
        plain = kill_context(actor=1, role_id=role_id, forced=False)
        assert validate_werewolf_action(plain, command("kill", 1)) == ()


def test_charm_records_the_target_privately_and_publishes_it_to_the_audience() -> None:
    effects = resolve_wolf_beauty_action(context(), command("charm", 3, "带走强神"))
    assert [effect.kind for effect in effects] == [
        EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_RELATION, EffectKind.EMIT_EVENT,
        EffectKind.EMIT_EVENT,
    ]
    assert [effect.effect_id for effect in effects] == [
        derive_effect_id("beauty:1", index) for index in range(1, 5)
    ]
    assert all(effect.expected_revision == 6 for effect in effects)
    assert effects[0].payload == {"target": 1, "key": "last_charmed", "value": 3}
    assert effects[1].payload == {
        "target": 3, "relation": "charmed_by", "other_seat": 1}
    assert effects[2].payload == {
        "event_type": "WOLF_BEAUTY_CHARM",
        "payload": {"seat": 1, "target_seat": 3, "round_number": 2},
    }
    # The audience is the god view: the charm event carries the charmed seat.
    # The players' own view still never sees it — the relation above stays
    # unprojected and the reach of this event is the audience stream only.
    assert effects[2].visibility == ("PUBLIC",)
    assert effects[3].payload["event_type"] == "WOLF_BEAUTY_REASONING"
    assert effects[3].payload["payload"]["thought"] == "决定魅惑 3 号玩家：带走强神"


def test_passing_keeps_the_night_free_and_records_the_reasoning() -> None:
    effects = resolve_wolf_beauty_action(context(), command("pass", None, "先观察"))
    assert [effect.kind for effect in effects] == [EffectKind.EMIT_EVENT]
    assert effects[0].payload["payload"] == {
        "seat": 1, "action_type": "pass", "target_seat": None,
        "reasoning": "先观察", "thought": "决定本晚不魅惑：先观察",
    }


def test_revenge_drags_the_charmed_player_along() -> None:
    ctx = revenge_context()
    assert wolf_beauty_revenge_applicable(ctx) is True
    effects = react_wolf_beauty_revenge(ctx)
    assert [effect.kind for effect in effects] == [
        EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT]
    assert [effect.effect_id for effect in effects] == [
        derive_effect_id("beauty:revenge", index) for index in range(1, 3)
    ]
    assert effects[0].payload == {"target": 3, "amount": 1, "cause": "charm"}
    assert effects[1].payload == {
        "event_type": "WOLF_BEAUTY_REVENGE",
        "payload": {"seat": 1, "target_seat": 3, "cause": "exile", "round_number": 2},
    }
    assert effects[1].visibility == ("PUBLIC",)


def test_revenge_covers_every_allowed_way_out() -> None:
    for reason in ("wolf_kill", "poison", "exile", "hunter_shot", "knight_duel"):
        assert react_wolf_beauty_revenge(revenge_context(reason=reason)) != (), reason


def test_revenge_stays_silent_for_the_forbidden_and_unrelated_cases() -> None:
    # Response windows bypass ``is_applicable``, so the hook must re-check it.
    assert react_wolf_beauty_revenge(revenge_context(reason="self_explode")) == ()
    assert react_wolf_beauty_revenge(revenge_context(last_charmed=None)) == ()
    assert react_wolf_beauty_revenge(revenge_context(alive=True)) == ()
    assert react_wolf_beauty_revenge(revenge_context(target=2)) == ()
    assert react_wolf_beauty_revenge(
        replace(revenge_context(), source_event_id=None, trigger_event=None)) == ()
    assert react_wolf_beauty_revenge(revenge_context(last_charmed="3")) == ()
    assert react_wolf_beauty_revenge(revenge_context(last_charmed=0)) == ()


def test_wolf_beauty_is_registered_in_both_registries() -> None:
    assert builtin_registry.freeze().require(BEAUTY) is WOLF_BEAUTY_SPEC
    roles = builtin_registry.create_roles(
        {BEAUTY: 1, WOLF: 1}, 2, object(), lambda seat: object(),
    )
    beauty = next(role for role in roles.values() if isinstance(role, WolfBeauty))
    assert beauty.get_skills() == ["kill", "charm"]


def _scheduler(provider) -> Scheduler:
    return Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), provider,
    )


def test_charm_window_runs_after_the_wolf_vote_and_spends_nothing() -> None:
    asked: list[str] = []

    def provider(request, projected, attempt):
        asked.append(request.contract.contract_id)
        if request.contract.contract_id == "werewolf_kill":
            return command("kill", 4)
        return command("charm", 3)

    game = GameState("beauty-night", phase=GamePhase.NIGHT, round_number=1, players={
        1: PlayerState(1, BEAUTY, "werewolf"), 2: PlayerState(2, WOLF, "werewolf"),
        3: PlayerState(3, VILLAGER, "good"), 4: PlayerState(4, HUNTER, "good"),
    })
    runner = _scheduler(provider)
    runner.run_point(game, SchedulePoint.NIGHT_WOLF_VOTE)
    assert asked == ["werewolf_kill", "werewolf_kill"]
    assert game._pipeline_runtime.pending_damage == (
        {"target": 4, "amount": 1, "cause": "wolf_kill"},
    )

    charm = runner.run_point(game, SchedulePoint.NIGHT_WITCH_ACTION)
    assert asked[-1] == "wolf_beauty_charm"
    assert [e["event_type"] for e in charm.events] == [
        "WOLF_BEAUTY_CHARM", "WOLF_BEAUTY_REASONING"]
    assert game._pipeline_runtime.private_data[1]["last_charmed"] == 3
    assert game._pipeline_runtime.relations[3] == frozenset({("charmed_by", 1)})
    assert charm.events[0]["payload"] == {
        "seat": 1, "target_seat": 3, "round_number": 1,
    }


def test_charm_window_can_still_charm_on_a_no_kill_night() -> None:
    """The rules charm on any night, so an all-pass wolf vote does not block it."""
    def provider(request, projected, attempt):
        if request.contract.contract_id == "werewolf_kill":
            return command("pass")
        return command("charm", 3)

    game = GameState("beauty-nokill", phase=GamePhase.NIGHT, round_number=1, players={
        1: PlayerState(1, BEAUTY, "werewolf"), 2: PlayerState(2, WOLF, "werewolf"),
        3: PlayerState(3, VILLAGER, "good"),
    })
    runner = _scheduler(provider)
    runner.run_point(game, SchedulePoint.NIGHT_WOLF_VOTE)
    assert game._pipeline_runtime.pending_damage == ()
    charm = runner.run_point(game, SchedulePoint.NIGHT_WITCH_ACTION)
    assert game._pipeline_runtime.private_data[1]["last_charmed"] == 3
    assert [e["event_type"] for e in charm.events][0] == "WOLF_BEAUTY_CHARM"


def test_charm_rejects_a_target_that_is_immune() -> None:
    def provider(request, projected, attempt):
        if request.contract.contract_id == "werewolf_kill":
            return command("pass")
        return command("charm", 3)

    game = GameState("beauty-immune", phase=GamePhase.NIGHT, round_number=1, players={
        1: PlayerState(1, BEAUTY, "werewolf"), 2: PlayerState(2, WOLF, "werewolf"),
        3: PlayerState(3, DRUNKARD, "good"),
    })
    runner = _scheduler(provider)
    runner.run_point(game, SchedulePoint.NIGHT_WOLF_VOTE)
    charm = runner.run_point(game, SchedulePoint.NIGHT_WITCH_ACTION)
    assert game._pipeline_runtime.private_data.get(1, {}).get("last_charmed") is None
    assert 3 not in game._pipeline_runtime.relations
    assert all(
        event.get("event_type") != "WOLF_BEAUTY_CHARM" for event in charm.events
    )


@pytest.mark.asyncio
async def test_an_exiled_wolf_beauty_drags_the_charmed_player_along(tmp_path) -> None:
    """End to end through the engine: exile → DAWN_REACTION → charm damage."""
    from unittest.mock import AsyncMock, MagicMock

    from app.core.event_bus import EventBus, GameEvent as BusEvent
    from app.core.game_engine import GameEngine
    from app.core.role_runtime import initialize_role_resources
    from app.models.game import GamePhase

    def provider(request, projected, attempt):
        if request.contract.contract_id == "werewolf_kill":
            return command("kill", 3)
        if request.contract.contract_id == "wolf_beauty_charm":
            return command("charm", 4)
        if request.contract.contract_id == "hunter_shoot":
            return command("shoot", 6)
        return None

    seats = {1: BEAUTY, 2: WOLF, 3: VILLAGER, 4: HUNTER, 5: VILLAGER, 6: WOLF, 7: VILLAGER}
    bus = EventBus()
    engine = GameEngine(
        "beauty-exile", roles={seat: MagicMock() for seat in seats}, event_bus=bus,
        pipeline_scheduler=_scheduler(provider), data_dir=str(tmp_path),
    )
    engine.state = GameState("beauty-exile", phase=GamePhase.NIGHT, round_number=1, players={
        seat: PlayerState(seat, role, "werewolf" if role in (BEAUTY, WOLF) else "good")
        for seat, role in seats.items()
    })
    initialize_role_resources(
        engine.state, engine._pipeline_scheduler.registry.specs,
        engine._pipeline_scheduler.registry.digest,
    )
    engine.give_last_words = AsyncMock()
    published: list[tuple[int, str]] = []

    async def on_died(**kwargs):
        death = kwargs["death"]
        published.append((death.player_seat, death.cause))

    bus.subscribe(BusEvent.PLAYER_DIED, on_died)

    runner = engine._pipeline_scheduler
    runner.run_point(engine.state, SchedulePoint.NIGHT_WOLF_VOTE)
    runner.run_point(engine.state, SchedulePoint.NIGHT_WITCH_ACTION)
    assert engine.state._pipeline_runtime.private_data[1]["last_charmed"] == 4

    # The night kill took a plain villager; the charmed hunter is still alive and
    # therefore a visible target for the revenge damage.
    await engine._settle_and_publish()
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [
        (3, "wolf_kill")]

    published.clear()
    engine.state.round_number = 2
    # The announcing step is what the spectator reads: it must name the exiled
    # seat *and* the charmed victim, whose cause (``charm``) no cause whitelist
    # would have let through.
    from app.services.audience_projector import AudienceProjector
    from app.services.game_service import GameService

    mapped: dict[str, list[dict]] = {}
    engine._checkpoint_hook = lambda step_key: mapped.__setitem__(
        step_key, GameService._checkpoint_domain_events(engine, step_key),
    )
    assert await engine._apply_exile(1) is False

    # Exiling the Wolf Beauty drags the charmed hunter along. The exiled seat
    # itself is recorded in the history rather than republished, and the hunter
    # may not shoot on a charm death, so the charm death is the only fresh
    # announcement.
    assert published == [(4, "charm")]
    reaction = next(key for key in mapped if ":exile_reaction:2:1" in key)
    assert reaction.endswith(":exile_reaction:2:1:1-4")
    assert [
        (event["event_type"], event["payload"]["cause"])
        for event in mapped[reaction]
    ] == [("PLAYER_DIED", "exile"), ("PLAYER_DIED", "charm")]
    projected = AudienceProjector().project_events("beauty-exile", mapped[reaction])
    assert [
        (row["event_type"], row["payload"]["cause"]) for row in projected
    ] == [("death", "exile"), ("death", "charm")]
    assert engine.state.players[4].is_alive is False
    assert engine.state.players[6].is_alive is True
    assert [(d.player_seat, d.cause) for d in engine.state.death_history] == [
        (3, "wolf_kill"), (1, "exile"), (4, "charm")]
