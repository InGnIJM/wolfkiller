import pytest
from pathlib import Path
from app.agents.prompt_builder import PromptBuilder
from app.core.conversation_log import ConversationLog
from app.models.game import GameState, GameConfig, PlayerState
from app.models.actions import SpeechRecord, DeathReport, VoteAction


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
    for seat, role in assignments.items():
        camp = "werewolf" if "werewolf" in role else "good"
        p = PlayerState(seat_number=seat, role=role, camp=camp)
        if "witch" in role:
            p.has_antidote = True
            p.has_poison = True
        if "hunter" in role:
            p.has_gun = True
        state.players[seat] = p
    return state


def make_log() -> ConversationLog:
    return ConversationLog()


class TestPromptBuilder:
    def test_build_speech_prompt_werewolf(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 1, "wolf-killer-werewolf", log, "day_speech")
        assert "1号玩家" in prompt
        assert "狼人" in prompt
        assert "狼人阵营" in prompt
        assert "狼队友" in prompt

    def test_build_speech_prompt_villager(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 4, "wolf-killer-villager", log, "day_speech")
        assert "平民" in prompt
        assert "好人阵营" in prompt

    def test_build_speech_prompt_seer(self):
        builder = PromptBuilder()
        state = make_state()
        state.players[7].check_results = [{"target_seat": 2, "result": "werewolf", "round": 1}]
        log = make_log()

        prompt = builder.build_speech_prompt(state, 7, "wolf-killer-seer", log, "day_speech")
        assert "预言家" in prompt
        assert "查验" in prompt

    def test_build_speech_prompt_witch(self):
        builder = PromptBuilder()
        state = make_state()
        state.last_wolf_kill_target = 4
        log = make_log()

        prompt = builder.build_speech_prompt(state, 8, "wolf-killer-witch", log, "day_speech")
        assert "女巫" in prompt
        assert "解药" in prompt
        assert "毒药" in prompt

    def test_build_speech_prompt_hunter(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 9, "wolf-killer-hunter", log, "day_speech")
        assert "猎人" in prompt
        assert "猎枪" in prompt

    def test_build_action_prompt(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 1, "wolf-killer-werewolf", log, "night_kill")
        assert "JSON格式" in prompt

    def test_night_kill_task(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 1, "wolf-killer-werewolf", log, "night_kill")
        assert ("击杀" in prompt or "杀" in prompt or "kill" in prompt.lower())

    def test_night_check_task(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 7, "wolf-killer-seer", log, "night_check")
        assert "查验" in prompt or "check" in prompt.lower()

    def test_witch_save_task(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 8, "wolf-killer-witch", log, "witch_save", wolf_target=3)
        assert "save" in prompt.lower() or "救人" in prompt or "解药" in prompt

    def test_witch_poison_task(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 8, "wolf-killer-witch", log, "witch_poison", wolf_target=3)
        assert "poison" in prompt.lower() or "毒药" in prompt or "毒" in prompt

    def test_day_speech_task(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 4, "wolf-killer-villager", log, "day_speech")
        assert "发言" in prompt

    def test_last_words_task(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 4, "wolf-killer-villager", log, "last_words")
        assert "遗言" in prompt

    def test_exile_vote_task(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_vote_prompt(state, 4, "wolf-killer-villager", log, "exile_vote")
        assert "放逐" in prompt or "投票" in prompt or "exile" in prompt.lower()

    def test_includes_conversations(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        log.add_public_speech(3, "wolf-killer-werewolf", "我觉得1号有问题", 1, "speech")

        prompt = builder.build_speech_prompt(state, 4, "wolf-killer-villager", log, "day_speech")
        assert "1号有问题" in prompt

    def test_includes_dead_players(self):
        builder = PromptBuilder()
        state = make_state()
        state.players[5].is_alive = False
        state.death_history = [DeathReport(player_seat=5, cause="wolf_kill", round_number=1)]
        log = make_log()

        prompt = builder.build_action_prompt(state, 1, "wolf-killer-werewolf", log, "night_kill")
        assert "5号" in prompt

    def test_seer_with_no_checks(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 7, "wolf-killer-seer", log, "day_speech")
        assert "尚未查验" in prompt

    def test_witch_without_antidote(self):
        builder = PromptBuilder()
        state = make_state()
        state.players[8].has_antidote = False
        log = make_log()

        prompt = builder.build_speech_prompt(state, 8, "wolf-killer-witch", log, "day_speech")
        assert "已用" in prompt

    def test_unknown_role_fallback(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 10, "wolf-killer-villager", log, "day_speech")
        assert "平民" in prompt

    def test_all_roles_can_generate_speech_prompt(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        for p in state.players.values():
            prompt = builder.build_speech_prompt(state, p.seat_number, p.role, log, "day_speech")
            assert len(prompt) > 100

    def test_format_alive_players(self):
        builder = PromptBuilder()
        state = make_state()
        result = builder._format_alive_players(state)
        assert "1号" in result
        assert "9号" in result

    def test_format_alive_empty(self):
        builder = PromptBuilder()
        state = GameState(game_id="test")
        result = builder._format_alive_players(state)
        assert result == "无"

    def test_format_conversations_empty(self):
        builder = PromptBuilder()
        log = make_log()
        result = builder._format_conversations(log, 1, 1, "wolf-killer-villager")
        assert "尚无对话记录" in result

    def test_task_instruction_default_fallback(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        result = builder._task_instruction("wolf-killer-villager", "unknown_context", 1, state, log)
        assert "合理决策" in result

    def test_hunter_shoot_task(self):
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        prompt = builder.build_action_prompt(state, 9, "wolf-killer-hunter", log, "hunter_shoot")
        assert "开枪" in prompt or "shoot" in prompt.lower() or "pass" in prompt.lower()

    def test_identity_emphasis(self):
        """Identity should be emphasized with bold markers and repetition."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 3, "wolf-killer-werewolf", log, "day_speech")
        assert "**你的身份是：3号玩家，狼人！**" in prompt
        assert "**重要提醒：你就是狼人" in prompt
        assert "**再次强调：请以狼人的身份" in prompt
        assert "**记住：你是狼人，3号位" in prompt

    def test_system_prompt_contains_all_rules(self):
        """System prompt must contain game flow, death rules, all roles, and win conditions."""
        sp = PromptBuilder.get_system_prompt()
        # Game flow
        assert "夜晚" in sp
        assert "天亮" in sp
        assert "遗言" in sp
        assert "发言" in sp
        assert "放逐投票" in sp
        # Death rules
        assert "不能开枪" in sp
        assert "无视一切保护" in sp
        # All 5 roles
        assert "狼人（3人" in sp or "狼人（" in sp
        assert "平民（3人" in sp or "平民（" in sp
        assert "预言家（1人" in sp or "预言家（" in sp
        assert "女巫（1人" in sp or "女巫（" in sp
        assert "猎人（1人" in sp or "猎人（" in sp
        # Win conditions
        assert "屠边" in sp
        assert "放逐所有狼人" in sp
        # Role skills
        assert "查验" in sp
        assert "解药" in sp
        assert "毒药" in sp
        assert "猎枪" in sp
        assert "狼刀" in sp

    def test_human_prompt_does_not_contain_full_rules(self):
        """Human message should focus on identity + state + task, not repeat full rules."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_speech_prompt(state, 1, "wolf-killer-werewolf", log, "day_speech")
        # Should NOT contain the full game rules (those are in system prompt)
        assert "板子配置（9人标准场）" not in prompt
        assert "游戏完整流程" not in prompt

    # ── Tool definitions tests ─────────────────────────────────

    def test_speech_tools_defined(self):
        """Tools list must contain speak and last_words functions."""
        tools = PromptBuilder.get_speech_tools()
        assert len(tools) == 2
        names = [t["function"]["name"] for t in tools]
        assert "speak" in names
        assert "last_words" in names

    def test_speak_tool_schema(self):
        """speak tool must have 'text' as required parameter."""
        tools = PromptBuilder.get_speech_tools()
        speak_tool = next(t for t in tools if t["function"]["name"] == "speak")
        params = speak_tool["function"]["parameters"]
        assert "text" in params["properties"]
        assert "text" in params["required"]

    def test_last_words_tool_schema(self):
        """last_words tool must have 'text' as required parameter."""
        tools = PromptBuilder.get_speech_tools()
        lw_tool = next(t for t in tools if t["function"]["name"] == "last_words")
        params = lw_tool["function"]["parameters"]
        assert "text" in params["properties"]
        assert "text" in params["required"]

    # ── System prompt tool usage tests ─────────────────────────

    def test_system_prompt_explains_tool_usage(self):
        """System prompt must explain how to use functions for speech."""
        sp = PromptBuilder.get_system_prompt()
        assert "如何使用发言功能" in sp
        assert "先进行深度思考" in sp
        assert "再使用函数发言" in sp
        assert "speak" in sp
        assert "last_words" in sp
        assert "严禁直接输出文本" in sp

    def test_system_prompt_requires_function_calling(self):
        """System prompt must mandate function usage."""
        sp = PromptBuilder.get_system_prompt()
        assert "必须" in sp
        assert "speak 函数或 last_words 函数" in sp
        assert "调用函数前先在内心深入思考" in sp

    # ── Task instruction tool usage tests ──────────────────────

    def test_day_speech_task_requires_deep_thinking(self):
        """Day speech task must instruct model to think deeply first."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        prompt = builder.build_speech_prompt(state, 4, "wolf-killer-villager", log, "day_speech")
        assert "先深度思考" in prompt
        assert "再调用函数发言" in prompt
        assert "speak" in prompt
        assert "必须调用 speak 函数" in prompt
        assert "直接输出文本将被系统拒绝" in prompt

    def test_last_words_task_requires_deep_thinking(self):
        """Last words task must instruct model to think deeply first."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        prompt = builder.build_speech_prompt(state, 4, "wolf-killer-villager", log, "last_words")
        assert "先深度思考" in prompt
        assert "再调用函数发表遗言" in prompt
        assert "last_words" in prompt
        assert "必须调用 last_words 函数" in prompt

    def test_day_speech_does_not_require_json(self):
        """Day speech should use function calling, not raw JSON output."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        prompt = builder.build_speech_prompt(state, 4, "wolf-killer-villager", log, "day_speech")
        # Should NOT ask for JSON output
        assert "JSON格式" not in prompt

    def test_anti_template_rules(self):
        """System prompt must forbid repeating previous speakers."""
        sp = PromptBuilder.get_system_prompt()
        assert "不要和前面的玩家说一样的话" in sp
        assert "模板化" in sp

    def test_personality_styles(self):
        """System prompt must suggest varied speaking styles."""
        sp = PromptBuilder.get_system_prompt()
        assert "激进攻击型" in sp
        assert "理性分析型" in sp
        assert "情绪渲染型" in sp

    def test_confrontation_encouragement(self):
        """System prompt must encourage direct confrontation."""
        sp = PromptBuilder.get_system_prompt()
        assert "直接点名" in sp
        assert "对抗" in sp or "冲突" in sp

    def test_werewolf_narrative_building(self):
        """Werewolf strategy guide must include narrative-building guidance."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        prompt = builder.build_speech_prompt(state, 1, "wolf-killer-werewolf", log, "day_speech")
        assert "反派主角" in prompt
        assert "剧本" in prompt or "故事线" in prompt

    def test_varied_pass_ending(self):
        """System prompt should discourage mechanical '过' ending."""
        sp = PromptBuilder.get_system_prompt()
        assert "我说完了" in sp or "先这样吧" in sp or "过" in sp

    def test_spectator_awareness(self):
        """System prompt should mention there are spectators watching."""
        sp = PromptBuilder.get_system_prompt()
        assert "观众" in sp
        assert "观赏性" in sp

    def test_system_prompt_warns_against_physical_sensations(self):
        """System prompt must forbid physical sensation language."""
        sp = PromptBuilder.get_system_prompt()
        assert "抽象桌游" in sp or "抽象的游戏机制" in sp
        assert "闻到" in sp
        assert "听到" in sp
        assert "五感" in sp
        assert "物理" in sp

    def test_system_prompt_forbids_smell_language(self):
        """Specifically forbid 'smell' related questions."""
        sp = PromptBuilder.get_system_prompt()
        assert "闻到味道" in sp or "闻到狼味" in sp

    def test_system_prompt_forbids_hearing_language(self):
        """Specifically forbid 'hearing footsteps' related language."""
        sp = PromptBuilder.get_system_prompt()
        assert "脚步声" in sp

    def test_system_prompt_forbids_visual_language(self):
        """Specifically forbid 'seeing shadows' related language."""
        sp = PromptBuilder.get_system_prompt()
        assert "看到人影" in sp or "看不到任何东西" in sp

    # ── Thought formatting tests ────────────────────────────────

    def test_format_thoughts_empty(self):
        """Empty thoughts should return placeholder."""
        builder = PromptBuilder()
        log = make_log()
        result = builder._format_thoughts(log, 1, 1)
        assert "尚无思考记录" in result

    def test_format_thoughts_with_records(self):
        """Thoughts should be formatted with round and context labels."""
        builder = PromptBuilder()
        log = make_log()
        log.add_thought(1, "wolf-killer-werewolf", "我分析了场上局势，觉得3号可能有问题", 1, "day_speech")
        log.add_thought(1, "wolf-killer-werewolf", "夜晚我决定刀预言家", 1, "night_kill")

        result = builder._format_thoughts(log, 1, 1)
        assert "第1轮" in result
        assert "我分析了场上局势" in result
        assert "夜晚我决定刀预言家" in result

    def test_format_thoughts_only_own_thoughts(self):
        """Only the player's own thoughts should be shown."""
        builder = PromptBuilder()
        log = make_log()
        log.add_thought(1, "wolf-killer-werewolf", "我的思考", 1, "day_speech")
        log.add_thought(3, "wolf-killer-villager", "别人的思考", 1, "day_speech")

        result = builder._format_thoughts(log, 1, 1)
        assert "我的思考" in result
        assert "别人的思考" not in result

    def test_format_thoughts_truncates_long_content(self):
        """Content over 400 chars should be truncated."""
        builder = PromptBuilder()
        log = make_log()
        long_thought = "思考" * 250  # 500 chars
        log.add_thought(1, "wolf-killer-werewolf", long_thought, 1, "day_speech")

        result = builder._format_thoughts(log, 1, 1)
        assert "..." in result
        assert len(result) < len(long_thought) + 100

    def test_format_thoughts_max_15_records(self):
        """Should only show last 15 thoughts."""
        builder = PromptBuilder()
        log = make_log()
        for i in range(20):
            log.add_thought(1, "wolf-killer-werewolf", f"思考第{i}条", i + 1, "day_speech")

        result = builder._format_thoughts(log, 21, 1)
        assert "思考第0条" not in result  # first 5 dropped
        assert "思考第19条" in result

    def test_speech_prompt_includes_thought_section(self):
        """Speech prompt should include thought history section."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        log.add_thought(1, "wolf-killer-werewolf", "我之前的分析思考", 1, "day_speech")

        prompt = builder.build_speech_prompt(state, 1, "wolf-killer-werewolf", log, "day_speech")
        assert "你的历史思考回顾" in prompt

    def test_action_prompt_includes_thought_section(self):
        """Action prompt should include thought history section."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()
        log.add_thought(1, "wolf-killer-werewolf", "夜晚前的思考", 1, "night_kill")

        prompt = builder.build_action_prompt(state, 1, "wolf-killer-werewolf", log, "night_kill")
        assert "你的历史思考回顾" in prompt

    def test_vote_prompt_includes_thought_section(self):
        """Vote prompt should include thought history section."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_vote_prompt(state, 4, "wolf-killer-villager", log, "exile_vote")
        assert "你的历史思考回顾" in prompt

    def test_night_kill_task_requires_thinking_field(self):
        """Night kill task should require thinking field in JSON output."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 1, "wolf-killer-werewolf", log, "night_kill")
        assert '"thinking"' in prompt

    def test_witch_save_task_requires_thinking_field(self):
        """Witch save task should require thinking field."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 8, "wolf-killer-witch", log, "witch_save", wolf_target=3)
        assert '"thinking"' in prompt

    def test_witch_poison_task_requires_thinking_field(self):
        """Witch poison task should require thinking field."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 8, "wolf-killer-witch", log, "witch_poison", wolf_target=3)
        assert '"thinking"' in prompt

    def test_night_check_task_requires_thinking_field(self):
        """Night check task should require thinking field."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 7, "wolf-killer-seer", log, "night_check")
        assert '"thinking"' in prompt

    def test_hunter_shoot_task_requires_thinking_field(self):
        """Hunter shoot task should require thinking field."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_action_prompt(state, 9, "wolf-killer-hunter", log, "hunter_shoot")
        assert '"thinking"' in prompt

    def test_exile_vote_task_requires_thinking_field(self):
        """Exile vote task should require thinking field."""
        builder = PromptBuilder()
        state = make_state()
        log = make_log()

        prompt = builder.build_vote_prompt(state, 4, "wolf-killer-villager", log, "exile_vote")
        assert '"thinking"' in prompt
