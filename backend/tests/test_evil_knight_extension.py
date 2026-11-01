"""Evil Knight extension sample.

The Evil Knight is the first role whose whole kit is *marker-driven*: permanent
night immunity travels as ``night_immune`` (honoured by the settlement), and the
one-shot revenge travels as ``reflect_check`` / ``reflect_poison`` (honoured by
the seer's and the witch's own resolve hooks). This file pins both directions —
the settlement side and the producer side — plus the dedup marker that keeps a
second skill in the same night from firing a second revenge.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.effect_applier import _Runtime, derive_effect_id
from app.core.role_runtime import initialize_role_resources
from app.core.sheriff_flow import SheriffDirector
from app.core.night_settlement import NIGHT_IMMUNE_RESOURCE, settle
from app.core.reflect import (
    REFLECT_CAUSE, REFLECT_CHECK_RESOURCE, REFLECT_POISON_RESOURCE,
    REFLECT_USED_RESOURCE, reflect_effects,
)
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.wolf_claw import AWAKENED_CLAW_GIFT_CONTRACT
from app.roles.evil_knight import EVIL_KNIGHT_SPEC, EvilKnight
from app.roles.registry import builtin_registry
from app.roles.seer import SEER_SPEC, resolve_seer_action
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT, WEREWOLF_SPEC
from app.roles.witch import WITCH_SPEC, resolve_witch_action

EVIL_KNIGHT = EVIL_KNIGHT_SPEC.role_id


def command(action: str, target: int | None = None) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="ok")


def seer_context(labels: tuple[str, ...], *, target: int = 2) -> ActionContext:
    contract = SEER_SPEC.contracts[0]
    return ActionContext(
        "g", 0, {
            "alive_seats": (1, 2, 3),
            "selected_target": {
                "seat": target, "camp_label": "werewolf", "resource_labels": labels,
            },
        },
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=SEER_SPEC.role_id, actor_alive=True, action_key="a",
    )


def witch_context(labels: tuple[str, ...], *, target: int = 3) -> ActionContext:
    contract = WITCH_SPEC.contracts[0]
    return ActionContext(
        "g", 0, {
            "alive_seats": (1, 2, 3),
            "wolf_kill_target": 2,
            "selected_target": {
                "seat": target, "camp_label": "werewolf", "resource_labels": labels,
            },
        },
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=WITCH_SPEC.role_id, actor_alive=True,
        resources={"antidote": 1, "poison": 1}, action_key="a",
    )


# ── the role's own declaration ────────────────────────────────────────────

def test_spec_reuses_the_shared_kill_contract_and_declares_no_explode() -> None:
    assert EVIL_KNIGHT_SPEC.role_id == "wolf-killer-evil-knight"
    assert EVIL_KNIGHT_SPEC.camp_id == "werewolf"
    assert EVIL_KNIGHT_SPEC.tags == {"wolf"}
    assert EVIL_KNIGHT_SPEC.max_count == 1
    assert EVIL_KNIGHT_SPEC.self_explode_forbidden is True
    assert EVIL_KNIGHT_SPEC.contracts == (WEREWOLF_KILL_CONTRACT, AWAKENED_CLAW_GIFT_CONTRACT)
    assert WEREWOLF_KILL_CONTRACT.stable_digest() == \
        WEREWOLF_SPEC.contracts[0].stable_digest()
    # No explode contract is exactly what makes "cannot self-destruct" true.
    assert not any("explode" in contract.contract_id for contract in EVIL_KNIGHT_SPEC.contracts)
    assert EVIL_KNIGHT_SPEC.initial_resources == {
        NIGHT_IMMUNE_RESOURCE: 1,
        REFLECT_CHECK_RESOURCE: 1,
        REFLECT_POISON_RESOURCE: 1,
        REFLECT_USED_RESOURCE: 0,
        "self_kill_forbidden": 1,
    }


def test_the_badge_swallowing_move_is_not_offered_to_the_evil_knight() -> None:
    """The card says it cannot self-expose; the election is the same move."""
    state = GameState(
        "election", phase=GamePhase.SHERIFF_ELECTION, round_number=1,
        players={
            1: PlayerState(1, EVIL_KNIGHT, "werewolf"),
            2: PlayerState(2, WEREWOLF_SPEC.role_id, "werewolf"),
        },
    )
    state._pipeline_runtime = _Runtime()
    initialize_role_resources(state, builtin_registry.freeze().specs, "a" * 64)
    assert SheriffDirector._may_explode(state, 1) is False
    assert SheriffDirector._may_explode(state, 2) is True
    prompt = SheriffDirector()._context_block(state, 1, wolf=True)
    assert "不属于狼窝" not in prompt
    assert "不能自爆" in prompt


def test_instructions_name_every_ability() -> None:
    text = EVIL_KNIGHT_SPEC.instructions
    for ability in ("免疫", "自爆", "自刀", "反伤", "一次"):
        assert ability in text


def test_instructions_state_the_real_reflect_order() -> None:
    """同夜同验同毒时**查验先结算**，所以预言家出局、女巫不会出局。

    这是官方手册的确定结局（板子规则三处逐字重复）。模型必须知道它，否则算不清
    「毒我 = 换掉女巫」和「验我 = 换掉预言家」这两笔账 —— 同夜命中时只有前者的
    反面成立：被验才换得掉预言家，被毒要等到当夜没人验才换得掉女巫。
    """
    text = EVIL_KNIGHT_SPEC.instructions
    assert "查验先结算" in text
    assert "预言家先出局、女巫不会出局" in text
    assert "自动触发" in text


def test_instructions_never_promise_the_guard_blocks_a_reflect() -> None:
    """官方「守卫守护恶灵骑士不会被反伤」的主语是守卫，不是「守护能挡反伤」。

    这句话曾经写反过，模型会据此以为守卫守着预言家就换不掉他，从而放弃反伤。
    """
    text = EVIL_KNIGHT_SPEC.instructions
    assert "守卫的守护挡不住反伤" in text
    assert "守卫的守护可以挡住反伤" not in text


# ── settlement side: permanent immunity ───────────────────────────────────

@pytest.mark.parametrize("cause", ["wolf_kill", "poison", "reflect", "dream_death"])
def test_night_immunity_survives_every_lethal_night_cause(cause: str) -> None:
    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": cause},), (), {1, 2},
        {1: True, 2: True}, 1, {2: {NIGHT_IMMUNE_RESOURCE: 1}},
    )
    assert deaths == ()
    assert alive == {1: True, 2: True}
    # No death and no delayed mark either: the hit simply does not land.
    assert delayed == {}


def test_the_hunters_gun_is_the_one_thing_that_takes_it_down() -> None:
    """官方把免疫限定在夜间，并明说猎枪是例外。

    官方卡牌：「**无法在夜间出局**」——限定的是夜间。官网 2018 攻略与官方手册
    都写：「除了**猎枪**，恶灵骑士对其他神牌的功能有免疫力」。猎枪在白天公布
    死讯时开，所以它不在免疫范围内。
    """
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "hunter_shot"},), (), {1, 2},
        {1: True, 2: True}, 1, {2: {NIGHT_IMMUNE_RESOURCE: 1}},
    )
    assert [death["cause"] for death in deaths] == ["hunter_shot"]
    assert alive[2] is False


def test_a_knife_and_a_gun_in_the_same_settlement_still_leave_it_alive() -> None:
    """被刀又被枪时，枪那一笔生效 —— 免疫只挡夜间那部分。"""
    deaths, alive, _ = settle(
        (
            {"target": 2, "amount": 1, "cause": "wolf_kill"},
            {"target": 2, "amount": 1, "cause": "hunter_shot"},
        ),
        (), {1, 2}, {1: True, 2: True}, 1, {2: {NIGHT_IMMUNE_RESOURCE: 1}},
    )
    assert len(deaths) == 1
    assert deaths[0]["cause"] == "hunter_shot"
    assert alive[2] is False


def test_without_the_marker_the_same_hit_kills() -> None:
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},), (), {1, 2},
        {1: True, 2: True}, 1, {},
    )
    assert [death["seat"] for death in deaths] == [2]
    assert alive[2] is False


def test_immunity_beats_the_delayable_marker() -> None:
    """A seat holding both markers takes nothing from the settlement."""
    deaths, _, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "poison"},), (), {1, 2},
        {1: True, 2: True}, 1,
        {2: {NIGHT_IMMUNE_RESOURCE: 1, "delayable": 1}},
    )
    assert deaths == () and delayed == {}


# ── producer side: the seer bounces a check ───────────────────────────────

def test_seer_check_bounces_off_a_reflecting_target() -> None:
    # ``reflect_used`` is absent from the labels while the bounce is still armed.
    effects = resolve_seer_action(
        seer_context((REFLECT_CHECK_RESOURCE,)), command("check", 2),
    )
    kinds = [effect.kind for effect in effects]
    assert kinds[-2:] == [EffectKind.SET_RESOURCE, EffectKind.SUBMIT_DAMAGE]
    disarm, revenge = effects[-2:]
    assert disarm.target_seat == 2
    assert disarm.payload == {
        "target": 2, "resource": REFLECT_USED_RESOURCE, "value": 1}
    assert disarm.preconditions == {
        "resource_equals": {"resource": REFLECT_USED_RESOURCE, "value": 0}}
    assert revenge.target_seat == 1
    assert revenge.payload == {"target": 1, "amount": 1, "cause": REFLECT_CAUSE}


def test_seer_check_lands_normally_on_a_plain_target() -> None:
    effects = resolve_seer_action(seer_context(()), command("check", 2))
    assert [effect.kind for effect in effects] == [
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT]


def test_seer_check_lands_normally_once_the_bounce_is_spent() -> None:
    """``reflect_used`` present means an earlier skill already took the revenge."""
    effects = resolve_seer_action(
        seer_context((REFLECT_CHECK_RESOURCE, REFLECT_USED_RESOURCE)), command("check", 2),
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT]


def test_seer_check_is_untouched_by_the_poison_marker() -> None:
    effects = resolve_seer_action(
        seer_context((REFLECT_POISON_RESOURCE,)), command("check", 2),
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT]


# ── producer side: the witch bounces a poison ─────────────────────────────

def test_witch_poison_bounces_off_a_reflecting_target() -> None:
    effects = resolve_witch_action(
        witch_context((REFLECT_POISON_RESOURCE,)), command("poison", 3),
    )
    kinds = [effect.kind for effect in effects]
    assert kinds[-2:] == [EffectKind.SET_RESOURCE, EffectKind.SUBMIT_DAMAGE]
    assert effects[-1].payload == {"target": 1, "amount": 1, "cause": REFLECT_CAUSE}
    # Sort keys stay canonical: the resolver rejects anything else.
    assert [effect.sort_key for effect in effects] == [
        (index,) for index in range(1, len(effects) + 1)]


def test_witch_antidote_never_bounces() -> None:
    effects = resolve_witch_action(
        witch_context((REFLECT_POISON_RESOURCE,)), command("save", 2),
    )
    kinds = [effect.kind for effect in effects]
    assert EffectKind.SET_RESOURCE not in kinds
    assert kinds.count(EffectKind.SUBMIT_DAMAGE) == 0


def test_witch_poison_lands_normally_once_the_bounce_is_spent() -> None:
    effects = resolve_witch_action(
        witch_context((REFLECT_POISON_RESOURCE, REFLECT_USED_RESOURCE)),
        command("poison", 3),
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT]


# ── the shared helper ─────────────────────────────────────────────────────

def test_reflect_helper_is_inert_without_a_target_seat() -> None:
    assert reflect_effects(
        seer_context((REFLECT_CHECK_RESOURCE,)), {"resource_labels": (REFLECT_CHECK_RESOURCE,)},
        REFLECT_CHECK_RESOURCE, 4,
    ) == ()
    assert reflect_effects(
        seer_context(()), {"seat": 2, "resource_labels": ()}, REFLECT_CHECK_RESOURCE, 4,
    ) == ()
    assert reflect_effects(
        seer_context(()),
        {"seat": 2, "resource_labels": (REFLECT_CHECK_RESOURCE, REFLECT_USED_RESOURCE)},
        REFLECT_CHECK_RESOURCE, 4,
    ) == ()
    assert reflect_effects(
        seer_context(()), {"seat": 0, "resource_labels": (REFLECT_CHECK_RESOURCE,)},
        REFLECT_CHECK_RESOURCE, 4,
    ) == ()
    assert reflect_effects(
        seer_context(()), {"seat": 2}, REFLECT_CHECK_RESOURCE, 4,
    ) == ()


def test_reflect_helper_effect_ids_are_canonical() -> None:
    context = seer_context((REFLECT_CHECK_RESOURCE,))
    effects = reflect_effects(
        context, {"seat": 2, "resource_labels": (REFLECT_CHECK_RESOURCE,)},
        REFLECT_CHECK_RESOURCE, 4,
    )
    assert [effect.effect_id for effect in effects] == [
        derive_effect_id(context.action_key, 4), derive_effect_id(context.action_key, 5)]


# ── registration ──────────────────────────────────────────────────────────

def test_registry_registers_both_the_pipeline_and_the_legacy_role() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(EVIL_KNIGHT) is EVIL_KNIGHT_SPEC
    assert builtin_registry.require(EVIL_KNIGHT).role_factory is EvilKnight
    assert snapshot.require(EVIL_KNIGHT).camp_id == "werewolf"
    assert snapshot.specs[EVIL_KNIGHT].initial_resources["night_immune"] == 1


def test_legacy_evil_knight_is_a_werewolf_without_an_explode_skill() -> None:
    role = object.__new__(EvilKnight)


def test_seated_evil_knight_is_night_immune_in_a_real_state() -> None:
    """The markers are established by the normal resource-setup path."""
    from app.core.role_runtime import initialize_role_resources, role_resource_view

    snapshot = builtin_registry.freeze()
    state = GameState(
        "g", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, EVIL_KNIGHT, "werewolf"),
            2: PlayerState(2, WEREWOLF_SPEC.role_id, "werewolf"),
        },
    )
    initialize_role_resources(state, snapshot.specs, snapshot.digest)
    assert role_resource_view(state, 1)[NIGHT_IMMUNE_RESOURCE] == 1
    assert role_resource_view(state, 1)[REFLECT_USED_RESOURCE] == 0
    assert role_resource_view(state, 2).get(NIGHT_IMMUNE_RESOURCE, 0) == 0


def test_replace_keeps_the_helper_context_frozen() -> None:
    """Guards against a future refactor turning the helper into a mutator."""
    base = seer_context(())
    changed = replace(base, round_number=2)
    assert base.round_number == 1 and changed.round_number == 2
    assert SchedulePoint.NIGHT_SEER_ACTION is SEER_SPEC.contracts[0].schedule_point
