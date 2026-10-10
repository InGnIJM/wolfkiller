import json

import pytest

from app.models.actions import NightAction, SpeechRecord, VoteAction, DeathReport
from app.models.game import GameState, GamePhase, PlayerState
from app.services.memory_service import MemoryService


def make_state() -> GameState:
    state = GameState(game_id="mem-game", phase=GamePhase.NIGHT, round_number=2)
    state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
        2: PlayerState(2, "wolf-killer-werewolf", "werewolf"),
        3: PlayerState(3, "wolf-killer-villager", "good"),
        4: PlayerState(
            4, "wolf-killer-seer", "good",
            check_results=[{"target_seat": 1, "result": "werewolf", "round": 1}],
        ),
        5: PlayerState(
            5, "wolf-killer-witch", "good",
            has_antidote=True, has_poison=False,
        ),
    }
    state.last_wolf_kill_target = 3
    state.night_actions = [
        NightAction(player_seat=1, action_type="kill", target_seat=3, reasoning=""),
    ]
    state.speeches = [SpeechRecord(player_seat=1, text="大家好", round_number=1)]
    state.votes = [VoteAction(voter_seat=1, target_seat=3)]
    state.death_history = [DeathReport(player_seat=2, cause="wolf_kill", round_number=1)]
    return state


class TestMemoryService:
    def test_wolf_teammates_follow_the_camp_not_the_role_id(self, tmp_path):
        """狼美人的 role id 不含 "werewolf"，旧实现按子串判狼。

        后果是双向的：狼美人自己的队友列表为空，其它狼的列表里也漏掉它 —— 上帝
        视角的记忆面板因此显示了一份不存在的狼队结构。
        """
        service = MemoryService(str(tmp_path))
        state = make_state()
        state.players[1] = PlayerState(1, "wolf-killer-wolf-beauty", "werewolf")
        state.players[2] = PlayerState(2, "wolf-killer-werewolf", "werewolf")

        beauty = service._build_private_knowledge(state, 1, state.players[1])
        wolf = service._build_private_knowledge(state, 2, state.players[2])

        assert beauty["teammates"] == [2]
        assert wolf["teammates"] == [1]

    def test_only_the_real_seer_and_witch_get_their_private_knowledge(self, tmp_path):
        """按角色名子串判定身份会把「名字里带 seer/witch 的角色」也算进去。

        这类判定的历史后果已经出现过两次（狼队按子串判狼漏掉狼美人、神职按子串计数
        漏掉骑士）；这里改成与权威角色 id 相等，任何取名相近的角色都不再误命中。
        """
        service = MemoryService(str(tmp_path))
        state = make_state()
        state.players[3] = PlayerState(
            3, "wolf-killer-seer-imitator", "good",
            check_results=[{"target_seat": 1, "result": "werewolf", "round": 1}],
        )

        knowledge = service._build_private_knowledge(state, 3, state.players[3])

        assert knowledge["check_results"] == []
        assert knowledge["last_wolf_kill_target"] is None

    def test_save_memories_writes_one_file_per_player(self, tmp_path):
        service = MemoryService(str(tmp_path))
        state = make_state()

        service.save_memories(state)

        memory_dir = service.get_memory_dir(state.game_id)
        files = sorted(path.name for path in memory_dir.iterdir())
        assert files == [
            "seat_1_wolf-killer-werewolf.json",
            "seat_2_wolf-killer-werewolf.json",
            "seat_3_wolf-killer-villager.json",
            "seat_4_wolf-killer-seer.json",
            "seat_5_wolf-killer-witch.json",
        ]

    def test_memory_content_includes_private_knowledge_and_history(self, tmp_path):
        from app.core.effect_applier import _Runtime
        service = MemoryService(str(tmp_path))
        state = make_state()
        # 药在 canonical 视图里；`PlayerState.has_*` 只是检查点兼容字段，不再被读。
        state._pipeline_runtime = _Runtime(role_resources={5: {"antidote": 1, "poison": 0}})

        service.save_memories(state)

        wolf = json.loads(
            (service.get_memory_dir(state.game_id) / "seat_1_wolf-killer-werewolf.json").read_text("utf-8")
        )
        assert wolf["role"] == "wolf-killer-werewolf"
        assert wolf["camp"] == "werewolf"
        assert wolf["private_knowledge"]["teammates"] == [2]
        night_actions = [item["action"] for item in wolf["action_history"] if item["phase"] == "night"]
        assert len(night_actions) == 1
        assert night_actions[0]["player_seat"] == 1
        assert night_actions[0]["action_type"] == "kill"
        assert night_actions[0]["target_seat"] == 3
        assert any(item["phase"] == "speech" for item in wolf["action_history"])
        assert any(item["phase"] == "vote_casting" for item in wolf["action_history"])
        assert wolf["witnessed_events"] == [
            {"round": 1, "event": "death", "details": {"player_seat": 2, "cause": "wolf_kill", "round_number": 1}}
        ]

        seer = json.loads(
            (service.get_memory_dir(state.game_id) / "seat_4_wolf-killer-seer.json").read_text("utf-8")
        )
        assert seer["private_knowledge"]["check_results"] == [
            {"target_seat": 1, "result": "werewolf", "round": 1}
        ]
        witch = json.loads(
            (service.get_memory_dir(state.game_id) / "seat_5_wolf-killer-witch.json").read_text("utf-8")
        )
        assert witch["private_knowledge"]["last_wolf_kill_target"] == 3
        assert witch["private_knowledge"]["has_antidote"] is True
        assert witch["private_knowledge"]["has_poison"] is False

        villager = json.loads(
            (service.get_memory_dir(state.game_id) / "seat_3_wolf-killer-villager.json").read_text("utf-8")
        )
        assert villager["private_knowledge"]["teammates"] == []
        assert villager["action_history"] == []

    def test_load_memory_returns_none_without_dir_or_matches(self, tmp_path):
        service = MemoryService(str(tmp_path))
        assert service.load_memory("missing", 1) is None

        (service.get_memory_dir("empty-game")).mkdir(parents=True)
        assert service.load_memory("empty-game", 1) is None

    def test_load_memory_round_trips_saved_memory(self, tmp_path):
        service = MemoryService(str(tmp_path))
        state = make_state()
        service.save_memories(state)

        loaded = service.load_memory(state.game_id, 4)
        assert loaded is not None
        assert loaded["role"] == "wolf-killer-seer"
        assert loaded["seat_number"] == 4

    def test_witch_potions_read_pipeline_resources_over_legacy_fields(self, tmp_path):
        from app.core.effect_applier import _Runtime
        service = MemoryService(str(tmp_path))
        state = make_state()
        # Runtime says the antidote was spent and the poison is still held,
        # which must override the legacy PlayerState flags (True / False).
        state._pipeline_runtime = _Runtime(role_resources={5: {"antidote": 0, "poison": 1}})

        service.save_memories(state)

        witch = json.loads(
            (service.get_memory_dir(state.game_id) / "seat_5_wolf-killer-witch.json").read_text("utf-8")
        )
        assert witch["private_knowledge"]["has_antidote"] is False
        assert witch["private_knowledge"]["has_poison"] is True

    def test_potions_read_the_canonical_view_only(self, tmp_path):
        """canonical 里没有该座位 → 读成「没有药」，不回落到 `PlayerState.has_*`。

        那三个镜像字段在生产代码里只读不写（资源统一走 `runtime.role_resources`），
        回落只会把「还没有 runtime」显示成「没有枪/没有药」之外的假象。
        """
        from app.core.effect_applier import _Runtime
        service = MemoryService(str(tmp_path))
        state = make_state()
        state._pipeline_runtime = _Runtime(role_resources={})

        service.save_memories(state)

        witch = json.loads(
            (service.get_memory_dir(state.game_id) / "seat_5_wolf-killer-witch.json").read_text("utf-8")
        )
        assert witch["private_knowledge"]["has_antidote"] is False
        assert witch["private_knowledge"]["has_poison"] is False
