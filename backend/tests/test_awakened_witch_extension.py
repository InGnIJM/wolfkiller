"""Awakened Witch extension: 1 antidote, 3 brews, an agreeing committee."""

from __future__ import annotations

from dataclasses import replace

from app.agents.prompt_renderer import _HOISTED_FACTS
from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector, _PUBLIC_RESOURCES
from app.core.effect_applier import EffectApplier
from app.core.role_runtime import role_private_data_view, role_resource_view
from app.core.scheduler import Scheduler
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, PROJECTED_FACT_NAMES, SchedulePoint,
)
from app.roles.awakened_witch import (
    ASSIST_HISTORY_KEY, ASSIST_TOKEN_RESOURCE, ASSIST_USED_RESOURCE,
    AWAKENED_WITCH_ASSIST_VOTE_CONTRACT, AWAKENED_WITCH_BREW_CONTRACT,
    AWAKENED_WITCH_PICK_1_CONTRACT, AWAKENED_WITCH_PICK_2_CONTRACT,
    AWAKENED_WITCH_PICK_3_CONTRACT, AWAKENED_WITCH_SPEC,
    AWAKENED_WITCH_TALLY_CONTRACT, BREW_PLAN_KEY, BREW_RESOURCE,
    BREW_USED_RESOURCE, resolve_awakened_witch_assist_tally,
    resolve_awakened_witch_assist_vote, resolve_awakened_witch_brew,
    validate_awakened_witch_assist_vote,
    validate_awakened_witch_brew,
)
from app.roles.registry import builtin_registry
from app.roles.awakened_witch import _pick_validate, _resolve_pick


def _context(
    contract, *, resources=None, facts=None, round_number=1, actor_alive=True,
) -> ActionContext:
    return ActionContext(
        "g", 0, facts or {}, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=round_number,
        phase="night", window_id="w", schedule_point=contract.schedule_point,
        actor_seat=1, actor_role_id=AWAKENED_WITCH_SPEC.role_id,
        actor_alive=actor_alive, resources=resources or {}, action_key="a",
    )


def _command(action, target=None, targets=None) -> ActionCommand:
    return ActionCommand(
        action_type=action, target_seat=target, target_seats=targets,
        reasoning="ok",
    )


def test_spec_shape_and_granted_vote() -> None:
    ids = [contract.contract_id for contract in AWAKENED_WITCH_SPEC.contracts]
    assert ids == [
        "awakened_witch_brew", "awakened_witch_assist_pick_1",
        "awakened_witch_assist_pick_2", "awakened_witch_assist_pick_3",
        "awakened_witch_assist_tally",
    ]
    assert AWAKENED_WITCH_SPEC.initial_resources == {
        "antidote": 1, BREW_RESOURCE: 3, BREW_USED_RESOURCE: 0,
        ASSIST_TOKEN_RESOURCE: 0, "assist_target": 0, "assist_count": 0,
        "assist_round": 0, "assist_vote": 0,
    }
    assert AWAKENED_WITCH_SPEC.granted_contracts == (AWAKENED_WITCH_ASSIST_VOTE_CONTRACT,)
    assert AWAKENED_WITCH_SPEC.granted_notes
    assert AWAKENED_WITCH_SPEC.projected_facts == {"assist_votes"}
    assert PROJECTED_FACT_NAMES >= AWAKENED_WITCH_SPEC.projected_facts
    assert "assist_votes" in _HOISTED_FACTS
    assert ASSIST_USED_RESOURCE in _PUBLIC_RESOURCES
    assert AWAKENED_WITCH_SPEC.role_id == "wolf-killer-awakened-witch"
    assert AWAKENED_WITCH_SPEC.camp_id == "good"
    assert AWAKENED_WITCH_SPEC.tags == {"god"}


def test_brew_validate_save_poison_pass() -> None:
    facts = {"wolf_kill_target": 2, "witch_self_save": "never"}
    ctx = _context(
        AWAKENED_WITCH_BREW_CONTRACT,
        resources={"antidote": 1, BREW_RESOURCE: 3, BREW_USED_RESOURCE: 0},
        facts=facts,
    )
    assert validate_awakened_witch_brew(ctx, _command("save", 2)) == ()
    assert validate_awakened_witch_brew(ctx, _command("poison", 3)) == ()
    assert validate_awakened_witch_brew(ctx, _command("pass")) == ()
    assert [v.code for v in validate_awakened_witch_brew(
        ctx, _command("save", 3)
    )] == ["invalid_save_target"]
    self_target = replace(ctx, facts={"wolf_kill_target": 1, "witch_self_save": "never"})
    assert [v.code for v in validate_awakened_witch_brew(
        self_target, _command("save", 1)
    )] == ["witch_self_save_forbidden"]
    assert [v.code for v in validate_awakened_witch_brew(
        ctx, _command("poison", 1)
    )] == ["self_poison_forbidden"]
    empty = replace(
        ctx, resources={"antidote": 0, BREW_RESOURCE: 0, BREW_USED_RESOURCE: 3},
    )
    assert [v.code for v in validate_awakened_witch_brew(
        empty, _command("poison", 3)
    )] == ["brew_unavailable"]


def test_brew_poison_writes_the_plan_and_consumes_one_use() -> None:
    ctx = _context(
        AWAKENED_WITCH_BREW_CONTRACT,
        resources={"antidote": 1, BREW_RESOURCE: 3, BREW_USED_RESOURCE: 0},
        facts={"wolf_kill_target": 2},
    )
    effects = resolve_awakened_witch_brew(ctx, _command("poison", 3))
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SET_RESOURCE,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT,
    ]
    assert effects[0].payload == {
        "target": 1, "resource": BREW_RESOURCE, "amount": 1,
    }
    assert effects[1].payload == {
        "target": 1, "resource": BREW_USED_RESOURCE, "value": 1,
    }
    plan = effects[2].payload["value"]
    assert plan["target"] == 3 and plan["use_index"] == 1
    assert plan["assist_count"] == 1 and plan["assist_seats"] == ()
    assert effects[3].payload["payload"]["action_type"] == "poison"


def test_save_still_uses_the_antidote_contract() -> None:
    ctx = _context(
        AWAKENED_WITCH_BREW_CONTRACT,
        resources={"antidote": 1, BREW_RESOURCE: 3, BREW_USED_RESOURCE: 0},
        facts={"wolf_kill_target": 2, "witch_self_save": "first_night_only"},
    )
    effects = resolve_awakened_witch_brew(ctx, _command("save", 2))
    assert [effect.kind for effect in effects] == [
        EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_PROTECTION,
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert effects[1].payload == {
        "target": 2, "amount": 1, "source": "witch_antidote",
    }
    assert effects[2].payload["key"] == "saved_seat"
    assert effects[3].payload["event_type"] == "WITCH_SAVE"


def test_pick_one_two_three_contracts_and_distinct_unused_assistants() -> None:
    assert AWAKENED_WITCH_PICK_1_CONTRACT.actions_requiring_target == {"assist"}
    assert AWAKENED_WITCH_PICK_2_CONTRACT.multi_target_actions == {"assist": 2}
    assert AWAKENED_WITCH_PICK_3_CONTRACT.multi_target_actions == {"assist": 3}
    for contract in (
        AWAKENED_WITCH_PICK_1_CONTRACT, AWAKENED_WITCH_PICK_2_CONTRACT,
        AWAKENED_WITCH_PICK_3_CONTRACT,
    ):
        assert contract.schedule_point is SchedulePoint.NIGHT_WITCH_ASSIST_PICK
        assert contract.fallback_action_type == "pass"
    plan = {
        "target": 3, "round": 1, "use_index": 1, "assist_count": 1,
        "assist_seats": (),
    }
    ctx = _context(
        AWAKENED_WITCH_PICK_1_CONTRACT,
        facts={
            "brew_plan": plan,
            "selected_target": {"seat": 1, "resource_labels": ()},
        },
    )
    assert AWAKENED_WITCH_PICK_1_CONTRACT.is_applicable(ctx) is True
    ctx2 = _context(
        AWAKENED_WITCH_PICK_2_CONTRACT,
        facts={"brew_plan": plan, "selected_target": {"seat": 1, "resource_labels": ()}},
    )
    ctx3 = _context(
        AWAKENED_WITCH_PICK_3_CONTRACT,
        facts={"brew_plan": plan, "selected_target": {"seat": 1, "resource_labels": ()}},
    )
    assert AWAKENED_WITCH_PICK_2_CONTRACT.is_applicable(ctx2) is False
    assert AWAKENED_WITCH_PICK_3_CONTRACT.is_applicable(ctx3) is False
    assert _resolve_pick(ctx, _command("assist", 1))[0].payload == {
        "target": 1, "resource": ASSIST_TOKEN_RESOURCE, "value": 1,
    }
    used = replace(
        ctx,
        facts={
            "brew_plan": plan,
            "selected_target": {"seat": 1, "resource_labels": (ASSIST_USED_RESOURCE,)},
        },
    )
    assert [v.code for v in _pick_validate(used, _command("assist", 1))] == ["assist_already_used"]
    passed = _resolve_pick(ctx, _command("pass"))
    assert [effect.kind for effect in passed] == [
        EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT, EffectKind.EMIT_EVENT,
    ]
    assert passed[1].payload["payload"]["result"] == "aborted"


def test_pick_two_and_three_require_the_exact_count() -> None:
    plan = {
        "target": 3, "round": 1, "use_index": 2, "assist_count": 2,
        "assist_seats": (),
    }
    ctx = _context(
        AWAKENED_WITCH_PICK_2_CONTRACT,
        facts={
            "brew_plan": plan,
            "selected_targets": (
                {"seat": 1, "resource_labels": ()},
                {"seat": 2, "resource_labels": ()},
            ),
        },
    )
    assert AWAKENED_WITCH_PICK_2_CONTRACT.is_applicable(ctx) is True
    effects = _resolve_pick(ctx, _command("assist", targets=(1, 2)))
    assert effects[0].payload["value"] == 1
    seats = [
        effect.payload.get("target") for effect in effects
        if effect.kind is EffectKind.SET_RESOURCE
    ]
    assert seats.count(1) == 5 and seats.count(2) == 5
    plan3 = {**plan, "use_index": 3, "assist_count": 3}
    ctx3 = _context(
        AWAKENED_WITCH_PICK_3_CONTRACT,
        facts={
            "brew_plan": plan3,
            "selected_targets": (
                {"seat": 1, "resource_labels": ()},
                {"seat": 2, "resource_labels": ()},
                {"seat": 4, "resource_labels": ()},
            ),
        },
    )
    assert AWAKENED_WITCH_PICK_3_CONTRACT.is_applicable(ctx3) is True
    assert AWAKENED_WITCH_PICK_3_CONTRACT.validate(
        ctx3, _command("assist", targets=(1, 1, 4)),
    )[0].code == "assist_duplicate_target"


def test_vote_agree_veto_pass_and_poison_target_binding() -> None:
    resources = {
        ASSIST_TOKEN_RESOURCE: 1, "assist_target": 3, "assist_round": 1,
    }
    ctx = _context(AWAKENED_WITCH_ASSIST_VOTE_CONTRACT, resources=resources)
    assert AWAKENED_WITCH_ASSIST_VOTE_CONTRACT.is_applicable(ctx) is True
    assert validate_awakened_witch_assist_vote(ctx, _command("agree", 3)) == ()
    assert validate_awakened_witch_assist_vote(ctx, _command("veto", 3)) == ()
    assert validate_awakened_witch_assist_vote(ctx, _command("pass")) == ()
    assert [v.code for v in validate_awakened_witch_assist_vote(
        ctx, _command("agree", 4)
    )] == ["not_the_poison_target"]
    effects = resolve_awakened_witch_assist_vote(ctx, _command("veto", 3))
    assert effects[0].payload == {
        "target": 1, "resource": ASSIST_TOKEN_RESOURCE, "amount": 1,
    }
    assert effects[1].payload["value"] == 0
    assert effects[2].payload["payload"]["vote"] == "veto"
    no_token = replace(ctx, resources={**resources, ASSIST_TOKEN_RESOURCE: 0})
    assert AWAKENED_WITCH_ASSIST_VOTE_CONTRACT.is_applicable(no_token) is False


def test_tally_damage_only_when_every_assistant_agrees() -> None:
    plan = {
        "target": 3, "round": 1, "use_index": 2, "assist_count": 2,
        "assist_seats": (1, 2),
    }
    base = _context(
        AWAKENED_WITCH_TALLY_CONTRACT,
        facts={"brew_plan": plan, "assist_votes": (
            {"seat": 1, "vote": 1}, {"seat": 2, "vote": 1},
        )},
    )
    effects = resolve_awakened_witch_assist_tally(
        base, _command("resolve"),
    )
    kinds = [effect.kind for effect in effects]
    assert EffectKind.SUBMIT_DAMAGE in kinds
    assert effects[0].payload == {
        "target": 3, "amount": 1, "cause": "poison",
    }
    passed = [
        effect for effect in effects
        if effect.payload.get("event_type") == "AWAKENED_WITCH_POISON_RESULT"
    ]
    assert passed[0].payload["payload"]["result"] == "passed"
    vetoed = resolve_awakened_witch_assist_tally(
        replace(base, facts={
            "brew_plan": plan,
            "assist_votes": ({"seat": 1, "vote": 1}, {"seat": 2, "vote": 0}),
        }),
        _command("resolve"),
    )
    assert not any(effect.kind is EffectKind.SUBMIT_DAMAGE for effect in vetoed)
    result = next(
        effect for effect in vetoed
        if effect.payload.get("event_type") == "AWAKENED_WITCH_POISON_RESULT"
    )
    assert result.payload["payload"]["result"] == "vetoed"
    incomplete = resolve_awakened_witch_assist_tally(
        replace(base, facts={
            "brew_plan": plan,
            "assist_votes": ({"seat": 1, "vote": 1},),
        }),
        _command("resolve"),
    )
    result = next(
        effect for effect in incomplete
        if effect.payload.get("event_type") == "AWAKENED_WITCH_POISON_RESULT"
    )
    assert result.payload["payload"]["result"] == "incomplete"


def _scheduler_game():
    registry = builtin_registry.freeze()
    game = GameState(
        "awakened-witch-game", phase=GamePhase.NIGHT, round_number=1,
        players={
            1: PlayerState(1, AWAKENED_WITCH_SPEC.role_id, "good"),
            2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
            3: PlayerState(3, "wolf-killer-villager", "good"),
            4: PlayerState(4, "wolf-killer-villager", "good"),
        },
    )
    return registry, game


def test_scheduler_runs_pick_vote_and_tally_end_to_end() -> None:
    registry, game = _scheduler_game()
    commands = {
        "awakened_witch_brew": _command("poison", 4),
        "awakened_witch_assist_pick_1": _command("assist", 2),
        "awakened_witch_assist_vote": _command("agree", 4),
        "awakened_witch_assist_tally": _command("resolve"),
    }

    def provider(request, projected, attempt):
        return commands[request.contract.contract_id]

    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), provider,
    )
    scheduler.run_point(game, SchedulePoint.NIGHT_WITCH_ACTION)
    plan = role_private_data_view(game, 1)[BREW_PLAN_KEY]
    assert plan["target"] == 4 and plan["assist_seats"] == ()
    assert role_resource_view(game, 1)[BREW_RESOURCE] == 2

    scheduler.run_point(game, SchedulePoint.NIGHT_WITCH_ASSIST_PICK)
    plan = role_private_data_view(game, 1)[BREW_PLAN_KEY]
    assert plan["assist_seats"] == (2,)
    assert role_private_data_view(game, 1)[ASSIST_HISTORY_KEY] == (2,)

    scheduler.run_point(game, SchedulePoint.NIGHT_WITCH_ASSIST_VOTE)
    scheduler.run_point(game, SchedulePoint.NIGHT_WITCH_ASSIST_TALLY)
    assert role_resource_view(game, 1)[BREW_RESOURCE] == 2
    assert role_private_data_view(game, 1)[BREW_PLAN_KEY] is None
    assert game._pipeline_runtime.pending_damage == (
        {"target": 4, "amount": 1, "cause": "poison"},
    )
    assert role_resource_view(game, 1)[ASSIST_TOKEN_RESOURCE] == 0
    assert role_resource_view(game, 2)[ASSIST_TOKEN_RESOURCE] == 0


def test_new_preset_field_is_twelve_players_with_both_roles() -> None:
    from app.catalog import STANDARD_PRESETS
    preset = next(p for p in STANDARD_PRESETS if p["id"] == "twelve-player-awakened-witch")
    assert preset["role_counts"] == {
        "wolf-killer-werewolf": 3,
        "wolf-killer-awakened-wolf-king": 1,
        "wolf-killer-villager": 4,
        "wolf-killer-seer": 1,
        "wolf-killer-guard": 1,
        "wolf-killer-hunter": 1,
        "wolf-killer-awakened-witch": 1,
    }
    assert preset["enable_sheriff"] is True
    assert preset["witch_self_save"] == "never"
    assert sum(preset["role_counts"].values()) == 12
