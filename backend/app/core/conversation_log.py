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

    def add_werewolf_chat(
        self, seat: int, role: str, content: str, round_num: int
    ) -> Conversation:
        record = Conversation(
            scope=ConversationScope.WEREWOLF,
            speaker_seat=seat,
            speaker_role=role,
            content=content,
            round_number=round_num,
            phase="night",
        )
        self.records.append(record)
        self._persist(record)
        return record

    def add_night_intel(
        self, content: str, round_num: int, phase: str, visible_to: list[int]
    ) -> Conversation:
        record = Conversation(
            scope=ConversationScope.NIGHT_INTEL,
            content=content,
            round_number=round_num,
            phase=phase,
            visible_to=visible_to,
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
        self, votes: list, exiled_seat: Optional[int], round_num: int
    ) -> Conversation:
        if exiled_seat is not None:
            content = f"投票结果：{exiled_seat}号玩家被放逐出局。"
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
