"""旧档回放投影：把 ``games/<id>/game.log`` 与 ``conversation.log`` 的记录
转成与观众流同一套封闭事件词汇。

这里原来长在 ``api/routes/game_routes.py``；导出功能也要消费同一份投影，
按本仓库「不跨模块 import 私有名」的约定挪到服务层。行为与原实现逐字一致，
``game_routes`` 仍 re-export 这些名字，既有测试与 ``/logs`` 路由不受影响。
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.api import replay_diagnostics

_drop_replay_row = replay_diagnostics.drop

# 旧档回放的三份过滤器必须覆盖引擎真实产出的取值：命中不认识的死因/胜因是
# `return []`（丢整条记录），而不是跳过单条。`schemas.py` 的公开 DTO 是同一份
# 枚举的第二处副本，两处要一起改；`tests/test_game_routes.py` 逐个取值钉住。
_PUBLIC_DEATH_CAUSES = frozenset({
    "wolf_kill", "poison", "hunter_shot", "exile", "self_explode",
    "knight_duel", "charm", "wolf_king_shot", "reflect", "dream_death",
    "verify_kill", "hunt", "dance", "awakened_hunt", "stand_in", "bond_death",
    "miracle_backlash",
    "radiance_backlash",
    "love_death",
})
_PUBLIC_WINNING_CAMPS = frozenset({"good", "werewolf", "third_party"})
_PUBLIC_WIN_REASONS = frozenset({
    "all_gods_dead", "all_villagers_dead", "all_wolves_dead",
    "wolves_outnumber_good", "all_good_dead", "all_others_dead",
})

_PUBLIC_GAME_PHASES = frozenset({
    "waiting", "role_deal", "night", "dawn", "last_words",
    "sheriff_election", "speech", "vote_casting", "vote_resolution",
    "game_over", "error",
})

_AUDIENCE_ACTION_SCHEMAS = {
    "WEREWOLF_KILL": frozenset({"target_seat", "vote_counts"}),
    "WITCH_SAVE": frozenset({"target_seat"}),
    "WITCH_POISON": frozenset({"target_seat"}),
    "SEER_CHECK": frozenset({"target_seat", "result"}),
    "FOOL_SEER_CHECK": frozenset({"target_seat", "result"}),
    "HUNTER_SHOT": frozenset({"target_seat"}),
    # 觉醒猎人的「巡猎」：命令只带方向，落地的仍是 target_seat，公开形状与猎枪一致。
    "AWAKENED_HUNT": frozenset({"target_seat"}),
    "WRAITH_ILLUSION": frozenset({"seat", "target_seat", "round_number"}),
    "WOLF_KING_SHOT": frozenset({"target_seat"}),
    "GUARD_PROTECT": frozenset({"target_seat"}),
    "VOTE_REWOUND": frozenset({"seat", "round_number"}),
    "WOLF_CHAT_MESSAGE": frozenset({"seat", "text"}),
    "WOLF_VOTE": frozenset({"seat", "target_seat", "reasoning"}),
    "WITCH_THOUGHT": frozenset({"seat", "text"}),
    "SEER_THOUGHT": frozenset({"seat", "text"}),
    "EXILE_CANCELLED": frozenset({"target_seat", "round_number"}),
    "SELF_EXPLODE": frozenset({"seat", "target_seat", "round_number"}),
    "SHERIFF_ELECTED": frozenset({"seat", "round_number", "reason"}),
    "SHERIFF_BADGE": frozenset({"from_seat", "to_seat", "round_number"}),
    "SHERIFF_RUN": frozenset({"seat", "choice", "round_number"}),
    "SHERIFF_WITHDRAW": frozenset({"seat", "choice", "round_number"}),
    "SHERIFF_VOTE": frozenset({"voter_seat", "target_seat", "kind", "round_number"}),
    "SHERIFF_SIDE": frozenset({"seat", "side", "round_number"}),
    "PLAYER_REVEALED": frozenset({"seat_number", "role", "camp"}),
    # 骑士与狼美人的白天/夜晚技能，以及结算留给座位的状态标记。投影器声明它们公开
    # （frontend 也早已渲染），但旧档回放白名单此前漏了这四类 —— 旧档回退路径上它们
    # 直接消失。字段集取自真实 game.log。
    "KNIGHT_DUEL": frozenset({"seat", "target_seat", "camp", "round_number"}),
    "WOLF_BEAUTY_CHARM": frozenset({"seat", "target_seat", "round_number"}),
    "WOLF_BEAUTY_REVENGE": frozenset({"seat", "target_seat", "cause", "round_number"}),
    "STATUS_ADDED": frozenset({"seat", "status", "round_number"}),
    "CRIMSON_EXPLODE": frozenset({"seat", "silenced_seats", "round_number"}),
    "FRAGRANCE_BOND": frozenset({"seat", "target_seats", "round_number"}),
    "DANCE_POOL": frozenset({"seat", "pool_seats", "round_number"}),
    "MASK_GRANTED": frozenset({"seat", "target_seat", "in_pool", "round_number"}),
    "AWAKENED_WITCH_ASSIST_PICK": frozenset({
        "target_seat", "assistant_seats", "round_number",
    }),
    "AWAKENED_WITCH_ASSIST_CHAT": frozenset({
        "seat", "target_seat", "text", "round_number",
    }),
    "AWAKENED_WITCH_ASSIST_VOTE": frozenset({
        "seat", "target_seat", "vote", "reasoning", "round_number",
    }),
    "AWAKENED_WITCH_POISON_RESULT": frozenset({
        "target_seat", "result", "round_number",
    }),
    "COMMENTARY": frozenset({
        "round_number", "beat", "title", "text",
    }),
}

#: 引擎写进 game.log、但投影器**不**声明为公开的 audience_action 类型：它们只是引擎的
#: 编排标记（白天中断、放逐前的待定宣告）。旧档回放要静默跳过它们 —— 按「未知类型」计数
#: 会让每次放逐都写的 `EXILE_PENDING` 把丢弃计数刷成噪音。`VOTE_REWOUND` 已经公开，
#: 不再列在这里。
_INTERNAL_AUDIENCE_ACTION_TYPES = frozenset({
    "DAY_INTERRUPTED", "EXILE_PENDING",
})

#: 允许缺省的可选字段：`day_plan` 是后加的，历史日志里的狼聊没有它 —— 若把它写进必填集，
#: 所有旧记录都会从「少一个字段」变成「整条丢弃」。
_AUDIENCE_ACTION_OPTIONAL = {
    "WOLF_CHAT_MESSAGE": frozenset({"day_plan"}),
    # `reasoning`（警长的交/撕徽理由）同理：本仓库里早于它的警徽记录都没有这个键。
    "SHERIFF_BADGE": frozenset({"reasoning"}),
    # `weighted_counts`（半票加权计票）同理：早于它的票型记录只有人头数 `counts`。
    "VOTE_RESULT": frozenset({"weighted_counts"}),
    # `seat`：个体技能标明施法座位；狼窝集体刀没有。旧档没有这个键。
    "WEREWOLF_KILL": frozenset({"seat"}),
    "WITCH_SAVE": frozenset({"seat"}),
    "HUNTER_SHOT": frozenset({"seat"}),
    "AWAKENED_HUNT": frozenset({"seat"}),
    "WOLF_KING_SHOT": frozenset({"seat"}),
    # `intended_seat`：时间封锁改向时记下模型原目标。解药不改向，所以救人没有。
    "WITCH_POISON": frozenset({"seat", "intended_seat"}),
    "SEER_CHECK": frozenset({"seat", "intended_seat"}),
    "FOOL_SEER_CHECK": frozenset({"seat", "intended_seat"}),
    "GUARD_PROTECT": frozenset({"seat", "intended_seat"}),
    "COMMENTARY": frozenset({"model_id", "generated_at", "status", "failure_code"}),
}


def _payload_matches(event_type: str, payload: object) -> bool:
    """字段集判定：必填齐全，且不含必填与可选之外的字段。"""
    if not isinstance(payload, dict):
        return False
    required = _AUDIENCE_ACTION_SCHEMAS[event_type]
    optional = _AUDIENCE_ACTION_OPTIONAL.get(event_type, frozenset())
    return required <= set(payload) <= required | optional

_SHERIFF_RUN_CHOICES = frozenset({"run", "pass"})
_SHERIFF_WITHDRAW_CHOICES = frozenset({"stay", "withdraw"})
_SHERIFF_VOTE_KINDS = frozenset({"vote", "pk"})
_SHERIFF_SIDES = frozenset({
    "sheriff_left", "sheriff_right", "death_left", "death_right",
})

_REASONING_EVENT_SCHEMA = frozenset(
    {"seat", "action_type", "target_seat", "reasoning", "thought"}
)
_REASONING_EVENT_TYPES = {
    "HUNTER_REASONING": ("hunter_reasoning", frozenset({"shoot", "pass"})),
    "WOLF_KING_REASONING": (
        "wolf_king_reasoning",
        frozenset({"shoot", "pass", "sweep", "pass_claw", "explode"}),
    ),
    "DREAM_WEAVER_REASONING": ("dream_weaver_reasoning", frozenset({"dream", "pass"})),
    "GARGOYLE_REASONING": ("gargoyle_reasoning", frozenset({"check", "pass"})),
    "WITCH_REASONING": (
        "witch_reasoning", frozenset({"save", "poison", "pass", "assist"}),
    ),
    "SEER_REASONING": ("seer_reasoning", frozenset({"check", "pass"})),
    "GUARD_REASONING": ("guard_reasoning", frozenset({"guard", "pass"})),
    "WEREWOLF_KING_REASONING": ("werewolf_king_reasoning", frozenset({"explode", "pass"})),
    "KNIGHT_REASONING": ("knight_reasoning", frozenset({"duel", "pass"})),
    "WOLF_BEAUTY_REASONING": ("wolf_beauty_reasoning", frozenset({"charm", "pass"})),
    "PURE_WHITE_REASONING": ("pure_white_reasoning", frozenset({"check", "pass"})),
    "WOLF_WITCH_REASONING": ("wolf_witch_reasoning", frozenset({"check", "pass"})),
    "DEMON_HUNTER_REASONING": ("demon_hunter_reasoning", frozenset({"hunt", "pass"})),
    "CRIMSON_ENVOY_REASONING": (
        "crimson_envoy_reasoning", frozenset({"explode", "pass"}),
    ),
    "NIGHTMARE_REASONING": ("nightmare_reasoning", frozenset({"fear", "pass"})),
    "DANCER_REASONING": ("dancer_reasoning", frozenset({"pool", "pass"})),
    "MASK_REASONING": ("mask_reasoning", frozenset({"mask", "pass"})),
    "AWAKENED_HUNTER_REASONING": (
        "awakened_hunter_reasoning", frozenset({"hunt_forward", "hunt_backward", "pass"}),
    ),
    "AWAKENED_BEAUTY_REASONING": (
        "awakened_beauty_reasoning", frozenset({"charm", "pass"}),
    ),
    "AWAKENED_SEER_REASONING": (
        "awakened_seer_reasoning", frozenset({"check", "pass"}),
    ),
    "FRAGRANCE_PHANTOM_REASONING": (
        "fragrance_phantom_reasoning", frozenset({"bond", "pass"}),
    ),
    "MIRACLE_MERCHANT_REASONING": (
        "miracle_merchant_reasoning", frozenset({"grant_seer", "grant_poison", "grant_guard", "pass"}),
    ),
    "LUCKY_REASONING": (
        "lucky_reasoning", frozenset({"check", "poison", "guard", "pass"}),
    ),
    "MIRROR_GIRL_REASONING": (
        "mirror_girl_reasoning", frozenset({"inspect", "pass"}),
    ),
    "AWAKENED_HIDDEN_WOLF_REASONING": (
        "awakened_hidden_wolf_reasoning", frozenset({"imitate", "pass"}),
    ),
    "RADIANCE_EARL_REASONING": (
        "radiance_earl_reasoning", frozenset({"shield", "pass"}),
    ),
    "ECLIPSE_MAID_REASONING": (
        "eclipse_maid_reasoning", frozenset({"devour", "pass"}),
    ),
    "TIME_WOLF_REASONING": (
        "time_wolf_reasoning", frozenset({"lock", "pass"}),
    ),
    "ORDER_PRINCE_REASONING": (
        "order_prince_reasoning", frozenset({"rewind", "pass"}),
    ),
    "FOOL_SEER_REASONING": ("fool_seer_reasoning", frozenset({"check", "pass"})),
    "FOOL_SEER_REVEAL_REASONING": (
        "fool_seer_reveal_reasoning", frozenset({"reveal", "pass"}),
    ),
    "IRON_VILLAGER_REASONING": (
        "iron_villager_reasoning", frozenset({"reveal", "pass"}),
    ),
    "CROW_REASONING": ("crow_reasoning", frozenset({"curse", "pass"})),
    "CUPID_REASONING": ("cupid_reasoning", frozenset({"link", "pass"})),
    "ADMIRER_REASONING": ("admirer_reasoning", frozenset({"crush", "pass"})),
}

#: `degraded`（引擎代行的动作）是本仓库后加的键：旧记录里不存在，必须仍算合法载荷。
#: 推理类事件共 17 种，加上狼票；逐个手写只会让下一次新增角色再漏一处，所以从
#: `_REASONING_EVENT_TYPES` 派生。
_AUDIENCE_ACTION_OPTIONAL.update({
    name: frozenset({"degraded"}) for name in (*_REASONING_EVENT_TYPES, "WOLF_VOTE")
})


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _timestamp(record: dict[str, Any]) -> datetime | None:
    value = record.get("timestamp")
    if not isinstance(value, str) or ("T" not in value and " " not in value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public_timestamp(value: datetime) -> str:
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return _is_int(value) and value > 0


def _is_non_negative_int(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _is_reasoning_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 500


def _public_conversation_event(record: dict[str, Any]) -> dict | None:
    if record.get("scope") != "public" or _timestamp(record) is None:
        return None
    seat = record.get("speaker_seat")
    content = record.get("content")
    round_number = record.get("round_number")
    phase = record.get("phase")
    if not (
        _is_positive_int(seat)
        and isinstance(content, str)
        and _is_non_negative_int(round_number)
        and isinstance(phase, str)
        and phase in _PUBLIC_GAME_PHASES
    ):
        return None
    return {
        "event_type": "speech",
        "payload": {
            "player_seat": seat, "text": content, "round_number": round_number,
            "phase": phase,
        },
    }


def _public_reasoning_event(
    event_type: str, payload: dict[str, Any], round_number: int,
) -> list[dict]:
    """Project a role reasoning audience record into a closed night_thought event."""
    public_type, allowed_actions = _REASONING_EVENT_TYPES[event_type]
    seat = payload.get("seat")
    action = payload.get("action_type")
    target = payload.get("target_seat")
    reasoning = payload.get("reasoning")
    thought = payload.get("thought")
    if not _is_positive_int(seat):
        return []
    if not isinstance(action, str) or action not in allowed_actions:
        return []
    if target is not None and not _is_positive_int(target):
        return []
    if not _is_reasoning_text(reasoning) or not _is_reasoning_text(thought):
        return []
    public_payload: dict[str, Any] = {
        "round_number": round_number, "seat": seat,
        "action_type": public_type, "target_seat": target,
        "reasoning": reasoning,
    }
    # `degraded` is optional: every record written before it existed lacks the
    # key, and those must keep replaying as a seat's own decision.
    if payload.get("degraded") is True:
        public_payload["degraded"] = True
    return [{"event_type": "night_thought", "payload": public_payload}]


def _public_day_verdict_event(
    event_type: str, payload: dict[str, Any], round_number: int,
) -> list[dict]:
    """Project the daytime verdict events (a card flip that cancels an exile,
    a self-destruct, a public identity reveal) into closed audience events."""
    if event_type == "EXILE_CANCELLED":
        target = payload.get("target_seat")
        if not _is_positive_int(target):
            return []
        return [{"event_type": "exile_cancelled", "payload": {
            "round_number": round_number, "target_seat": target}}]
    if event_type == "SELF_EXPLODE":
        seat = payload.get("seat"); target = payload.get("target_seat")
        if not _is_positive_int(seat) or not _is_positive_int(target):
            return []
        return [{"event_type": "self_explode", "payload": {
            "round_number": round_number, "seat": seat, "target_seat": target}}]
    if event_type == "VOTE_REWOUND":
        seat = payload.get("seat")
        if not _is_positive_int(seat):
            return []
        return [{"event_type": "vote_rewound", "payload": {
            "round_number": round_number, "seat": seat}}]
    if event_type == "DANCE_POOL":
        seat = payload.get("seat")
        pool = payload.get("pool_seats")
        if not _is_positive_int(seat):
            return []
        if not isinstance(pool, (list, tuple)) or not pool or any(
            not _is_positive_int(item) for item in pool
        ):
            return []
        return [{"event_type": "dance_pool", "payload": {
            "round_number": round_number, "seat": seat,
            "pool_seats": list(pool)}}]
    if event_type == "MASK_GRANTED":
        seat = payload.get("seat"); target = payload.get("target_seat")
        in_pool = payload.get("in_pool")
        if not _is_positive_int(seat) or not _is_positive_int(target):
            return []
        if type(in_pool) is not bool:
            return []
        return [{"event_type": "mask_granted", "payload": {
            "round_number": round_number, "seat": seat,
            "target_seat": target, "in_pool": in_pool}}]
    if event_type == "CRIMSON_EXPLODE":
        seat = payload.get("seat")
        silence = payload.get("silenced_seats")
        if not _is_positive_int(seat):
            return []
        if not isinstance(silence, (list, tuple)) or any(
            not _is_positive_int(item) for item in silence
        ):
            return []
        return [{"event_type": "crimson_explode", "payload": {
            "round_number": round_number, "seat": seat,
            "silenced_seats": list(silence)}}]
    if event_type == "AWAKENED_WITCH_ASSIST_PICK":
        target = payload.get("target_seat")
        seats = payload.get("assistant_seats")
        if not _is_positive_int(target):
            return []
        if not isinstance(seats, (list, tuple)) or not seats or any(
            not _is_positive_int(item) for item in seats
        ):
            return []
        return [{"event_type": "awakened_witch_assist_pick", "payload": {
            "round_number": round_number, "target_seat": target,
            "assistant_seats": list(seats)}}]
    if event_type == "AWAKENED_WITCH_ASSIST_CHAT":
        seat = payload.get("seat"); target = payload.get("target_seat")
        text = payload.get("text")
        if not _is_positive_int(seat) or not _is_positive_int(target):
            return []
        if not isinstance(text, str) or not text:
            return []
        return [{"event_type": "awakened_witch_assist_chat", "payload": {
            "round_number": round_number, "seat": seat,
            "target_seat": target, "text": text}}]
    if event_type == "AWAKENED_WITCH_ASSIST_VOTE":
        seat = payload.get("seat"); target = payload.get("target_seat")
        vote = payload.get("vote"); reasoning = payload.get("reasoning")
        if not _is_positive_int(seat) or not _is_positive_int(target):
            return []
        if vote not in {"agree", "veto", "pass"}:
            return []
        if not isinstance(reasoning, str):
            return []
        return [{"event_type": "awakened_witch_assist_vote", "payload": {
            "round_number": round_number, "seat": seat, "target_seat": target,
            "vote": vote, "reasoning": reasoning}}]
    if event_type == "AWAKENED_WITCH_POISON_RESULT":
        target = payload.get("target_seat"); result = payload.get("result")
        if not _is_positive_int(target):
            return []
        if result not in {"passed", "vetoed", "incomplete", "aborted"}:
            return []
        return [{"event_type": "awakened_witch_poison_result", "payload": {
            "round_number": round_number, "target_seat": target,
            "result": result}}]
    if event_type == "SHERIFF_ELECTED":
        seat = payload.get("seat")
        reason = payload.get("reason")
        if seat is not None and not _is_positive_int(seat):
            return []
        if not isinstance(reason, str) or not reason:
            return []
        return [{"event_type": "sheriff_elected", "payload": {
            "round_number": round_number, "seat": seat, "reason": reason}}]
    if event_type == "SHERIFF_BADGE":
        from_seat = payload.get("from_seat"); to_seat = payload.get("to_seat")
        if not _is_positive_int(from_seat):
            return []
        if to_seat is not None and not _is_positive_int(to_seat):
            return []
        public_payload: dict[str, Any] = {
            "round_number": round_number, "from_seat": from_seat, "to_seat": to_seat,
        }
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, str) and reasoning and _is_reasoning_text(reasoning):
            # 警长为什么交徽/撕徽：观众流一直带它，早于它的日志记录没有这个键。
            public_payload["reasoning"] = reasoning
        return [{"event_type": "sheriff_badge", "payload": public_payload}]
    if event_type == "SHERIFF_RUN":
        seat = payload.get("seat"); choice = payload.get("choice")
        if not _is_positive_int(seat) or choice not in _SHERIFF_RUN_CHOICES:
            return []
        return [{"event_type": "sheriff_run", "payload": {
            "round_number": round_number, "seat": seat, "choice": choice}}]
    if event_type == "SHERIFF_WITHDRAW":
        seat = payload.get("seat"); choice = payload.get("choice")
        if not _is_positive_int(seat) or choice not in _SHERIFF_WITHDRAW_CHOICES:
            return []
        return [{"event_type": "sheriff_withdraw", "payload": {
            "round_number": round_number, "seat": seat, "choice": choice}}]
    if event_type == "SHERIFF_VOTE":
        voter = payload.get("voter_seat"); target = payload.get("target_seat")
        kind = payload.get("kind")
        if (
            not _is_positive_int(voter)
            or (target is not None and not _is_positive_int(target))
            or kind not in _SHERIFF_VOTE_KINDS
        ):
            return []
        return [{"event_type": "sheriff_vote", "payload": {
            "round_number": round_number, "voter_seat": voter,
            "target_seat": target, "kind": kind}}]
    if event_type == "SHERIFF_SIDE":
        seat = payload.get("seat"); side = payload.get("side")
        if not _is_positive_int(seat) or side not in _SHERIFF_SIDES:
            return []
        return [{"event_type": "sheriff_side", "payload": {
            "round_number": round_number, "seat": seat, "side": side}}]
    seat = payload.get("seat_number"); role = payload.get("role"); camp = payload.get("camp")
    if (not _is_positive_int(seat) or not isinstance(role, str) or not role
            or camp not in _PUBLIC_WINNING_CAMPS):
        return []
    return [{"event_type": "player_revealed", "payload": {
        "seat_number": seat, "role": role, "camp": camp}}]


def _public_role_skill_event(
    event_type: str, payload: dict[str, Any], round_number: int,
) -> list[dict]:
    """Project the knight's duel, the wolf beauty's charm/revenge and the status
    markers a settlement leaves on a seat into closed audience events.

    The audience stream already serves all four (the frontend renders them in the
    day cards, the seat display and the chronicle); the old-archive replay simply
    never learned them, so they vanished on that path.
    """
    if event_type == "FRAGRANCE_BOND":
        seat = payload.get("seat")
        seats = payload.get("target_seats")
        if not _is_positive_int(seat):
            _drop_replay_row("audience_action", "invalid_bond", event_type)
            return []
        if not isinstance(seats, (list, tuple)) or not seats or any(
            not _is_positive_int(item) for item in seats
        ):
            _drop_replay_row("audience_action", "invalid_bond_targets", event_type)
            return []
        return [{"event_type": "fragrance_bond", "payload": {
            "round_number": round_number, "seat": seat,
            "target_seats": list(seats)}}]
    if event_type == "KNIGHT_DUEL":
        seat = payload.get("seat"); target = payload.get("target_seat")
        camp = payload.get("camp")
        if (not _is_positive_int(seat) or not _is_positive_int(target)
                or camp not in _PUBLIC_WINNING_CAMPS):
            _drop_replay_row("audience_action", "invalid_duel", event_type)
            return []
        return [{"event_type": "knight_duel", "payload": {
            "round_number": round_number, "seat": seat,
            "target_seat": target, "camp": camp}}]
    if event_type == "WOLF_BEAUTY_CHARM":
        seat = payload.get("seat"); target = payload.get("target_seat")
        if not _is_positive_int(seat):
            _drop_replay_row("audience_action", "invalid_charm", event_type)
            return []
        if target is not None and not _is_positive_int(target):
            _drop_replay_row("audience_action", "invalid_charm_target", event_type)
            return []
        return [{"event_type": "wolf_beauty_charm", "payload": {
            "round_number": round_number, "seat": seat, "target_seat": target}}]
    if event_type == "WRAITH_ILLUSION":
        seat = payload.get("seat"); target = payload.get("target_seat")
        if not _is_positive_int(seat):
            _drop_replay_row("audience_action", "invalid_illusion", event_type)
            return []
        if target is not None and not _is_positive_int(target):
            _drop_replay_row("audience_action", "invalid_illusion_target", event_type)
            return []
        return [{"event_type": "wraith_illusion", "payload": {
            "round_number": round_number, "seat": seat, "target_seat": target}}]
    if event_type == "WOLF_BEAUTY_REVENGE":
        seat = payload.get("seat"); target = payload.get("target_seat")
        cause = payload.get("cause")
        if (not _is_positive_int(seat) or not _is_positive_int(target)
                or not isinstance(cause, str) or cause not in _PUBLIC_DEATH_CAUSES):
            _drop_replay_row("audience_action", "invalid_revenge", event_type)
            return []
        return [{"event_type": "wolf_beauty_revenge", "payload": {
            "round_number": round_number, "seat": seat,
            "target_seat": target, "cause": cause}}]
    seat = payload.get("seat"); status = payload.get("status")
    if not _is_positive_int(seat) or not isinstance(status, str) or not status:
        _drop_replay_row("audience_action", "invalid_status", event_type)
        return []
    # Same seat-key mapping the audience projector does: the settlement names the
    # seat `seat`, the public contract keeps the `player_seat` shape a death uses.
    return [{"event_type": "player_status", "payload": {
        "round_number": round_number, "player_seat": seat, "status": status}}]


def _public_audience_action_event(record: dict[str, Any], round_number: int) -> list[dict]:
    """Project one audience_action log record into a closed audience event."""
    data = record.get("data")
    if not isinstance(data, dict):
        _drop_replay_row("audience_action", "malformed_record")
        return []
    event_type = data.get("event_type")
    payload = data.get("payload")
    if not isinstance(event_type, str):
        _drop_replay_row("audience_action", "malformed_event_type", event_type)
        return []
    if event_type in ("WOLF_CHAT_MESSAGE", "WITCH_THOUGHT", "SEER_THOUGHT"):
        if not _payload_matches(event_type, payload):
            _drop_replay_row("audience_action", "malformed_payload", event_type)
            return []
        seat = payload.get("seat"); text = payload.get("text")
        # No length bound: the DTO accepts free text and the audience projection
        # serves it unbounded. The old 200-char cap silently dropped every longer
        # wolf message from the file replay (see `PublicWolfChatMessageResponse`).
        if not _is_positive_int(seat) or not isinstance(text, str) or not text:
            _drop_replay_row("audience_action", "invalid_seat_or_text", event_type)
            return []
        public_type = {"WOLF_CHAT_MESSAGE": "wolf_chat_message",
                       "WITCH_THOUGHT": "witch_thought",
                       "SEER_THOUGHT": "seer_thought"}[event_type]
        public_payload: dict[str, Any] = {
            "round_number": round_number, "seat": seat, "text": text,
        }
        day_plan = payload.get("day_plan")
        if isinstance(day_plan, str) and day_plan:
            # 狼队当夜商定的次日计划：观众流一直带它，旧档回放的日志记录此前根本没有。
            public_payload["day_plan"] = day_plan
        return [{"event_type": public_type, "payload": public_payload}]
    if event_type == "WOLF_VOTE":
        if not _payload_matches(event_type, payload):
            _drop_replay_row("audience_action", "malformed_payload", event_type)
            return []
        seat = payload.get("seat"); target = payload.get("target_seat")
        reasoning = payload.get("reasoning")
        if (not _is_positive_int(seat)
                or (target is not None and not _is_positive_int(target))
                or not _is_reasoning_text(reasoning)):
            _drop_replay_row("audience_action", "invalid_reasoning", event_type)
            return []
        return [{"event_type": "wolf_vote", "payload": {
            "round_number": round_number, "seat": seat,
            "target_seat": target, "reasoning": reasoning,
            # 同上：早于 `degraded` 的记录一律按「玩家自己的票」回放。
            **({"degraded": True} if payload.get("degraded") is True else {}),
        }}]
    if event_type in _REASONING_EVENT_TYPES:
        # 必填字段齐全即可；`degraded` 是本仓库后加的，旧记录没有这个键。
        if not isinstance(payload, dict) or not _REASONING_EVENT_SCHEMA <= set(payload) <= (
            _REASONING_EVENT_SCHEMA | {"degraded"}
        ):
            _drop_replay_row("audience_action", "malformed_payload", event_type)
            return []
        return _public_reasoning_event(event_type, payload, round_number)
    if event_type in ("KNIGHT_DUEL", "WOLF_BEAUTY_CHARM", "WOLF_BEAUTY_REVENGE",
                      "WRAITH_ILLUSION", "FRAGRANCE_BOND", "STATUS_ADDED"):
        if not _payload_matches(event_type, payload):
            _drop_replay_row("audience_action", "malformed_payload", event_type)
            return []
        return _public_role_skill_event(event_type, payload, round_number)
    if event_type in ("EXILE_CANCELLED", "SELF_EXPLODE", "CRIMSON_EXPLODE",
                      "DANCE_POOL", "MASK_GRANTED", "PLAYER_REVEALED",
                      "AWAKENED_WITCH_ASSIST_PICK", "AWAKENED_WITCH_ASSIST_CHAT",
                      "AWAKENED_WITCH_ASSIST_VOTE", "AWAKENED_WITCH_POISON_RESULT",
                      "SHERIFF_ELECTED", "SHERIFF_BADGE",
                      "SHERIFF_RUN", "SHERIFF_WITHDRAW", "SHERIFF_VOTE", "SHERIFF_SIDE",
                      "VOTE_REWOUND"):
        if not _payload_matches(event_type, payload):
            _drop_replay_row("audience_action", "malformed_payload", event_type)
            return []
        return _public_day_verdict_event(event_type, payload, round_number)
    if event_type in _INTERNAL_AUDIENCE_ACTION_TYPES:
        # 引擎的编排标记，不是「未知类型」：静默跳过，不计数（否则每次放逐的
        # EXILE_PENDING 会把丢弃计数刷成噪音）。
        return []
    if event_type == "COMMENTARY":
        if not _payload_matches(event_type, payload):
            _drop_replay_row("audience_action", "malformed_payload", event_type)
            return []
        beat = payload.get("beat")
        title = payload.get("title")
        text = payload.get("text")
        status = payload.get("status")
        if (
            beat not in {"night", "day", "final"}
            or not isinstance(title, str) or not (1 <= len(title) <= 100)
            or not isinstance(text, str) or not text
            or (status is not None and status not in {"ok", "failed"})
        ):
            _drop_replay_row("audience_action", "invalid_commentary", event_type)
            return []
        comm_payload: dict[str, Any] = {
            "round_number": round_number,
            "beat": beat,
            "title": title,
            "text": text,
        }
        for opt_key in ("model_id", "generated_at", "status", "failure_code"):
            val = payload.get(opt_key)
            if val is not None:
                if not isinstance(val, str):
                    _drop_replay_row("audience_action", "invalid_commentary", event_type)
                    return []
                comm_payload[opt_key] = val
        return [{"event_type": "commentary", "payload": comm_payload}]
    if event_type not in _AUDIENCE_ACTION_SCHEMAS:
        _drop_replay_row("audience_action", "unsupported_event_type", event_type)
        return []
    if not _payload_matches(event_type, payload):
        _drop_replay_row("audience_action", "malformed_payload", event_type)
        return []
    target = payload.get("target_seat")
    if not _is_positive_int(target):
        _drop_replay_row("audience_action", "invalid_target", event_type)
        return []
    public_payload: dict[str, Any] = {
        "action_type": event_type.lower(),
        "target_seat": target,
        "round_number": round_number,
    }
    for key in ("seat", "intended_seat"):
        value = payload.get(key)
        if value is None:
            continue
        if not _is_positive_int(value):
            _drop_replay_row("audience_action", f"invalid_{key}", event_type)
            return []
        public_payload[key] = value
    if event_type == "WEREWOLF_KILL":
        votes = payload.get("vote_counts")
        if not isinstance(votes, dict):
            _drop_replay_row("audience_action", "invalid_vote_counts", event_type)
            return []
        for seat_key, count in votes.items():
            if not isinstance(seat_key, str) or not seat_key.isdigit() or not _is_positive_int(count):
                _drop_replay_row("audience_action", "invalid_vote_counts", event_type)
                return []
        public_payload["vote_counts"] = votes
    if event_type == "SEER_CHECK":
        result = payload.get("result")
        if not isinstance(result, str) or result not in _PUBLIC_WINNING_CAMPS:
            _drop_replay_row("audience_action", "undeclared_camp", event_type)
            return []
        public_payload["result"] = result
    return [{"event_type": "night_action", "payload": public_payload}]


def _public_operation_events(record: dict[str, Any]) -> list[dict]:
    timestamp = _timestamp(record)
    operation = record.get("operation")
    round_number = record.get("round")
    phase = record.get("phase")
    data = record.get("data")
    if (
        timestamp is None
        or not _is_non_negative_int(round_number)
        or not isinstance(phase, str)
        or phase not in _PUBLIC_GAME_PHASES
        or not isinstance(data, dict)
    ):
        _drop_replay_row("replay", "malformed_envelope")
        return []

    if operation == "vote":
        seat = record.get("seat")
        target = data.get("target")
        if (
            phase != "vote_casting"
            or not _is_positive_int(seat)
            or (target is not None and not _is_positive_int(target))
        ):
            _drop_replay_row("vote", "invalid_vote")
            return []
        return [{
            "event_type": "vote",
            "payload": {"voter_seat": seat, "target_seat": target, "round_number": round_number},
        }]

    if operation == "vote_technical_abstain":
        seat = record.get("seat")
        failure_code = data.get("failure_code")
        if (
            phase != "vote_casting"
            or not _is_positive_int(seat)
            or not isinstance(failure_code, str)
            or not failure_code
        ):
            _drop_replay_row("vote_technical_abstain", "invalid_abstention")
            return []
        return [{
            "event_type": "technical_abstain",
            "payload": {
                "voter_seat": seat,
                "round_number": round_number,
                "failure_code": failure_code,
            },
        }]

    if operation == "vote_result":
        if "exiled" not in data:
            _drop_replay_row("vote_result", "malformed_payload")
            return []
        exiled = data.get("exiled")
        if phase != "vote_resolution" or (exiled is not None and (not _is_int(exiled) or exiled <= 0)):
            _drop_replay_row("vote_result", "invalid_vote_result")
            return []
        return [{
            "event_type": "vote_result",
            "payload": {"round_number": round_number, "exiled_seat": exiled},
        }]

    if operation == "night_deaths":
        if phase != "dawn" or not isinstance(data.get("deaths"), list):
            _drop_replay_row("night_deaths", "malformed_record", data.get("deaths"))
            return []
        events = []
        for death in data["deaths"]:
            if not isinstance(death, dict) or set(death) != {"player_seat", "cause", "round_number"}:
                _drop_replay_row("night_deaths", "malformed_row", death)
                continue
            if not (
                _is_positive_int(death["player_seat"])
                and _is_non_negative_int(death["round_number"])
            ):
                _drop_replay_row("night_deaths", "invalid_seat_or_round", death)
                continue
            if (
                not isinstance(death["cause"], str)
                or death["cause"] not in _PUBLIC_DEATH_CAUSES
            ):
                _drop_replay_row("night_deaths", "undeclared_cause", death["cause"])
                continue
            events.append({
                "event_type": "death",
                "payload": {
                    "player_seat": death["player_seat"],
                    "cause": death["cause"],
                    "round_number": death["round_number"],
                },
            })
        return events

    if operation == "audience_action":
        return _public_audience_action_event(record, round_number)

    if operation == "phase_change":
        new_phase = data.get("new_phase")
        if not isinstance(new_phase, str) or new_phase not in _PUBLIC_GAME_PHASES:
            _drop_replay_row("phase_change", "undeclared_phase")
            return []
        return [{
            "event_type": "phase",
            "payload": {"phase": new_phase, "round_number": round_number},
        }]

    if operation == "narration":
        title, text = data.get("title"), data.get("text")
        if (not isinstance(title, str) or not isinstance(text, str)
                or not title or not text or len(title) > 100 or len(text) > 200):
            _drop_replay_row("narration", "invalid_narration")
            return []
        return [{"event_type": "narration", "payload": {
            "round_number": round_number, "title": title, "text": text}}]

    if operation == "game_over":
        winner = data.get("winner")
        reason = data.get("reason")
        # 孤独少女的对局才写这个键；它必须逐个校验座位，坏数据只丢这一条记录。
        seats = data.get("winners")
        if seats is not None and (
            not isinstance(seats, list)
            or not seats
            or any(not _is_positive_int(seat) for seat in seats)
        ):
            _drop_replay_row("game_over", "invalid_winners", seats)
            return []
        if not isinstance(winner, str) or winner not in _PUBLIC_WINNING_CAMPS:
            _drop_replay_row("game_over", "undeclared_camp", winner)
            return []
        if reason is None:
            # An archive reconstructed from the manifest knows the camp but not the
            # reason (that only lives in the log). "Unknown" is absence, not a value
            # the vocabulary would have to carry -- `PublicWinnerResponse.reason` is
            # optional for exactly this case.
            return [{"event_type": "winner", "payload": {
                "winning_camp": winner,
                **({"winners": seats} if seats is not None else {}),
            }}]
        if not isinstance(reason, str) or reason not in _PUBLIC_WIN_REASONS:
            _drop_replay_row("game_over", "undeclared_reason", reason)
            return []
        return [{
            "event_type": "winner",
            "payload": {
                "winning_camp": winner, "reason": reason,
                **({"winners": seats} if seats is not None else {}),
            },
        }]

    return []


def _public_replay_events(conversations: list[dict], operations: list[dict]) -> list[dict]:
    ordered_events: list[tuple[datetime, int, dict]] = []
    order = 0
    for record in conversations:
        if not isinstance(record, dict):
            continue
        event = _public_conversation_event(record)
        timestamp = _timestamp(record)
        if event is not None and timestamp is not None:
            ordered_events.append((timestamp, order, event))
            order += 1
    for record in operations:
        if not isinstance(record, dict):
            continue
        timestamp = _timestamp(record)
        if timestamp is None:
            continue
        for event in _public_operation_events(record):
            ordered_events.append((timestamp, order, event))
            order += 1
    return [
        {**event, "timestamp": _public_timestamp(timestamp)}
        for timestamp, _, event in sorted(ordered_events, key=lambda item: (item[0], item[1]))
    ]
