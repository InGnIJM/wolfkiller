"""The night script: every role that acts in the first point declares its line.

The narration used to be a `role_counts` check for the guard alone, so a board
with a dead guard still called it and every other night role acted silently.
The script is now read off the roles that actually received a command, which
keeps it honest in two directions: no line for a seat that is dead or whose
skill has not unlocked yet, and no engine change when a role is added.
"""

from __future__ import annotations

from app.core.night_flow import NightDirector
from app.models.pipeline import SchedulePoint
from app.roles.registry import builtin_registry

#: role id -> the line it must declare, for every role acting in NIGHT_ACTION.
SCRIPT = {
    "wolf-killer-time-wolf": ("蚀时狼妃请睁眼", "请选择要封锁到时间夹缝中的玩家。"),
    "wolf-killer-awakened-hidden-wolf": (
        "觉醒隐狼请睁眼", "请选择一名玩家进行模仿，或使用已获得的技能。",
    ),
    "wolf-killer-miracle-merchant": ("奇迹商人请睁眼", "请选择一名玩家成为幸运儿，并决定赠予他的技能。"),
    "wolf-killer-lonely-girl": ("孤独少女请睁眼", "请选择一名玩家作为你的崇拜对象。"),
    "wolf-killer-fragrance-phantom": ("寻香魅影请睁眼", "请选择两名玩家进行绑定。"),
    "wolf-killer-nightmare": ("噩梦之影请睁眼", "请选择今晚要恐惧的玩家。"),
    "wolf-killer-dancer": ("舞者请睁眼", "请邀请三名玩家共舞。"),
    "wolf-killer-mask": ("假面请睁眼", "请选择今晚要戴面具的玩家。"),
    "wolf-killer-demon-hunter": ("猎魔人请睁眼", "请选择今晚要狩猎的玩家。"),
    "wolf-killer-guard": ("守卫请睁眼", "请选择今晚要守护的玩家。"),
    "wolf-killer-dream-weaver": ("摄梦人请睁眼", "请选择今晚要摄梦的玩家。"),
}


def test_every_first_point_role_declares_a_distinct_opening_line() -> None:
    snapshot = builtin_registry.freeze()
    acting = {
        spec.role_id
        for spec in snapshot.specs.values()
        if any(c.schedule_point is SchedulePoint.NIGHT_ACTION for c in spec.contracts)
    }
    assert acting == set(SCRIPT)
    declared = {role_id: snapshot.require(role_id).opening_narration for role_id in acting}
    assert declared == SCRIPT
    titles = [line[0] for line in declared.values()]
    assert len(set(titles)) == len(titles)


def test_roles_outside_that_point_stay_silent() -> None:
    """首个调度点之外只有「共享查验点」用角色台词。

    查验点（`NIGHT_SEER_ACTION`）由预言家 / 纯白之女 / 狼巫 / 石像鬼共用，用药点
    （`NIGHT_WITCH_ACTION`）由女巫 / 狼美人共用，所以这些角色各自声明台词（否则没有
    预言家的板子会听到「预言家请睁眼」、只有狼美人的板子会听到「女巫请睁眼」，
    2026-10-07 复盘 3.3）。女巫那句依赖当晚刀口、狼刀那句由引擎自己播，两者都不声明
    —— 节拍只属于没声明台词的角色。
    """
    snapshot = builtin_registry.freeze()
    for role_id in ("wolf-killer-witch", "wolf-killer-werewolf"):
        assert snapshot.require(role_id).opening_narration == ()
    for role_id in (
        "wolf-killer-seer", "wolf-killer-pure-white", "wolf-killer-wolf-witch",
        "wolf-killer-gargoyle", "wolf-killer-wolf-beauty",
    ):
        title, text = snapshot.require(role_id).opening_narration
        assert title and text


def test_the_director_table_no_longer_carries_role_openings() -> None:
    """角色开眼旁白归角色自己声明，导演表只留引擎拥有的节拍。"""
    import pytest

    # 夜幕开场与狼人开眼是两个节拍：「天黑请闭眼」由 stage 0 播（先于任何角色
    # 台词），狼人那句只属于狼队讨论窗口。
    assert NightDirector.narration("night_open")[0] == "天黑请闭眼"
    assert NightDirector.narration("wolf_open")[0] == "狼人请睁眼"
    assert NightDirector.narration("pack_sealed")[0] == "狼人阵营被封印"
    with pytest.raises(ValueError):
        NightDirector.narration("guard_open")
