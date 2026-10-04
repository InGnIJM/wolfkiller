"""Gargoyle and Gravekeeper extension sample.

The Gargoyle is the hidden-wolf archetype in the flesh: three of its four
properties come from markers the engine already reads, and only the identity
check is its own. The Gravekeeper is the opposite extreme — no contract at all,
because the rules give it no choice to make, so its brief has to be projected.

The pair also pins the two facts added for them: the ``role_label``
selected-target namespace (the only way a hook can learn a target's exact role)
and the derived ``last_exiled`` private fact.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.role_runtime import initialize_role_resources
from app.core.scheduler import IssuedActionRequest
from app.core.wolf_pack import in_wolf_pack
from app.models.actions import DeathReport
from app.models.game import Camp, GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.gargoyle import (
    GARGOYLE_CHECK_CONTRACT, GARGOYLE_SPEC, ROLE_CHECK_NAMESPACE, Gargoyle,
    gargoyle_applicable, resolve_gargoyle_action, validate_gargoyle_action,
)
from app.roles.gravekeeper import GRAVEKEEPER_SPEC, Gravekeeper
from app.roles.hidden_wolf import HIDDEN_WOLF_KILL_CONTRACT
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry
from app.roles.seer import SEER_SPEC
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC

GARGOYLE = GARGOYLE_SPEC.role_id
GRAVEKEEPER = GRAVEKEEPER_SPEC.role_id


def registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    for spec in (WEREWOLF_SPEC, VILLAGER_SPEC, SEER_SPEC, GARGOYLE_SPEC, GRAVEKEEPER_SPEC):
        registry.register_pipeline(spec)
    return registry.freeze()


def state(*, gargoyle: bool = True, gravekeeper: bool = False) -> GameState:
    seats = {1: WEREWOLF_SPEC.role_id, 3: VILLAGER_SPEC.role_id, 4: SEER_SPEC.role_id}
    if gargoyle:
        seats[2] = GARGOYLE
    if gravekeeper:
        seats[5] = GRAVEKEEPER
    game = GameState(
        "g", config=GameConfig(), phase=GamePhase.NIGHT, round_number=1,
        players={
            seat: PlayerState(
                seat, role,
                Camp.WEREWOLF if role in (WEREWOLF_SPEC.role_id, GARGOYLE) else Camp.GOOD,
            )
            for seat, role in seats.items()
        },
    )
    snapshot = registry()
    initialize_role_resources(game, snapshot.specs, snapshot.digest)
    return game


def context(
    *, target: int | None = 3, role_label: str | None = "wolf-killer-villager",
    checked: tuple[int, ...] = (), actor: int = 2,
) -> ActionContext:
    facts: dict[str, object] = {
        "alive_seats": (1, 2, 3, 4), "checked_seats": checked,
    }
    if target is not None:
        facts["selected_target"] = {"seat": target, "role_label": role_label}
    return ActionContext(
        "g", 0, facts, config_version="a" * 64,
        contract_id=GARGOYLE_CHECK_CONTRACT.contract_id,
        contract_version=GARGOYLE_CHECK_CONTRACT.schema_version,
        contract_digest=GARGOYLE_CHECK_CONTRACT.stable_digest(),
        round_number=2, phase="night", window_id="w",
        schedule_point=SchedulePoint.NIGHT_SEER_ACTION, actor_seat=actor,
        actor_role_id=GARGOYLE, actor_alive=True, action_key="a",
    )


def _record(game: GameState, seat: int, cause: str, round_number: int) -> None:
    """Append a death the way the engine does."""
    game.death_history.append(DeathReport(seat, cause, round_number))


def command(action: str, target: int | None = None) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="ok")


# ── the Gargoyle's declaration ────────────────────────────────────────────

def test_gargoyle_is_a_hidden_wolf_with_its_own_check() -> None:
    assert GARGOYLE_SPEC.camp_id == "werewolf"
    assert GARGOYLE_SPEC.tags == {"wolf"}
    assert GARGOYLE_SPEC.max_count == 1
    assert GARGOYLE_SPEC.initial_resources == {"no_wolf_pack": 1}
    assert [c.contract_id for c in GARGOYLE_SPEC.contracts] == [
        HIDDEN_WOLF_KILL_CONTRACT.contract_id, GARGOYLE_CHECK_CONTRACT.contract_id]
    # It never joins the pack's own vote, and it declares no explode contract,
    # which is what makes "cannot self-destruct" true without an engine branch.
    assert all(c.contract_id != "werewolf_kill" for c in GARGOYLE_SPEC.contracts)
    assert all("explode" not in c.contract_id for c in GARGOYLE_SPEC.contracts)
    assert GARGOYLE_SPEC.initial_private_data == {
        "checked_seats": (), ROLE_CHECK_NAMESPACE: ()}


def test_gargoyle_sits_outside_the_pack_in_a_real_state() -> None:
    game = state()
    assert in_wolf_pack(game, 1) is True
    assert in_wolf_pack(game, 2) is False


def test_check_contract_reads_the_exact_role_and_nothing_else() -> None:
    assert GARGOYLE_CHECK_CONTRACT.schedule_point is SchedulePoint.NIGHT_SEER_ACTION
    assert GARGOYLE_CHECK_CONTRACT.order > SEER_SPEC.contracts[0].order
    assert GARGOYLE_CHECK_CONTRACT.selected_target_fact_namespaces == {"role_label"}
    assert GARGOYLE_CHECK_CONTRACT.actions_requiring_target == {"check"}
    assert GARGOYLE_CHECK_CONTRACT.fallback_action_type == "pass"


def test_instructions_state_every_rule() -> None:
    text = GARGOYLE_SPEC.instructions
    for rule in ("不属于狼窝", "带刀权", "自爆", "具体身份", "不能重复", "显示为狼人"):
        assert rule in text


# ── the check ─────────────────────────────────────────────────────────────

def test_applicable_is_just_liveness() -> None:
    assert gargoyle_applicable(context()) is True
    assert gargoyle_applicable(replace(context(), actor_alive=False)) is False


def test_validation_forbids_self_and_repeat_checks() -> None:
    assert validate_gargoyle_action(context(), command("check", 3)) == ()
    assert validate_gargoyle_action(context(), command("pass")) == ()
    assert [v.code for v in validate_gargoyle_action(context(), command("check", 2))] == [
        "gargoyle_self_check"]
    assert [v.code for v in
            validate_gargoyle_action(context(checked=(3,)), command("check", 3))] == [
        "gargoyle_repeat_check"]


def test_generic_validator_requires_a_living_target() -> None:
    validator = ActionValidator()
    assert validator.validate(context(), GARGOYLE_CHECK_CONTRACT, command("check", 3)) == ()
    assert {v.code for v in validator.validate(
        context(), GARGOYLE_CHECK_CONTRACT, command("check"))} == {"target_required"}
    assert {v.code for v in validator.validate(
        context(), GARGOYLE_CHECK_CONTRACT, command("check", 9))} == {"target_not_alive"}


def test_a_check_records_the_identity_privately_and_never_publicly() -> None:
    effects = resolve_gargoyle_action(context(), command("check", 3))
    assert [effect.kind for effect in effects] == [
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT]
    record = effects[0]
    assert record.visibility == ("ACTOR",)
    assert record.payload == {
        "target": 2, "namespace": ROLE_CHECK_NAMESPACE,
        "fact": {"target": 3, "role": "wolf-killer-villager"},
    }
    # The effect freezes its payload, so the recorded list lands as a tuple.
    assert effects[1].payload == {"target": 2, "key": "checked_seats", "value": (3,)}
    # The audience only ever sees the thought, which carries the answer.
    assert effects[2].visibility == ("PUBLIC",)
    assert "wolf-killer-villager" in effects[2].payload["payload"]["thought"]


def test_a_second_check_appends_to_the_record() -> None:
    effects = resolve_gargoyle_action(context(checked=(1,)), command("check", 3))
    assert effects[1].payload["value"] == (1, 3)


def test_passing_emits_only_a_reason() -> None:
    effects = resolve_gargoyle_action(context(target=None), command("pass"))
    assert [effect.kind for effect in effects] == [EffectKind.EMIT_EVENT]
    assert "决定今晚不查验" in effects[0].payload["payload"]["thought"]


def test_a_missing_selected_target_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        resolve_gargoyle_action(context(target=None), command("check", 3))
    # The projected seat must be the one that was checked...
    with pytest.raises(ValueError):
        resolve_gargoyle_action(context(), command("check", 4))
    # ...and its role label must be a string, never a missing or odd value.
    with pytest.raises(TypeError):
        resolve_gargoyle_action(context(role_label=None), command("check", 3))


def test_resolver_accepts_the_whole_batch() -> None:
    effects = ActionResolver().resolve_effects(
        context(), GARGOYLE_SPEC, GARGOYLE_CHECK_CONTRACT, command("check", 3),
    )
    assert [effect.sort_key for effect in effects] == [(0,), (1,), (2,), (3,)]


# ── the projector: role_label and last_exiled ─────────────────────────────

def _facts(game: GameState, seat: int, snapshot: RegistrySnapshot) -> dict:
    """The contract-free view, which works for a role with no contract at all."""
    return dict(ContextProjector().project_view(game, snapshot, seat).facts)


def _gargoyle_context(game: GameState, snapshot: RegistrySnapshot) -> ActionContext:
    request = IssuedActionRequest(
        2, GARGOYLE, GARGOYLE_CHECK_CONTRACT, 0, 1, "night", "w", "a")
    return ContextProjector().project(game, request, snapshot)


def test_role_label_reaches_the_hook_but_the_answer_stays_private() -> None:
    game, snapshot = state(), registry()
    base = _gargoyle_context(game, snapshot)
    projected = ContextProjector().project_selected_target(
        game,
        IssuedActionRequest(2, GARGOYLE, GARGOYLE_CHECK_CONTRACT, 0, 1, "night", "w", "a"),
        base, command("check", 4), snapshot,
    )
    assert projected.facts["selected_target"] == {
        "seat": 4, "role_label": SEER_SPEC.role_id}
    # The model's own context never carries it: only the rule context does.
    assert "selected_target" not in base.facts


def test_gravekeeper_reads_none_before_anyone_is_exiled() -> None:
    game, snapshot = state(gravekeeper=True), registry()
    assert _facts(game, 5, snapshot)["last_exiled"] is None


def test_gravekeeper_reads_the_last_exiles_seat_and_camp() -> None:
    game, snapshot = state(gravekeeper=True), registry()
    _record(game, 1, "wolf_kill", 1)
    _record(game, 3, "exile", 1)
    assert _facts(game, 5, snapshot)["last_exiled"] == {"seat": 3, "camp": "good"}
    _record(game, 1, "exile", 2)
    assert _facts(game, 5, snapshot)["last_exiled"] == {"seat": 1, "camp": "werewolf"}


def test_gravekeeper_ignores_non_exile_deaths() -> None:
    game, snapshot = state(gravekeeper=True), registry()
    _record(game, 3, "exile", 1)
    _record(game, 4, "poison", 2)
    assert _facts(game, 5, snapshot)["last_exiled"] == {"seat": 3, "camp": "good"}


def test_gravekeeper_brief_is_absent_for_everyone_else() -> None:
    game, snapshot = state(gravekeeper=True), registry()
    _record(game, 3, "exile", 1)
    assert "last_exiled" not in _facts(game, 1, snapshot)
    assert "last_exiled" not in _facts(game, 4, snapshot)


def test_an_unseated_exile_reads_as_no_information() -> None:
    game, snapshot = state(gravekeeper=True), registry()
    _record(game, 9, "exile", 1)
    assert _facts(game, 5, snapshot)["last_exiled"] is None


# ── the Gravekeeper's declaration ─────────────────────────────────────────

def test_gravekeeper_has_no_contract_and_no_resource() -> None:
    assert GRAVEKEEPER_SPEC.contracts == ()
    assert GRAVEKEEPER_SPEC.allowed_effects == frozenset()
    assert GRAVEKEEPER_SPEC.initial_resources == {}
    assert GRAVEKEEPER_SPEC.initial_private_data == {"last_exiled": None}
    assert GRAVEKEEPER_SPEC.tags == {"god"}
    assert GRAVEKEEPER_SPEC.max_count == 1


def test_gravekeeper_instructions_state_every_rule() -> None:
    text = GRAVEKEEPER_SPEC.instructions
    for rule in ("没有", "上一个白天被放逐", "第一个夜晚", "石像鬼"):
        assert rule in text


def test_gravekeeper_is_a_plain_daytime_role() -> None:
    assert object.__new__(Gravekeeper).get_skills() == []


# ── registration ──────────────────────────────────────────────────────────

def test_registry_registers_both_roles_in_both_places() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(GARGOYLE) is GARGOYLE_SPEC
    assert snapshot.require(GRAVEKEEPER) is GRAVEKEEPER_SPEC
    assert builtin_registry.require(GARGOYLE).role_factory is Gargoyle
    assert builtin_registry.require(GRAVEKEEPER).role_factory is Gravekeeper
    assert snapshot.require(GARGOYLE).camp_id == "werewolf"
    assert snapshot.require(GRAVEKEEPER).camp_id == "good"


def test_replace_keeps_the_helper_context_frozen() -> None:
    base = context()
    assert replace(base, round_number=3).round_number == 3
    assert base.round_number == 2
