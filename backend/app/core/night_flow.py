from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from app.models.game import GameState
from app.models.pipeline import ActionCommand
from app.roles.registry import RegistrySnapshot

_MAX_UTTERANCE = 200
_MAX_THOUGHT = 200

_CHINESE_DIRECTIVE = (
    "IMPORTANT: Every piece of text you produce (message, reasoning, thought) "
    "MUST be written in Simplified Chinese (简体中文)."
)

_NARRATIONS: dict[str, tuple[str, str]] = {
    "wolf_open": ("天黑请闭眼", "狼人请睁眼，开始讨论今晚的行动。"),
    "witch_open": ("女巫请睁眼", "昨晚有人被袭击。"),
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

    def __post_init__(self) -> None:
        if type(self.seat) is not int or self.seat <= 0:
            raise ValueError("invalid seat")
        if type(self.spoke) is not bool:
            raise TypeError("spoke must be bool")
        if self.spoke:
            _clean(self.text, "text", _MAX_UTTERANCE)
        elif self.text != "":
            raise ValueError("skipped turn cannot carry text")


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


@dataclass(frozen=True)
class ThinkResult:
    seat: int
    text: str

    def __post_init__(self) -> None:
        if type(self.seat) is not int or self.seat <= 0:
            raise ValueError("invalid seat")
        _clean(self.text, "text", _MAX_THOUGHT)


class NightDirector:
    """Pure night-flow orchestrator: builds prompts, parses LLM answers with
    safe fallbacks, produces narration texts, and hands the engine the
    pre-collected wolf votes for the aggregate contract point."""

    def __init__(
        self,
        snapshot: RegistrySnapshot,
        invoke: Callable[[list[dict[str, str]]], str],
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

    def _prior_votes_text(self, votes: Sequence[WolfVote]) -> str:
        if not votes:
            return "（还没有人出票）"
        return "\n".join(
            f"{vote.seat}号：{'刀 ' + str(vote.target_seat) + ' 号' if vote.action_type == 'kill' else '弃权'}（{vote.reasoning}）"
            for vote in votes
        )

    def discussion_prompt(self, state: GameState, seat: int, history: Sequence[str]) -> list[dict[str, str]]:
        wolves = self._wolf_team(state)
        alive = self._alive_text(state)
        system = (
            f"You are seat {seat}, a werewolf in an AI Werewolf game. "
            "Discuss tonight's kill target with your teammates. You may speak "
            "or stay silent on your turn. Never reveal that you are a werewolf. "
            + _CHINESE_DIRECTIVE
        )
        human = (
            f"第{state.round_number}晚狼队讨论。你的队友：{('、'.join(str(w) for w in wolves))}号。"
            f"场上存活玩家：{alive}。\n"
            f"已进行的讨论：\n{self._history_text(history) or '（尚无发言）'}\n\n"
            '现在轮到你了。输出 JSON：{"speak": true, "text": "你的发言(≤200字)"} 表示发言，'
            '{"speak": false} 表示跳过本轮发言。'
        )
        return self._messages(system, human)

    def vote_prompt(
        self, state: GameState, seat: int,
        discussion: Sequence[str], prior_votes: Sequence[WolfVote],
    ) -> list[dict[str, str]]:
        wolves = self._wolf_team(state)
        alive = self._alive_text(state)
        system = (
            f"You are seat {seat}, a werewolf in an AI Werewolf game. "
            "Cast your kill vote. You can see the discussion and the votes cast "
            "before you. Never reveal that you are a werewolf. " + _CHINESE_DIRECTIVE
        )
        human = (
            f"第{state.round_number}晚狼队投票。你的队友：{('、'.join(str(w) for w in wolves))}号。"
            f"场上存活玩家：{alive}。\n"
            f"讨论记录：\n{self._history_text(discussion) or '（无）'}\n"
            f"已出票：\n{self._prior_votes_text(prior_votes)}\n\n"
            '输出 JSON：{"schema_version": 1, "action_type": "kill", "target_seat": 目标座位号, '
            '"reasoning": "中文理由(≤500字)"} 或 {"schema_version": 1, "action_type": "pass", '
            '"target_seat": null, "reasoning": "中文理由(≤500字)"}。'
        )
        return self._messages(system, human)

    def _think_prompt(self, state: GameState, seat: int, role_display: str, extra: str) -> list[dict[str, str]]:
        system = (
            f"You are seat {seat}, the {role_display} in an AI Werewolf game. "
            "Think out loud about tonight's decision. " + _CHINESE_DIRECTIVE
        )
        human = (
            f"第{state.round_number}晚。场上存活玩家：{self._alive_text(state)}。\n"
            f"{extra}\n"
            '输出 JSON：{"text": "你的思考(≤200字)"}。'
        )
        return self._messages(system, human)

    def witch_think_prompt(self, state: GameState, seat: int, wolf_target: Optional[int]) -> list[dict[str, str]]:
        antidote, poison = self._witch_potions(state, seat)
        extra = (
            f"昨夜狼人袭击了 {wolf_target} 号。" if wolf_target is not None
            else "昨夜没有袭击发生。"
        ) + f"你目前剩余解药 {antidote} 瓶、毒药 {poison} 瓶。"
        return self._think_prompt(state, seat, "Witch", extra)

    def _witch_potions(self, state: GameState, seat: int) -> tuple[int, int]:
        """Current witch potion counts from the pipeline resources.

        Falls back to the registered witch spec's initial resources when the
        pipeline runtime has not initialized this seat (e.g. early snapshots),
        and to zero when no witch spec is available at all.
        """
        from app.core.role_runtime import role_resource_view

        resources = dict(role_resource_view(state, seat))
        if resources:
            return resources.get("antidote", 0), resources.get("poison", 0)
        spec = self._snapshot.specs.get("wolf-killer-witch")
        if spec is None:
            return 0, 0
        return int(spec.initial_resources.get("antidote", 0)), int(
            spec.initial_resources.get("poison", 0)
        )

    def seer_think_prompt(self, state: GameState, seat: int) -> list[dict[str, str]]:
        return self._think_prompt(state, seat, "Seer", "你每晚可以查验一名玩家的阵营。")

    # ── LLM turns (failure always degrades to a safe fallback) ──

    def _invoke_json(self, messages: list[dict[str, str]]) -> Mapping[str, object]:
        raw = self._invoke(messages)
        if type(raw) is not str:
            raise ValueError("model response is not text")
        value = json.loads(raw)
        if not isinstance(value, Mapping):
            raise ValueError("model response is not an object")
        return value

    def wolf_discussion_turn(self, state: GameState, seat: int, history: Sequence[str]) -> DiscussionTurn:
        try:
            value = self._invoke_json(self.discussion_prompt(state, seat, history))
            if value.get("speak") is not True:
                return DiscussionTurn(seat, False)
            text = _clean(value.get("text"), "text", _MAX_UTTERANCE)
            return DiscussionTurn(seat, True, text)
        except Exception:
            return DiscussionTurn(seat, False)

    def wolf_vote_turn(
        self, state: GameState, seat: int,
        discussion: Sequence[str], prior_votes: Sequence[WolfVote],
    ) -> WolfVote:
        try:
            value = self._invoke_json(self.vote_prompt(state, seat, discussion, prior_votes))
            if value.get("action_type") == "kill":
                target = value.get("target_seat")
                if type(target) is not int or target <= 0 or target not in state.players:
                    raise ValueError("invalid kill target")
                return WolfVote(seat, "kill", target, _clean(value.get("reasoning"), "reasoning", 500))
            return WolfVote(seat, "pass", None, _clean(value.get("reasoning"), "reasoning", 500))
        except Exception:
            return WolfVote(seat, "pass", None, "safe fallback")

    def witch_think(self, state: GameState, seat: int, wolf_target: Optional[int]) -> ThinkResult | None:
        try:
            value = self._invoke_json(self.witch_think_prompt(state, seat, wolf_target))
            text = _clean(value.get("text"), "text", _MAX_THOUGHT)
            return ThinkResult(seat, text)
        except Exception:
            return None

    def seer_think(self, state: GameState, seat: int) -> ThinkResult | None:
        try:
            value = self._invoke_json(self.seer_think_prompt(state, seat))
            text = _clean(value.get("text"), "text", _MAX_THOUGHT)
            return ThinkResult(seat, text)
        except Exception:
            return None

    # ── narration ──────────────────────────────────────────────

    @staticmethod
    def narration(kind: str) -> tuple[str, str]:
        if kind not in _NARRATIONS:
            raise ValueError(f"unknown narration: {kind}")
        return _NARRATIONS[kind]

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
