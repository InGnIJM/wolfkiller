"""Regression tests for live election context, not constructor-only wiring."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.prompt_builder import PromptBuilder
from app.core.game_engine import GameEngine
from app.core.night_flow import build_briefing
from app.models.conversation import Conversation, ConversationScope
from app.models.game import GameConfig, GamePhase
from app.persistence.checkpoint_codec import CheckpointCodec
from app.roles.registry import builtin_registry

SPEECH = "竞选证据：2号报告查验3号，请结合此信息判断而非凭空猜测。"


def engine_at_election(tmp_path, invoke):
    roles = {
        1: SimpleNamespace(role_name="wolf-killer-werewolf"),
        2: SimpleNamespace(role_name="wolf-killer-seer"),
        3: SimpleNamespace(role_name="wolf-killer-villager"),
        4: SimpleNamespace(role_name="wolf-killer-villager"),
    }
    engine = GameEngine(
        "sheriff-context", roles=roles, data_dir=str(tmp_path),
        config=GameConfig(role_counts={
            "wolf-killer-werewolf": 1, "wolf-killer-seer": 1,
            "wolf-killer-villager": 2,
        }, enable_sheriff=True),
        director=SimpleNamespace(_invoke=invoke),
    )
    engine._broadcast_phase_change = AsyncMock()
    engine._game_loop = AsyncMock()
    engine.speak = AsyncMock(return_value=SPEECH)
    return engine


def enter_election(engine):
    engine.state.round_number = 1
    engine.state.phase = GamePhase.SHERIFF_ELECTION
    engine.sm.set_state(GamePhase.SHERIFF_ELECTION)


def scripted(captured, *, tie=False, abstain=False):
    calls = []

    def invoke(messages, tool_name, schema, seat):
        human = messages[1]["content"]
        captured.append((tool_name, seat, human))
        if tool_name == "sheriff_campaign":
            return json.dumps({"action_type": "run" if seat in {1, 2} else "pass"})
        if tool_name == "sheriff_withdraw":
            return '{"action_type":"stay"}'
        assert tool_name == "sheriff_vote"
        calls.append(seat)
        target = (1 if seat == 3 else 2) if tie and len(calls) <= 2 else 2
        return json.dumps({"action_type": "pass" if abstain else "vote",
                           "target_seat": None if abstain else target})
    return invoke


@pytest.mark.asyncio
async def test_real_new_game_requests_include_live_speeches(tmp_path):
    captured = []
    engine = engine_at_election(tmp_path, scripted(captured))
    engine.conversation_log.add_public_speech(2, "unused", "STALE_OLD_GAME", 1, "sheriff_election")
    director = engine._sheriff_director
    await engine.create_new()
    enter_election(engine)
    await engine._execute_sheriff_election()
    decisions = [human for tool, _, human in captured if tool in {"sheriff_withdraw", "sheriff_vote"}]
    assert len(decisions) == 4
    assert all(SPEECH in human and "STALE_OLD_GAME" not in human for human in decisions)
    assert engine._sheriff_director is director
    assert engine.state.sheriff == 2


@pytest.mark.asyncio
async def test_restored_election_requests_read_restored_not_constructor_log(tmp_path):
    captured = []
    source = engine_at_election(tmp_path, scripted([]))
    await source.create_new()
    enter_election(source)
    source.state.sheriff_office.candidates = {1, 2}
    source.state.sheriff_office.active = {1, 2}
    source.state.sheriff_office.step = "withdraw"
    await source._publish_public_speech(2, source.state.players[2].role, SPEECH, "sheriff_election")
    codec = CheckpointCodec(builtin_registry.freeze())
    document = codec.encode(source.state, orchestration=source.export_orchestration(codec))
    state, orchestration = codec.decode(json.loads(json.dumps(document)))
    restored = engine_at_election(tmp_path, scripted(captured))
    restored.conversation_log.add_public_speech(2, "unused", "STALE_CONSTRUCTOR", 1, "sheriff_election")
    restored.load_restored_state(state, orchestration, codec)
    await restored._execute_sheriff_election()
    assert len(captured) == 4
    assert all(SPEECH in human and "STALE_CONSTRUCTOR" not in human for _, _, human in captured)


@pytest.mark.asyncio
@pytest.mark.parametrize("abstain", [False, True])
async def test_completed_ballots_enter_later_decisions_not_current_voters(tmp_path, abstain):
    captured = []
    engine = engine_at_election(tmp_path, scripted(captured, abstain=abstain))
    await engine.create_new()
    enter_election(engine)
    await engine._execute_sheriff_election()
    summaries = [r for r in engine.conversation_log.records if r.phase == "sheriff_ballot"]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.scope is ConversationScope.PUBLIC
    assert "3号→弃票" in summary.content if abstain else "3号→2号" in summary.content
    assert "4号→弃票" in summary.content if abstain else "4号→2号" in summary.content
    assert "首轮" in summary.content
    vote_prompts = [h for tool, _, h in captured if tool == "sheriff_vote"]
    assert all("3号→" not in h for h in vote_prompts)
    builder = PromptBuilder()
    engine.state.phase = GamePhase.SPEECH
    for seat, player in engine.state.players.items():
        speech = builder.build_speech_prompt(engine.state, seat, player.role, engine.conversation_log, "day_speech")
        vote = builder.build_vote_prompt(engine.state, seat, player.role, engine.conversation_log, "exile_vote")
        assert SPEECH in speech and SPEECH in vote
        assert summary.content in speech and summary.content in vote
    engine.state.round_number = 2
    assert any(summary.content in line for line in build_briefing(engine.conversation_log, 1, 2).public_lines)
    codec = CheckpointCodec(builtin_registry.freeze())
    document = codec.encode(engine.state, orchestration=engine.export_orchestration(codec))
    state, orchestration = codec.decode(json.loads(json.dumps(document)))
    restored = engine_at_election(tmp_path, scripted([]))
    restored.load_restored_state(state, orchestration, codec)
    assert [r.content for r in restored.conversation_log.records if r.phase == "sheriff_ballot"] == [summary.content]


@pytest.mark.asyncio
async def test_pk_voters_see_first_ballot_not_partial_pk_and_no_private_history(tmp_path):
    captured = []
    engine = engine_at_election(tmp_path, scripted(captured, tie=True))
    await engine.create_new()
    enter_election(engine)
    for scope in (ConversationScope.WEREWOLF, ConversationScope.THOUGHT, ConversationScope.NIGHT_INTEL):
        engine.conversation_log.records.append(Conversation(scope, "PRIVATE_SENTINEL", 1, speaker_seat=1, phase="sheriff_ballot"))
    await engine._execute_sheriff_election()
    votes = [h for t, _, h in captured if t == "sheriff_vote"]
    assert len(votes) == 4
    assert all("3号→1号" not in h for h in votes[:2])
    assert all("3号→1号" in h and "4号→2号" in h for h in votes[2:])
    assert all("PRIVATE_SENTINEL" not in h and "3号→2号" not in h for h in votes)
    summaries = [r for r in engine.conversation_log.get_public() if r.phase == "sheriff_ballot"]
    assert len(summaries) == 2
    assert "PK" in summaries[1].content


@pytest.mark.asyncio
async def test_pk_checkpoint_restores_summary_without_replaying_first_vote(tmp_path):
    captured = []
    engine = engine_at_election(tmp_path, scripted(captured, tie=True))
    await engine.create_new()
    enter_election(engine)
    codec = CheckpointCodec(builtin_registry.freeze())
    documents = []

    async def checkpoint(step_key):
        if step_key.endswith("sheriff_pk_ready:1"):
            documents.append(codec.encode(engine.state, orchestration=engine.export_orchestration(codec)))

    engine._checkpoint_hook = checkpoint
    await engine._execute_sheriff_election()
    assert len(documents) == 1
    state, orchestration = codec.decode(json.loads(json.dumps(documents[0])))
    resumed_calls = []
    restored = engine_at_election(tmp_path, scripted(resumed_calls))
    restored.load_restored_state(state, orchestration, codec)
    await restored._execute_sheriff_election()
    assert [tool for tool, _, _ in resumed_calls] == ["sheriff_vote", "sheriff_vote"]
    assert all("3号→1号" in h for _, _, h in resumed_calls)
    assert len([r for r in restored.conversation_log.records if r.phase == "sheriff_ballot"]) == 2


@pytest.mark.asyncio
async def test_explosion_does_not_publish_partial_ballot(tmp_path):
    def explode(messages, tool, schema, seat):
        return '{"action_type":"explode"}'

    engine = engine_at_election(tmp_path, explode)
    await engine.create_new()
    enter_election(engine)
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.active = {2, 3}
    engine.state.sheriff_office.step = "vote"
    await engine._execute_sheriff_election()
    assert engine.state.sheriff_office.skip_remaining_day
    assert not any(r.phase == "sheriff_ballot" for r in engine.conversation_log.records)


@pytest.mark.asyncio
async def test_mid_ballot_explosion_after_accepted_vote_publishes_nothing(tmp_path):
    """An accepted vote followed by an explosion must not publish a partial ballot."""
    order = []

    def explode_late(messages, tool, schema, seat):
        assert tool == "sheriff_vote"
        order.append(seat)
        if len(order) == 1:
            return '{"action_type":"vote","target_seat":2}'
        return '{"action_type":"explode"}'

    engine = engine_at_election(tmp_path, explode_late)
    engine.roles[1].role_name = "wolf-killer-seer"  # first voter is not a wolf
    await engine.create_new()
    enter_election(engine)
    # seat 4 must be a living wolf for the explode option to exist.
    for player in engine.state.players.values():
        player.role = "wolf-killer-villager"
        player.camp = "good"
    engine.state.players[4].role = "wolf-killer-werewolf"
    engine.state.players[4].camp = "werewolf"
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.active = {2, 3}
    engine.state.sheriff_office.step = "vote"
    await engine._execute_sheriff_election()
    assert order == [1, 4]  # seat 1 accepted a vote, then seat 4 (wolf) exploded
    assert engine.state.players[4].is_alive is False  # exploding wolf dies
    assert engine.state.players[1].is_alive is True  # accepted vote, no ballot published
    assert not any(r.phase == "sheriff_ballot" for r in engine.conversation_log.records)
