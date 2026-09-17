from __future__ import annotations

import json

import pytest

from app.core.sheriff_flow import (
    DEATH_LEFT,
    DEATH_RIGHT,
    SHERIFF_LEFT,
    SHERIFF_RIGHT,
    SheriffDirector,
    apply_badge,
    badge_targets,
    ballot_weight,
    clear_office,
    decide_after_runs,
    decide_after_withdraw,
    decide_tally,
    eligible_sheriff_voters,
    night_death_seats,
    office_enabled,
    parse_badge,
    parse_campaign,
    parse_explode_choice,
    parse_side,
    parse_vote,
    parse_withdraw,
    set_sheriff,
    sheriff_active,
    should_run_election,
    speech_order,
    speech_sides,
)
from app.core.conversation_log import ConversationLog
from app.models.actions import DeathReport
from app.models.game import Camp, GameConfig, GameState, PlayerState


def _state(*, enable: bool = True, seats: int = 5, sheriff: int | None = None) -> GameState:
    state = GameState(
        game_id="sheriff-test",
        config=GameConfig(
            role_counts={
                "wolf-killer-werewolf": 1,
                "wolf-killer-villager": seats - 1,
            },
            enable_sheriff=enable,
        ),
    )
    for seat in range(1, seats + 1):
        camp = Camp.WEREWOLF.value if seat == 1 else Camp.GOOD.value
        role = "wolf-killer-werewolf" if seat == 1 else "wolf-killer-villager"
        state.players[seat] = PlayerState(seat, role, camp)
    if sheriff is not None:
        set_sheriff(state, sheriff)
    return state


def test_office_flag_defaults_off_and_gates_election() -> None:
    off = _state(enable=False)
    assert office_enabled(off) is False
    assert should_run_election(off) is False
    on = _state(enable=True)
    assert should_run_election(on) is True
    on.sheriff_election_complete = True
    assert should_run_election(on) is False


def test_election_decisions_cover_empty_auto_campaign_and_all_ran() -> None:
    alive = frozenset({1, 2, 3, 4})
    assert decide_after_runs(frozenset(), alive) == "none"
    assert decide_after_runs(frozenset({2}), alive) == "auto"
    assert decide_after_runs(frozenset({2, 3}), alive) == "campaign"
    assert decide_after_withdraw(frozenset({1, 2, 3, 4}), frozenset({1, 2}), alive) == "none"
    assert decide_after_withdraw(frozenset({2, 3}), frozenset(), alive) == "none"
    assert decide_after_withdraw(frozenset({2, 3}), frozenset({2}), alive) == "auto"
    assert decide_after_withdraw(frozenset({2, 3}), frozenset({2, 3}), alive) == "vote"


def test_eligible_voters_exclude_anyone_who_ran() -> None:
    assert eligible_sheriff_voters(frozenset({1, 3}), frozenset({1, 2, 3, 4})) == frozenset({2, 4})


def test_tally_picks_unique_winner_or_tie() -> None:
    assert decide_tally({2: 3, 3: 1}) == (2, ())
    assert decide_tally({2: 2, 3: 2}) == (None, (2, 3))
    assert decide_tally({}) == (None, ())


def test_set_and_clear_sheriff_updates_player_flags() -> None:
    state = _state()
    assert sheriff_active(state) is False
    set_sheriff(state, 3)
    assert state.sheriff == 3
    assert state.players[3].is_sheriff is True
    assert state.sheriff_office.badge_destroyed is False
    assert sheriff_active(state) is True
    state.sheriff = 9
    assert sheriff_active(state) is False
    clear_office(state, destroyed=True)
    assert state.sheriff is None
    assert state.players[3].is_sheriff is False
    assert state.sheriff_office.badge_destroyed is True


def test_ballot_weight_is_one_unless_living_sheriff_office_is_active() -> None:
    off = _state(enable=False, sheriff=2)
    assert ballot_weight(off, 2) == 1
    assert ballot_weight(off, 3) == 1
    lost = _state(enable=True)
    clear_office(lost, destroyed=True)
    assert ballot_weight(lost, 2) == 1
    on = _state(enable=True, sheriff=2)
    assert ballot_weight(on, 2) == 3
    assert ballot_weight(on, 3) == 2
    on.players[2].is_alive = False
    assert ballot_weight(on, 2) == 1


def test_speech_sides_and_order_use_anchor_neighbors() -> None:
    assert speech_sides(0) == (SHERIFF_LEFT, SHERIFF_RIGHT)
    assert speech_sides(2) == (SHERIFF_LEFT, SHERIFF_RIGHT)
    assert speech_sides(1) == (DEATH_LEFT, DEATH_RIGHT)
    alive = (1, 2, 3, 4, 5)
    assert speech_order(alive, 3, SHERIFF_LEFT) == [4, 5, 1, 2, 3]
    assert speech_order(alive, 3, SHERIFF_RIGHT) == [2, 1, 5, 4, 3]
    assert speech_order(alive, 1, DEATH_LEFT) == [2, 3, 4, 5, 1]
    assert speech_order(alive, 5, DEATH_RIGHT) == [4, 3, 2, 1, 5]
    assert speech_order((1, 2, 3), 9, SHERIFF_LEFT) == [1, 2, 3]
    assert speech_order((2, 3, 4), 1, SHERIFF_RIGHT) == [4, 3, 2]
    assert speech_order((1, "x", 3), 1, SHERIFF_LEFT) == [3, 1]
    assert speech_order((), 1, SHERIFF_LEFT) == []


def test_badge_transfer_and_tear() -> None:
    state = _state(sheriff=2)
    assert badge_targets(state, 2) == (1, 3, 4, 5)
    apply_badge(state, 2, "tear", None)
    assert state.sheriff is None
    assert state.sheriff_office.badge_destroyed is True
    set_sheriff(state, 2)
    apply_badge(state, 2, "transfer", 4)
    assert state.sheriff == 4
    assert state.players[4].is_sheriff is True
    assert state.players[2].is_sheriff is False
    apply_badge(state, 4, "transfer", 99)
    assert state.sheriff is None
    assert state.sheriff_office.badge_destroyed is True


def test_night_death_seats_are_current_round_only() -> None:
    state = _state()
    state.round_number = 2
    state.death_history = [
        DeathReport(3, "wolf_kill", 1),
        DeathReport(4, "wolf_kill", 2),
        DeathReport(5, "poison", 2),
    ]
    assert night_death_seats(state) == (4, 5)


def test_parsers_default_safely_and_recognize_explode_only_for_wolves() -> None:
    assert parse_campaign({"action_type": "run"}, wolf=False) == "run"
    assert parse_campaign({"action_type": "explode"}, wolf=False) == "pass"
    assert parse_campaign({"action_type": "explode"}, wolf=True) == "explode"
    assert parse_campaign("bad", wolf=True) == "pass"
    assert parse_withdraw({"action_type": "withdraw"}, wolf=False) == "withdraw"
    assert parse_withdraw({"action_type": "explode"}, wolf=True) == "explode"
    assert parse_withdraw(None, wolf=False) == "stay"
    assert parse_vote({"action_type": "vote", "target_seat": 3}, {2, 3}, wolf=False) == 3
    assert parse_vote({"action_type": "vote", "target_seat": 9}, {2, 3}, wolf=False) is None
    assert parse_vote({"action_type": "explode"}, {2, 3}, wolf=True) == "explode"
    assert parse_vote("x", {2}, wolf=False) is None
    assert parse_side({"side": SHERIFF_LEFT}, (SHERIFF_LEFT, SHERIFF_RIGHT)) == SHERIFF_LEFT
    assert parse_side({"side": "nope"}, (SHERIFF_LEFT, SHERIFF_RIGHT)) == SHERIFF_LEFT
    assert parse_badge({"action_type": "transfer", "target_seat": 4}, {3, 4}) == ("transfer", 4)
    assert parse_badge({"action_type": "transfer", "target_seat": 9}, {3, 4}) == ("tear", None)
    assert parse_badge({"action_type": "tear"}, {3}) == ("tear", None)
    assert parse_explode_choice({"action_type": "explode"}, wolf=True) is True
    assert parse_explode_choice({"action_type": "explode"}, wolf=False) is False


def test_director_prompts_include_sheriff_rules_and_only_current_tools() -> None:
    state = _state()
    director = SheriffDirector()
    run_prompt = json.dumps(director.campaign_prompt(state, 2, wolf=False), ensure_ascii=False)
    assert "上警" in run_prompt
    assert "1.5" in run_prompt
    assert "explode" not in run_prompt
    wolf_prompt = json.dumps(director.campaign_prompt(state, 1, wolf=True), ensure_ascii=False)
    assert "explode" in wolf_prompt
    vote_human = director.vote_prompt(state, 4, (2, 3))[1]["content"]
    assert "2号" in vote_human and "3号" in vote_human
    assert "action_type=vote" in vote_human
    withdraw_human = director.withdraw_prompt(state, 2, wolf=False)[1]["content"]
    assert "退水" in withdraw_human
    assert "explode" not in withdraw_human
    assert "sheriff_left" in director.side_prompt(state, 2, (SHERIFF_LEFT, SHERIFF_RIGHT))[1]["content"]
    assert "撕毁" in director.badge_prompt(state, 2, (3, 4))[1]["content"]
    assert "无" in director.badge_prompt(state, 2, ())[1]["content"]


def test_director_turns_use_invoke_and_fall_back() -> None:
    calls: list[str] = []

    def invoke(messages, tool_name, schema, seat):
        calls.append(tool_name)
        if tool_name == "sheriff_campaign":
            return '{"action_type":"run"}'
        if tool_name == "sheriff_withdraw":
            raise RuntimeError("timeout")
        if tool_name == "sheriff_vote":
            return '{"action_type":"vote","target_seat":2}'
        if tool_name == "sheriff_speech_side":
            return '{"action_type":"choose","side":"sheriff_right"}'
        return '{"action_type":"transfer","target_seat":4}'

    director = SheriffDirector(invoke)
    state = _state()
    assert director.campaign_turn(state, 2, wolf=False) == "run"
    assert director.withdraw_turn(state, 2, wolf=False) == "stay"
    assert director.vote_turn(state, 4, (2, 3), wolf=False) == 2
    assert director.side_turn(state, 2, (SHERIFF_LEFT, SHERIFF_RIGHT)) == SHERIFF_RIGHT
    assert director.badge_turn(state, 2, (3, 4)) == ("transfer", 4)
    assert calls[0] == "sheriff_campaign"

    empty = SheriffDirector()
    assert empty.campaign_turn(state, 2, wolf=False) == "pass"
    assert empty.withdraw_turn(state, 2, wolf=False) == "stay"
    assert empty.vote_turn(state, 4, (2, 3), wolf=False) is None
    assert empty.side_turn(state, 2, (SHERIFF_LEFT, SHERIFF_RIGHT)) == SHERIFF_LEFT
    assert empty.badge_turn(state, 2, (3, 4)) == ("tear", None)

    def bad_invoke(messages, tool_name, schema, seat):
        if tool_name == "sheriff_campaign":
            return {"action_type": "run"}
        if tool_name == "sheriff_withdraw":
            return '["withdraw"]'
        return 1

    broken = SheriffDirector(bad_invoke)
    assert broken.campaign_turn(state, 2, wolf=False) == "pass"
    assert broken.withdraw_turn(state, 2, wolf=False) == "stay"
    assert broken.vote_turn(state, 4, (2, 3), wolf=False) is None

    def wolf_invoke(messages, tool_name, schema, seat):
        if tool_name == "sheriff_campaign":
            return '{"action_type":"explode"}'
        if tool_name == "sheriff_withdraw":
            return '{"action_type":"withdraw"}'
        if tool_name in {"sheriff_speech_side", "sheriff_badge"}:
            raise RuntimeError("timeout")
        return '{"action_type":"explode"}'

    wolves = SheriffDirector(wolf_invoke)
    assert wolves.campaign_turn(state, 1, wolf=True) == "explode"
    assert wolves.withdraw_turn(state, 1, wolf=True) == "withdraw"
    assert wolves.vote_turn(state, 1, (2, 3), wolf=True) == "explode"
    assert wolves.side_turn(state, 2, (SHERIFF_LEFT, SHERIFF_RIGHT)) == SHERIFF_LEFT
    assert wolves.badge_turn(state, 2, (3, 4)) == ("tear", None)


def test_director_rejects_non_callable_invoke() -> None:
    with pytest.raises(TypeError, match="callable"):
        SheriffDirector(invoke=1)  # type: ignore[arg-type]


# ── Context-aware director prompts (regression: 12-man all-run badge loss) ──

def _rich_state(*, seats: int = 12) -> GameState:
    """12-seat state mirroring game 453ae071: seer 5, wolves 2/6/10/12."""
    counts = {
        "wolf-killer-werewolf": 4, "wolf-killer-villager": 4,
        "wolf-killer-seer": 1, "wolf-killer-witch": 1,
        "wolf-killer-hunter": 1, "wolf-killer-idiot": 1,
    }
    state = GameState(
        game_id="sheriff-ctx", config=GameConfig(role_counts=counts, enable_sheriff=True),
    )
    roles = {
        1: "wolf-killer-villager", 2: "wolf-killer-werewolf", 3: "wolf-killer-villager",
        4: "wolf-killer-hunter", 5: "wolf-killer-seer", 6: "wolf-killer-werewolf",
        7: "wolf-killer-villager", 8: "wolf-killer-idiot", 9: "wolf-killer-villager",
        10: "wolf-killer-werewolf", 11: "wolf-killer-witch", 12: "wolf-killer-werewolf",
    }
    for seat in range(1, seats + 1):
        role = roles[seat]
        camp = Camp.WEREWOLF.value if role == "wolf-killer-werewolf" else Camp.GOOD.value
        state.players[seat] = PlayerState(seat, role, camp)
    state.round_number = 1
    return state


def _campaign_log() -> ConversationLog:
    from app.core.conversation_log import ConversationLog
    log = ConversationLog()
    log.add_public_speech(5, "wolf-killer-seer", "我是真预言家，昨晚查验3号是金水。", 1, "sheriff_election")
    log.add_public_speech(6, "wolf-killer-werewolf", "6号才是全场唯一真预言家，昨晚查验8号。", 1, "sheriff_election")
    return log


def test_campaign_prompt_carries_identity_and_campaign_speeches() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    office = state.sheriff_office
    office.candidates = {1, 2, 3, 4, 5}
    office.active = {1, 2, 3, 4, 5}
    director = Director(conversation_log=_campaign_log())
    human = director.campaign_prompt(state, 6, wolf=True)[1]["content"]
    assert "6号" in human
    assert "camp_id\":\"werewolf\"" in human or "狼人" in human
    assert "警下" in human and "投票权" in human
    assert "自爆" in human
    assert "我是真预言家" in human  # campaign speech excerpt
    assert "已上警" in human


def _wolf_channel_log() -> "object":
    from app.core.conversation_log import ConversationLog
    log = ConversationLog()
    log.add_werewolf_channel(
        "1号：今晚统一刀6号。白天2号悍跳预言家，12号警下冲票，10号倒钩做深水。",
        1, speaker_seat=1, speaker_role="wolf-killer-werewolf",
    )
    log.add_werewolf_channel(
        "2号：同意刀6号。我的次日计划：起跳发查杀，压8号；你们站边我。",
        1, speaker_seat=2, speaker_role="wolf-killer-werewolf",
    )
    return log


def test_wolf_sheriff_prompts_carry_teammates_and_channel_plan() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    log = _wolf_channel_log()
    director = Director(conversation_log=log)
    for prompt in (
        director.campaign_prompt(state, 2, wolf=True),
        director.withdraw_prompt(state, 2, wolf=True),
        director.vote_prompt(state, 2, (5, 6), wolf=True),
    ):
        human = prompt[1]["content"]
        assert "狼队成员" in human
        assert "2号、6号、10号、12号" in human
        assert "悍跳预言家" in human
        assert "次日计划" in human or "白天" in human


def test_good_player_sheriff_prompts_never_see_wolf_channel() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    director = Director(conversation_log=_wolf_channel_log())
    for prompt in (
        director.campaign_prompt(state, 5, wolf=False),
        director.withdraw_prompt(state, 5, wolf=False),
        director.vote_prompt(state, 9, (5, 6)),
    ):
        human = prompt[1]["content"]
        assert "狼队成员" not in human
        assert "悍跳预言家" not in human
        assert "次日计划" not in human


def test_sheriff_prompts_survive_empty_wolf_channel() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    director = Director()
    human = director.campaign_prompt(state, 2, wolf=True)[1]["content"]
    assert "狼队成员" in human
    assert "2号、6号、10号、12号" in human
    assert "狼队频道记录" not in human


def test_wolf_team_block_skips_non_wolf_and_truncates_long_channel() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    from app.core.conversation_log import ConversationLog
    state = _rich_state()
    director = Director()
    # Good seat: no wolf block even when wolf=True is passed by mistake.
    assert director._wolf_team_block(state, 5) == []
    assert director._wolf_team_block(state, 99) == []
    log = _wolf_channel_log()
    log.add_werewolf_channel("长" * 1200, 1, speaker_seat=None,
                             speaker_role="wolf-killer-werewolf")
    director = Director(conversation_log=log)
    lines = director._wolf_team_block(state, 2)
    channel_line = lines[-1]
    assert channel_line.endswith("...")
    assert len(channel_line) < 1500


def test_campaign_prompt_shows_seer_private_check() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    from app.core.effect_applier import (
        EffectApplier, EffectPermission, EffectKind, derive_effect_id,
    )
    from app.models.pipeline import GameEffect
    state = _rich_state()
    effects = (
        GameEffect(
            derive_effect_id("seer:1:check", 0), EffectKind.ACCEPT_ACTION,
            "seer:1:check", payload={}, visibility=("ACTOR",),
            expected_revision=0, sort_key=(0,),
        ),
        GameEffect(
            derive_effect_id("seer:1:check", 1), EffectKind.RECORD_PRIVATE_FACT,
            "seer:1:check",
            payload={"target": 5, "namespace": "private_checks",
                     "fact": {"target": 3, "camp": "good"}},
            visibility=("ACTOR",), expected_revision=0,
            target_seat=5, sort_key=(1,),
        ),
    )
    permission = EffectPermission(
        5,
        frozenset({EffectKind.RECORD_PRIVATE_FACT}),
        frozenset({EffectKind.RECORD_PRIVATE_FACT}),
        frozenset({5}),
        frozenset({"ACTOR"}),
    )
    EffectApplier().apply(state, effects, permission)
    human = Director().campaign_prompt(state, 5, wolf=False)[1]["content"]
    assert "查验" in human and "3号" in human
    assert "好人" in human


def test_withdraw_prompt_carries_speeches_and_consequences() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    office = state.sheriff_office
    office.candidates = {1, 2, 5}
    office.active = {1, 2, 5}
    human = Director(conversation_log=_campaign_log()).withdraw_prompt(state, 5, wolf=False)[1]["content"]
    assert "退水" in human
    assert "失去" in human and "投票" in human  # withdrawal loses sheriff vote
    assert "我是真预言家" in human


def test_vote_prompt_lists_candidates_and_speeches() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    office = state.sheriff_office
    office.candidates = {5, 6}
    office.active = {5, 6}
    human = Director(conversation_log=_campaign_log()).vote_prompt(state, 9, (5, 6))[1]["content"]
    assert "从未上警" in human
    assert "5号" in human and "6号" in human
    assert "我是真预言家" in human


def test_badge_loss_message_covers_all_reasons() -> None:
    from app.core.sheriff_flow import badge_loss_message
    full_slate = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12})
    all_alive = frozenset(range(1, 13))
    # 12-man all-ran, 10 withdrew, 2 remain: no eligible voters -> badge lost
    message = badge_loss_message("none", full_slate, all_alive, frozenset({1, 2}))
    assert "全员上警" in message and "流失" in message
    assert "无人上警" in badge_loss_message("none", frozenset(), all_alive, frozenset())
    assert "平票" in badge_loss_message("tie", frozenset({5, 6}), all_alive, frozenset({5, 6}))
    assert "退水" in badge_loss_message("none", frozenset({5, 6}), all_alive, frozenset())


def test_rules_text_matches_withdraw_semantics() -> None:
    from app.agents.game_rules import SHERIFF_GAME_RULES
    text = " ".join(SHERIFF_GAME_RULES)
    assert "every living player ran" in text
    assert "withdrew" in text


def test_withdraw_turn_receives_context_messages() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    captured: list[list[dict[str, str]]] = []

    def invoke(messages, tool_name, schema, seat):
        captured.append(messages)
        return '{"action_type":"withdraw"}'

    state = _rich_state()
    office = state.sheriff_office
    office.candidates = {5, 6}
    office.active = {5, 6}
    director = Director(invoke=invoke, conversation_log=_campaign_log())
    assert director.withdraw_turn(state, 5, wolf=False) == "withdraw"
    human = captured[0][1]["content"]
    assert "我是真预言家" in human
    assert "5号" in human


def test_director_rejects_non_conversation_log() -> None:
    with pytest.raises(TypeError, match="ConversationLog"):
        SheriffDirector(conversation_log="nope")  # type: ignore[arg-type]


def test_speech_lines_truncate_and_use_registry_fallbacks() -> None:
    from app.core.conversation_log import ConversationLog
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    log = ConversationLog()
    log.add_public_speech(5, "wolf-killer-seer", "长" * 300, 1, "sheriff_election")
    lines = Director(conversation_log=log)._speech_lines(state)
    assert lines and lines[0].startswith("5号：") and lines[0].endswith("...")
    assert len(lines[0]) == 166  # "5号：" + 160 truncated chars + "..."
    # Unknown role string falls back to the raw role id.
    state.players[5].role = "unknown-role"
    human = Director().campaign_prompt(state, 5, wolf=False)[1]["content"]
    assert "unknown-role" in human
    # Missing player falls back to generic identity.
    human = Director().campaign_prompt(state, 99, wolf=False)[1]["content"]
    assert "99号" in human and "玩家" in human


def test_private_fact_and_resource_paths_cover_all_branches() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    from app.core.effect_applier import (
        EffectApplier, EffectPermission, EffectKind, derive_effect_id,
    )
    from app.core.role_runtime import initialize_role_resources
    from app.models.pipeline import GameEffect
    from app.roles.registry import builtin_registry

    def _seed_resources(state, seat):
        registry = builtin_registry.freeze()
        initialize_role_resources(state, registry.specs, registry.digest)

    def _apply(state, action, effects_spec, target):
        revision = getattr(
            getattr(state, "_pipeline_runtime", None), "revision", 0,
        )
        effects = (
            GameEffect(
                derive_effect_id(f"ctx:{action}", 0), EffectKind.ACCEPT_ACTION,
                f"ctx:{action}", payload={}, visibility=("ACTOR",),
                expected_revision=revision, sort_key=(0,),
            ),
            *(
                GameEffect(
                    derive_effect_id(f"ctx:{action}", ordinal), kind,
                    f"ctx:{action}", payload=payload, visibility=("ACTOR",),
                    expected_revision=revision,
                    target_seat=payload.get("target"), sort_key=(ordinal,),
                )
                for ordinal, (kind, payload) in enumerate(effects_spec, start=1)
            ),
        )
        permission = EffectPermission(
            target, frozenset({kind for kind, _ in effects_spec}),
            frozenset({kind for kind, _ in effects_spec}),
            frozenset({target}), frozenset({"ACTOR"}),
        )
        EffectApplier().apply(state, effects, permission)

    # Witch with resources seeded by the REAL production API, then effects
    # applied to drain antidote and record a check.
    state = _rich_state()
    _seed_resources = None
    registry = builtin_registry.freeze()
    initialize_role_resources(state, registry.specs, registry.digest)
    _apply(state, "witch", (
        (EffectKind.SET_RESOURCE, {"target": 11, "resource": "antidote", "value": 0}),
        (EffectKind.RECORD_PRIVATE_FACT,
         {"target": 11, "namespace": "private_checks",
          "fact": {"target": 9, "camp": "werewolf"}}),
    ), 11)
    human = Director().campaign_prompt(state, 11, wolf=False)[1]["content"]
    assert "解药已用" in human and "毒药仍在手" in human
    assert "查验9号：狼人" in human

    # Hunter carries a gun line.
    state2 = _rich_state()
    initialize_role_resources(state2, registry.specs, registry.digest)
    _apply(state2, "hunter", (
        (EffectKind.SET_RESOURCE, {"target": 4, "resource": "gun", "value": 1}),
    ), 4)
    human = Director().campaign_prompt(state2, 4, wolf=False)[1]["content"]
    assert "猎枪仍在手" in human

    # Poison drained only.
    state3 = _rich_state()
    initialize_role_resources(state3, registry.specs, registry.digest)
    _apply(state3, "witch3", (
        (EffectKind.SET_RESOURCE, {"target": 11, "resource": "poison", "value": 0}),
    ), 11)
    human = Director().campaign_prompt(state3, 11, wolf=False)[1]["content"]
    assert "解药仍在手" in human and "毒药已用" in human


def test_context_block_survives_malformed_private_state() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    # Corrupt runtime objects to force both guarded exception paths.
    class _Boom:
        def clone(self):
            raise RuntimeError("boom")

    runtime = type("_R", (), {})()
    runtime.role_resources = {"11": _Boom()}
    runtime.private_facts = {"11": [{"namespace": "private_checks", "fact": {"target": 9}}]}
    state._pipeline_runtime = runtime
    human = Director().campaign_prompt(state, 11, wolf=False)[1]["content"]
    assert "暂无夜间私密信息" in human
