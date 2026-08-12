from __future__ import annotations

import hashlib
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
    with pytest.raises((TypeError, ValueError), match="JSON|mapping"):
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


def test_context_freezes_trigger_and_aggregation_inputs() -> None:
    trigger = {"event_type": "PLAYER_DIED", "seats": [2]}
    summaries = [{"actor": 1, "action": "kill"}]
    aggregate = {"target": 2, "votes": [1]}
    ctx = _context(
        trigger_event=trigger,
        trigger_reason="night_damage",
        accepted_command_summaries=summaries,
        aggregate_result=aggregate,
    )
    trigger["seats"].append(3)
    summaries[0]["action"] = "pass"
    aggregate["votes"].append(4)

    assert ctx.trigger_event["seats"] == (2,)
    assert ctx.accepted_command_summaries[0]["action"] == "kill"
    assert ctx.aggregate_result["votes"] == (1,)
    assert ActionContext.from_json(ctx.to_json()) == ctx


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("trigger_event", []),
        ("accepted_command_summaries", object()),
        ("aggregate_result", []),
        ("counters", []),
    ],
)
def test_context_rejects_invalid_container_shapes(
    field: str, bad_value: object
) -> None:
    with pytest.raises(TypeError, match=field):
        _context(**{field: bad_value})


@pytest.mark.parametrize("field", ["facts", "resources", "counters"])
@pytest.mark.parametrize("bad_value", [[], (), "value", 1])
def test_context_mapping_fields_reject_every_non_mapping_shape(
    field: str, bad_value: object
) -> None:
    with pytest.raises(TypeError, match=field):
        _context(**{field: bad_value})


def test_context_rejects_non_sequence_command_summaries_after_freeze() -> None:
    with pytest.raises(TypeError, match="accepted_command_summaries"):
        _context(accepted_command_summaries={"action": "pass"})


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("game_id", 1),
        ("config_version", 1),
        ("phase", 1),
        ("window_id", 1),
        ("actor_role_id", 1),
        ("action_key", 1),
        ("source_event_id", 1),
        ("trigger_reason", 1),
    ],
)
def test_context_rejects_coercible_scalar_types(field: str, bad_value: object) -> None:
    with pytest.raises(TypeError, match=field):
        _context(**{field: bad_value})


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
    assert contract.stable_digest() == hashlib.sha256(
        contract.to_json().encode("utf-8")
    ).hexdigest()


def test_contract_from_json_rejects_hook_references() -> None:
    raw = json.loads(_contract().to_json())
    raw["resolve"] = "trusted.module.resolve"
    with pytest.raises(ValueError, match="Hook.*registry"):
        ActionContract.from_json(json.dumps(raw))


def test_contract_constructor_rejects_non_callable_hook() -> None:
    with pytest.raises(ValueError, match="Hook.*registry"):
        _contract(resolve="trusted.module.resolve")


def test_contract_from_json_accepts_omitted_optional_collections() -> None:
    raw = {
        "schema_version": 1,
        "contract_id": "passive",
        "schedule_point": "day_action",
        "order": 1,
        "action_types": ["pass"],
        "actions_requiring_target": [],
        "fallback_action_type": "pass",
    }
    contract = ActionContract.from_json(json.dumps(raw))
    assert contract.allowed_effects == frozenset()
    assert contract.response_event_types == frozenset()


def test_contract_from_json_rejects_non_array_collection_shape() -> None:
    raw = json.loads(_contract().to_json())
    raw["actions_requiring_target"] = "kill"
    with pytest.raises(TypeError, match="actions_requiring_target"):
        ActionContract.from_json(json.dumps(raw))


def test_contract_from_json_rejects_non_array_allowed_effects() -> None:
    raw = json.loads(_contract().to_json())
    raw["allowed_effects"] = "submit_damage"
    with pytest.raises((TypeError, ValueError), match="effect"):
        ActionContract.from_json(json.dumps(raw))


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


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("schema_version", True),
        ("contract_id", 1),
        ("order", True),
        ("order", 1.0),
        ("action_types", ("kill", 1)),
        ("actions_requiring_target", frozenset({1})),
        ("visibility_namespaces", ("public",)),
        ("fallback_action_type", 1),
        ("visibility_namespaces", frozenset({1})),
        ("response_event_types", frozenset({1})),
        ("response_reasons", frozenset({1})),
        ("per_window_limit", True),
        ("per_round_limit", 1.0),
        ("per_game_limit", "1"),
    ],
)
def test_contract_rejects_coercible_scalar_and_sequence_types(
    field: str, bad_value: object
) -> None:
    with pytest.raises(TypeError, match=field):
        _contract(**{field: bad_value})


def test_role_rejects_unknown_effect_kind() -> None:
    with pytest.raises(ValueError, match="effect kind"):
        RoleSpec(role_id="role", allowed_effects=frozenset({"invalid"}))


@pytest.mark.parametrize("field", ["initial_resources", "initial_private_data"])
@pytest.mark.parametrize("bad_value", [[], (), "value", 1])
def test_role_mapping_fields_reject_every_non_mapping_shape(
    field: str, bad_value: object
) -> None:
    with pytest.raises(TypeError, match=field):
        RoleSpec(role_id="role", **{field: bad_value})


def test_role_with_hook_contract_serializes_descriptor_but_cannot_restore_it() -> None:
    def resolve_hook() -> tuple[()]:
        return ()

    role = RoleSpec(role_id="role", contracts=(_contract(resolve=resolve_hook),))
    encoded = json.loads(role.to_json())
    assert encoded["contracts"][0]["resolve"].endswith("resolve_hook")
    assert role.stable_digest() == hashlib.sha256(role.to_json().encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match="Hook.*registry"):
        RoleSpec.from_json(role.to_json())


def test_role_from_json_requires_contract_mappings() -> None:
    raw = json.loads(RoleSpec(role_id="role").to_json())
    raw["contracts"] = ["not-a-contract"]
    with pytest.raises(TypeError, match="contracts"):
        RoleSpec.from_json(json.dumps(raw))


def test_role_from_json_requires_contract_array() -> None:
    raw = json.loads(RoleSpec(role_id="role").to_json())
    raw["contracts"] = "not-an-array"
    with pytest.raises(TypeError, match="contracts"):
        RoleSpec.from_json(json.dumps(raw))


def test_role_from_json_restores_embedded_contract_mapping() -> None:
    role = RoleSpec(role_id="role", contracts=(_contract(),))
    restored = RoleSpec.from_json(role.to_json())
    assert restored == role


def test_role_from_json_accepts_omitted_optional_collections() -> None:
    role = RoleSpec.from_json('{"schema_version": 1, "role_id": "role"}')
    assert role.contracts == ()
    assert role.allowed_effects == frozenset()


def test_role_from_json_rejects_non_array_frozenset_field() -> None:
    raw = json.loads(RoleSpec(role_id="role").to_json())
    raw["tags"] = "passive"
    with pytest.raises(TypeError, match="tags"):
        RoleSpec.from_json(json.dumps(raw))


def test_role_from_mapping_requires_tuple_contracts() -> None:
    with pytest.raises(TypeError, match="contracts"):
        RoleSpec.from_mapping({"role_id": "role", "contracts": []})


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("schema_version", 1.0),
        ("role_id", 1),
        ("display_name", 1),
        ("camp_id", 1),
        ("contracts", ("not-a-contract",)),
        ("visibility_namespaces", frozenset({1})),
        ("tags", frozenset({1})),
        ("dependencies", frozenset({1})),
        ("exclusions", frozenset({1})),
        ("min_count", True),
        ("max_count", 1.0),
        ("instructions", 1),
    ],
)
def test_role_rejects_coercible_scalar_and_sequence_types(
    field: str, bad_value: object
) -> None:
    with pytest.raises((TypeError, ValueError), match=field):
        RoleSpec(role_id="role", **{field: bad_value})


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


def test_request_rejects_non_contract_container() -> None:
    with pytest.raises(TypeError, match="contract"):
        IssuedActionRequest(
            actor_seat=1,
            role_id="werewolf",
            contract="not-a-contract",
            context_revision=3,
            round_number=2,
            phase="night",
            window_id="window",
            action_key="action",
        )


def test_request_from_json_requires_contract_mapping() -> None:
    contract = _contract()
    raw = {
        "schema_version": 1,
        "actor_seat": 1,
        "role_id": "werewolf",
        "contract": "not-a-contract",
        "context_revision": 3,
        "round_number": 2,
        "phase": "night",
        "window_id": "window",
        "action_key": "action",
    }
    with pytest.raises(TypeError, match="contract"):
        IssuedActionRequest.from_json(json.dumps(raw))


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("schema_version", "1"),
        ("actor_seat", True),
        ("role_id", 1),
        ("context_revision", 1.0),
        ("round_number", True),
        ("phase", 1),
        ("window_id", 1),
        ("action_key", 1),
    ],
)
def test_request_rejects_coercible_scalar_types(field: str, bad_value: object) -> None:
    values: dict[str, object] = {
        "actor_seat": 1,
        "role_id": "werewolf",
        "contract": _contract(),
        "context_revision": 3,
        "round_number": 2,
        "phase": "night",
        "window_id": "window",
        "action_key": "action",
    }
    values[field] = bad_value
    with pytest.raises(TypeError, match=field):
        IssuedActionRequest(**values)


def test_effect_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="effect kind"):
        GameEffect(effect_id="effect", kind="invalid", source_action_key="action")


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("schema_version", True),
        ("effect_id", 1),
        ("source_action_key", 1),
        ("visibility", ["ACTOR"]),
        ("visibility", ("ACTOR", 1)),
        ("expected_revision", True),
        ("target_seat", 1.0),
        ("source_event_id", 1),
        ("sort_key", [1, 2]),
        ("sort_key", (1, True)),
    ],
)
def test_effect_rejects_coercible_scalar_and_sequence_types(
    field: str, bad_value: object
) -> None:
    with pytest.raises(TypeError, match=field):
        GameEffect(
            effect_id="effect",
            kind=EffectKind.EMIT_EVENT,
            source_action_key="action",
            **{field: bad_value},
        )


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("schema_version", True),
        ("code", 1),
        ("message", 1),
    ],
)
def test_violation_rejects_coercible_scalar_types(
    field: str, bad_value: object
) -> None:
    values = {"code": "invalid", "message": "invalid command", field: bad_value}
    with pytest.raises(TypeError, match=field):
        RuleViolation(**values)


@pytest.mark.parametrize(
    "factory, field",
    [
        (lambda value: RuleViolation("code", "message", details=value), "details"),
        (
            lambda value: GameEffect(
                effect_id="effect",
                kind=EffectKind.EMIT_EVENT,
                source_action_key="action",
                payload=value,
            ),
            "payload",
        ),
        (
            lambda value: GameEffect(
                effect_id="effect",
                kind=EffectKind.EMIT_EVENT,
                source_action_key="action",
                preconditions=value,
            ),
            "preconditions",
        ),
    ],
)
@pytest.mark.parametrize("bad_value", [[], (), "value", 1])
def test_remaining_mapping_fields_reject_every_non_mapping_shape(
    factory: object, field: str, bad_value: object
) -> None:
    with pytest.raises(TypeError, match=field):
        factory(bad_value)


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


def test_json_restore_does_not_coerce_non_array_sequence_fields() -> None:
    effect_raw = json.loads(
        GameEffect(
            effect_id="e", kind=EffectKind.EMIT_EVENT, source_action_key="a"
        ).to_json()
    )
    effect_raw["visibility"] = "ACTOR"
    with pytest.raises(TypeError, match="visibility"):
        GameEffect.from_json(json.dumps(effect_raw))


def test_serialization_rejects_unsupported_enum_and_callable_values() -> None:
    class ForeignEnum(str, Enum):
        VALUE = "value"

    with pytest.raises(TypeError, match="JSON"):
        _context(facts={"bad": ForeignEnum.VALUE})
    with pytest.raises(TypeError, match="JSON"):
        _context(facts={"bad": lambda: None})
