"""Thinking-stream extraction: reasoning deltas and content-block lists."""
import pytest
from langchain_core.messages import AIMessageChunk

from app.agents import llm_client
from app.agents.llm_client import LLMClient, split_stream_content


class _Recorder:
    def __init__(self):
        self.speech: list[str] = []
        self.thinking: list[str] = []

    async def __call__(self, text: str, kind: str = "speech") -> None:
        (self.thinking if kind == "thinking" else self.speech).append(text)


def _stream(*chunks):
    class Model:
        async def astream(self, messages):
            for chunk in chunks:
                yield chunk
    return Model()


@pytest.fixture
def no_throttle(monkeypatch):
    monkeypatch.setattr(llm_client, "_THINKING_EMIT_INTERVAL_SECONDS", 0.0)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("plain", ("plain", "")),
        (None, ("", "")),
        (
            [
                "raw",
                {"type": "thinking", "thinking": "想"},
                {"type": "thinking", "thinking": None},
                {"type": "text", "text": "说"},
                {"type": "text", "text": None},
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "摘"}, "x"]},
                {"type": "reasoning", "reasoning": "推"},
                {"type": "reasoning", "summary": [], "text": "理"},
                {"type": "reasoning"},
                {"type": "tool_use", "partial_json": "{}"},
                42,
            ],
            ("raw说", "想摘推理"),
        ),
    ],
)
def test_split_stream_content(content, expected):
    assert split_stream_content(content) == expected


@pytest.mark.asyncio
async def test_reasoning_content_streams_before_the_tool_arguments(no_throttle):
    record = _Recorder()
    model = _stream(
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "先看"}),
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "局势"}),
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "speak", "args": '{"text": "我是1号', "id": "c", "index": 0},
        ]),
    )
    await LLMClient._stream_and_accumulate(model, [], on_chunk=record)
    assert record.thinking == ["先看", "先看局势"]
    assert record.speech == ["我是1号"]


@pytest.mark.asyncio
async def test_block_lists_split_thinking_from_the_answer(no_throttle):
    record = _Recorder()
    model = _stream(
        AIMessageChunk(content=[{"type": "thinking", "thinking": "思", "index": 0}]),
        AIMessageChunk(content=[{"type": "text", "text": '{"text": "发言', "index": 1}]),
    )
    await LLMClient._stream_and_accumulate(model, [], on_chunk=record)
    assert record.thinking == ["思"]
    assert record.speech == ["发言"]


@pytest.mark.asyncio
async def test_responses_text_blocks_are_read_as_text(no_throttle):
    record = _Recorder()
    model = _stream(
        AIMessageChunk(content=[{"type": "text", "text": "第一句", "index": 0}]),
        AIMessageChunk(content=[{"type": "text", "text": "第二句", "index": 0}]),
    )
    await LLMClient._stream_and_accumulate(model, [], on_chunk=record)
    assert record.speech == ["第一句", "第一句第二句"]
    assert record.thinking == []


@pytest.mark.asyncio
async def test_thinking_is_throttled_then_flushed_and_tail_capped(monkeypatch):
    monkeypatch.setattr(llm_client, "_THINKING_TAIL_CHARS", 3)
    record = _Recorder()
    model = _stream(
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "一二"}),
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "三四"}),
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "五"}),
    )
    await LLMClient._stream_and_accumulate(model, [], on_chunk=record)
    # The first delta goes out at once; the next two fall inside the throttle
    # window and only the end-of-stream flush delivers the final tail.
    assert record.thinking == ["一二", "三四五"]


@pytest.mark.asyncio
async def test_no_flush_when_the_last_thinking_was_already_sent(no_throttle):
    record = _Recorder()
    model = _stream(
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "想"}),
        AIMessageChunk(content="答"),
    )
    await LLMClient._stream_and_accumulate(model, [], on_chunk=record)
    assert record.thinking == ["想"]
    assert record.speech == ["答"]


@pytest.mark.asyncio
async def test_an_empty_stream_with_a_callback_emits_nothing():
    record = _Recorder()
    response = await LLMClient._stream_and_accumulate(_stream(), [], on_chunk=record)
    assert response.content == ""
    assert record.thinking == record.speech == []
