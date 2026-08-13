from dataclasses import replace
import traceback
from pathlib import Path
from types import MappingProxyType

import pytest

from app.core.action_resolver import ActionResolver, RuleExecutionError
from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext,
    ActionContract as PipelineActionContract,
    EffectKind,
    GameEffect,
    RoleSpec,
    SchedulePoint,
)


def test_action_core_has_no_state_write_or_builtin_tokens() -> None:
    source = (
        Path("app/core/action_validator.py").read_text("utf-8")
        + Path("app/core/action_resolver.py").read_text("utf-8")
    )
    for token in (
        "accepted_action_keys.add", "mark_dead", "has_antidote =", "has_poison =",
        "check_results.append", '"save"', '"poison"', '"hunter"', '"werewolf"',
    ):
        assert token not in source


PIPELINE_CALLS: list[object] = []
PIPELINE_MODE = "ok"


class GameEffectSubclass(GameEffect):
    pass


def _pipeline_effect(
    context: ActionContext,
    *,
    ordinal: int = 1,
    kind: EffectKind = EffectKind.SUBMIT_DAMAGE,
    target: int | None = 2,
    visibility: tuple[str, ...] = ("ACTOR",),
    source_key: str | None = None,
    revision: int | None = None,
    source_event_id: str | None | object = ...,
    effect_id: str | None = None,
    sort_key: tuple[int, ...] | None = None,
) -> GameEffect:
    event_id = context.source_event_id if source_event_id is ... else source_event_id
    return GameEffect(
        effect_id=effect_id or derive_effect_id(context.action_key, ordinal),
        kind=kind,
        source_action_key=source_key or context.action_key,
        payload={"target": target, "amount": 1},
        visibility=visibility,
        expected_revision=context.revision if revision is None else revision,
        target_seat=target,
        source_event_id=event_id,
        sort_key=sort_key or (ordinal,),
    )


def pipeline_resolve_hook(
    context: ActionContext, command: PipelineActionCommand
) -> object:
    PIPELINE_CALLS.append(command)
    if PIPELINE_MODE == "raise":
        raise RuntimeError("SECRET target/reasoning leaked")
    if PIPELINE_MODE == "list":
        return [_pipeline_effect(context)]
    if PIPELINE_MODE == "subclass":
        item = _pipeline_effect(context)
        return (GameEffectSubclass(**item.__dict__),)
    if PIPELINE_MODE == "accept":
        return (_pipeline_effect(context, kind=EffectKind.ACCEPT_ACTION, target=None),)
    return (_pipeline_effect(context),)


def pipeline_aggregate_hook(
    context: ActionContext, commands: tuple[PipelineActionCommand, ...]
) -> tuple[GameEffect, ...]:
    PIPELINE_CALLS.append(commands)
    return (_pipeline_effect(context),)


def pipeline_react_hook(context: ActionContext) -> tuple[GameEffect, ...]:
    PIPELINE_CALLS.append(context)
    return (_pipeline_effect(context, target=context.actor_seat),)


def pipeline_contract(**changes: object) -> PipelineActionContract:
    values: dict[str, object] = {
        "contract_id": "pipeline-action",
        "schedule_point": SchedulePoint.NIGHT_ACTION,
        "order": 1,
        "action_types": ("kill", "pass"),
        "actions_requiring_target": frozenset({"kill"}),
        "fallback_action_type": "pass",
        "allowed_effects": frozenset({EffectKind.SUBMIT_DAMAGE}),
        "visibility_namespaces": frozenset({"ACTOR"}),
        "resolve": pipeline_resolve_hook,
    }
    values.update(changes)
    return PipelineActionContract(**values)


def pipeline_role(contract: PipelineActionContract, **changes: object) -> RoleSpec:
    values: dict[str, object] = {
        "role_id": "pipeline-role",
        "contracts": (contract,),
        "allowed_effects": frozenset({EffectKind.SUBMIT_DAMAGE}),
        "visibility_namespaces": frozenset({"ACTOR"}),
    }
    values.update(changes)
    return RoleSpec(**values)


def pipeline_context(contract: PipelineActionContract, **changes: object) -> ActionContext:
    values: dict[str, object] = {
        "game_id": "pipeline-game",
        "revision": 4,
        "config_version": "registry-version-secret-free",
        "contract_id": contract.contract_id,
        "contract_version": contract.schema_version,
        "contract_digest": contract.stable_digest(),
        "round_number": 2,
        "phase": "night",
        "window_id": "window",
        "schedule_point": contract.schedule_point,
        "actor_seat": 1,
        "actor_role_id": "pipeline-role",
        "actor_alive": True,
        "action_key": "pipeline:action:1",
        "facts": {"alive_seats": (1, 2, 3)},
    }
    values.update(changes)
    return ActionContext(**values)


def pipeline_command(action: str = "kill", target: int | None = 2) -> PipelineActionCommand:
    return PipelineActionCommand(action_type=action, target_seat=target, reasoning="SECRET")


@pytest.fixture(autouse=True)
def reset_pipeline_hook_state():
    global PIPELINE_MODE
    PIPELINE_MODE = "ok"
    PIPELINE_CALLS.clear()
    yield
    PIPELINE_MODE = "ok"
    PIPELINE_CALLS.clear()


def test_pipeline_resolve_is_pure_adds_accept_and_is_deterministic():
    contract = pipeline_contract()
    role = pipeline_role(contract)
    context = pipeline_context(contract)
    command = pipeline_command()
    before = (context.to_json(), role.to_json(), contract.to_json(), command.to_json())

    first = ActionResolver().resolve_effects(context, role, contract, command)
    second = ActionResolver().resolve_effects(context, role, contract, command)

    assert tuple(effect.kind for effect in first) == (
        EffectKind.ACCEPT_ACTION,
        EffectKind.SUBMIT_DAMAGE,
    )
    assert first[0].payload == {
        "actor_seat": 1, "contract_id": "pipeline-action",
        "window_id": "window", "round_number": 2,
    }
    assert tuple(effect.to_json() for effect in first) == tuple(
        effect.to_json() for effect in second
    )
    assert (context.to_json(), role.to_json(), contract.to_json(), command.to_json()) == before


def test_pipeline_aggregate_sorts_commands_stably_and_allows_self_target():
    contract = pipeline_contract(resolve=None, aggregate=pipeline_aggregate_hook)
    role = pipeline_role(contract)
    context = pipeline_context(contract)
    commands = (
        pipeline_command("pass", None),
        pipeline_command("kill", 3),
        pipeline_command("kill", 1),
        pipeline_command("kill", 2),
    )

    effects = ActionResolver().aggregate_effects(context, role, contract, commands)

    observed = PIPELINE_CALLS[0]
    assert effects[0].payload == {
        "actor_seat": context.actor_seat, "contract_id": contract.contract_id,
        "window_id": context.window_id, "round_number": context.round_number,
    }
    assert type(observed) is tuple
    assert [(item.action_type, item.target_seat) for item in observed] == [
        ("kill", 1), ("kill", 2), ("kill", 3), ("pass", None)
    ]


def test_pipeline_react_requires_bound_response_and_allows_dead_actor_effect():
    contract = pipeline_contract(
        schedule_point=SchedulePoint.DAWN_REACTION,
        resolve=None,
        react=pipeline_react_hook,
        response_event_types=frozenset({"PLAYER_DIED"}),
    )
    role = pipeline_role(contract)
    context = pipeline_context(
        contract,
        actor_alive=False,
        source_event_id="event:0123456789abcdef",
        trigger_event={"event_id": "event:0123456789abcdef", "type": "PLAYER_DIED"},
        facts={"alive_seats": (2, 3), "dead_seats": (1,)},
    )

    effects = ActionResolver().react_effects(context, role, contract)

    assert effects[0].payload == {
        "actor_seat": context.actor_seat, "contract_id": contract.contract_id,
        "window_id": context.window_id, "round_number": context.round_number,
    }
    assert effects[1].target_seat == context.actor_seat
    assert PIPELINE_CALLS == [context]


def test_pipeline_normal_contract_cannot_target_dead_actor():
    contract = pipeline_contract()
    context = pipeline_context(
        contract, actor_alive=False, facts={"alive_seats": (2, 3), "dead_seats": (1,)}
    )

    def dead_actor(ctx, command):
        return (_pipeline_effect(ctx, target=ctx.actor_seat),)

    object.__setattr__(contract, "resolve", dead_actor)
    object.__setattr__(context, "contract_digest", contract.stable_digest())
    with pytest.raises(RuleExecutionError):
        ActionResolver().resolve_effects(
            context, pipeline_role(contract), contract, pipeline_command()
        )


@pytest.mark.parametrize(
    ("method", "arguments", "message"),
    [
        ("resolve_effects", (object(), None, None, None), "context"),
        ("resolve_effects", (None, object(), None, None), "role_spec"),
        ("resolve_effects", (None, None, object(), None), "contract"),
        ("resolve_effects", (None, None, None, object()), "command"),
    ],
)
def test_pipeline_resolve_requires_exact_types(method, arguments, message):
    contract = pipeline_contract()
    valid = [pipeline_context(contract), pipeline_role(contract), contract, pipeline_command()]
    for index, argument in enumerate(arguments):
        if argument is not None:
            valid[index] = argument
    with pytest.raises(TypeError, match=message):
        getattr(ActionResolver(), method)(*valid)


def test_pipeline_aggregate_requires_exact_tuple_and_command_items():
    contract = pipeline_contract(resolve=None, aggregate=pipeline_aggregate_hook)
    role = pipeline_role(contract)
    context = pipeline_context(contract)
    with pytest.raises(TypeError, match="commands must be an exact tuple"):
        ActionResolver().aggregate_effects(context, role, contract, [pipeline_command()])
    with pytest.raises(TypeError, match="exact ActionCommand"):
        ActionResolver().aggregate_effects(context, role, contract, (object(),))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"contract_id": "other"}, "contract id"),
        ({"contract_version": 2}, "contract version"),
        ({"contract_digest": "other"}, "contract digest"),
        ({"schedule_point": SchedulePoint.DAY_ACTION}, "schedule point"),
        ({"actor_role_id": "other"}, "actor role"),
    ],
)
def test_pipeline_rejects_unbound_context(changes, message):
    contract = pipeline_contract()
    with pytest.raises(ValueError, match=message):
        ActionResolver().resolve_effects(
            pipeline_context(contract, **changes),
            pipeline_role(contract), contract, pipeline_command(),
        )


def test_pipeline_rejects_unregistered_equal_contract_and_missing_hook():
    contract = pipeline_contract()
    clone = pipeline_contract()
    with pytest.raises(ValueError, match="registered contract"):
        ActionResolver().resolve_effects(
            pipeline_context(clone), pipeline_role(contract), clone, pipeline_command()
        )
    no_hook = pipeline_contract(resolve=None)
    with pytest.raises(ValueError, match="resolve hook"):
        ActionResolver().resolve_effects(
            pipeline_context(no_hook), pipeline_role(no_hook), no_hook, pipeline_command()
        )


def test_pipeline_requires_aggregate_and_react_hooks_and_response_context():
    contract = pipeline_contract(resolve=None)
    role = pipeline_role(contract)
    context = pipeline_context(contract)
    with pytest.raises(ValueError, match="aggregate hook"):
        ActionResolver().aggregate_effects(context, role, contract, ())
    with pytest.raises(ValueError, match="react hook"):
        ActionResolver().react_effects(context, role, contract)
    reaction = pipeline_contract(resolve=None, react=pipeline_react_hook)
    with pytest.raises(ValueError, match="response context"):
        ActionResolver().react_effects(
            pipeline_context(reaction), pipeline_role(reaction), reaction
        )


@pytest.mark.parametrize(
    ("role_effects", "contract_effects"),
    [
        (frozenset({EffectKind.SUBMIT_DAMAGE}), frozenset({EffectKind.EMIT_EVENT})),
        (frozenset({EffectKind.EMIT_EVENT}), frozenset({EffectKind.SUBMIT_DAMAGE})),
    ],
)
def test_pipeline_effect_kind_must_be_in_role_contract_intersection(
    role_effects, contract_effects
):
    contract = pipeline_contract(allowed_effects=contract_effects)
    role = pipeline_role(contract, allowed_effects=role_effects)
    with pytest.raises(RuleExecutionError) as caught:
        ActionResolver().resolve_effects(
            pipeline_context(contract), role, contract, pipeline_command()
        )
    assert caught.value.__cause__ is None
    assert caught.value.failure_type == "ValueError"


@pytest.mark.parametrize("mode", ["list", "subclass", "accept"])
def test_pipeline_rejects_bad_hook_output_container_subclass_and_accept(mode):
    global PIPELINE_MODE
    PIPELINE_MODE = mode
    contract = pipeline_contract()
    with pytest.raises(RuleExecutionError):
        ActionResolver().resolve_effects(
            pipeline_context(contract), pipeline_role(contract), contract, pipeline_command()
        )


@pytest.mark.parametrize(
    "effect_changes",
    [
        {"source_key": "other"},
        {"revision": 99},
        {"source_event_id": "event:other"},
        {"target": 99},
        {"target": 0},
        {"visibility": ("CAMP",)},
        {"effect_id": "not-canonical"},
        {"sort_key": (99,)},
    ],
)
def test_pipeline_rejects_invalid_effect_boundaries(monkeypatch, effect_changes):
    contract = pipeline_contract()
    context = pipeline_context(contract, source_event_id="event:source")

    def bad_hook(ctx, command):
        return (_pipeline_effect(ctx, **effect_changes),)

    object.__setattr__(contract, "resolve", bad_hook)
    object.__setattr__(context, "contract_digest", contract.stable_digest())
    with pytest.raises(RuleExecutionError):
        ActionResolver().resolve_effects(
            context, pipeline_role(contract), contract, pipeline_command()
        )


def test_pipeline_rejects_duplicate_effect_ids():
    contract = pipeline_contract()
    context = pipeline_context(contract)

    def duplicate(ctx, command):
        item = _pipeline_effect(ctx)
        return (item, item)

    object.__setattr__(contract, "resolve", duplicate)
    object.__setattr__(context, "contract_digest", contract.stable_digest())
    with pytest.raises(RuleExecutionError):
        ActionResolver().resolve_effects(
            context, pipeline_role(contract), contract, pipeline_command()
        )


def test_pipeline_rule_error_is_sanitized_but_keeps_internal_cause():
    global PIPELINE_MODE
    PIPELINE_MODE = "raise"
    contract = pipeline_contract()
    context = pipeline_context(contract)
    with pytest.raises(RuleExecutionError) as caught:
        ActionResolver().resolve_effects(
            context, pipeline_role(contract), contract, pipeline_command()
        )
    rendered = "\n".join(
        (str(caught.value), repr(caught.value), "".join(traceback.format_exception(caught.value)))
    )
    assert "role_version=1" in rendered and "correlation_id=" in rendered
    assert context.config_version not in rendered and context.action_key not in rendered
    assert "SECRET" not in rendered and "target" not in rendered
    assert caught.value.__cause__ is None
    assert not hasattr(caught.value, "config_version")
    assert not hasattr(caught.value, "action_key")
    assert caught.value.failure_type == "RuntimeError"


def test_pipeline_rejects_more_than_64_hook_effects_before_item_validation():
    contract = pipeline_contract()
    context = pipeline_context(contract)
    invalid = object()

    def too_many(ctx, command):
        return tuple([invalid] * 65)

    object.__setattr__(contract, "resolve", too_many)
    object.__setattr__(context, "contract_digest", contract.stable_digest())
    with pytest.raises(RuleExecutionError) as caught:
        ActionResolver().resolve_effects(
            context, pipeline_role(contract), contract, pipeline_command()
        )
    assert caught.value.failure_type == "ValueError"


@pytest.mark.parametrize("alive", [(), (1, True)])
def test_pipeline_rejects_malformed_visible_targets(alive):
    contract = pipeline_contract()
    with pytest.raises(RuleExecutionError):
        ActionResolver().resolve_effects(
            pipeline_context(contract, facts={"alive_seats": alive}),
            pipeline_role(contract), contract, pipeline_command(),
        )


@pytest.mark.parametrize("raised", [KeyboardInterrupt(), SystemExit()])
def test_pipeline_base_exceptions_propagate(monkeypatch, raised):
    contract = pipeline_contract()
    context = pipeline_context(contract)

    def interrupt(ctx, command):
        raise raised

    object.__setattr__(contract, "resolve", interrupt)
    object.__setattr__(context, "contract_digest", contract.stable_digest())
    with pytest.raises(type(raised)):
        ActionResolver().resolve_effects(
            context, pipeline_role(contract), contract, pipeline_command()
        )


