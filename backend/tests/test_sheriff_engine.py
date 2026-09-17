from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.config import PipelineMode
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.core.role_pipeline import PipelineResult
from app.core.sheriff_flow import DEATH_LEFT, SHERIFF_LEFT, SHERIFF_RIGHT, set_sheriff
from app.models.actions import DeathReport, VoteAction
from app.models.game import GameConfig, GamePhase, PlayerState
import app.core.game_engine as game_engine_module


class ScriptedSheriff:
    def __init__(
        self, campaign=(), withdraw=(), votes=(), side=SHERIFF_LEFT,
        badge=("tear", None),
    ):
        self.campaign = list(campaign)
        self.withdraw = list(withdraw)
        self.votes = list(votes)
        self.side = side
        self.badge = badge

    def campaign_turn(self, state, seat, *, wolf):
        return self.campaign.pop(0) if self.campaign else "pass"

    def withdraw_turn(self, state, seat, *, wolf):
        return self.withdraw.pop(0) if self.withdraw else "stay"

    def vote_turn(self, state, seat, candidates, *, wolf):
        return self.votes.pop(0) if self.votes else None

    def side_turn(self, state, seat, sides):
        return self.side if self.side in sides else sides[0]

    def badge_turn(self, state, seat, targets):
        return self.badge


def _engine(*, seats=4, wolves=1, enable=True) -> GameEngine:
    god = 0 if seats <= wolves else 1
    config = GameConfig(
        role_counts={
            "wolf-killer-werewolf": wolves,
            "wolf-killer-seer": god,
            "wolf-killer-villager": max(seats - wolves - god, 0),
        },
        enable_sheriff=enable,
    )
    engine = GameEngine("sheriff-engine", config=config, event_bus=EventBus())
    for seat in range(1, seats + 1):
        if seat <= wolves:
            role, camp = "wolf-killer-werewolf", "werewolf"
        elif seat == wolves + 1 and god:
            role, camp = "wolf-killer-seer", "good"
        else:
            role, camp = "wolf-killer-villager", "good"
        engine.state.players[seat] = PlayerState(seat, role, camp)
    engine.state.round_number = 1
    engine.sm.set_state(GamePhase.SHERIFF_ELECTION)
    engine.state.phase = GamePhase.SHERIFF_ELECTION
    return engine


@pytest.mark.asyncio
async def test_game_loop_runs_sheriff_election() -> None:
    engine = _engine()
    engine._running = True
    engine._wait_if_paused = AsyncMock()
    engine._execute_sheriff_election = AsyncMock(
        side_effect=lambda: engine.sm.set_state(GamePhase.GAME_OVER),
    )
    await engine._game_loop()
    engine._execute_sheriff_election.assert_awaited()


@pytest.mark.asyncio
async def test_election_requires_phase_and_resumes_when_already_complete() -> None:
    engine = _engine(enable=False)
    engine.sm.set_state(GamePhase.NIGHT)
    with pytest.raises(RuntimeError, match="SHERIFF_ELECTION"):
        await engine._execute_sheriff_election()
    engine.sm.set_state(GamePhase.SHERIFF_ELECTION)
    engine.state.sheriff_election_complete = True
    await engine._execute_sheriff_election()
    assert engine.sm.get_state() is GamePhase.DAWN


@pytest.mark.asyncio
async def test_zero_candidates_destroys_badge() -> None:
    engine = _engine()
    await engine._execute_sheriff_election()
    assert engine.state.sheriff is None
    assert engine.state.sheriff_office.badge_destroyed is True
    assert engine.state.sheriff_election_complete is True
    assert engine.sm.get_state() is GamePhase.DAWN


@pytest.mark.asyncio
async def test_single_candidate_is_elected_without_vote() -> None:
    engine = _engine()
    engine._sheriff_director = ScriptedSheriff(campaign=["pass", "run"])
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 2
    assert engine.state.players[2].is_sheriff is True


@pytest.mark.asyncio
async def test_withdraw_to_one_candidate_and_skip_dead_campaigners() -> None:
    engine = _engine()
    engine.state.players[4].is_alive = False
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run"],
        withdraw=["withdraw", "stay"],
    )
    engine.speak = AsyncMock(return_value="请投给我，我会认真履职保护好人。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 3
    assert engine.speak.await_count == 2


@pytest.mark.asyncio
async def test_vote_elects_and_pk_breaks_a_tie() -> None:
    engine = _engine()
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass"],
        withdraw=["stay", "stay"],
        votes=[2, 3, 2, 2],
    )
    engine.speak = AsyncMock(return_value="平票加赛，请再听我一次竞选发言。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 2
    assert engine.state.sheriff_office.pk_seats == {2, 3}


@pytest.mark.asyncio
async def test_tied_pk_and_empty_tally_destroy_the_badge() -> None:
    engine = _engine()
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass"],
        withdraw=["stay", "stay"],
        votes=[2, 3, 2, 3],
    )
    engine.speak = AsyncMock(return_value="加赛发言请再考虑一下我的上警理由。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff is None
    assert engine.state.sheriff_office.badge_destroyed is True

    engine = _engine()
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass"],
        withdraw=["stay", "stay"],
        votes=[None, None],
    )
    engine.speak = AsyncMock(return_value="我会认真当警长，请大家投票给我。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff is None


@pytest.mark.asyncio
async def test_all_alive_remain_after_withdraw_destroys_badge() -> None:
    engine = _engine(seats=3)
    engine._sheriff_director = ScriptedSheriff(
        campaign=["run", "run", "run"],
        withdraw=["stay", "stay", "stay"],
    )
    engine.speak = AsyncMock(return_value="全员上警后我选择继续留在警上。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff is None
    assert engine.state.sheriff_office.badge_destroyed is True


@pytest.mark.asyncio
async def test_election_explode_swallows_badge_and_skips_the_day() -> None:
    engine = _engine(wolves=2)
    engine._sheriff_director = ScriptedSheriff(campaign=["explode"])
    await engine._execute_sheriff_election()
    assert engine.state.players[1].is_alive is False
    assert engine.state.sheriff_office.skip_remaining_day is True
    assert engine.state.sheriff_office.badge_destroyed is True
    assert engine.sm.get_state() is GamePhase.DAWN
    engine.sm.set_state(GamePhase.SPEECH)
    interrupted = await engine._execute_speech_round()
    assert interrupted is True
    assert engine.sm.get_state() is GamePhase.NIGHT
    assert engine.state.sheriff_office.skip_remaining_day is False


@pytest.mark.asyncio
async def test_explode_on_withdraw_and_vote_and_dead_player_is_ignored() -> None:
    engine = _engine(wolves=2)
    engine._sheriff_director = ScriptedSheriff(
        campaign=["run", "run"],
        withdraw=["explode"],
    )
    engine.speak = AsyncMock(return_value="先听我竞选，再决定是否退水。")
    await engine._execute_sheriff_election()
    assert engine.state.players[1].is_alive is False
    assert engine.state.sheriff_office.skip_remaining_day is True

    engine = _engine(wolves=2)
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass"],
        withdraw=["stay", "stay"],
        votes=["explode"],
    )
    engine.speak = AsyncMock(return_value="投票前我再强调一次上警理由请支持。")
    await engine._execute_sheriff_election()
    assert engine.state.players[1].is_alive is False

    engine = _engine()
    engine.state.players[1].mark_dead("self_explode")
    await engine._election_explode(1)
    await engine._election_explode(99)
    assert engine.state.sheriff_election_complete is False


@pytest.mark.asyncio
async def test_finish_and_explode_stop_when_game_is_already_over() -> None:
    engine = _engine()
    engine._check_game_over = AsyncMock(return_value=True)
    await engine._finish_election(2, "auto")
    assert engine.sm.get_state() is GamePhase.SHERIFF_ELECTION
    engine = _engine(wolves=2)
    engine._check_game_over = AsyncMock(return_value=True)
    await engine._election_explode(1)
    assert engine.sm.get_state() is GamePhase.SHERIFF_ELECTION
    engine.sm.set_state(GamePhase.SPEECH)
    engine.state.sheriff_office.skip_remaining_day = True
    assert await engine._execute_speech_round() is True


@pytest.mark.asyncio
async def test_resume_campaign_withdraw_vote_and_pk_steps() -> None:
    engine = _engine()
    engine.state.sheriff_office.step = "campaign"
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.active = {2, 3}
    engine._sheriff_director = ScriptedSheriff(
        withdraw=["stay", "stay"], votes=[2, None],
    )
    engine.speak = AsyncMock(return_value="续跑竞选发言，请投给我这个位置。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 2

    engine = _engine()
    engine.state.sheriff_office.step = "vote"
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.active = {2, 3}
    engine._sheriff_director = ScriptedSheriff(votes=[3, 3])
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 3

    engine = _engine()
    engine.state.sheriff_office.step = "pk"
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.pk_seats = {2, 3}
    engine.state.players[2].is_alive = False
    engine._sheriff_director = ScriptedSheriff(votes=[3])
    engine.speak = AsyncMock(return_value="PK 加赛发言请再投我一次谢谢。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 3

    engine = _engine()
    engine.state.sheriff_office.step = "campaign"
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.active = {2, 3, 9}
    engine.speak = AsyncMock(return_value=None)
    engine._sheriff_director = ScriptedSheriff(
        withdraw=["stay", "stay"], votes=[2, None],
    )
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 2

    engine = _engine()
    engine.state.sheriff_office.step = "withdraw"
    engine.state.sheriff_office.candidates = {2, 3, 9}
    engine.state.players[3].is_alive = False
    engine._sheriff_director = ScriptedSheriff(withdraw=["stay"])
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 2

    engine = _engine()
    engine.state.sheriff_office.step = "done"
    await engine._execute_sheriff_election()
    assert engine.sm.get_state() is GamePhase.SHERIFF_ELECTION
    assert engine.state.sheriff is None

    engine = _engine()
    engine.state.sheriff_office.step = "pk"
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.pk_seats = {2, 3}
    engine.speak = AsyncMock(return_value=None)
    engine._sheriff_director = ScriptedSheriff(votes=[3, None])
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 3


class _KillVoterSheriff(ScriptedSheriff):
    def vote_turn(self, state, seat, candidates, *, wolf):
        if seat == 1:
            state.players[4].is_alive = False
        return super().vote_turn(state, seat, candidates, wolf=wolf)


@pytest.mark.asyncio
async def test_vote_skips_a_voter_who_dies_mid_round() -> None:
    engine = _engine()
    engine.state.sheriff_office.step = "vote"
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.active = {2, 3}
    engine._sheriff_director = _KillVoterSheriff(votes=[2])
    await engine._execute_sheriff_election()
    assert engine.state.sheriff == 2


@pytest.mark.asyncio
async def test_badge_transfer_tear_and_skip_when_disabled() -> None:
    engine = _engine(enable=False)
    await engine._maybe_reassign_badge()
    engine = _engine()
    set_sheriff(engine.state, 2)
    await engine._maybe_reassign_badge()
    engine.state.players[2].mark_dead("wolf_kill")
    engine._sheriff_director = ScriptedSheriff(badge=("transfer", 3))
    await engine._maybe_reassign_badge()
    assert engine.state.sheriff == 3
    engine.state.players[3].mark_dead("hunter_shot")
    engine._sheriff_director = ScriptedSheriff(badge=("tear", None))
    await engine._maybe_reassign_badge()
    assert engine.state.sheriff is None
    assert engine.state.sheriff_office.badge_destroyed is True
    await engine._maybe_reassign_badge()


@pytest.mark.asyncio
async def test_speech_side_and_order_and_half_votes() -> None:
    engine = _engine()
    set_sheriff(engine.state, 3)
    await engine._choose_speech_side()
    assert engine.state.sheriff_office.speech_side == SHERIFF_LEFT
    alive = list(engine.state.alive_players().items())
    ordered = engine._rotate_speech_order(alive)
    assert [seat for seat, _ in ordered] == [4, 1, 2, 3]
    engine.state.sheriff_office.speech_side = DEATH_LEFT
    engine.state.death_history = [DeathReport(2, "wolf_kill", 1)]
    ordered = engine._rotate_speech_order(list(engine.state.alive_players().items()))
    assert [seat for seat, _ in ordered][0] == 3
    engine.state.death_history = []
    engine.state.sheriff_office.speech_side = SHERIFF_RIGHT
    ordered = engine._rotate_speech_order(list(engine.state.alive_players().items()))
    assert [seat for seat, _ in ordered] == [2, 1, 4, 3]
    await engine._choose_speech_side()
    engine._prepare_night()
    assert engine.state.sheriff_office.speech_side is None
    off = _engine(enable=False)
    await off._choose_speech_side()
    assert off.state.sheriff_office.speech_side is None
    engine.state.death_history = [DeathReport(2, "wolf_kill", engine.state.round_number)]
    fallback = engine._rotate_speech_order(list(engine.state.alive_players().items()))
    assert [seat for seat, _ in fallback][0] == 3
    engine.state.votes = [VoteAction(3, 1), VoteAction(4, 2)]
    set_sheriff(engine.state, 3)
    tally = engine._tally_votes()
    assert tally == {1: 3, 2: 2}


@pytest.mark.asyncio
async def test_night_complete_forks_to_sheriff_election() -> None:
    engine = _engine()
    engine.sm.set_state(GamePhase.NIGHT)
    engine.state.phase = GamePhase.NIGHT
    result = PipelineResult((), (), "digest", (), PipelineMode.V2)
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(
        result, deaths=(), stage=6, win_checked=True,
    )
    engine.rule_engine.check_win = lambda state: None
    engine._broadcast_phase_change = AsyncMock()
    await engine._execute_night()
    assert engine.sm.get_state() is GamePhase.SHERIFF_ELECTION
    assert engine.state.phase is GamePhase.SHERIFF_ELECTION


@pytest.mark.asyncio
async def test_night_transition_raise_marks_sheriff_election_phase() -> None:
    engine = _engine()
    engine.sm.set_state(GamePhase.NIGHT)
    result = PipelineResult((), (), "digest", (), PipelineMode.V2)
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(
        result, deaths=(), stage=6, win_checked=True,
    )
    engine.rule_engine.check_win = lambda state: None

    def explode(event):
        engine.sm.set_state(GamePhase.SHERIFF_ELECTION)
        raise RuntimeError("transition exploded")

    engine.sm.transition = explode
    with pytest.raises(RuntimeError, match="transition exploded"):
        await engine._execute_night()
    assert engine.state.phase is GamePhase.SHERIFF_ELECTION
    assert engine._pending_night_completion.stage == 7


def _night_pending(engine: GameEngine) -> None:
    result = PipelineResult((), (), "digest", (), PipelineMode.V2)
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(
        result, deaths=(), stage=6, win_checked=True,
    )
    engine.rule_engine.check_win = lambda state: None
    engine._broadcast_phase_change = AsyncMock()
    engine.sm.set_state(GamePhase.NIGHT)
    engine.state.phase = GamePhase.NIGHT


@pytest.mark.asyncio
async def test_night_complete_goes_to_dawn_when_election_is_done_or_disabled() -> None:
    engine = _engine()
    engine.state.sheriff_election_complete = True
    _night_pending(engine)
    await engine._execute_night()
    assert engine.sm.get_state() is GamePhase.DAWN

    engine = _engine(enable=False)
    _night_pending(engine)
    await engine._execute_night()
    assert engine.sm.get_state() is GamePhase.DAWN


@pytest.mark.asyncio
async def test_campaign_and_pk_speeches_are_public_and_do_not_block_day_speech() -> None:
    engine = _engine()
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass"],
        withdraw=["stay", "stay"],
        votes=[2, 3, 2, 2],
        side=SHERIFF_LEFT,
    )
    audience: list[tuple[str, dict]] = []
    original = engine.game_logger.log_audience_action

    def capture(game_id, round_num, phase, event_type, payload):
        audience.append((event_type, dict(payload)))
        original(game_id, round_num, phase, event_type, payload)

    engine.game_logger.log_audience_action = capture
    engine.speak = AsyncMock(return_value="请投给我，我会认真履职保护好人。")
    await engine._execute_sheriff_election()
    campaign = [record for record in engine.state.speeches if record.phase == "sheriff_election"]
    assert campaign
    assert {item[0] for item in audience} >= {
        "SHERIFF_RUN", "SHERIFF_WITHDRAW", "SHERIFF_VOTE", "SHERIFF_ELECTED",
    }
    assert any(item[0] == "SHERIFF_VOTE" and item[1]["kind"] == "pk" for item in audience)
    engine.sm.set_state(GamePhase.SPEECH)
    engine.state.phase = GamePhase.SPEECH
    spoken: list[tuple[int, str]] = []

    async def capture_speak(seat, kind):
        spoken.append((seat, kind))
        return f"{seat}号白天发言请大家听我分析局势再投票。"

    engine.speak = capture_speak
    await engine._execute_speech_round()
    assert any(item[0] == "SHERIFF_SIDE" for item in audience)
    assert [kind for _, kind in spoken] == ["day_speech"] * 4
    day = [record for record in engine.state.speeches if record.phase == "speech"]
    assert {record.player_seat for record in day} == {1, 2, 3, 4}


@pytest.mark.asyncio
async def test_pk_speech_appends_speech_record_with_election_phase() -> None:
    engine = _engine()
    engine.state.sheriff_office.step = "pk"
    engine.state.sheriff_office.candidates = {2, 3}
    engine.state.sheriff_office.pk_seats = {2, 3}
    engine.speak = AsyncMock(return_value="PK 加赛发言请再投我一次谢谢。")
    engine._sheriff_director = ScriptedSheriff(votes=[3, None])
    await engine._execute_sheriff_election()
    assert [record.phase for record in engine.state.speeches] == [
        "sheriff_election", "sheriff_election",
    ]
    assert {record.player_seat for record in engine.state.speeches} == {2, 3}


# ── Context propagation & explicit badge-loss announcements ──

class RecordingSheriff:
    """Scripted director that captures every prompt it receives."""

    def __init__(self, campaign=(), withdraw=(), votes=()):
        self.campaign = list(campaign)
        self.withdraw = list(withdraw)
        self.votes = list(votes)
        self.side = SHERIFF_LEFT
        self.badge = ("tear", None)
        self.humans: list[str] = []

    def _record(self, messages):
        self.humans.append(messages[1]["content"])

    def campaign_turn(self, state, seat, *, wolf):
        self._record(_last_messages.get("campaign", ["", ""]))
        return self.campaign.pop(0) if self.campaign else "pass"

    def withdraw_turn(self, state, seat, *, wolf):
        self._record(_last_messages.get("withdraw", ["", ""]))
        return self.withdraw.pop(0) if self.withdraw else "stay"

    def vote_turn(self, state, seat, candidates, *, wolf):
        self._record(_last_messages.get("vote", ["", ""]))
        return self.votes.pop(0) if self.votes else None

    def side_turn(self, state, seat, sides):
        return self.side if self.side in sides else sides[0]

    def badge_turn(self, state, seat, targets):
        return self.badge


_last_messages: dict[str, list[dict[str, str]]] = {}


def _conversation_public(engine, seat: int, text: str) -> None:
    engine.conversation_log.add_public_speech(
        seat, engine.state.players[seat].role, text,
        engine.state.round_number, "sheriff_election",
    )


@pytest.mark.asyncio
async def test_withdraw_decision_sees_campaign_speeches() -> None:
    from app.core.conversation_log import ConversationLog
    engine = _engine(seats=12, wolves=4)
    engine._sheriff_director = ScriptedSheriff(
        campaign=["run"] * 12, withdraw=["withdraw"] * 10 + ["stay", "stay"],
    )
    engine.speak = AsyncMock(return_value="我是真预言家，昨晚查验3号是金水。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff is None  # 12 ran, 2 remain, zero eligible voters
    assert engine.state.sheriff_office.badge_destroyed is True


@pytest.mark.asyncio
async def test_all_run_badge_loss_announces_reason() -> None:
    engine = _engine(seats=3)
    engine._sheriff_director = ScriptedSheriff(
        campaign=["run", "run", "run"], withdraw=["withdraw", "stay", "stay"],
    )
    engine.speak = AsyncMock(return_value="请听我说明上警理由后再决定去留。")
    await engine._execute_sheriff_election()
    records = [
        record.content for record in engine.conversation_log.records
        if record.scope.value == "public" and "警徽流失" in record.content
    ]
    assert records and "全员上警" in records[-1]


@pytest.mark.asyncio
async def test_director_receives_conversation_log_from_engine() -> None:
    engine = _engine(seats=3)
    assert engine._sheriff_director._conversation_log is engine.conversation_log


@pytest.mark.asyncio
async def test_badge_loss_message_for_all_withdraw() -> None:
    from app.core.sheriff_flow import badge_loss_message
    engine = _engine(seats=4)
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass"],
        withdraw=["withdraw", "withdraw"],
    )
    engine.speak = AsyncMock(return_value="我退水，把警徽让给真预言家。")
    await engine._execute_sheriff_election()
    assert engine.state.sheriff is None
    records = [
        record.content for record in engine.conversation_log.records
        if record.scope.value == "public" and "警徽流失" in record.content
    ]
    assert records and "退水" in records[-1]
