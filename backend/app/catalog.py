"""Role display metadata, standard field presets, and custom-field constraints.

Kept OUTSIDE the frozen RoleSpec so adding names/icons/presets never changes
the registry digest and never invalidates persisted game archives.
"""

from __future__ import annotations

ROLE_METADATA: dict[str, dict[str, str]] = {
    "wolf-killer-werewolf": {
        "name_zh": "狼人", "icon": "wolf",
        "description": "夜晚睁眼，与狼队友商议刀人",
    },
    "wolf-killer-villager": {
        "name_zh": "平民", "icon": "villager",
        "description": "白天发言投票，找出狼人",
    },
    "wolf-killer-seer": {
        "name_zh": "预言家", "icon": "seer",
        "description": "每晚查验一名玩家的阵营",
    },
    "wolf-killer-witch": {
        "name_zh": "女巫", "icon": "witch",
        "description": "拥有一瓶解药和一瓶毒药",
    },
    "wolf-killer-hunter": {
        "name_zh": "猎人", "icon": "hunter",
        "description": "死亡时可以开枪带走一名玩家",
    },
    "wolf-killer-guard": {
        "name_zh": "守卫", "icon": "guard",
        "description": "每晚守护一名玩家免于狼刀",
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
