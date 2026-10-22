"""Gravekeeper: a good-camp god that learns the last exile's camp, free of charge.

The role has no contract at all, which is the point: the rules give it no
choice, so spending a model call to answer "pass" every night would buy nothing.
The information arrives as a projected private fact instead, computed in
``ContextProjector._actor_private_facts`` from the death log — the exile's seat
and camp, or ``None`` before anyone has been voted out.

Deriving it beats storing it: the death record already carries the cause and the
round, so a new ``GameState`` field (and the exact-match checkpoint field set
that would have to grow with it) is not needed.
"""

from __future__ import annotations

from app.models.pipeline import RoleSpec
from app.roles.villager import Villager

GRAVEKEEPER_SPEC = RoleSpec(
    role_id="wolf-killer-gravekeeper", display_name="Gravekeeper", camp_id="good",
    contracts=(),
    # No resources either: the brief is projected, not acted for.
    allowed_effects=frozenset(),
    initial_private_data={"last_exiled": None},
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    tags=frozenset({"god"}),
    max_count=1,
    instructions=(
        "你是守墓人（好人神职）：你没有任何主动技能，白天像普通平民一样发言与投票。"
        "每晚你会自动获知**上一个白天被放逐玩家**的阵营（好人 / 狼人），"
        "不需要做任何选择，也不会消耗任何资源。"
        "第一个夜晚还没有人被放逐，因此你当晚没有信息。"
        "被放逐的石像鬼会显示为狼人（它本来就是狼人阵营）。"
        "战术提示：你的信息是「已出局玩家的阵营」，可以用来验证放逐是否正确、"
        "复盘谁在带队投好人；但你无法自证身份，过早公布容易被狼人当作抗推目标。"
        "在需要为好人阵营提供确定性信息时（例如有人跳守墓人对跳），"
        "再报出你的历史查验结果。"
    ),
)


class Gravekeeper(Villager):
    """Gravekeeper: the nightly brief is projected, so the daytime role is bare."""

    pass
