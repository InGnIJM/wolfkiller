"""Iron Villager: a plain villager who may flip their card once."""

from __future__ import annotations

from app.roles.base import BaseRole
from app.models.pipeline import ActionCommand, ActionContext, EffectKind, GameEffect, RoleSpec
from app.roles.self_reveal import REVEAL_RESOURCE, resolve_reveal_action, reveal_contract


def resolve_iron_villager_reveal(
    context: ActionContext, command: ActionCommand,
) -> tuple[GameEffect, ...]:
    return resolve_reveal_action(
        context, command, event_type="IRON_VILLAGER_REASONING",
    )


IRON_VILLAGER_SPEC = RoleSpec(
    tags=frozenset({"villager"}),
    role_id="wolf-killer-iron-villager",
    display_name="Iron Villager",
    camp_id="good",
    contracts=(reveal_contract(
        contract_id="iron_villager_reveal",
        order=16,
        resolve=resolve_iron_villager_reveal,
    ),),
    initial_resources={REVEAL_RESOURCE: 1},
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions=(
        "You are a villager. You have no night skill. During the day, before a speech, "
        "you may flip your card once and show the table who you are. Flipping does not "
        "kill you, does not take your vote, and does not protect you from exile."
    ),
)


class IronVillager(BaseRole):
    """Daytime instance: the flip is a pipeline contract."""

    pass
