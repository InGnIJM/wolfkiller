"""How close the table is to an ending, using only what the viewer may know.

Everything here is derivable by the viewer: the public board, who is alive, the
roles dead players revealed, and — for a wolf that sees its pack — its own
team. It never counts hidden gods or villagers.
"""

from __future__ import annotations

from app.core.sheriff_flow import public_alive_seats, public_dead_seats
from app.core.wolf_pack import actor_sees_pack, pack_seats
from app.models.game import GameState
from app.roles.registry import builtin_registry

_WOLF_CAMP = "werewolf"


def _wolf_cards(state: GameState) -> int:
    specs = builtin_registry.freeze().specs
    return sum(
        count for role_id, count in state.config.role_counts.items()
        if role_id in specs and specs[role_id].camp_id == _WOLF_CAMP
    )


def _revealed_wolves(state: GameState) -> int:
    specs = builtin_registry.freeze().specs
    total = 0
    for seat in public_dead_seats(state):
        role = state.players[seat].revealed_role
        if role in specs and specs[role].camp_id == _WOLF_CAMP:
            total += 1
    return total


def stakes_lines(state: GameState, seat: int) -> list[str]:
    alive = len(public_alive_seats(state))
    lines = [
        f"存活 {alive} 人；按每昼夜约减员 2 人估算，最多约还有 {max(alive // 2, 1)} 个白天。",
    ]
    if actor_sees_pack(state, seat):
        wolves = sum(1 for s in pack_seats(state) if state.players[s].is_alive)
        good = max(alive - wolves, 0)
        lines.append(
            f"狼队存活 {wolves}，其余 {good}；狼人数大于其余存活数即狼队获胜，"
            f"再减员 {max(good - wolves + 1, 0)} 名好人即可达成。"
        )
        return lines
    ceiling = max(_wolf_cards(state) - _revealed_wolves(state), 0)
    lines.append(
        f"板上狼人阵营共 {_wolf_cards(state)} 张牌，已公布出局 {_revealed_wolves(state)} 张，"
        f"场上至多还有 {ceiling} 只狼；每一次放逐都在逼近终局。"
    )
    return lines


def stakes_text(state: GameState, seat: int) -> str:
    return "；".join(stakes_lines(state, seat))
