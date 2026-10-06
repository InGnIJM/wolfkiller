import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.llm_client import LLMClient, LLMClientConfig, extract_partial_speech


def _test_config():
    return LLMClientConfig(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model_id="gpt-4o",
        temperature=0.7,
        max_tokens=512,
        strict_base_url="https://api.openai.com/v1",
        action_max_tokens=2048,
    )


def test_extract_partial_speech_cases():
    assert extract_partial_speech("") == ""
    assert extract_partial_speech("   ") == ""
    # Plain text
    assert extract_partial_speech("我是6号玩家，今天上警") == "我是6号玩家，今天上警"
    # Incomplete json object without text key
    assert extract_partial_speech('{"action": "speak"') == ""
    # JSON with partial text
    assert extract_partial_speech('{"text": "我是6号') == "我是6号"
    # JSON with escaped quotes, newlines, tabs, backslashes
    raw = r'{"text": "他说：\"你好\"\n第二行\t缩进\\斜杠"}'
    expected = '他说："你好"\n第二行\t缩进\\斜杠'
    assert extract_partial_speech(raw) == expected
    # Trailing single backslash
    raw_trailing = r'{"text": "测试尾部斜杠\\'
    assert extract_partial_speech(raw_trailing) == "测试尾部斜杠\\"
    raw_incomplete_escape = '{"text": "未完成转义\\'
    assert extract_partial_speech(raw_incomplete_escape) == "未完成转义"


@pytest.mark.asyncio
async def test_stream_and_accumulate_with_tool_chunks():
    client = LLMClient(config=_test_config())

    class MockStreamModel:
        async def astream(self, messages):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "speak", "args": '{"text": "我是6号', "id": "call_1", "index": 0}],
            )
            # Duplicate text should be suppressed
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": None, "args": "", "id": None, "index": 0}],
            )
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": None, "args": "，猎人。\"}", "id": None, "index": 0}],
            )

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    response = await client._stream_and_accumulate(
        MockStreamModel(), [], on_chunk=on_chunk,
    )
    assert response.tool_calls == [{"name": "speak", "args": {"text": "我是6号，猎人。"}, "id": "call_1", "type": "tool_call"}]
    assert emitted == ["我是6号", "我是6号，猎人。"]


@pytest.mark.asyncio
async def test_stream_and_accumulate_with_content_chunks():
    client = LLMClient(config=_test_config())

    class MockStreamModel:
        async def astream(self, messages):
            yield AIMessageChunk(content="第一段发言，")
            yield AIMessageChunk(content="")
            yield AIMessageChunk(content="第二段发言。")

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    response = await client._stream_and_accumulate(
        MockStreamModel(), [], on_chunk=on_chunk,
    )
    assert response.content == "第一段发言，第二段发言。"
    assert emitted == ["第一段发言，", "第一段发言，第二段发言。"]


@pytest.mark.asyncio
async def test_stream_and_accumulate_empty_and_no_on_chunk():
    client = LLMClient(config=_test_config())

    class EmptyStreamModel:
        async def astream(self, messages):
            if False:
                yield None

    response = await client._stream_and_accumulate(
        EmptyStreamModel(), [], on_chunk=None,
    )
    assert response.content == ""


@pytest.mark.asyncio
async def test_ainvoke_json_streaming_success():
    client = LLMClient(config=_test_config())

    class ToolModel:
        async def astream(self, messages):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "speak", "args": '{"text": "流式发言内容"}', "id": "call_1", "index": 0}],
            )

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    with patch.object(client, "get_model_with_json_tool", return_value=ToolModel()):
        result = await client.ainvoke_json(
            [],
            tool_name="speak",
            schema={"type": "object", "properties": {"text": {"type": "string"}}},
            on_chunk=on_chunk,
        )

    assert result.payload == {"text": "流式发言内容"}
    assert emitted == ["流式发言内容"]


@pytest.mark.asyncio
async def test_ainvoke_json_streaming_fallback():
    client = LLMClient(config=_test_config())

    class FailingToolModel:
        async def astream(self, messages):
            raise ValueError("Tool unsupported")
            yield None

    class FallbackModel:
        async def astream(self, messages):
            yield AIMessageChunk(content='{"text": "降级流式内容"}')

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    with (
        patch.object(client, "get_model_with_json_tool", return_value=FailingToolModel()),
        patch.object(client, "get_action_model", return_value=FallbackModel()),
    ):
        result = await client.ainvoke_json(
            [],
            tool_name="speak",
            schema={"type": "object", "properties": {"text": {"type": "string"}}},
            on_chunk=on_chunk,
        )

    assert result.payload == {"text": "降级流式内容"}
    assert emitted == ["降级流式内容"]


@pytest.mark.asyncio
async def test_ainvoke_json_streaming_without_action_tools_support():
    from dataclasses import replace
    client = LLMClient(config=_test_config())
    client.provider_profile = replace(
        client.provider_profile,
        capabilities=replace(client.provider_profile.capabilities, tools=False),
    )

    class ActionStreamModel:
        async def astream(self, messages):
            yield AIMessageChunk(content='{"text": "无工具直接流式"}')

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    with patch.object(client, "get_action_model", return_value=ActionStreamModel()):
        result = await client.ainvoke_json(
            [],
            tool_name="speak",
            schema={"type": "object", "properties": {"text": {"type": "string"}}},
            on_chunk=on_chunk,
        )

    assert result.payload == {"text": "无工具直接流式"}
    assert emitted == ["无工具直接流式"]


@pytest.mark.asyncio
async def test_stream_and_accumulate_with_empty_or_whitespace_chunks():
    client = LLMClient(config=_test_config())

    class EmptyChunkModel:
        async def astream(self, messages):
            # chunk with neither tool calls nor content
            yield AIMessageChunk(content="")
            # chunk with unextractable json
            yield AIMessageChunk(content='{"reasoning": "abc"}')

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    await client._stream_and_accumulate(
        EmptyChunkModel(), [], on_chunk=on_chunk,
    )
    assert emitted == []

