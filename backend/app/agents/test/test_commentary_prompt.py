from __future__ import annotations

import pytest

from app.agents.commentary_prompt import (
    COMMENTARY_SCHEMA,
    COMMENTARY_SYSTEM_PROMPT,
    ROUND_DIGEST_SCHEMA,
    REVIEW_SCHEMA,
    beat_title,
    build_review_prompt,
    build_round_digest_prompt,
    build_segment_prompt,
    format_roster,
    format_transcript,
    normalize_review_payload,
    role_name_zh,
)


def test_role_name_zh_and_format_roster() -> None:
    assert role_name_zh("wolf-killer-werewolf") == "狼人"
    assert role_name_zh("unknown-role") == "unknown-role"
    assert role_name_zh(None) == "未知"

    roster = {
        "1": {"seat_number": 1, "role": "wolf-killer-seer", "camp": "good", "is_alive": True},
        "2": {"seat_number": 2, "role": "wolf-killer-werewolf", "camp": "werewolf", "is_alive": False},
        "bad": "not-a-dict",
    }
    text = format_roster(roster)
    assert "1号：预言家（好人，存活）" in text
    assert "2号：狼人（狼人，出局）" in text
    assert format_roster({}) == "暂无座位信息"


def test_beat_title_covers_all_beats() -> None:
    assert beat_title(1, "night") == "第1夜 · 夜话解说"
    assert beat_title(2, "day") == "第2天 · 日评解说"
    assert beat_title(3, "final") == "终局解说"
    assert beat_title(4, "other") == "第4轮 · 解说"


def test_format_transcript_renders_all_audience_event_shapes_and_truncates() -> None:
    roles = {1: "wolf-killer-seer", 2: "wolf-killer-werewolf", 3: "wolf-killer-witch"}
    events = [
        {"event_type": "execution_state", "payload": {}},
        {"event_type": "speaking", "payload": {"seat": 1, "round_number": 1}},
        {"event_type": "commentary", "payload": {"text": "skip"}},
        {"event_type": "game_initialized", "payload": {}},
        {"event_type": "phase", "payload": {"phase": "night", "round_number": 1}},
        {"event_type": "narration", "payload": {"round_number": 1, "title": "夜", "text": "入夜"}},
        {
            "event_type": "wolf_chat_message",
            "payload": {"round_number": 1, "seat": 2, "text": "刀1号", "day_plan": "悍跳"},
        },
        {
            "event_type": "wolf_vote",
            "payload": {"round_number": 1, "seat": 2, "target_seat": 1, "reasoning": "疑似神"},
        },
        {
            "event_type": "wolf_vote",
            "payload": {"round_number": 1, "seat": 2, "target_seat": None, "reasoning": "空刀", "degraded": True},
        },
        {
            "event_type": "night_thought",
            "payload": {"round_number": 1, "seat": 1, "action_type": "seer_reasoning", "target_seat": 2, "reasoning": "验2"},
        },
        {
            "event_type": "night_action",
            "payload": {"round_number": 1, "action_type": "seer_check", "seat": 1, "target_seat": 2, "result": "werewolf"},
        },
        {
            "event_type": "death",
            "payload": {"round_number": 1, "player_seat": "unknown", "cause": "wolf_kill"},
        },
        {
            "event_type": "death",
            "payload": {"round_number": 1, "player_seat": 3, "cause": "wolf_kill"},
        },
        {
            "event_type": "speech",
            "payload": {"round_number": 1, "player_seat": 1, "text": "A" * 400, "phase": "speech"},
        },
        {
            "event_type": "vote",
            "payload": {"round_number": 1, "voter_seat": 1, "target_seat": 2},
        },
        {
            "event_type": "vote",
            "payload": {"round_number": 1, "voter_seat": 3, "target_seat": None},
        },
        {
            "event_type": "vote_result",
            "payload": {"round_number": 1, "exiled_seat": 2, "counts": {"2": 1}},
        },
        {
            "event_type": "vote_result",
            "payload": {"round_number": 1, "exiled_seat": None, "counts": {}},
        },
        {
            "event_type": "winner",
            "payload": {"winning_camp": "good", "reason": "all_wolves_dead"},
        },
        {
            "event_type": "knight_duel",
            "payload": {"round_number": 1, "seat": 1, "target_seat": 2, "camp": "werewolf"},
        },
        {"event_type": "not-a-dict", "payload": None},
        "not-a-mapping",
    ]
    out = format_transcript(events, roles, max_speech_chars=50, max_total_chars=5000)
    assert "【狼聊】2号[狼人]：刀1号（次日计划：悍跳）" in out
    assert "【狼票】2号[狼人] → 1号（疑似神）" in out
    assert "【狼票】2号[狼人] → 弃票[代行]（空刀）" in out
    assert "【心声】1号[预言家](seer_reasoning→2)：验2" in out
    assert "【夜间行动】seer_check 1号 → 2号 (werewolf)" in out
    assert "【出局】3号[女巫]（死因：wolf_kill）" in out
    assert "…" in out
    assert "【投票】1号[预言家] → 2号" in out
    assert "【投票】3号[女巫] → 弃票" in out
    assert "【放逐结果】2号出局" in out
    assert "【放逐结果】平票无人出局" in out
    assert "【终局】好人获胜（all_wolves_dead）" in out
    assert "【事件】knight_duel" in out

    capped = format_transcript(events, roles, max_speech_chars=50, max_total_chars=80)
    assert len(capped) <= 100
    assert capped.endswith("…（已截断）")
    assert format_transcript([], roles) == "（本段无事件记录）"


def test_build_prompts_and_schemas() -> None:
    assert "全知视角" in COMMENTARY_SYSTEM_PROMPT
    assert COMMENTARY_SCHEMA["required"] == ["text"]
    assert ROUND_DIGEST_SCHEMA["required"] == ["digest"]
    assert "overview" in REVIEW_SCHEMA["required"]

    seg_prompt = build_segment_prompt(
        round_number=1,
        beat="night",
        roster_text="1号：预言家",
        transcript_text="【夜间行动】...",
    )
    assert "第1夜" in seg_prompt
    assert "1号：预言家" in seg_prompt

    day_prompt = build_segment_prompt(
        round_number=2,
        beat="day",
        roster_text="1号：预言家",
        transcript_text="【发言】...",
    )
    assert "第2天白天" in day_prompt

    final_prompt = build_segment_prompt(
        round_number=3,
        beat="final",
        roster_text="1号：预言家",
        transcript_text="【终局】...",
    )
    assert "终局收尾" in final_prompt

    digest_prompt = build_round_digest_prompt(
        round_number=1,
        roster_text="1号：预言家",
        transcript_text="【发言】...",
    )
    assert "第 1 轮" in digest_prompt

    review_prompt = build_review_prompt(
        roster_text="1号：预言家",
        outcome_text="好人阵营获胜（all_wolves_dead）",
        round_digests=[(1, "第一轮摘要"), (2, "第二轮摘要")],
    )
    assert "第1轮摘要：第一轮摘要" in review_prompt
    assert "好人阵营获胜" in review_prompt


def test_normalize_review_payload_handles_full_and_fallback_shapes() -> None:
    raw = {
        "overview": "精彩对局",
        "mvp": {"seat": 1, "role": "预言家", "reason": "精准查验"},
        "turning_points": [{"round": 2, "title": "关键放逐", "detail": "投出狼王"}, "skip-bad"],
        "mistakes": [{"seat": 4, "title": "误毒", "detail": "毒走守卫"}, "skip-bad"],
        "camp_assessment": {"good": "配合默契", "werewolf": "悍跳失败", "third_party": ""},
        "closing": "好人完胜",
    }
    norm = normalize_review_payload(raw, round_digests=[{"round": 1, "digest": "d1"}])
    assert norm["overview"] == "精彩对局"
    assert norm["mvp"] == {"seat": 1, "role": "预言家", "reason": "精准查验"}
    assert len(norm["turning_points"]) == 1
    assert len(norm["mistakes"]) == 1
    assert norm["camp_assessment"] == {"good": "配合默契", "werewolf": "悍跳失败", "third_party": ""}
    assert norm["closing"] == "好人完胜"
    assert norm["round_digests"] == [{"round": 1, "digest": "d1"}]

    no_tp = normalize_review_payload(
        {"camp_assessment": {"good": "g", "werewolf": "w"}}, round_digests=[],
    )
    assert "third_party" not in no_tp["camp_assessment"]

    fallback = normalize_review_payload({"unexpected": "value"}, round_digests=[])
    assert fallback["overview"] == "暂无综述"
    assert fallback["mvp"] is None
    assert fallback["turning_points"] == []
    assert fallback["mistakes"] == []
    assert fallback["camp_assessment"] == {"good": "", "werewolf": ""}
    assert fallback["closing"] == ""
