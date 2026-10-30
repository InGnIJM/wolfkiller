"""Per-seat persona cards: expression style and risk appetite, nothing else.

Twelve seats driven by one model read the same prompt and converge on the same
cold analytic voice. A persona decorrelates them without touching rules or
information: it is derived (never stored) from ``game_id`` and the seat number,
so a resumed game deals the same card to the same seat and no checkpoint field
is needed.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    persona_id: str
    label: str
    style: str


PERSONA_DECK: tuple[Persona, ...] = (
    Persona("aggressive", "激进冲锋型", "发言鲜明、敢下判断、愿意承担被怀疑的风险；讨厌和稀泥，宁可错推也不观望，语气带压迫感。"),
    Persona("analyst", "冷静数据流", "靠票型、发言顺序与逻辑链说话，语气克制，每个结论都附上可核对的依据。"),
    Persona("troublemaker", "搅局钓鱼型", "喜欢抛出有争议的观点、试探他人反应，制造对立来逼出破绽。"),
    Persona("lowprofile", "低调潜伏型", "不抢话语权，发言简短克制，只在关键节点亮出明确立场。"),
    Persona("showman", "表演张扬型", "发言有节奏、有态度，爱立 flag 和公开承诺，用气势带动场上情绪。"),
    Persona("skeptic", "逆风怀疑论型", "习惯质疑主流意见，当全场风向一致时主动寻找被忽视的反面证据。"),
    Persona("cautious", "谨慎惜命型", "重视自保与信息安全，立场表达留有余地，但在被点名时会果断自辩。"),
    Persona("balanced", "均衡型", "按局势灵活切换进攻与防守，不固守单一套路。"),
)

PERSONA_GUARD = "风格只影响你的表达方式与风险偏好，不改变你的胜利目标与信息边界。"


def persona_for(game_id: str, seat: int) -> Persona:
    """The card for ``seat``; stable for a given game and seat, any resume."""
    digest = hashlib.sha256(game_id.encode("utf-8")).digest()
    order = list(range(len(PERSONA_DECK)))
    random.Random(digest).shuffle(order)
    return PERSONA_DECK[order[(seat - 1) % len(order)]]


def persona_line(game_id: str, seat: int) -> str:
    """The Chinese directive line for day / sheriff prompts."""
    card = persona_for(game_id, seat)
    return f"你的风格：{card.label}——{card.style}{PERSONA_GUARD}"
