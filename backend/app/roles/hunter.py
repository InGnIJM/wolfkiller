from __future__ import annotations
from app.models.game import GameState
from app.models.actions import NightAction
from app.core.conversation_log import ConversationLog
from app.roles.base import BaseRole
from app.core.effect_applier import derive_effect_id
from app.models.pipeline import (
    ActionCommand, ActionContext, ActionContract, EffectKind, GameEffect, RoleSpec,
    RuleViolation, SchedulePoint,
)


_SHOOT_REASONS = frozenset({"wolf_kill", "exile", "hunter_shot"})


def hunter_applicable(context: ActionContext) -> bool:
    trigger = context.trigger_event
    return (
        context.source_event_id is not None
        and trigger is not None
        and trigger.get("target_seat") == context.actor_seat
        and context.trigger_reason in _SHOOT_REASONS
        and context.resources.get("gun", 0) > 0
    )


def validate_hunter_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    if command.action_type == "shoot" and context.resources.get("gun", 0) <= 0:
        return (RuleViolation("gun_unavailable", "hunter gun is unavailable"),)
    return ()


def resolve_hunter_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    if command.action_type == "pass":
        return ()
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (
        GameEffect(
            derive_effect_id(context.action_key, 1), EffectKind.CONSUME_RESOURCE,
            context.action_key, target_seat=context.actor_seat,
            payload={"target": context.actor_seat, "resource": "gun", "amount": 1},
            preconditions={"resource_equals": {"resource": "gun", "value": 1}},
            sort_key=(1,), **common,
        ),
        GameEffect(
            derive_effect_id(context.action_key, 2), EffectKind.SUBMIT_DAMAGE,
            context.action_key, target_seat=command.target_seat,
            payload={"target": command.target_seat, "amount": 1, "cause": "hunter_shot"},
            sort_key=(2,), **common,
        ),
    )


HUNTER_SPEC = RoleSpec(
    role_id="wolf-killer-hunter", display_name="Hunter", camp_id="good",
    contracts=(ActionContract(
        contract_id="hunter_shoot", schedule_point=SchedulePoint.DAWN_REACTION,
        order=40, action_types=("shoot", "pass"),
        actions_requiring_target=frozenset({"shoot"}), fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        response_event_types=frozenset({"PLAYER_DIED"}),
        response_reasons=_SHOOT_REASONS, per_window_limit=1, per_game_limit=1,
        is_applicable=hunter_applicable, validate=validate_hunter_action,
        resolve=resolve_hunter_action,
    ),), initial_resources={"gun": 1},
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions="After an eligible death, shoot one living player or pass.",
)


class Hunter(BaseRole):
    """Hunter role: can shoot one player on death (not if poisoned)."""

    def get_skills(self) -> list[str]:
        return ["shoot"]

    async def shoot(
        self, state: GameState, conversation_log: ConversationLog
    ) -> NightAction:
        """Decide who to shoot when dying. Cannot shoot if killed by poison."""
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "hunter_shoot"
        )
        raw = await self._invoke_llm(prompt)
        action = self.output_parser.parse_night_action(raw, self.seat)
        return action
