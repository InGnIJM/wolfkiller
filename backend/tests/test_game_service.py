import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from app.services.game_service import GameService
from app.core.event_bus import EventBus
from app.api.websocket.ws_handler import WSManager


class TestGameService:
    @pytest.mark.asyncio
    async def test_create_and_list_games(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game(
            num_werewolves=3, num_villagers=3,
            num_seers=1, num_witches=1, num_hunters=1,
        )

        assert game_id is not None
        assert len(game_id) == 8

        games = service.list_games()
        assert game_id in games

        state = service.get_game_state(game_id)
        assert state is not None
        assert state.config.total_players == 9

    @pytest.mark.asyncio
    async def test_get_nonexistent_game(self):
        service = GameService(WSManager(), EventBus())
        assert service.get_game_state("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_empty_games(self):
        service = GameService(WSManager(), EventBus())
        assert service.list_games() == []

    @pytest.mark.asyncio
    async def test_create_game_with_custom_config(self):
        service = GameService(WSManager(), EventBus())

        game_id = await service.create_game(
            num_werewolves=2, num_villagers=2,
            num_seers=1, num_witches=1, num_hunters=1,
        )

        state = service.get_game_state(game_id)
        assert state is not None
        assert state.config.total_players == 7

    @pytest.mark.asyncio
    async def test_phase_change_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        state = service.get_game_state(game_id)
        assert state is not None

        # Simulate phase change event
        await service._on_phase_changed(phase="night", round_number=1, state=state)
        # Should not crash; state is updated
        assert service.get_game_state(game_id) is not None

    @pytest.mark.asyncio
    async def test_game_over_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        state = service.get_game_state(game_id)

        from app.models.actions import WinResult
        win = WinResult(winning_camp="good", reason="all_wolves_dead")
        await service._on_game_over(win_result=win)
        # Should not crash

    @pytest.mark.asyncio
    async def test_phase_changed_handler_none_state(self):
        service = GameService(WSManager(), EventBus())
        await service._on_phase_changed(phase="night", round_number=1)
        # Should not crash when state is None

    @pytest.mark.asyncio
    async def test_speech_made_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        from app.models.actions import SpeechRecord
        speech = SpeechRecord(player_seat=1, text="test speech", round_number=1)
        await service._on_speech_made(speech=speech)
        # Should not crash

    @pytest.mark.asyncio
    async def test_speech_made_handler_none(self):
        service = GameService(WSManager(), EventBus())
        await service._on_speech_made(speech=None)
        # Should not crash

    @pytest.mark.asyncio
    async def test_vote_cast_handler(self):
        ws_manager = WSManager()
        bus = EventBus()
        service = GameService(ws_manager, bus)

        game_id = await service.create_game()
        from app.models.actions import VoteAction
        vote = VoteAction(voter_seat=1, target_seat=3, reasoning="test")
        await service._on_vote_cast(vote=vote)
        # Should not crash

    @pytest.mark.asyncio
    async def test_vote_cast_handler_none(self):
        service = GameService(WSManager(), EventBus())
        await service._on_vote_cast(vote=None)
        # Should not crash

    @pytest.mark.asyncio
    async def test_game_over_handler_none(self):
        service = GameService(WSManager(), EventBus())
        await service._on_game_over(win_result=None)
        # Should not crash
