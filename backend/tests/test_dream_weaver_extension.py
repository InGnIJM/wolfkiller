"""Dream Weaver extension sample.

The Dream Weaver is the first role whose shield has to stop the *poison*, which
is why it rides the any-damage tier added alongside it: a plain guard shield
would not. It is also the first role with a ``react`` contract whose payload
comes from its own private data, so this file pins the record-and-read-back
round trip as well as the two ways the weave turns lethal.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.night_settlement import settle
from app.models.game import GameState
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.dream_weaver import (
    DREAM_DEATH_CAUSE, DREAM_WEAVER_REVENGE_CONTRACT, DREAM_WEAVER_SPEC,
    DreamWeaver, dream_weaver_applicable, dream_weaver_revenge_applicable,
    react_dream_weaver_revenge, resolve_dream_weaver_action,
    validate_dream_weaver_action,
)
from app.roles.registry import builtin_registry

ROLE = DREAM_WEAVER_SPEC.role_id
DREAM_CONTRACT = DREAM_WEAVER_SPEC.contracts[0]


def context(
    *, last_dreamed: object = None, alive=(1, 2, 3), actor: int = 1,
    reason: str = "wolf_kill", trigger_target: int | None = None,
    bound: bool = False,
) -> ActionContext:
    contract = DREAM_WEAVER_REVENGE_CONTRACT if bound else DREAM_CONTRACT
    event_id = "event:" + "1" * 16 if bound else None
    facts: dict[str, object] = {"alive_seats": alive, "last_dreamed": last_dreamed}
    return ActionContext(
        "g", 0, facts, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=2, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=actor,
        actor_role_id=ROLE, actor_alive=True, action_key="a",
        source_event_id=event_id,
        trigger_event=(
            None if event_id is None else {
                "event_id": event_id, "type": "PLAYER_DIED",
                "target_seat": actor if trigger_target is None else trigger_target,
                "cause": reason,
            }
        ),
        trigger_reason=reason if event_id is not None else None,
    )


def command(action: str, target: int | None = None) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="ok")


# ── declaration ───────────────────────────────────────────────────────────

def test_spec_declares_a_mandatory_weave_and_a_death_response() -> None:
    assert DREAM_WEAVER_SPEC.role_id == "wolf-killer-dream-weaver"
    assert DREAM_WEAVER_SPEC.camp_id == "good"
    assert DREAM_WEAVER_SPEC.tags == {"god"}
    assert DREAM_WEAVER_SPEC.max_count == 1
    assert DREAM_WEAVER_SPEC.initial_private_data == {"last_dreamed": None}
    assert DREAM_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert DREAM_CONTRACT.action_types == ("dream", "pass")
    assert DREAM_CONTRACT.actions_requiring_target == {"dream"}
    # The registry forbids a fallback that requires a target, so ``pass`` has to
    # exist as the engine's safety valve even though the rules say "mandatory".
    assert DREAM_CONTRACT.fallback_action_type == "pass"
    assert DREAM_WEAVER_REVENGE_CONTRACT.schedule_point is SchedulePoint.DAWN_REACTION
    assert DREAM_WEAVER_REVENGE_CONTRACT.response_event_types == {"PLAYER_DIED"}
    assert DREAM_WEAVER_REVENGE_CONTRACT.react is react_dream_weaver_revenge


def test_revenge_binds_only_the_night_ways_out() -> None:
    reasons = DREAM_WEAVER_REVENGE_CONTRACT.response_reasons
    assert {"wolf_kill", "poison", "charm", DREAM_DEATH_CAUSE} <= reasons
    for daytime in ("exile", "self_explode", "knight_duel"):
        assert daytime not in reasons


def test_instructions_state_every_rule() -> None:
    text = DREAM_WEAVER_SPEC.instructions
    for rule in ("必须", "不可放弃", "免疫", "连续两晚", "夜间出局", "白天被放逐"):
        assert rule in text


# ── the weave ─────────────────────────────────────────────────────────────

def test_applicable_is_just_liveness() -> None:
    assert dream_weaver_applicable(context()) is True
    assert dream_weaver_applicable(replace(context(), actor_alive=False)) is False


def test_validation_forbids_dreaming_itself() -> None:
    assert validate_dream_weaver_action(context(), command("dream", 2)) == ()
    assert validate_dream_weaver_action(context(), command("pass")) == ()
    assert [violation.code for violation in
            validate_dream_weaver_action(context(), command("dream", 1))] == [
        "dream_self_forbidden"]


def test_generic_validator_requires_a_target_and_a_living_one() -> None:
    validator = ActionValidator()
    assert validator.validate(context(), DREAM_CONTRACT, command("dream", 2)) == ()
    assert {item.code for item in
            validator.validate(context(), DREAM_CONTRACT, command("dream"))} == {
        "target_required"}
    assert {item.code for item in
            validator.validate(context(), DREAM_CONTRACT, command("dream", 9))} == {
        "target_not_alive"}


def test_a_fresh_dream_shields_and_records() -> None:
    effects = resolve_dream_weaver_action(context(last_dreamed=3), command("dream", 2))
    assert [effect.kind for effect in effects] == [
        EffectKind.SUBMIT_PROTECTION, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT]
    shield = effects[0]
    assert shield.target_seat == 2
    assert shield.payload == {"target": 2, "amount": 1, "source": "dream"}
    assert effects[1].payload == {"target": 1, "key": "last_dreamed", "value": 2}
    assert effects[2].payload["payload"]["thought"] == "决定让 2 号玩家梦游：ok"


def test_a_repeat_dream_kills_instead_of_shielding() -> None:
    effects = resolve_dream_weaver_action(context(last_dreamed=2), command("dream", 2))
    assert [effect.kind for effect in effects] == [
        EffectKind.SUBMIT_DAMAGE, EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT]
    assert effects[0].payload == {
        "target": 2, "amount": 1, "cause": DREAM_DEATH_CAUSE}
    # No shield is submitted, so nothing can save the seat from this one.
    assert EffectKind.SUBMIT_PROTECTION not in [effect.kind for effect in effects]


def test_passing_only_records_a_reason() -> None:
    effects = resolve_dream_weaver_action(context(), command("pass"))
    assert [effect.kind for effect in effects] == [EffectKind.EMIT_EVENT]
    assert effects[0].payload["payload"]["thought"] == "决定今晚不发动摄梦：ok"


def test_a_targetless_dream_degrades_to_the_same_shape_as_a_pass() -> None:
    """Only reachable through the engine's safety valve, never through a model."""
    effects = resolve_dream_weaver_action(context(), command("dream"))
    assert [effect.kind for effect in effects] == [EffectKind.EMIT_EVENT]


def test_resolver_accepts_the_whole_batch() -> None:
    effects = ActionResolver().resolve_effects(
        context(last_dreamed=3), DREAM_WEAVER_SPEC, DREAM_CONTRACT, command("dream", 2),
    )
    assert [effect.sort_key for effect in effects] == [(0,), (1,), (2,), (3,)]


# ── the shield actually stops the vial ────────────────────────────────────

def test_a_dreamed_seat_survives_the_poison_a_guard_could_not_stop() -> None:
    for source, expected in (("dream", 0), ("guard", 1)):
        deaths, alive, _ = settle(
            ({"target": 2, "amount": 1, "cause": "poison"},),
            ({"target": 2, "amount": 1, "source": source},),
            {1, 2}, {1: True, 2: True}, 1,
        )
        assert len(deaths) == expected, source


def test_a_dreamed_seat_survives_the_knife() -> None:
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        ({"target": 2, "amount": 1, "source": "dream"},),
        {1, 2}, {1: True, 2: True}, 1,
    )
    assert deaths == () and alive[2] is True


# ── the death response ────────────────────────────────────────────────────

def test_revenge_applicable_requires_a_bound_night_death_and_a_dreamer() -> None:
    assert dream_weaver_revenge_applicable(context(last_dreamed=2, bound=True)) is True
    assert dream_weaver_revenge_applicable(
        context(last_dreamed=2, bound=True, reason="poison")) is True
    for daytime in ("exile", "self_explode", "knight_duel"):
        assert dream_weaver_revenge_applicable(
            context(last_dreamed=2, bound=True, reason=daytime)) is False, daytime
    # Nobody was dreamed, or the record points at the Weaver itself.
    assert dream_weaver_revenge_applicable(context(last_dreamed=None, bound=True)) is False
    assert dream_weaver_revenge_applicable(context(last_dreamed=1, bound=True)) is False
    assert dream_weaver_revenge_applicable(
        context(last_dreamed="2", bound=True)) is False
    # The window must be bound to this seat's own death.
    assert dream_weaver_revenge_applicable(
        context(last_dreamed=2, bound=True, trigger_target=3)) is False
    assert dream_weaver_revenge_applicable(context(last_dreamed=2)) is False


def test_revenge_drags_the_dreamer_along() -> None:
    effects = react_dream_weaver_revenge(context(last_dreamed=2, bound=True))
    assert [effect.kind for effect in effects] == [EffectKind.SUBMIT_DAMAGE]
    assert effects[0].target_seat == 2
    assert effects[0].payload == {
        "target": 2, "amount": 1, "cause": DREAM_DEATH_CAUSE}
    assert effects[0].sort_key == (1,)


def test_revenge_stays_silent_on_a_daytime_exit() -> None:
    assert react_dream_weaver_revenge(
        context(last_dreamed=2, bound=True, reason="exile")) == ()


def test_resolver_accepts_the_react_batch() -> None:
    effects = ActionResolver().react_effects(
        context(last_dreamed=2, bound=True), DREAM_WEAVER_SPEC,
        DREAM_WEAVER_REVENGE_CONTRACT,
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.ACCEPT_ACTION, EffectKind.SUBMIT_DAMAGE]


# ── registration ──────────────────────────────────────────────────────────

def test_registry_registers_both_the_pipeline_and_the_legacy_role() -> None:
    snapshot = builtin_registry.freeze()
    assert snapshot.require(ROLE) is DREAM_WEAVER_SPEC
    assert builtin_registry.require(ROLE).role_factory is DreamWeaver
    assert snapshot.require(ROLE).tags == {"god"}


def test_legacy_dream_weaver_has_no_daytime_skill() -> None:
    """The weave is a pipeline contract, so the daytime role stays bare."""
    assert object.__new__(DreamWeaver).get_skills() == []


@pytest.mark.parametrize("kind", [EffectKind.SUBMIT_PROTECTION, EffectKind.SET_PRIVATE_DATA])
def test_spec_allows_every_effect_the_hooks_produce(kind: EffectKind) -> None:
    assert kind in DREAM_WEAVER_SPEC.allowed_effects
    assert kind in DREAM_CONTRACT.allowed_effects


def test_the_weave_lands_through_a_real_scheduler_run() -> None:
    """端到端回归：摄梦人的守护必须真的写进 pending_protection 并能挡毒。

    此前没有任何测试把 `source="dream"` 送过 `EffectApplier`，而写入口的来源白名单
    漏了它 —— 真跑一局时摄梦人第一夜就会把调度点打成失败。
    """
    from app.core.action_resolver import ActionResolver
    from app.core.action_validator import ActionValidator
    from app.core.context_projector import ContextProjector
    from app.core.effect_applier import EffectApplier
    from app.core.night_settlement import settle
    from app.core.scheduler import Scheduler
    from app.models.game import GamePhase, GameState, PlayerState
    from app.models.pipeline import ActionCommand, SchedulePoint
    from app.roles.registry import RegistrySnapshot, RoleRegistry
    from app.roles.villager import VILLAGER_SPEC

    registry = RoleRegistry()
    for spec in (DREAM_WEAVER_SPEC, VILLAGER_SPEC):
        registry.register_pipeline(spec)
    snapshot: RegistrySnapshot = registry.freeze()
    game = GameState(
        "g", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, DREAM_WEAVER_SPEC.role_id, "good"),
            2: PlayerState(2, VILLAGER_SPEC.role_id, "good"),
        },
    )

    def provider(request, context, attempt):
        return ActionCommand(action_type="dream", target_seat=2, reasoning="护一手")

    scheduler = Scheduler(
        snapshot, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(game, SchedulePoint.NIGHT_ACTION)

    assert game._pipeline_runtime.pending_protection == (
        {"target": 2, "amount": 1, "source": "dream"},
    )
    deaths, alive, _ = settle(
        game._pipeline_runtime.pending_damage,
        game._pipeline_runtime.pending_protection,
        set(game.players), {seat: player.is_alive for seat, player in game.players.items()},
        1, game._pipeline_runtime.role_resources,
    )
    assert deaths == () and alive[1] is True

def test_the_dream_weaver_declares_its_opening_line() -> None:
    assert DREAM_WEAVER_SPEC.opening_narration == ("摄梦人请睁眼", "请选择今晚要摄梦的玩家。")


