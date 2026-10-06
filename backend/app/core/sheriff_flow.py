"""Sheriff office: hardcoded election, vote weight, speech order, and badge flow.

The sheriff is a game-level office, not a role. Agents only pick the tool bound
for the current engine step; they never choose the next phase.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Optional

from app.agents.game_rules import BASE_RULES, SHERIFF_GAME_RULES
from app.agents.output_parser import extract_json_object
from app.core.conversation_log import ConversationLog
from app.core.wolf_pack import actor_sees_pack, in_wolf_pack, pack_seats
from app.core.role_runtime import (
    role_private_facts_view, role_resource_view,
)
from app.models.game import Camp, GameState
from app.models.conversation import ConversationScope

logger = logging.getLogger(__name__)

SHERIFF_LEFT = "sheriff_left"
SHERIFF_RIGHT = "sheriff_right"
DEATH_LEFT = "death_left"
DEATH_RIGHT = "death_right"

_EXPLODE = "explode"
_RUN = "run"
_PASS = "pass"
_STAY = "stay"
_WITHDRAW = "withdraw"
_VOTE = "vote"
_TRANSFER = "transfer"
_TEAR = "tear"

InvokeFn = Callable[[list[dict[str, str]], str, dict[str, object], int], str]


def office_enabled(state: GameState) -> bool:
    return bool(getattr(state.config, "enable_sheriff", False))


def should_run_election(state: GameState) -> bool:
    return office_enabled(state) and not state.sheriff_election_complete


def decide_after_runs(ran: frozenset[int], alive: frozenset[int]) -> str:
    if not ran:
        return "none"
    if len(ran) == 1:
        return "auto"
    return "campaign"


def decide_after_withdraw(
    ran: frozenset[int], remaining: frozenset[int], alive: frozenset[int],
) -> str:
    if not remaining:
        return "none"
    if len(remaining) == 1:
        return "auto"
    if ran >= alive:
        return "none"
    return "vote"


def eligible_sheriff_voters(ran: frozenset[int], alive: frozenset[int]) -> frozenset[int]:
    return alive - ran


def decide_tally(counts: Mapping[int, int]) -> tuple[Optional[int], tuple[int, ...]]:
    if not counts:
        return None, ()
    top = max(counts.values())
    seats = tuple(sorted(seat for seat, count in counts.items() if count == top))
    if len(seats) == 1:
        return seats[0], ()
    return None, seats


def badge_loss_message(
    reason: str,
    candidates: frozenset[int],
    alive: frozenset[int],
    remaining: frozenset[int],
) -> str:
    """Human-readable public reason for a lost badge election."""
    if reason == "tie":
        return "警长投票平票，警徽流失。"
    if not candidates:
        return "无人上警，警徽流失。"
    if not remaining:
        return "所有候选人退水，警徽流失。"
    if candidates >= alive:
        return "全员上警，警下无人可投票，警徽流失。"
    return "警长竞选结束，警徽流失。"


def set_sheriff(state: GameState, seat: int) -> None:
    clear_office(state, destroyed=False)
    player = state.players[seat]
    player.is_sheriff = True
    state.sheriff = seat
    state.sheriff_office.badge_destroyed = False


def clear_office(state: GameState, *, destroyed: bool) -> None:
    for player in state.players.values():
        player.is_sheriff = False
    state.sheriff = None
    state.sheriff_office.badge_destroyed = bool(destroyed)


def sheriff_active(state: GameState) -> bool:
    if not office_enabled(state) or state.sheriff_office.badge_destroyed:
        return False
    seat = state.sheriff
    if seat is None:
        return False
    player = state.players.get(seat)
    return player is not None and player.is_alive and player.is_sheriff


def ballot_weight(state: GameState, voter_seat: int) -> int:
    if not sheriff_active(state):
        return 1
    return 3 if voter_seat == state.sheriff else 2


def speech_sides(night_death_count: int) -> tuple[str, ...]:
    if night_death_count == 1:
        return (DEATH_LEFT, DEATH_RIGHT)
    return (SHERIFF_LEFT, SHERIFF_RIGHT)


def speech_order(alive: Sequence[int], anchor: int, side: str) -> list[int]:
    ordered = [seat for seat in alive if type(seat) is int]
    if not ordered:
        return []
    clockwise = side in {SHERIFF_LEFT, DEATH_LEFT}
    if clockwise:
        start = next((index for index, seat in enumerate(ordered) if seat > anchor), 0)
        return list(ordered[start:] + ordered[:start])
    start = next(
        (index for index, seat in reversed(list(enumerate(ordered))) if seat < anchor),
        len(ordered) - 1,
    )
    rotated = list(ordered[start + 1:] + ordered[:start + 1])
    return list(reversed(rotated))


def badge_targets(state: GameState, sheriff_seat: int) -> tuple[int, ...]:
    return tuple(
        sorted(
            seat for seat, player in state.players.items()
            if player.is_alive and seat != sheriff_seat
        )
    )


def apply_badge(state: GameState, from_seat: int, action: str, target: Optional[int]) -> None:
    if action == _TRANSFER and type(target) is int and target in badge_targets(state, from_seat):
        set_sheriff(state, target)
        return
    clear_office(state, destroyed=True)


def night_death_seats(state: GameState) -> tuple[int, ...]:
    return tuple(
        death.player_seat
        for death in state.death_history
        if death.round_number == state.round_number
    )


#: The election runs once, after the first night.
_ELECTION_ROUND = 1

#: Recorded before any seat can choose to run. This is every cause the first
#: night can produce: the wolf kill, the witch's poison and the hunter's shot —
#: plus `charm`, because a wolf beauty poisoned on the first night takes her
#: charmed victim with her before the election starts. `knight_duel` is absent
#: because the duel only happens after the day's speeches.
_PRE_ELECTION_DEATH_CAUSES = frozenset({
    "wolf_kill", "poison", "hunter_shot", "charm", "wolf_king_shot",
    "reflect", "dream_death",
})

#: These causes begin at the election or the day after it. A death of one of
#: the causes above that appears later in the same round happened to someone
#: who was alive when the election started.
_POST_ELECTION_DEATH_CAUSES = frozenset({"exile", "self_explode"})


def seats_dead_before_election(state: GameState) -> frozenset[int]:
    """Seats that died on the first night, before they could run for sheriff."""
    dead: set[int] = set()
    for death in state.death_history:
        if death.round_number != _ELECTION_ROUND:
            continue
        if death.cause in _POST_ELECTION_DEATH_CAUSES:
            break
        if death.cause in _PRE_ELECTION_DEATH_CAUSES:
            dead.add(death.player_seat)
    return frozenset(dead)


def off_badge_seats(state: GameState) -> tuple[int, ...]:
    """Seats that were alive at the election and never ran, in seat order.

    A seat that stayed off badge remains listed after a later death. A seat
    that was already dead when the election started is omitted.
    """
    excluded = seats_dead_before_election(state)
    candidates = state.sheriff_office.candidates
    return tuple(sorted(
        seat for seat in state.players
        if seat not in candidates and seat not in excluded
    ))


def _as_object(raw: object) -> Mapping[str, object]:
    if isinstance(raw, Mapping):
        return raw
    return {}


def parse_explode_choice(raw: object, *, wolf: bool) -> bool:
    return wolf and _as_object(raw).get("action_type") == _EXPLODE


def parse_campaign(raw: object, *, wolf: bool) -> str:
    action = _as_object(raw).get("action_type")
    if wolf and action == _EXPLODE:
        return _EXPLODE
    if action == _RUN:
        return _RUN
    return _PASS


def parse_withdraw(raw: object, *, wolf: bool) -> str:
    action = _as_object(raw).get("action_type")
    if wolf and action == _EXPLODE:
        return _EXPLODE
    if action == _WITHDRAW:
        return _WITHDRAW
    return _STAY


def parse_vote(raw: object, legal: set[int] | frozenset[int], *, wolf: bool) -> int | str | None:
    payload = _as_object(raw)
    action = payload.get("action_type")
    if wolf and action == _EXPLODE:
        return _EXPLODE
    if action != _VOTE:
        return None
    target = payload.get("target_seat")
    if type(target) is int and target in legal:
        return target
    return None


def parse_side(raw: object, legal: Sequence[str]) -> str:
    side = _as_object(raw).get("side")
    if type(side) is str and side in legal:
        return side
    return legal[0]


def parse_badge(raw: object, legal: set[int] | frozenset[int]) -> tuple[str, Optional[int]]:
    payload = _as_object(raw)
    if payload.get("action_type") == _TRANSFER:
        target = payload.get("target_seat")
        if type(target) is int and target in legal:
            return _TRANSFER, target
    return _TEAR, None


#: The tool schema bounds a reason at 500 characters and the audience DTO shares
#: that bound; a model can still overrun the schema, so the director clips it.
_MAX_REASONING_CHARS = 500


def badge_reasoning(raw: object) -> str:
    """The dying sheriff's own explanation for the badge decision.

    The tool schema requires this field, but ``parse_badge`` only reads the
    action and its target: without this helper the model's reasoning is parsed
    and then dropped, leaving a torn badge with no recorded cause.
    """
    value = _as_object(raw).get("reasoning")
    if not isinstance(value, str):
        return ""
    return value.strip()[:_MAX_REASONING_CHARS]


def _schema(action_types: Sequence[str], extra: dict[str, object] | None = None) -> dict[str, object]:
    properties: dict[str, object] = {
        "action_type": {"type": "string", "enum": list(action_types)},
        "reasoning": {"type": "string", "maxLength": 500},
    }
    required = ["action_type", "reasoning"]
    if extra:
        properties.update(extra)
        required.extend(key for key in extra if key not in required)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


class SheriffDirector:
    """Prompt + parse the current sheriff tool. Engine advances the phase."""

    _MAX_SPEECH_LINES = 12
    _MAX_SPEECH_CHARS = 160

    def __init__(
        self, invoke: InvokeFn | None = None,
        conversation_log: ConversationLog | None = None,
    ) -> None:
        if invoke is not None and not callable(invoke):
            raise TypeError("invoke must be callable")
        if conversation_log is not None and not isinstance(
            conversation_log, ConversationLog,
        ):
            raise TypeError("conversation_log must be a ConversationLog")
        self._invoke = invoke
        self._conversation_log = conversation_log

    @staticmethod
    def _messages(system: str, human: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": human}]

    @staticmethod
    def _system() -> str:
        return (
            BASE_RULES
            + " ".join(SHERIFF_GAME_RULES)
            + " Only use the currently bound tool. Do not invent other Werewolf offices."
        )

    def _speech_lines(self, state: GameState) -> list[str]:
        """Recent public campaign speeches for the current election round."""
        if self._conversation_log is None:
            return []
        records = [
            record for record in self._conversation_log.records
            if record.scope is ConversationScope.PUBLIC
            and record.round_number == state.round_number
            and record.phase == "sheriff_election"
            and record.speaker_seat is not None
        ][-self._MAX_SPEECH_LINES:]
        lines = []
        for record in records:
            content = record.content
            if len(content) > self._MAX_SPEECH_CHARS:
                content = content[:self._MAX_SPEECH_CHARS] + "..."
            lines.append(f"{record.speaker_seat}号：{content}")
        return lines

    def _context_block(self, state: GameState, seat: int, *, wolf: bool) -> str:
        from app.roles.registry import builtin_registry
        player = state.players.get(seat)
        identity = "玩家"
        camp = ""
        if player is not None:
            try:
                identity = builtin_registry.freeze().specs[player.role].display_name
            except KeyError:
                identity = player.role
            camp = player.camp
        lines = [f"你是{seat}号，身份：{identity}，阵营：{camp}。"]

        facts: list[str] = []
        try:
            checks = role_private_facts_view(state, seat, "private_checks")
            for fact in checks[-3:]:
                target = fact.get("target")
                label = "好人" if fact.get("camp") == "good" else "狼人"
                facts.append(f"查验{target}号：{label}")
        except Exception:
            checks = ()
        try:
            resources = role_resource_view(state, seat)
            if "antidote" in resources:
                facts.append("解药" + ("仍在手" if resources["antidote"] else "已用"))
            if "poison" in resources:
                facts.append("毒药" + ("仍在手" if resources["poison"] else "已用"))
            if "gun" in resources:
                facts.append("猎枪仍在手")
        except Exception:
            pass
        lines.append(
            "你的私有事实：" + ("；".join(facts) if facts else "暂无夜间私密信息。")
        )
        office = state.sheriff_office
        ran = "、".join(f"{seat}号" for seat in sorted(office.candidates)) or "无"
        active = "、".join(f"{seat}号" for seat in sorted(office.active)) or "无"
        off_badge = "、".join(f"{seat}号" for seat in off_badge_seats(state)) or "无"
        lines.append(f"已上警座位：{ran}；当前候选人：{active}。")
        lines.append(f"未上警座位（警下，拥有警长投票权）：{off_badge}。")
        speeches = self._speech_lines(state)
        if speeches:
            lines.append("警上已发言（仅作参考）：\n" + "\n".join(speeches))
        if self._conversation_log is not None:
            completed = [
                record.content for record in self._conversation_log.get_public()
                if record.phase == "sheriff_ballot"
                and record.round_number == state.round_number
            ][-2:]
            lines.extend(completed)
        if wolf:
            # The camp line and the explode line have different gates: a hidden
            # wolf is a werewolf but sits outside the pack, and only the pack may
            # swallow the badge. Handing it the explode sentence contradicted its
            # own role contract, which says it can neither self-destruct nor
            # swallow the badge.
            if self._may_explode(state, seat):
                lines.append(
                    "你是狼人阵营：警上可起跳伪装、可冲锋带节奏，也可自爆吞警徽（只死自己）。"
                )
            else:
                lines.append(
                    "你是狼人阵营，但你不属于狼窝：警上可起跳伪装、可冲锋带节奏，"
                    "但**不能自爆，也不能吞警徽**。"
                )
            lines.extend(self._wolf_team_block(state, seat))
        return "\n".join(lines) + "\n"

    _MAX_WOLF_CHANNEL_LINES = 24
    _MAX_WOLF_CHANNEL_CHARS = 1000

    def _wolf_team_block(self, state: GameState, seat: int) -> list[str]:
        """Wolf-only lines: teammate seats and this night's wolf-channel plan.

        Gated on *pack* membership, not camp: a role declaring ``no_wolf_pack``
        is a werewolf the pack cannot see, so handing it the roster (or the
        channel) would leak exactly the team the rules keep from it.
        """
        if not actor_sees_pack(state, seat):
            return []
        teammates = sorted(pack_seats(state))
        lines = ["狼队成员：" + "、".join(f"{item}号" for item in teammates) + "。"]
        if self._conversation_log is not None:
            channel = [
                record for record in self._conversation_log.records
                if record.scope is ConversationScope.WEREWOLF
                and record.round_number == state.round_number
            ][-self._MAX_WOLF_CHANNEL_LINES:]
            if channel:
                plan_lines = []
                for record in channel:
                    speaker = (
                        f"{record.speaker_seat}号" if record.speaker_seat else "系统"
                    )
                    content = record.content
                    if len(content) > self._MAX_WOLF_CHANNEL_CHARS:
                        content = content[:self._MAX_WOLF_CHANNEL_CHARS] + "..."
                    plan_lines.append(f"{speaker}：{content}")
                lines.append("本夜狼队频道记录（含次日计划，仅作参考）：\n" + "\n".join(plan_lines))
        return lines

    @staticmethod
    def _may_explode(state: GameState, seat: int) -> bool:
        """Only a wolf the pack can actually see may swallow the badge.

        ``wolf`` at every call site means *werewolf camp*, because a hidden wolf
        still knows what it is. Exploding is a pack move, so it follows pack
        membership instead: a role declaring ``no_wolf_pack`` has no badge to
        swallow on the team's behalf.
        """
        return in_wolf_pack(state, seat)

    def campaign_prompt(self, state: GameState, seat: int, *, wolf: bool) -> list[dict[str, str]]:
        explode = wolf and self._may_explode(state, seat)
        actions = [_RUN, _PASS] + ([_EXPLODE] if explode else [])
        human = (
            f"第{state.round_number}轮警长竞选。{self._context_block(state, seat, wolf=wolf)}"
            "action_type=run 表示上警成为候选人（由从未上警的警下玩家投票，"
            "当选者白天放逐票计 1.5 票）；action_type=pass 表示不上警、"
            "保留警长投票权，白天仍可正常发言。"
            "退水或上警都会让你失去警长投票权，请结合自身身份与目标权衡。"
            f"请选择 action_type：{'、'.join(actions)}。"
        )
        return self._messages(self._system(), human)

    def withdraw_prompt(self, state: GameState, seat: int, *, wolf: bool) -> list[dict[str, str]]:
        explode = wolf and self._may_explode(state, seat)
        actions = [_STAY, _WITHDRAW] + ([_EXPLODE] if explode else [])
        human = (
            f"第{state.round_number}轮警长竞选退水。你是{seat}号候选人。\n"
            f"{self._context_block(state, seat, wolf=wolf)}"
            "action_type=stay 表示继续竞选；action_type=withdraw 表示退水，"
            "退水后不再竞争警徽，也将失去警长投票权。"
            f"请选择 action_type：{'、'.join(actions)}。"
        )
        return self._messages(self._system(), human)

    def vote_prompt(
        self, state: GameState, seat: int, candidates: Sequence[int],
        *, wolf: bool = False,
    ) -> list[dict[str, str]]:
        listed = "、".join(f"{item}号" for item in candidates)
        human = (
            f"第{state.round_number}轮警长投票。你是{seat}号，从未上警，因此拥有警长投票权，"
            f"请在候选人中选择：{listed}。结合警上发言判断谁更适合当警长。"
            f"输出 action_type=vote 且 target_seat 为候选座位，或 action_type=pass。\n"
            + self._context_block(state, seat, wolf=wolf)
        )
        return self._messages(self._system(), human)

    def side_prompt(self, state: GameState, seat: int, sides: Sequence[str]) -> list[dict[str, str]]:
        human = (
            f"你是警长{seat}号。请选择今天的发言方向 side，只能是：{'、'.join(sides)}。"
        )
        return self._messages(self._system(), human)

    def badge_prompt(self, state: GameState, seat: int, targets: Sequence[int]) -> list[dict[str, str]]:
        listed = "、".join(f"{item}号" for item in targets) or "无"
        human = (
            f"你是即将出局的警长{seat}号。\n"
            # 出局警长拿到与其他警长提示词同样的私有事实：没有它们，预言家无从
            # 知道自己刚验出的是金水还是查杀，也就无法用警徽去标记这次查验。
            # wolf=False 是刻意的：狼人分支那句「可自爆吞警徽」只属于竞选期。
            f"{self._context_block(state, seat, wolf=False)}"
            f"可移交警徽给：{listed}，或撕毁警徽。"
            "action_type 为 transfer 时必须给出 target_seat；tear 时 target_seat 为 null。"
        )
        return self._messages(self._system(), human)

    def _invoke_json(
        self, messages: list[dict[str, str]], *, tool_name: str,
        schema: dict[str, object], seat: int,
    ) -> Mapping[str, object]:
        if self._invoke is None:
            return {}
        raw = self._invoke(messages, tool_name, schema, seat)
        if type(raw) is not str:
            raise ValueError("model response is not text")
        value = extract_json_object(raw)
        if not isinstance(value, Mapping):
            raise ValueError("model response is not an object")
        return value

    def campaign_turn(self, state: GameState, seat: int, *, wolf: bool) -> str:
        explode = wolf and self._may_explode(state, seat)
        actions = [_RUN, _PASS] + ([_EXPLODE] if explode else [])
        try:
            value = self._invoke_json(
                self.campaign_prompt(state, seat, wolf=wolf),
                tool_name="sheriff_campaign",
                schema=_schema(actions),
                seat=seat,
            )
            return parse_campaign(value, wolf=explode)
        except Exception:
            logger.warning("sheriff campaign failed seat=%s", seat, exc_info=True)
            return _PASS

    def withdraw_turn(self, state: GameState, seat: int, *, wolf: bool) -> str:
        explode = wolf and self._may_explode(state, seat)
        actions = [_STAY, _WITHDRAW] + ([_EXPLODE] if explode else [])
        try:
            value = self._invoke_json(
                self.withdraw_prompt(state, seat, wolf=wolf),
                tool_name="sheriff_withdraw",
                schema=_schema(actions),
                seat=seat,
            )
            return parse_withdraw(value, wolf=explode)
        except Exception:
            logger.warning("sheriff withdraw failed seat=%s", seat, exc_info=True)
            return _STAY

    def vote_turn(
        self, state: GameState, seat: int, candidates: Sequence[int], *, wolf: bool,
    ) -> int | str | None:
        explode = wolf and self._may_explode(state, seat)
        actions = [_VOTE, _PASS] + ([_EXPLODE] if explode else [])
        try:
            value = self._invoke_json(
                # ``wolf`` (camp) is what the prompt needs: dropping it here left
                # the wolf roster out of the ballot while campaign, withdraw and
                # every day-time tool carried it, so wolves voted without knowing
                # their own team. ``explode`` stays schema-only.
                self.vote_prompt(state, seat, candidates, wolf=wolf),
                tool_name="sheriff_vote",
                schema=_schema(
                    actions,
                    {"target_seat": {"type": ["integer", "null"], "minimum": 1}},
                ),
                seat=seat,
            )
            return parse_vote(value, set(candidates), wolf=explode)
        except Exception:
            logger.warning("sheriff vote failed seat=%s", seat, exc_info=True)
            return None

    def side_turn(self, state: GameState, seat: int, sides: Sequence[str]) -> str:
        try:
            value = self._invoke_json(
                self.side_prompt(state, seat, sides),
                tool_name="sheriff_speech_side",
                schema=_schema(["choose"], {"side": {"type": "string", "enum": list(sides)}}),
                seat=seat,
            )
            return parse_side(value, sides)
        except Exception:
            logger.warning("sheriff speech side failed seat=%s", seat, exc_info=True)
            return sides[0]

    def badge_turn(
        self, state: GameState, seat: int, targets: Sequence[int],
    ) -> tuple[str, Optional[int], str]:
        try:
            value = self._invoke_json(
                self.badge_prompt(state, seat, targets),
                tool_name="sheriff_badge",
                schema=_schema(
                    [_TRANSFER, _TEAR],
                    {"target_seat": {"type": ["integer", "null"], "minimum": 1}},
                ),
                seat=seat,
            )
            action, target = parse_badge(value, set(targets))
            if action == _TEAR and _as_object(value).get("action_type") == _TRANSFER:
                # The model named a seat the badge may not go to, so the ledger
                # says tear. Keep the intent diagnosable in the server log: the
                # published reason would otherwise read as contradicting the
                # action that actually landed.
                logger.warning(
                    "sheriff badge transfer degraded seat=%s requested=%s",
                    seat, _as_object(value).get("target_seat"),
                )
            return action, target, badge_reasoning(value)
        except Exception:
            logger.warning("sheriff badge failed seat=%s", seat, exc_info=True)
            return _TEAR, None, ""
