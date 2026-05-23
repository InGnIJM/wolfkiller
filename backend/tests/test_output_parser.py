import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage
from app.agents.output_parser import OutputParser, ToolCallResult


class TestOutputParser:
    def test_parse_night_action_json(self):
        parser = OutputParser()
        raw = '{"action_type": "kill", "target_seat": 5, "reasoning": "suspicious"}'
        action = parser.parse_night_action(raw, player_seat=1)
        assert action.action_type == "kill"
        assert action.target_seat == 5
        assert action.player_seat == 1

    def test_parse_night_action_code_block(self):
        parser = OutputParser()
        raw = '```json\n{"action_type": "check", "target_seat": 3, "reasoning": "need info"}\n```'
        action = parser.parse_night_action(raw, player_seat=1)
        assert action.action_type == "check"
        assert action.target_seat == 3

    def test_parse_night_action_malformed_defaults_to_pass(self):
        parser = OutputParser()
        raw = "I think I'll check player 3"
        action = parser.parse_night_action(raw, player_seat=1)
        assert action.action_type == "pass"
        assert action.target_seat is None

    def test_parse_vote_action(self):
        parser = OutputParser()
        raw = '{"target_seat": 4, "reasoning": "was suspicious"}'
        vote = parser.parse_vote_action(raw, voter_seat=2)
        assert vote.target_seat == 4
        assert vote.voter_seat == 2

    def test_parse_vote_abstain(self):
        parser = OutputParser()
        raw = '{"target_seat": 0, "reasoning": "not sure"}'
        vote = parser.parse_vote_action(raw, voter_seat=2)
        assert vote.target_seat is None

    def test_parse_vote_malformed(self):
        parser = OutputParser()
        raw = "I don't know who to vote for"
        vote = parser.parse_vote_action(raw, voter_seat=2)
        assert vote.target_seat is None

    def test_parse_speech_plain_text(self):
        parser = OutputParser()
        raw = "我觉得3号发言很可疑，建议今天出3号。"
        speech = parser.parse_speech(raw)
        assert speech == raw.strip()

    def test_parse_speech_from_json(self):
        parser = OutputParser()
        raw = '{"speech_text": "我怀疑5号是狼"}'
        speech = parser.parse_speech(raw)
        assert speech == "我怀疑5号是狼"

    def test_parse_night_action_json_in_text(self):
        parser = OutputParser()
        raw = '我的想法如下：\n{"action_type": "kill", "target_seat": 2, "reasoning": "必须刀预言家"}'
        action = parser.parse_night_action(raw, player_seat=1)
        assert action.action_type == "kill"
        assert action.target_seat == 2

    def test_parse_speech_from_json_with_text_key(self):
        parser = OutputParser()
        raw = '{"text": "我怀疑5号", "reasoning": "因为他的发言"}'
        speech = parser.parse_speech(raw)
        assert speech == "我怀疑5号"

    def test_parse_speech_empty_json(self):
        parser = OutputParser()
        raw = '{"other_field": "value"}'
        speech = parser.parse_speech(raw)
        assert speech == '{"other_field": "value"}'

    def test_parse_night_action_empty_text(self):
        parser = OutputParser()
        action = parser.parse_night_action("", player_seat=1)
        assert action.action_type == "pass"

    def test_parse_vote_empty_text(self):
        parser = OutputParser()
        vote = parser.parse_vote_action("", voter_seat=1)
        assert vote.target_seat is None

    def test_parse_night_action_brace_json_decode_fails(self):
        parser = OutputParser()
        raw = '文本前缀 {"action_type": "check", "target_seat": 3, "reasoning": "test"} 文本后缀'
        action = parser.parse_night_action(raw, player_seat=1)
        assert action.action_type == "check"
        assert action.target_seat == 3

    def test_parse_speech_from_code_block_json_decode_fails(self):
        parser = OutputParser()
        raw = "```json\n{invalid json content}\n```"
        speech = parser.parse_speech(raw)
        assert speech == raw.strip()

    def test_parse_night_action_code_block_parse_fails(self):
        parser = OutputParser()
        raw = "```json\n{not valid json!!!}\n```"
        action = parser.parse_night_action(raw, player_seat=1)
        assert action.action_type == "pass"

    # ── Tool call parsing tests ───────────────────────────────

    def test_parse_tool_call_native_speak(self):
        parser = OutputParser()
        msg = AIMessage(
            content="",
            tool_calls=[{
                "name": "speak",
                "args": {"text": "我觉得3号很可疑，建议今天出3号。"},
                "id": "call_001",
            }],
        )
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert result.function_name == "speak"
        assert result.arguments["text"] == "我觉得3号很可疑，建议今天出3号。"

    def test_parse_tool_call_native_last_words(self):
        parser = OutputParser()
        msg = AIMessage(
            content="",
            tool_calls=[{
                "name": "last_words",
                "args": {"text": "我是预言家，昨晚查了5号是狼。"},
                "id": "call_002",
            }],
        )
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert result.function_name == "last_words"
        assert "预言家" in result.arguments["text"]

    def test_parse_tool_call_args_as_dict(self):
        """Native tool call where args is already a dict."""
        parser = OutputParser()
        msg = AIMessage(
            content="",
            tool_calls=[{
                "name": "speak",
                "args": {"text": "我怀疑1号和5号是狼队友。"},
                "id": "call_003",
            }],
        )
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert result.function_name == "speak"
        assert "1号" in result.arguments["text"]

    def test_parse_tool_call_no_tool_calls(self):
        parser = OutputParser()
        msg = AIMessage(content="我觉得应该出3号。")
        result = parser.parse_tool_call(msg)
        assert result is None

    def test_parse_tool_call_empty_response(self):
        parser = OutputParser()
        msg = AIMessage(content="")
        result = parser.parse_tool_call(msg)
        assert result is None

    def test_parse_tool_call_fallback_speak_named_param(self):
        """Fallback: parse speak(text="...") pattern from plain text."""
        parser = OutputParser()
        msg = AIMessage(content='speak(text="我怀疑3号是狼，建议出3号。")')
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert result.function_name == "speak"
        assert "3号" in result.arguments["text"]

    def test_parse_tool_call_fallback_speak_positional(self):
        """Fallback: parse speak("...") positional arg pattern."""
        parser = OutputParser()
        msg = AIMessage(content='speak("我认为5号发言有矛盾。")')
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert result.function_name == "speak"
        assert "5号" in result.arguments["text"]

    def test_parse_tool_call_fallback_last_words(self):
        """Fallback: parse last_words("...") pattern."""
        parser = OutputParser()
        msg = AIMessage(content='last_words("我是平民，我认为狼人是1号和3号。")')
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert result.function_name == "last_words"
        assert "平民" in result.arguments["text"]

    def test_parse_tool_call_fallback_no_match(self):
        parser = OutputParser()
        msg = AIMessage(content="我觉得3号是狼，但我不调用任何函数。")
        result = parser.parse_tool_call(msg)
        assert result is None

    # ── Thinking extraction tests ──────────────────────────────

    def test_parse_tool_call_extracts_thinking_from_content(self):
        """Thinking text should be extracted from AIMessage.content."""
        parser = OutputParser()
        msg = AIMessage(
            content="我先分析一下局势：3号发言有漏洞，5号投票可疑。决定指认3号。",
            tool_calls=[{
                "name": "speak",
                "args": {"text": "我觉得3号发言很有问题，建议今天出3号。"},
                "id": "call_001",
            }],
        )
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert "3号发言有漏洞" in result.thinking_text

    def test_parse_tool_call_empty_content_no_thinking(self):
        """Empty content should result in empty thinking_text."""
        parser = OutputParser()
        msg = AIMessage(
            content="",
            tool_calls=[{
                "name": "speak",
                "args": {"text": "我觉得3号可疑。"},
                "id": "call_001",
            }],
        )
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert result.thinking_text == ""

    def test_parse_tool_call_fallback_with_thinking(self):
        """Fallback text parsing should capture thinking from raw content."""
        parser = OutputParser()
        msg = AIMessage(
            content='让我分析一下...speak(text="我觉得3号可疑")',
        )
        result = parser.parse_tool_call(msg)
        assert result is not None
        assert result.function_name == "speak"
        assert "让我分析一下" in result.thinking_text

    def test_parse_night_action_extracts_thinking(self):
        """Night action JSON with thinking field should be extracted."""
        parser = OutputParser()
        raw = '{"thinking":"分析了局势觉得3号像狼","action_type":"kill","target_seat":3,"reasoning":"3号发言有漏洞"}'
        action = parser.parse_night_action(raw, player_seat=1)
        assert action.thinking == "分析了局势觉得3号像狼"

    def test_parse_night_action_no_thinking_field(self):
        """Night action JSON without thinking field should default to empty."""
        parser = OutputParser()
        raw = '{"action_type":"check","target_seat":5,"reasoning":"need info"}'
        action = parser.parse_night_action(raw, player_seat=1)
        assert action.thinking == ""

    def test_parse_vote_action_extracts_thinking(self):
        """Vote action JSON with thinking field should be extracted."""
        parser = OutputParser()
        raw = '{"thinking":"经过分析决定投3号","target_seat":3,"reasoning":"3号发言最可疑"}'
        vote = parser.parse_vote_action(raw, voter_seat=1)
        assert vote.thinking == "经过分析决定投3号"

    def test_parse_vote_action_no_thinking_field(self):
        """Vote action JSON without thinking field should default to empty."""
        parser = OutputParser()
        raw = '{"target_seat":4,"reasoning":"suspicious"}'
        vote = parser.parse_vote_action(raw, voter_seat=1)
        assert vote.thinking == ""
