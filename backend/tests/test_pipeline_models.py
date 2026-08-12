from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from enum import Enum
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from app.models.pipeline import (
    ActionCommand,
    ActionContext,
    ActionContract,
    EffectKind,
    GameEffect,
    IssuedActionRequest,
    RoleSpec,
    RuleViolation,
    SchedulePoint,
)


def _context(**changes: object) -> ActionContext:
    values: dict[str, object] = {
        "game_id": "game-1",
        "revision": 3,
        "config_version": "config-v1",
        "round_number": 2,
        "phase": "night",
        "window_id": "window-1",
        "schedule_point": SchedulePoint.NIGHT_ACTION,
        "actor_seat": 1,
        "actor_role_id": "werewolf",
        "actor_alive": True,
        "resources": {"charges": 1},
        "facts": {"alive_seats": [1, 2], "public": {"phase": "night"}},
        "action_key": "action-1",
        "counters": {"window": 0},
        "source_event_id": None,
    }
    values.update(changes)
    return ActionContext.from_mapping(**values)


def _contract(**changes: object) -> ActionContract:
    values: dict[str, object] = {
        "contract_id": "werewolf-kill",
        "schedule_point": SchedulePoint.NIGHT_ACTION,
        "order": 10,
        "action_types": ("kill", "pass"),
        "actions_requiring_target": frozenset({"kill"}),
        "fallback_action_type": "pass",
        "allowed_effects": frozenset({EffectKind.SUBMIT_DAMAGE}),
        "required_resources": {"energy": 1},
    }
    values.update(changes)
    return ActionContract(**values)


def test_schedule_and_effect_enums_are_complete_and_stable() -> None:
    assert [point.value for point in SchedulePoint] == [
        "game_setup",
        "night_action",
        "night_commit",
        "dawn_reaction",
        "day_action",
        "vote_action",
        "round_end",
        "game_end",
    ]
    assert [kind.value for kind in EffectKind] == [
        "accept_action",
        "consume_resource",
        "set_resource",
        "set_private_data",
        "add_status",
        "remove_status",
        "add_relation",
        "remove_relation",
        "record_private_fact",
        "submit_damage",
        "submit_protection",
        "mark_death",
        "emit_event",
    ]


def test_action_context_is_deeply_frozen_and_stable() -> None:
    raw = {"alive_seats": [1, 2], "public": {"phase": "night"}}
    ctx = _context(facts=raw)
    raw["alive_seats"].append(3)
    raw["public"]["phase"] = "day"

    assert isinstance(ctx.facts, MappingProxyType)
    assert ctx.facts["alive_seats"] == (1, 2)
    assert isinstance(ctx.facts["public"], MappingProxyType)
    with pytest.raises(TypeError):
        ctx.facts["public"]["phase"] = "day"
    with pytest.raises(FrozenInstanceError):
        ctx.phase = "day"

    encoded = ctx.to_json()
    restored = ActionContext.from_json(encoded)
    assert restored == ctx
    assert json.loads(encoded)["schedule_point"] == "night_action"
    assert restored.stable_digest() == ctx.stable_digest()
    assert len(ctx.stable_digest()) == 64


def test_action_context_supports_plan_defaults() -> None:
    ctx = ActionContext.from_mapping(
        game_id="g", revision=3, facts={"alive_seats": [1, 2]}
    )
    assert ctx.schema_version == 1
    assert ctx.config_version == ""
    assert ctx.round_number == 0
    assert ctx.resources == {}
    assert ctx.counters == {}


@pytest.mark.parametrize(
    "bad_value",
    [object(), {1: "not-a-string-key"}, {"value": float("nan")}, {"value": {1, 2}}],
)
def test_context_rejects_values_that_are_not_strict_json(bad_value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="JSON"):
        _context(facts=bad_value)


@pytest.mark.parametrize(
    "payload, error",
    [
        ({"schema_version": 99}, "schema_version"),
        ({"unknown": True}, "unknown field"),
    ],
)
def test_context_rejects_unknown_versions_and_fields(
    payload: dict[str, object], error: str
) -> None:
    raw = json.loads(_context().to_json())
    raw.update(payload)
    with pytest.raises(ValueError, match=error):
        ActionContext.from_json(json.dumps(raw))


@pytest.mark.parametrize("raw", ["[]", "null", "not-json"])
def test_context_rejects_invalid_json_documents(raw: str) -> None:
    with pytest.raises(ValueError):
        ActionContext.from_json(raw)


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"revision": True}, "revision"),
        ({"round_number": False}, "round_number"),
        ({"actor_seat": "1"}, "actor_seat"),
        ({"actor_alive": 1}, "actor_alive"),
        ({"schedule_point": "unknown"}, "schedule_point"),
        ({"counters": {1: 0}}, "keys"),
        ({"counters": {"window": True}}, "integers"),
    ],
)
def test_context_rejects_invalid_strict_fields(
    changes: dict[str, object], error: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        _context(**changes)


def test_context_accepts_finite_json_numbers() -> None:
    ctx = _context(facts={"ratio": 1.25})
    assert ctx.facts["ratio"] == 1.25


def test_action_command_is_strict_frozen_and_bounded() -> None:
    command = ActionCommand(
        schema_version=1, action_type="pass", target_seat=None, reasoning="safe"
    )
    assert command.stable_digest() == ActionCommand.from_json(command.to_json()).stable_digest()
    with pytest.raises(ValidationError):
        command.reasoning = "changed"
    with pytest.raises(ValidationError):
        ActionCommand(
            schema_version=1,
            action_type="pass",
            target_seat=None,
            reasoning="safe",
            unknown=True,
        )
    with pytest.raises(ValidationError):
        ActionCommand(
            schema_version=2, action_type="pass", target_seat=None, reasoning="safe"
        )
    with pytest.raises(ValidationError):
        ActionCommand(
            schema_version=1, action_type="pass", target_seat=None, reasoning="x" * 501
        )
    with pytest.raises(ValidationError):
        ActionCommand(
            schema_version=1, action_type="kill", target_seat=True, reasoning="strict"
        )


def test_contract_role_request_violation_and_effect_are_frozen_values() -> None:
    contract = _contract()
    role = RoleSpec(
        role_id="werewolf",
        display_name="Werewolf",
        camp_id="werewolf",
        contracts=(contract,),
        initial_resources={"energy": 1},
        initial_private_data={"known": [2]},
        visibility_namespaces=frozenset({"public", "camp"}),
        allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE}),
    )
    request = IssuedActionRequest(
        actor_seat=1,
        role_id=role.role_id,
        contract=contract,
        context_revision=3,
        round_number=2,
        phase="night",
        window_id="window-1",
        action_key="action-1",
    )
    violation = RuleViolation("invalid_target", "target must be alive")
    payload = {"target": 4, "causes": ["attack"]}
    effect = GameEffect(
        effect_id="effect-1",
        kind=EffectKind.SUBMIT_DAMAGE,
        source_action_key=request.action_key,
        payload=payload,
        visibility=("SERVER_ONLY",),
        expected_revision=3,
        target_seat=4,
        preconditions={"alive": True},
        sort_key=(10, 1),
    )
    payload["causes"].append("poison")

    assert contract.action_types == ("kill", "pass")
    assert isinstance(contract.required_resources, MappingProxyType)
    assert isinstance(role.initial_private_data, MappingProxyType)
    assert role.initial_private_data["known"] == (2,)
    assert request.contract is contract
    assert violation.schema_version == 1
    assert effect.payload["causes"] == ("attack",)
    assert isinstance(effect.preconditions, MappingProxyType)
    with pytest.raises(TypeError):
        effect.payload["target"] = 5
    with pytest.raises(FrozenInstanceError):
        effect.kind = EffectKind.MARK_DEATH

    for value in (contract, role, request, violation, effect):
        assert len(value.stable_digest()) == 64
        assert json.loads(value.to_json())["schema_version"] == 1


def test_all_dataclass_values_round_trip_from_json() -> None:
    contract = _contract()
    role = RoleSpec(
        role_id="werewolf",
        contracts=(contract,),
        allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE}),
    )
    request = IssuedActionRequest(
        actor_seat=1,
        role_id="werewolf",
        contract=contract,
        context_revision=3,
        round_number=2,
        phase="night",
        window_id="window",
        action_key="action",
    )
    violation = RuleViolation("invalid", "invalid command", details={"seat": 1})
    effect = GameEffect(
        effect_id="effect",
        kind=EffectKind.SUBMIT_DAMAGE,
        source_action_key="action",
        payload={"target": 2},
    )

    for value in (contract, role, request, violation, effect):
        restored = type(value).from_json(value.to_json())
        assert restored == value
        assert restored.stable_digest() == value.stable_digest()


def test_contract_serializes_hooks_by_stable_qualified_name() -> None:
    def resolve_hook() -> tuple[()]:
        return ()

    contract = _contract(resolve=resolve_hook)
    encoded = json.loads(contract.to_json())
    assert encoded["resolve"].endswith("resolve_hook")


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"schedule_point": "invalid"}, "schedule_point"),
        ({"allowed_effects": frozenset({"invalid"})}, "effect kind"),
        ({"required_resources": {1: 1}}, "keys"),
        ({"required_resources": {"charge": True}}, "integers"),
    ],
)
def test_contract_rejects_invalid_frozen_fields(
    changes: dict[str, object], error: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        _contract(**changes)


def test_role_rejects_unknown_effect_kind() -> None:
    with pytest.raises(ValueError, match="effect kind"):
        RoleSpec(role_id="role", allowed_effects=frozenset({"invalid"}))


def test_request_converts_a_serialized_contract_to_frozen_value() -> None:
    contract = _contract()
    request = IssuedActionRequest(
        actor_seat=1,
        role_id="werewolf",
        contract=json.loads(contract.to_json()),
        context_revision=3,
        round_number=2,
        phase="night",
        window_id="window",
        action_key="action",
    )
    assert request.contract == contract


def test_effect_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="effect kind"):
        GameEffect(effect_id="effect", kind="invalid", source_action_key="action")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _contract(schema_version=2),
        lambda: RoleSpec(
            schema_version=2,
            role_id="villager",
            display_name="Villager",
            camp_id="good",
        ),
        lambda: IssuedActionRequest(
            schema_version=2,
            actor_seat=1,
            role_id="villager",
            contract=_contract(),
            context_revision=0,
            round_number=0,
            phase="day",
            window_id="w",
            action_key="a",
        ),
        lambda: RuleViolation("code", "message", schema_version=2),
        lambda: GameEffect(
            schema_version=2,
            effect_id="e",
            kind=EffectKind.EMIT_EVENT,
            source_action_key="a",
        ),
    ],
)
def test_frozen_values_reject_unknown_schema_versions(factory: object) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        factory()


def test_model_from_mapping_rejects_unknown_fields_and_round_trips() -> None:
    effect = GameEffect.from_mapping(
        {
            "schema_version": 1,
            "effect_id": "event-1",
            "kind": "emit_event",
            "source_action_key": "action-1",
            "payload": {"event": "PLAYER_DIED"},
        }
    )
    assert effect == GameEffect.from_json(effect.to_json())
    with pytest.raises(ValueError, match="unknown field"):
        GameEffect.from_mapping(
            {
                "schema_version": 1,
                "effect_id": "event-1",
                "kind": "emit_event",
                "source_action_key": "action-1",
                "surprise": True,
            }
        )

    with pytest.raises(TypeError, match="either"):
        GameEffect.from_mapping(
            {"effect_id": "e", "kind": "emit_event", "source_action_key": "a"},
            schema_version=1,
        )
    with pytest.raises(TypeError, match="mapping"):
        GameEffect.from_mapping(["not", "a", "mapping"])


def test_serialization_rejects_unsupported_enum_and_callable_values() -> None:
    class ForeignEnum(str, Enum):
        VALUE = "value"

    with pytest.raises(TypeError, match="JSON"):
        _context(facts={"bad": ForeignEnum.VALUE})
    with pytest.raises(TypeError, match="JSON"):
        _context(facts={"bad": lambda: None})
