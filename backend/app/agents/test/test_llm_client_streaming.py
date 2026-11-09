import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents import llm_client
from app.agents.llm_client import LLMClient, LLMClientConfig, extract_partial_speech
from app.models.contracts import ActionContract
from app.models.game import GamePhase


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
async def test_stream_and_accumulate_without_a_callback_keeps_every_chunk():
    """No callback: the loop still accumulates chunk by chunk."""
    client = LLMClient(config=_test_config())

    class TwoChunkModel:
        async def astream(self, messages):
            yield AIMessageChunk(content="上半句，")
            yield AIMessageChunk(content="下半句。")

    result = await client._stream_and_accumulate(TwoChunkModel(), [])

    assert result.content == "上半句，下半句。"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # "text" appears only inside another key ("context"), so the key
        # pattern does not match and the JSON must not be read as speech.
        ('{"context": "火", "seat": 3}', ""),
        # An escape the extractor does not know about keeps the literal char.
        ('{"text": "a\\qb"}', "aqb"),
        ("plain words", "plain words"),
    ],
)
def test_extract_partial_speech_edge_cases(raw, expected):
    assert extract_partial_speech(raw) == expected


@pytest.mark.asyncio
async def test_stream_and_accumulate_with_empty_or_whitespace_chunks():
    client = LLMClient(config=_test_config())

    class EmptyChunkModel:
        async def astream(self, messages):
            # chunk with neither tool calls nor content
            yield AIMessageChunk(content="")
            # chunk with unextractable json
            yield AIMessageChunk(content='{"reasoning": "abc"}')
            # a later chunk carries the text: the first two must stay silent,
            # and the callback fires exactly once with the extracted partial
            yield AIMessageChunk(content='{"text": "后来者"}')

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    await client._stream_and_accumulate(
        EmptyChunkModel(), [], on_chunk=on_chunk,
    )
    assert emitted == ["后来者"]


def _action_contract() -> ActionContract:
    return ActionContract(
        contract_id="night_check",
        phase=GamePhase.NIGHT,
        action_types=("check", "pass"),
        actions_requiring_target=frozenset({"check"}),
        resolution_priority=1,
        fallback_action_type="pass",
    )


def test_complete_falls_back_to_invoke_when_type_has_no_stream():
    class InvokeOnly:
        def invoke(self, messages):
            return AIMessage(content="via invoke")

    assert LLMClient._complete(InvokeOnly(), []).content == "via invoke"


def test_complete_ignores_instance_stream_that_the_type_does_not_declare():
    class InvokeOnly:
        def invoke(self, messages):
            return AIMessage(content="via invoke")

    model = InvokeOnly()
    model.stream = object()
    assert LLMClient._complete(model, []).content == "via invoke"


def test_complete_uses_stream_inherited_from_a_base_class():
    class StreamBase:
        def stream(self, messages):
            yield AIMessageChunk(content="from ")
            yield AIMessageChunk(content="base")

    class Derived(StreamBase):
        pass

    assert LLMClient._complete(Derived(), []).content == "from base"


def test_complete_ignores_non_callable_stream_attr():
    class Model:
        stream = "not a method"

        def invoke(self, messages):
            return AIMessage(content="via invoke")

    assert LLMClient._complete(Model(), []).content == "via invoke"


def test_complete_empty_stream_returns_empty_message():
    class Model:
        def stream(self, messages):
            return iter(())

    assert LLMClient._complete(Model(), []).content == ""


def test_invoke_action_prefers_stream_over_invoke():
    streamed = []

    class Model:
        def stream(self, messages):
            streamed.append(True)
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "night_check",
                    "args": '{"action_type":"check","target_seat":2,"reasoning":"x"}',
                    "id": "call_1",
                    "index": 0,
                }],
            )

    client = LLMClient(config=_test_config())
    with patch.object(client, "get_model_with_action_tool", return_value=Model()):
        result = client.invoke_action([], _action_contract())

    assert streamed == [True]
    assert result.payload["target_seat"] == 2
    assert result.has_tool_calls is True


def test_invoke_json_prefers_stream_over_invoke():
    streamed = []
    schema = {
        "type": "object",
        "properties": {"speak": {"type": "boolean"}},
        "required": ["speak"],
    }

    class Model:
        def stream(self, messages):
            streamed.append(True)
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "submit_night_json",
                    "args": '{"speak":false}',
                    "id": "call_1",
                    "index": 0,
                }],
            )

    client = LLMClient(config=_test_config())
    with patch.object(client, "get_model_with_json_tool", return_value=Model()):
        result = client.invoke_json(
            [], tool_name="submit_night_json", schema=schema,
        )

    assert streamed == [True]
    assert result.payload == {"speak": False}


def test_probe_prefers_stream_over_invoke():
    streamed = []

    class Model:
        def stream(self, messages):
            streamed.append(messages)
            yield AIMessageChunk(content="pong")

    client = LLMClient(config=_test_config())
    with patch.object(client, "get_model", return_value=Model()):
        caps = client.probe()

    assert streamed
    assert caps["tools"] is True


@pytest.mark.asyncio
async def test_ainvoke_json_streams_without_on_chunk():
    class ToolModel:
        async def astream(self, messages):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "speak",
                    "args": '{"text": "无回调也流式"}',
                    "id": "call_1",
                    "index": 0,
                }],
            )

    client = LLMClient(config=_test_config())
    with patch.object(client, "get_model_with_json_tool", return_value=ToolModel()):
        result = await client.ainvoke_json(
            [],
            tool_name="speak",
            schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )

    assert result.payload == {"text": "无回调也流式"}


@pytest.mark.asyncio
async def test_acomplete_falls_back_to_ainvoke():
    class InvokeOnly:
        async def ainvoke(self, messages):
            return AIMessage(content="via ainvoke")

    result = await LLMClient._acomplete(InvokeOnly(), [])
    assert result.content == "via ainvoke"


@pytest.mark.asyncio
async def test_acomplete_ignores_non_callable_astream_attr():
    class Model:
        astream = "not a method"

        async def ainvoke(self, messages):
            return AIMessage(content="via ainvoke")

    result = await LLMClient._acomplete(Model(), [])
    assert result.content == "via ainvoke"


@pytest.mark.asyncio
async def test_stream_and_accumulate_throttles_speech_but_flushes_the_tail(monkeypatch):
    """Partials inside the emit interval are swallowed; the last one is flushed."""
    monkeypatch.setattr(llm_client, "_SPEECH_EMIT_INTERVAL_SECONDS", 999.0)
    client = LLMClient(config=_test_config())

    class ThreeChunkModel:
        async def astream(self, messages):
            yield AIMessageChunk(content="第一句，")
            yield AIMessageChunk(content="第二句，")
            yield AIMessageChunk(content="第三句。")

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    await client._stream_and_accumulate(
        ThreeChunkModel(), [], on_chunk=on_chunk,
    )
    assert emitted == ["第一句，", "第一句，第二句，第三句。"]


@pytest.mark.asyncio
async def test_stream_and_accumulate_emits_every_partial_once_interval_elapses(monkeypatch):
    """interval=0 preserves the legacy every-chunk emission."""
    monkeypatch.setattr(llm_client, "_SPEECH_EMIT_INTERVAL_SECONDS", 0.0)
    client = LLMClient(config=_test_config())

    class ThreeChunkModel:
        async def astream(self, messages):
            yield AIMessageChunk(content="第一句，")
            yield AIMessageChunk(content="第二句，")
            yield AIMessageChunk(content="第三句。")

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    await client._stream_and_accumulate(
        ThreeChunkModel(), [], on_chunk=on_chunk,
    )
    assert emitted == ["第一句，", "第一句，第二句，", "第一句，第二句，第三句。"]


@pytest.mark.asyncio
async def test_stream_and_accumulate_single_partial_emits_once(monkeypatch):
    """The tail flush must not re-emit a partial already sent."""
    monkeypatch.setattr(llm_client, "_SPEECH_EMIT_INTERVAL_SECONDS", 999.0)
    client = LLMClient(config=_test_config())

    class SingleChunkModel:
        async def astream(self, messages):
            yield AIMessageChunk(content="只发一次")

    emitted = []

    async def on_chunk(text: str):
        emitted.append(text)

    await client._stream_and_accumulate(
        SingleChunkModel(), [], on_chunk=on_chunk,
    )
    assert emitted == ["只发一次"]


@pytest.mark.asyncio
async def test_stream_and_accumulate_without_speech_emits_nothing(monkeypatch):
    """Chunks with no extractable speech never emit, not even at flush time."""
    monkeypatch.setattr(llm_client, "_SPEECH_EMIT_INTERVAL_SECONDS", 999.0)
    client = LLMClient(config=_test_config())

    class NoSpeechModel:
        async def astream(self, messages):
            yield AIMessageChunk(content='{"reasoning": "想一想"}')
            yield AIMessageChunk(content="")

    on_chunk = AsyncMock()

    await client._stream_and_accumulate(
        NoSpeechModel(), [], on_chunk=on_chunk,
    )
    on_chunk.assert_not_called()

