"""Awakened Wolf King extension: two claws, a gift, and the last-wolf boundary."""

from __future__ import annotations

from dataclasses import replace

from app.core.action_validator import ActionValidator
from app.core.context_projector import _PUBLIC_RESOURCES
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.awakened_wolf_king import (
    AWAKENED_WOLF_KING_EXPLODE_CONTRACT, AWAKENED_WOLF_KING_SPEC,
    EXPLODE_RESOURCE, resolve_awakened_wolf_king_explode,
    validate_awakened_wolf_king_explode,
    awakened_wolf_king_explode_applicable,
)
from app.roles.registry import builtin_registry
from app.roles.werewolf import WEREWOLF_KILL_CONTRACT
from app.roles.wolf_claw import (
    AWAKENED_CLAW_CONTRACT, AWAKENED_CLAW_GIFT_CONTRACT, AWAKENED_CLAW_GIFT_KEY,
    AWAKENED_CLAW_PASS_CONTRACT, AWAKENED_CLAW_RECEIVED_RESOURCE,
    AWAKENED_CLAW_RESOURCE, AWAKENED_CLAW_SWEEP_CONTRACT,
    resolve_awakened_claw_action, resolve_awakened_claw_pass,
    resolve_awakened_claw_sweep, validate_awakened_claw_action,
    validate_awakened_claw_gift, validate_awakened_claw_pass,
    validate_awakened_claw_sweep, resolve_awakened_claw_gift,
)


def _response_context(
    *, claw: int = 2, reason: str = "wolf_kill", actor_alive: bool = False,
    alive=(2, 3), pack=(1, 2, 3),
) -> ActionContext:
    contract = AWAKENED_CLAW_CONTRACT
    event_id = "event:" + "2" * 16
    return ActionContext(
        "g", 0,
        {"alive_seats": alive, "dead_seats": (1,), "camp_members": pack},
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="dawn",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=AWAKENED_WOLF_KING_SPEC.role_id, actor_alive=actor_alive,
        resources={AWAKENED_CLAW_RESOURCE: claw}, action_key="a",
        source_event_id=event_id,
        trigger_event={"event_id": event_id, "type": "PLAYER_DIED",
                       "target_seat": 1, "cause": reason},
        trigger_reason=reason,
    )


def _pass_context(
    *, claw: int = 2, target: int = 2, labels=(), alive=(1, 2, 3), pack=(1, 2, 3),
) -> ActionContext:
    contract = AWAKENED_CLAW_PASS_CONTRACT
    return ActionContext(
        "g", 0,
        {
            "alive_seats": alive, "dead_seats": (),
            "camp_members": pack,
            "selected_target": {"seat": target, "resource_labels": tuple(labels)},
        },
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=2, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=AWAKENED_WOLF_KING_SPEC.role_id, actor_alive=True,
        resources={AWAKENED_CLAW_RESOURCE: claw}, action_key="a",
    )


def _command(action: str, target: int | None = None, targets=None) -> ActionCommand:
    return ActionCommand(
        action_type=action, target_seat=target, target_seats=targets, reasoning="ok",
    )


def test_spec_owns_two_claws_and_the_shared_kill_contract() -> None:
    assert AWAKENED_WOLF_KING_SPEC.role_id == "wolf-killer-awakened-wolf-king"
    assert AWAKENED_WOLF_KING_SPEC.camp_id == "werewolf"
    assert AWAKENED_WOLF_KING_SPEC.tags == {"wolf"}
    assert AWAKENED_WOLF_KING_SPEC.max_count == 1
    assert AWAKENED_WOLF_KING_SPEC.initial_resources == {
        AWAKENED_CLAW_RESOURCE: 2, EXPLODE_RESOURCE: 1,
    }
    ids = [contract.contract_id for contract in AWAKENED_WOLF_KING_SPEC.contracts]
    assert ids == [
        "werewolf_kill", "awakened_wolf_claw_pass",
        "awakened_wolf_king_explode", "awakened_wolf_claw_sweep",
        "awakened_wolf_claw",
    ]
    assert any(
        contract is WEREWOLF_KILL_CONTRACT
        for contract in AWAKENED_WOLF_KING_SPEC.contracts
    )
    assert AWAKENED_WOLF_KING_SPEC.opening_points == {"night_action"}


def test_single_and_sweep_are_bounded_death_responses() -> None:
    assert AWAKENED_CLAW_CONTRACT.schedule_point is SchedulePoint.DAWN_REACTION
    assert AWAKENED_CLAW_CONTRACT.order == 42
    assert AWAKENED_CLAW_CONTRACT.action_types == ("shoot", "pass")
    assert AWAKENED_CLAW_CONTRACT.response_reasons == {
        "wolf_kill", "exile", "hunter_shot",
    }
    assert AWAKENED_CLAW_SWEEP_CONTRACT.order == 41
    assert AWAKENED_CLAW_SWEEP_CONTRACT.multi_target_actions == {"sweep": 2}
    assert AWAKENED_CLAW_SWEEP_CONTRACT.fallback_action_type == "pass"
    for contract in (AWAKENED_CLAW_CONTRACT, AWAKENED_CLAW_SWEEP_CONTRACT):
        assert contract.response_event_types == {"PLAYER_DIED"}
        assert contract.per_window_limit == 1
        for forbidden in ("self_explode", "poison", "charm", "knight_duel"):
            assert forbidden not in contract.response_reasons
    assert AWAKENED_CLAW_PASS_CONTRACT.schedule_point is SchedulePoint.NIGHT_ACTION
    assert AWAKENED_CLAW_PASS_CONTRACT.order == 56


def test_single_and_sweep_applicability_read_the_claw_count_and_last_wolf() -> None:
    assert AWAKENED_CLAW_CONTRACT.is_applicable(_response_context()) is True
    assert AWAKENED_CLAW_CONTRACT.is_applicable(
        _response_context(reason="exile")
    ) is True
    assert AWAKENED_CLAW_CONTRACT.is_applicable(
        _response_context(reason="hunter_shot")
    ) is True
    for forbidden in ("self_explode", "poison", "charm", "knight_duel"):
        assert AWAKENED_CLAW_CONTRACT.is_applicable(
            _response_context(reason=forbidden)
        ) is False, forbidden
    assert AWAKENED_CLAW_CONTRACT.is_applicable(
        _response_context(claw=0)
    ) is False
    # The last wolf cannot fire: no other pack member is alive.
    assert AWAKENED_CLAW_CONTRACT.is_applicable(
        _response_context(alive=(1,), pack=(1,))
    ) is False
    assert AWAKENED_CLAW_SWEEP_CONTRACT.is_applicable(
        _response_context(claw=2)
    ) is True
    assert AWAKENED_CLAW_SWEEP_CONTRACT.is_applicable(
        _response_context(claw=1)
    ) is False
    assert AWAKENED_CLAW_SWEEP_CONTRACT.is_applicable(
        _response_context(alive=(1,), pack=(1,))
    ) is False


def test_single_claw_validate_and_resolve() -> None:
    assert validate_awakened_claw_action(
        _response_context(), _command("shoot", 2)
    ) == ()
    assert [v.code for v in validate_awakened_claw_action(
        _response_context(claw=0), _command("shoot", 2)
    )] == ["awakened_claw_unavailable"]
    assert [v.code for v in validate_awakened_claw_action(
        _response_context(), _command("shoot")
    )] == ["awakened_claw_target_required"]

    passed = resolve_awakened_claw_action(_response_context(), _command("pass"))
    assert [effect.kind for effect in passed] == [EffectKind.EMIT_EVENT]
    assert passed[0].payload["payload"]["action_type"] == "pass"

    effects = resolve_awakened_claw_action(
        _response_context(), _command("shoot", 2)
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {
        "target": 1, "resource": AWAKENED_CLAW_RESOURCE, "amount": 1,
    }
    assert effects[0].preconditions == {}
    assert effects[1].payload == {
        "target": 2, "amount": 1, "cause": "wolf_king_shot",
    }
    assert effects[2].payload == {
        "event_type": "WOLF_KING_SHOT",
        "payload": {"target_seat": 2, "seat": 1},
    }
    assert effects[3].payload["payload"]["thought"].startswith("决定发动狼王爪")


def test_sweep_validate_and_resolve_two_distinct_targets() -> None:
    assert validate_awakened_claw_sweep(
        _response_context(claw=2), _command("sweep", targets=(2, 3))
    ) == ()
    assert [v.code for v in validate_awakened_claw_sweep(
        _response_context(claw=1), _command("sweep", targets=(2, 3))
    )] == ["awakened_claw_pair_unavailable"]
    assert [v.code for v in validate_awakened_claw_sweep(
        _response_context(), _command("sweep", targets=(2, 2))
    )] == ["awakened_claw_duplicate_target"]
    passed = resolve_awakened_claw_sweep(_response_context(), _command("pass"))
    assert [effect.kind for effect in passed] == [EffectKind.EMIT_EVENT]
    effects = resolve_awakened_claw_sweep(
        _response_context(), _command("sweep", targets=(2, 3))
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE,
        EffectKind.SUBMIT_DAMAGE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
        EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {
        "target": 1, "resource": AWAKENED_CLAW_RESOURCE, "amount": 2,
    }
    assert effects[1].payload["target"] == 2
    assert effects[2].payload["target"] == 3
    shots = [
        effect.payload["payload"]["target_seat"] for effect in effects
        if effect.payload.get("event_type") == "WOLF_KING_SHOT"
    ]
    assert shots == [2, 3]


def test_pass_applicable_validate_and_resolve() -> None:
    assert AWAKENED_CLAW_PASS_CONTRACT.is_applicable(_pass_context()) is True
    assert AWAKENED_CLAW_PASS_CONTRACT.is_applicable(
        _pass_context(claw=0)
    ) is False
    assert AWAKENED_CLAW_PASS_CONTRACT.is_applicable(
        replace(_pass_context(), actor_alive=False)
    ) is False
    assert AWAKENED_CLAW_PASS_CONTRACT.is_applicable(
        _pass_context(alive=(1,), pack=(1,))
    ) is False

    assert validate_awakened_claw_pass(_pass_context(), _command("pass")) == ()
    assert [v.code for v in validate_awakened_claw_pass(
        _pass_context(), _command("pass_claw")
    )] == ["awakened_claw_target_required"]
    assert [v.code for v in validate_awakened_claw_pass(
        _pass_context(target=1), _command("pass_claw", 1)
    )] == ["awakened_claw_self_target"]
    assert [v.code for v in validate_awakened_claw_pass(
        _pass_context(target=9), _command("pass_claw", 9)
    )] == ["awakened_claw_not_a_teammate"]
    assert [v.code for v in validate_awakened_claw_pass(
        _pass_context(labels=(AWAKENED_CLAW_RECEIVED_RESOURCE,)),
        _command("pass_claw", 2),
    )] == ["awakened_claw_already_held"]

    effects = resolve_awakened_claw_pass(
        _pass_context(), _command("pass_claw", 2)
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_RESOURCE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {
        "target": 1, "resource": AWAKENED_CLAW_RESOURCE, "amount": 1,
    }
    assert effects[1].payload == {
        "target": 2, "resource": AWAKENED_CLAW_RECEIVED_RESOURCE, "value": 1,
    }
    assert effects[2].payload["key"] == AWAKENED_CLAW_GIFT_KEY
    assert "觉醒狼王" in effects[2].payload["value"]
    assert effects[3].payload["payload"]["action_type"] == "pass_claw"


def test_explode_is_a_targetless_day_response_that_never_fires_the_claw() -> None:
    assert AWAKENED_WOLF_KING_EXPLODE_CONTRACT.schedule_point is SchedulePoint.DAY_ACTION
    assert AWAKENED_WOLF_KING_EXPLODE_CONTRACT.actions_requiring_target == frozenset()
    assert awakened_wolf_king_explode_applicable(
        ActionContext(
            "g", 0, {}, config_version="a" * 64,
            contract_id=AWAKENED_WOLF_KING_EXPLODE_CONTRACT.contract_id,
            contract_version=1,
            contract_digest=AWAKENED_WOLF_KING_EXPLODE_CONTRACT.stable_digest(),
            round_number=1, schedule_point=SchedulePoint.DAY_ACTION,
            actor_seat=1, actor_role_id=AWAKENED_WOLF_KING_SPEC.role_id,
            actor_alive=True, resources={EXPLODE_RESOURCE: 1}, action_key="a",
        )
    ) is True
    assert validate_awakened_wolf_king_explode(
        _pass_context(), _command("pass")
    ) == ()
    effects = resolve_awakened_wolf_king_explode(
        _pass_context(), _command("explode")
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
        EffectKind.EMIT_EVENT,
    ]
    assert effects[1].payload == {
        "target": 1, "amount": 1, "cause": "self_explode",
    }
    event_types = [
        effect.payload.get("event_type") for effect in effects
        if effect.kind is EffectKind.EMIT_EVENT
    ]
    assert event_types == [
        "PLAYER_REVEALED", "DAY_INTERRUPTED", "WOLF_KING_REASONING",
    ]


def test_every_wolf_pack_role_carries_the_shared_claw_and_the_gift_slot() -> None:
    snapshot = builtin_registry.freeze()
    for role_id in (
        "wolf-killer-werewolf", "wolf-killer-werewolf-king",
        "wolf-killer-wolf-king", "wolf-killer-wolf-beauty",
        "wolf-killer-awakened-wolf-beauty", "wolf-killer-evil-knight",
        "wolf-killer-mask", "wolf-killer-nightmare",
        "wolf-killer-wolf-witch", "wolf-killer-crimson-envoy",
        "wolf-killer-time-wolf",
    ):
        spec = snapshot.require(role_id)
        assert any(
            contract is AWAKENED_CLAW_GIFT_CONTRACT for contract in spec.contracts
        ), role_id
        assert EffectKind.SET_PRIVATE_DATA in spec.allowed_effects, role_id
        assert AWAKENED_CLAW_GIFT_KEY in spec.initial_private_data, role_id
    assert snapshot.require("wolf-killer-awakened-wolf-king") is AWAKENED_WOLF_KING_SPEC
    assert AWAKENED_CLAW_RECEIVED_RESOURCE in _PUBLIC_RESOURCES


def test_generic_validator_accepts_a_dead_awakened_claw_holder() -> None:
    validator = ActionValidator()
    assert validator.validate(
        _response_context(), AWAKENED_CLAW_CONTRACT, _command("shoot", 2)
    ) == ()
    assert {
        item.code for item in validator.validate(
            _response_context(), AWAKENED_CLAW_CONTRACT, _command("shoot", 1)
        )
    } == {"target_not_alive"}


def _gift_context(*, gift="note", reason="wolf_kill", alive=(2, 3)):
    contract = AWAKENED_CLAW_GIFT_CONTRACT
    event_id = "event:" + "4" * 16
    return ActionContext(
        "g", 0,
        {
            "alive_seats": alive, "dead_seats": (1,),
            "camp_members": (1, 2, 3),
            AWAKENED_CLAW_GIFT_KEY: gift,
        },
        config_version="a" * 64, contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="dawn",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=2,
        actor_role_id="wolf-killer-werewolf", actor_alive=False,
        resources={}, action_key="a", source_event_id=event_id,
        trigger_event={"event_id": event_id, "type": "PLAYER_DIED",
                       "target_seat": 2, "cause": reason},
        trigger_reason=reason,
    )


def test_gift_contract_uses_the_private_slot_not_a_claw_resource() -> None:
    contract = AWAKENED_CLAW_GIFT_CONTRACT
    assert contract.action_types == ("shoot", "pass")
    assert contract.response_reasons == {"wolf_kill", "exile", "hunter_shot"}
    assert contract.schedule_point is SchedulePoint.DAWN_REACTION
    assert contract.order == 42
    assert contract.is_applicable(_gift_context()) is True
    assert contract.is_applicable(_gift_context(gift=None)) is False
    assert contract.is_applicable(_gift_context(reason="poison")) is False
    assert contract.is_applicable(_gift_context(alive=(2,))) is False
    assert validate_awakened_claw_gift(
        _gift_context(), _command("shoot", 3)
    ) == ()
    assert validate_awakened_claw_gift(
        _gift_context(), _command("pass")
    ) == ()
    assert validate_awakened_claw_gift(
        _gift_context(gift=None), _command("shoot", 3)
    )[0].code == "awakened_claw_unavailable"
    effects = resolve_awakened_claw_gift(
        _gift_context(), _command("shoot", 3)
    )
    assert [effect.kind for effect in effects] == [
        EffectKind.SET_PRIVATE_DATA, EffectKind.SUBMIT_DAMAGE,
        EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {
        "target": 2, "key": AWAKENED_CLAW_GIFT_KEY, "value": None,
    }
    assert effects[1].payload == {
        "target": 3, "amount": 1, "cause": "wolf_king_shot",
    }
    assert effects[2].payload["event_type"] == "WOLF_KING_SHOT"
    assert resolve_awakened_claw_gift(
        _gift_context(), _command("pass")
    )[0].payload["payload"]["action_type"] == "pass"


def test_gift_contract_is_on_a_real_wolf_spec_and_projects_the_slot() -> None:
    from app.core.context_projector import ContextProjector
    from app.core.role_runtime import initialize_role_resources
    from app.models.game import GameState, PlayerState
    from app.models.pipeline import IssuedActionRequest

    snapshot = builtin_registry.freeze()
    game = GameState(
        "gift-game", phase="night", round_number=1,
        players={
            1: PlayerState(1, "wolf-killer-awakened-wolf-king", "werewolf"),
            2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
            3: PlayerState(3, "wolf-killer-villager", "good"),
        },
    )
    initialize_role_resources(game, snapshot.specs, snapshot.digest)
    game._pipeline_runtime.private_data.setdefault(2, {})[AWAKENED_CLAW_GIFT_KEY] = "note"
    request = IssuedActionRequest(
        2, "wolf-killer-werewolf", AWAKENED_CLAW_GIFT_CONTRACT,
        0, 1, "night", "window", "action",
    )
    context = ContextProjector().project(
        game, request, snapshot,
        source_event_id="event:" + "5" * 16,
        trigger_event={
            "event_id": "event:" + "5" * 16, "type": "PLAYER_DIED",
            "target_seat": 2, "cause": "wolf_kill",
        },
        trigger_reason="wolf_kill",
    )
    assert context.facts[AWAKENED_CLAW_GIFT_KEY] == "note"
    assert AWAKENED_CLAW_GIFT_CONTRACT.is_applicable(context) is True
