"""Role display metadata, standard field presets, and custom-field constraints.

Kept OUTSIDE the frozen RoleSpec so adding names/icons/presets never changes
the registry digest and never invalidates persisted game archives.

``rules`` holds the player-facing rule bullets shown on the in-app rules page
(``frontend/src/components/rules/``). It is display text only: never read it
from the pipeline, and never move it into a ``RoleSpec`` (that would change the
frozen digest).
"""

from __future__ import annotations

from typing import Any

ROLE_METADATA: dict[str, dict[str, Any]] = {
    "wolf-killer-werewolf": {
        "name_zh": "狼人", "icon": "wolf",
        "description": "夜晚睁眼，与狼队友商议刀人",
        "rules": [
            "夜晚与所有狼队友（含白狼王、狼美人）共用同一次狼刀：先在狼队频道讨论，再各自投票决定当晚刀口，可以空刀。",
            "狼人彼此知道身份；胜利条件是清空神职、清空平民，或让存活狼人数超过存活好人数。",
        ],
    },
    "wolf-killer-villager": {
        "name_zh": "平民", "icon": "villager",
        "description": "白天发言投票，找出狼人",
        "rules": [
            "没有夜间技能，夜晚全程闭眼。",
            "靠白天发言与放逐投票找出狼人；计入平民，是狼人屠边的目标之一。",
        ],
    },
    "wolf-killer-seer": {
        "name_zh": "预言家", "icon": "seer",
        "description": "每晚查验一名玩家的阵营",
        "rules": [
            "每晚查验一名存活玩家的阵营（好人 / 狼人），查验结果只有自己可见。",
            "没有自保能力，需要靠发言把自己验出的信息传递给好人。",
        ],
    },
    "wolf-killer-witch": {
        "name_zh": "女巫", "icon": "witch",
        "description": "拥有一瓶解药和一瓶毒药",
        "rules": [
            "全局各有一瓶解药与一瓶毒药，且每晚至多使用一瓶。",
            "只要存活，每晚都能看到当晚狼刀目标（药已用完也能看到，刀口为空代表空刀）。",
            "解药只能救当晚刀口上的人，自救仅限第一晚；毒药可毒任意存活玩家，无视守卫的守护。",
            "守卫守护与解药同时落在同一刀口上时，该玩家仍然死亡（对穿）。",
        ],
    },
    "wolf-killer-hunter": {
        "name_zh": "猎人", "icon": "hunter",
        "description": "死亡时可以开枪带走一名玩家",
        "rules": [
            "出局时可以开枪带走一名存活玩家，也可以选择不开枪（开枪不是强制的）。",
            "可以开枪的死因：被狼刀、被放逐、被其他猎人开枪带走、被白狼王自爆带走。",
            "被女巫毒杀、被骑士决斗致死、被狼美人殉情带走时不能开枪。",
        ],
    },
    "wolf-killer-guard": {
        "name_zh": "守卫", "icon": "guard",
        "description": "每晚守护一名玩家免于狼刀",
        "rules": [
            "每晚守护一名存活玩家（也可以不守），但不能连续两晚守护同一人。",
            "守护只抵消狼刀：毒药与猎枪无视守护。",
            "与女巫解药同时作用于同一刀口时目标仍死亡（对穿）。",
        ],
    },
    "wolf-killer-idiot": {
        "name_zh": "白痴", "icon": "idiot",
        "description": "被投票出局时翻牌免死，但失去投票权",
        "rules": [
            "被放逐时翻牌：公开身份并免于出局，但仍可发言。",
            "翻牌后失去投票权，也不再出现在放逐候选中（不会被再次放逐）。",
            "被狼刀、被毒杀、被枪杀或被自爆带走时正常死亡；翻牌后只要存活就仍计入神职。",
        ],
    },
    "wolf-killer-werewolf-king": {
        "name_zh": "白狼王", "icon": "wolf_king",
        "description": "狼人，白天发言阶段可自爆带走一名玩家",
        "rules": [
            "夜晚与狼队共用同一次狼刀，按阵营合并计票。",
            "白天每位玩家开口发言前可以自爆一次并带走一名存活玩家：双方都没有遗言，当日剩余发言与投票全部取消，直接进入夜晚。",
            "被毒杀、被放逐或被枪杀时正常出局，不能带人。",
        ],
    },
    "wolf-killer-wolf-king": {
        "name_zh": "狼王", "icon": "wolf_king",
        "description": "狼人，出局时可以发动一次狼王爪带走一名玩家",
        "rules": [
            "夜晚与所有狼队友共用同一次狼刀，按阵营合并计票。",
            "出局时可以发动一次「狼王爪」带走一名存活玩家（也可以不发动）。",
            "可以发动的出局方式：夜间被狼刀、白天被放逐、被猎人开枪带走。",
            "不能发动的出局方式：自爆、被女巫毒杀、被狼美人殉情带走、被骑士决斗出局。",
            "发动时不需要翻牌；被带走的猎人仍然可以开枪反击。",
        ],
    },
    "wolf-killer-knight": {
        "name_zh": "骑士", "icon": "knight",
        "description": "发言结束后投票前翻牌决斗一名玩家，是狼人则白天立即结束",
        "rules": [
            "一局一次：全体发言结束、放逐投票开始前，翻牌决斗一名存活玩家（也可以不发动）。",
            "对方是狼人 → 该玩家立即死亡，并在进入夜晚前发表遗言；当日余下流程（含投票）取消，直接进入夜晚。",
            "对方是好人 → 骑士以死谢罪且没有遗言，当日投票照常进行。",
        ],
    },
    "wolf-killer-wolf-beauty": {
        "name_zh": "狼美人", "icon": "wolf_beauty",
        "description": "狼人，每晚可魅惑一名好人，出局时被魅惑者殉情",
        "rules": [
            "夜晚与狼队共用同一次狼刀；狼刀之后可以额外魅惑一名好人（也可以不魅）。",
            "不能连续两晚魅惑同一人，不能魅惑自己、狼队友或免疫魅惑的老酒鬼。",
            "出局时把当晚被魅惑的玩家一起带走（殉情）；被骑士决斗出局时不带走任何人。殉情者没有遗言，被带走的猎人也不能开枪。",
            "不能自爆，也不能成为狼队的刀口。",
        ],
    },
    "wolf-killer-old-drunkard": {
        "name_zh": "老酒鬼", "icon": "old_drunkard",
        "description": "平民，免疫魅惑；被毒或枪杀延到次日发言结束后死亡",
        "rules": [
            "属于平民（不计入神职），免疫狼美人的魅惑。",
            "被女巫毒杀或被猎人枪杀时不当场死亡：留下「中毒 / 中枪」标记，于次日全体发言结束后、放逐投票前死亡。",
            "其他出局方式（狼刀、放逐、自爆、被决斗或殉情带走）都立即死亡。",
        ],
    },
}

STANDARD_PRESETS: list[dict] = [
    {
        "id": "nine-player-standard",
        "name": "九人标准场",
        "description": "3狼 3民 1预言家 1女巫 1猎人",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
        },
        "enable_sheriff": False,
        "recommended_timeout_seconds": 3600,
    },
    {
        "id": "ten-player-standard",
        "name": "十人标准场",
        "description": "3狼 3民 1预言家 1女巫 1猎人 1守卫",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": False,
        "recommended_timeout_seconds": 3600,
    },
    {
        "id": "twelve-player-idiot",
        "name": "十二人预女猎白",
        "description": "4狼 4民 1预言家 1女巫 1猎人 1白痴",
        "role_counts": {
            "wolf-killer-werewolf": 4,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-idiot": 1,
        },
        "enable_sheriff": True,
        "recommended_timeout_seconds": 7200,
    },
    {
        "id": "twelve-player-wolf-king",
        "name": "十二人白狼王",
        "description": "3狼 1白狼王 4民 1预言家 1女巫 1猎人 1守卫",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-werewolf-king": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "recommended_timeout_seconds": 7200,
    },
    {
        "id": "twelve-player-knight",
        "name": "十二人骑士",
        "description": "4狼 4民 1预言家 1女巫 1猎人 1骑士",
        "role_counts": {
            "wolf-killer-werewolf": 4,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-knight": 1,
        },
        "enable_sheriff": False,
        "recommended_timeout_seconds": 7200,
    },
    {
        "id": "twelve-player-wolf-beauty",
        "name": "十二人狼美人",
        "description": "3狼 1狼美人 3民 1老酒鬼 1预言家 1女巫 1猎人 1守卫",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-beauty": 1,
            "wolf-killer-villager": 3,
            "wolf-killer-old-drunkard": 1,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Wolf Beauty + Knight" tournament board.
        # Its rule set differs from the Jiamian boards above on two points: the
        # witch may never save herself, and a duel by the Knight takes nobody
        # along (``roles/wolf_beauty.py`` drops ``knight_duel`` from the revenge
        # reasons for every board, which is safe because Knight and Wolf Beauty
        # never share a Jiamian preset).
        "id": "twelve-player-wolf-beauty-knight",
        "name": "十二人狼美人骑士",
        "description": "3狼 1狼美人 4民 1预言家 1女巫 1守卫 1骑士",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-beauty": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-guard": 1,
            "wolf-killer-knight": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Wolf King + Guard" tournament board.
        "id": "twelve-player-wolf-king-guard",
        "name": "十二人狼王守卫",
        "description": "3狼 1狼王 4民 1预言家 1女巫 1猎人 1守卫",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-king": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
]

FIELD_CONSTRAINTS: dict[str, int] = {
    "min_players": 4,
    "max_players": 12,
    "min_werewolves": 1,
    "min_good": 1,
}


def _catalog_item(role_id: str, spec) -> dict:
    meta = ROLE_METADATA.get(role_id)
    if meta is None:
        meta = {}
    display = spec.display_name or role_id
    return {
        "role_id": role_id,
        "display_name": display,
        "name_zh": meta.get("name_zh", display),
        "camp": spec.camp_id,
        "icon": meta.get("icon", "unknown"),
        "description": meta.get("description", ""),
        "rules": [rule for rule in meta.get("rules", ()) if isinstance(rule, str)],
        "min_count": spec.min_count,
        "max_count": spec.max_count,
        "dependencies": sorted(spec.dependencies),
        "exclusions": sorted(spec.exclusions),
    }


def role_catalog(registry_snapshot) -> list[dict]:
    """Project the frozen registry snapshot into frontend-facing role items."""
    return [
        _catalog_item(role_id, spec)
        for role_id, spec in sorted(registry_snapshot.specs.items())
    ]
