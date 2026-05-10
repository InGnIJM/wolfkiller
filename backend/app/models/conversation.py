from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from typing import Optional


class ConversationScope(str, Enum):
    PUBLIC = "public"              # 全员可见：白天发言、遗言、死亡公告、投票结果
    WEREWOLF = "werewolf"          # 狼人内部频道：夜晚交流
    NIGHT_INTEL = "night_intel"    # 夜间情报：仅特定角色可见（女巫刀口、预言家查验、猎人开枪等），通过 visible_to 控制


@dataclass
class Conversation:
    scope: ConversationScope
    content: str
    round_number: int
    speaker_seat: Optional[int] = None
    speaker_role: Optional[str] = None
    phase: str = ""
    visible_to: Optional[list[int]] = None  # SYSTEM消息时指定可见座位
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "scope": self.scope.value,
            "content": self.content,
            "round_number": self.round_number,
            "speaker_seat": self.speaker_seat,
            "speaker_role": self.speaker_role,
            "phase": self.phase,
            "visible_to": self.visible_to,
            "timestamp": self.timestamp,
        }
