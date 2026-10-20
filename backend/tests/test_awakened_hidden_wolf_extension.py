"""Awakened Hidden Wolf: an out-of-pack wolf that imitates one player.

Two clocks, one card: the **identity** the imitation grants is read by every
inspection from the moment the night resolves, while the **skill** only opens on
the following night. Both are exercised against the real ``Scheduler`` — the
disguise reaching a Mirror Girl and a Seer, the copied skills each opening the next
night and being spent — and the gates are pinned at hook level.
"""

from __future__ import annotations

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.role_runtime import role_private_facts_view
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.awakened_hidden_wolf import (
    AWAKENED_HIDDEN_WOLF_SPEC, GRANTS, IMITATE_CONTRACT, MIMIC_CHECK_CONTRACT,
    MIMIC_GUARD_CONTRACT, MIMIC_HUNTER_CONTRACT, MIMIC_INSPECT_CONTRACT,
    MIMIC_WITCH_CONTRACT, AwakenedHiddenWolf, imitate_applicable,
    mimic_check_applicable, mimic_guard_applicable, mimic_inspect_applicable,
    mimic_witch_applicable, resolve_imitate_action, validate_imitate_action,
)
from app.roles.base import BaseRole
from app.roles.hunter import hunter_applicable
from app.roles.registry import builtin_registry

HIDDEN = "wolf-killer-awakened-hidden-wolf"
# seats: 1 wolf, 2 hidden wolf, 3 seer, 4 mirror girl, 5 witch, 6 hunter,
#        7 guard, 8 villager
WOLF, AHW, SEER, MIRROR, WITCH, HUNTER, GUARD, VILL = range(1, 9)


def cmd(action: str, target: int | None = None, *, degraded: bool = False) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="理由", degraded=degraded)


def context(contract=IMITATE_CONTRACT, *, facts=None, resources=None, alive=True,
            round_number=1, degraded=False) -> ActionContext:
    return ActionContext(
        game_id="g", revision=3, facts={"alive_seats": (1, 2, 3), **(facts or {})},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=round_number, phase="night", window_id="w",
        schedule_point=contract.schedule_point, actor_seat=AHW, actor_role_id=HIDDEN,
        actor_alive=alive, resources=resources if resources is not None else {"imitate": 1},
        action_key="k:1", degraded=degraded,
    )


def shown(seat: int, role: str, camp: str) -> dict:
    return {"seat": seat, "role_label": role, "camp_label": camp}


# ── declarations ───────────────────────────────────────────────────────────


def test_the_card_is_a_hidden_wolf_with_a_predeclared_skill_pool() -> None:
    spec = AWAKENED_HIDDEN_WOLF_SPEC
    assert spec.camp_id == "werewolf"
    assert spec.tags == frozenset({"wolf"})
    assert spec.initial_resources["no_wolf_pack"] == 1
    assert spec.initial_resources["imitate"] == 1
    # Every copied skill starts closed: nothing can fire before an imitation.
    for marker in ("mimic_check", "mimic_guard", "mimic_inspect", "gun", "antidote", "poison"):
        assert spec.initial_resources[marker] == 0
    assert {c.contract_id for c in spec.contracts} == {
        "hidden_wolf_kill", "hidden_wolf_imitate", "mimic_seer_check",
        "mimic_witch_action", "mimic_guard_action", "mimic_hunter_shoot",
        "mimic_mirror_inspect",
    }
    assert issubclass(AwakenedHiddenWolf, BaseRole)
    assert builtin_registry.require(HIDDEN).role_factory is AwakenedHiddenWolf


def test_every_copyable_skill_keeps_the_originals_point_and_hooks() -> None:
    from app.roles.hunter import HUNTER_SPEC
    from app.roles.seer import SEER_SPEC
    from app.roles.witch import WITCH_SPEC

    for clone, original in (
        (MIMIC_CHECK_CONTRACT, SEER_SPEC.contracts[0]),
        (MIMIC_WITCH_CONTRACT, WITCH_SPEC.contracts[0]),
        (MIMIC_HUNTER_CONTRACT, HUNTER_SPEC.contracts[0]),
    ):
        assert clone.schedule_point is original.schedule_point
        assert clone.resolve is original.resolve
        assert clone.contract_id != original.contract_id
    # The one-shot shot still consumes its resource, so it passes the registry gate.
    assert MIMIC_HUNTER_CONTRACT.per_game_limit == 1
    assert MIMIC_GUARD_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert MIMIC_INSPECT_CONTRACT.schedule_point is SchedulePoint.NIGHT_SEER_ACTION


def test_the_grants_name_exactly_the_skills_the_card_lists() -> None:
    assert set(GRANTS) == {
        "wolf-killer-seer", "wolf-killer-witch", "wolf-killer-guard",
        "wolf-killer-hunter", "wolf-killer-mirror-girl",
    }
    # Every marker a grant sets is declared closed on the spec.
    for markers in GRANTS.values():
        assert set(markers) <= set(AWAKENED_HIDDEN_WOLF_SPEC.initial_resources)


# ── the imitation hook ─────────────────────────────────────────────────────


def test_the_imitation_opens_once_and_only_for_a_living_wolf() -> None:
    assert imitate_applicable(context()) is True
    assert imitate_applicable(context(resources={"imitate": 0})) is False
    assert imitate_applicable(context(alive=False)) is False


def test_a_player_cannot_imitate_itself() -> None:
    assert validate_imitate_action(context(), cmd("imitate", VILL)) == ()
    assert validate_imitate_action(context(), cmd("pass")) == ()
    assert [v.code for v in validate_imitate_action(context(), cmd("imitate", AHW))] == [
        "self_imitate_forbidden",
    ]


def test_imitating_a_skill_holder_spends_the_card_and_opens_its_markers() -> None:
    ctx = context(facts={"selected_target": shown(WITCH, "wolf-killer-witch", "good")})
    effects = resolve_imitate_action(ctx, cmd("imitate", WITCH))
    assert [e.kind for e in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_PRIVATE_DATA,
        EffectKind.SET_RESOURCE, EffectKind.SET_RESOURCE, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {"target": AHW, "resource": "imitate", "amount": 1}
    assert effects[0].preconditions == {"resource_equals": {"resource": "imitate", "value": 1}}
    assert effects[1].payload["key"] == "disguise"
    assert effects[1].payload["value"] == {
        "role": "wolf-killer-witch", "camp": "good", "seat": WITCH, "round": 1,
    }
    assert {(e.payload["resource"], e.payload["value"]) for e in effects[2:4]} == {
        ("antidote", 1), ("poison", 1),
    }
    event = effects[-1].payload
    assert event["event_type"] == "AWAKENED_HIDDEN_WOLF_REASONING"
    assert "wolf-killer-witch" in event["payload"]["thought"]


def test_imitating_a_role_without_a_skill_only_changes_the_display() -> None:
    ctx = context(facts={"selected_target": shown(VILL, "wolf-killer-villager", "good")})
    effects = resolve_imitate_action(ctx, cmd("imitate", VILL))
    assert [e.kind for e in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    ]


def test_passing_and_degrading_only_leave_a_line() -> None:
    passed = resolve_imitate_action(context(), cmd("pass"))
    assert [e.kind for e in passed] == [EffectKind.EMIT_EVENT]
    assert "不模仿" in passed[0].payload["payload"]["thought"]
    degraded = resolve_imitate_action(context(degraded=True), cmd("pass", degraded=True))
    assert degraded[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert len(resolve_imitate_action(context(), cmd("imitate", None))) == 1


def test_an_unlabelled_imitation_still_renders_a_thought() -> None:
    from app.roles.awakened_hidden_wolf import _awakened_hidden_wolf_reasoning_effect

    effect = _awakened_hidden_wolf_reasoning_effect(
        context(), cmd("imitate", VILL), 1, None, expected_revision=3, source_event_id=None,
    )
    assert "模仿 8 号玩家" in effect.payload["payload"]["thought"]


def test_a_missing_or_malformed_target_fact_is_refused() -> None:
    with pytest.raises(ValueError):
        resolve_imitate_action(context(), cmd("imitate", VILL))
    with pytest.raises(ValueError):
        resolve_imitate_action(
            context(facts={"selected_target": shown(9, "x", "good")}), cmd("imitate", VILL),
        )
    with pytest.raises(TypeError):
        resolve_imitate_action(
            context(facts={"selected_target": {"seat": VILL, "role_label": "x"}}),
            cmd("imitate", VILL),
        )


# ── the same-night gate on the copied skills ───────────────────────────────


def _disguised(round_taken, **extra) -> dict:
    return {"disguise": {"role": "wolf-killer-seer", "camp": "good", "seat": SEER,
                         "round": round_taken}, **extra}


@pytest.mark.parametrize(("check", "marker"), [
    (mimic_check_applicable, "mimic_check"),
    (mimic_guard_applicable, "mimic_guard"),
    (mimic_inspect_applicable, "mimic_inspect"),
])
def test_a_copied_skill_opens_the_night_after_the_imitation(check, marker) -> None:
    held = {marker: 1}
    assert check(context(facts=_disguised(1), resources=held, round_number=2)) is True
    # The very night of the imitation: identity yes, skill not yet.
    assert check(context(facts=_disguised(1), resources=held, round_number=1)) is False
    assert check(context(facts=_disguised(1), resources={marker: 0}, round_number=2)) is False
    assert check(context(facts=_disguised(1), resources=held, round_number=2, alive=False)) is False
    # No imitation on record: nothing to copy from.
    assert check(context(facts={"disguise": None}, resources=held, round_number=2)) is False
    assert check(context(facts={}, resources=held, round_number=2)) is False
    assert check(context(
        facts={"disguise": {"role": "x", "camp": "good", "round": "1"}},
        resources=held, round_number=2,
    )) is False


def test_the_witch_clone_opens_with_either_potion() -> None:
    facts = _disguised(1)
    assert mimic_witch_applicable(context(facts=facts, resources={"antidote": 1}, round_number=2))
    assert mimic_witch_applicable(context(facts=facts, resources={"poison": 1}, round_number=2))
    assert not mimic_witch_applicable(context(
        facts=facts, resources={"antidote": 0, "poison": 0}, round_number=2,
    ))
    assert not mimic_witch_applicable(context(
        facts=facts, resources={"antidote": 1}, round_number=1,
    ))


def test_the_hunters_gun_is_closed_until_a_hunter_is_imitated() -> None:
    from app.roles.hunter import HUNTER_SPEC

    trigger = {"target_seat": AHW}
    base = ActionContext(
        game_id="g", revision=3, facts={}, config_version="a" * 64,
        contract_id=MIMIC_HUNTER_CONTRACT.contract_id,
        contract_version=MIMIC_HUNTER_CONTRACT.schema_version,
        contract_digest=MIMIC_HUNTER_CONTRACT.stable_digest(), round_number=2,
        phase="night", window_id="w", schedule_point=SchedulePoint.DAWN_REACTION,
        actor_seat=AHW, actor_role_id=HIDDEN, actor_alive=True,
        resources={"gun": 0}, action_key="k:1", source_event_id="e",
        trigger_event=trigger, trigger_reason="wolf_kill",
    )
    assert hunter_applicable(base) is False
    from dataclasses import replace
    assert hunter_applicable(replace(base, resources={"gun": 1})) is True
    assert MIMIC_HUNTER_CONTRACT.is_applicable is HUNTER_SPEC.contracts[0].is_applicable


# ── end to end through the real scheduler ──────────────────────────────────


def _state(game_id: str) -> GameState:
    roles = {
        WOLF: ("wolf-killer-werewolf", "werewolf"),
        AHW: (HIDDEN, "werewolf"),
        SEER: ("wolf-killer-seer", "good"),
        MIRROR: ("wolf-killer-mirror-girl", "good"),
        WITCH: ("wolf-killer-witch", "good"),
        HUNTER: ("wolf-killer-hunter", "good"),
        GUARD: ("wolf-killer-guard", "good"),
        VILL: ("wolf-killer-villager", "good"),
    }
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

    def asked_ids(self) -> set[str]:
        return {contract for _, contract in self.asked}

    @property
    def runtime(self):
        return self.state._pipeline_runtime


def test_an_imitated_seer_is_copied_from_the_next_night_and_shows_as_good() -> None:
    state = _state("copy-seer")
    table = Table(state, {
        "hidden_wolf_imitate": cmd("imitate", SEER),
        "mirror_inspect": cmd("inspect", AHW),
        "mimic_seer_check": cmd("check", VILL),
        "seer_check": cmd("check", AHW),
    })
    table.night(1, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_SEER_ACTION)
    assert table.runtime.role_resources[AHW]["imitate"] == 0
    assert table.runtime.private_data[AHW]["disguise"]["round"] == 1
    # The skill is not asked on the night it was taken…
    assert "mimic_seer_check" not in table.asked_ids()
    # …but the identity already is: the Mirror Girl reads the imitated seer…
    inspected = role_private_facts_view(state, MIRROR, "role_checks")
    assert [dict(item) for item in inspected] == [{"target": AHW, "role": "wolf-killer-seer"}]
    # …and so does the real Seer, who is told "good" about a wolf.
    checked = role_private_facts_view(state, SEER, "private_checks")
    assert [dict(item)["camp"] for item in checked] == ["good"]
    assert state.players[AHW].camp == "werewolf", "the real camp never moves"

    table.night(2, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_SEER_ACTION)
    assert "hidden_wolf_imitate" not in table.asked_ids(), "one imitation per game"
    assert (AHW, "mimic_seer_check") in table.asked
    own = role_private_facts_view(state, AHW, "private_checks")
    assert [dict(item) for item in own] == [{"target": VILL, "camp": "good"}]
    # A seer's check is a standing skill, not a charge: it stays open every night.
    assert table.runtime.role_resources[AHW]["mimic_check"] == 1

    table.night(3, SchedulePoint.NIGHT_SEER_ACTION)
    assert (AHW, "mimic_seer_check") in table.asked


def test_a_dormant_wolf_is_asked_for_nothing_but_the_imitation() -> None:
    state = _state("dormant")
    table = Table(state, {})
    table.night(
        1, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_SEER_ACTION,
        SchedulePoint.NIGHT_WITCH_ACTION,
    )
    mine = {contract for seat, contract in table.asked if seat == AHW}
    assert mine == {"hidden_wolf_imitate"}
    assert table.runtime.role_resources[AHW]["imitate"] == 1, "a pass keeps the card"


def test_an_imitated_witch_brings_her_own_potions_the_next_night() -> None:
    state = _state("copy-witch")
    table = Table(state, {
        "hidden_wolf_imitate": cmd("imitate", WITCH),
        "mimic_witch_action": cmd("poison", VILL),
    })
    table.night(1, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_WITCH_ACTION)
    assert "mimic_witch_action" not in table.asked_ids()
    assert table.runtime.role_resources[AHW]["poison"] == 1
    assert table.runtime.role_resources[AHW]["antidote"] == 1

    table.night(2, SchedulePoint.NIGHT_WITCH_ACTION)
    assert (AHW, "mimic_witch_action") in table.asked
    assert table.runtime.role_resources[AHW]["poison"] == 0
    assert table.runtime.role_resources[AHW]["antidote"] == 1, "the other potion is untouched"
    assert any(item["target"] == VILL for item in table.runtime.pending_damage)


def test_an_imitated_guard_protects_the_next_night() -> None:
    state = _state("copy-guard")
    table = Table(state, {
        "hidden_wolf_imitate": cmd("imitate", GUARD),
        "mimic_guard_action": cmd("guard", VILL),
    })
    table.night(1, SchedulePoint.NIGHT_ACTION)
    assert "mimic_guard_action" not in table.asked_ids()
    assert table.runtime.role_resources[AHW]["mimic_guard"] == 1

    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert (AHW, "mimic_guard_action") in table.asked
    assert table.runtime.role_resources[AHW]["mimic_guard"] == 1, "a guard's watch is standing"
    assert table.runtime.pending_protection


def test_an_imitated_mirror_girl_reads_identities_the_next_night() -> None:
    state = _state("copy-mirror")
    table = Table(state, {
        "hidden_wolf_imitate": cmd("imitate", MIRROR),
        "mimic_mirror_inspect": cmd("inspect", SEER),
    })
    table.night(1, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_SEER_ACTION)
    assert "mimic_mirror_inspect" not in table.asked_ids()
    table.night(2, SchedulePoint.NIGHT_SEER_ACTION)
    assert (AHW, "mimic_mirror_inspect") in table.asked
    own = role_private_facts_view(state, AHW, "role_checks")
    assert [dict(item) for item in own] == [{"target": SEER, "role": "wolf-killer-seer"}]


def test_an_imitated_hunter_is_handed_a_gun() -> None:
    state = _state("copy-hunter")
    table = Table(state, {"hidden_wolf_imitate": cmd("imitate", HUNTER)})
    table.night(1, SchedulePoint.NIGHT_ACTION)
    assert table.runtime.role_resources[AHW]["gun"] == 1


def test_a_hidden_wolf_with_no_gun_is_not_asked_to_shoot_when_exiled() -> None:
    from app.core.effect_applier import CommitResult
    from app.core.point_journal import (
        PendingEvent, PointCheckpoint, PointKey, WorkCursor, point_journal,
    )
    from app.core.registry_identity import frozen_registry_digest

    state = _state("no-gun-exile")
    table = Table(state, {"mimic_hunter_shoot": cmd("shoot", VILL)})
    table.night(2, SchedulePoint.NIGHT_ACTION)
    assert table.runtime.role_resources[AHW]["gun"] == 0
    state.players[AHW].is_alive = False
    identity = frozen_registry_digest(state, table.scheduler.registry)
    events = ({"event_type": "PLAYER_DIED", "payload": {"seat": AHW, "cause": "exile"}},)
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
    result = table.scheduler.run_point(state, SchedulePoint.DAWN_REACTION)
    assert (AHW, "mimic_hunter_shoot") not in table.asked
    assert "HUNTER_REASONING" not in [event["event_type"] for event in result.events]


def test_imitating_a_villager_grants_no_skill() -> None:
    state = _state("copy-villager")
    table = Table(state, {"hidden_wolf_imitate": cmd("imitate", VILL)})
    table.night(1, SchedulePoint.NIGHT_ACTION)
    table.night(
        2, SchedulePoint.NIGHT_ACTION, SchedulePoint.NIGHT_SEER_ACTION,
        SchedulePoint.NIGHT_WITCH_ACTION,
    )
    mine = {contract for seat, contract in table.asked if seat == AHW}
    assert mine == set()
    assert table.runtime.private_data[AHW]["disguise"]["role"] == "wolf-killer-villager"


def test_the_disguise_is_the_only_thing_that_changes_for_the_projector() -> None:
    state = _state("projector")
    table = Table(state, {"hidden_wolf_imitate": cmd("imitate", SEER)})
    table.night(1, SchedulePoint.NIGHT_ACTION)
    projector = ContextProjector()
    row = projector._selected_target_row(
        state, AHW, frozenset({"camp_label", "role_label"}),
    )
    assert row == {"seat": AHW, "camp_label": "good", "role_label": "wolf-killer-seer"}
    plain = projector._selected_target_row(
        state, WOLF, frozenset({"camp_label", "role_label"}),
    )
    assert plain == {"seat": WOLF, "camp_label": "werewolf", "role_label": "wolf-killer-werewolf"}
