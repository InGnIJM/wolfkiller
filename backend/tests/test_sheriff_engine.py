from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import PipelineMode
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.game_engine import GameEngine
from app.core.role_pipeline import PipelineResult
from app.core.sheriff_flow import (
    DEATH_LEFT,
    DEATH_RIGHT,
    SHERIFF_LEFT,
    SHERIFF_RIGHT,
    SHERIFF_TECHNICAL_ABSTAIN,
    SHERIFF_VOTE_FAILURE_CODE,
    SheriffDirector,
    day_speech_order,
    set_sheriff,
)
from app.models.actions import DeathReport, VoteAction
from app.models.game import GameConfig, GamePhase, PlayerState
from app.services.game_summary import build_game_summary
import app.core.game_engine as game_engine_module


class ScriptedSheriff:
    def __init__(
        self, campaign=(), withdraw=(), votes=(), side=SHERIFF_LEFT,
        badge=("tear", None), badge_reason="",
    ):
        self.campaign = list(campaign)
        self.withdraw = list(withdraw)
        self.votes = list(votes)
        self.side = side
        self.badge = badge
        self.badge_reason = badge_reason

    def campaign_turn(self, state, seat, *, wolf):
        return self.campaign.pop(0) if self.campaign else "pass"

    def withdraw_turn(self, state, seat, *, wolf):
        return self.withdraw.pop(0) if self.withdraw else "stay"

    def vote_turn(self, state, seat, candidates, *, wolf):
        return self.votes.pop(0) if self.votes else None

    def side_turn(self, state, seat, sides):
        return self.side if self.side in sides else sides[0]

    def badge_turn(self, state, seat, targets):
        return (*self.badge, self.badge_reason)


def _engine(*, seats=4, wolves=1, enable=True, data_dir="data", reveal=False) -> GameEngine:
    god = 0 if seats <= wolves else 1
    config = GameConfig(
        role_counts={
            "wolf-killer-werewolf": wolves,
            "wolf-killer-seer": god,
            "wolf-killer-villager": max(seats - wolves - god, 0),
        },
        enable_sheriff=enable,
        reveal_on_death=reveal,
    )
    engine = GameEngine("sheriff-engine", config=config, event_bus=EventBus(), data_dir=data_dir)
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
async def test_an_election_explode_is_logged_as_a_death_batch(tmp_path) -> None:
    """竞选期自爆也是一次死亡：必须落进 ``night_deaths`` 批次。

    它从前只发 ``PLAYER_DIED``（总线 + 观众流），从不调 ``log_deaths``，于是
    ``summary.json`` 的 ``rounds.*.deaths`` 与 ``/logs`` 文件回放都看不到这一死 ——
    观众流有、两条公开路径没有。白天自爆与延迟死亡一直走 ``log_deaths``，只有竞选
    这一支漏了。这条用例走真实竞选循环，再把写出来的 ``game.log`` 喂给真正的
    ``build_game_summary``，所以两边任何一处回退都会红。
    """
    engine = _engine(wolves=2, data_dir=str(tmp_path))
    engine._sheriff_director = ScriptedSheriff(campaign=["explode"])
    await engine._execute_sheriff_election()

    rows = [
        json.loads(line)
        for line in (
            tmp_path / "games" / "sheriff-engine" / "game.log"
        ).read_text(encoding="utf-8").splitlines()
    ]
    batches = [row for row in rows if row["operation"] == "night_deaths"]
    assert [row["data"]["deaths"] for row in batches] == [
        [{"player_seat": 1, "cause": "self_explode", "round_number": 1}],
    ]
    assert [row["phase"] for row in batches] == ["sheriff_election"]

    summary = build_game_summary(str(tmp_path), "sheriff-engine")
    assert summary["rounds"]["1"]["deaths"] == [
        {"player_seat": 1, "cause": "self_explode", "round_number": 1},
    ]


@pytest.mark.asyncio
async def test_an_election_explode_reveals_its_identity_when_the_table_reveals(tmp_path) -> None:
    """明牌局里自爆同样要公布身份：``_publish_deaths`` 对每一死都调 ``_reveal_on_death``。"""
    engine = _engine(wolves=2, data_dir=str(tmp_path), reveal=True)
    engine._sheriff_director = ScriptedSheriff(campaign=["explode"])
    await engine._execute_sheriff_election()

    public = [record.content for record in engine.conversation_log.get_public()]
    assert "1号玩家出局，身份是：Werewolf。" in public


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
async def test_badge_reason_is_handed_to_the_step_and_then_cleared() -> None:
    """理由是一次性槽位：提交那一刻在，提交后必须清空。

    观众流里的 `sheriff_badge` 事件靠这个槽位拿到理由（与旁白同理），
    所以「提交时在读」与「提交后清掉」两半都要钉住。
    """
    engine = _engine()
    set_sheriff(engine.state, 2)
    engine.state.players[2].mark_dead("wolf_kill")
    engine._sheriff_director = ScriptedSheriff(
        badge=("transfer", 3), badge_reason="9号是金水，警徽交给他",
    )
    seen: list[str | None] = []

    async def hook(step_key: str) -> None:
        seen.append(engine._pending_badge_reasoning)

    engine._checkpoint_hook = hook
    await engine._maybe_reassign_badge()
    assert seen == ["9号是金水，警徽交给他"]
    assert engine._pending_badge_reasoning is None


@pytest.mark.asyncio
async def test_a_reasonless_badge_leaves_the_slot_empty() -> None:
    engine = _engine()
    set_sheriff(engine.state, 2)
    engine.state.players[2].mark_dead("wolf_kill")
    engine._sheriff_director = ScriptedSheriff(badge=("tear", None))
    seen: list[str | None] = []

    async def hook(step_key: str) -> None:
        seen.append(engine._pending_badge_reasoning)

    engine._checkpoint_hook = hook
    await engine._maybe_reassign_badge()
    assert seen == [None]
    assert engine.state.sheriff is None


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


def _candidate_fields(human: str) -> tuple[str, str]:
    line = next(item for item in human.splitlines() if item.startswith("已上警座位"))
    ran, contesting = line[len("已上警座位："):].split("；当前候选人：")
    return ran.rstrip("。"), contesting.rstrip("。")


@pytest.mark.asyncio
async def test_the_run_prompts_carry_the_candidates_the_engine_really_has() -> None:
    """引擎真实竞选循环渲染的提示词：候选人字段必须等于当时已上警的座位。

    这是「fixture 与真实状态不同形」的堵漏用例。旧的提示词测试直接手写
    `office.active`，而上警循环里引擎从不写它（`game_engine.py:1268` 只写
    `candidates`，`:1282` 循环结束才写 `active`），于是「已上警座位：4号、5号；
    当前候选人：无」这行自相矛盾的提示词在 72 局里一路绿灯。
    """
    engine = _engine(seats=6, wolves=1)
    engine.speak = AsyncMock(return_value="请投给我，我会认真履职。")
    prompts: list[str] = []

    def invoke(messages, tool_name, schema, seat):
        prompts.append(messages[1]["content"])
        if tool_name == "sheriff_campaign":
            choice = "run" if seat in (2, 3) else "pass"
            return json.dumps({"action_type": choice, "reasoning": "x"})
        if tool_name == "sheriff_withdraw":
            return json.dumps({"action_type": "stay", "reasoning": "x"})
        if tool_name == "sheriff_vote":
            return json.dumps({"action_type": "vote", "target_seat": 2, "reasoning": "x"})
        return "{}"

    engine._sheriff_director = SheriffDirector(invoke)
    await engine._execute_sheriff_election()

    run_prompts = [item for item in prompts if "action_type=run" in item]
    assert len(run_prompts) == 6  # 每个座位问一次上警决定
    for prompt in run_prompts:
        ran, contesting = _candidate_fields(prompt)
        assert contesting == ran, prompt
    # 上警顺序 1→6，2、3 号上警：最后一次问到时两人都已在候选人名单里。
    assert _candidate_fields(run_prompts[-1]) == ("2号、3号", "2号、3号")
    assert engine.state.sheriff == 2


@pytest.mark.asyncio
async def test_the_side_prompt_previews_the_order_the_engine_rotates_to() -> None:
    """警长选方向时看到的顺序预览必须等于引擎随后真正 rotate 出来的顺序。"""
    engine = _engine(seats=6, wolves=1)
    set_sheriff(engine.state, 2)
    # 选方向发生在白天发言开始前（死讯已公布），所以此时「表上认为存活」与
    # 引擎的 alive 集合一致 —— 预览用的就是这两个集合。
    engine.sm.set_state(GamePhase.SPEECH)
    engine.state.phase = GamePhase.SPEECH
    engine.state.players[4].is_alive = False
    engine.state.death_history = [DeathReport(4, "wolf_kill", 1)]
    captured: list[str] = []

    def invoke(messages, tool_name, schema, seat):
        captured.append(messages[1]["content"])
        return json.dumps({"action_type": "choose", "side": DEATH_LEFT, "reasoning": "x"})

    engine._sheriff_director = SheriffDirector(invoke)
    await engine._choose_speech_side()

    assert engine.state.sheriff_office.speech_side == DEATH_LEFT
    prompt = captured[0]
    assert "昨夜死亡：4号" in prompt
    assert "你是2号，身份：" in prompt and "你的私有事实" in prompt
    for side in (DEATH_LEFT, DEATH_RIGHT):
        expected = day_speech_order(engine.state, side)
        sequence = " → ".join(f"{seat}号" for seat in expected)
        assert f"{expected[0]}号先发言；顺序 {sequence}。" in prompt
    alive = list(engine.state.alive_players().items())
    assert [seat for seat, _ in engine._rotate_speech_order(alive)] == day_speech_order(
        engine.state, DEATH_LEFT,
    )


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
async def test_campaign_speeches_are_written_to_game_log(tmp_path) -> None:
    """Campaign speeches must be in the public event log, not only conversation.

    The summary's ``llm.calls_by_kind.speech`` counted them (4 rows in the
    2026-10-27 game) while ``rounds.*.speeches`` and ``game.log`` did not; the
    missing ``log=True`` made the replay and the two benchmarks disagree.
    """
    engine = _engine(data_dir=str(tmp_path))
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass"],
        withdraw=["stay", "stay"],
        votes=[2, 3, 2, 2],
    )
    engine.speak = AsyncMock(return_value="请投给我，我会认真履职保护好人。")

    await engine._execute_sheriff_election()

    rows = [
        json.loads(line)
        for line in (
            tmp_path / "games" / "sheriff-engine" / "game.log"
        ).read_text(encoding="utf-8").splitlines()
    ]
    speaks = [
        row for row in rows
        if row["operation"] == "speak" and row["phase"] == "sheriff_election"
    ]
    assert [row["seat"] for row in speaks] == [2, 3, 2, 3]

    summary = build_game_summary(str(tmp_path), "sheriff-engine")
    assert [row["seat"] for row in summary["rounds"]["1"]["speeches"]] == [2, 3, 2, 3]


@pytest.mark.asyncio
async def test_a_sheriff_technical_abstain_is_checkpointed_and_announced() -> None:
    """A ``vote`` the model could not complete must not pass for a voluntary pass.

    The campaign leaves seats 2 and 3 as candidates; seat 1's ballot is the
    director's technical sentinel, seat 4 still votes 2. The engine keeps the
    empty ``SHERIFF_VOTE`` row (the timeline must show the empty ballot), writes
    the sibling ``sheriff_vote_technical_abstain`` audit row, and commits the
    label the GameService maps to the existing ``TECHNICAL_ABSTAIN`` event.
    """
    engine = _engine()
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass"],
        withdraw=["stay", "stay"],
        votes=[SHERIFF_TECHNICAL_ABSTAIN, 2],
    )
    labels: list[str] = []
    engine._checkpoint_hook = labels.append
    technical: list[tuple[tuple, dict]] = []
    engine.game_logger.log_sheriff_vote_technical_abstain = (
        lambda *args, **kwargs: technical.append((args, kwargs))
    )
    audience: list[tuple[str, dict]] = []
    original = engine.game_logger.log_audience_action

    def capture(game_id, round_num, phase, event_type, payload):
        audience.append((event_type, dict(payload)))
        original(game_id, round_num, phase, event_type, payload)

    engine.game_logger.log_audience_action = capture
    engine.speak = AsyncMock(return_value="请投给我，我会认真履职保护好人。")

    await engine._execute_sheriff_election()

    assert technical == [
        ((engine.game_id, 1, 1), {"failure_code": SHERIFF_VOTE_FAILURE_CODE}),
    ]
    assert any(
        label.endswith(
            f"sheriff_vote_technical_abstain:1:vote:1:{SHERIFF_VOTE_FAILURE_CODE}"
        )
        for label in labels
    )
    ballots = {
        payload["voter_seat"]: payload["target_seat"]
        for event_type, payload in audience
        if event_type == "SHERIFF_VOTE"
    }
    assert ballots == {1: None, 4: 2}
    assert engine.state.sheriff == 2


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
        self.badge_reason = ""
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
        return (*self.badge, self.badge_reason)


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


# ── 官方顺序：竞选在公布死讯之前 ──────────────────────────────────

class ScriptedNarrator:
    """The dawn beat of the night director, without building a whole one."""

    def dawn_narration(self, deaths):
        seats = "、".join(f"{seat}号" for seat in deaths) or "无人"
        return ("天亮了", f"昨晚 {seats} 玩家死亡。")


class QuietScheduler:
    """A scheduler with no contracts bound: response windows open and settle nothing."""

    def __init__(self):
        from app.roles.registry import builtin_registry
        self.registry = builtin_registry.freeze()

    def run_point(self, state, point, *, slot="", defer_event_types=()):
        from app.core.scheduler import PointResult
        return PointResult((), (), (), point.value)

    def settle_pending(self, state, **kwargs):
        return None


def _night_engine(*, enable: bool = True, death_seat: int = 4):
    """An engine parked at the night's completion with one recorded death."""
    engine = _engine(enable=enable)
    engine._director = ScriptedNarrator()
    engine._pipeline_scheduler = QuietScheduler()
    engine.sm.set_state(GamePhase.NIGHT)
    engine.state.phase = GamePhase.NIGHT
    engine.state.players[death_seat].is_alive = False
    engine.state.death_history = [DeathReport(death_seat, "wolf_kill", 1)]
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(
        PipelineResult((), (), "digest", (), PipelineMode.V2),
        deaths=(game_engine_module._PendingDeath(death_seat, "wolf_kill", 1),),
        stage=0,
    )
    engine.rule_engine.check_win = lambda state: None
    engine._broadcast_phase_change = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_first_night_deaths_wait_for_the_election() -> None:
    """开警长时首夜死讯不在夜末公布：槽位停在 stage 10，等竞选结束。"""
    engine = _night_engine()
    published: list[object] = []
    engine.event_bus.publish = AsyncMock(side_effect=lambda event, **kw: published.append(event))
    engine.game_logger.log_deaths = MagicMock()

    await engine._execute_night()

    assert engine.sm.get_state() is GamePhase.SHERIFF_ELECTION
    assert engine.state.phase is GamePhase.SHERIFF_ELECTION
    pending = engine._pending_night_completion
    assert pending is not None and pending.stage == game_engine_module._ANNOUNCE_STAGE
    assert BusEvent.PLAYER_DIED not in published
    engine.game_logger.log_deaths.assert_not_called()


@pytest.mark.asyncio
async def test_without_the_office_the_night_still_announces_itself() -> None:
    """关警长的对局保持原样：夜末照常发布死亡并清空槽位。"""
    engine = _night_engine(enable=False)
    published: list[object] = []
    engine.event_bus.publish = AsyncMock(side_effect=lambda event, **kw: published.append(event))
    engine.game_logger.log_deaths = MagicMock()

    await engine._execute_night()

    assert BusEvent.PLAYER_DIED in published
    engine.game_logger.log_deaths.assert_called_once()
    assert engine._pending_night_completion is None
    assert engine.sm.get_state() is GamePhase.DAWN


@pytest.mark.asyncio
async def test_the_election_reads_the_deaths_out_after_the_badge_lands() -> None:
    """观众顺序：警长产生 → 天亮了/死讯 → 死亡 → 进入天亮。"""
    engine = _night_engine()
    order: list[tuple[str, object]] = []
    engine.event_bus.publish = AsyncMock(
        side_effect=lambda event, **kw: order.append(("bus", event)),
    )
    engine.game_logger.log_deaths = MagicMock(
        side_effect=lambda *args, **kwargs: order.append(("log_deaths", args[2])),
    )
    engine._log_sheriff_audience = MagicMock(
        side_effect=lambda event_type, payload: order.append(("audience", event_type)),
    )
    engine._narrate = AsyncMock(
        side_effect=lambda title, text, phase="night": order.append(("narration", title)),
    )
    engine._sheriff_director = ScriptedSheriff(campaign=["pass", "run", "pass", "pass"])

    await engine._execute_night()
    assert engine._pending_night_completion is not None
    await engine._execute_sheriff_election()

    def index_of(predicate) -> int:
        return next(index for index, item in enumerate(order) if predicate(*item))

    elected = index_of(lambda kind, value: kind == "audience" and value == "SHERIFF_ELECTED")
    # 只认「天亮了」那一拍：夜幕开场（stage 0）是更早的旁白，不能拿来当报死讯。
    narration = index_of(lambda kind, value: kind == "narration" and value == "天亮了")
    death = index_of(lambda kind, value: kind == "bus" and value is BusEvent.PLAYER_DIED)
    logged = index_of(lambda kind, value: kind == "log_deaths")
    assert elected < narration < death < logged
    assert order[logged][1] == [
        {"player_seat": 4, "cause": "wolf_kill", "round_number": 1},
    ]
    assert engine._pending_night_completion is None
    assert engine.sm.get_state() is GamePhase.DAWN


@pytest.mark.asyncio
async def test_a_decided_night_announces_and_skips_the_election() -> None:
    """夜里就判出胜负时不再竞选，但死讯必须当场公布。"""
    from app.models.actions import WinResult

    engine = _night_engine()
    engine.rule_engine.check_win = lambda state: WinResult("werewolf", "all_gods_dead")
    published: list[object] = []
    engine.event_bus.publish = AsyncMock(side_effect=lambda event, **kw: published.append(event))
    engine.game_logger.log_deaths = MagicMock()
    engine._narrate = AsyncMock()

    await engine._execute_night()

    assert BusEvent.PLAYER_DIED in published
    engine.game_logger.log_deaths.assert_called_once()
    engine._narrate.assert_awaited()
    assert engine._pending_night_completion is None
    assert engine.sm.get_state() is GamePhase.GAME_OVER


@pytest.mark.asyncio
async def test_an_explode_still_reads_the_night_out() -> None:
    """竞选中自爆：自爆当场公布，夜死批次紧随其后公布（docs 口径）。

    两条批次都要落 ``night_deaths``：自爆自己是一条（它从前不落，于是
    ``rounds.*.deaths`` 与 ``/logs`` 都看不到这一死），夜死是另一条。
    """
    engine = _night_engine()
    engine.event_bus.publish = AsyncMock()
    engine.game_logger.log_deaths = MagicMock()
    engine._narrate = AsyncMock()
    engine._sheriff_director = ScriptedSheriff(campaign=["explode", "pass", "pass", "pass"])

    await engine._execute_night()
    await engine._execute_sheriff_election()

    assert engine.state.players[1].is_alive is False
    assert engine._pending_night_completion is None
    assert [
        call.args[2] for call in engine.game_logger.log_deaths.call_args_list
    ] == [
        [{"player_seat": 1, "cause": "self_explode", "round_number": 1}],
        [{"player_seat": 4, "cause": "wolf_kill", "round_number": 1}],
    ]
    engine._narrate.assert_awaited()
    assert engine.sm.get_state() is GamePhase.DAWN


@pytest.mark.asyncio
async def test_the_deferred_announcement_resumes_at_its_stage() -> None:
    """公告分阶段落盘：从 stage 11 恢复时只补死亡，不重播旁白。"""
    engine = _night_engine()
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(
        PipelineResult((), (), "digest", (), PipelineMode.V2),
        deaths=(game_engine_module._PendingDeath(4, "wolf_kill", 1),),
        stage=game_engine_module._ANNOUNCE_STAGE + 1,
    )
    engine.event_bus.publish = AsyncMock()
    engine.game_logger.log_deaths = MagicMock()
    engine._narrate = AsyncMock()

    await engine._announce_pending_night_deaths()

    engine._narrate.assert_not_awaited()
    engine.game_logger.log_deaths.assert_called_once()
    pending = engine._pending_night_completion
    assert pending is not None and pending.stage == game_engine_module._ANNOUNCE_STAGE + 4
    # 再调一次是空操作：阶段已到「公告完成」，只剩调用方释放槽位。
    await engine._announce_pending_night_deaths()
    engine.game_logger.log_deaths.assert_called_once()
    assert engine._pending_night_completion.stage == game_engine_module._ANNOUNCE_STAGE + 4


@pytest.mark.asyncio
async def test_the_nights_response_window_opens_after_the_deaths_are_public() -> None:
    """夜死座位的反应窗口（开枪）在死讯公布之后才开，且带着夜死批次。"""
    from app.models.pipeline import SchedulePoint

    engine = _night_engine()
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(
        PipelineResult((), (), "digest", (), PipelineMode.V2),
        deaths=(game_engine_module._PendingDeath(4, "wolf_kill", 1),),
        stage=game_engine_module._ANNOUNCE_STAGE,
    )
    order: list[str] = []
    seeded: list[object] = []
    engine._narrate = AsyncMock(side_effect=lambda *a, **kw: order.append("narration"))
    engine.game_logger.log_deaths = MagicMock(
        side_effect=lambda *args, **kwargs: order.append("log_deaths"),
    )
    real = engine._run_response_point

    async def record(point, commit, **kwargs):
        order.append(f"response:{point.value}")
        seeded.extend(commit.events)
        return await real(point, commit, **kwargs)

    engine._run_response_point = record

    await engine._announce_pending_night_deaths()

    assert order == [
        "narration", "log_deaths", f"response:{SchedulePoint.DAWN_REACTION.value}",
    ]
    assert [event["payload"]["target_seat"] for event in seeded] == [4]
    assert [event["payload"]["cause"] for event in seeded] == ["wolf_kill"]
    assert engine._pending_night_completion.stage == game_engine_module._ANNOUNCE_STAGE + 4


@pytest.mark.asyncio
async def test_a_resume_after_the_election_still_reads_the_deaths_out() -> None:
    """竞选已完成、公告未做就中断：恢复时只补公告，不重跑竞选。"""
    engine = _engine()
    engine.state.sheriff_election_complete = True
    engine.state.players[4].is_alive = False
    engine.state.death_history = [DeathReport(4, "wolf_kill", 1)]
    engine._pending_night_completion = game_engine_module._PendingNightCompletion(
        PipelineResult((), (), "digest", (), PipelineMode.V2),
        deaths=(game_engine_module._PendingDeath(4, "wolf_kill", 1),),
        stage=game_engine_module._ANNOUNCE_STAGE,
    )
    engine._director = ScriptedNarrator()
    engine._pipeline_scheduler = QuietScheduler()
    engine.game_logger.log_deaths = MagicMock()
    engine._narrate = AsyncMock()
    engine.event_bus.publish = AsyncMock()

    await engine._execute_sheriff_election()

    engine._narrate.assert_awaited()
    engine.game_logger.log_deaths.assert_called_once()
    assert engine._pending_night_completion is None
    assert engine.state.sheriff is None and engine.state.sheriff_office.badge_destroyed is False
    assert engine.sm.get_state() is GamePhase.DAWN


@pytest.mark.asyncio
async def test_a_first_night_victim_still_runs_for_sheriff() -> None:
    """首夜被刀的座位照常上警并能当选（死讯公布前它还在场上）。"""
    engine = _engine(seats=4)
    engine.state.players[4].is_alive = False
    engine.state.death_history = [DeathReport(4, "wolf_kill", 1)]
    engine._sheriff_director = ScriptedSheriff(campaign=["pass", "pass", "pass", "run"])

    await engine._execute_sheriff_election()

    assert engine.state.sheriff == 4
    assert engine.state.players[4].is_sheriff is True


@pytest.mark.asyncio
async def test_a_first_night_victim_still_votes_for_sheriff() -> None:
    """未上警的首夜死者有警长投票权，它的一票照常计票。"""
    engine = _engine(seats=5)
    engine.state.players[5].is_alive = False
    engine.state.death_history = [DeathReport(5, "wolf_kill", 1)]
    engine._sheriff_director = ScriptedSheriff(
        campaign=["pass", "run", "run", "pass", "pass"],
        withdraw=["stay", "stay"],
        votes=[2, 2, 3],
    )
    engine.speak = AsyncMock(return_value="我上警竞选，请大家把票投给我。")

    await engine._execute_sheriff_election()

    assert engine.state.sheriff == 2
    ballots = [
        record.content for record in engine.conversation_log.records
        if record.phase == "sheriff_ballot"
    ]
    assert ballots and "5号→3号" in ballots[-1]


@pytest.mark.asyncio
async def test_the_night_batch_skips_its_dawn_beat_when_the_election_owes_it() -> None:
    """夜批次的天亮旁白由竞选那一侧播报，批次自己不再播一次。"""
    from app.core.scheduler import PointResult

    engine = _night_engine()
    engine._pending_night_completion = None
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        round_number=1, stage=11, discussion_history=(), wolf_votes=(),
        raw_results=tuple(PointResult((), (), (), "digest") for _ in range(5)),
    )
    engine._narrate = AsyncMock()

    await engine._execute_night_owned()

    engine._narrate.assert_not_awaited()
    assert engine.sm.get_state() is GamePhase.SHERIFF_ELECTION
    pending = engine._pending_night_completion
    assert pending is not None and pending.stage == game_engine_module._ANNOUNCE_STAGE


@pytest.mark.asyncio
async def test_the_night_batch_keeps_its_dawn_beat_without_the_office() -> None:
    """关警长时夜批次照旧自己播天亮旁白。"""
    from app.core.scheduler import PointResult

    engine = _night_engine(enable=False)
    engine._pending_night_completion = None
    engine._pending_night_batch = game_engine_module._PendingNightBatch(
        round_number=1, stage=11, discussion_history=(), wolf_votes=(),
        raw_results=tuple(PointResult((), (), (), "digest") for _ in range(5)),
    )
    engine._narrate = AsyncMock()

    await engine._execute_night_owned()

    engine._narrate.assert_awaited()
    assert engine.sm.get_state() is GamePhase.DAWN
    assert engine._pending_night_completion is None


@pytest.mark.asyncio
async def test_announcement_guards_are_no_ops_without_a_slot() -> None:
    """公告助手在空槽位、无死亡、槽位为 None 时都是空操作。"""
    engine = _night_engine()
    engine.event_bus.publish = AsyncMock()
    engine.game_logger.log_deaths = MagicMock()
    engine._narrate = AsyncMock()

    engine._pending_night_completion = None
    await engine._announce_pending_night_deaths()
    await engine._run_deferred_night_reactions()
    await engine._advance_announcement(game_engine_module._ANNOUNCE_STAGE)
    engine._narrate.assert_not_awaited()
    engine.game_logger.log_deaths.assert_not_called()

    empty = game_engine_module._PendingNightCompletion(
        PipelineResult((), (), "digest", (), PipelineMode.V2), deaths=(),
    )
    engine._pending_night_completion = empty
    await engine._run_deferred_night_reactions()
    assert engine._pending_night_completion is empty

    # 槽位停在公告阶段但还没推导死亡批次时，助手自己补推导（空结果 = 平安夜）。
    underived = game_engine_module._PendingNightCompletion(
        PipelineResult((), (), "digest", (), PipelineMode.V2),
        stage=game_engine_module._ANNOUNCE_STAGE,
    )
    engine._pending_night_completion = underived
    await engine._announce_pending_night_deaths()
    engine._narrate.assert_awaited()
    engine.game_logger.log_deaths.assert_called_once_with(
        engine.game_id, 1, [],
    )


def test_still_on_the_table_rejects_an_unknown_seat() -> None:
    engine = _night_engine()
    assert engine._still_on_the_table(99) is False
    assert engine._still_on_the_table(1) is True
