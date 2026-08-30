from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from app.core.conversation_log import ConversationLog
from app.models.conversation import ConversationScope
from app.models.game import GameState
from app.models.pipeline import ActionCommand
from app.roles.registry import RegistrySnapshot
from app.agents.game_rules import TARGET_SELECTION_RULE
from app.agents.output_parser import extract_json_object

logger = logging.getLogger(__name__)

_MAX_UTTERANCE = 200
_MAX_DAY_PLAN = 150

_WOLF_DISCUSSION_TOOL_NAME = "werewolf_discussion"
_WOLF_VOTE_TOOL_NAME = "werewolf_kill"


def _wolf_discussion_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "speak": {"type": "boolean"},
            "text": {"type": "string", "maxLength": _MAX_UTTERANCE},
            "preferred_target": {"type": ["integer", "null"]},
            "day_plan": {"type": "string", "maxLength": _MAX_DAY_PLAN},
        },
        "required": ["speak", "text", "preferred_target", "day_plan"],
    }


def _wolf_vote_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "action_type": {"type": "string", "enum": ["kill", "pass"]},
            "target_seat": {"type": ["integer", "null"]},
            "reasoning": {"type": "string", "maxLength": 500},
        },
        "required": [
            "schema_version", "action_type", "target_seat", "reasoning",
        ],
    }

_CHINESE_DIRECTIVE = (
    "IMPORTANT: Every piece of text you produce (message, reasoning, thought) "
    "MUST be written in Simplified Chinese (简体中文)."
)

_NO_FABRICATION_RULE = (
    "Base every claim ONLY on the information provided in this prompt: the "
    "alive players, the discussion log, the day recap, the wolf channel "
    "history and your own thoughts. You have no knowledge beyond this prompt. "
    "NEVER invent or imply facts that are not provided "
    '(for example claiming that someone is "active" or "talkative"); if no such '
    "information exists, reason from objective facts only. "
)

_FIRST_NIGHT_NOTICE = (
    "注意：这是第 1 晚，白天尚未开始，所有玩家都没有任何发言记录。"
    "不要引用或暗示任何玩家此前的行为特征（如\"活跃\"\"话多\"\"像有身份\"），"
    "只能依据座位位置等客观信息选择目标。"
    "首夜没有信息也没有倾向，直接选择一个目标即可；没有思路时随机选一个。\n"
)

_TARGET_RULE = TARGET_SELECTION_RULE


_MAX_BRIEFING_LINES = 20
_MAX_BRIEFING_THOUGHTS = 5
_MAX_BRIEFING_LINE = 200


@dataclass(frozen=True)
class NightBriefing:
    """Frozen context lines handed to the wolves' nightly prompts.

    Lines are pre-formatted plain text derived from the conversation log:
    prior rounds' public day records, prior nights' wolf channel records and
    the acting wolf's own thoughts.
    """

    public_lines: tuple[str, ...] = ()
    wolf_lines: tuple[str, ...] = ()
    thoughts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("public_lines", "wolf_lines", "thoughts"):
            value = getattr(self, name)
            if type(value) is not tuple or any(type(item) is not str for item in value):
                raise TypeError(f"{name} must be a tuple of strings")


def _briefing_line(record: object, label: str) -> str:
    speaker = f"{record.speaker_seat}号" if record.speaker_seat else "系统"
    content = record.content
    if len(content) > _MAX_BRIEFING_LINE:
        content = content[:_MAX_BRIEFING_LINE] + "..."
    return f"第{record.round_number}轮{label} {speaker}：{content}"


def build_briefing(log: ConversationLog, seat: int, round_num: int) -> NightBriefing:
    """Build the wolves' nightly briefing from prior conversation records.

    Only records strictly before the current round are included so the live
    discussion history (passed separately) is never duplicated; thoughts are
    always the acting wolf's own.
    """
    if type(log) is not ConversationLog:
        raise TypeError("log must be a ConversationLog")
    if type(seat) is not int or seat <= 0:
        raise ValueError("seat must be a positive integer")
    if type(round_num) is not int or round_num < 0:
        raise ValueError("round_num must be a non-negative integer")
    public: list[str] = []
    wolf: list[str] = []
    for record in log.get_all():
        if record.round_number >= round_num:
            continue
        if record.scope is ConversationScope.PUBLIC:
            public.append(_briefing_line(record, "公开"))
        elif record.scope is ConversationScope.WEREWOLF:
            wolf.append(_briefing_line(record, "狼队频道"))
    thoughts = [
        f"第{thought.round_number}轮[{thought.phase}]：{thought.content[:_MAX_BRIEFING_LINE]}"
        for thought in log.get_thoughts_for_seat(seat)[-_MAX_BRIEFING_THOUGHTS:]
    ]
    return NightBriefing(
        tuple(public[-_MAX_BRIEFING_LINES:]),
        tuple(wolf[-_MAX_BRIEFING_LINES:]),
        tuple(thoughts),
    )


def _night_notice(state: GameState) -> str:
    return _FIRST_NIGHT_NOTICE if state.round_number == 1 else ""

_NARRATIONS: dict[str, tuple[str, str]] = {
    "guard_open": ("守卫请睁眼", "请选择今晚要守护的玩家。"),
    "wolf_open": ("天黑请闭眼", "狼人请睁眼，开始讨论今晚的行动。"),
    "seer_open": ("预言家请睁眼", "请查验一名玩家的身份。"),
}


def _clean(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be a string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be UTF-8") from error
    if not value or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class DiscussionTurn:
    seat: int
    spoke: bool
    text: str = ""
    preferred_target: Optional[int] = None
    day_plan: str = ""

    def __post_init__(self) -> None:
        if type(self.seat) is not int or self.seat <= 0:
            raise ValueError("invalid seat")
        if type(self.spoke) is not bool:
            raise TypeError("spoke must be bool")
        if self.preferred_target is not None and (
            type(self.preferred_target) is not int
            or not 1 <= self.preferred_target <= 2_147_483_647
        ):
            raise ValueError("invalid preferred target")
        if type(self.day_plan) is not str:
            raise TypeError("day_plan must be a string")
        if len(self.day_plan) > _MAX_DAY_PLAN:
            raise ValueError("day_plan is too long")
        if self.spoke:
            _clean(self.text, "text", _MAX_UTTERANCE)
        else:
            if self.text != "":
                raise ValueError("skipped turn cannot carry text")
            if self.preferred_target is not None:
                raise ValueError("skipped turn cannot carry target")
            if self.day_plan != "":
                raise ValueError("skipped turn cannot carry day plan")


@dataclass(frozen=True)
class WolfVote:
    seat: int
    action_type: str
    target_seat: Optional[int]
    reasoning: str

    def __post_init__(self) -> None:
        if type(self.seat) is not int or self.seat <= 0:
            raise ValueError("invalid seat")
        if self.action_type not in ("kill", "pass"):
            raise ValueError("invalid action_type")
        if self.action_type == "kill" and (type(self.target_seat) is not int or self.target_seat <= 0):
            raise ValueError("kill requires target")
        if self.action_type == "pass" and self.target_seat is not None:
            raise ValueError("pass cannot have target")
        _clean(self.reasoning, "reasoning", 500)

    def to_command(self) -> ActionCommand:
        return ActionCommand(
            action_type=self.action_type,
            target_seat=self.target_seat,
            reasoning=self.reasoning,
        )


class NightDirector:
    """Pure night-flow orchestrator: builds prompts, parses LLM answers with
    safe fallbacks, produces narration texts, and hands the engine the
    pre-collected wolf votes for the aggregate contract point."""

    def __init__(
        self,
        snapshot: RegistrySnapshot,
        invoke: Callable[
            [list[dict[str, str]], str, dict[str, object], int], str
        ],
    ) -> None:
        if type(snapshot) is not RegistrySnapshot:
            raise TypeError("snapshot must be RegistrySnapshot")
        if not callable(invoke):
            raise TypeError("invoke must be callable")
        self._snapshot = snapshot
        self._invoke = invoke
        self._collected_votes: dict[int, WolfVote] = {}

    # ── prompts ────────────────────────────────────────────────

    @staticmethod
    def _messages(system: str, human: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": human}]

    @staticmethod
    def _alive_text(state: GameState) -> str:
        seats = sorted(state.alive_players())
        return "、".join(f"{seat}号" for seat in seats)

    def _wolf_team(self, state: GameState) -> list[int]:
        return sorted(
            seat for seat, player in state.players.items()
            if player.role == "wolf-killer-werewolf" and player.is_alive
        )

    def _history_text(self, history: Sequence[str]) -> str:
        return "\n".join(f"{index + 1}. {line}" for index, line in enumerate(history))

    @staticmethod
    def _briefing_text(lines: Sequence[str], empty: str) -> str:
        return "\n".join(lines) if lines else empty

    def _prior_votes_text(self, votes: Sequence[WolfVote]) -> str:
        if not votes:
            return "（还没有人出票）"
        return "\n".join(
            f"{vote.seat}号：{'刀 ' + str(vote.target_seat) + ' 号' if vote.action_type == 'kill' else '弃权'}（{vote.reasoning}）"
            for vote in votes
        )

    @staticmethod
    def _fallback_hint(random_hint: int | None) -> str:
        """Render a server-selected legal fallback target, when one is needed."""
        if random_hint is None:
            return ""
        if type(random_hint) is not int or random_hint <= 0:
            raise ValueError("random hint must be a positive target seat")
        return f"RANDOM_HINT={random_hint}\n"

    def discussion_prompt(
        self, state: GameState, seat: int, history: Sequence[str],
        briefing: NightBriefing = NightBriefing(), random_hint: int | None = None,
    ) -> list[dict[str, str]]:
        wolves = self._wolf_team(state)
        alive = self._alive_text(state)
        total_turns = 3 * len(wolves)
        turn_number = len(history) + 1
        remaining = max(total_turns - len(history) - 1, 0)
        system = (
            f"You are seat {seat}, a werewolf in an AI Werewolf game. "
            "Discuss tonight's kill target with your teammates in a natural "
            "private chat: reply to what your teammates just said (agree, "
            "add, or push back) and then state your own view. Never reveal "
            "that you are a werewolf. Speak like a real player — do not quote "
            "or reference the rules of this prompt. "
            "A deliberate target among your teammates, including yourself, is "
            "a valid strategy unless the action contract forbids it. "
            + _NO_FABRICATION_RULE
            + _CHINESE_DIRECTIVE
        )
        human = (
            f"第{state.round_number}晚狼队讨论。你的队友：{('、'.join(str(w) for w in wolves))}号。"
            f"场上存活玩家：{alive}。\n"
            + self._fallback_hint(random_hint) + ""
            f"## 白天公开信息回顾\n{self._briefing_text(briefing.public_lines, '（暂无白天公开信息）')}\n"
            f"## 此前夜晚狼队频道记录\n{self._briefing_text(briefing.wolf_lines, '（暂无狼队频道记录）')}\n"
            f"## 你的思考回顾\n{self._briefing_text(briefing.thoughts, '（暂无思考记录）')}\n\n"
            f"已进行的讨论：\n{self._history_text(history) or '（尚无发言）'}\n\n"
            f"当前为第 {turn_number}/{total_turns} 轮发言，最多还可继续 {remaining} 轮。\n"
            + _TARGET_RULE
            + "\n讨论要求：\n"
            "- 这是与队友的实时对话：先简要回应队友刚提出的观点（同意、补充或反对），"
            "再给出你的新想法；不要自说自话，也不要复述队友已经说过的内容。\n"
            "- 听完队友发言后，如果没有新的、不重复的想法，请直接跳过本轮，"
            "不要为了说话而说话。\n"
            "- 如果你有倾向的刀人目标，把该座位号填入 preferred_target；没有倾向就填 null。\n"
            "- 除了今晚的刀人目标，还应商定明天白天的配合计划：带节奏方向、嫁祸对象等，"
            "用 day_plan 字段（≤150字，没有计划则填空串）提交，计划会同步给全体队友。\n"
            "- 当所有狼队友都认可同一个目标后，讨论会提前结束。\n"
            + _night_notice(state)
            + '现在轮到你了。输出 JSON：{"speak": true, "text": "你的发言(≤200字)", '
            '"preferred_target": 座位号或null, "day_plan": "次日白天配合计划(≤150字，可空)"} '
            '表示发言；{"speak": false, "text": "", "preferred_target": null, '
            '"day_plan": ""} 表示跳过本轮发言。'
        )
        return self._messages(system, human)

    def vote_prompt(
        self, state: GameState, seat: int,
        discussion: Sequence[str], prior_votes: Sequence[WolfVote],
        briefing: NightBriefing = NightBriefing(), random_hint: int | None = None,
    ) -> list[dict[str, str]]:
        wolves = self._wolf_team(state)
        alive = self._alive_text(state)
        system = (
            f"You are seat {seat}, a werewolf in an AI Werewolf game. "
            "Cast your kill vote. You can see the discussion and the votes cast "
            "before you. Never reveal that you are a werewolf. "
            "A deliberate target among your teammates, including yourself, is "
            "a valid strategy unless the action contract forbids it. "
            + _NO_FABRICATION_RULE
            + _CHINESE_DIRECTIVE
        )
        human = (
            f"第{state.round_number}晚狼队投票。你的队友：{('、'.join(str(w) for w in wolves))}号。"
            f"场上存活玩家：{alive}。\n"
            + self._fallback_hint(random_hint) + ""
            f"## 白天公开信息回顾\n{self._briefing_text(briefing.public_lines, '（暂无白天公开信息）')}\n"
            f"## 此前夜晚狼队频道记录\n{self._briefing_text(briefing.wolf_lines, '（暂无狼队频道记录）')}\n"
            f"## 你的思考回顾\n{self._briefing_text(briefing.thoughts, '（暂无思考记录）')}\n\n"
            f"讨论记录：\n{self._history_text(discussion) or '（无）'}\n"
            f"已出票：\n{self._prior_votes_text(prior_votes)}\n\n"
            + _night_notice(state)
            + _TARGET_RULE
            + '\n输出 JSON：{"schema_version": 1, "action_type": "kill", "target_seat": 目标座位号, '
            '"reasoning": "中文理由(≤500字)"} 或 {"schema_version": 1, "action_type": "pass", '
            '"target_seat": null, "reasoning": "中文理由(≤500字)"}。'
        )
        return self._messages(system, human)

    # ── LLM turns (failure always degrades to a safe fallback) ──

    def _invoke_json(
        self,
        messages: list[dict[str, str]],
        *,
        tool_name: str,
        schema: dict[str, object],
        seat: int,
    ) -> Mapping[str, object]:
        raw = self._invoke(messages, tool_name, schema, seat)
        if type(raw) is not str:
            raise ValueError("model response is not text")
        value = extract_json_object(raw)
        if not isinstance(value, Mapping):
            raise ValueError("model response is not an object")
        return value

    def wolf_discussion_turn(
        self, state: GameState, seat: int, history: Sequence[str],
        briefing: NightBriefing = NightBriefing(), random_hint: int | None = None,
    ) -> DiscussionTurn:
        try:
            value = self._invoke_json(
                self.discussion_prompt(state, seat, history, briefing, random_hint),
                tool_name=_WOLF_DISCUSSION_TOOL_NAME,
                schema=_wolf_discussion_schema(),
                seat=seat,
            )
            if value.get("speak") is not True:
                return DiscussionTurn(seat, False)
            text = _clean(value.get("text"), "text", _MAX_UTTERANCE)
            target = value.get("preferred_target")
            if target is None:
                preferred = None
            elif type(target) is int and target in state.players:
                preferred = target
            else:
                preferred = None
            day_plan = value.get("day_plan")
            if type(day_plan) is not str or len(day_plan) > _MAX_DAY_PLAN:
                day_plan = ""
            return DiscussionTurn(seat, True, text, preferred, day_plan)
        except Exception:
            logger.warning(
                "Wolf discussion LLM failed for seat %s round %s; treating as skip",
                seat, state.round_number,
                exc_info=True,
            )
            return DiscussionTurn(seat, False)

    def wolf_vote_turn(
        self, state: GameState, seat: int,
        discussion: Sequence[str], prior_votes: Sequence[WolfVote],
        briefing: NightBriefing = NightBriefing(), random_hint: int | None = None,
    ) -> WolfVote:
        try:
            value = self._invoke_json(
                self.vote_prompt(
                    state, seat, discussion, prior_votes, briefing, random_hint,
                ),
                tool_name=_WOLF_VOTE_TOOL_NAME,
                schema=_wolf_vote_schema(),
                seat=seat,
            )
            if value.get("action_type") == "kill":
                target = value.get("target_seat")
                if type(target) is not int or target <= 0 or target not in state.players:
                    raise ValueError("invalid kill target")
                return WolfVote(seat, "kill", target, _clean(value.get("reasoning"), "reasoning", 500))
            return WolfVote(seat, "pass", None, _clean(value.get("reasoning"), "reasoning", 500))
        except Exception:
            logger.warning(
                "Wolf vote LLM failed for seat %s round %s; degrading to safe pass",
                seat, state.round_number,
                exc_info=True,
            )
            return WolfVote(seat, "pass", None, "系统异常，本轮未行动")

    # ── narration ──────────────────────────────────────────────

    @staticmethod
    def narration(kind: str) -> tuple[str, str]:
        if kind not in _NARRATIONS:
            raise ValueError(f"unknown narration: {kind}")
        return _NARRATIONS[kind]

    @staticmethod
    def witch_narration(kill_target: int | None) -> tuple[str, str]:
        """Witch opening line must match whether a wolf kill actually happened."""
        if kill_target is None:
            return ("女巫请睁眼", "昨晚风平浪静，无人被袭击。")
        return ("女巫请睁眼", "昨晚有人被袭击。")

    @staticmethod
    def dawn_narration(deaths: Sequence[int]) -> tuple[str, str]:
        if not deaths:
            return ("天亮了", "昨晚是平安夜，没有人死亡。")
        seats = "、".join(f"{seat}号" for seat in sorted(deaths))
        return ("天亮了", f"昨晚 {seats} 玩家死亡。")

    # ── wolf vote hand-off (engine → aggregate contract provider) ──

    def record_votes(self, votes: Sequence[WolfVote]) -> None:
        self._collected_votes = {vote.seat: vote for vote in votes}

    def collected_vote(self, seat: int) -> ActionCommand | None:
        vote = self._collected_votes.get(seat)
        return None if vote is None else vote.to_command()
