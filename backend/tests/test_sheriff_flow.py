from __future__ import annotations

import json
import re

import pytest

from app.core.sheriff_flow import (
    DEATH_LEFT,
    DEATH_RIGHT,
    SHERIFF_LEFT,
    SHERIFF_RIGHT,
    SHERIFF_TECHNICAL_ABSTAIN,
    SHERIFF_VOTE_FAILURE_CODE,
    SheriffDirector,
    apply_badge,
    badge_reasoning,
    badge_targets,
    ballot_weight,
    clear_office,
    decide_after_runs,
    decide_after_withdraw,
    decide_tally,
    eligible_sheriff_voters,
    election_seats,
    night_batch_deaths,
    night_death_seats,
    off_badge_seats,
    office_enabled,
    parse_badge,
    parse_campaign,
    parse_explode_choice,
    parse_side,
    parse_vote,
    parse_withdraw,
    public_alive_seats,
    public_dead_seats,
    set_sheriff,
    sheriff_active,
    should_run_election,
    speech_order,
    speech_sides,
    unannounced_death_seats,
)
from app.core.conversation_log import ConversationLog
from app.models.actions import DeathReport
from app.models.game import Camp, GameConfig, GamePhase, GameState, PlayerState


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


def test_off_badge_seats_list_everyone_who_never_ran() -> None:
    """警下名单是「竞选时在场且从未上警」的历史事实，不再按死亡过滤。

    首夜被刀的座位在死讯公布前照样上警、发言、投警长票（官方：竞选在公布死讯
    之前），所以它属于参选名单；白天再看到它时，它已经作为「从未上警者」留在
    名单里 —— 与「后来才死的座位仍留在名单里」是同一条规则。
    """
    state = _state(seats=9)
    state.sheriff_office.candidates = {1, 2}
    state.death_history = [
        DeathReport(3, "wolf_kill", 2),
        DeathReport(4, "poison", 1),
        DeathReport(5, "charm", 1),
        DeathReport(6, "hunter_shot", 1),
        DeathReport(7, "exile", 1),
        DeathReport(8, "hunter_shot", 1),
        DeathReport(9, "self_explode", 1),
    ]

    assert off_badge_seats(state) == (3, 4, 5, 6, 7, 8, 9)


def test_unannounced_deaths_are_only_the_first_nights_batch() -> None:
    """未公布死亡 = 首夜批次：竞选窗口内、非当场公布死因。"""
    state = _state(seats=9)
    state.round_number = 1
    state.phase = GamePhase.SHERIFF_ELECTION
    state.death_history = [
        DeathReport(3, "wolf_kill", 1),
        DeathReport(4, "poison", 1),
        DeathReport(5, "verify_kill", 1),
        DeathReport(6, "hunt", 1),
        DeathReport(7, "dance", 1),
        DeathReport(8, "self_explode", 1),
    ]

    assert unannounced_death_seats(state) == frozenset({3, 4, 5, 6, 7})
    assert election_seats(state) == frozenset(state.alive_players()) | {3, 4, 5, 6, 7}


def test_unannounced_deaths_end_when_the_round_is_read_out() -> None:
    """死讯公布后（天亮及以后）就没有「未公布死亡」了；旧轮次也永远不是。"""
    state = _state(seats=9)
    state.round_number = 1
    state.phase = GamePhase.SHERIFF_ELECTION
    state.death_history = [DeathReport(3, "wolf_kill", 1)]
    state.players[3].is_alive = False

    assert unannounced_death_seats(state) == frozenset({3})
    assert public_alive_seats(state) == (1, 2, 3, 4, 5, 6, 7, 8, 9)
    assert public_dead_seats(state) == ()

    state.phase = GamePhase.DAWN
    assert unannounced_death_seats(state) == frozenset()
    assert public_alive_seats(state) == (1, 2, 4, 5, 6, 7, 8, 9)
    assert public_dead_seats(state) == (3,)

    state.round_number = 2
    state.phase = GamePhase.SHERIFF_ELECTION
    assert unannounced_death_seats(state) == frozenset()


def test_night_batch_deaths_skip_the_causes_announced_where_they_happen() -> None:
    state = _state(seats=9)
    state.round_number = 1
    state.death_history = [
        DeathReport(3, "wolf_kill", 1),
        DeathReport(4, "self_explode", 1),
        DeathReport(5, "poison", 1),
        DeathReport(6, "exile", 1),
        DeathReport(7, "knight_duel", 1),
    ]

    assert night_batch_deaths(state) == (3, 5)


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
    # 交/撕徽提示词曾经不带上文（当时还以为它是唯一一条，其实选发言方向那条同样
    # 没有）：预言家因此不知道自己刚验出的是金水还是查杀，也就无从用警徽去标记
    # 这次查验。现在两条都走 `_context_block`，并由
    # `test_every_sheriff_prompt_carries_identity_and_the_office_lists` 遍历把关。
    badge_human = director.badge_prompt(state, 2, (3, 4))[1]["content"]
    assert "你是2号，身份：" in badge_human
    assert "你的私有事实" in badge_human


def test_badge_reasoning_keeps_only_a_bounded_string() -> None:
    assert badge_reasoning({"reasoning": "  验出9号金水  "}) == "验出9号金水"
    assert badge_reasoning({"reasoning": "x" * 600}) == "x" * 500
    assert badge_reasoning({"reasoning": ""}) == ""
    assert badge_reasoning({"reasoning": "   "}) == ""
    assert badge_reasoning({"reasoning": 7}) == ""
    assert badge_reasoning({"action_type": "tear"}) == ""
    assert badge_reasoning("not an object") == ""
    assert badge_reasoning(None) == ""


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
        return '{"action_type":"transfer","target_seat":4,"reasoning":"9号是金水"}'

    director = SheriffDirector(invoke)
    state = _state()
    assert director.campaign_turn(state, 2, wolf=False) == "run"
    assert director.withdraw_turn(state, 2, wolf=False) == "stay"
    assert director.vote_turn(state, 4, (2, 3), wolf=False) == 2
    assert director.side_turn(state, 2, (SHERIFF_LEFT, SHERIFF_RIGHT)) == SHERIFF_RIGHT
    assert director.badge_turn(state, 2, (3, 4)) == ("transfer", 4, "9号是金水")
    assert calls[0] == "sheriff_campaign"

    empty = SheriffDirector()
    assert empty.campaign_turn(state, 2, wolf=False) == "pass"
    assert empty.withdraw_turn(state, 2, wolf=False) == "stay"
    assert empty.vote_turn(state, 4, (2, 3), wolf=False) is None
    assert empty.side_turn(state, 2, (SHERIFF_LEFT, SHERIFF_RIGHT)) == SHERIFF_LEFT
    assert empty.badge_turn(state, 2, (3, 4)) == ("tear", None, "")

    def bad_invoke(messages, tool_name, schema, seat):
        if tool_name == "sheriff_campaign":
            return {"action_type": "run"}
        if tool_name == "sheriff_withdraw":
            return '["withdraw"]'
        return 1

    broken = SheriffDirector(bad_invoke)
    assert broken.campaign_turn(state, 2, wolf=False) == "pass"
    assert broken.withdraw_turn(state, 2, wolf=False) == "stay"
    assert broken.vote_turn(state, 4, (2, 3), wolf=False) == SHERIFF_TECHNICAL_ABSTAIN

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
    assert wolves.badge_turn(state, 2, (3, 4)) == ("tear", None, "")


def test_a_vote_without_a_target_is_reasked_once_with_a_repair_hint() -> None:
    """vote 少 target_seat 不是主动弃票：必须换问法再问一次。

    最新一局 9/9 张警长票都是 ``action_type=vote`` 但落库没有
    ``target_seat``；旧的 ``vote_turn`` 把它与 ``pass`` 一起吞成 ``None``，
    观众流因此显示 9 个「弃权」，警徽直接流失。
    """
    prompts: list[str] = []

    def invoke(messages, tool_name, schema, seat):
        prompts.append(messages[1]["content"])
        if len(prompts) == 1:
            return '{"action_type":"vote","reasoning":"我投2号"}'
        return '{"action_type":"vote","target_seat":2,"reasoning":"我投2号"}'

    director = SheriffDirector(invoke)
    assert director.vote_turn(_state(), 4, (2, 3), wolf=False) == 2
    assert len(prompts) == 2
    assert "target_seat" in prompts[1]
    assert prompts[0] != prompts[1]


def test_a_vote_still_missing_its_target_after_the_retry_is_a_technical_abstain() -> None:
    prompts: list[str] = []

    def invoke(messages, tool_name, schema, seat):
        prompts.append(1)
        return '{"action_type":"vote","reasoning":"我投2号"}'

    director = SheriffDirector(invoke)
    assert (
        director.vote_turn(_state(), 4, (2, 3), wolf=False)
        == SHERIFF_TECHNICAL_ABSTAIN
    )
    assert len(prompts) == 2


def test_a_pass_ballot_is_not_retried() -> None:
    prompts: list[str] = []

    def invoke(messages, tool_name, schema, seat):
        prompts.append(1)
        return '{"action_type":"pass","reasoning":"不投任何人"}'

    director = SheriffDirector(invoke)
    assert director.vote_turn(_state(), 4, (2, 3), wolf=False) is None
    assert len(prompts) == 1


def test_a_retry_can_still_return_pass() -> None:
    prompts: list[str] = []

    def invoke(messages, tool_name, schema, seat):
        prompts.append(1)
        if len(prompts) == 1:
            return '{"action_type":"vote","reasoning":"我投2号"}'
        return '{"action_type":"pass","reasoning":"不投任何人"}'

    director = SheriffDirector(invoke)
    assert director.vote_turn(_state(), 4, (2, 3), wolf=False) is None
    assert len(prompts) == 2


def test_a_provider_failure_is_a_technical_abstain() -> None:
    def invoke(messages, tool_name, schema, seat):
        raise RuntimeError("timeout")

    director = SheriffDirector(invoke)
    assert (
        director.vote_turn(_state(), 4, (2, 3), wolf=False)
        == SHERIFF_TECHNICAL_ABSTAIN
    )


def test_the_technical_abstain_failure_code_is_stable() -> None:
    assert SHERIFF_VOTE_FAILURE_CODE == "sheriff_vote_unusable"


def test_the_vote_prompt_requires_a_target_for_vote_not_for_pass() -> None:
    human = SheriffDirector().vote_prompt(_state(), 4, (2, 3))[1]["content"]
    assert "action_type=vote 时" in human
    assert "必须" in human
    assert "target_seat" in human


def test_a_refused_transfer_target_keeps_the_reason_but_logs_the_degrade(caplog) -> None:
    """模型选了警徽不能去的座位时落账是撕徽：理由照发，但降级要在服务端留痕。"""

    def invoke(messages, tool_name, schema, seat):
        return '{"action_type":"transfer","target_seat":9,"reasoning":"把警徽交给9号"}'

    director = SheriffDirector(invoke)
    with caplog.at_level("WARNING"):
        assert director.badge_turn(_state(), 2, (3, 4)) == (
            "tear", None, "把警徽交给9号",
        )
    assert "sheriff badge transfer degraded seat=2 requested=9" in caplog.text


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


def test_a_hidden_wolf_is_not_told_it_can_swallow_the_badge() -> None:
    """The camp line and the explode line have different gates.

    A ``no_wolf_pack`` werewolf still knows it is a werewolf, but it sits outside
    the pack: only the pack may self-destruct to swallow the badge. The prompt
    used to append the explode sentence on camp alone, telling the Gargoyle it
    could do something its own role contract forbids.
    """
    from app.core.effect_applier import _Runtime
    from app.core.role_runtime import initialize_role_resources
    from app.core.sheriff_flow import SheriffDirector
    from app.roles.registry import builtin_registry

    state = _state()
    state.players[2] = PlayerState(2, "wolf-killer-gargoyle", Camp.WEREWOLF.value)
    initialize_role_resources(
        state, builtin_registry.freeze().specs, "a" * 64,
    )
    director = SheriffDirector()

    hidden = director.campaign_prompt(state, 2, wolf=True)[1]["content"]
    assert "不能自爆" in hidden
    assert "吞警徽" in hidden

    pack = director.campaign_prompt(state, 1, wolf=True)[1]["content"]
    assert "也可自爆吞警徽" in pack


def test_a_camp_only_wolf_without_live_state_keeps_the_pack_sentence() -> None:
    """No runtime to read the marker from: the historical line stands."""
    from app.core.sheriff_flow import SheriffDirector

    state = _state()
    state.players[2] = PlayerState(2, "wolf-killer-gargoyle", Camp.WEREWOLF.value)
    prompt = SheriffDirector().campaign_prompt(state, 2, wolf=True)[1]["content"]
    assert "也可自爆吞警徽" in prompt


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


def test_sheriff_context_block_lists_off_badge_voters() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    office = state.sheriff_office
    office.candidates = {1, 2, 3, 4, 5}
    office.active = {1, 2, 3, 4, 5}
    director = Director(conversation_log=_campaign_log())

    human = director.campaign_prompt(state, 6, wolf=True)[1]["content"]

    off_badge = next(
        line for line in human.splitlines() if line.startswith("未上警座位")
    )
    assert {6, 7, 8, 9, 10, 11, 12} <= {int(seat) for seat in re.findall(r"(\d+)号", off_badge)}
    assert "1号" not in re.findall(r"(\d+)号", off_badge)
    assert "已上警座位" in human

    state.death_history = [DeathReport(9, "wolf_kill", 1)]
    human = director.campaign_prompt(state, 6, wolf=True)[1]["content"]
    off_badge = next(
        line for line in human.splitlines() if line.startswith("未上警座位")
    )
    seats = {int(seat) for seat in re.findall(r"(\d+)号", off_badge)}
    # 首夜被刀的 9 号在死讯公布前仍是有投票权的警下玩家，名单必须留着它：
    # 竞选发生在公布死讯之前，把它写成「不在名单里」等于提前泄死讯。
    assert 9 in seats
    assert {6, 7, 8, 10, 11, 12} <= seats


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


def test_vote_turn_carries_the_team_block_into_the_ballot() -> None:
    """The director's own ballot must render the camp context it was handed.

    ``vote_prompt(wolf=True)`` renders the roster, but ``vote_turn`` used to call
    it without the flag, so the block never reached the model in a real game:
    captured ballots show 0/46 wolf ballots carrying the roster while campaign
    (75/76), withdraw (21/22), the day vote (73/83) and day speech (196/210) all
    did. Wolves then voted without knowing their own team.
    """
    humans: dict[int, str] = {}

    def invoke(messages, tool_name, schema, seat):
        humans[seat] = messages[1]["content"]
        return '{"action_type":"vote","target_seat":5}'

    state = _rich_state()
    director = SheriffDirector(invoke, conversation_log=_wolf_channel_log())
    assert director.vote_turn(state, 2, (5, 6), wolf=True) == 5
    assert director.vote_turn(state, 9, (5, 6), wolf=False) == 5

    assert "狼队成员" in humans[2]
    assert "2号、6号、10号、12号" in humans[2]
    assert "次日计划" in humans[2]
    assert "狼队成员" not in humans[9]
    assert "次日计划" not in humans[9]


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


def test_badge_prompt_shows_the_seer_own_last_check() -> None:
    """出局警长的交/撕徽提示词必须带上他刚验出的结果。

    这条门禁的由来：`badge_prompt` 从前是唯一不调 `_context_block` 的警长提示词。
    实战里预言家验出 9 号是金水、六秒后被要求决定警徽去留，提示词里却没有这行，
    于是他撕了徽；规则写着「预言家可以用交徽方向传递查验结果」，而全场按
    「撕徽＝查杀」把金水读成了狼。没有这个上下文，那条规则就落不了地。
    """
    from app.core.sheriff_flow import SheriffDirector as Director
    from app.core.effect_applier import (
        EffectApplier, EffectPermission, EffectKind, derive_effect_id,
    )
    from app.models.pipeline import GameEffect
    state = _rich_state()
    effects = (
        GameEffect(
            derive_effect_id("seer:3:check", 0), EffectKind.ACCEPT_ACTION,
            "seer:3:check", payload={}, visibility=("ACTOR",),
            expected_revision=0, sort_key=(0,),
        ),
        GameEffect(
            derive_effect_id("seer:3:check", 1), EffectKind.RECORD_PRIVATE_FACT,
            "seer:3:check",
            payload={"target": 5, "namespace": "private_checks",
                     "fact": {"target": 9, "camp": "good"}},
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

    human = Director().badge_prompt(state, 5, (1, 2, 3, 4, 6, 7, 8, 9))[1]["content"]
    assert "你是5号，身份：" in human
    assert "查验9号：好人" in human
    # 交/撕徽的目标清单与工具约定不能被这次注入挤掉
    assert "9号" in human and "撕毁" in human and "target_seat" in human


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


def test_sheriff_prompts_put_shared_speeches_before_identity_and_rosters_last() -> None:
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    office = state.sheriff_office
    office.candidates = {5, 6}
    office.active = {5, 6}
    director = Director(conversation_log=_campaign_log())
    seven = director.vote_prompt(state, 7, (5, 6))
    nine = director.vote_prompt(state, 9, (5, 6))

    assert seven[0] == nine[0]
    human = nine[1]["content"]
    order = [human.index(marker) for marker in (
        "警上已发言", "你是9号，身份", "你的私有事实", "已上警座位", "局势距离", "第1轮警长投票",
    )]
    assert order == sorted(order)
    shared = human[:human.index("你是9号，身份")]
    assert seven[1]["content"].startswith(shared)


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
    # The engine runs and campaigns in ascending seat order, so the rules must
    # tell agents that seat order, not intent, decides who speaks first.
    assert "ascending seat number" in text
    assert "counter-claim" in text


def test_public_game_rules_cover_hidden_role_mode() -> None:
    from app.agents.game_rules import PUBLIC_GAME_RULES
    text = " ".join(PUBLIC_GAME_RULES)
    assert "unrevealed" in text
    assert "non-reveals" in text


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


def test_sheriff_vote_schema_does_not_require_a_target_for_pass() -> None:
    """`pass` 不该被工具 schema 逼着给 `target_seat`。

    schema 曾把 `target_seat` 无条件塞进 `required`，而提示词明说可以
    `action_type=pass`；`_require_schema_fields` 于是把一条合法的弃票判成
    `response_missing_fields` —— 一批 7 张警长票里有 4 张就是这么死的。
    """
    captured: dict[str, object] = {}

    def invoke(messages, tool_name, schema, seat):
        captured.update(schema)
        return '{"action_type":"pass","reasoning":"不投任何人"}'

    director = SheriffDirector(invoke)
    assert director.vote_turn(_rich_state(), 9, (5, 6), wolf=False) is None

    assert "target_seat" in captured["properties"]
    assert "target_seat" not in captured["required"]


def test_a_schema_valid_pass_answer_survives_the_required_field_check() -> None:
    """把 schema 交给真正的字段检查：`pass` 少一个 `target_seat` 必须能过。

    这一格才是「4/7 张警长票死于 JSON 少必填字段」的根因所在。
    """
    from app.agents.llm_client import LLMClient, StructuredResponse
    from app.core.sheriff_flow import _schema

    schema = _schema(
        ["vote", "pass"],
        {"target_seat": {"type": ["integer", "null"], "minimum": 1}},
        optional=("target_seat",),
    )
    answer = StructuredResponse(
        payload={"action_type": "pass", "reasoning": "不投任何人"},
        transport="json", has_tool_calls=False,
    )

    assert LLMClient._require_schema_fields(answer, schema) is answer


def test_sheriff_badge_schema_does_not_require_a_target_for_tear() -> None:
    captured: dict[str, object] = {}

    def invoke(messages, tool_name, schema, seat):
        captured.update(schema)
        return '{"action_type":"tear","reasoning":"警徽不留人"}'

    director = SheriffDirector(invoke)

    assert director.badge_turn(_rich_state(), 2, (5, 6)) == ("tear", None, "警徽不留人")
    assert "target_seat" not in captured["required"]


def _candidate_line(human: str) -> str:
    return next(line for line in human.splitlines() if line.startswith("已上警座位"))


def test_the_run_prompt_lists_the_seats_that_already_ran() -> None:
    """上警循环里的「当前候选人」必须有真值。

    `office.active` 只在 run 循环结束（game_engine.py:1282）与退水循环结束
    （:1321）之后才写：上警阶段读它必然为空，于是每一张「已经有人上警」的提示词
    都写着「已上警座位：4号、5号；当前候选人：无」——同一行自相矛盾。实测近 200 局
    里 72 局命中，而测试 fixture 总是手写 `active`，所以一直没被发现。
    """
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    office = state.sheriff_office
    office.step = "run"
    office.candidates = {4, 5}
    office.active = set()

    human = Director().campaign_prompt(state, 6, wolf=False)[1]["content"]

    assert _candidate_line(human) == "已上警座位：4号、5号；当前候选人：4号、5号。"


def test_the_withdraw_stage_keeps_the_full_candidate_list() -> None:
    """退水阶段读的还是 active（此时它等于本轮全部上警座位），渲染不变。"""
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    office = state.sheriff_office
    office.step = "withdraw"
    office.candidates = {4, 5, 9}
    office.active = {4, 5, 9}

    human = Director().withdraw_prompt(state, 4, wolf=False)[1]["content"]

    assert _candidate_line(human) == "已上警座位：4号、5号、9号；当前候选人：4号、5号、9号。"


def test_the_side_prompt_carries_the_anchor_the_alive_list_and_both_orders() -> None:
    """警长选发言方向的提示词必须能支撑这个决定。

    从前它只有一句「你是警长9号。请选择今天的发言方向 side，只能是：…」：不知道
    昨晚谁死了（锚点）、不知道存活名单、也没人告诉他 left/right 对应座号方向
    —— 系统提示反而写着座位号不含空间含义，所以这个选择只能瞎猜。提示词现在给出
    死者、存活名单与两个选项各自的真实顺序，且顺序来自引擎同一套
    `speech_anchor` / `speech_order`。
    """
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    set_sheriff(state, 9)
    state.players[6].is_alive = False
    state.death_history = [DeathReport(6, "wolf_kill", 1)]

    human = Director().side_prompt(state, 9, (DEATH_LEFT, DEATH_RIGHT))[1]["content"]

    assert "你是9号，身份：" in human          # 身份/阵营
    assert "你的私有事实" in human              # 私有事实（查验、药）
    assert "未上警座位" in human                # 警上/警下
    assert "昨夜死亡：6号" in human             # 锚点
    assert "存活玩家：1号、2号、3号、4号、5号、7号、8号、9号、10号、11号、12号" in human
    # death_left = 死者下一号起升序 → 7 号先；death_right = 死者上一号起降序 → 5 号先。
    assert "death_left" in human and "7号先发言" in human
    assert "death_right" in human and "5号先发言" in human
    assert "从昨夜死者下一号起按座号升序" in human


def test_the_side_prompt_survives_an_empty_table() -> None:
    """边界：没有可选座位时预览退化成「无人可发言」，不抛异常。"""
    from app.core.sheriff_flow import SheriffDirector as Director
    state = _rich_state()
    set_sheriff(state, 9)
    for player in state.players.values():
        player.is_alive = False

    human = Director().side_prompt(state, 9, (DEATH_LEFT, DEATH_RIGHT))[1]["content"]

    assert "昨夜死亡：无人" in human
    assert human.count("无人可发言") == 2


def test_every_sheriff_prompt_carries_identity_and_the_office_lists() -> None:
    """所有警长提示词的最低上下文：身份/阵营、私有事实、警上/警下名单。

    `_context_block` 是每个提示词各自拼的，漏掉它不会有任何结构性报错：交/撕徽
    漏过一次（预言家看不见刚验出的金水，撕掉了本该用来传查验的警徽），发言方向
    又漏了一次（选择退化成抛硬币）。这条不变量遍历全部构造器，新加提示词漏拼
    上下文即红。
    """
    state = _rich_state()
    set_sheriff(state, 9)
    director = SheriffDirector()
    prompts = {
        "campaign": director.campaign_prompt(state, 2, wolf=False),
        "withdraw": director.withdraw_prompt(state, 2, wolf=False),
        "vote": director.vote_prompt(state, 4, (5, 9)),
        "badge": director.badge_prompt(state, 9, (1, 3)),
        "side": director.side_prompt(state, 9, (DEATH_LEFT, DEATH_RIGHT)),
    }

    for name, prompt in prompts.items():
        human = prompt[1]["content"]
        assert "身份：" in human, name
        assert "你的私有事实" in human, name
        assert "未上警座位" in human, name


def test_the_side_prompt_preview_matches_the_shared_order() -> None:
    """提示词里的顺序预览来自共享的 `day_speech_order`，不是第二份实现。"""
    from app.core.sheriff_flow import SheriffDirector as Director, day_speech_order
    state = _rich_state()
    set_sheriff(state, 9)
    state.players[6].is_alive = False
    state.death_history = [DeathReport(6, "wolf_kill", 1)]

    human = Director().side_prompt(state, 9, (DEATH_LEFT, DEATH_RIGHT))[1]["content"]

    for side in (DEATH_LEFT, DEATH_RIGHT):
        expected = day_speech_order(state, side)
        sequence = " → ".join(f"{seat}号" for seat in expected)
        assert f"{side}（" in human
        assert f"{expected[0]}号先发言；顺序 {sequence}。" in human


def test_an_unknown_role_is_a_plain_pack_wolf_for_the_badge_move() -> None:
    """No spec means no static self-explode ban; a missing seat never qualifies."""
    state = _state(seats=3)
    state.players[1] = PlayerState(1, "unknown-role", Camp.WEREWOLF.value)
    assert SheriffDirector._may_explode(state, 1) is True
    assert SheriffDirector._may_explode(state, 99) is False
