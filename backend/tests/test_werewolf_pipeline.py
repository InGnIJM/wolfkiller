from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, SchedulePoint
from app.roles.registry import builtin_registry
from app.roles.werewolf import WEREWOLF_SPEC, aggregate_werewolf_votes, validate_werewolf_action
from app.roles.werewolf import Werewolf, werewolf_applicable


def context(alive=(1, 2, 3)) -> ActionContext:
    contract = WEREWOLF_SPEC.contracts[0]
    return ActionContext(
        "g", 0, {"alive_seats": alive}, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=contract.schedule_point, actor_seat=1,
        actor_role_id=WEREWOLF_SPEC.role_id, actor_alive=True, action_key="a",
    )


def command(action: str, target=None) -> ActionCommand:
    return ActionCommand(action_type=action, target_seat=target, reasoning="ok")


def test_validate_allows_self_teammate_and_other_alive_targets() -> None:
    ctx = context()
    assert validate_werewolf_action(ctx, command("kill", 1)) == ()
    assert validate_werewolf_action(ctx, command("kill", 2)) == ()
    assert validate_werewolf_action(ctx, command("kill", 3)) == ()
    assert validate_werewolf_action(ctx, command("pass")) == ()
    contract = WEREWOLF_SPEC.contracts[0]
    assert ActionValidator().validate(replace(ctx, facts={"alive_seats": (1, 2)}), contract, command("kill", 3))


def test_aggregate_majority_tie_lowest_and_all_pass() -> None:
    ctx = context((1, 2, 3, 4))
    majority = aggregate_werewolf_votes(ctx, (command("kill", 3), command("kill", 4), command("kill", 3)))
    tie = aggregate_werewolf_votes(ctx, (command("kill", 4), command("kill", 3)))
    assert majority[0].target_seat == 3 and tie[0].target_seat == 3
    assert aggregate_werewolf_votes(ctx, (command("pass"), command("pass"))) == ()
    effect = majority[0]
    assert effect.kind is EffectKind.SUBMIT_DAMAGE
    assert effect.source_action_key == ctx.action_key and effect.expected_revision == ctx.revision
    assert effect.sort_key == (1,) and effect.payload == {"target": 3, "amount": 1}


def test_resolver_adds_single_accept_and_registry_keeps_legacy() -> None:
    snapshot = builtin_registry.freeze()
    spec = snapshot.require("wolf-killer-werewolf"); contract = spec.contracts[0]
    effects = ActionResolver().aggregate_effects(context(), spec, contract, (command("kill", 1), command("pass")))
    assert [effect.kind for effect in effects] == [EffectKind.ACCEPT_ACTION, EffectKind.SUBMIT_DAMAGE]
    assert spec is WEREWOLF_SPEC and contract.schedule_point is SchedulePoint.NIGHT_ACTION
    assert builtin_registry.require("wolf-killer-werewolf").role_factory is not None
    assert werewolf_applicable(context()) is True


@pytest.mark.asyncio
async def test_legacy_werewolf_behavior_remains_available() -> None:
    class Builder:
        def build_action_prompt(self, *args): return "prompt"
        def get_system_prompt(self): return "system"
    class Client:
        def get_model(self): return self
        async def ainvoke(self, prompt):
            class Response: content = '{"action_type":"pass","target_seat":null,"reasoning":"ok"}'
            return Response()
    role = Werewolf(1, "wolf-killer-werewolf", Builder(), Client())
    assert role.get_skills() == ["kill"]
    action = await role.kill(object(), object())
    assert action.action_type == "pass"
