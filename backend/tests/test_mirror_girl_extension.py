"""Mirror Girl: a good-camp god who reads one seat's exact identity every night.

The hooks are the Gargoyle's check moved to the good side; the interesting part —
what a *disguised* seat shows her — is exercised end to end in
``test_awakened_hidden_wolf_extension.py`` where the disguise is actually written.
"""

from __future__ import annotations

import pytest

from app.core.disguise import DISGUISE_KEY, disguised_identity
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, EffectKind, SchedulePoint,
)
from app.roles.base import BaseRole
from app.roles.mirror_girl import (
    MIRROR_GIRL_SPEC, MIRROR_INSPECT_CONTRACT, ROLE_CHECK_NAMESPACE, MirrorGirl,
    mirror_girl_applicable, resolve_mirror_girl_action, validate_mirror_girl_action,
)
from app.roles.registry import builtin_registry

MIRROR = "wolf-killer-mirror-girl"
GIRL, TARGET = 2, 3


def context(*, facts=None, alive=True, degraded=False) -> ActionContext:
    contract = MIRROR_INSPECT_CONTRACT
    return ActionContext(
        game_id="g", revision=3, facts={"alive_seats": (1, 2, 3), **(facts or {})},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
        round_number=1, phase="night", window_id="w", schedule_point=contract.schedule_point,
        actor_seat=GIRL, actor_role_id=MIRROR, actor_alive=alive, action_key="k:1",
        degraded=degraded,
    )


def cmd(action: str, target: int | None = None, *, degraded: bool = False) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="理由", degraded=degraded)


def test_the_card_is_a_good_god_that_shares_the_identity_check_point() -> None:
    assert MIRROR_GIRL_SPEC.camp_id == "good"
    assert MIRROR_GIRL_SPEC.tags == frozenset({"god"})
    assert MIRROR_INSPECT_CONTRACT.schedule_point is SchedulePoint.NIGHT_SEER_ACTION
    # The check reaches the pure hook only: the model never sees a role label.
    assert MIRROR_INSPECT_CONTRACT.selected_target_fact_namespaces == frozenset({"role_label"})
    assert ROLE_CHECK_NAMESPACE == "role_checks"
    assert issubclass(MirrorGirl, BaseRole)
    assert builtin_registry.require(MIRROR).role_factory is MirrorGirl


def test_applicability_and_validation() -> None:
    assert mirror_girl_applicable(context()) is True
    assert mirror_girl_applicable(context(alive=False)) is False
    assert validate_mirror_girl_action(context(), cmd("inspect", TARGET)) == ()
    assert validate_mirror_girl_action(context(), cmd("pass")) == ()
    assert [v.code for v in validate_mirror_girl_action(context(), cmd("inspect", GIRL))] == [
        "self_inspect_forbidden",
    ]


def test_an_inspection_records_the_exact_role_privately() -> None:
    ctx = context(facts={"selected_target": {"seat": TARGET, "role_label": "wolf-killer-seer"}})
    effects = resolve_mirror_girl_action(ctx, cmd("inspect", TARGET))
    assert [e.kind for e in effects] == [EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT]
    assert effects[0].payload == {
        "target": GIRL, "namespace": "role_checks",
        "fact": {"target": TARGET, "role": "wolf-killer-seer"},
    }
    assert effects[0].visibility == ("ACTOR",)
    event = effects[1].payload
    assert event["event_type"] == "MIRROR_GIRL_REASONING"
    assert "wolf-killer-seer" in event["payload"]["thought"]


def test_passing_and_degrading_only_leave_a_line() -> None:
    passed = resolve_mirror_girl_action(context(), cmd("pass"))
    assert [e.kind for e in passed] == [EffectKind.EMIT_EVENT]
    assert "不查验" in passed[0].payload["payload"]["thought"]
    degraded = resolve_mirror_girl_action(context(degraded=True), cmd("pass", degraded=True))
    assert degraded[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    # A target-less inspection is a pass, not a crash.
    assert len(resolve_mirror_girl_action(context(), cmd("inspect", None))) == 1


def test_an_unlabelled_answer_still_renders_a_thought() -> None:
    from app.roles.mirror_girl import _mirror_girl_reasoning_effect

    effect = _mirror_girl_reasoning_effect(
        context(), cmd("inspect", TARGET), 1, None,
        expected_revision=3, source_event_id=None,
    )
    assert "查验 3 号玩家" in effect.payload["payload"]["thought"]


def test_a_missing_or_malformed_target_fact_is_refused() -> None:
    with pytest.raises(ValueError):
        resolve_mirror_girl_action(context(), cmd("inspect", TARGET))
    with pytest.raises(ValueError):
        resolve_mirror_girl_action(
            context(facts={"selected_target": {"seat": 9, "role_label": "x"}}),
            cmd("inspect", TARGET),
        )
    with pytest.raises(TypeError):
        resolve_mirror_girl_action(
            context(facts={"selected_target": {"seat": TARGET, "role_label": None}}),
            cmd("inspect", TARGET),
        )


# ── the disguise reader ────────────────────────────────────────────────────


def _state_with(private) -> GameState:
    from app.core.effect_applier import _Runtime

    state = GameState(
        "d", phase=GamePhase.NIGHT, round_number=1,
        players={TARGET: PlayerState(TARGET, "wolf-killer-werewolf", "werewolf")},
    )
    runtime = _Runtime()
    runtime.private_data[TARGET] = {DISGUISE_KEY: private}
    state._pipeline_runtime = runtime
    return state


@pytest.mark.parametrize(("record", "expected"), [
    ({"role": "wolf-killer-seer", "camp": "good"}, ("wolf-killer-seer", "good")),
    (None, None),
    ("seer", None),
    ({"role": "wolf-killer-seer"}, None),
    ({"role": 3, "camp": "good"}, None),
    ({"role": "wolf-killer-seer", "camp": ["good"]}, None),
])
def test_a_seat_shows_a_disguise_only_when_the_record_is_whole(record, expected) -> None:
    assert disguised_identity(_state_with(record), TARGET) == expected
