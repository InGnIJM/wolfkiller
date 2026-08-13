from dataclasses import replace
import traceback
from types import MappingProxyType

import pytest

from app.core.action_resolver import ActionResolver, RuleExecutionError
from app.core.effect_applier import derive_effect_id
from app.core.action_validator import ActionValidationError, ActionValidator
from app.models.actions import DeathReport, NightAction
from app.models.contracts import AcceptedAction, ActionCommand
from app.models.game import Camp, GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand as PipelineActionCommand,
    ActionContext,
    ActionContract as PipelineActionContract,
    EffectKind,
    GameEffect,
    RoleSpec,
    SchedulePoint,
)
from app.roles.registry import builtin_registry


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


def make_player(seat: int, role: str, camp: str, **kwargs) -> PlayerState:
    player = PlayerState(
        seat_number=seat, role=role, camp=camp, is_alive=kwargs.get("is_alive", True)
    )
    if "witch" in role:
        player.has_antidote = kwargs.get("has_antidote", True)
        player.has_poison = kwargs.get("has_poison", True)
    if "hunter" in role:
        player.has_gun = kwargs.get("has_gun", True)
    return player


def make_state(players: list[PlayerState]) -> GameState:
    state = GameState(
        game_id="test", config=GameConfig(), phase=GamePhase.NIGHT, round_number=1
    )
    state.players = {player.seat_number: player for player in players}
    return state


def request_for(state: GameState, seat: int):
    return next(
        request
        for request in builtin_registry.build_requests(
            state, {player_seat: object() for player_seat in state.players}, state.phase
        )
        if request.actor_seat == seat
    )


def accept(state: GameState, seat: int, action_type: str, target_seat: int | None = None):
    return ActionValidator().validate_and_accept(
        state,
        request_for(state, seat),
        {"action_type": action_type, "target_seat": target_seat, "reasoning": "x"},
    )


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


class TestActionResolver:
    def test_resolver_rejects_unaccepted_night_action(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
        ])

        with pytest.raises(TypeError, match="AcceptedAction"):
            ActionResolver().resolve(
                state, [NightAction(player_seat=1, action_type="kill", target_seat=2)]
            )

    def test_resolves_accepted_wolf_majority_without_random_tie_breaking(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
            make_player(3, "wolf-killer-werewolf", "werewolf"),
            make_player(4, "wolf-killer-villager", "good"),
            make_player(5, "wolf-killer-villager", "good"),
        ])

        deaths = ActionResolver().resolve(state, [
            accept(state, 1, "kill", 4),
            accept(state, 2, "kill", 4),
            accept(state, 3, "kill", 5),
        ])

        assert [(death.player_seat, death.cause) for death in deaths] == [(4, "wolf_kill")]

    def test_legacy_skips_missing_or_already_dead_damage_targets(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good", is_alive=False),
        ])
        request = request_for(state, 1)
        dead_kill = AcceptedAction(
            request=request,
            command=ActionCommand(action_type="kill", target_seat=2, reasoning="x"),
        )
        assert ActionResolver().resolve(state, [dead_kill]) == []

        state.players[3] = make_player(3, "wolf-killer-witch", "good")
        poison = AcceptedAction(
            request=request_for(state, 3),
            command=ActionCommand(action_type="poison", target_seat=99, reasoning="x"),
        )
        assert ActionResolver().resolve(state, [poison]) == []

    def test_resolves_accepted_seer_check(self):
        state = make_state([
            make_player(1, "wolf-killer-seer", "good"),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
        ])

        assert ActionResolver().resolve(state, [accept(state, 1, "check", 2)]) == []
        assert state.players[1].check_results == [
            {"target_seat": 2, "result": "werewolf", "round": 1}
        ]

    def test_resolves_accepted_save_at_the_recorded_wolf_target_without_second_consumption(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good", has_antidote=True),
            make_player(3, "wolf-killer-villager", "good"),
        ])
        kill = accept(state, 1, "kill", 3)
        state.last_wolf_kill_target = 3
        save = accept(state, 2, "save", 3)

        assert ActionResolver().resolve(state, [kill, save]) == []
        assert state.players[2].has_antidote is False

    def test_resolver_does_not_apply_an_accepted_save_for_a_different_target(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good"),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-villager", "good"),
        ])
        kill = accept(state, 1, "kill", 3)
        wrong_save = AcceptedAction(
            request=request_for(state, 2),
            command=ActionCommand(action_type="save", target_seat=4, reasoning="x"),
        )

        deaths = ActionResolver().resolve(state, [kill, wrong_save])

        assert [(death.player_seat, death.cause) for death in deaths] == [(3, "wolf_kill")]

    def test_resolves_accepted_poison_without_second_consumption(self):
        state = make_state([
            make_player(1, "wolf-killer-witch", "good", has_poison=True),
            make_player(2, "wolf-killer-werewolf", "werewolf"),
        ])

        deaths = ActionResolver().resolve(state, [accept(state, 1, "poison", 2)])

        assert [(death.player_seat, death.cause) for death in deaths] == [(2, "poison")]
        assert state.players[1].has_poison is False

    def test_rejects_multiple_witch_potion_actions_before_resolving_them(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good", has_antidote=False, has_poison=True),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-villager", "good"),
        ])
        kill = accept(state, 1, "kill", 3)
        witch_request = request_for(state, 2)
        save = AcceptedAction(
            request=witch_request,
            command=ActionCommand(action_type="save", target_seat=3, reasoning="x"),
        )
        poison = AcceptedAction(
            request=witch_request,
            command=ActionCommand(action_type="poison", target_seat=4, reasoning="x"),
        )

        with pytest.raises(ActionValidationError, match="multiple witch actions"):
            ActionResolver().resolve(state, [kill, save, poison])

        assert state.players[3].is_alive is True
        assert state.players[4].is_alive is True
        assert state.players[2].has_antidote is False
        assert state.players[2].has_poison is True

    @pytest.mark.parametrize(
        ("first_action_type", "second_action_type"),
        [
            ("pass", "pass"),
            ("pass", "poison"),
            ("pass", "save"),
        ],
    )
    def test_rejects_multiple_witch_actions_including_pass_before_state_changes(
        self, first_action_type: str, second_action_type: str
    ):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-witch", "good"),
            make_player(3, "wolf-killer-villager", "good"),
            make_player(4, "wolf-killer-villager", "good"),
        ])
        kill = accept(state, 1, "kill", 3)
        witch_request = request_for(state, 2)

        def witch_action(action_type: str) -> AcceptedAction:
            target_seat = {"save": 3, "poison": 4}.get(action_type)
            return AcceptedAction(
                request=witch_request,
                command=ActionCommand(
                    action_type=action_type, target_seat=target_seat, reasoning="x"
                ),
            )

        with pytest.raises(ActionValidationError, match="multiple witch actions"):
            ActionResolver().resolve(
                state,
                [kill, witch_action(first_action_type), witch_action(second_action_type)],
            )

        assert state.last_wolf_kill_target is None
        assert state.players[3].is_alive is True
        assert state.players[4].is_alive is True

    def test_allows_duplicate_passes_from_non_witch_contracts(self):
        state = make_state([
            make_player(1, "wolf-killer-seer", "good"),
        ])
        seer_request = request_for(state, 1)
        actions = [
            AcceptedAction(
                request=seer_request,
                command=ActionCommand(action_type="pass", target_seat=None, reasoning="x"),
            ),
            AcceptedAction(
                request=seer_request,
                command=ActionCommand(action_type="pass", target_seat=None, reasoning="x"),
            ),
        ]

        assert ActionResolver().resolve(state, actions) == []

    @pytest.mark.parametrize("contract_id", ["werewolf_kill", "future_night_action"])
    def test_does_not_treat_forged_potions_from_non_witch_contract_as_witch_actions(
        self, contract_id: str
    ):
        state = make_state([
            make_player(1, "wolf-killer-witch", "good"),
        ])
        witch_request = request_for(state, 1)
        forged_request = replace(
            witch_request,
            contract=replace(witch_request.contract, contract_id=contract_id),
        )
        actions = [
            AcceptedAction(
                request=forged_request,
                command=ActionCommand(action_type="save", target_seat=2, reasoning="x"),
            ),
            AcceptedAction(
                request=forged_request,
                command=ActionCommand(action_type="poison", target_seat=3, reasoning="x"),
            ),
        ]

        assert ActionResolver().resolve(state, actions) == []

    def test_does_not_treat_witch_contract_from_non_witch_role_as_witch_action(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
        ])
        werewolf_request = request_for(state, 1)
        witch_contract = builtin_registry.require("wolf-killer-witch").contracts[0]
        forged_request = replace(werewolf_request, contract=witch_contract)
        actions = [
            AcceptedAction(
                request=forged_request,
                command=ActionCommand(action_type="save", target_seat=2, reasoning="x"),
            ),
            AcceptedAction(
                request=forged_request,
                command=ActionCommand(action_type="poison", target_seat=3, reasoning="x"),
            ),
        ]

        assert ActionResolver().resolve(state, actions) == []

    def test_resolves_single_witch_pass(self):
        state = make_state([
            make_player(1, "wolf-killer-witch", "good"),
        ])

        assert ActionResolver().resolve(state, [accept(state, 1, "pass")]) == []
        assert state.players[1].has_antidote is True
        assert state.players[1].has_poison is True

    def test_ignores_zero_and_none_targets_even_in_accepted_action_objects(self):
        state = make_state([
            make_player(1, "wolf-killer-werewolf", "werewolf"),
            make_player(2, "wolf-killer-villager", "good"),
        ])
        request = request_for(state, 1)
        malformed = [
            AcceptedAction(
                request=request,
                command=ActionCommand(action_type="kill", target_seat=0, reasoning="x"),
            ),
            AcceptedAction(
                request=request,
                command=ActionCommand(action_type="kill", target_seat=None, reasoning="x"),
            ),
        ]

        assert ActionResolver().resolve(state, malformed) == []
        assert state.last_wolf_kill_target is None

    def test_hunter_shoot_does_not_consume_gun_for_invalid_target(self):
        state = make_state([
            make_player(1, "wolf-killer-hunter", "good", has_gun=True),
            make_player(2, "wolf-killer-villager", "good"),
        ])
        state.phase = GamePhase.DAWN
        accepted = accept(state, 1, "shoot", 2)
        state.players[2].is_alive = False

        with pytest.raises(TypeError, match="AcceptedAction"):
            ActionResolver().resolve_hunter_shoot(
                state, 1, NightAction(player_seat=1, action_type="shoot", target_seat=2)
            )

        assert ActionResolver().resolve_hunter_shoot(
            state, 1, accepted
        ) is None
        assert state.players[1].has_gun is True

    def test_hunter_shoot_rejects_wrong_actor_type_or_target_before_valid_shot(self):
        state = make_state([
            make_player(1, "wolf-killer-hunter", "good", has_gun=True),
            make_player(2, "wolf-killer-villager", "good"),
        ])
        state.phase = GamePhase.DAWN
        shoot = accept(state, 1, "shoot", 2)
        resolver = ActionResolver()

        wrong_actor = AcceptedAction(
            request=replace(shoot.request, actor_seat=2), command=shoot.command
        )
        wrong_type = AcceptedAction(
            request=shoot.request,
            command=ActionCommand(action_type="pass", target_seat=None, reasoning="x"),
        )
        no_target = AcceptedAction(
            request=shoot.request,
            command=ActionCommand(action_type="shoot", target_seat=None, reasoning="x"),
        )

        assert resolver.resolve_hunter_shoot(state, 3, shoot) is None
        assert resolver.resolve_hunter_shoot(state, 1, wrong_actor) is None
        assert resolver.resolve_hunter_shoot(state, 1, wrong_type) is None
        assert resolver.resolve_hunter_shoot(state, 1, no_target) is None
        assert state.players[1].has_gun is True

        death = resolver.resolve_hunter_shoot(state, 1, shoot)

        assert death == DeathReport(player_seat=2, cause="hunter_shot", round_number=1)
        assert state.players[1].has_gun is False
        assert state.players[2].is_alive is False

    def test_identifies_an_armed_hunter_killed_by_non_poison(self):
        state = make_state([
            make_player(1, "wolf-killer-hunter", "good", has_gun=True),
        ])
        resolver = ActionResolver()

        assert resolver.has_hunter_died(
            state, [DeathReport(player_seat=1, cause="poison", round_number=1)]
        ) is None
        assert resolver.has_hunter_died(
            state, [DeathReport(player_seat=1, cause="wolf_kill", round_number=1)]
        ) == 1
        state.players[1].has_gun = False
        assert resolver.has_hunter_died(
            state,
            [
                DeathReport(player_seat=99, cause="wolf_kill", round_number=1),
                DeathReport(player_seat=1, cause="wolf_kill", round_number=1),
            ],
        ) is None

    def test_legacy_ignores_nonaccepted_and_wrong_actor_witch_rows(self):
        state = make_state([make_player(1, "wolf-killer-witch", "good")])
        request = request_for(state, 1)
        forged = replace(request, actor_seat=99)
        ActionResolver()._validate_witch_actions(
            state,
            [object(), AcceptedAction(
                request=forged,
                command=ActionCommand(action_type="save", target_seat=1, reasoning="x"),
            )],
        )

    def test_legacy_ignores_unknown_action_from_valid_witch_contract(self):
        state = make_state([make_player(1, "wolf-killer-witch", "good")])
        request = request_for(state, 1)
        ActionResolver()._validate_witch_actions(
            state,
            [AcceptedAction(
                request=request,
                command=ActionCommand(action_type="future", target_seat=None, reasoning="x"),
            )],
        )

    def test_ignores_accepted_seer_checks_without_a_live_target(self):
        state = make_state([
            make_player(1, "wolf-killer-seer", "good"),
        ])
        seer_request = request_for(state, 1)
        no_target = AcceptedAction(
            request=seer_request,
            command=ActionCommand(action_type="check", target_seat=None, reasoning="x"),
        )
        missing_target = AcceptedAction(
            request=seer_request,
            command=ActionCommand(action_type="check", target_seat=2, reasoning="x"),
        )

        assert ActionResolver().resolve(state, [no_target]) == []
        assert ActionResolver().resolve(state, [missing_target]) == []
        assert state.players[1].check_results == []

    def test_ignores_malformed_poison_and_non_check_actions(self):
        state = make_state([
            make_player(1, "wolf-killer-witch", "good"),
            make_player(2, "wolf-killer-seer", "good"),
        ])
        malformed_poison = AcceptedAction(
            request=request_for(state, 1),
            command=ActionCommand(action_type="poison", target_seat=None, reasoning="x"),
        )

        assert ActionResolver().resolve(state, [malformed_poison]) == []
        ActionResolver()._process_seer_checks(
            state, [NightAction(player_seat=2, action_type="pass", target_seat=None)]
        )
        state.players[1].role = "wolf-killer-werewolf"
        ActionResolver()._process_seer_checks(
            state,
            [NightAction(player_seat=99, action_type="check", target_seat=1)],
        )
        assert state.players[2].check_results == []
