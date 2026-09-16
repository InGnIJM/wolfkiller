from __future__ import annotations
from typing import Optional
from app.models.conversation import Conversation, ConversationScope


class ConversationLog:
    """Manages all conversation records for a game, providing role-filtered views."""

    def __init__(self, logger=None, game_id: str = ""):
        self.records: list[Conversation] = []
        self._logger = logger
        self._game_id = game_id

    def set_persistence(self, logger, game_id: str) -> None:
        self._logger = logger
        self._game_id = game_id

    def _persist(self, record: Conversation) -> None:
        if self._logger and self._game_id:
            self._logger.log_conversation(self._game_id, record.to_dict())

    def log_vote_telemetry(self, round_num: int, seat: int, **data: object) -> None:
        if self._logger and self._game_id:
            self._logger.log_vote_telemetry(self._game_id, round_num, seat, **data)

    def log_llm_call(
        self, round_num: int, seat: int, phase: str, **data: object,
    ) -> None:
        log_call = getattr(self._logger, "log_llm_call", None)
        if self._game_id and callable(log_call):
            log_call(self._game_id, round_num, phase, seat, **data)

    def log_vote_technical_abstain(
        self, round_num: int, seat: int, *, failure_code: str,
        timeout_type: Optional[str] = None, window_id: Optional[str] = None,
    ) -> None:
        log_abstain = getattr(self._logger, "log_vote_technical_abstain", None)
        if self._game_id and callable(log_abstain):
            data = {"failure_code": failure_code, "window_id": window_id}
            if timeout_type is not None:
                data["timeout_type"] = timeout_type
            log_abstain(
                self._game_id, round_num, seat, **data,
            )

    # ── Add methods ──────────────────────────────────────────────

    def add_public_speech(
        self, seat: int, role: str, content: str, round_num: int, phase: str
    ) -> Conversation:
        record = Conversation(
            scope=ConversationScope.PUBLIC,
            speaker_seat=seat,
            speaker_role=role,
            content=content,
            round_number=round_num,
            phase=phase,
        )
        self.records.append(record)
        self._persist(record)
        return record

    def add_death_announcement(
        self, deaths: list, round_num: int
    ) -> Conversation:
        if not deaths:
            content = f"天亮了，昨晚是平安夜，无人死亡。"
        else:
            names = "、".join(str(d.player_seat) for d in deaths)
            content = f"天亮了，昨晚 {names} 号玩家死亡。"
        record = Conversation(
            scope=ConversationScope.PUBLIC,
            content=content,
            round_number=round_num,
            phase="dawn",
        )
        self.records.append(record)
        self._persist(record)
        return record

    def add_vote_result(
        self, votes: list, exiled_seat: Optional[int], round_num: int,
        *, cancelled_seat: Optional[int] = None,
    ) -> Conversation:
        if exiled_seat is not None:
            content = f"投票结果：{exiled_seat}号玩家被放逐出局。"
        elif cancelled_seat is not None:
            content = f"投票结果：{cancelled_seat}号玩家得票最高，但翻牌免于出局。"
        else:
            content = "投票结果：平票，无人被放逐。"
        record = Conversation(
            scope=ConversationScope.PUBLIC,
            content=content,
            round_number=round_num,
            phase="vote_resolution",
        )
        self.records.append(record)
        self._persist(record)
        return record

    def add_system_message(
        self, content: str, round_num: int, scope: str = "public"
    ) -> Conversation:
        scope_map = {"public": ConversationScope.PUBLIC, "werewolf": ConversationScope.WEREWOLF}
        record = Conversation(
            scope=scope_map.get(scope, ConversationScope.PUBLIC),
            content=content,
            round_number=round_num,
            phase="system",
        )
        self.records.append(record)
        self._persist(record)
        return record

    # ── Thought (internal monologue) methods ────────────────────────

    def add_thought(
        self, seat: int, role: str, content: str, round_num: int, phase: str,
    ) -> Conversation:
        """Record a player's internal thought. Only visible to the thinker and the audience."""
        record = Conversation(
            scope=ConversationScope.THOUGHT,
            speaker_seat=seat,
            speaker_role=role,
            content=content,
            round_number=round_num,
            phase=phase,
            visible_to=[seat],
        )
        self.records.append(record)
        self._persist(record)
        return record

    def add_werewolf_channel(
        self, content: str, round_num: int,
        speaker_seat: Optional[int] = None, speaker_role: Optional[str] = None,
    ) -> Conversation:
        """Record one message in the wolves' private night channel."""
        record = Conversation(
            scope=ConversationScope.WEREWOLF,
            content=content,
            round_number=round_num,
            phase="night",
            speaker_seat=speaker_seat,
            speaker_role=speaker_role,
        )
        self.records.append(record)
        self._persist(record)
        return record

    def get_thoughts_for_seat(self, seat: int) -> list[Conversation]:
        """Return all thought records belonging to a specific player."""
        return [
            r for r in self.records
            if r.scope == ConversationScope.THOUGHT and r.speaker_seat == seat
        ]

    def get_all_thoughts(self) -> list[Conversation]:
        """Return all thought records (for audience/frontend view)."""
        return [r for r in self.records if r.scope == ConversationScope.THOUGHT]

    # ── Query methods ─────────────────────────────────────────────

    def get_conversations_for_role(
        self, seat: int, role: str
    ) -> list[Conversation]:
        """Return all conversation records visible to a given player."""
        result: list[Conversation] = []
        is_werewolf = "werewolf" in role
        for record in self.records:
            if record.scope == ConversationScope.PUBLIC:
                result.append(record)
            elif record.scope == ConversationScope.WEREWOLF and is_werewolf:
                result.append(record)
            elif record.scope == ConversationScope.NIGHT_INTEL:
                if record.visible_to and seat in record.visible_to:
                    result.append(record)
        return result

    def get_all(self) -> list[Conversation]:
        return list(self.records)

    def get_public(self) -> list[Conversation]:
        return [r for r in self.records if r.scope == ConversationScope.PUBLIC]

    def get_by_round(self, round_num: int) -> list[Conversation]:
        return [r for r in self.records if r.round_number == round_num]
