import json
import logging
import os

import pytest

from app.core.prompt_capture import (
    PROMPT_CAPTURE_ENV,
    PROMPT_CAPTURE_FILENAME,
    PROMPT_CAPTURE_OPERATION,
    PromptCapture,
    PromptSink,
    build_prompt_record,
    capture_enabled,
    capture_enabled_for_game,
    parse_capture_flag,
    prompt_capture_marker_exists,
    prompt_sha256,
    resolve_capture_setting,
)


def read_lines(tmp_path, game_id="g"):
    path = tmp_path / "games" / game_id / PROMPT_CAPTURE_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class FakeSystemMessage:
    type = "system"
    content = "sys"


class FakeHumanMessage:
    type = "human"
    content = "usr"


# ── PromptSink ────────────────────────────────────────────────

def test_record_normalizes_langchain_and_dict_messages_onto_one_vocabulary():
    sink = PromptSink()
    sink.record([FakeSystemMessage(), FakeHumanMessage()], tool_schema={"a": 1}, tool_name="t")

    assert sink.messages == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert sink.tool_schema == {"a": 1}
    assert sink.tool_name == "t"
    assert sink.attempt_count == 1
    assert sink.empty is False


def test_record_keeps_assistant_role_from_dict_messages():
    sink = PromptSink()
    sink.record([{"role": "assistant", "content": "hi"}, {"role": "AI", "content": "x"}])

    assert [item["role"] for item in sink.messages] == ["assistant", "assistant"]


def test_record_stringifies_non_string_content_and_untyped_role():
    sink = PromptSink()
    sink.record([{"role": 7, "content": None}, {"role": "user", "content": ["block"]}])

    assert sink.messages == [
        {"role": "", "content": "None"},
        {"role": "user", "content": "['block']"},
    ]


def test_record_drops_non_string_tool_name():
    sink = PromptSink()
    sink.record([{"role": "user", "content": "x"}], tool_name=42)

    assert sink.tool_name is None
    assert sink.tool_schema is None


def test_record_swallows_a_non_iterable_message_list():
    sink = PromptSink()
    sink.record(5)

    assert sink.empty is True
    assert sink.attempt_count == 0


def test_sink_properties_are_empty_before_any_record():
    sink = PromptSink()

    assert sink.empty is True
    assert sink.attempt_count == 0
    assert sink.messages == []
    assert sink.tool_schema is None
    assert sink.tool_name is None


def test_sink_keeps_the_last_attempt_and_counts_every_record():
    sink = PromptSink()
    sink.record([{"role": "user", "content": "first"}], tool_name="a")
    sink.record([{"role": "user", "content": "second"}], tool_name="b")

    assert sink.attempt_count == 2
    assert sink.messages == [{"role": "user", "content": "second"}]
    assert sink.tool_name == "b"


def test_record_deep_copies_frozen_tool_schema():
    source = {"type": "object", "properties": {"seat": {"enum": (1, 2)}}}
    sink = PromptSink()
    sink.record([{"role": "user", "content": "x"}], tool_schema=source)
    source["properties"]["seat"]["enum"] = "mutated"

    assert sink.tool_schema == {
        "type": "object", "properties": {"seat": {"enum": [1, 2]}},
    }


def test_record_stringifies_unknown_tool_schema_values():
    class Exotic:
        def __str__(self):
            return "exotic"

    sink = PromptSink()
    sink.record(
        [{"role": "user", "content": "x"}],
        tool_schema={"nested": [None, 1, 2.5, True, Exotic()]},
    )

    assert sink.tool_schema == {"nested": [None, 1, 2.5, True, "exotic"]}


# ── prompt_sha256 ─────────────────────────────────────────────

def test_prompt_sha256_is_key_order_insensitive_but_message_order_sensitive():
    first = [{"role": "system", "content": "a"}, {"role": "user", "content": "b"}]
    reordered_keys = [{"content": "a", "role": "system"}, {"role": "user", "content": "b"}]
    swapped = [{"role": "user", "content": "b"}, {"role": "system", "content": "a"}]

    assert prompt_sha256(first) == prompt_sha256(reordered_keys)
    assert prompt_sha256(first) != prompt_sha256(swapped)
    assert len(prompt_sha256(first)) == 64


# ── build_prompt_record ───────────────────────────────────────

def test_build_prompt_record_returns_none_when_nothing_was_sent():
    assert build_prompt_record(
        request_id="r", call_kind="speech", round_number=1, phase="speech",
        seat=1, contract_id="day_speech", transport="speech_tool", attempt=1,
        model_id="m", request_digest=None, sink=PromptSink(),
    ) is None


def test_build_prompt_record_carries_every_fidelity_field():
    sink = PromptSink()
    sink.record(
        [{"role": "system", "content": "abc"}, {"role": "user", "content": "de"}],
        tool_schema={"type": "object"}, tool_name="werewolf_kill",
    )

    record = build_prompt_record(
        request_id="night:1:werewolf_kill:5:abc", call_kind="night",
        round_number=1, phase="night", seat=5, contract_id="werewolf_kill",
        transport="night_json", attempt=1, model_id="deepseek-v4-pro",
        request_digest="deadbeef", sink=sink,
    )

    assert record["operation"] == PROMPT_CAPTURE_OPERATION
    assert record["round"] == 1
    assert record["phase"] == "night"
    assert record["seat"] == 5
    data = record["data"]
    assert data["schema_version"] == 1
    assert data["request_id"] == "night:1:werewolf_kill:5:abc"
    assert data["call_kind"] == "night"
    assert data["contract_id"] == "werewolf_kill"
    assert data["transport"] == "night_json"
    assert data["attempt"] == 1
    assert data["attempt_count"] == 1
    assert data["model_id"] == "deepseek-v4-pro"
    assert data["request_digest"] == "deadbeef"
    assert data["prompt_chars"] == 5
    assert data["prompt_sha256"] == prompt_sha256(data["messages"])
    assert data["tool_name"] == "werewolf_kill"
    assert data["tool_schema"] == {"type": "object"}
    assert data["messages"] == [
        {"role": "system", "content": "abc"}, {"role": "user", "content": "de"},
    ]


def test_build_prompt_record_accepts_an_injected_timestamp():
    from datetime import datetime, timezone

    sink = PromptSink()
    sink.record([{"role": "user", "content": "x"}])
    stamp = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

    record = build_prompt_record(
        request_id="r", call_kind="speech", round_number=1, phase="speech",
        seat=1, contract_id=None, transport=None, attempt=2, model_id=None,
        request_digest=None, sink=sink, now=stamp,
    )

    assert record["timestamp"] == stamp.isoformat()
    assert record["data"]["contract_id"] is None
    assert record["data"]["transport"] is None
    assert record["data"]["model_id"] is None


# ── marker ────────────────────────────────────────────────────

def test_marker_reflects_the_capture_file_existence(tmp_path):
    assert prompt_capture_marker_exists(str(tmp_path), "g") is False
    PromptCapture(str(tmp_path), "g")
    assert prompt_capture_marker_exists(str(tmp_path), "g") is True


# ── configuration ─────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on", "On"])
def test_parse_capture_flag_accepts_the_truthy_spellings(raw):
    assert parse_capture_flag(raw) is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "maybe", "", "  ", None, 1, True])
def test_parse_capture_flag_closes_on_anything_else(raw):
    assert parse_capture_flag(raw) is False


def test_capture_enabled_defaults_on_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv(PROMPT_CAPTURE_ENV, raising=False)

    assert capture_enabled() is True


def test_capture_enabled_reads_the_environment_when_no_value_is_given(monkeypatch):
    monkeypatch.setenv(PROMPT_CAPTURE_ENV, "off")

    assert capture_enabled() is False


def test_capture_enabled_prefers_an_explicit_value_over_the_environment(monkeypatch):
    monkeypatch.setenv(PROMPT_CAPTURE_ENV, "off")

    assert capture_enabled("on") is True


def test_resolve_capture_setting_lets_the_ui_short_circuit_the_environment():
    assert resolve_capture_setting(True, env_value="off") == (True, "ui")
    assert resolve_capture_setting(False, env_value="on") == (False, "ui")


def test_resolve_capture_setting_falls_back_to_the_environment():
    assert resolve_capture_setting(None, env_value="off") == (False, "env")
    assert resolve_capture_setting(None, env_value="yes") == (True, "env")


def test_resolve_capture_setting_reports_the_default_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv(PROMPT_CAPTURE_ENV, raising=False)

    assert resolve_capture_setting(None) == (True, "default")


def test_resolve_capture_setting_ignores_a_non_boolean_stored_value(monkeypatch):
    monkeypatch.delenv(PROMPT_CAPTURE_ENV, raising=False)

    assert resolve_capture_setting("yes") == (True, "default")


def test_capture_enabled_for_game_latches_on_either_signal():
    # The in-memory half decides a fresh game; the marker half is durable, so a
    # game that already wrote one corpus line keeps capturing after a resume.
    assert capture_enabled_for_game(True, False) is True
    assert capture_enabled_for_game(False, True) is True
    assert capture_enabled_for_game(False, False) is False


# ── PromptCapture: disabled ───────────────────────────────────

def test_disabled_capture_creates_no_file_and_writes_nothing(tmp_path):
    capture = PromptCapture(str(tmp_path), "g", record_enabled=False)
    capture.flush({"any": "record"})
    capture.close()

    assert prompt_capture_marker_exists(str(tmp_path), "g") is False
    assert not (tmp_path / "games").exists()


def test_disabled_capture_ignores_none_records(tmp_path):
    capture = PromptCapture(str(tmp_path), "g", record_enabled=False)
    capture.flush(None)

    assert not (tmp_path / "games").exists()


# ── PromptCapture: enabled ────────────────────────────────────

def test_enabled_capture_appends_one_json_line_per_record(tmp_path):
    capture = PromptCapture(str(tmp_path), "g")
    capture.flush({"round": 1})
    capture.flush(None)
    capture.flush({"round": 2})
    capture.close()

    assert read_lines(tmp_path) == [{"round": 1}, {"round": 2}]


def test_enabled_capture_writes_unicode_without_escaping(tmp_path):
    capture = PromptCapture(str(tmp_path), "g")
    capture.flush({"text": "狼人杀"})
    capture.close()

    raw = (tmp_path / "games" / "g" / PROMPT_CAPTURE_FILENAME).read_text(encoding="utf-8")
    assert "狼人杀" in raw
    assert raw.endswith("\n")


def test_capture_open_failure_degrades_without_raising(tmp_path, caplog):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        capture = PromptCapture(str(blocker), "g")
        capture.flush({"round": 1})
        capture.flush({"round": 2})
        capture.close()

    assert [item.getMessage() for item in caplog.records] == [
        "prompt capture degraded for game g; the game continues normally",
    ]


def test_flush_degrades_once_when_the_record_is_not_serializable(tmp_path, caplog):
    capture = PromptCapture(str(tmp_path), "g")

    with caplog.at_level(logging.WARNING):
        capture.flush({"bad": object()})
        capture.flush({"round": 1})

    assert [item.getMessage() for item in caplog.records] == [
        "prompt capture degraded for game g; the game continues normally",
    ]
    assert (tmp_path / "games" / "g" / PROMPT_CAPTURE_FILENAME).read_text(
        encoding="utf-8",
    ) == ""


def test_flush_degrades_on_a_short_write(tmp_path, monkeypatch, caplog):
    capture = PromptCapture(str(tmp_path), "g")
    monkeypatch.setattr(os, "write", lambda fd, payload: 0)

    with caplog.at_level(logging.WARNING):
        capture.flush({"round": 1})

    assert len(caplog.records) == 1


def test_degrade_swallows_a_failing_close(tmp_path, monkeypatch):
    capture = PromptCapture(str(tmp_path), "g")
    monkeypatch.setattr(os, "write", lambda fd, payload: 0)
    monkeypatch.setattr(os, "close", _raising_close)

    capture.flush({"round": 1})

    assert capture._fd is None


def test_close_is_idempotent_and_swallows_errors(tmp_path, monkeypatch):
    capture = PromptCapture(str(tmp_path), "g")
    capture.close()
    capture.close()

    still_open = PromptCapture(str(tmp_path), "g")
    monkeypatch.setattr(os, "close", _raising_close)
    still_open.close()

    assert still_open._fd is None


def test_degrade_skips_close_when_already_degraded(tmp_path):
    capture = PromptCapture(str(tmp_path), "g")
    capture._degrade()
    capture._degrade()

    assert capture._fd is None


def _raising_close(fd):
    raise OSError("close failed")
