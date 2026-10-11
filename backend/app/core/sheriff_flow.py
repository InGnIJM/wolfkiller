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
from app.models.game import Camp, GamePhase, GameState
from app.models.conversation import ConversationScope

logger = logging.getLogger(__name__)

SHERIFF_LEFT = "sheriff_left"
SHERIFF_RIGHT = "sheriff_right"
DEATH_LEFT = "death_left"
DEATH_RIGHT = "death_right"

#: What each side choice means in seat numbers. The engine maps left to
#: ascending and right to descending (``speech_order``); the prompt has to say so
#: because nothing else does — the system prompt only states that the sheriff
#: chooses the day's start.
_SIDE_HINTS: dict[str, str] = {
    SHERIFF_LEFT: "从警长下一号起按座号升序",
    SHERIFF_RIGHT: "从警长上一号起按座号降序",
    DEATH_LEFT: "从昨夜死者下一号起按座号升序",
    DEATH_RIGHT: "从昨夜死者上一号起按座号降序",
}

_EXPLODE = "explode"
_RUN = "run"
_PASS = "pass"
_STAY = "stay"
_WITHDRAW = "withdraw"
_VOTE = "vote"
_TRANSFER = "transfer"
_TEAR = "tear"

#: Declared by a werewolf-camp role whose card forbids self-destruction. It
#: travels as a resource so the pack-wide kill contract stays byte-identical
#: across roles, and this module is its only reader: the badge-swallowing move
#: is a self-destruct too.
NO_SELF_EXPLODE_RESOURCE = "no_self_explode"

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


def speech_anchor(state: GameState, sheriff_seat: Optional[int]) -> int:
    """The seat the day's rotation is anchored on.

    Exactly one night death anchors the order on that death (``death_left`` /
    ``death_right``), a peaceful or multi-death night anchors it on the sheriff
    (``sheriff_left`` / ``sheriff_right``).
    """
    deaths = night_death_seats(state)
    if len(deaths) == 1:
        return deaths[0]
    return sheriff_seat or 0


def day_speech_order(
    state: GameState, side: str, seats: Optional[Sequence[int]] = None,
) -> list[int]:
    """The seats in the order they will speak today, for one side choice.

    The sheriff's prompt previews these orders and the engine rotates with the
    same call, so what the badge picks is what actually runs — the preview can
    never drift into a second implementation. ``seats`` defaults to the living
    seats the table believes in; the engine passes the list it is about to
    rotate (a resumed round may already exclude seats that spoke).
    """
    ordered = list(seats) if seats is not None else list(public_alive_seats(state))
    return speech_order(ordered, speech_anchor(state, state.sheriff), side)


#: The election runs once, after the first night.
_ELECTION_ROUND = 1

#: Causes announced where they happen, never in the dawn batch. An exile and the
#: election's own self-explode belong to the day that produces them; a knight
#: duel only happens after the day's speeches; and the night's response windows
#: (a shot fired by a seat the night killed, and the revenge it can trigger) run
#: *after* the deaths are read out, so their victims are announced on that beat.
#: Every *other* cause is recorded by the night settlement and read out by the
#: dawn announcement — which is why the election can run before anyone is
#: publicly dead.
_ANNOUNCED_IMMEDIATELY = frozenset({
    "exile", "self_explode", "knight_duel",
    "hunter_shot", "wolf_king_shot", "charm",
})


def _phase_value(state: GameState) -> str:
    phase = state.phase
    return phase.value if hasattr(phase, "value") else str(phase)


def unannounced_death_seats(state: GameState) -> frozenset[int]:
    """Seats recorded dead whose death the table has not heard yet.

    Only the sheriff election can open that window: the first night is settled
    before the election but announced after it (official flow — the judge reads
    the deaths once the badge has landed). A seat listed here is still playing:
    it may run for sheriff, speak on badge, and vote. Everywhere else the set is
    empty, and a seat that died in an earlier round is long announced.
    """
    if state.round_number != _ELECTION_ROUND:
        return frozenset()
    if _phase_value(state) not in ("night", "sheriff_election"):
        return frozenset()
    return frozenset(
        death.player_seat
        for death in state.death_history
        if death.round_number == _ELECTION_ROUND
        and death.cause not in _ANNOUNCED_IMMEDIATELY
    )


def election_seats(state: GameState) -> frozenset[int]:
    """Everyone who takes part in the sheriff election.

    The living plus the first night's dead: officially the deaths are read out
    *after* the badge lands, so the night's victims campaign like everyone else.
    """
    return frozenset(state.alive_players()) | unannounced_death_seats(state)


def night_batch_deaths(state: GameState) -> tuple[int, ...]:
    """Seats the dawn announcement reads out this round, in death order.

    A hunter's shot and the election's self-explode are announced on their own
    beat, so they never ride along in the night's batch.
    """
    return tuple(
        death.player_seat
        for death in state.death_history
        if death.round_number == state.round_number
        and death.cause not in _ANNOUNCED_IMMEDIATELY
    )


def public_alive_seats(state: GameState) -> tuple[int, ...]:
    """Seats the table still believes are playing, in seat order."""
    unannounced = unannounced_death_seats(state)
    return tuple(sorted(
        seat for seat, player in state.players.items()
        if player.is_alive or seat in unannounced
    ))


def public_dead_seats(state: GameState) -> tuple[int, ...]:
    """Seats whose deaths the table has already heard, in seat order."""
    unannounced = unannounced_death_seats(state)
    return tuple(sorted(
        seat for seat, player in state.players.items()
        if not player.is_alive and seat not in unannounced
    ))


def off_badge_seats(state: GameState) -> tuple[int, ...]:
    """Seats that never ran for sheriff, in seat order.

    The roster is a historical fact about the election, so a seat that stayed
    off badge remains listed after a later death — including the first night's
    victims, who are on the table until the deaths are read out.
    """
    candidates = state.sheriff_office.candidates
    return tuple(sorted(
        seat for seat in state.players
        if seat not in candidates
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


def _schema(
    action_types: Sequence[str], extra: dict[str, object] | None = None,
    *, optional: Sequence[str] = (),
) -> dict[str, object]:
    """The bound tool's schema.

    ``optional`` keeps a property out of ``required``. That matters for the two
    calls where one action needs a target and another must not have one
    (``vote`` vs ``pass``, ``transfer`` vs ``tear``): listing ``target_seat`` as
    required made the schema contradict the prompt, and ``_require_schema_fields``
    then threw away a perfectly good ``pass`` answer as
    ``response_missing_fields`` — 4 of 7 sheriff ballots died that way in one
    batch. The JSON tool channel is bound non-strict (``get_model_with_json_tool``),
    so relaxing ``required`` cannot be rejected provider-side either.
    """
    properties: dict[str, object] = {
        "action_type": {"type": "string", "enum": list(action_types)},
        "reasoning": {"type": "string", "maxLength": 500},
    }
    required = ["action_type", "reasoning"]
    if extra:
        properties.update(extra)
        required.extend(
            key for key in extra if key not in required and key not in optional
        )
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
        # ``active`` is only written after the run loop (game_engine.py:1282) and
        # again after the withdrawals (:1321): during the run stage it is still
        # empty, so reading it there told every seat who had already run that
        # nobody was a candidate ("已上警座位：4号、5号；当前候选人：无"). In the
        # run stage the candidates are exactly the seats that have run so far.
        contesting = office.candidates if office.step == "run" else office.active
        active = "、".join(f"{seat}号" for seat in sorted(contesting)) or "无"
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
        swallow on the team's behalf. A role whose card forbids self-destruction
        declares ``no_self_explode`` — swallowing the badge is the same move, so
        it stays out too, even though it belongs to the pack.
        """
        if not in_wolf_pack(state, seat):
            return False
        return role_resource_view(state, seat).get(NO_SELF_EXPLODE_RESOURCE, 0) <= 0

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
            f"输出 action_type=vote 且 target_seat 为候选座位；不投任何人时输出 "
            f"action_type=pass 且**省略 target_seat**。\n"
            + self._context_block(state, seat, wolf=wolf)
        )
        return self._messages(self._system(), human)

    def side_prompt(self, state: GameState, seat: int, sides: Sequence[str]) -> list[dict[str, str]]:
        """The sheriff picks where today's speaking starts.

        This used to be the only sheriff prompt with no context at all — it named
        the two options and nothing else, so the sheriff did not know who died
        tonight (the anchor), which seats are alive, or that left/right even maps
        onto seat order (the system prompt says seat numbers carry no spatial
        meaning). The rule could not do what it promises — the badge decides who
        speaks first — because the choice was a coin flip. The prompt now hands
        over the anchor, the living seats and each option's real order, read from
        the same ``speech_anchor`` / ``speech_order`` the engine rotates with.
        """
        deaths = night_death_seats(state)
        alive = public_alive_seats(state)
        dead_line = "、".join(f"{item}号" for item in deaths) or "无人"
        alive_line = "、".join(f"{item}号" for item in alive) or "无"
        previews = []
        for side in sides:
            order = day_speech_order(state, side)
            if order:
                head = f"{order[0]}号先发言"
                sequence = " → ".join(f"{item}号" for item in order)
            else:
                head, sequence = "无人可发言", "无"
            previews.append(
                f"{side}（{_SIDE_HINTS.get(side, side)}）：{head}；顺序 {sequence}。"
            )
        human = (
            f"你是警长{seat}号，请选择今天的发言方向。\n"
            f"{self._context_block(state, seat, wolf=False)}"
            f"昨夜死亡：{dead_line}；存活玩家：{alive_line}。\n"
            + "\n".join(previews)
            + f"\n请选择 side，只能是：{'、'.join(sides)}。"
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
            "action_type 为 transfer 时必须给出 target_seat；tear 时省略 target_seat（或给 null）。"
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
                    optional=("target_seat",),
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
                    optional=("target_seat",),
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
