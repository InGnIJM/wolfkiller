"""Hidden-wolf extension sample.

Werewolf-camp membership and *pack* membership are two different things. The win
check keeps reading the camp, so a hidden wolf still counts as a werewolf; the
nightly discussion, the kill vote, the wolf channel, the teammate lists and the
``CAMP`` projection read pack membership instead. This file pins both directions
of the blind spot, because getting either one wrong is silent: leak the roster
and the Gargoyle's whole board disappears, hide the camp and the win check
undercounts the wolves.
"""

from __future__ import annotations

import pytest

from app.core.context_projector import ContextProjector
from app.core.conversation_log import ConversationLog
from app.core.night_flow import NightDirector
from app.core.role_runtime import has_role_marker, initialize_role_resources
from app.core.rule_engine import RuleEngine
from app.core.scheduler import IssuedActionRequest, Scheduler
from app.core.wolf_pack import (
    NO_WOLF_PACK_RESOURCE, WOLF_KNOWS_PACK_RESOURCE, actor_sees_pack, in_wolf_pack,
    is_werewolf_camp, pack_seats,
)
from app.models.conversation import ConversationScope
from app.models.game import Camp, GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, RoleSpec, SchedulePoint,
)
from app.roles.hidden_wolf import (
    HIDDEN_WOLF_KILL_CONTRACT, hidden_wolf_applicable, hidden_wolf_kill_effects,
    hidden_wolf_role_spec, resolve_hidden_wolf_kill, validate_hidden_wolf_kill,
)
from app.roles.registry import RegistrySnapshot, RoleRegistry
from app.roles.seer import SEER_SPEC
from app.roles.villager import VILLAGER_SPEC
from app.roles.werewolf import WEREWOLF_SPEC

HIDDEN = "wolf-killer-test-hidden"
SEES = "wolf-killer-test-sees"

HIDDEN_SPEC = hidden_wolf_role_spec(HIDDEN, "Hidden", instructions="隐狼。")
# The one-way variant: it sees the pack, the pack does not see it.
SEES_SPEC = hidden_wolf_role_spec(
    SEES, "Sees", instructions="单向。",
    extra_resources={WOLF_KNOWS_PACK_RESOURCE: 1},
)


# A stand-in for any future good-camp role that asks for the CAMP namespace.
# Nothing today does, so this is the only way to exercise the generic branch —
# and it is the branch that proves the wolf-specific blind spot does not leak
# into a good-camp roster.
WIDE = "wolf-killer-test-wide-camp"
WIDE_SPEC = RoleSpec(
    tags=frozenset({"villager"}), role_id=WIDE, display_name="Wide", camp_id="good",
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
)


def registry() -> RegistrySnapshot:
    registry = RoleRegistry()
    registry.register_pipeline(WIDE_SPEC)
    registry.register_pipeline(WEREWOLF_SPEC)
    registry.register_pipeline(VILLAGER_SPEC)
    registry.register_pipeline(SEER_SPEC)
    registry.register_pipeline(HIDDEN_SPEC)
    registry.register_pipeline(SEES_SPEC)
    return registry.freeze()


def state(*, seats: dict[int, str]) -> GameState:
    game = GameState(
        "g", config=GameConfig(), phase=GamePhase.NIGHT, round_number=1,
        players={
            seat: PlayerState(seat, role, "werewolf")
            for seat, role in seats.items()
        },
    )
    snapshot = registry()
    initialize_role_resources(game, snapshot.specs, snapshot.digest)
    return game


def plain_game() -> GameState:
    """1 plain wolf, 2 hidden wolf, 3 one-way wolf, 4/5 villagers."""
    game = state(seats={
        1: WEREWOLF_SPEC.role_id, 2: HIDDEN, 3: SEES,
        4: VILLAGER_SPEC.role_id, 5: VILLAGER_SPEC.role_id,
    })
    for seat in (4, 5):
        game.players[seat].camp = Camp.GOOD
    return game


# ── the predicate ─────────────────────────────────────────────────────────

def test_pack_membership_reads_the_marker_not_the_camp() -> None:
    game = plain_game()
    assert in_wolf_pack(game, 1) is True
    assert in_wolf_pack(game, 2) is False
    assert in_wolf_pack(game, 3) is False
    assert in_wolf_pack(game, 4) is False
    assert pack_seats(game) == (1,)


def test_camp_membership_still_counts_every_werewolf() -> None:
    game = plain_game()
    assert [seat for seat in sorted(game.players) if is_werewolf_camp(game.players[seat])] == [
        1, 2, 3]
    assert all(is_werewolf_camp(game.players[seat]) for seat in (1, 2, 3))


def test_marker_helper_reads_declared_resources() -> None:
    game = plain_game()
    assert has_role_marker(game, 2, NO_WOLF_PACK_RESOURCE) is True
    assert has_role_marker(game, 1, NO_WOLF_PACK_RESOURCE) is False
    with pytest.raises(ValueError):
        has_role_marker(game, 1, "")


def test_actor_sees_pack_is_camp_and_pack() -> None:
    game = plain_game()
    assert actor_sees_pack(game, 1) is True      # plain wolf
    assert actor_sees_pack(game, 2) is False     # hidden, and blind
    assert actor_sees_pack(game, 3) is True      # hidden, but the one-way variant
    assert actor_sees_pack(game, 4) is False     # not a wolf at all
    assert actor_sees_pack(game, 99) is False    # unknown seat


# ── projection: the blind spot is asymmetric ──────────────────────────────

def _facts(game: GameState, seat: int, snapshot: RegistrySnapshot) -> dict:
    role = game.players[seat].role
    contract = snapshot.require(role).contracts[0]
    request = IssuedActionRequest(
        seat, role, contract, 0, 1, "night", "w", "a",
    )
    context = ContextProjector().project(game, request, snapshot)
    return dict(context.facts)


def test_plain_wolf_sees_the_pack_but_not_the_hidden_wolves() -> None:
    game, snapshot = plain_game(), registry()
    facts = _facts(game, 1, snapshot)
    assert facts["camp_members"] == (1,)
    assert "wolf_pack_count" not in facts


def test_hidden_wolf_counts_the_pack_without_learning_its_seats() -> None:
    game, snapshot = plain_game(), registry()
    facts = _facts(game, 2, snapshot)
    assert facts["camp_members"] == ()
    assert facts["wolf_pack_count"] == 1


def test_one_way_variant_sees_the_pack_that_cannot_see_it() -> None:
    game, snapshot = plain_game(), registry()
    facts = _facts(game, 3, snapshot)
    assert facts["camp_members"] == (1,)
    assert facts["wolf_pack_count"] == 1


def test_pack_count_tracks_life_state() -> None:
    game, snapshot = plain_game(), registry()
    game.players[1].is_alive = False
    assert _facts(game, 2, snapshot)["wolf_pack_count"] == 0
    # A dead pack member stays listed for the seats allowed to see the roster.
    assert _facts(game, 3, snapshot)["camp_members"] == (1,)


def test_a_good_camp_role_declaring_camp_sees_its_own_roster() -> None:
    game = state(seats={1: WIDE, 2: VILLAGER_SPEC.role_id, 3: WEREWOLF_SPEC.role_id})
    for seat in (1, 2):
        game.players[seat].camp = Camp.GOOD
    snapshot = registry()
    facts = dict(ContextProjector().project_view(game, snapshot, 1).facts)
    # Camp-based, and untouched by the wolf pack's blind spot.
    assert facts["camp_members"] == (1, 2)
    assert "wolf_pack_count" not in facts


def test_good_camp_projection_is_untouched() -> None:
    game = state(seats={1: VILLAGER_SPEC.role_id, 2: WEREWOLF_SPEC.role_id})
    game.players[1].camp = Camp.GOOD
    snapshot = registry()
    facts = dict(ContextProjector().project_view(game, snapshot, 1).facts)
    # Villagers declare no CAMP namespace, so the projector adds nothing at all.
    assert "camp_members" not in facts
    assert "wolf_pack_count" not in facts


# ── the prompt ────────────────────────────────────────────────────────────

def test_wolf_prompts_never_name_a_hidden_teammate() -> None:
    game, snapshot = plain_game(), registry()
    director = NightDirector(
        snapshot, lambda messages, _tool_name, _schema, _seat: "{}",
    )
    assert director._wolf_team(game) == [1]
    for messages in (
        director.discussion_prompt(game, 1, []),
        director.vote_prompt(game, 1, [], []),
    ):
        text = "\n".join(message["content"] for message in messages)
        teammates = text.split("你的队友：", 1)[1].split("号。", 1)[0]
        assert teammates == "1"
        assert "2" not in teammates and "3" not in teammates


def test_a_hidden_wolfs_own_prompt_never_names_the_pack() -> None:
    """The roster line is a visibility decision, not a pack-membership one.

    A hidden wolf may never be told who the pack is, even if it reaches a wolf
    prompt by mistake; the one-way variant (``wolf_knows_pack``, seat 3 here)
    still reads the pack it stays invisible to.
    """
    game, snapshot = plain_game(), registry()
    director = NightDirector(
        snapshot, lambda messages, _tool_name, _schema, _seat: "{}",
    )
    for messages in (
        director.discussion_prompt(game, 2, []),
        director.vote_prompt(game, 2, [], []),
    ):
        text = "\n".join(message["content"] for message in messages)
        assert "你的队友：" not in text
        assert "你不属于狼窝，你看不到任何队友。" in text
    one_way = director.discussion_prompt(game, 3, [])
    one_way_text = "\n".join(message["content"] for message in one_way)
    assert one_way_text.split("你的队友：", 1)[1].split("号。", 1)[0] == "1"


# ── the wolf channel ──────────────────────────────────────────────────────

def test_hidden_wolf_cannot_read_the_wolf_channel() -> None:
    log = ConversationLog()
    log.add_werewolf_channel("今晚刀 4 号", 1, speaker_seat=1)
    assert [r.content for r in log.get_conversations_for_role(1, "werewolf")] == [
        "今晚刀 4 号"]
    assert log.get_conversations_for_role(2, "werewolf", in_pack=False) == []
    # The default keeps the long-standing camp-only behaviour for callers that
    # have no state to read a marker from.
    assert len(log.get_conversations_for_role(2, "werewolf")) == 1


# ── the lone kill ─────────────────────────────────────────────────────────

def context(*, pack_count: int | None, alive: bool = True) -> ActionContext:
    contract = HIDDEN_WOLF_KILL_CONTRACT
    facts: dict[str, object] = {"alive_seats": (1, 2, 3)}
    if pack_count is not None:
        facts["wolf_pack_count"] = pack_count
    return ActionContext(
        "g", 0, facts, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=1, phase="night",
        window_id="w", schedule_point=SchedulePoint.NIGHT_WOLF_VOTE, actor_seat=2,
        actor_role_id=HIDDEN, actor_alive=alive, action_key="a",
    )


def test_lone_kill_opens_only_once_the_pack_is_gone() -> None:
    assert hidden_wolf_applicable(context(pack_count=0)) is True
    assert hidden_wolf_applicable(context(pack_count=1)) is False
    assert hidden_wolf_applicable(context(pack_count=None)) is False
    assert hidden_wolf_applicable(context(pack_count=0, alive=False)) is False


def test_lone_kill_rejects_cutting_itself() -> None:
    def command(action: str, target: int | None = None) -> ActionCommand:
        return ActionCommand(action_type=action, target_seat=target, reasoning="ok")

    assert validate_hidden_wolf_kill(context(pack_count=0), command("kill", 4)) == ()
    assert validate_hidden_wolf_kill(context(pack_count=0), command("pass")) == ()
    assert [violation.code for violation in
            validate_hidden_wolf_kill(context(pack_count=0), command("kill", 2))] == [
        "self_kill_forbidden"]


def test_lone_kill_emits_one_damage_and_one_public_event() -> None:
    command = ActionCommand(action_type="kill", target_seat=4, reasoning="ok")
    effects = resolve_hidden_wolf_kill(context(pack_count=0), command)
    assert [effect.kind for effect in effects] == [
        EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT]
    assert effects[0].payload == {"target": 4, "amount": 1, "cause": "wolf_kill"}
    assert effects[1].payload == {
        "event_type": "WEREWOLF_KILL",
        "payload": {"target_seat": 4, "vote_counts": {"4": 1}, "seat": 2},
    }
    passed = resolve_hidden_wolf_kill(
        context(pack_count=0), ActionCommand(action_type="pass", target_seat=None, reasoning="ok"),
    )
    assert passed == ()


def test_two_hidden_wolves_still_cast_one_kill_between_them() -> None:
    """The contract aggregates, so the pack's majority rule survives the pack."""
    context_ = context(pack_count=0)
    ballots = (
        ActionCommand(action_type="kill", target_seat=4, reasoning="a"),
        ActionCommand(action_type="kill", target_seat=4, reasoning="b"),
    )
    effects = hidden_wolf_kill_effects(context_, ballots)
    assert effects[0].payload["target"] == 4
    assert effects[1].payload["payload"]["vote_counts"] == {"4": 2}
    assert effects[1].payload["payload"]["seat"] == 2
    assert hidden_wolf_kill_effects(context_, (
        ActionCommand(action_type="pass", target_seat=None, reasoning="a"),
    )) == ()


def test_contract_is_scheduled_after_the_pack_vote() -> None:
    assert HIDDEN_WOLF_KILL_CONTRACT.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE
    assert HIDDEN_WOLF_KILL_CONTRACT.order > WEREWOLF_SPEC.contracts[0].order
    assert HIDDEN_WOLF_KILL_CONTRACT.fallback_action_type == "pass"
    assert HIDDEN_WOLF_KILL_CONTRACT.aggregate is hidden_wolf_kill_effects


def test_scheduler_issues_no_lone_kill_while_the_pack_lives() -> None:
    game, snapshot = plain_game(), registry()
    engine = Scheduler(
        snapshot, ContextProjector(), None, None, None,  # type: ignore[arg-type]
        lambda request, context, attempt: ActionCommand(
            action_type="pass", target_seat=None, reasoning="ok",
        ),
    )
    issued = engine.issue(game, SchedulePoint.NIGHT_WOLF_VOTE, snapshot)
    contracts = {(item.actor_seat, item.contract.contract_id) for item in issued}
    assert (1, "werewolf_kill") in contracts
    assert (2, HIDDEN_WOLF_KILL_CONTRACT.contract_id) not in contracts
    assert (3, HIDDEN_WOLF_KILL_CONTRACT.contract_id) not in contracts


def test_scheduler_issues_the_lone_kill_once_the_pack_dies() -> None:
    game, snapshot = plain_game(), registry()
    game.players[1].is_alive = False
    engine = Scheduler(
        snapshot, ContextProjector(), None, None, None,  # type: ignore[arg-type]
        lambda request, context, attempt: ActionCommand(
            action_type="pass", target_seat=None, reasoning="ok",
        ),
    )
    issued = engine.issue(game, SchedulePoint.NIGHT_WOLF_VOTE, snapshot)
    contracts = {(item.actor_seat, item.contract.contract_id) for item in issued}
    assert (2, HIDDEN_WOLF_KILL_CONTRACT.contract_id) in contracts
    assert (3, HIDDEN_WOLF_KILL_CONTRACT.contract_id) in contracts


# ── the win check ─────────────────────────────────────────────────────────

def _win_state() -> GameState:
    """One hidden wolf, one god, one villager — no pack member anywhere."""
    game = state(seats={
        1: HIDDEN, 2: SEER_SPEC.role_id, 3: VILLAGER_SPEC.role_id,
    })
    game.players[2].camp = Camp.GOOD
    game.players[3].camp = Camp.GOOD
    return game


def test_hidden_wolves_still_count_towards_the_werewolf_win() -> None:
    """The last villager dies: the wolves win the villager edge.

    A hidden wolf that did not count would leave zero wolves alive, and the
    engine would see a good camp that still holds a god and a wolf alive — no
    win at all.
    """
    game = _win_state()
    game.players[3].is_alive = False
    result = RuleEngine().check_win(game)
    assert result is not None
    assert result.winning_camp == Camp.WEREWOLF
    assert result.reason == "all_villagers_dead"


def test_a_hidden_wolf_alone_does_not_win_while_the_good_camp_stands() -> None:
    assert RuleEngine().check_win(_win_state()) is None
