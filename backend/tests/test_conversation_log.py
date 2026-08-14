import pytest
from unittest.mock import MagicMock
from app.core.conversation_log import ConversationLog
from app.models.conversation import Conversation, ConversationScope


class TestConversationLogLifecycle:
    def test_set_persistence_enables_writes_to_logger(self):
        logger = MagicMock()
        log = ConversationLog()
        log.set_persistence(logger, "game-x")

        record = log.add_public_speech(1, "wolf-killer-villager", "大家好", 1, "speech")

        logger.log_conversation.assert_called_once_with("game-x", record.to_dict())

    def test_death_announcement_with_and_without_deaths(self):
        from app.models.actions import DeathReport
        log = ConversationLog()
        peaceful = log.add_death_announcement([], 1)
        assert "平安夜" in peaceful.content

        with_deaths = log.add_death_announcement(
            [DeathReport(1, "wolf_kill", 1), DeathReport(2, "poison", 1)], 1,
        )
        assert "1" in with_deaths.content and "2" in with_deaths.content

    def test_vote_result_with_and_without_exile(self):
        log = ConversationLog()
        exiled = log.add_vote_result([], 3, 1)
        assert "3号玩家被放逐" in exiled.content
        tied = log.add_vote_result([], None, 1)
        assert "平票" in tied.content

    def test_system_message_scopes(self):
        log = ConversationLog()
        public = log.add_system_message("公开提示", 1, "public")
        assert public.scope is ConversationScope.PUBLIC
        camp = log.add_system_message("阵营提示", 1, "werewolf")
        assert camp.scope is ConversationScope.WEREWOLF

    def test_add_werewolf_channel_visible_only_to_wolves(self):
        log = ConversationLog()
        record = log.add_werewolf_channel("提议刀 2 号：理由", 3)
        assert record.scope is ConversationScope.WEREWOLF
        assert record.phase == "night"
        assert record.round_number == 3

        wolf_view = log.get_conversations_for_role(1, "wolf-killer-werewolf")
        assert [item.content for item in wolf_view] == ["提议刀 2 号：理由"]
        villager_view = log.get_conversations_for_role(2, "wolf-killer-villager")
        assert villager_view == []

    def test_get_all_get_public_and_get_by_round(self):
        log = ConversationLog()
        log.add_public_speech(1, "wolf-killer-villager", "第一轮", 1, "speech")
        log.add_public_speech(1, "wolf-killer-villager", "第二轮", 2, "speech")
        log.add_thought(1, "wolf-killer-villager", "思考", 2, "speech")

        assert len(log.get_all()) == 3
        assert len(log.get_public()) == 2
        assert len(log.get_by_round(2)) == 2

    def test_conversations_for_role_filters_night_intel_by_visible_to(self):
        log = ConversationLog()
        log.records.append(Conversation(
            ConversationScope.NIGHT_INTEL, "只给2号看的情报", 1, phase="night", visible_to=[2],
        ))
        log.records.append(Conversation(
            ConversationScope.NIGHT_INTEL, "只给3号看的情报", 1, phase="night", visible_to=[3],
        ))

        visible_to_2 = log.get_conversations_for_role(2, "wolf-killer-witch")
        assert [record.content for record in visible_to_2] == ["只给2号看的情报"]
        visible_to_1 = log.get_conversations_for_role(1, "wolf-killer-villager")
        assert visible_to_1 == []


class TestConversationLogThoughts:
    def test_add_thought_creates_record(self):
        log = ConversationLog()
        record = log.add_thought(1, "wolf-killer-werewolf", "我分析了局势", 1, "day_speech")
        assert record.scope == ConversationScope.THOUGHT
        assert record.speaker_seat == 1
        assert record.content == "我分析了局势"
        assert record.visible_to == [1]

    def test_get_thoughts_for_seat_returns_only_own(self):
        log = ConversationLog()
        log.add_thought(1, "wolf-killer-werewolf", "1号的思考", 1, "day_speech")
        log.add_thought(2, "wolf-killer-villager", "2号的思考", 1, "day_speech")
        log.add_thought(1, "wolf-killer-werewolf", "1号的第二次思考", 1, "vote_casting")

        thoughts_1 = log.get_thoughts_for_seat(1)
        assert len(thoughts_1) == 2
        assert all(t.speaker_seat == 1 for t in thoughts_1)

    def test_get_thoughts_for_seat_empty(self):
        log = ConversationLog()
        thoughts = log.get_thoughts_for_seat(1)
        assert thoughts == []

    def test_get_all_thoughts_returns_all(self):
        log = ConversationLog()
        log.add_thought(1, "wolf-killer-werewolf", "1号的思考", 1, "day_speech")
        log.add_thought(3, "wolf-killer-villager", "3号的思考", 1, "day_speech")

        all_thoughts = log.get_all_thoughts()
        assert len(all_thoughts) == 2

    def test_thoughts_not_in_conversations_for_role(self):
        """Thoughts should NOT appear in get_conversations_for_role."""
        log = ConversationLog()
        log.add_thought(1, "wolf-killer-werewolf", "我的思考", 1, "day_speech")
        log.add_public_speech(1, "wolf-killer-werewolf", "我的发言", 1, "speech")

        conversations = log.get_conversations_for_role(1, "wolf-killer-werewolf")
        scopes = [r.scope for r in conversations]
        assert ConversationScope.THOUGHT not in scopes
        assert ConversationScope.PUBLIC in scopes

    def test_add_thought_with_different_contexts(self):
        log = ConversationLog()
        log.add_thought(1, "wolf-killer-werewolf", "刀人思考", 1, "night_kill")
        log.add_thought(1, "wolf-killer-werewolf", "发言思考", 2, "day_speech")
        log.add_thought(1, "wolf-killer-werewolf", "投票思考", 2, "exile_vote")

        thoughts = log.get_thoughts_for_seat(1)
        assert len(thoughts) == 3
        phases = [t.phase for t in thoughts]
        assert "night_kill" in phases
        assert "day_speech" in phases
        assert "exile_vote" in phases
