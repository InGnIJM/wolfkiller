import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from app.core.game_engine import GameEngine
from app.models.game import GameState, GameConfig, GamePhase, PlayerState
from app.models.actions import NightAction, VoteAction, DeathReport
from app.models.contracts import AcceptedAction
from app.core.event_bus import EventBus


def make_mock_role(seat: int, role_name: str,
                   night_action: NightAction = None,
                   speech: str = "test speech",
                   vote: VoteAction = None,
                   chat_msg: str = "let's kill someone"):
    role = MagicMock()
    role.seat = seat
    role.role_name = role_name

    async def _speak(state, conversation_log, context):
        return speech

    async def _vote(state, conversation_log, context):
        if vote:
            return vote
        return VoteAction(voter_seat=seat, target_seat=None)

    async def _kill(state, conversation_log):
        if night_action and night_action.action_type == "kill":
            return night_action
        return NightAction(player_seat=seat, action_type="kill", target_seat=9)

    async def _save(state, conversation_log, wolf_target):
        return False  # Default: don't save

    async def _poison(state, conversation_log, wolf_target):
        if night_action and night_action.action_type == "poison":
            return night_action
        return NightAction(player_seat=seat, action_type="pass")

    async def _check(state, conversation_log):
        if night_action and night_action.action_type == "check":
            return night_action
        return NightAction(player_seat=seat, action_type="check", target_seat=1)

    async def _shoot(state, conversation_log):
        if night_action and night_action.action_type == "shoot":
            return night_action
        return NightAction(player_seat=seat, action_type="pass")

    async def _chat(state, conversation_log):
        return chat_msg

    role.speak = AsyncMock(side_effect=_speak)
    role.vote = AsyncMock(side_effect=_vote)

    if "werewolf" in role_name:
        role.kill = AsyncMock(side_effect=_kill)
        role.chat = AsyncMock(side_effect=_chat)
    if "witch" in role_name:
        role.save = AsyncMock(side_effect=_save)
        role.poison = AsyncMock(side_effect=_poison)
    if "seer" in role_name:
        role.check = AsyncMock(side_effect=_check)
    if "hunter" in role_name:
        role.shoot = AsyncMock(side_effect=_shoot)

    return role


def make_9_mock_roles():
    roles = {}
    role_names = [
        (1, "wolf-killer-werewolf"), (2, "wolf-killer-werewolf"), (3, "wolf-killer-werewolf"),
        (4, "wolf-killer-villager"), (5, "wolf-killer-villager"), (6, "wolf-killer-villager"),
        (7, "wolf-killer-seer"), (8, "wolf-killer-witch"), (9, "wolf-killer-hunter"),
    ]
    for seat, role_name in role_names:
        target = 9 if "werewolf" in role_name else (2 if "seer" in role_name else None)
        action_type = "kill" if "werewolf" in role_name else ("check" if "seer" in role_name else "pass")
        roles[seat] = make_mock_role(seat, role_name,
            night_action=NightAction(player_seat=seat, action_type=action_type, target_seat=target),
            vote=VoteAction(voter_seat=seat, target_seat=1),
        )
    return roles


class TestGameEngine:
    @pytest.mark.asyncio
    async def test_role_assignment(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        assert len(engine.state.players) == 9
        wolves = [p for p in engine.state.players.values() if p.camp == "werewolf"]
        assert len(wolves) == 3

        witch = [p for p in engine.state.players.values() if "witch" in p.role]
        assert len(witch) == 1
        assert witch[0].has_antidote is True
        assert witch[0].has_poison is True

        hunter = [p for p in engine.state.players.values() if "hunter" in p.role]
        assert len(hunter) == 1
        assert hunter[0].has_gun is True

    @pytest.mark.asyncio
    async def test_camp_from_role(self):
        roles = make_9_mock_roles()
        engine = GameEngine(game_id="test", roles=roles)
        assert engine._camp_from_role("wolf-killer-werewolf") == "werewolf"
        assert engine._camp_from_role("wolf-killer-villager") == "good"
        assert engine._camp_from_role("wolf-killer-seer") == "good"

    @pytest.mark.asyncio
    async def test_find_player_by_role(self):
        roles = make_9_mock_roles()
        engine = GameEngine(game_id="test", roles=roles)
        engine._assign_roles()

        seer = engine._find_player_by_role("seer")
        assert seer is not None
        assert "seer" in seer.role

        nonexistent = engine._find_player_by_role("nonexistent")
        assert nonexistent is None

    @pytest.mark.asyncio
    async def test_resolve_votes(self):
        engine = GameEngine(game_id="test")
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=5),
            VoteAction(voter_seat=2, target_seat=5),
            VoteAction(voter_seat=3, target_seat=4),
        ]
        exiled = engine.resolve_votes()
        assert exiled == 5

    @pytest.mark.asyncio
    async def test_resolve_votes_tie(self):
        engine = GameEngine(game_id="test")
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=5),
            VoteAction(voter_seat=2, target_seat=5),
            VoteAction(voter_seat=3, target_seat=4),
            VoteAction(voter_seat=4, target_seat=4),
        ]
        exiled = engine.resolve_votes()
        assert exiled is None  # tie

    @pytest.mark.asyncio
    async def test_resolve_votes_abstain(self):
        engine = GameEngine(game_id="test")
        engine.state.votes = [
            VoteAction(voter_seat=1, target_seat=None),
            VoteAction(voter_seat=2, target_seat=0),
            VoteAction(voter_seat=3, target_seat=5),
        ]
        exiled = engine.resolve_votes()
        assert exiled == 5

    @pytest.mark.asyncio
    async def test_phase_delay(self):
        engine = GameEngine(game_id="test")
        assert engine.phase_delay == 2.0
        engine.phase_delay = 1.0
        assert engine.phase_delay == 1.0
        engine.phase_delay = 0.01
        assert engine.phase_delay == 0.1  # clamped

    @pytest.mark.asyncio
    async def test_pause_resume(self):
        engine = GameEngine(game_id="test")
        assert engine._paused is False
        engine.pause()
        assert engine._paused is True
        engine.resume()
        assert engine._paused is False

    @pytest.mark.asyncio
    async def test_stop(self):
        engine = GameEngine(game_id="test")
        engine._running = True
        await engine.stop()
        assert engine._running is False

    @pytest.mark.asyncio
    async def test_broadcast_phase_change(self):
        bus = EventBus()
        received = []

        async def handler(**kwargs):
            received.append(kwargs)

        bus.subscribe("phase_changed", handler)
        engine = GameEngine(game_id="test", event_bus=bus)
        engine._assign_roles()
        await engine._broadcast_phase_change()

        assert len(received) == 1
        assert received[0]["phase"] == engine.sm.get_state().value

    @pytest.mark.asyncio
    async def test_vote_resolution_wolf_wins(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        for p in engine.state.players.values():
            if "villager" in p.role:
                p.is_alive = False

        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        await engine._execute_vote_resolution()

        assert engine.state.win_result is not None
        assert engine.state.win_result["winning_camp"] == "werewolf"
        assert engine.sm.is_terminal()

    @pytest.mark.asyncio
    async def test_vote_resolution_good_wins(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        for p in engine.state.players.values():
            if "werewolf" in p.role:
                p.is_alive = False

        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        await engine._execute_vote_resolution()

        assert engine.state.win_result is not None
        assert engine.state.win_result["winning_camp"] == "good"

    @pytest.mark.asyncio
    async def test_vote_resolution_exile_player(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        votes = [VoteAction(voter_seat=s, target_seat=1) for s in range(2, 10)]
        engine.state.votes = votes
        engine.state.players[1].is_alive = True

        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)
        await engine._execute_vote_resolution()
        assert engine.state.players[1].is_alive is False

    @pytest.mark.asyncio
    async def test_speak_no_role(self):
        engine = GameEngine(game_id="test", roles={})
        result = await engine.speak(999, "day_speech")
        assert result is None

    @pytest.mark.asyncio
    async def test_vote_no_role(self):
        engine = GameEngine(game_id="test", roles={})
        result = await engine.vote(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_speak_with_role(self):
        role = make_mock_role(1, "wolf-killer-villager", speech="hello world")
        engine = GameEngine(game_id="test", roles={1: role})

        speech = await engine.speak(1, "day_speech")
        assert speech == "hello world"

    @pytest.mark.asyncio
    async def test_vote_with_role(self):
        role = make_mock_role(1, "wolf-killer-villager",
                              vote=VoteAction(voter_seat=1, target_seat=3))
        engine = GameEngine(game_id="test", roles={1: role})

        vote = await engine.vote(1)
        assert vote is not None
        assert vote.target_seat == 3

    @pytest.mark.asyncio
    async def test_speak_error_handling(self):
        role = MagicMock()
        role.speak = AsyncMock(side_effect=Exception("LLM error"))

        engine = GameEngine(game_id="test", roles={1: role})
        result = await engine.speak(1, "day_speech")
        # Emergency fallback: engine should never return None for speech errors;
        # it generates a placeholder speech so the player is never silently skipped.
        assert result is not None
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_vote_error_handling(self):
        role = MagicMock()
        role.vote = AsyncMock(side_effect=Exception("LLM error"))

        engine = GameEngine(game_id="test", roles={1: role})
        result = await engine.vote(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_night_round(self):
        roles = {}
        role_list = [
            (1, "wolf-killer-werewolf"), (2, "wolf-killer-werewolf"), (3, "wolf-killer-werewolf"),
            (4, "wolf-killer-villager"), (5, "wolf-killer-villager"), (6, "wolf-killer-villager"),
            (7, "wolf-killer-seer"), (8, "wolf-killer-witch"), (9, "wolf-killer-hunter"),
        ]
        for seat, role_name in role_list:
            target = 4 if "werewolf" in role_name else (2 if "seer" in role_name else None)
            action_type = "kill" if "werewolf" in role_name else ("check" if "seer" in role_name else "pass")
            roles[seat] = make_mock_role(seat, role_name,
                night_action=NightAction(player_seat=seat, action_type=action_type, target_seat=target),
            )

        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        # Manually set up players without random shuffle to match mock roles
        for seat, role_name in role_list:
            camp = "werewolf" if "werewolf" in role_name else "good"
            player = PlayerState(seat_number=seat, role=role_name, camp=camp)
            if "witch" in role_name:
                player.has_antidote = True
                player.has_poison = True
            if "hunter" in role_name:
                player.has_gun = True
            engine.state.players[seat] = player
        engine.sm.set_state(GamePhase.NIGHT)
        engine.state.phase = GamePhase.NIGHT
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        await engine._execute_night()

        assert engine.state.round_number == 1
        assert len(engine.state.night_actions) > 0
        assert all(
            isinstance(action, AcceptedAction)
            for action in resolve.call_args.args[1]
        )
        assert engine.sm.get_state() == GamePhase.DAWN

    @pytest.mark.asyncio
    async def test_accept_night_actions_converts_invalid_actions_before_resolver(self):
        roles = {
            1: make_mock_role(1, "wolf-killer-werewolf"),
            7: make_mock_role(7, "wolf-killer-seer"),
        }
        engine = GameEngine(game_id="test", roles=roles)
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            4: PlayerState(4, "wolf-killer-villager", "good"),
            7: PlayerState(7, "wolf-killer-seer", "good"),
        }
        engine.state.phase = GamePhase.NIGHT

        accepted_actions = engine._accept_night_actions([
            NightAction(player_seat=1, action_type="poison", target_seat=4),
            NightAction(player_seat=7, action_type="check", target_seat=99),
        ])
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        deaths = engine.action_resolver.resolve(engine.state, accepted_actions)

        resolver_actions = resolve.call_args.args[1]
        assert deaths == []
        assert all(isinstance(action, AcceptedAction) for action in resolver_actions)
        assert all(not isinstance(action, NightAction) for action in resolver_actions)
        assert [action.command.action_type for action in resolver_actions] == ["pass", "pass"]
        assert all(action.command.target_seat is None for action in resolver_actions)

    @pytest.mark.asyncio
    async def test_execute_night_does_not_send_second_witch_potion_to_resolver(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=5),
            ),
            2: make_mock_role(2, "wolf-killer-witch"),
            3: make_mock_role(3, "wolf-killer-villager"),
            4: make_mock_role(
                4, "wolf-killer-seer",
                night_action=NightAction(player_seat=4, action_type="check", target_seat=1),
            ),
            5: make_mock_role(5, "wolf-killer-villager"),
        }
        roles[2].save = AsyncMock(return_value=True)
        roles[2].poison = AsyncMock(return_value=NightAction(
            player_seat=2, action_type="poison", target_seat=1,
        ))
        engine = GameEngine(game_id="test", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        await engine._execute_night()

        resolver_actions = resolve.call_args.args[1]
        witch_actions = [
            action for action in resolver_actions
            if action.request.actor_seat == 2
        ]
        assert all(isinstance(action, AcceptedAction) for action in resolver_actions)
        assert [action.command.action_type for action in witch_actions] == ["save"]
        assert [action.action_type for action in engine.state.night_actions if action.player_seat == 2] == ["save"]
        assert engine.state.players[2].has_antidote is False
        assert engine.state.players[2].has_poison is True

    @pytest.mark.asyncio
    async def test_execute_night_records_safe_fallback_instead_of_invalid_raw_action(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
            ),
            2: make_mock_role(2, "wolf-killer-villager"),
        }
        roles[1].kill = AsyncMock(return_value=NightAction(
            player_seat=1, action_type="poison", target_seat=2,
        ))
        engine = GameEngine(game_id="test", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()

        await engine._execute_night()

        assert [(action.player_seat, action.action_type, action.target_seat) for action in engine.state.night_actions] == [
            (1, "pass", None),
        ]

    @pytest.mark.asyncio
    async def test_execute_night_records_each_wolf_kill_without_overwriting(self, tmp_path):
        roles = {
            1: make_mock_role(
                1, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=1, action_type="kill", target_seat=3),
            ),
            2: make_mock_role(
                2, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=2, action_type="kill", target_seat=3),
            ),
            3: make_mock_role(3, "wolf-killer-villager"),
        }
        engine = GameEngine(game_id="test", roles=roles, data_dir=str(tmp_path))
        engine._assign_roles()
        engine.state.phase = GamePhase.NIGHT
        engine.sm.set_state(GamePhase.NIGHT)
        engine._sleep_night_step = AsyncMock()

        await engine._execute_night()

        assert [(action.player_seat, action.action_type, action.target_seat) for action in engine.state.night_actions] == [
            (1, "kill", 3),
            (2, "kill", 3),
        ]

    def test_accept_night_actions_rejects_duplicate_without_second_resolution(self):
        roles = {1: make_mock_role(1, "wolf-killer-werewolf")}
        engine = GameEngine(game_id="test", roles=roles)
        engine.state.players = {
            1: PlayerState(1, "wolf-killer-werewolf", "werewolf"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        }
        engine.state.phase = GamePhase.NIGHT
        action = NightAction(player_seat=1, action_type="kill", target_seat=2)
        resolve = MagicMock(wraps=engine.action_resolver.resolve)
        engine.action_resolver.resolve = resolve

        accepted_actions = engine._accept_night_actions([action])
        engine.action_resolver.resolve(engine.state, accepted_actions)
        duplicate_actions = engine._accept_night_actions([action])
        if duplicate_actions:
            engine.action_resolver.resolve(engine.state, duplicate_actions)

        assert [accepted.command.action_type for accepted in accepted_actions] == ["kill"]
        assert duplicate_actions == []
        assert resolve.call_count == 1

    @pytest.mark.asyncio
    async def test_execute_speech_round(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.SPEECH)

        await engine._execute_speech_round()

        assert len(engine.state.speeches) == len(engine.state.alive_players())
        assert engine.sm.get_state() == GamePhase.VOTE_CASTING

    @pytest.mark.asyncio
    async def test_execute_vote_casting(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.sm.set_state(GamePhase.VOTE_CASTING)

        await engine._execute_vote_casting()

        assert len(engine.state.votes) == len(engine.state.alive_players())
        assert engine.sm.get_state() == GamePhase.VOTE_RESOLUTION

    @pytest.mark.asyncio
    async def test_execute_dawn(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.phase_delay = 0.01
        engine.sm.set_state(GamePhase.DAWN)

        await engine._execute_dawn()

        assert engine.sm.get_state() == GamePhase.LAST_WORDS

    @pytest.mark.asyncio
    async def test_execute_last_words(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.state.round_number = 1
        engine.state.death_history.append(
            DeathReport(player_seat=1, cause="wolf_kill", round_number=1)
        )
        engine.state.players[1].is_alive = False
        engine.sm.set_state(GamePhase.LAST_WORDS)

        await engine._execute_last_words()

        assert engine.sm.get_state() == GamePhase.SPEECH

    @pytest.mark.asyncio
    async def test_execute_last_words_dead_player_no_agent(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.state.round_number = 1
        engine.state.death_history.append(
            DeathReport(player_seat=99, cause="wolf_kill", round_number=1)
        )
        engine.sm.set_state(GamePhase.LAST_WORDS)

        await engine._execute_last_words()

        assert engine.sm.get_state() == GamePhase.SPEECH

    @pytest.mark.asyncio
    async def test_execute_last_words_after_day1(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()
        engine.state.round_number = 2
        engine.state.death_history.append(
            DeathReport(player_seat=1, cause="wolf_kill", round_number=2)
        )
        engine.state.players[1].is_alive = False
        engine.sm.set_state(GamePhase.LAST_WORDS)

        await engine._execute_last_words()

        assert engine.sm.get_state() == GamePhase.SPEECH

    @pytest.mark.asyncio
    async def test_wait_if_paused(self):
        engine = GameEngine(game_id="test")
        engine._running = True
        engine._paused = True

        async def unpause():
            await asyncio.sleep(0.05)
            engine._paused = False

        task = asyncio.create_task(unpause())
        await engine._wait_if_paused()
        await task
        assert engine._paused is False

    @pytest.mark.asyncio
    async def test_vote_resolution_tie_no_exile(self):
        roles = make_9_mock_roles()
        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        engine._assign_roles()

        # Initial vote: tie (3 for 1, 3 for 2)
        votes = []
        for s in [3, 4, 5]:
            votes.append(VoteAction(voter_seat=s, target_seat=1))
        for s in [6, 7, 8]:
            votes.append(VoteAction(voter_seat=s, target_seat=2))
        engine.state.votes = votes
        engine.sm.set_state(GamePhase.VOTE_RESOLUTION)

        # Make re-vote also a tie — all roles abstain so tally is empty
        for role in roles.values():
            role.vote = AsyncMock(return_value=VoteAction(voter_seat=role.seat, target_seat=None))

        await engine._execute_vote_resolution()

        assert engine.state.players[1].is_alive
        assert engine.state.players[2].is_alive

    @pytest.mark.asyncio
    async def test_seer_check_result(self):
        engine = GameEngine(game_id="test")
        engine.state.players[1] = PlayerState(seat_number=1, role="wolf-killer-werewolf", camp="werewolf")
        engine.state.players[2] = PlayerState(seat_number=2, role="wolf-killer-villager", camp="good")

        assert engine.resolve_seer_check(NightAction(player_seat=7, action_type="check", target_seat=1)) == "werewolf"
        assert engine.resolve_seer_check(NightAction(player_seat=7, action_type="check", target_seat=2)) == "good"

    @pytest.mark.asyncio
    async def test_werewolf_kill(self):
        roles = {}
        for seat in [1, 2, 3]:
            roles[seat] = make_mock_role(seat, "wolf-killer-werewolf",
                night_action=NightAction(player_seat=seat, action_type="kill", target_seat=4))

        bus = EventBus()
        engine = GameEngine(game_id="test", roles=roles, event_bus=bus)
        for seat, role_name in [(1, "wolf-killer-werewolf"), (2, "wolf-killer-werewolf"),
                                 (3, "wolf-killer-werewolf"), (4, "wolf-killer-villager")]:
            camp = "werewolf" if "werewolf" in role_name else "good"
            engine.state.players[seat] = PlayerState(seat_number=seat, role=role_name, camp=camp)

        actions, target = await engine.werewolf_kill([1, 2, 3])
        assert len(actions) == 3
        assert target == 4

    @pytest.mark.asyncio
    async def test_get_night_deaths(self):
        engine = GameEngine(game_id="test")
        engine.state.round_number = 1
        engine.state.death_history = [
            DeathReport(player_seat=1, cause="wolf_kill", round_number=1),
            DeathReport(player_seat=2, cause="wolf_kill", round_number=0),
        ]
        deaths = engine.get_night_deaths()
        assert len(deaths) == 1
        assert deaths[0].player_seat == 1

    @pytest.mark.asyncio
    async def test_conversation_log_integration(self):
        """Verify ConversationLog is initialized and accessible."""
        engine = GameEngine(game_id="test")
        assert engine.conversation_log is not None
        assert len(engine.conversation_log.get_all()) == 0

        engine.conversation_log.add_public_speech(1, "wolf-killer-villager", "hello", 1, "speech")
        visible = engine.conversation_log.get_conversations_for_role(1, "wolf-killer-villager")
        assert len(visible) == 1

    @pytest.mark.asyncio
    async def test_game_logger_integration(self):
        """Verify GameLogger is initialized."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = GameEngine(game_id="test", data_dir=tmpdir)
            engine.game_logger.log_operation("test", "test_op", 1, "night", {"key": "val"})
            engine.game_logger.log_conversation("test",
                {"scope": "public", "content": "test", "round_number": 1})
            log_path = os.path.join(tmpdir, "games", "test", "game.log")
            conv_path = os.path.join(tmpdir, "games", "test", "conversation.log")
            assert os.path.exists(log_path)
            assert os.path.exists(conv_path)
