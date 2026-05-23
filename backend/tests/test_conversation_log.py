import pytest
from app.core.conversation_log import ConversationLog
from app.models.conversation import ConversationScope


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
