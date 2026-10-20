"""Eclipse Maid: a wolf that devours one good player's skill and uses it that night.

Three things make this card easy to get wrong, and each is pinned against the real
``Scheduler`` rather than only through the hooks:

* the borrowed skill is usable **the same night** and **only that night** — which for
  the NIGHT_ACTION skills means their clones live at a later point, because requests
  for a point are issued before that point's members act;
* a devoured witch / hunter hands over **what the target still holds**, not a fresh set;
* devouring the Radiance Earl or a sheltered seat costs the Maid her life **after the
  next day's speeches** (``radiance_backlash``), not that night.
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.night_settlement import PROTECTION_SOURCES, resolve_delayed_deaths
from app.core.role_runtime import role_private_facts_view
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.eclipse_maid import (
    DEVOUR_CHECK_CONTRACT, DEVOUR_CONTRACT, DEVOUR_DREAM_CONTRACT, DEVOUR_GUARD_CONTRACT,
    DEVOUR_HUNTER_CONTRACT, DEVOUR_MIRROR_CONTRACT, DEVOUR_SHIELD_CONTRACT,
    DEVOUR_WITCH_CONTRACT, DEVOURED_KEY, ECLIPSE_MAID_SPEC, INHERITED_MARKERS,
    LAST_DEVOURED_KEY, POOL, EclipseMaid, _eclipse_reasoning_effect,
    devour_check_applicable, devour_dream_applicable, devour_guard_applicable,
    devour_hunter_applicable, devour_mirror_applicable, devour_shield_applicable,
    devour_witch_applicable, eclipse_applicable, resolve_eclipse_action,
    validate_devour_dream_action, validate_eclipse_action,
)
from app.roles.registry import builtin_registry

MAID_ROLE = "wolf-killer-eclipse-maid"
# seats: 1 wolf, 2 maid, 3 seer, 4 witch, 5 hunter, 6 guard, 7 villager, 8 earl,
#        9 dream weaver, 10 mirror girl
WOLF, MAID, SEER, WITCH, HUNTER, GUARD, VILL, EARL, DREAMER, MIRROR = range(1, 11)
ROLE_OF = {
    SEER: "wolf-killer-seer", WITCH: "wolf-killer-witch", HUNTER: "wolf-killer-hunter",
    GUARD: "wolf-killer-guard", VILL: "wolf-killer-villager",
    EARL: "wolf-killer-radiance-earl", DREAMER: "wolf-killer-dream-weaver",
    MIRROR: "wolf-killer-mirror-girl",
}


def cmd(action: str, target: int | None = None, *, degraded: bool = False) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="理由", degraded=degraded)


def context(contract=DEVOUR_CONTRACT, *, facts=None, resources=None, alive=True,
            round_number=2, degraded=False) -> ActionContext:
    return ActionContext(
        game_id="g", revision=3, facts={"alive_seats": (1, 2, 3, 4), **(facts or {})},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=MAID, actor_role_id=MAID_ROLE,
        actor_alive=alive, resources=resources if resources is not None else {},
        action_key="k:1", degraded=degraded,
    )


def shown(seat: int, role: str, camp: str = "good", *, resources=(), statuses=()) -> dict:
    return {
        "seat": seat, "role_label": role, "camp_label": camp,
        "resource_labels": tuple(resources), "status_labels": tuple(statuses),
    }


def held(role: str, round_taken: int = 2) -> dict:
    return {DEVOURED_KEY: {"role": role, "seat": 3, "round": round_taken}}


# ── declarations ───────────────────────────────────────────────────────────


def test_the_card_is_a_hidden_wolf_that_knows_the_pack() -> None:
    spec = ECLIPSE_MAID_SPEC
    assert spec.camp_id == "werewolf"
    assert spec.tags == frozenset({"wolf"})
    assert spec.initial_resources["no_wolf_pack"] == 1
    # The one-way variant: she reads the pack, the pack does not read her.
    assert spec.initial_resources["wolf_knows_pack"] == 1
    for marker in INHERITED_MARKERS_ALL:
        assert spec.initial_resources[marker] == 0
    assert issubclass(EclipseMaid, BaseRole)
    assert builtin_registry.require(MAID_ROLE).role_factory is EclipseMaid
    assert spec.opening_points == frozenset({"night_action"})
    assert "流光反噬" in spec.instructions


INHERITED_MARKERS_ALL = {m for markers in INHERITED_MARKERS.values() for m in markers}


def test_the_devour_acts_after_the_earl_and_before_every_borrowable_skill() -> None:
    from app.roles.dream_weaver import DREAM_WEAVER_SPEC
    from app.roles.guard import GUARD_SPEC
    from app.roles.radiance_earl import RADIANCE_SHIELD_CONTRACT

    assert RADIANCE_SHIELD_CONTRACT.order < DEVOUR_CONTRACT.order
    assert DEVOUR_CONTRACT.order < DREAM_WEAVER_SPEC.contracts[0].order
    assert DEVOUR_CONTRACT.order < GUARD_SPEC.contracts[0].order
    assert DEVOUR_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert DEVOUR_CONTRACT.fallback_action_type == "pass"


def test_every_skill_is_a_predeclared_clone_with_its_own_id() -> None:
    assert {c.contract_id for c in POOL} == {
        "devour_seer_check", "devour_witch_action", "devour_mirror_inspect",
        "devour_hunter_shoot", "devour_guard_action", "devour_dream_weave",
        "devour_radiance_shield",
    }
    assert {c.contract_id for c in ECLIPSE_MAID_SPEC.contracts} == {
        "hidden_wolf_kill", "eclipse_devour", *(c.contract_id for c in POOL),
    }


def test_night_action_skills_are_moved_past_the_devour() -> None:
    """Requests for a point are issued before its members act, so a clone left at
    ``NIGHT_ACTION`` would only ever see *last* night's devour."""
    for clone in (DEVOUR_GUARD_CONTRACT, DEVOUR_DREAM_CONTRACT, DEVOUR_SHIELD_CONTRACT):
        assert clone.schedule_point is SchedulePoint.NIGHT_WITCH_ACTION
    assert DEVOUR_CHECK_CONTRACT.schedule_point is SchedulePoint.NIGHT_SEER_ACTION
    assert DEVOUR_MIRROR_CONTRACT.schedule_point is SchedulePoint.NIGHT_SEER_ACTION
    assert DEVOUR_WITCH_CONTRACT.schedule_point is SchedulePoint.NIGHT_WITCH_ACTION
    assert DEVOUR_HUNTER_CONTRACT.schedule_point is SchedulePoint.DAWN_REACTION
    assert DEVOUR_HUNTER_CONTRACT.per_game_limit == 1, "the shot still spends its resource"


# ── the devour hook ────────────────────────────────────────────────────────


def test_the_devour_opens_from_the_second_night_while_alive() -> None:
    assert eclipse_applicable(context()) is True
    assert eclipse_applicable(context(round_number=1)) is False
    assert eclipse_applicable(context(alive=False)) is False


def test_the_maid_cannot_devour_herself_or_repeat_a_seat_on_consecutive_nights() -> None:
    assert validate_eclipse_action(context(), cmd("pass")) == ()
    assert validate_eclipse_action(context(), cmd("devour", None)) == ()
    assert validate_eclipse_action(context(), cmd("devour", SEER)) == ()
    assert [v.code for v in validate_eclipse_action(context(), cmd("devour", MAID))] == [
        "self_devour_forbidden",
    ]
    last_night = {LAST_DEVOURED_KEY: {"seat": SEER, "round": 1}}
    assert [v.code for v in validate_eclipse_action(
        context(facts=last_night), cmd("devour", SEER),
    )] == ["consecutive_devour_forbidden"]
    # A skipped night breaks the chain.
    assert validate_eclipse_action(
        context(facts=last_night, round_number=3), cmd("devour", SEER),
    ) == ()


def test_devouring_a_good_seat_silences_it_and_records_the_role() -> None:
    ctx = context(facts={"selected_target": shown(SEER, "wolf-killer-seer")})
    effects = resolve_eclipse_action(ctx, cmd("devour", SEER))
    assert [e.kind for e in effects] == [
        EffectKind.SET_PRIVATE_DATA, EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_STATUS,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload["key"] == LAST_DEVOURED_KEY
    assert effects[1].payload["value"] == {"role": "wolf-killer-seer", "seat": SEER, "round": 2}
    assert effects[2].payload == {"target": SEER, "status": "silenced"}
    assert effects[3].payload["event_type"] == "STATUS_ADDED"
    reasoning = effects[-1].payload
    assert reasoning["event_type"] == "ECLIPSE_MAID_REASONING"
    assert "wolf-killer-seer" in reasoning["payload"]["thought"]
    assert "反噬" not in reasoning["payload"]["thought"]


def test_a_devoured_witch_hands_over_only_the_potions_she_still_holds() -> None:
    both = resolve_eclipse_action(
        context(facts={"selected_target": shown(
            WITCH, "wolf-killer-witch", resources=("antidote", "poison"),
        )}), cmd("devour", WITCH),
    )
    only_poison = resolve_eclipse_action(
        context(facts={"selected_target": shown(
            WITCH, "wolf-killer-witch", resources=("poison",),
        )}), cmd("devour", WITCH),
    )

    def markers(effects):
        return {
            e.payload["resource"]: e.payload["value"]
            for e in effects if e.kind is EffectKind.SET_RESOURCE
        }

    assert markers(both) == {"antidote": 1, "poison": 1}
    assert markers(only_poison) == {"antidote": 0, "poison": 1}


def test_a_devoured_hunter_hands_over_the_gun_only_while_it_is_loaded() -> None:
    def gun(resources):
        effects = resolve_eclipse_action(
            context(facts={"selected_target": shown(
                HUNTER, "wolf-killer-hunter", resources=resources,
            )}), cmd("devour", HUNTER),
        )
        return [e.payload["value"] for e in effects if e.kind is EffectKind.SET_RESOURCE]

    assert gun(("gun",)) == [1]
    assert gun(()) == [0]


def test_devouring_a_wolf_is_a_wasted_night_that_still_counts_as_last_night() -> None:
    ctx = context(facts={"selected_target": shown(WOLF, "wolf-killer-werewolf", "werewolf")})
    effects = resolve_eclipse_action(ctx, cmd("devour", WOLF))
    assert [e.kind for e in effects] == [EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT]
    assert effects[0].payload["key"] == LAST_DEVOURED_KEY
    assert "落空" in effects[1].payload["payload"]["thought"]


@pytest.mark.parametrize(("target", "expected"), [
    (shown(EARL, "wolf-killer-radiance-earl"), True),
    (shown(VILL, "wolf-killer-villager", statuses=("radiance_shielded",)), True),
    (shown(VILL, "wolf-killer-villager"), False),
])
def test_the_radiance_backlash_lands_on_the_maid(target: dict, expected: bool) -> None:
    effects = resolve_eclipse_action(
        context(facts={"selected_target": target}), cmd("devour", target["seat"]),
    )
    damage = [e for e in effects if e.kind is EffectKind.SUBMIT_DAMAGE]
    assert bool(damage) is expected
    if expected:
        assert damage[0].payload == {"target": MAID, "amount": 1, "cause": "radiance_backlash"}
        assert "反噬" in effects[-1].payload["payload"]["thought"]


def test_passing_degrading_and_a_targetless_devour_only_leave_a_line() -> None:
    passed = resolve_eclipse_action(context(), cmd("pass"))
    assert [e.kind for e in passed] == [EffectKind.EMIT_EVENT]
    assert "不吞噬" in passed[0].payload["payload"]["thought"]
    degraded = resolve_eclipse_action(context(degraded=True), cmd("pass", degraded=True))
    assert degraded[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert len(resolve_eclipse_action(context(), cmd("devour", None))) == 1
    labelled = _eclipse_reasoning_effect(
        context(), cmd("devour", SEER), 1, role_label=None,
        expected_revision=3, source_event_id=None,
    )
    assert "落空" in labelled.payload["payload"]["thought"]


def test_a_missing_or_malformed_target_fact_is_refused() -> None:
    with pytest.raises(ValueError):
        resolve_eclipse_action(context(), cmd("devour", SEER))
    with pytest.raises(ValueError):
        resolve_eclipse_action(
            context(facts={"selected_target": shown(9, "x")}), cmd("devour", SEER),
        )
    with pytest.raises(TypeError):
        resolve_eclipse_action(
            context(facts={"selected_target": {"seat": SEER, "role_label": "x"}}),
            cmd("devour", SEER),
        )


# ── the same-night gates on the borrowed skills ────────────────────────────


@pytest.mark.parametrize(("applicable", "role", "resources"), [
    (devour_check_applicable, "wolf-killer-seer", {}),
    (devour_witch_applicable, "wolf-killer-witch", {"antidote": 1}),
    (devour_guard_applicable, "wolf-killer-guard", {}),
    (devour_mirror_applicable, "wolf-killer-mirror-girl", {}),
    (devour_dream_applicable, "wolf-killer-dream-weaver", {}),
    (devour_shield_applicable, "wolf-killer-radiance-earl", {}),
])
def test_a_skill_opens_only_on_the_night_it_was_devoured(applicable, role, resources) -> None:
    facts = held(role)
    assert applicable(context(facts=facts, resources=resources)) is True
    # Gone the next night, and never open before a devour.
    assert applicable(context(facts=facts, resources=resources, round_number=3)) is False
    assert applicable(context(facts=held("wolf-killer-villager"), resources=resources)) is False
    assert applicable(context(facts=facts, resources=resources, alive=False)) is False
    assert applicable(context(facts={DEVOURED_KEY: None}, resources=resources)) is False
    assert applicable(context(facts={}, resources=resources)) is False


def test_the_witch_clone_needs_a_potion_to_use() -> None:
    facts = held("wolf-killer-witch")
    assert devour_witch_applicable(context(facts=facts, resources={"poison": 1}))
    assert not devour_witch_applicable(context(facts=facts, resources={}))
    assert not devour_witch_applicable(
        context(facts=facts, resources={"antidote": 0, "poison": 0}),
    )


def test_the_shield_clone_keeps_the_earls_own_round_gate() -> None:
    assert not devour_shield_applicable(
        context(facts=held("wolf-killer-radiance-earl", 1), round_number=1),
    )


def test_the_hunter_clone_keeps_the_hunters_own_trigger_gate() -> None:
    from dataclasses import replace

    base = replace(
        context(DEVOUR_HUNTER_CONTRACT, facts=held("wolf-killer-hunter"), resources={"gun": 1}),
        schedule_point=SchedulePoint.DAWN_REACTION, source_event_id="e",
        trigger_event={"target_seat": MAID}, trigger_reason="wolf_kill",
    )
    assert devour_hunter_applicable(base) is True
    # The window runs after she is marked dead. Alive must not close the gun.
    assert devour_hunter_applicable(replace(base, actor_alive=False)) is True
    assert devour_hunter_applicable(replace(
        base, actor_alive=False, trigger_reason="exile",
    )) is True
    assert devour_hunter_applicable(replace(base, trigger_reason="poison")) is False
    assert devour_hunter_applicable(replace(base, resources={"gun": 0})) is False
    assert devour_hunter_applicable(replace(base, round_number=3)) is False
    assert devour_hunter_applicable(replace(base, facts={})) is False


def test_the_borrowed_weave_may_be_declined_but_not_aimed_at_herself() -> None:
    assert [v.code for v in validate_devour_dream_action(context(), cmd("dream", MAID))] == [
        "dream_self_forbidden",
    ]
    assert validate_devour_dream_action(context(), cmd("dream", VILL)) == ()
    assert validate_devour_dream_action(context(), cmd("pass")) == ()


# ── end to end through the real scheduler ──────────────────────────────────


def _state(game_id: str) -> GameState:
    roles = {WOLF: ("wolf-killer-werewolf", "werewolf"), MAID: (MAID_ROLE, "werewolf")}
    roles.update({seat: (role, "good") for seat, role in ROLE_OF.items()})
    return GameState(
        game_id, phase=GamePhase.NIGHT, round_number=1,
        players={seat: PlayerState(seat, role, camp) for seat, (role, camp) in roles.items()},
    )


class Table:
    """A scripted night: answers by contract id and records who was asked."""

    def __init__(self, state: GameState, answers: dict[str, ActionCommand]) -> None:
        self.state = state
        self.answers = answers
        self.asked: list[tuple[int, str]] = []
        self.scheduler = Scheduler(
            builtin_registry.freeze(), ContextProjector(), ActionValidator(),
            ActionResolver(), EffectApplier(), self._provider,
        )

    def _provider(self, request, projected, attempt):
        self.asked.append((request.actor_seat, request.contract.contract_id))
        return self.answers.get(request.contract.contract_id, cmd("pass"))

    def night(self, round_number: int, *points: SchedulePoint) -> None:
        self.state.round_number = round_number
        self.asked.clear()
        for point in points:
            self.scheduler.run_point(self.state, point)

    @property
    def runtime(self):
        return self.state._pipeline_runtime


_ALL_NIGHT_POINTS = (
    SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_SEER_ACTION,
    SchedulePoint.NIGHT_WITCH_ACTION,
)


def test_a_devoured_seer_is_blinded_and_the_maid_checks_the_same_night() -> None:
    state = _state("devour-seer")
    table = Table(state, {
        "eclipse_devour": cmd("devour", SEER),
        "devour_seer_check": cmd("check", WOLF),
        "seer_check": cmd("check", VILL),
    })
    table.night(2, *_ALL_NIGHT_POINTS)
    assert (MAID, "devour_seer_check") in table.asked
    assert (SEER, "seer_check") not in table.asked, "the real seer lost her skill for the night"
    own = role_private_facts_view(state, MAID, "private_checks")
    assert [dict(item) for item in own] == [{"target": WOLF, "camp": "werewolf"}]
    assert table.runtime.statuses[SEER] == {"silenced"}
    assert table.runtime.private_data[MAID][DEVOURED_KEY]["role"] == "wolf-killer-seer"

    # Tomorrow night the skill is gone, and the seer is back.
    EffectApplier().commit_night(state, round_number=2)
    assert "silenced" not in table.runtime.statuses.get(SEER, set())
    table.night(3, *_ALL_NIGHT_POINTS)
    assert (MAID, "devour_seer_check") not in table.asked
    assert (SEER, "seer_check") in table.asked


def test_the_maid_is_not_asked_for_anything_on_the_first_night() -> None:
    state = _state("first-night")
    table = Table(state, {"eclipse_devour": cmd("devour", SEER)})
    table.night(1, *_ALL_NIGHT_POINTS)
    assert not [item for item in table.asked if item[0] == MAID]


def test_a_devoured_guard_protects_at_the_last_point_of_the_night() -> None:
    state = _state("devour-guard")
    table = Table(state, {
        "eclipse_devour": cmd("devour", GUARD),
        "devour_guard_action": cmd("guard", VILL),
        "guard_action": cmd("guard", VILL),
    })
    table.night(2, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_WITCH_ACTION)
    assert (GUARD, "guard_action") not in table.asked
    assert (MAID, "devour_guard_action") in table.asked
    assert table.runtime.pending_protection == (
        {"target": VILL, "amount": 1, "source": "guard"},
    )


def test_a_devoured_dream_weaver_weaves_through_the_write_gate() -> None:
    state = _state("devour-dream")
    table = Table(state, {
        "eclipse_devour": cmd("devour", DREAMER),
        "devour_dream_weave": cmd("dream", VILL),
    })
    table.night(2, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_WITCH_ACTION)
    assert "dream" in PROTECTION_SOURCES
    assert {"target": VILL, "amount": 1, "source": "dream"} in table.runtime.pending_protection


def test_a_devoured_earl_shelters_for_the_maid_and_costs_her_the_next_day() -> None:
    state = _state("devour-earl")
    table = Table(state, {
        "eclipse_devour": cmd("devour", EARL),
        "devour_radiance_shield": cmd("shield", VILL),
    })
    table.night(2, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_WITCH_ACTION)
    assert {"target": VILL, "amount": 1, "source": "radiance"} in table.runtime.pending_protection
    # The skill was taken at the cost of the backlash — pending, not yet dead.
    assert {"target": MAID, "amount": 1, "cause": "radiance_backlash"} in (
        table.runtime.pending_damage
    )

    result = EffectApplier().commit_night(state, round_number=2)
    assert result is not None
    assert state.players[MAID].is_alive is True, "the backlash waits for the day"
    assert state.death_history == []
    assert table.runtime.statuses[MAID] == {"radiance_marked", "delayed_death"}
    deaths, cleared = resolve_delayed_deaths(table.runtime.statuses, 3)
    assert deaths == ({"seat": MAID, "cause": "radiance_backlash", "round_number": 3},)
    assert set(cleared[MAID]) == {"radiance_marked", "delayed_death"}


def test_devouring_a_sheltered_seat_is_punished_through_the_projected_mark() -> None:
    state = _state("devour-sheltered")
    table = Table(state, {
        "radiance_shield": cmd("shield", VILL),
        "eclipse_devour": cmd("devour", VILL),
    })
    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert {"target": MAID, "amount": 1, "cause": "radiance_backlash"} in (
        table.runtime.pending_damage
    )


def test_devouring_an_unsheltered_villager_is_free() -> None:
    state = _state("devour-villager")
    table = Table(state, {"eclipse_devour": cmd("devour", VILL)})
    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert not table.runtime.pending_damage
    assert table.runtime.statuses[VILL] == {"silenced"}


def test_a_devoured_witch_poisons_with_the_one_vial_she_had_left() -> None:
    state = _state("devour-witch")
    table = Table(state, {
        "eclipse_devour": cmd("devour", WITCH),
        "devour_witch_action": cmd("poison", VILL),
    })
    table.night(1, SchedulePoint.NIGHT_ACTION)  # builds the runtime
    table.runtime.role_resources[WITCH]["antidote"] = 0
    table.night(2, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_WITCH_ACTION)
    assert (WITCH, "witch_action") not in table.asked
    assert (MAID, "devour_witch_action") in table.asked
    assert table.runtime.role_resources[MAID]["antidote"] == 0
    assert table.runtime.role_resources[MAID]["poison"] == 0, "the vial is spent"
    assert {"target": VILL, "amount": 1, "cause": "poison"} in table.runtime.pending_damage


def test_a_borrowed_poison_names_the_maid_as_the_caster() -> None:
    """克隆仍走女巫的 resolve，outcome 必须带上侍女自己的座位。"""
    from app.roles.witch import resolve_witch_action

    effects = resolve_witch_action(
        context(DEVOUR_WITCH_CONTRACT, resources={"poison": 1}),
        cmd("poison", VILL),
    )
    poison = next(item for item in effects if item.payload.get("event_type") == "WITCH_POISON")
    assert poison.payload["payload"] == {"target_seat": VILL, "seat": MAID}


def test_a_devoured_hunter_hands_over_the_loaded_gun() -> None:
    state = _state("devour-hunter")
    table = Table(state, {"eclipse_devour": cmd("devour", HUNTER)})
    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert table.runtime.role_resources[MAID]["gun"] == 1


def _resume_death(table: Table, seat: int, cause: str):
    """Open the dawn reaction on a death that has already been marked."""
    from app.core.effect_applier import CommitResult
    from app.core.point_journal import (
        PendingEvent, PointCheckpoint, PointKey, WorkCursor, point_journal,
    )
    from app.core.registry_identity import frozen_registry_digest

    state = table.state
    state.players[seat].is_alive = False
    registry = table.scheduler.registry
    identity = frozen_registry_digest(state, registry)
    events = ({"event_type": "PLAYER_DIED", "payload": {"seat": seat, "cause": cause}},)
    commit = CommitResult("death:seed", (), 0, events, "d")
    key = PointKey(
        state.game_id, state.round_number, Scheduler.point_phase(state, ""),
        SchedulePoint.DAWN_REACTION, identity,
    )
    point_journal(state).put(key, PointCheckpoint(
        (), (), (commit,), events, (), (PendingEvent(0, 0, 0),),
        WorkCursor("response", 0, 0), work_count=0,
    ))
    table.asked.clear()
    return table.scheduler.run_point(state, SchedulePoint.DAWN_REACTION)


@pytest.mark.parametrize("cause", ["exile", "wolf_kill"])
def test_a_maid_without_a_hunters_gun_is_not_asked_to_shoot(cause: str) -> None:
    state = _state(f"no-gun-{cause}")
    table = Table(state, {"devour_hunter_shoot": cmd("shoot", VILL)})
    table.night(2, SchedulePoint.NIGHT_ACTION)
    result = _resume_death(table, MAID, cause)
    assert (MAID, "devour_hunter_shoot") not in table.asked
    assert "HUNTER_REASONING" not in [event["event_type"] for event in result.events]


def test_a_dead_maid_who_devoured_a_hunter_tonight_is_asked_to_shoot() -> None:
    state = _state("dead-gun")
    table = Table(state, {
        "eclipse_devour": cmd("devour", HUNTER),
        "devour_hunter_shoot": cmd("pass"),
    })
    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert table.runtime.role_resources[MAID]["gun"] == 1
    result = _resume_death(table, MAID, "exile")
    assert (MAID, "devour_hunter_shoot") in table.asked
    assert "HUNTER_REASONING" in [event["event_type"] for event in result.events]


def test_devouring_a_wolf_changes_nothing_but_the_memory() -> None:
    state = _state("devour-wolf")
    table = Table(state, {"eclipse_devour": cmd("devour", WOLF)})
    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert not table.runtime.statuses.get(WOLF)
    assert DEVOURED_KEY not in table.runtime.private_data[MAID]
    assert table.runtime.private_data[MAID][LAST_DEVOURED_KEY] == {"seat": WOLF, "round": 2}


def test_devouring_an_already_sealed_seat_degrades_instead_of_failing_the_game() -> None:
    state = _state("devour-sealed")
    table = Table(state, {"eclipse_devour": cmd("devour", SEER)})
    table.night(1, SchedulePoint.NIGHT_ACTION)  # builds the runtime
    table.runtime.statuses[SEER] = {"silenced"}
    table.night(2, SchedulePoint.NIGHT_ACTION)
    # The write gate refuses a second seal: the Maid's turn falls back to a pass.
    assert DEVOURED_KEY not in table.runtime.private_data.get(MAID, {})
    assert table.runtime.statuses[SEER] == {"silenced"}
