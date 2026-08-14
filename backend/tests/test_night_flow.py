from __future__ import annotations

import pytest

from app.core.effect_applier import _Runtime
from app.core.night_flow import (
    DiscussionTurn,
    NightDirector,
    ThinkResult,
    WolfVote,
    _clean,
)
from app.models.game import GameConfig, GameState, PlayerState
from app.models.pipeline import ActionCommand
from app.roles.registry import RegistrySnapshot, builtin_registry


def _state() -> GameState:
    state = GameState(game_id="g", config=GameConfig())
    roles = [
        "wolf-killer-werewolf",
        "wolf-killer-witch",
        "wolf-killer-seer",
        "wolf-killer-villager",
        "wolf-killer-villager",
    ]
    for seat, role in enumerate(roles, start=1):
        state.players[seat] = PlayerState(
            seat_number=seat,
            role=role,
            camp="werewolf" if role.endswith("werewolf") else "good",
        )
    state.round_number = 1
    return state


def _snapshot() -> RegistrySnapshot:
    return RegistrySnapshot(specs={}, digest="test")


def _director(invoke) -> NightDirector:
    return NightDirector(_snapshot(), invoke)


@pytest.fixture
def state() -> GameState:
    return _state()


@pytest.fixture
def director() -> NightDirector:
    return _director(lambda _messages: "{}")


# ── _clean ──────────────────────────────────────────────────


def test_clean_valid():
    assert _clean("hello", "text", 200) == "hello"
    assert _clean("x" * 200, "text", 200) == "x" * 200


def test_clean_non_string():
    with pytest.raises(ValueError):
        _clean(123, "text", 200)


def test_clean_non_utf8():
    with pytest.raises(ValueError):
        _clean(chr(0xD800), "text", 200)


def test_clean_empty():
    with pytest.raises(ValueError):
        _clean("", "text", 200)


def test_clean_too_long():
    with pytest.raises(ValueError):
        _clean("x" * 201, "text", 200)


# ── DiscussionTurn ──────────────────────────────────────────


def test_discussion_turn_spoke():
    turn = DiscussionTurn(1, True, "今晚刀预言家")
    assert turn.seat == 1
    assert turn.spoke is True
    assert turn.text == "今晚刀预言家"


def test_discussion_turn_spoke_boundary_200():
    turn = DiscussionTurn(1, True, "t" * 200)
    assert turn.text == "t" * 200


def test_discussion_turn_skip():
    turn = DiscussionTurn(2, False)
    assert turn.spoke is False
    assert turn.text == ""


def test_discussion_turn_bad_seat():
    with pytest.raises(ValueError):
        DiscussionTurn(0, False)
    with pytest.raises(ValueError):
        DiscussionTurn(-1, False)
    with pytest.raises(ValueError):
        DiscussionTurn("1", False)  # type: ignore[arg-type]


def test_discussion_turn_spoke_not_bool():
    with pytest.raises(TypeError):
        DiscussionTurn(1, "yes")  # type: ignore[arg-type]


def test_discussion_turn_skip_with_text():
    with pytest.raises(ValueError):
        DiscussionTurn(1, False, "不该有发言")


def test_discussion_turn_spoke_empty_text():
    with pytest.raises(ValueError):
        DiscussionTurn(1, True, "")


def test_discussion_turn_spoke_too_long():
    with pytest.raises(ValueError):
        DiscussionTurn(1, True, "t" * 201)


def test_discussion_turn_spoke_non_string_text():
    with pytest.raises(ValueError):
        DiscussionTurn(1, True, 123)  # type: ignore[arg-type]


# ── WolfVote ────────────────────────────────────────────────


def test_wolf_vote_kill():
    vote = WolfVote(1, "kill", 2, "他很可疑")
    assert vote.seat == 1
    assert vote.action_type == "kill"
    assert vote.target_seat == 2


def test_wolf_vote_pass():
    vote = WolfVote(1, "pass", None, "先观察")
    assert vote.action_type == "pass"
    assert vote.target_seat is None


def test_wolf_vote_bad_seat():
    with pytest.raises(ValueError):
        WolfVote(0, "pass", None, "理由")
    with pytest.raises(ValueError):
        WolfVote(-2, "pass", None, "理由")


def test_wolf_vote_bad_action_type():
    with pytest.raises(ValueError):
        WolfVote(1, "check", None, "理由")


def test_wolf_vote_kill_missing_target():
    with pytest.raises(ValueError):
        WolfVote(1, "kill", None, "理由")  # type: ignore[arg-type]


def test_wolf_vote_kill_bad_target():
    with pytest.raises(ValueError):
        WolfVote(1, "kill", 0, "理由")
    with pytest.raises(ValueError):
        WolfVote(1, "kill", -3, "理由")
    with pytest.raises(ValueError):
        WolfVote(1, "kill", "2", "理由")  # type: ignore[arg-type]


def test_wolf_vote_pass_with_target():
    with pytest.raises(ValueError):
        WolfVote(1, "pass", 2, "理由")  # type: ignore[arg-type]


def test_wolf_vote_reasoning_boundary_500():
    vote = WolfVote(1, "pass", None, "r" * 500)
    assert vote.reasoning == "r" * 500


def test_wolf_vote_reasoning_too_long():
    with pytest.raises(ValueError):
        WolfVote(1, "pass", None, "r" * 501)


def test_wolf_vote_reasoning_empty():
    with pytest.raises(ValueError):
        WolfVote(1, "pass", None, "")


def test_wolf_vote_reasoning_non_string():
    with pytest.raises(ValueError):
        WolfVote(1, "pass", None, 123)  # type: ignore[arg-type]


def test_wolf_vote_to_command_kill():
    command = WolfVote(1, "kill", 2, "理由").to_command()
    assert isinstance(command, ActionCommand)
    assert command.action_type == "kill"
    assert command.target_seat == 2
    assert command.reasoning == "理由"
    assert command.schema_version == 1


def test_wolf_vote_to_command_pass():
    command = WolfVote(1, "pass", None, "理由").to_command()
    assert command.action_type == "pass"
    assert command.target_seat is None
    assert command.reasoning == "理由"


# ── ThinkResult ─────────────────────────────────────────────


def test_think_result_valid():
    result = ThinkResult(2, "今晚救谁呢")
    assert result.seat == 2
    assert result.text == "今晚救谁呢"


def test_think_result_bad_seat():
    with pytest.raises(ValueError):
        ThinkResult(0, "思考")
    with pytest.raises(ValueError):
        ThinkResult(-1, "思考")


def test_think_result_too_long():
    with pytest.raises(ValueError):
        ThinkResult(2, "t" * 201)


def test_think_result_non_string():
    with pytest.raises(ValueError):
        ThinkResult(2, 123)  # type: ignore[arg-type]


# ── NightDirector.__init__ ──────────────────────────────────


def test_init_bad_snapshot():
    with pytest.raises(TypeError):
        NightDirector(object(), lambda _messages: "{}")  # type: ignore[arg-type]


def test_init_bad_invoke():
    with pytest.raises(TypeError):
        NightDirector(_snapshot(), "not callable")  # type: ignore[arg-type]


# ── private prompt helpers ──────────────────────────────────


def test_history_text_empty():
    director = _director(lambda _messages: "{}")
    assert director._history_text([]) == ""


def test_history_text_non_empty():
    director = _director(lambda _messages: "{}")
    text = director._history_text(["第一条", "第二条"])
    assert text == "1. 第一条\n2. 第二条"


def test_prior_votes_text_empty():
    director = _director(lambda _messages: "{}")
    assert director._prior_votes_text([]) == "（还没有人出票）"


def test_prior_votes_text_kill_and_pass():
    director = _director(lambda _messages: "{}")
    votes = [
        WolfVote(1, "kill", 2, "可疑"),
        WolfVote(3, "pass", None, "先看"),
    ]
    text = director._prior_votes_text(votes)
    assert "刀 2 号" in text
    assert "弃权" in text
    assert "可疑" in text
    assert "先看" in text


def test_alive_text_sorted():
    director = _director(lambda _messages: "{}")
    state = _state()
    assert director._alive_text(state) == "1号、2号、3号、4号、5号"


def test_wolf_team():
    director = _director(lambda _messages: "{}")
    state = _state()
    assert director._wolf_team(state) == [1]


# ── prompt builders ─────────────────────────────────────────


def test_discussion_prompt(state: GameState, director: NightDirector):
    messages = director.discussion_prompt(state, 1, ["狼1：刀3号"])
    assert messages[0]["role"] == "system"
    assert "简体中文" in messages[0]["content"]
    assert "seat 1" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "第1晚" in messages[1]["content"]
    assert "1号" in messages[1]["content"]
    assert "5号" in messages[1]["content"]
    assert "狼1：刀3号" in messages[1]["content"]


def test_discussion_prompt_empty_history(state: GameState, director: NightDirector):
    messages = director.discussion_prompt(state, 1, [])
    assert "（尚无发言）" in messages[1]["content"]


def test_vote_prompt(state: GameState, director: NightDirector):
    prior = [WolfVote(1, "kill", 2, "可疑")]
    messages = director.vote_prompt(state, 1, ["狼1：刀3号"], prior)
    assert messages[0]["role"] == "system"
    assert "简体中文" in messages[0]["content"]
    assert "第1晚" in messages[1]["content"]
    assert "刀 2 号" in messages[1]["content"]


def test_vote_prompt_empty(state: GameState, director: NightDirector):
    messages = director.vote_prompt(state, 1, [], [])
    assert "（无）" in messages[1]["content"]
    assert "（还没有人出票）" in messages[1]["content"]


def test_witch_think_prompt_with_target(state: GameState, director: NightDirector):
    messages = director.witch_think_prompt(state, 2, 3)
    assert "简体中文" in messages[0]["content"]
    assert "Witch" in messages[0]["content"]
    assert "昨夜狼人袭击了 3 号" in messages[1]["content"]


def test_witch_think_prompt_no_target(state: GameState, director: NightDirector):
    messages = director.witch_think_prompt(state, 2, None)
    assert "昨夜没有袭击发生" in messages[1]["content"]


def test_witch_think_prompt_reports_remaining_potions_from_runtime(state: GameState, director: NightDirector):
    state._pipeline_runtime = _Runtime(role_resources={2: {"antidote": 0, "poison": 1}})
    messages = director.witch_think_prompt(state, 2, 3)
    assert "昨夜狼人袭击了 3 号" in messages[1]["content"]
    assert "解药 0 瓶" in messages[1]["content"]
    assert "毒药 1 瓶" in messages[1]["content"]


def test_witch_think_prompt_defaults_to_initial_potions_without_runtime(state: GameState):
    director = NightDirector(builtin_registry.freeze(), lambda _messages: "{}")
    messages = director.witch_think_prompt(state, 2, None)
    assert "解药 1 瓶" in messages[1]["content"]
    assert "毒药 1 瓶" in messages[1]["content"]


def test_witch_think_prompt_falls_back_to_spec_when_runtime_lacks_seat(state: GameState):
    state._pipeline_runtime = _Runtime(role_resources={})
    director = NightDirector(builtin_registry.freeze(), lambda _messages: "{}")
    messages = director.witch_think_prompt(state, 2, None)
    assert "解药 1 瓶" in messages[1]["content"]
    assert "毒药 1 瓶" in messages[1]["content"]


def test_witch_think_prompt_zero_potions_without_runtime_and_spec(state: GameState, director: NightDirector):
    messages = director.witch_think_prompt(state, 2, None)
    assert "解药 0 瓶" in messages[1]["content"]
    assert "毒药 0 瓶" in messages[1]["content"]


def test_seer_think_prompt(state: GameState, director: NightDirector):
    messages = director.seer_think_prompt(state, 3)
    assert "简体中文" in messages[0]["content"]
    assert "Seer" in messages[0]["content"]
    assert "第1晚" in messages[1]["content"]
    assert "3号" in messages[1]["content"]


# ── wolf_discussion_turn ────────────────────────────────────


def test_wolf_discussion_turn_speak(state: GameState):
    director = _director(lambda _messages: '{"speak": true, "text": "刀预言家"}')
    turn = director.wolf_discussion_turn(state, 1, [])
    assert turn == DiscussionTurn(1, True, "刀预言家")


def test_wolf_discussion_turn_skip_via_false(state: GameState):
    director = _director(lambda _messages: '{"speak": false}')
    turn = director.wolf_discussion_turn(state, 1, [])
    assert turn == DiscussionTurn(1, False)


def test_wolf_discussion_turn_skip_via_missing_speak(state: GameState):
    director = _director(lambda _messages: '{"text": "x"}')
    turn = director.wolf_discussion_turn(state, 1, [])
    assert turn == DiscussionTurn(1, False)


def test_wolf_discussion_turn_fallback_invoke_raises(state: GameState):
    def invoke(_messages):
        raise RuntimeError("boom")

    director = _director(invoke)
    assert director.wolf_discussion_turn(state, 1, []) == DiscussionTurn(1, False)


def test_wolf_discussion_turn_fallback_non_str(state: GameState):
    director = _director(lambda _messages: 123)  # type: ignore[arg-type,return-value]
    assert director.wolf_discussion_turn(state, 1, []) == DiscussionTurn(1, False)


def test_wolf_discussion_turn_fallback_invalid_json(state: GameState):
    director = _director(lambda _messages: "not json")
    assert director.wolf_discussion_turn(state, 1, []) == DiscussionTurn(1, False)


def test_wolf_discussion_turn_fallback_list(state: GameState):
    director = _director(lambda _messages: "[]")
    assert director.wolf_discussion_turn(state, 1, []) == DiscussionTurn(1, False)


def test_wolf_discussion_turn_fallback_bad_text(state: GameState):
    director = _director(lambda _messages: '{"speak": true, "text": 123}')
    assert director.wolf_discussion_turn(state, 1, []) == DiscussionTurn(1, False)


def test_wolf_discussion_turn_fallback_too_long_text(state: GameState):
    director = _director(
        lambda _messages: '{"speak": true, "text": "' + "x" * 201 + '"}'
    )
    assert director.wolf_discussion_turn(state, 1, []) == DiscussionTurn(1, False)


# ── wolf_vote_turn ──────────────────────────────────────────


def test_wolf_vote_turn_kill(state: GameState):
    director = _director(
        lambda _messages: '{"action_type": "kill", "target_seat": 2, "reasoning": "可疑"}'
    )
    vote = director.wolf_vote_turn(state, 1, [], [])
    assert vote == WolfVote(1, "kill", 2, "可疑")


def test_wolf_vote_turn_pass(state: GameState):
    director = _director(
        lambda _messages: '{"action_type": "pass", "target_seat": null, "reasoning": "先看"}'
    )
    vote = director.wolf_vote_turn(state, 1, [], [])
    assert vote == WolfVote(1, "pass", None, "先看")


def test_wolf_vote_turn_fallback_invoke_raises(state: GameState):
    def invoke(_messages):
        raise RuntimeError("boom")

    director = _director(invoke)
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


def test_wolf_vote_turn_fallback_non_str(state: GameState):
    director = _director(lambda _messages: 123)  # type: ignore[arg-type,return-value]
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


def test_wolf_vote_turn_fallback_invalid_json(state: GameState):
    director = _director(lambda _messages: "not json")
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


def test_wolf_vote_turn_fallback_list(state: GameState):
    director = _director(lambda _messages: "[]")
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


def test_wolf_vote_turn_kill_missing_target(state: GameState):
    director = _director(
        lambda _messages: '{"action_type": "kill", "reasoning": "可疑"}'
    )
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


def test_wolf_vote_turn_kill_non_int_target(state: GameState):
    director = _director(
        lambda _messages: '{"action_type": "kill", "target_seat": "2", "reasoning": "可疑"}'
    )
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


def test_wolf_vote_turn_kill_out_of_seats_target(state: GameState):
    director = _director(
        lambda _messages: '{"action_type": "kill", "target_seat": 99, "reasoning": "可疑"}'
    )
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


def test_wolf_vote_turn_kill_missing_reasoning(state: GameState):
    director = _director(
        lambda _messages: '{"action_type": "kill", "target_seat": 2}'
    )
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


def test_wolf_vote_turn_pass_missing_reasoning(state: GameState):
    director = _director(lambda _messages: '{"action_type": "pass", "target_seat": null}')
    assert director.wolf_vote_turn(state, 1, [], []) == WolfVote(
        1, "pass", None, "safe fallback"
    )


# ── witch_think / seer_think ────────────────────────────────


def test_witch_think_happy_with_target(state: GameState):
    director = _director(lambda _messages: '{"text": "救3号"}')
    result = director.witch_think(state, 2, 3)
    assert result == ThinkResult(2, "救3号")


def test_witch_think_happy_no_target(state: GameState):
    director = _director(lambda _messages: '{"text": "平安夜"}')
    result = director.witch_think(state, 2, None)
    assert result == ThinkResult(2, "平安夜")


def test_witch_think_fallback(state: GameState):
    def invoke(_messages):
        raise RuntimeError("boom")

    director = _director(invoke)
    assert director.witch_think(state, 2, 3) is None


def test_witch_think_fallback_bad_text(state: GameState):
    director = _director(lambda _messages: '{"text": 123}')
    assert director.witch_think(state, 2, 3) is None


def test_seer_think_happy(state: GameState):
    director = _director(lambda _messages: '{"text": "查2号"}')
    result = director.seer_think(state, 3)
    assert result == ThinkResult(3, "查2号")


def test_seer_think_fallback(state: GameState):
    def invoke(_messages):
        raise RuntimeError("boom")

    director = _director(invoke)
    assert director.seer_think(state, 3) is None


# ── narration / dawn_narration ──────────────────────────────


def test_narration_known():
    assert NightDirector.narration("wolf_open") == ("天黑请闭眼", "狼人请睁眼，开始讨论今晚的行动。")
    assert NightDirector.narration("witch_open") == ("女巫请睁眼", "昨晚有人被袭击。")
    assert NightDirector.narration("seer_open") == ("预言家请睁眼", "请查验一名玩家的身份。")


def test_narration_unknown():
    with pytest.raises(ValueError):
        NightDirector.narration("bogus")


def test_dawn_narration_empty():
    assert NightDirector.dawn_narration([]) == ("天亮了", "昨晚是平安夜，没有人死亡。")


def test_dawn_narration_non_empty():
    assert NightDirector.dawn_narration([3, 1]) == ("天亮了", "昨晚 1号、3号 玩家死亡。")


# ── wolf vote hand-off ──────────────────────────────────────


def test_record_and_collect(state: GameState):
    director = _director(lambda _messages: "{}")
    votes = [WolfVote(1, "kill", 2, "可疑"), WolfVote(3, "pass", None, "先看")]
    director.record_votes(votes)
    command = director.collected_vote(1)
    assert command is not None
    assert command.action_type == "kill"
    assert command.target_seat == 2
    assert command.reasoning == "可疑"
    assert director.collected_vote(3).action_type == "pass"  # type: ignore[union-attr]


def test_collect_unknown_seat(state: GameState):
    director = _director(lambda _messages: "{}")
    director.record_votes([WolfVote(1, "kill", 2, "可疑")])
    assert director.collected_vote(9) is None


def test_record_overwrites(state: GameState):
    director = _director(lambda _messages: "{}")
    director.record_votes([WolfVote(1, "kill", 2, "第一票")])
    director.record_votes([WolfVote(1, "pass", None, "改票")])
    command = director.collected_vote(1)
    assert command is not None
    assert command.action_type == "pass"
    assert command.target_seat is None
    assert command.reasoning == "改票"
