"""A degraded action must not spend a one-shot card's quota.

``action_counts`` is both a statistic and a **gate**: ``role_action_counters``
feeds it into ``ActionValidator._validate_counters``, which enforces
``per_window_limit`` / ``per_round_limit`` / ``per_game_limit``. A fallback used
to be accepted like any other command, so it was counted like one — and because
every one-shot card also consumes a resource with a compare-and-set
precondition, the two ledgers disagreed in the worst possible direction: the
resource was still in hand, yet every later attempt answered
``game_limit_reached``. One model failure silently voided the Hunter's gun.

The fix splits the accounting by what each scope is for: ``window`` and
``round`` are the re-ask guard — a degraded attempt did happen, so it counts,
otherwise an outage would buy the seat another attempt at every later slot of
the round — while ``game`` means "this card is spent" and stays a resource fact.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier, EffectPermission, EffectRejected
from app.core.role_runtime import (
    initialize_role_resources, record_accepted_action, role_action_counters, role_resource_view,
)
from app.core.scheduler import Scheduler
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    DEGRADED_REASONING, ActionCommand, ActionContract, ActionContext, EffectKind,
    RoleSpec, SchedulePoint,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry, builtin_registry

HUNTER = "wolf-killer-hunter"
SHOOT = "hunter_shoot"
ONE_SHOT = "one_shot"

#: A contract's hooks must be stable module-level functions, so the recording
#: ones cannot be closures: they append to a module-level list the test resets.
RESOLVED: list[tuple[bool, str]] = []


def _always_applicable(context: ActionContext) -> bool:
    return True


def _no_effects(context: ActionContext, command: ActionCommand) -> tuple:
    return ()


def _record_effects(context: ActionContext, command: ActionCommand) -> tuple:
    RESOLVED.append((context.degraded, command.reasoning))
    return ()


def _contract(*limits: str, consumes_resource: bool = True, resolve=_no_effects) -> ActionContract:
    """A minimal one-shot contract carrying only the limits under test.

    The limit fields are omitted rather than passed as ``None``: the frozen type
    validates them without ``optional=True``, so ``None`` is a type error there.
    """
    effects = {EffectKind.EMIT_EVENT}
    if consumes_resource:
        effects.add(EffectKind.CONSUME_RESOURCE)
    fields: dict[str, object] = {
        "contract_id": ONE_SHOT, "schedule_point": SchedulePoint.NIGHT_ACTION, "order": 1,
        "action_types": ("act", "pass"), "actions_requiring_target": frozenset(),
        "fallback_action_type": "pass", "allowed_effects": frozenset(effects),
        "visibility_namespaces": frozenset({"PUBLIC"}),
        "is_applicable": _always_applicable, "resolve": resolve,
    }
    for scope, field in (("window", "per_window_limit"), ("round", "per_round_limit"),
                         ("game", "per_game_limit")):
        if scope in limits:
            fields[field] = 1
    return ActionContract(**fields)  # type: ignore[arg-type]


def _snapshot(contract: ActionContract) -> RegistrySnapshot:
    spec = RoleSpec(
        role_id="one-shot-role", camp_id="good", contracts=(contract,),
        allowed_effects=frozenset({EffectKind.EMIT_EVENT, EffectKind.CONSUME_RESOURCE}),
        visibility_namespaces=frozenset({"PUBLIC"}),
    )
    return RegistrySnapshot({"one-shot-role": spec}, "a" * 64)


def _state(registry: RegistrySnapshot) -> GameState:
    state = GameState(
        "limits", phase=GamePhase.NIGHT, round_number=2,
        config=GameConfig(role_counts={"one-shot-role": 1}),
        players={1: PlayerState(1, "one-shot-role", "good")},
    )
    initialize_role_resources(state, registry.specs, registry.digest)
    return state


class _SpyApplier(EffectApplier):
    """Record the permission each apply saw — it is what carries ``degraded``."""

    def __init__(self) -> None:
        super().__init__()
        self.permissions: list[EffectPermission] = []

    def apply(self, state, effects, permission):  # type: ignore[override]
        self.permissions.append(permission)
        return super().apply(state, effects, permission)


def _degraded_answer(*_args, **_kwargs):
    """The shape the real command provider returns when the model call fails.

    A provider that *raises* is not this path: the scheduler turns that into
    ``PipelinePaused`` (the engine has to decide whether to pause). Degradation
    reaches the pipeline as a command that says it is one.
    """
    return ActionCommand(
        action_type="pass", target_seat=None,
        reasoning=DEGRADED_REASONING, degraded=True,
    )


def _raising_provider(*_args, **_kwargs):
    raise RuntimeError("provider down")


def _answer(*_args, **_kwargs):
    return ActionCommand(action_type="act", target_seat=None, reasoning="我做这个")


def _unusable(*_args, **_kwargs):
    return ActionCommand(action_type="nonsense", target_seat=None, reasoning="别问我")


def _run(contract: ActionContract, provider) -> tuple[GameState, _SpyApplier]:
    registry = _snapshot(contract)
    state = _state(registry)
    applier = _SpyApplier()
    Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(), applier, provider,
    ).run_point(state, SchedulePoint.NIGHT_ACTION)
    return state, applier


def _counts(state: GameState) -> dict[str, dict[str, int]]:
    runtime = state._pipeline_runtime
    return {scope: dict(row) for scope, row in runtime.action_counts.items() if row}


class TestTheQuotaSplit:
    def test_an_engine_fallback_does_not_spend_the_game_quota(self) -> None:
        """The red light: one failure used to consume the card for the whole game."""
        state, applier = _run(_contract("window", "round", "game"), _degraded_answer)

        assert [permission.degraded for permission in applier.permissions] == [True]
        counts = _counts(state)
        # The re-ask guard still fired: exactly one window and one round entry …
        assert list(counts["window"].values()) == [1]
        assert list(counts["round"].values()) == [1]
        # … but the card was not spent.
        assert "game" not in counts

    def test_a_real_answer_still_spends_all_three_scopes(self) -> None:
        state, applier = _run(_contract("window", "round", "game"), _answer)

        assert [permission.degraded for permission in applier.permissions] == [False]
        counts = _counts(state)
        assert list(counts["window"].values()) == [1]
        assert list(counts["round"].values()) == [1]
        assert list(counts["game"].values()) == [1]

    def test_a_contract_without_limits_still_counts_only_the_attempt_scopes(self) -> None:
        """No declared limit changes nothing: the same split applies.

        ``window`` / ``round`` are recorded for every contract — the validator is
        what ignores them when no limit is declared — while ``game`` follows the
        degraded flag, so an engine-made command never looks "spent".
        """
        state, _ = _run(_contract(consumes_resource=False), _degraded_answer)

        counts = _counts(state)
        assert list(counts["window"].values()) == [1]
        assert list(counts["round"].values()) == [1]
        assert "game" not in counts

    def test_an_unusable_answer_is_degraded_too(self) -> None:
        """The engine's own fallback path, not just a provider exception."""
        state, applier = _run(_contract("window", "round", "game"), _unusable)

        assert [permission.degraded for permission in applier.permissions] == [True]
        assert "game" not in _counts(state)


class TestTheDegradedFlagReachesEveryConsumer:
    def setup_method(self) -> None:
        RESOLVED.clear()

    def test_the_resolve_hook_sees_a_degraded_context(self) -> None:
        """The context is what the hook reads, and it must match the permission."""
        _run(_contract("window", "round", "game", resolve=_record_effects), _unusable)

        assert RESOLVED == [(True, DEGRADED_REASONING)]

    def test_a_normal_answer_is_not_marked(self) -> None:
        _run(_contract("window", resolve=_record_effects), _answer)

        assert RESOLVED == [(False, "我做这个")]

    def test_an_engine_made_command_reaches_the_hook_as_degraded(self) -> None:
        _run(_contract("window", resolve=_record_effects), _degraded_answer)

        assert RESOLVED == [(True, DEGRADED_REASONING)]

    def test_a_provider_that_raises_pauses_instead_of_degrading(self) -> None:
        """The boundary: an exception is the engine's call, not a fallback.

        ``_command`` turns a throwing provider into ``PipelinePaused`` so the
        caller can decide whether to pause; only a returned command can degrade.
        """
        from app.core.scheduler import PipelinePaused

        with pytest.raises(PipelinePaused, match="command provider failed"):
            _run(_contract("window", resolve=_record_effects), _raising_provider)

    def test_the_permission_defaults_to_not_degraded(self) -> None:
        permission = EffectPermission(
            actor_seat=1, role_effects=frozenset({EffectKind.EMIT_EVENT}),
            contract_effects=frozenset({EffectKind.EMIT_EVENT}),
            allowed_targets=frozenset({1}), allowed_visibility=frozenset({"PUBLIC"}),
        )

        assert permission.degraded is False

    def test_a_non_boolean_permission_flag_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="degraded"):
            EffectPermission(
                actor_seat=1, role_effects=frozenset({EffectKind.EMIT_EVENT}),
                contract_effects=frozenset({EffectKind.EMIT_EVENT}),
                allowed_targets=frozenset({1}), allowed_visibility=frozenset({"PUBLIC"}),
                degraded=1,  # type: ignore[arg-type]
            )

    def test_a_non_boolean_context_flag_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="degraded"):
            ActionContext(game_id="g", revision=0, facts={}, degraded=1)  # type: ignore[arg-type]


class TestRecordAcceptedActionKeepsTheLedgersApart:
    PAYLOAD = {"actor_seat": 1, "contract_id": "c", "window_id": "w", "round_number": 3}

    @staticmethod
    def _counts() -> dict[str, dict[str, int]]:
        return {"window": {}, "round": {}, "game": {}}

    def test_degraded_counts_window_and_round_only(self) -> None:
        counts = self._counts()

        record_accepted_action(counts, self.PAYLOAD, {1}, degraded=True)

        assert counts["window"] == {"1\0c\0w": 1}
        assert counts["round"] == {"1\0c\0" + "3": 1}
        assert counts["game"] == {}

    def test_a_real_action_counts_every_scope(self) -> None:
        counts = self._counts()

        record_accepted_action(counts, self.PAYLOAD, {1})

        assert counts["window"] == {"1\0c\0w": 1}
        assert counts["round"] == {"1\0c\0" + "3": 1}
        assert counts["game"] == {"1\0c": 1}

    def test_the_overflow_guard_still_covers_the_counted_scopes(self) -> None:
        counts = self._counts()
        counts["window"]["1\0c\0w"] = 2_147_483_647

        with pytest.raises(EffectRejected, match="overflow"):
            record_accepted_action(counts, self.PAYLOAD, {1}, degraded=True)

    def test_a_non_boolean_flag_is_rejected_here_too(self) -> None:
        with pytest.raises(EffectRejected, match="degraded"):
            record_accepted_action(self._counts(), self.PAYLOAD, {1}, degraded=1)  # type: ignore[arg-type]


class TestTheOneShotCardSurvivesAnOutage:
    """The end-to-end shape of the bug, on the real Hunter contract."""

    @staticmethod
    def _hunter() -> tuple[GameState, object, ActionContract]:
        registry = builtin_registry.freeze()
        spec = registry.require(HUNTER)
        contract = next(c for c in spec.contracts if c.contract_id == SHOOT)
        state = GameState(
            "hunter-outage", phase=GamePhase.DAWN, round_number=2,
            config=GameConfig(role_counts={HUNTER: 1, "wolf-killer-werewolf": 1}),
            players={1: PlayerState(1, HUNTER, "good"),
                     2: PlayerState(2, "wolf-killer-werewolf", "werewolf")},
        )
        initialize_role_resources(state, registry.specs, registry.digest)
        return state, spec, contract

    @staticmethod
    def _context(state: GameState, spec, contract, window: str, *, degraded: bool) -> ActionContext:
        return ActionContext(
            game_id=state.game_id, revision=state._pipeline_runtime.revision,
            config_version="registry", contract_id=contract.contract_id,
            contract_version=contract.schema_version, contract_digest=contract.stable_digest(),
            round_number=state.round_number, phase="dawn", window_id=window,
            schedule_point=contract.schedule_point, actor_seat=1, actor_role_id=spec.role_id,
            actor_alive=True, action_key=f"key:{window}", counters={}, resources={"gun": 1},
            facts={"alive_seats": (1, 2)}, degraded=degraded,
        )

    @staticmethod
    def _permission(spec, contract, *, degraded: bool) -> EffectPermission:
        return EffectPermission(
            actor_seat=1, role_effects=spec.allowed_effects,
            contract_effects=contract.allowed_effects,
            allowed_targets=frozenset({1, 2}), allowed_visibility=frozenset({"PUBLIC", "ACTOR"}),
            degraded=degraded,
        )

    def _degrade(self, state, spec, contract, window: str) -> None:
        command = ActionCommand(
            action_type=contract.fallback_action_type, target_seat=None,
            reasoning=DEGRADED_REASONING, degraded=True,
        )
        context = self._context(state, spec, contract, window, degraded=True)
        effects = ActionResolver().resolve_effects(context, spec, contract, command)
        EffectApplier().apply(state, effects, self._permission(spec, contract, degraded=True))

    def test_the_gun_is_still_in_hand_and_the_quota_is_not_spent(self) -> None:
        state, spec, contract = self._hunter()
        assert contract.per_game_limit == 1

        self._degrade(state, spec, contract, "w2")

        assert dict(role_resource_view(state, 1)) == {"gun": 1}
        assert "game" not in _counts(state)
        # The window guard fired for that window, so it is not asked twice.
        assert list(_counts(state)["window"].values()) == [1]

    def test_a_later_trigger_window_can_still_shoot(self) -> None:
        """The card is not void: the next death window validates cleanly."""
        state, spec, contract = self._hunter()
        self._degrade(state, spec, contract, "w2")

        state.round_number = 5
        context = replace(
            self._context(state, spec, contract, "w5", degraded=False),
            counters=role_action_counters(state, 1, SHOOT, "w5", 5),
        )
        violations = ActionValidator().validate(
            context, contract,
            ActionCommand(action_type="shoot", target_seat=2, reasoning="我要开枪"),
        )

        assert [v.code for v in violations] == []


class TestTheRegistryKeepsThePairingTrue:
    def test_a_count_only_one_shot_is_rejected(self) -> None:
        """``game`` is no longer the authority, so the resource has to exist."""
        contract = replace(
            _contract("game", consumes_resource=False), contract_id="count_only",
        )
        spec = RoleSpec(
            role_id="count-only-role", camp_id="good", contracts=(contract,),
            allowed_effects=frozenset({EffectKind.EMIT_EVENT}),
            visibility_namespaces=frozenset({"PUBLIC"}),
        )
        registry = RoleRegistry()
        registry.register_pipeline(spec)

        with pytest.raises(ValueError, match="must consume a resource"):
            registry.freeze()

    def test_every_builtin_one_shot_pairs_its_limit_with_a_resource(self) -> None:
        """The gate is only useful if the shipped roles satisfy it."""
        snapshot = builtin_registry.freeze()

        paired = [
            contract
            for spec in snapshot.specs.values()
            for contract in spec.contracts
            if contract.per_game_limit == 1
        ]

        assert paired, "no per_game_limit==1 contract found — has the catalog changed?"
        for contract in paired:
            assert EffectKind.CONSUME_RESOURCE in contract.allowed_effects, contract.contract_id
