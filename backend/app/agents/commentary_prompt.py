"""Prompts, transcript formatters, and schemas for god-view commentary and post-game review."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.catalog import ROLE_METADATA

COMMENTARY_SYSTEM_PROMPT = (
    "你是狼人杀对局的【全知视角（上帝视角）解说员与赛后复盘分析师】。\n"
    "你可以看到所有座位的真实底牌身份、狼人夜间密谈、神职夜间行动与内心独白，以及白天的公开发言与投票。\n"
    "核心要求：\n"
    "1. 只依据提供的对局记录解说，严禁编造未发生的发言、查验或技能结果；\n"
    "2. 点明信息差（例如谁在悍跳、谁被蒙在鼓里、哪一刀或哪一票改变了局势）；\n"
    "3. 语言简练有感染力，使用中文，称呼玩家为「X号[角色名]」。"
)

COMMENTARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "120～260字的上帝视角解说正文，直接给出解说内容。",
        },
    },
    "required": ["text"],
}

ROUND_DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "digest": {
            "type": "string",
            "description": "150～300字概括本轮夜间行动、白天焦点发言、放逐票型与局势变化。",
        },
    },
    "required": ["digest"],
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": {
            "type": "string",
            "description": "200～400字整局走势综述。",
        },
        "mvp": {
            "type": "object",
            "properties": {
                "seat": {"type": "integer"},
                "role": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["seat", "role", "reason"],
        },
        "turning_points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "round": {"type": "integer"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["round", "title", "detail"],
            },
        },
        "mistakes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "seat": {"type": "integer"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["seat", "title", "detail"],
            },
        },
        "camp_assessment": {
            "type": "object",
            "properties": {
                "good": {"type": "string"},
                "werewolf": {"type": "string"},
                "third_party": {"type": "string"},
            },
            "required": ["good", "werewolf"],
        },
        "closing": {
            "type": "string",
            "description": "一句话总结。",
        },
    },
    "required": [
        "overview",
        "mvp",
        "turning_points",
        "mistakes",
        "camp_assessment",
        "closing",
    ],
}

_SKIPPED_EVENT_TYPES = frozenset({
    "execution_state",
    "speaking",
    "commentary",
    "game_initialized",
    "phase",
    "narration",
})

_CAMP_ZH = {
    "good": "好人",
    "werewolf": "狼人",
    "third_party": "第三方",
}


def role_name_zh(role_id: str | None) -> str:
    if not isinstance(role_id, str) or not role_id:
        return "未知"
    meta = ROLE_METADATA.get(role_id)
    if isinstance(meta, Mapping) and isinstance(meta.get("name_zh"), str):
        return str(meta["name_zh"])
    return role_id


def format_roster(players: Mapping[str, Any] | Mapping[int, Any]) -> str:
    lines: list[str] = []
    for key, raw in sorted(players.items(), key=lambda item: str(item[0])):
        if not isinstance(raw, Mapping):
            continue
        seat = raw.get("seat_number", key)
        role_zh = role_name_zh(raw.get("role"))
        camp_zh = _CAMP_ZH.get(str(raw.get("camp", "")), str(raw.get("camp", "未知")))
        alive_zh = "存活" if raw.get("is_alive", True) else "出局"
        lines.append(f"{seat}号：{role_zh}（{camp_zh}，{alive_zh}）")
    return "\n".join(lines) if lines else "暂无座位信息"


def beat_title(round_number: int, beat: str) -> str:
    if beat == "night":
        return f"第{round_number}夜 · 夜话解说"
    if beat == "day":
        return f"第{round_number}天 · 日评解说"
    if beat == "final":
        return "终局解说"
    return f"第{round_number}轮 · 解说"


def _seat_tag(seat: object, roles_by_seat: Mapping[int, str]) -> str:
    if type(seat) is int:
        role_zh = role_name_zh(roles_by_seat.get(seat))
        return f"{seat}号[{role_zh}]"
    return f"{seat}号"


def _truncate_text(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "…"


def _format_event_line(
    event_type: str,
    payload: Mapping[str, Any],
    roles_by_seat: Mapping[int, str],
    max_speech_chars: int,
) -> str:
    if event_type == "wolf_chat_message":
        seat = _seat_tag(payload.get("seat"), roles_by_seat)
        text = _truncate_text(str(payload.get("text", "")), max_speech_chars)
        plan = payload.get("day_plan")
        plan_suffix = f"（次日计划：{plan}）" if isinstance(plan, str) and plan else ""
        return f"【狼聊】{seat}：{text}{plan_suffix}"
    if event_type == "wolf_vote":
        seat = _seat_tag(payload.get("seat"), roles_by_seat)
        target = payload.get("target_seat")
        target_str = f"{target}号" if type(target) is int else "弃票"
        degraded = "[代行]" if payload.get("degraded") is True else ""
        reason = _truncate_text(str(payload.get("reasoning", "")), max_speech_chars)
        return f"【狼票】{seat} → {target_str}{degraded}（{reason}）"
    if event_type == "night_thought":
        seat = _seat_tag(payload.get("seat"), roles_by_seat)
        action = payload.get("action_type", "")
        target = payload.get("target_seat")
        reason = _truncate_text(str(payload.get("reasoning", "")), max_speech_chars)
        return f"【心声】{seat}({action}→{target})：{reason}"
    if event_type == "night_action":
        action = payload.get("action_type", "")
        actor = payload.get("seat")
        actor_str = f" {actor}号" if type(actor) is int else ""
        target = payload.get("target_seat")
        result = payload.get("result")
        res_str = f" ({result})" if isinstance(result, str) and result else ""
        return f"【夜间行动】{action}{actor_str} → {target}号{res_str}"
    if event_type == "death":
        seat = _seat_tag(payload.get("player_seat"), roles_by_seat)
        cause = payload.get("cause", "")
        return f"【出局】{seat}（死因：{cause}）"
    if event_type == "speech":
        seat = _seat_tag(payload.get("player_seat"), roles_by_seat)
        text = _truncate_text(str(payload.get("text", "")), max_speech_chars)
        return f"【发言】{seat}：{text}"
    if event_type == "vote":
        voter = _seat_tag(payload.get("voter_seat"), roles_by_seat)
        target = payload.get("target_seat")
        target_str = f"{target}号" if type(target) is int else "弃票"
        return f"【投票】{voter} → {target_str}"
    if event_type == "vote_result":
        exiled = payload.get("exiled_seat")
        if type(exiled) is int:
            return f"【放逐结果】{exiled}号出局"
        return "【放逐结果】平票无人出局"
    if event_type == "winner":
        camp = _CAMP_ZH.get(str(payload.get("winning_camp", "")), str(payload.get("winning_camp", "")))
        reason = payload.get("reason", "")
        return f"【终局】{camp}获胜（{reason}）"
    return f"【事件】{event_type}: {dict(payload)}"


def format_transcript(
    events: Iterable[Mapping[str, Any]],
    roles_by_seat: Mapping[int, str],
    *,
    max_speech_chars: int = 300,
    max_total_chars: int = 12000,
) -> str:
    lines: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(event_type, str) or event_type in _SKIPPED_EVENT_TYPES:
            continue
        if not isinstance(payload, Mapping):
            continue
        lines.append(
            _format_event_line(event_type, payload, roles_by_seat, max_speech_chars)
        )
    if not lines:
        return "（本段无事件记录）"
    joined = "\n".join(lines)
    if len(joined) <= max_total_chars:
        return joined
    return joined[:max_total_chars] + "…（已截断）"


def build_segment_prompt(
    *,
    round_number: int,
    beat: str,
    roster_text: str,
    transcript_text: str,
) -> str:
    if beat == "night":
        stage_desc = f"第{round_number}夜的夜间博弈与黎明结果"
    elif beat == "day":
        stage_desc = f"第{round_number}天白天的公开发言、归票与放逐结果"
    else:
        stage_desc = f"第{round_number}轮终局收尾与胜负揭晓"
    return (
        f"请以全知视角解说本局【{stage_desc}】（120～260字）。\n\n"
        f"【场上座位与底牌】\n{roster_text}\n\n"
        f"【本段事件记录】\n{transcript_text}\n"
    )


def build_round_digest_prompt(
    *,
    round_number: int,
    roster_text: str,
    transcript_text: str,
) -> str:
    return (
        f"请概括本局【第 {round_number} 轮】的关键夜间行动、白天对跳与发言焦点、投票结果及局势影响（150～300字）。\n\n"
        f"【场上座位与底牌】\n{roster_text}\n\n"
        f"【本轮事件记录】\n{transcript_text}\n"
    )


def build_review_prompt(
    *,
    roster_text: str,
    outcome_text: str,
    round_digests: Iterable[tuple[int, str]],
) -> str:
    digest_block = "\n".join(
        f"- 第{rnd}轮摘要：{text}" for rnd, text in round_digests
    )
    return (
        "请根据以下全知视角底牌、终局结果与逐轮摘要，生成结构化赛后复盘报告：\n"
        "- overview：200～400字整局复盘综述\n"
        "- mvp：全场最佳座位（seat、role、reason）\n"
        "- turning_points：1～4个关键胜负手/转折点（round、title、detail）\n"
        "- mistakes：0～4个关键失误或误判（seat、title、detail）\n"
        "- camp_assessment：好人阵营（good）与狼人阵营（werewolf）整体表现点评，若有第三方填 third_party\n"
        "- closing：一句话总结本局\n\n"
        f"【终局结果】\n{outcome_text}\n\n"
        f"【场上座位与底牌】\n{roster_text}\n\n"
        f"【逐轮摘要】\n{digest_block}\n"
    )


def normalize_review_payload(
    raw: Mapping[str, Any],
    *,
    round_digests: list[dict[str, Any]],
) -> dict[str, Any]:
    overview = raw.get("overview")
    overview_str = str(overview).strip() if isinstance(overview, str) and overview.strip() else "暂无综述"

    mvp_raw = raw.get("mvp")
    mvp: dict[str, Any] | None = None
    if isinstance(mvp_raw, Mapping) and type(mvp_raw.get("seat")) is int:
        mvp = {
            "seat": int(mvp_raw["seat"]),
            "role": str(mvp_raw.get("role") or ""),
            "reason": str(mvp_raw.get("reason") or ""),
        }

    turning_points: list[dict[str, Any]] = []
    tp_raw = raw.get("turning_points")
    if isinstance(tp_raw, list):
        for item in tp_raw:
            if isinstance(item, Mapping) and type(item.get("round")) is int:
                turning_points.append({
                    "round": int(item["round"]),
                    "title": str(item.get("title") or ""),
                    "detail": str(item.get("detail") or ""),
                })

    mistakes: list[dict[str, Any]] = []
    ms_raw = raw.get("mistakes")
    if isinstance(ms_raw, list):
        for item in ms_raw:
            if isinstance(item, Mapping) and type(item.get("seat")) is int:
                mistakes.append({
                    "seat": int(item["seat"]),
                    "title": str(item.get("title") or ""),
                    "detail": str(item.get("detail") or ""),
                })

    camp_raw = raw.get("camp_assessment")
    camp_assessment: dict[str, str] = {"good": "", "werewolf": ""}
    if isinstance(camp_raw, Mapping):
        camp_assessment["good"] = str(camp_raw.get("good") or "")
        camp_assessment["werewolf"] = str(camp_raw.get("werewolf") or "")
        if "third_party" in camp_raw and isinstance(camp_raw.get("third_party"), str):
            camp_assessment["third_party"] = str(camp_raw["third_party"])

    closing = raw.get("closing")
    closing_str = str(closing).strip() if isinstance(closing, str) else ""

    return {
        "overview": overview_str,
        "mvp": mvp,
        "turning_points": turning_points,
        "mistakes": mistakes,
        "camp_assessment": camp_assessment,
        "closing": closing_str,
        "round_digests": list(round_digests),
    }
