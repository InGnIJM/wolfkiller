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


def werewolf_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_werewolf_action(
    context: ActionContext, command: ActionCommand,
) -> tuple[RuleViolation, ...]:
    return ()


def aggregate_werewolf_votes(
    context: ActionContext, commands: tuple[ActionCommand, ...],
) -> tuple[GameEffect, ...]:
    counts: dict[int, int] = {}
    for command in commands:
        if command.action_type == "kill" and command.target_seat is not None:
            counts[command.target_seat] = counts.get(command.target_seat, 0) + 1
    if not counts:
        return ()
    target = min(counts, key=lambda seat: (-counts[seat], seat))
    common = {
        "expected_revision": context.revision,
        "source_event_id": context.source_event_id,
    }
    return (GameEffect(
        derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_DAMAGE,
        context.action_key, target_seat=target,
        payload={"target": target, "amount": 1, "cause": "wolf_kill"},
        sort_key=(1,), **common,
    ), GameEffect(
        derive_effect_id(context.action_key, 2), EffectKind.EMIT_EVENT,
        context.action_key,
        payload={
            "event_type": "WEREWOLF_KILL",
            "payload": {
                "target_seat": target,
                "vote_counts": {str(seat): count for seat, count in counts.items()},
            },
        },
        visibility=("PUBLIC",),
        sort_key=(2,), **common,
    ),)


WEREWOLF_SPEC = RoleSpec(
    role_id="wolf-killer-werewolf", display_name="Werewolf", camp_id="werewolf",
    schema_version=2,
    contracts=(ActionContract(
        contract_id="werewolf_kill", schedule_point=SchedulePoint.NIGHT_WOLF_VOTE,
        order=10, action_types=("kill", "pass"),
        actions_requiring_target=frozenset({"kill"}), fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
        is_applicable=werewolf_applicable, validate=validate_werewolf_action,
        aggregate=aggregate_werewolf_votes,
    ),),
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    instructions="Choose a night kill target or pass.",
)


class Werewolf(BaseRole):
    """Werewolf role: can kill at night. Wolves coordinate through their kill votes."""

    def get_skills(self) -> list[str]:
        return ["kill"]

    async def kill(
        self, state: GameState, conversation_log: ConversationLog
    ) -> NightAction:
        """Decide kill target. Can see previous wolves' votes via conversation log."""
        prompt = self.prompt_builder.build_action_prompt(
            state, self.seat, self.role_name, conversation_log, "night_kill"
        )
        raw = await self._invoke_llm(prompt)
        action = self.output_parser.parse_night_action(raw, self.seat)
        return action
