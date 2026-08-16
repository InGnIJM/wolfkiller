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

    def test_day_speech_first_speaker_gets_opening_framework_instruction(self):
        builder = PromptBuilder()
        state = make_state()
        state.speaking_order = [4, 5, 6, 8, 9]
        prompt = builder.build_speech_prompt(
            state, 4, "wolf-killer-villager", make_log(), "day_speech"
        )
        assert "第 1 位发言者" in prompt
        assert "开场分析框架" in prompt
        assert "具体观点" not in prompt

    def test_day_speech_later_speaker_must_react_and_add_new_points(self):
        builder = PromptBuilder()
        state = make_state()
        state.speaking_order = [4, 5, 6, 8, 9]
        prompt = builder.build_speech_prompt(
            state, 6, "wolf-killer-villager", make_log(), "day_speech"
        )
        assert "已有 2 位玩家发过言" in prompt
        assert "具体观点" in prompt
        assert "新的论点" in prompt
        assert "复述" in prompt

    def test_day_speech_without_speaking_order_still_asks_for_own_analysis(self):
        builder = PromptBuilder()
        state = make_state()
        state.speaking_order = []
        prompt = builder.build_speech_prompt(
            state, 4, "wolf-killer-villager", make_log(), "day_speech"
        )
        assert "个人分析" in prompt

    def test_system_prompt_forbids_echoing_previous_speakers(self):
        prompt = PromptBuilder.get_system_prompt()
        assert "雷同" in prompt or "复述" in prompt

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


class TestPromptBuilderHelpers:
    def test_speech_tools_and_system_prompt_are_static(self):
        assert len(PromptBuilder.get_speech_tools()) == 2
        assert PromptBuilder.get_system_prompt()

    def test_identity_block_falls_back_for_unknown_role(self):
        builder = PromptBuilder()
        block = builder._identity_block(1, {
            "facts": {"actor_identity": {"role_id": "unknown-role", "camp_id": "good"}},
        })
        assert "unknown-role" in block and "good" in block

    def test_speaking_progress_for_outsider_and_empty_order(self):
        state = make_state()
        state.speaking_order = [1, 2, 3]
        outsider = PromptBuilder._format_speaking_progress(state, 9)
        assert "发言顺序：1号 → 2号 → 3号" in outsider
        assert "当前发言者" not in outsider

        state.speaking_order = []
        assert PromptBuilder._format_speaking_progress(state, 1) == "（当前不是发言阶段）"

    def test_private_facts_block_minimal_and_with_facts(self):
        assert PromptBuilder._private_facts_block({"facts": {"actor_identity": {}}}) == "- 无额外私有事实。"
        block = PromptBuilder._private_facts_block({
            "resources": {"gun": 1},
            "facts": {"actor_identity": {}, "alive_seats": [1, 2], "dead_seats": []},
        })
        assert "可用资源：gun=1" in block
        assert "alive_seats" in block
        assert "dead_seats" not in block  # empty sequences are skipped

    def test_conversations_render_scopes_and_empty_state(self):
        from app.models.conversation import Conversation, ConversationScope
        log = ConversationLog()
        assert PromptBuilder()._format_conversations(log, 1, 1, "wolf-killer-werewolf") == "（尚无对话记录）"
        log.records.append(Conversation(
            ConversationScope.WEREWOLF, "夜间交流", 1, speaker_seat=2, speaker_role="wolf-killer-werewolf",
        ))
        log.records.append(Conversation(
            ConversationScope.PUBLIC, "系统提示", 1, phase="system",
        ))
        out = PromptBuilder()._format_conversations(log, 1, 1, "wolf-killer-werewolf")
        assert "[狼队频道]" in out and "[系统]" in out and "【本轮】" in out

    def test_thoughts_render_and_empty_state(self):
        log = ConversationLog()
        assert PromptBuilder._format_thoughts(log, 1, 1) == "（尚无思考记录）"
        log.add_thought(1, "wolf-killer-werewolf", "思考内容", 1, "night")
        out = PromptBuilder._format_thoughts(log, 1, 1)
        assert "思考内容" in out and "第1轮" in out

    def test_task_instruction_default_context(self):
        assert PromptBuilder._task_instruction("unknown_context", make_state()) == "请根据你的身份和当前局势做出合理决策。"
