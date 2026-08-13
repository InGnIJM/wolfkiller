import json
import re
from pathlib import Path

import pytest

from app.agents.prompt_builder import PromptBuilder
from app.core.conversation_log import ConversationLog
from app.core.game_engine import VOTE_CONTRACT
from app.models.game import GameState, GameConfig, GamePhase, PlayerState
from app.models.actions import SpeechRecord, VoteAction
from app.roles.registry import builtin_registry


def make_state(role_assignments: dict[int, str] = None) -> GameState:
    state = GameState(game_id="test", config=GameConfig(
        num_werewolves=3, num_villagers=3, num_seers=1, num_witches=1, num_hunters=1
    ), round_number=1)
    defaults = {
        1: "wolf-killer-werewolf", 2: "wolf-killer-werewolf", 3: "wolf-killer-werewolf",
        4: "wolf-killer-villager", 5: "wolf-killer-villager", 6: "wolf-killer-villager",
        7: "wolf-killer-seer", 8: "wolf-killer-witch", 9: "wolf-killer-hunter",
    }
    assignments = role_assignments or defaults
    specs = builtin_registry.freeze().specs
    for seat, role in assignments.items():
        state.players[seat] = PlayerState(
            seat_number=seat, role=role, camp=specs[role].camp_id
        )
    return state


def make_log() -> ConversationLog:
    log = ConversationLog()
    log.add_public_speech(4, "wolf-killer-villager", "我觉得3号有点可疑。", 1, "speech")
    log.add_thought(1, "wolf-killer-werewolf", "今晚先观察一下。", 1, "night")
    return log


def test_legacy_sources_have_no_builtin_role_names() -> None:
    sources = (
        Path("app/agents/prompt_builder.py").read_text("utf-8")
        + Path("app/agents/state_filter.py").read_text("utf-8")
    )
    assert not re.search(
        r"witch|hunter|werewolf|seer|has_antidote|has_gun", sources
    )


def test_prompt_builder_delegates_action_rendering() -> None:
    class RendererStub:
        def __init__(self): self.calls = []
        def render(self, spec, contract, context, history):
            self.calls.append((spec, contract, context, history))
            return "rendered-action-prompt"

    builder = PromptBuilder()
    renderer = RendererStub()
    builder.renderer = renderer
    result = builder.build_action_prompt("spec", "contract", "context", history="x")
    assert result == "rendered-action-prompt"
    assert renderer.calls == [("spec", "contract", "context", "x")]


class TestPromptBuilder:
    def test_board_renders_display_names_from_registry(self):
        builder = PromptBuilder()
        state = make_state({
            1: "wolf-killer-werewolf",
            2: "wolf-killer-villager",
            3: "wolf-killer-villager",
            4: "wolf-killer-seer",
        })
        state.config = GameConfig(role_counts={
            "wolf-killer-werewolf": 1,
            "wolf-killer-villager": 2,
            "wolf-killer-seer": 1,
        })

        prompt = builder.build_speech_prompt(
            state, 2, "wolf-killer-villager", make_log(), "day_speech"
        )

        assert "公开板子：4人" in prompt
        assert "Werewolf：1人" in prompt
        assert "Villager：2人" in prompt
        assert "Seer：1人" in prompt

    def test_system_prompt_has_no_fixed_nine_player_board_or_long_thinking_order(self):
        prompt = PromptBuilder.get_system_prompt()

        assert "9人标准场" not in prompt
        assert "3名狼人" not in prompt
        assert "深度思考" not in prompt

    def test_speech_prompt_contains_identity_public_state_and_history(self):
        builder = PromptBuilder()
        state = make_state()
        state.speaking_order = [1, 2, 3, 4]
        state.current_speaker = 4
        state.speeches = [SpeechRecord(player_seat=4, text="大家好", round_number=1)]
        prompt = builder.build_speech_prompt(
            state, 4, "wolf-killer-villager", make_log(), "day_speech"
        )

        assert "你的身份：4号玩家，Villager" in prompt
        assert "你的阵营：good" in prompt
        assert "当前发言者：4号" in prompt
        assert "你的任务：白天发言" in prompt
        assert "你的历史思考回顾" in prompt
        assert "本轮对话记录" in prompt
        assert "我觉得3号有点可疑" in prompt
        assert "你的私有事实" in prompt

    def test_speech_prompt_contains_camp_members_only_for_camp_roles(self):
        builder = PromptBuilder()
        state = make_state()
        wolf_prompt = builder.build_speech_prompt(
            state, 1, "wolf-killer-werewolf", make_log(), "day_speech"
        )
        villager_prompt = builder.build_speech_prompt(
            state, 4, "wolf-killer-villager", make_log(), "day_speech"
        )
        assert "camp_members" in wolf_prompt
        assert "camp_members" not in villager_prompt

    def test_last_words_prompt_uses_last_words_task(self):
        builder = PromptBuilder()
        state = make_state()
        prompt = builder.build_speech_prompt(
            state, 9, "wolf-killer-hunter", make_log(), "last_words"
        )
        assert "你的任务：遗言" in prompt

    def test_exile_vote_prompt_renders_vote_task_with_valid_example(self):
        builder = PromptBuilder()
        state = make_state()
        prompt = builder.build_vote_prompt(
            state, 4, "wolf-killer-villager", make_log(), "exile_vote"
        )
        assert "你的任务：放逐投票" in prompt
        example = json.loads(re.search(r"JSON字段：(\{.*?\})。", prompt, re.S).group(1))
        assert example == {"action_type": "abstain", "target_seat": None, "reasoning": "基于当前可见事实作出选择"}

    def test_tiebreak_vote_prompt_mentions_candidates_and_resume(self):
        builder = PromptBuilder()
        state = make_state()
        state.vote_round = 2
        state.is_tiebreak = True
        state.tiebreak_candidates = {1, 2}
        state.supplemental_speakers = {1, 3}
        prompt = builder.build_vote_prompt(
            state, 4, "wolf-killer-villager", make_log(), "exile_vote"
        )
        assert "平票复投（第2轮）" in prompt

    @pytest.mark.parametrize("role_name", [
        "wolf-killer-werewolf", "wolf-killer-villager", "wolf-killer-seer",
        "wolf-killer-witch", "wolf-killer-hunter",
    ])
    def test_speech_prompt_works_for_every_builtin_role(self, role_name):
        builder = PromptBuilder()
        state = make_state()
        seat = next(seat for seat, role in {
            1: "wolf-killer-werewolf", 4: "wolf-killer-villager", 7: "wolf-killer-seer",
            8: "wolf-killer-witch", 9: "wolf-killer-hunter",
        }.items() if role == role_name)
        prompt = builder.build_speech_prompt(state, seat, role_name, make_log(), "day_speech")
        assert prompt
        assert f"{seat}号玩家" in prompt

    def test_vote_contract_is_still_used_by_engine_transport(self):
        assert VOTE_CONTRACT.contract_id == "exile_vote"
        assert VOTE_CONTRACT.fallback_action_type == "abstain"
