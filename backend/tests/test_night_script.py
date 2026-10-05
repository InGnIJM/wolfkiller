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
    """夜间旁白只覆盖首个调度点：预言家/女巫的窗口由导演自己播报。"""
    snapshot = builtin_registry.freeze()
    for role_id in ("wolf-killer-seer", "wolf-killer-witch", "wolf-killer-werewolf"):
        assert snapshot.require(role_id).opening_narration == ()


def test_the_director_table_no_longer_carries_role_openings() -> None:
    """角色开眼旁白归角色自己声明，导演表只留引擎拥有的节拍。"""
    import pytest

    assert NightDirector.narration("wolf_open")[0] == "天黑请闭眼"
    assert NightDirector.narration("pack_sealed")[0] == "狼人阵营被封印"
    with pytest.raises(ValueError):
        NightDirector.narration("guard_open")
